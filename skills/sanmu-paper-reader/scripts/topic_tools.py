"""Offline Markdown topic candidates; Python 3.10+, standard library only."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import sys
from urllib.parse import quote

NOTICE = "仅为主题候选，请回源读取核实；不是摘要或科学答案。"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def relative(value):
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or any(ord(c) < 32 for c in value) or PureWindowsPath(value).drive or PureWindowsPath(value).root
            or Path(value).is_absolute() or ".." in Path(value).parts
            or Path(value).suffix.lower() != ".md"):
        raise ValueError("file must be a relative POSIX .md path without traversal")
    return Path(value).as_posix()


def source(root, name):
    path = (root / relative(name)).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("source must be a regular file inside root: " + name)
    data = path.read_bytes()
    return path, data, data.decode("utf-8").splitlines(keepends=True)


def structure(lines, frontmatter=True):
    """Find real headings and boundaries outside fenced code/display math."""
    first = 0
    if frontmatter and lines and lines[0].lstrip("\ufeff").strip() == "---":
        first = next((i + 1 for i in range(1, len(lines))
                      if lines[i].strip() in ("---", "...")), None)
        if first is None:
            raise ValueError("unclosed frontmatter")
    headings, safe, breaks, stack = [], {first}, {first}, []
    fence = math_end = None
    previous = None
    for i in range(first, len(lines)):
        line = lines[i].rstrip("\r\n")
        if i == 0:
            line = line.lstrip("\ufeff")
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
            previous = None
        elif math_end:
            if math_end in line:
                math_end = None
            previous = None
        elif marker:
            fence = marker[1]
            previous = None
        else:
            stripped = line.strip()
            opener = re.search(r"\$\$|\\\[|\\begin\{(equation\*?|align\*?|gather\*?|multline\*?)\}", line)
            if opener:
                closing = {"$$": "$$", "\\[": "\\]"}.get(opener[0])
                closing = closing or "\\end{" + opener[1] + "}"
                if closing not in line[opener.end():]:
                    math_end = closing
                previous = None
            else:
                atx = re.match(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?)|[ \t]*)$", line)
                setext = re.fullmatch(r" {0,3}(=+|-+)[ \t]*", line) if previous is not None else None
                if atx or setext:
                    level = len(atx[1]) if atx else (1 if stripped[0] == "=" else 2)
                    title = re.sub(r"[ \t]+#+[ \t]*$", "", atx[2] or "").strip() if atx else previous[1]
                    start = i if atx else previous[0]
                    while stack and stack[-1][0] >= level:
                        stack.pop()
                    stack.append((level, title))
                    headings.append((start, [s[1] for s in stack]))
                    previous = None
                else:
                    previous = (i, stripped) if stripped and not re.match(r"^( {4}|\t| {0,3}([-+*>]|\d+[.)])\s)", line) else None
                if not stripped:
                    breaks.add(i + 1)
        if not fence and not math_end:
            safe.add(i + 1)
    # An unclosed block extends to EOF; never cut it internally.
    safe.add(len(lines))
    return first, headings, safe, breaks


def section_at(headings, start):
    return next((crumb for pos, crumb in reversed(headings) if pos <= start), [])


def make_chunk(name, lines, start, end, headings, pages=None, mapping=None):
    return {"id": "C-" + digest(f"{name}:{start + 1}:{end}".encode())[:20],
            "file": name, "start_line": start + 1, "end_line": end,
            "section": section_at(headings, start), "pages": pages or [],
            "mapping": mapping, "text": "".join(lines[start:end])}


def native_chunks(name, lines, start, end, headings, breaks):
    cuts = sorted({start, end} | {p for p, _ in headings if start < p < end})
    result = []
    for a, b in zip(cuts, cuts[1:]):
        cursor, size = a, 0
        for i in range(a, b):
            size += len(lines[i])
            if size >= 2200 and i + 1 in breaks:
                result.append(make_chunk(name, lines, cursor, i + 1, headings))
                cursor, size = i + 1, 0
        if cursor < b:
            result.append(make_chunk(name, lines, cursor, b, headings))
    return result


def external_chunks(directory, documents):
    imported = {name: [] for name in documents}
    if directory is None:
        return imported
    path = Path(directory) / "chunks.jsonl"
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"external chunk {number}: expected object")
        name = relative(row.get("file"))
        if name not in documents:
            continue
        lines, (_, headings, safe, _) = documents[name]
        start, end = row.get("start", row.get("start_line")), row.get("end", row.get("end_line"))
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
            raise ValueError(f"external chunk {number}: invalid line range")
        if row.get("text") != "".join(lines[start - 1:end]):
            raise ValueError(f"external chunk {number}: text does not exactly match source lines")
        if start - 1 not in safe or end not in safe:
            raise ValueError(f"external chunk {number}: boundary splits frontmatter/code/math")
        pages = row.get("pages", [])
        if not isinstance(pages, list) or any(type(p) is not int or p <= 0 for p in pages):
            raise ValueError(f"external chunk {number}: pages must be positive integers")
        imported[name].append(make_chunk(name, lines, start - 1, end, headings, pages,
                                         "external:agent/chunks.jsonl"))
    return imported


def build(args, root):
    out = Path(args.out)
    if out.exists() or out.is_symlink():
        raise ValueError("output directory already exists; choose a NEW_DIR")
    documents, sources, protected = {}, [], []
    for value in args.files:
        name = relative(value)
        if name in documents:
            continue
        path, data, lines = source(root, name)
        protected.append(path)
        documents[name] = (lines, structure(lines))
        sources.append({"path": name, "sha256": digest(data)})
    target = out.resolve()
    if any(path == target or path.is_relative_to(target) for path in protected):
        raise ValueError("output cannot contain or overwrite a source")
    imported = external_chunks(args.agent_index, documents)
    chunks = []
    for name, (lines, (first, headings, _, breaks)) in documents.items():
        cursor = first
        for chunk in sorted(imported[name], key=lambda c: c["start_line"]):
            start, end = chunk["start_line"] - 1, chunk["end_line"]
            if start < cursor:
                raise ValueError("external chunks overlap or include frontmatter: " + name)
            chunks.extend(native_chunks(name, lines, cursor, start, headings, breaks))
            chunks.append(chunk)
            cursor = end
        chunks.extend(native_chunks(name, lines, cursor, len(lines), headings, breaks))
    navigation, groups = ["# 主题路由\n\n" + NOTICE + "\n"], {}
    for chunk in chunks:
        key = (chunk["file"], tuple(chunk["section"]))
        groups.setdefault(key, []).append(chunk)
    for (name, section), members in groups.items():
        navigation.append("\n## " + name + " · " + (" / ".join(section) or "（无标题）") + "\n")
        try:
            destination = os.path.relpath(root / name, target).replace("\\", "/")
        except ValueError:  # Windows output can be on a different drive.
            destination = (root / name).as_uri()
        link = quote(destination, safe="/.:%")
        for c in members:
            navigation.append(f"- [{c['id']}]({link}) · L{c['start_line']}–L{c['end_line']}\n")
    out.mkdir(parents=True, exist_ok=False)
    (out / "index.json").write_text(json.dumps({"schema_version": 1, "sources": sources}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "chunks.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in chunks), encoding="utf-8")
    (out / "00-主题路由.md").write_text("".join(navigation), encoding="utf-8")
    return {"sources": len(sources), "chunks": len(chunks), "notice": NOTICE}


def load(directory, root):
    directory = Path(directory)
    index = json.loads((directory / "index.json").read_text(encoding="utf-8"))
    if not isinstance(index, dict) or index.get("schema_version") != 1 or not isinstance(index.get("sources"), list):
        raise ValueError("invalid index schema")
    documents = {}
    for entry in index["sources"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str):
            raise ValueError("invalid source metadata")
        name = relative(entry.get("path"))
        if name in documents:
            raise ValueError("duplicate source: " + name)
        _, data, lines = source(root, name)
        if digest(data) != entry["sha256"]:
            raise ValueError("source stale (SHA-256 mismatch); rebuild in a new directory: " + name)
        documents[name] = (lines, structure(lines))
    chunks, seen = [], set()
    for line in (directory / "chunks.jsonl").read_text(encoding="utf-8").splitlines():
        c = json.loads(line)
        if not isinstance(c, dict) or c.get("file") not in documents:
            raise ValueError("invalid chunk source")
        a, b = c.get("start_line"), c.get("end_line")
        lines, (_, headings, safe, _) = documents[c["file"]]
        if type(a) is not int or type(b) is not int or not 1 <= a <= b <= len(lines):
            raise ValueError("invalid chunk line range")
        if c.get("text") != "".join(lines[a - 1:b]):
            raise ValueError("chunk text differs from source; rebuild index")
        expected = make_chunk(c["file"], lines, a - 1, b, headings)
        if (c.get("id") != expected["id"] or c["id"] in seen
                or c.get("section") != expected["section"]
                or a - 1 not in safe or b not in safe):
            raise ValueError("invalid chunk id/section/boundary")
        pages, mapping = c.get("pages"), c.get("mapping")
        if (not isinstance(pages, list) or any(type(p) is not int or p <= 0 for p in pages)
                or mapping not in (None, "external:agent/chunks.jsonl")
                or (pages and mapping is None)):
            raise ValueError("invalid chunk pages/mapping")
        seen.add(c["id"])
        chunks.append(c)
    return chunks


def terms(text):
    """Binary word/character n-grams: repetitions never add term frequency."""
    result = set(re.findall(r"[a-z0-9_]+", text.casefold()))
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        for n in (1, 2, 3):
            result.update(run[i:i + n] for i in range(len(run) - n + 1))
    return result


def snippet(text, needles):
    # Return complete blank-line-delimited blocks, even when an equation is large.
    lines = text.splitlines(keepends=True)
    _, _, _, breaks = structure(lines, frontmatter=False)
    cuts = sorted(breaks | {0, len(lines)})
    blocks = ["".join(lines[a:b]) for a, b in zip(cuts, cuts[1:])]
    return max(blocks, key=lambda b: len(terms(b) & needles), default=text)


def query(chunks, args):
    needles = terms(args.query)
    phrases = [p.casefold() for p in args.terms or [] if p.strip()]
    for phrase in phrases:
        needles.update(terms(phrase))
    documents = [(terms(c["text"]), terms(" ".join(c["section"]))) for c in chunks]
    frequency = Counter(t for body, heading in documents for t in body | heading)
    weights = {t: math.log(1 + len(chunks) / (1 + frequency[t])) * (0.2 if len(t) == 1 and "\u3400" <= t <= "\u9fff" else 1)
               for t in needles}
    hits = []
    for c, (body, heading) in zip(chunks, documents):
        score = sum(w * ((t in body) + 2 * (t in heading)) for t, w in weights.items())
        score += sum(3 * (p in c["text"].casefold()) + 6 * (p in " ".join(c["section"]).casefold()) for p in set(phrases))
        if score > 0:
            hit = {k: v for k, v in c.items() if k != "text"}
            hit.update(score=round(score, 6), snippet=snippet(c["text"], needles))
            hits.append(hit)
    hits.sort(key=lambda h: (-h["score"], h["file"], h["start_line"]))
    return {"notice": NOTICE, "hits": hits[:args.limit]}


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("limit must be positive")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--root", required=True)
    b.add_argument("--files", nargs="+", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--agent-index")
    for command in ("query", "read"):
        p = sub.add_parser(command)
        p.add_argument("index_dir")
        p.add_argument("--root", required=True)
        if command == "query":
            p.add_argument("--query", required=True)
            p.add_argument("--terms", nargs="+")
            p.add_argument("--limit", type=positive, default=5)
        else:
            p.add_argument("--id", required=True)
    args = parser.parse_args(argv)
    try:
        root = Path(args.root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("root must be a directory")
        if args.command == "build":
            result = build(args, root)
        else:
            chunks = load(args.index_dir, root)
            if args.command == "query":
                result = query(chunks, args)
            else:
                result = next((dict(c, notice=NOTICE) for c in chunks if c["id"] == args.id), None)
                if result is None:
                    raise ValueError("unknown chunk id: " + args.id)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
