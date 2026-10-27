#!/usr/bin/env python3
"""Validate channel manifests and build packages from Git-tracked skill files."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from urllib.parse import unquote, urlsplit
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_FILES = ("plugin.json", ".claude-plugin/plugin.json", "README.md", "README.en.md", "LICENSE")
BLOCKED_PARTS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "cache", ".cache", ".search-work", "runs",
    "tests", "test", "examples", "docs", ".ocr-work", ".ocr_work", "ocr-out",
}
DEVELOPMENT_PARTS = {"tests", "examples", "docs"}
SKILL_ROOT_FILES = {"SKILL.md", "LICENSE", "README.md", "README.en.md", "requirements.txt"}
# SkillHub rejects uploads containing files without an extension; license stays in the bundle.
SKILLHUB_EXCLUDED_FILES = {"LICENSE"}
ZCODE_SHELL_DIR = "distribution/zcode/sanmu-paper-search"
ZCODE_SHELL_FILES = (
    ".zcode-plugin/plugin.json",
    ".claude-plugin/plugin.json",
    "package.json",
    "cordis.patch.yml",
    "assets/panel.html",
    "commands/help.md",
    "scripts/mcp-server.mjs",
    "README.md",
)
FRONTMATTER_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def skill_header(text):
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
    if not match:
        raise ValueError("SKILL.md must start with YAML frontmatter")
    fields = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#") or line.startswith((" ", "\t")):
            continue
        key, separator, value = line.partition(":")
        if not separator or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", key):
            raise ValueError(f"Invalid frontmatter field: {line}")
        if key in fields:
            raise ValueError(f"Duplicate frontmatter field: {key}")
        fields[key] = value.strip()
    return fields, match


def validate_standard_header(text, name):
    fields, match = skill_header(text)
    unknown = set(fields) - FRONTMATTER_FIELDS
    if unknown:
        raise ValueError(f"Nonstandard Agent Skills frontmatter fields in {name}: {sorted(unknown)}")
    # Portable sources use plain or quoted single-line required values.
    values = {}
    for key in ("name", "description"):
        value = fields.get(key, "")
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid quoted frontmatter {key}: {name}") from error
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("''", "'")
        if not isinstance(value, str) or not value or value in {"|", ">", "null", "true", "false"}:
            raise ValueError(f"Frontmatter {key} must be a non-empty string: {name}")
        values[key] = value
    if values["name"] != name or len(values["description"]) > 1024 or re.search(r"[<>]", values["description"]):
        raise ValueError(f"Invalid Agent Skills metadata: {name}")
    return fields, match


def runtime_file(path, name):
    relative = PurePosixPath(path).relative_to(f"skills/{name}")
    return (relative.as_posix() in SKILL_ROOT_FILES
            or len(relative.parts) > 1 and relative.parts[0] in {"scripts", "references", "assets"})


def validate_runtime_references(root, selected, names):
    packaged = set(selected)
    for name in names:
        directory = root / "skills" / name
        documents = [directory / "SKILL.md", *sorted((directory / "references").rglob("*.md"))]
        for document in documents:
            text = document.read_text(encoding="utf-8")
            # Ignore examples in fenced code; check actual Markdown links and inline reference paths.
            text = re.sub(r"(?ms)^[ \t]*(?:```|~~~)[^\n]*\n.*?^[ \t]*(?:```|~~~)[^\n]*(?:\n|$)", "", text)
            targets = re.findall(r"`((?:\./|\.\./)*references/[^`\s]+\.md(?:#[^`\s]*)?)`", text)
            prose = re.sub(r"`+[^`\n]*`+", "", text)
            # Chemical states such as [Fe(CN)6](aq) are not document references.
            links = re.findall(r"\[[^\]\n]*\]\(<?([^\s)>]+)>?(?:\s+[^)]*)?\)", prose)
            targets += [target for target in links if target not in {"aq", "s", "l", "g"}]
            targets += re.findall(r"(?m)^\s*\[[^\]]+\]:\s*<?([^\s>]+)>?", prose)
            for target in targets:
                parsed = urlsplit(target)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                path = (document.parent / unquote(parsed.path)).resolve()
                try:
                    relative = path.relative_to(directory.resolve())
                except ValueError as error:
                    raise ValueError(f"Runtime link escapes standalone skill: {document}: {target}") from error
                package_path = f"skills/{name}/{relative.as_posix()}"
                if package_path not in packaged or not path.is_file():
                    raise ValueError(f"Runtime reference is missing from package: {document}: {target}")


def tracked_files(root):
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "-z"],
        check=True, stdout=subprocess.PIPE,
    )
    return sorted(set(result.stdout.decode("utf-8").split("\0")) - {""})


def validate(root=ROOT):
    release = read_json(root / "distribution/manifest.json")
    version = release["version"]
    for value in (version, release.get("workbuddyVersion", version)):
        if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", value):
            raise ValueError("Release and WorkBuddy versions must be semantic versions")
    names = [skill["name"] for skill in release["skills"]]
    if len(names) != len(set(names)) or not names:
        raise ValueError("Skill names must be unique and non-empty")
    for skill in release["skills"]:
        name = skill["name"]
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64:
            raise ValueError(f"Invalid skill name: {name}")
        directory = root / "skills" / name
        validate_standard_header((directory / "SKILL.md").read_text(encoding="utf-8"), name)
        if not skill.get("displayName") or not skill.get("summary"):
            raise ValueError(f"Missing SkillHub display metadata: {name}")
        if not skill.get("displayNameEn") or not skill.get("summaryEn") or not release.get("author"):
            raise ValueError(f"Missing WorkBuddy display metadata: {name}")
        workbuddy_name = skill.get("workbuddyName", name)
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", workbuddy_name) or len(workbuddy_name) > 64:
            raise ValueError(f"Invalid WorkBuddy skill name: {name}")
        for field in ("workbuddyDisplayName", "workbuddyDisplayNameEn"):
            value = skill.get(field, "")
            if not isinstance(value, str) or not re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9_-]+", value):
                raise ValueError(f"Invalid WorkBuddy paid display name {field}: {name}")
        if not (directory / "LICENSE").is_file():
            raise ValueError(f"Standalone skill lacks LICENSE: {name}")
    for path in ("plugin.json", ".claude-plugin/plugin.json"):
        plugin = read_json(root / path)
        if plugin.get("name") != release["name"] or plugin.get("version") != version:
            raise ValueError(f"Plugin name/version differs from release manifest: {path}")
    for path in (".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json"):
        market = read_json(root / path)
        if market.get("name") != release["name"] or [p["name"] for p in market["plugins"]] != [release["name"]]:
            raise ValueError(f"Unexpected marketplace entries: {path}")
    files = tracked_files(root)
    selected = []
    for path in files:
        if path not in PLUGIN_FILES and not any(path.startswith(f"skills/{name}/") for name in names):
            continue
        parts = PurePosixPath(path).parts
        # Development trees are kept in the repository, never in runtime archives.
        if len(parts) > 2 and parts[0] == "skills" and parts[2] in DEVELOPMENT_PARTS:
            continue
        if len(parts) > 2 and parts[0] == "skills" and parts[2] == "ROADMAP.md":
            continue
        if (BLOCKED_PARTS.intersection(part.lower() for part in parts)
                or any(p == ".env" or p.startswith(".env.") for p in parts)
                or PurePosixPath(path).suffix.lower() in {".pyc", ".pyo"}
                or PurePosixPath(path).name in {"_selftest.py", "ROADMAP.md"}
                or PurePosixPath(path).name.startswith("test_")):
            raise ValueError(f"Development, private or installed files must not be packaged: {path}")
        if parts[0] == "skills" and not runtime_file(path, parts[1]):
            raise ValueError(f"Unexpected runtime package input: {path}")
        source = root / path
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"Package input is not a regular file: {path}")
        selected.append(path)
    for path in PLUGIN_FILES:
        if path not in selected:
            raise ValueError(f"Package input must be tracked in Git: {path}")
    for name in names:
        for filename in ("SKILL.md", "LICENSE", "README.md", "README.en.md"):
            if f"skills/{name}/{filename}" not in selected:
                raise ValueError(f"Missing tracked package file: {name}/{filename}")
    validate_runtime_references(root, selected, names)
    zcode_shell_root = root / ZCODE_SHELL_DIR
    for rel in ZCODE_SHELL_FILES:
        shell_file = zcode_shell_root / rel
        if shell_file.is_symlink() or not shell_file.is_file():
            raise ValueError(f"Missing or invalid ZCode plugin shell file: {ZCODE_SHELL_DIR}/{rel}")
        if f"{ZCODE_SHELL_DIR}/{rel}" not in files:
            raise ValueError(f"ZCode plugin shell file must be tracked in Git: {rel}")
    for rel_manifest in (".zcode-plugin/plugin.json", ".claude-plugin/plugin.json", "package.json"):
        zcode_plugin = read_json(zcode_shell_root / rel_manifest)
        if zcode_plugin.get("name") != "sanmu-paper-search" or zcode_plugin.get("version") != version:
            raise ValueError(f"ZCode plugin name/version differs from release manifest: {rel_manifest}")
    server_text = (zcode_shell_root / "scripts/mcp-server.mjs").read_text(encoding="utf-8")
    panel_text = (zcode_shell_root / "assets/panel.html").read_text(encoding="utf-8")
    server_versions = re.findall(r'\bversion:\s*"(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)"', server_text)
    panel_versions = re.findall(r'(?:\bversion:\s*"|class="brand-sub">v)(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)', panel_text)
    if not server_versions or not panel_versions or any(value != version for value in server_versions + panel_versions):
        raise ValueError("ZCode server/panel version differs from release manifest")
    budget = release.get("packageBudget", {"maxFiles": 200, "maxBytes": 5 * 1024 * 1024})
    for key in ("maxFiles", "maxBytes"):
        if type(budget.get(key)) is not int or budget[key] <= 0:
            raise ValueError(f"Invalid project package budget: {key}")
    total_bytes = sum((root / path).stat().st_size for path in selected)
    if len(selected) > budget["maxFiles"] or total_bytes > budget["maxBytes"]:
        raise ValueError(f"Project package budget exceeded: {len(selected)} files, {total_bytes} bytes")
    return release, selected


def skillhub_document(text, skill, release):
    return platform_document(text, {
        "slug": skill["name"],
        "displayName": skill["displayName"],
        "version": release["version"],
        "summary": skill["summary"],
        "license": release["license"],
    })


def workbuddy_document(text, skill, release):
    # WorkBuddy requires description_zh, description_en, version and author;
    # uploads without display names were rejected in practice.
    text = re.sub(r"(?m)^name:[^\r\n]*", f"name: {skill.get('workbuddyName', skill['name'])}", text, count=1)
    return platform_document(text, {
        "display_name": skill["workbuddyDisplayName"],
        "display_name_en": skill["workbuddyDisplayNameEn"],
        "description_zh": skill["summary"],
        "description_en": skill["summaryEn"],
        "version": release.get("workbuddyVersion", release["version"]),
        "author": release["author"],
    })


def platform_document(text, extra):
    _, match = skill_header(text)
    header = match.group(1)
    for key, value in extra.items():
        header += f"\n{key}: {json.dumps(value, ensure_ascii=False)}"
    return "---\n" + header + "\n---\n" + text[match.end():]


def write_zip(path, entries):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)


def build(output, root=ROOT):
    release, files = validate(root)
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Output already exists; choose a new directory: {output}")
    output.mkdir(parents=True)
    skillhub = output / "skillhub"
    skillhub.mkdir()
    workbuddy = output / "workbuddy"
    workbuddy.mkdir()
    bundle = {path: (root / path).read_bytes() for path in files}
    bundle_path = output / f"{release['name']}-{release['version']}.zip"
    write_zip(bundle_path, bundle)
    archives = [bundle_path]
    for skill in release["skills"]:
        prefix = f"skills/{skill['name']}/"
        entries = {path[len(prefix):]: data for path, data in bundle.items()
                   if path.startswith(prefix) and path[len(prefix):] not in SKILLHUB_EXCLUDED_FILES}
        text = entries["SKILL.md"].decode("utf-8")
        entries["SKILL.md"] = skillhub_document(text, skill, release).encode("utf-8")
        directory = skillhub / skill["name"]
        for name, data in entries.items():
            destination = directory / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        archive = skillhub / f"{skill['name']}-{release['version']}.zip"
        write_zip(archive, entries)
        archives.append(archive)
        # Same file set as SkillHub, with SKILL.md at the archive root.
        entries["SKILL.md"] = workbuddy_document(text, skill, release).encode("utf-8")
        archive = workbuddy / f"{skill.get('workbuddyName', skill['name'])}-{release.get('workbuddyVersion', release['version'])}.zip"
        write_zip(archive, entries)
        archives.append(archive)
    zcode_shell_root = root / ZCODE_SHELL_DIR
    if zcode_shell_root.is_dir():
        zcode = output / "zcode"
        zcode.mkdir()
        zcode_dir = zcode / "sanmu-paper-search"
        zcode_entries = {}
        for rel in ZCODE_SHELL_FILES:
            zcode_entries[rel] = (zcode_shell_root / rel).read_bytes()
        search_prefix = "skills/sanmu-paper-search/"
        for path, data in bundle.items():
            if not path.startswith(search_prefix):
                continue
            rel = path[len(search_prefix):]
            if rel == "LICENSE":
                zcode_entries["LICENSE"] = data
            elif rel == "SKILL.md" or rel.startswith("references/"):
                zcode_entries[f"skills/sanmu-paper-search/{rel}"] = data
            elif rel.startswith("scripts/"):
                zcode_entries[rel] = data
                zcode_entries[f"skills/sanmu-paper-search/{rel}"] = data
        for rel, data in zcode_entries.items():
            dest = zcode_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        zcode_archive = zcode / f"sanmu-paper-search-zcode-plugin-{release['version']}.zip"
        write_zip(zcode_archive, zcode_entries)
        archives.append(zcode_archive)
    checksums = []
    for path in archives:
        checksums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(output).as_posix()}")
    # LF endings so `sha256sum -c` works everywhere, even when built on Windows.
    (output / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8", newline="\n")
    return archives


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "build"))
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if args.command == "check":
        release, files = validate()
        size = sum((ROOT / path).stat().st_size for path in files)
        print(f"{release['name']} {release['version']}: {len(release['skills'])} skills, "
              f"{len(files)} tracked runtime files, {size} uncompressed bytes")
        for skill in release["skills"]:
            selected = [path for path in files if path.startswith(f"skills/{skill['name']}/")]
            print(f"  {skill['name']}: {len(selected)} files, "
                  f"{sum((ROOT / path).stat().st_size for path in selected)} bytes")
    else:
        for path in build(args.out):
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                print(f"{path}: {len(entries)} files, {sum(item.file_size for item in entries)} "
                      f"uncompressed bytes, {path.stat().st_size} ZIP bytes")


if __name__ == "__main__":
    main()
