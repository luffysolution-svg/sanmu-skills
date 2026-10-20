#!/usr/bin/env python3
"""Validate channel manifests and build packages from Git-tracked skill files."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_FILES = ("plugin.json", ".claude-plugin/plugin.json", "README.md", "README.en.md", "LICENSE")
BLOCKED_PARTS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".search-work", "runs"}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def skill_header(text):
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
    if not match:
        raise ValueError("SKILL.md must start with YAML frontmatter")
    fields = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if separator and not key.startswith(" "):
            fields[key] = value.strip()
    return fields, match


def tracked_files(root):
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "-z"],
        check=True, stdout=subprocess.PIPE,
    )
    return sorted(set(result.stdout.decode("utf-8").split("\0")) - {""})


def validate(root=ROOT):
    release = read_json(root / "distribution/manifest.json")
    version = release["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version):
        raise ValueError("Release version must be a semantic version")
    names = [skill["name"] for skill in release["skills"]]
    if len(names) != len(set(names)) or not names:
        raise ValueError("Skill names must be unique and non-empty")
    for skill in release["skills"]:
        name = skill["name"]
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64:
            raise ValueError(f"Invalid skill name: {name}")
        directory = root / "skills" / name
        fields, _ = skill_header((directory / "SKILL.md").read_text(encoding="utf-8"))
        if fields.get("name") != name or not 1 <= len(fields.get("description", "")) <= 1024:
            raise ValueError(f"Invalid Agent Skills metadata: {name}")
        if not skill.get("displayName") or not skill.get("summary"):
            raise ValueError(f"Missing SkillHub display metadata: {name}")
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
        if BLOCKED_PARTS.intersection(parts) or any(p == ".env" or p.startswith(".env.") for p in parts):
            raise ValueError(f"Private or installed files must not be packaged: {path}")
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
    return release, selected


def skillhub_document(text, skill, release):
    _, match = skill_header(text)
    extra = {
        "slug": skill["name"],
        "displayName": skill["displayName"],
        "version": release["version"],
        "summary": skill["summary"],
        "license": release["license"],
    }
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
    bundle = {path: (root / path).read_bytes() for path in files}
    bundle_path = output / f"{release['name']}-{release['version']}.zip"
    write_zip(bundle_path, bundle)
    archives = [bundle_path]
    for skill in release["skills"]:
        prefix = f"skills/{skill['name']}/"
        entries = {path[len(prefix):]: data for path, data in bundle.items() if path.startswith(prefix)}
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
    checksums = []
    for path in archives:
        checksums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(output).as_posix()}")
    (output / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    return archives


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "build"))
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if args.command == "check":
        release, files = validate()
        print(f"{release['name']} {release['version']}: {len(release['skills'])} skills, {len(files)} tracked package files")
    else:
        for path in build(args.out):
            print(path)


if __name__ == "__main__":
    main()
