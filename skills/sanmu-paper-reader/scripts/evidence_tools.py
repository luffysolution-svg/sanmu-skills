"""Offline Markdown evidence checks (Python 3.10+, standard library only)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import sys

MAX_BYTES = 32 * 1024 * 1024
SCOPE = ("Mechanical checks establish only file existence, byte hash agreement, and "
         "a whitespace-normalized quotation in Markdown prose. Locator pages/figures, "
         "claim meaning, support relationships, measurements, and scientific correctness "
         "are not automatically verified. Mechanical success is not scientific approval; "
         "human verification is still required.")


class Invalid(ValueError):
    pass


class CLIParser(argparse.ArgumentParser):
    def error(self, message):
        raise Invalid("invalid CLI arguments; use --help for the command contract")


def root_path(value):
    root = Path(value).resolve(strict=True)
    if not root.is_dir():
        raise Invalid("root must be an existing directory")
    return root


def confined(path, root):
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise Invalid("source resolves outside root (including symbolic links)")
    return resolved


def read_bytes(path):
    if not stat.S_ISREG(path.stat().st_mode):
        raise Invalid("input must be a regular file")
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise Invalid("input exceeds the 32 MiB limit")
    return data


def prose(data):
    """Remove leading YAML and fenced code, without changing scientific symbols."""
    lines = data.decode("utf-8-sig").splitlines()
    start = 0
    if lines and lines[0].strip() == "---":
        for start in range(1, len(lines)):
            if lines[start].strip() in ("---", "..."):
                start += 1
                break
        else:
            raise Invalid("unclosed YAML frontmatter")
    body, fence = [], None
    for line in lines[start:]:
        match = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = None
            body.append("")
        elif match:
            fence = match[1]
            body.append("")
        else:
            body.append(line)
    return "\n".join(body)


def headings(body):
    result, previous = [], ""
    for line in body.splitlines():
        match = re.match(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?)|[ \t]*)$", line)
        if match:
            text = re.sub(r"[ \t]+#+[ \t]*$", "", match[2] or "").strip()
            result.append({"level": len(match[1]), "text": text})
        elif previous.strip() and re.fullmatch(r" {0,3}(=+|-+)[ \t]*", line):
            result.append({"level": 1 if line.lstrip()[0] == "=" else 2,
                           "text": previous.strip()})
        previous = "" if match else line
    return result


def markdown_files(root):
    def walk(directory, ancestors):
        real = confined(directory, root)
        if real in ancestors:
            raise Invalid("directory symbolic-link cycle inside root")
        with os.scandir(directory) as entries:
            paths = sorted((Path(entry.path) for entry in entries), key=lambda p: p.name)
        for path in paths:
            target = confined(path, root)
            mode = target.stat().st_mode
            if stat.S_ISDIR(mode):
                yield from walk(path, ancestors | {real})
            elif path.suffix.lower() == ".md":
                if not stat.S_ISREG(mode):
                    raise Invalid("Markdown source must be a regular file")
                yield path
    return walk(root, set())


def inventory(root, protected):
    sources = []
    for path in markdown_files(root):
        protected.append(path)
        relative = path.relative_to(root).as_posix()
        data = read_bytes(path)
        outline = headings(prose(data))
        sources.append({"id": "S-" + hashlib.sha256(relative.encode("utf-8")).hexdigest()[:12],
                        "path": relative, "sha256": hashlib.sha256(data).hexdigest(),
                        "title": next((h["text"] for h in outline if h["level"] == 1), path.name),
                        "headings": outline})
    return {"schema_version": 1, "sources": sorted(sources, key=lambda s: s["path"])}


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def normalize(value):
    return " ".join(value.split())


def source_path(value, root):
    if (not nonempty(value) or "\\" in value or PureWindowsPath(value).drive
            or PureWindowsPath(value).root or Path(value).is_absolute()
            or ".." in Path(value).parts or Path(value).suffix.lower() != ".md"):
        raise Invalid("source.path must be a relative POSIX .md path without '..'")
    return confined(root / value, root)


def failure_message(error):
    # OSError messages contain absolute filenames; never expose them in reports.
    if isinstance(error, Invalid):
        return str(error)
    if isinstance(error, UnicodeError):
        return "input is not valid UTF-8"
    if isinstance(error, json.JSONDecodeError):
        return "invalid JSON at line %d column %d" % (error.lineno, error.colno)
    if isinstance(error, RecursionError):
        return "input nesting is too deep"
    if isinstance(error, RuntimeError):
        return "cannot resolve path (possible symbolic-link cycle)"
    return "file access failed (%s, errno=%s)" % (type(error).__name__, getattr(error, "errno", None))


def report(errors, checked=0):
    return {"schema_version": 1, "mechanical_pass": not errors,
            "scientific_status": "not_verified", "verification_scope": SCOPE,
            "checked_evidence": checked, "errors": errors}


FILE_ERRORS = (ValueError, OSError, RuntimeError)


def check(ledger, root, protected):
    errors, checked, seen = [], 0, set()
    document = json.loads(read_bytes(ledger).decode("utf-8-sig"))
    if not isinstance(document, dict):
        return report(["ledger must be an object"])
    if type(document.get("schema_version")) is not int or document.get("schema_version") != 1:
        errors.append("schema_version must be integer 1")
    evidence = document.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return report(errors + ["evidence must be a nonempty list"])
    for index, entry in enumerate(evidence):
        prefix = "evidence[%d]: " % index
        def error(message):
            errors.append(prefix + message)
        if not isinstance(entry, dict):
            error("entry must be an object")
            continue
        for key in ("id", "claim", "kind", "support"):
            if not nonempty(entry.get(key)):
                error(key + " must be a nonempty string")
        identifier = entry.get("id")
        if nonempty(identifier):
            if identifier in seen:
                error("duplicate id")
            seen.add(identifier)
        if entry.get("kind") not in ("reported", "interpretation", "hypothesis"):
            error("invalid kind")
        if entry.get("support") not in ("supports", "contradicts", "context"):
            error("invalid support")
        if "measurements" in entry:
            measurements = entry["measurements"]
            if not isinstance(measurements, list):
                error("measurements must be a list")
            else:
                for position, measurement in enumerate(measurements):
                    if not isinstance(measurement, dict) or any(
                            not nonempty(measurement.get(k)) for k in ("value", "unit", "conditions")):
                        error("measurements[%d] requires nonempty value/unit/conditions strings" % position)
        source = entry.get("source")
        if not isinstance(source, dict):
            error("source must be an object")
            continue
        valid = True
        for key in ("path", "sha256", "quote", "locator"):
            if not nonempty(source.get(key)):
                error("source." + key + " must be a nonempty string")
                valid = False
        digest = source.get("sha256")
        if nonempty(digest) and not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            error("source.sha256 must contain 64 hexadecimal characters")
            valid = False
        try:
            path = source_path(source.get("path"), root)
            protected.append(path)
            if not valid:
                continue
            data = read_bytes(path)
            if hashlib.sha256(data).hexdigest() != digest.lower():
                error("source SHA-256 mismatch")
            if normalize(source["quote"]) not in normalize(prose(data)):
                error("source quote not found in Markdown prose")
            checked += 1
        except FILE_ERRORS as exc:
            error(failure_message(exc))
    return report(errors, checked)


def encode(document):
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_output(filename, data, protected):
    output = Path(filename)
    resolved = output.resolve()
    for path in protected:
        if resolved == path.resolve() or (output.exists() and output.samefile(path)):
            raise Invalid("output must not alias the ledger or a Markdown source")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        stream = output.open("xb")
    except FileExistsError:
        if read_bytes(output) != data:
            raise Invalid("output already exists with different content; refusing overwrite")
    else:
        with stream:
            stream.write(data)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = CLIParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inv = commands.add_parser("inventory")
    inv.add_argument("root")
    inv.add_argument("--out", required=True)
    chk = commands.add_parser("check")
    chk.add_argument("ledger")
    chk.add_argument("--root", required=True)
    chk.add_argument("--out")
    try:
        args = parser.parse_args(argv)
    except Invalid as exc:
        sys.stdout.write(encode(report([str(exc)])).decode("utf-8"))
        return 1
    protected = [Path(args.ledger)] if args.command == "check" else []
    try:
        root = root_path(args.root)
        if args.command == "inventory":
            result = inventory(root, protected)
        else:
            try:
                result = check(Path(args.ledger), root, protected)
            except (*FILE_ERRORS, json.JSONDecodeError, RecursionError) as exc:
                result = report([failure_message(exc)])
        if args.out:
            write_output(args.out, encode(result), protected)
        else:
            sys.stdout.write(encode(result).decode("utf-8"))
        return 0 if result.get("mechanical_pass", True) else 1
    except (*FILE_ERRORS, RecursionError) as exc:
        sys.stdout.write(encode(report([failure_message(exc)])).decode("utf-8"))
        return 1


if __name__ == "__main__":
    sys.exit(main())
