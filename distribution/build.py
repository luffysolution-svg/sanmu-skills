#!/usr/bin/env python3
"""Validate channel manifests and build packages from Git-tracked skill files."""

import argparse
from fnmatch import fnmatchcase
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
from urllib.parse import unquote, urlsplit
import zipfile


ROOT = Path(__file__).resolve().parents[1]
ROOT_RUNTIME_FILES = (
    "scripts/sanmu.mjs", "scripts/runtime.mjs", "scripts/panel-server.mjs", "scripts/zotero-tools.mjs",
)
ROOT_ASSET_FILES = (
    "assets/favicon.ico", "assets/icon-64.png", "assets/icon.png", "assets/icon.svg",
    "assets/logo.png", "assets/logo.svg", "assets/panel.html",
)
ROOT_HOST_FILES = (
    "plugin.json", "mcp.json", ".mcp.json", ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json", ".codex-plugin/plugin.json", ".codex-plugin/mcp-env.json",
    ".agents/plugins/marketplace.json", ".zcode-plugin/plugin.json", "marketplace.json", "package.json", "cordis.patch.yml",
    *ROOT_RUNTIME_FILES, "scripts/mcp-server.mjs", "scripts/dsh-client.js", "locale/en.json", "locale/zh.json",
    *ROOT_ASSET_FILES, "extensions/pi.ts",
)
ZCODE_SHELL_DIR = "distribution/zcode/sanmu-paper-search"
CANONICAL_RUNTIME_FILES = tuple(f"{ZCODE_SHELL_DIR}/{path}" for path in (
    "scripts/mcp-server.mjs", "assets/panel.html", "scripts/dsh-plugin.mjs",
))
PLUGIN_FILES = (*ROOT_HOST_FILES, *CANONICAL_RUNTIME_FILES, "README.md", "README.en.md", "LICENSE")
BLOCKED_PARTS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "cache", ".cache", ".search-work", "runs",
    "tests", "test", "examples", "docs", ".ocr-work", ".ocr_work", "ocr-out",
}
DEVELOPMENT_PARTS = {"tests", "examples", "docs"}
SKILL_ROOT_FILES = {"SKILL.md", "LICENSE", "README.md", "README.en.md", "requirements.txt"}
# SkillHub rejects uploads containing files without an extension; license stays in the bundle.
SKILLHUB_EXCLUDED_FILES = {"LICENSE"}
SKILLHUB_LIMITS = {"maxFiles": 200, "maxBytes": 10 * 1024 * 1024}
ARCHIVE_MTIME = 1767225600  # 2026-01-01T00:00:00Z, independent of source mtimes and time zones.
ZCODE_SHELL_FILES = (
    ".zcode-plugin/plugin.json",
    ".claude-plugin/plugin.json",
    "package.json",
    "cordis.patch.yml",
    "assets/panel.html",
    "commands/help.md",
    "scripts/mcp-server.mjs",
    "scripts/dsh-plugin.mjs",
    "scripts/dsh-client.js",
    "locale/en.json",
    "locale/zh.json",
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


def local_path(value, label):
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or value.startswith("/") or ".." in PurePosixPath(value).parts):
        raise ValueError(f"Invalid package-relative path in {label}: {value}")
    path = PurePosixPath(value).as_posix().rstrip("/")
    if path == "." or "$" in path or path.startswith("!"):
        raise ValueError(f"Invalid package-relative path in {label}: {value}")
    return path


def require_path(value, files, label, directory=False, pattern=False):
    path = local_path(value, label)
    if directory:
        present = any(name.startswith(path + "/") for name in files)
    elif pattern and "*" in path:
        present = any(fnmatchcase(name, path) for name in files)
    else:
        present = path in files
    if not present:
        raise ValueError(f"Missing packaged path in {label}: {value}")
    return path


def npm_files(root, files):
    package = read_json(root / "package.json")
    patterns = package.get("files")
    if not isinstance(patterns, list) or not patterns:
        raise ValueError("Root package.json files must be a non-empty allowlist")
    patterns = [local_path(pattern, "package.json files") for pattern in patterns]
    automatic = {"package.json", "README.md", "README.en.md", "LICENSE"}
    selected = [name for name in files if name in automatic or any(
        name == pattern or name.startswith(pattern + "/") or fnmatchcase(name, pattern)
        for pattern in patterns
    )]
    missing = set(files) - set(selected)
    if missing:
        raise ValueError(f"Root package.json files omits runtime inputs: {sorted(missing)}")
    for pattern in patterns:
        if not any(name == pattern or name.startswith(pattern + "/") or fnmatchcase(name, pattern) for name in selected):
            raise ValueError(f"Root package.json files has no packaged input: {pattern}")
    return selected


def validate_mcp(config, files, label, variable, entrypoint, cli=False):
    servers = config.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        raise ValueError(f"Missing MCP server configuration: {label}")
    for server in servers.values():
        args = server.get("args", []) if isinstance(server, dict) else []
        expected = [f"${{{variable}}}/{entrypoint}", *(["mcp"] if cli else [])]
        if (not isinstance(server, dict) or server.get("command") != "node"
                or server.get("type", "stdio") != "stdio" or args != expected):
            raise ValueError(f"Invalid packaged MCP command: {label}")
        require_path(entrypoint, files, label)


def validate_patch(root, path, files):
    text = (root / path).read_text(encoding="utf-8")
    entries = re.findall(r'(?m)^\s*(?:-\s*)?name:\s*[\"\']?([^\s\"\']+)[\"\']?\s*$', text)
    if not entries:
        raise ValueError(f"Missing DeepSeek plugin entry: {path}")
    package = read_json(root / "package.json")
    for entry in entries:
        if entry == package.get("name"):
            adapter = require_path(package.get("main"), files, f"{path} package main")
        elif entry.startswith("./"):
            adapter = require_path(entry, files, path)
        else:
            raise ValueError(f"DeepSeek patch must reference its own package or a packaged adapter: {path}: {entry}")
        source = root / adapter
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"Missing or invalid DeepSeek adapter file: {path}: {adapter}")


def validate_client(root, package, files, label):
    client = package.get("dsh", {}).get("client", {})
    expected_inject = {"@deepseek-ai/dsh-client-ui-sidebar-right", "@deepseek-ai/dsh-client-locale",
                       "@deepseek-ai/dsh-api-remotes"}
    if (client.get("platform") != "web" or client.get("immediately") is not True
            or set(client.get("inject", [])) != expected_inject):
        raise ValueError(f"Invalid native DeepSeek client manifest: {label}")
    exports = package.get("exports", {})
    for key, expected in (("./client", "scripts/dsh-client.js"), ("./icon", "assets/icon.svg"),
                          ("./locale/*.json", "locale/*.json")):
        target = require_path(exports.get(key), files, f"{label} {key}", pattern=True)
        if target != expected:
            raise ValueError(f"Unexpected native DeepSeek resource export: {label}: {key}")
    for target in exports.values():
        require_path(target, files, f"{label} exports", pattern=True)
    for language in ("en", "zh"):
        path = require_path(f"locale/{language}.json", files, f"{label} metadata")
        metadata = read_json(root / path).get("meta", {})
        if any(not isinstance(metadata.get(key), str) or not metadata[key].strip() for key in ("title", "description")):
            raise ValueError(f"Missing localized DeepSeek display metadata: {label}: {language}")


def zcode_sources(files):
    sources = {path: f"{ZCODE_SHELL_DIR}/{path}" for path in ZCODE_SHELL_FILES}
    sources["LICENSE"] = "LICENSE"
    sources.update({path: path for path in files if path.startswith("skills/") or path in ROOT_RUNTIME_FILES or path in ROOT_ASSET_FILES})
    # Older ZCode hosts and MCP engines also discover search tools at scripts/.
    prefix = "skills/sanmu-paper-search/scripts/"
    for path in files:
        if path.startswith(prefix):
            target = "scripts/" + path[len(prefix):]
            if target in sources:
                raise ValueError(f"Conflicting ZCode runtime path: {target}")
            sources[target] = path
    return sources


def validate_hosts(root, release, files):
    version = release["version"]
    manifests = ("plugin.json", ".claude-plugin/plugin.json", ".codex-plugin/plugin.json", ".zcode-plugin/plugin.json", "package.json")
    for path in manifests:
        plugin = read_json(root / path)
        if plugin.get("name") != release["name"] or plugin.get("version") != version:
            raise ValueError(f"Plugin name/version differs from release manifest: {path}")
    portable = read_json(root / "plugin.json")
    if portable.get("$schema") != "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json":
        raise ValueError("Invalid portable plugin schema: plugin.json")
    if any(key in portable for key in ("skills", "mcpServers")):
        raise ValueError("Portable plugin uses automatic skills/ and mcp.json discovery")
    for path, mcp in ((".claude-plugin/plugin.json", ".mcp.json"), (".codex-plugin/plugin.json", ".codex-plugin/mcp-env.json")):
        plugin = read_json(root / path)
        if require_path(plugin.get("skills"), files, path, directory=True) != "skills":
            raise ValueError(f"Plugin must expose all three skills: {path}")
        if require_path(plugin.get("mcpServers"), files, path) != mcp:
            raise ValueError(f"Unexpected MCP manifest path: {path}")
    for path, variable in (("mcp.json", "PLUGIN_ROOT"), (".mcp.json", "CLAUDE_PLUGIN_ROOT")):
        config = read_json(root / path)
        if path == "mcp.json" and config.get("$schema") != "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json":
            raise ValueError("Invalid portable MCP schema: mcp.json")
        validate_mcp(config, files, path, variable, "scripts/sanmu.mjs", cli=True)
    for path in (".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json", "marketplace.json"):
        market = read_json(root / path)
        entries = market.get("plugins", [])
        if market.get("name") != release["name"] or [p.get("name") for p in entries] != [release["name"]]:
            raise ValueError(f"Unexpected marketplace entries: {path}")
        if any(p.get("version", version) != version for p in entries):
            raise ValueError(f"Marketplace version differs from release manifest: {path}")
        source = entries[0].get("source")
        if path.startswith(".claude-plugin/") or path == "marketplace.json":
            valid = source in {".", "./"} if isinstance(source, str) else False
        else:
            valid = isinstance(source, dict) and source.get("source") == "local" and source.get("path") in {".", "./"}
        if not valid:
            raise ValueError(f"Marketplace must reference the packaged plugin root: {path}")
    package = read_json(root / "package.json")
    if package.get("type") != "module" or package.get("bin", {}).get("sanmu") != "./scripts/sanmu.mjs":
        raise ValueError("Root package.json must expose the sanmu CLI")
    require_path(package["bin"]["sanmu"], files, "package.json bin")
    require_path(package.get("main"), files, "package.json main")
    validate_client(root, package, files, "package.json")
    pi = package.get("pi", {})
    if pi.get("skills") != ["./skills"] or pi.get("extensions") != ["./extensions/pi.ts"]:
        raise ValueError("Root package.json Pi paths must expose all three skills and the extension")
    require_path(pi["skills"][0], files, "package.json pi.skills", directory=True)
    require_path(pi["extensions"][0], files, "package.json pi.extensions")
    patch = package.get("dsh", {}).get("bundle", {}).get("patch")
    if require_path(patch, files, "package.json dsh.bundle.patch") != "cordis.patch.yml":
        raise ValueError("Unexpected DeepSeek patch path: package.json")
    validate_patch(root, "cordis.patch.yml", files)
    npm_files(root, files)


def validate(root=ROOT):
    release = read_json(root / "distribution/manifest.json")
    version = release["version"]
    for value in (version, release.get("workbuddyVersion", version)):
        if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", value):
            raise ValueError("Release and WorkBuddy versions must be semantic versions")
    if release.get("workbuddyVersion", version) != version:
        raise ValueError("WorkBuddy version differs from release manifest")
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
    validate_hosts(root, release, selected)
    for skill in release["skills"]:
        entries = skill_entries(root, selected, skill, release)
        validate_skillhub_budget(entries, skill["name"])
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
    zcode_files = zcode_sources(selected)
    for path in (".zcode-plugin/plugin.json", ".claude-plugin/plugin.json"):
        plugin = read_json(zcode_shell_root / path)
        if require_path(plugin.get("skills"), zcode_files, path, directory=True) != "skills":
            raise ValueError(f"ZCode plugin must expose all three skills: {path}")
        require_path(plugin.get("commands"), zcode_files, path, directory=True)
        validate_mcp(plugin, zcode_files, path, "CLAUDE_PLUGIN_ROOT", "scripts/mcp-server.mjs")
    package = read_json(zcode_shell_root / "package.json")
    require_path(package.get("main"), zcode_files, "ZCode package.json main")
    validate_client(zcode_shell_root, package, zcode_files, "ZCode package.json")
    patch = package.get("dsh", {}).get("bundle", {}).get("patch")
    if require_path(patch, zcode_files, "ZCode package.json dsh.bundle.patch") != "cordis.patch.yml":
        raise ValueError("Unexpected DeepSeek patch path: ZCode package.json")
    validate_patch(zcode_shell_root, "cordis.patch.yml", zcode_files)
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


def skill_entries(root, files, skill, release):
    prefix = f"skills/{skill['name']}/"
    entries = {path[len(prefix):]: (root / path).read_bytes() for path in files
               if path.startswith(prefix) and path[len(prefix):] not in SKILLHUB_EXCLUDED_FILES}
    entries["SKILL.md"] = skillhub_document(entries["SKILL.md"].decode("utf-8"), skill, release).encode("utf-8")
    return entries


def validate_skillhub_budget(entries, name):
    size = sum(len(data) for data in entries.values())
    if len(entries) > SKILLHUB_LIMITS["maxFiles"] or size > SKILLHUB_LIMITS["maxBytes"]:
        raise ValueError(f"SkillHub package limit exceeded for {name}: {len(entries)} files, {size} bytes "
                         "(maximum 200 files and 10 MiB)")


def write_zip(path, entries):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100755 if name == "scripts/sanmu.mjs" else 0o100644) << 16
            archive.writestr(info, data)


def write_npm_tgz(path, entries):
    # An npm installable tarball uses package/ and Unix permission bits, not st_mode.
    with path.open("wb") as destination:
        with gzip.GzipFile(filename="", mode="wb", fileobj=destination, mtime=ARCHIVE_MTIME) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for name, data in sorted(entries.items()):
                    info = tarfile.TarInfo("package/" + name)
                    info.size = len(data)
                    info.mode = 0o755 if name == "scripts/sanmu.mjs" else 0o644
                    info.mtime = ARCHIVE_MTIME
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    archive.addfile(info, io.BytesIO(data))


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
        text = bundle[f"skills/{skill['name']}/SKILL.md"].decode("utf-8")
        entries = skill_entries(root, files, skill, release)
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
    zcode = output / "zcode"
    zcode.mkdir()
    zcode_dir = zcode / "sanmu-paper-search"
    zcode_entries = {path: (root / source).read_bytes() for path, source in zcode_sources(files).items()}
    for rel, data in zcode_entries.items():
        dest = zcode_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    zcode_archive = zcode / f"sanmu-paper-search-zcode-plugin-{release['version']}.zip"
    write_zip(zcode_archive, zcode_entries)
    archives.append(zcode_archive)
    npm_directory = output / "npm"
    npm_directory.mkdir()
    npm_archive = npm_directory / f"{release['name']}-{release['version']}.tgz"
    write_npm_tgz(npm_archive, {path: bundle[path] for path in npm_files(root, files)})
    archives.append(npm_archive)
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
            if path.suffix == ".tgz":
                with tarfile.open(path, "r:gz") as archive:
                    entries = archive.getmembers()
                    size = sum(item.size for item in entries)
            else:
                with zipfile.ZipFile(path) as archive:
                    entries = archive.infolist()
                    size = sum(item.file_size for item in entries)
            print(f"{path}: {len(entries)} files, {size} uncompressed bytes, "
                  f"{path.stat().st_size} {path.suffix[1:].upper()} bytes")


if __name__ == "__main__":
    main()
