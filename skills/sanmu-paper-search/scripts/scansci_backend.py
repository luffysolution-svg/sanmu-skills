"""Optional ScanSci OA resolver; never invokes the package's download CLI.

The audited try_openalex_oa function is extracted as AST, hash checked, and run
with stdlib shims in a child interpreter. No scansci_pdf module is imported:
its __init__ changes proxy settings, and default/batch downloaders mix sources.
The download shim RETURNS A CANDIDATE, not bytes. acquire.py uses fetch_pdf.
Audit: https://github.com/Rimagination/scansci-pdf at commit
6d3b8e1cfe698577a8838dc818f3fc03caa34e32, version 1.18.2:
sources/openalex.py::try_openalex_oa and europepmc.py::extract_europepmc_pdf_candidates
(also identical in installed 1.5.0). These THIRD-PARTY functions execute from
the installed distribution after hash checks; their source is not bundled here.
Upstream pure Python license: Apache-2.0, copyright 2024-2026 scansci-pdf
contributors; https://github.com/Rimagination/scansci-pdf/blob/main/LICENSE.
Upstream proprietary _core binaries are neither imported nor extracted.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from urllib.request import Request, urlopen

PIN = "1.18.2"
PACKAGE = "scansci-pdf==" + PIN
FUNCTION_HASH = "67cba1405cf2b60a0f835cfc86ec18bf3249b6f92c9e6d51034d5ef5620b4b03"
EUROPEPMC_HASH = "2d64c47826378d9fecafe3b5f78a67861734cbe2caabfa1f75e839072ac7fb80"
SUPPORTED_SOURCES = ["OpenAlexOA", "EuropePMC_explicit_OA"]
AUDIT_COMMIT = "6d3b8e1cfe698577a8838dc818f3fc03caa34e32"
MAX_JSON = 2 * 1024 * 1024


def cache_root():
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    return Path(base) / "sanmu-paper-search" / "scansci" if base else Path.home() / ".cache/sanmu-paper-search/scansci"


def _run(command, **kwargs):
    return subprocess.run([str(v) for v in command], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=kwargs.pop("timeout", 45), **kwargs)


def audited_function(path, name="try_openalex_oa", expected=FUNCTION_HASH):
    """Fail closed on ANY change to the single allowed function (not package version)."""
    text = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(text)
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
    if len(functions) != 1:
        raise ValueError("unsupported_policy")
    source = ast.get_source_segment(text, functions[0])
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != expected:
        raise ValueError("unsupported_policy")
    return source


def _probe(python):
    # Distribution metadata is read without importing scansci_pdf or its CLI.
    code = ("import importlib.metadata as m,json; d=m.distribution('scansci-pdf'); "
            "print(json.dumps({'version':d.version,'source':str(d.locate_file('scansci_pdf/sources/openalex.py'))}))")
    try:
        r = _run([python, "-I", "-c", code])
        if r.returncode:
            return None
        data = json.loads(r.stdout)
        audited_function(data["source"])
        audited_function(Path(data["source"]).with_name("europepmc.py"),
                         "extract_europepmc_pdf_candidates", EUROPEPMC_HASH)
        return {"supported_sources": SUPPORTED_SOURCES, "institutional_execution": "browser_handoff_only", "status": "ready", "python": str(python), **data,
                "execution": "audited_oa_function_stdlib_shims", "policy": "oa_only",
                "audit_commit": AUDIT_COMMIT, "function_sha256": FUNCTION_HASH,
                "dependency_status": "stdlib_only; package_CLI_dependencies_not_used"}
    except (OSError, ValueError, KeyError, SyntaxError, subprocess.SubprocessError):
        return None


def _python_in(root):
    root = Path(root)
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def prepare(cache=None, install=True):
    """Prefer audited existing tools; at most one isolated install per cache/pin.

    No package CLI, browser, Tor, proxy mutation, login or library access occurs.
    Setup diagnostics contain statuses, never raw pip output or config values.
    """
    root = Path(cache) if cache else cache_root()
    candidates = [Path(sys.executable), _python_in(root / "tools/scansci-pdf"), _python_in(root / "venv")]
    exe = shutil.which("scansci-pdf")
    if exe:
        directory = Path(exe).parent
        candidates.extend([directory / ("python.exe" if os.name == "nt" else "python"),
                           directory.parent / "python.exe"])
    uv = shutil.which("uv")
    if uv:
        try:
            result = _run([uv, "tool", "dir"])
            if result.returncode == 0 and result.stdout.strip():
                candidates.append(_python_in(Path(result.stdout.strip()) / "scansci-pdf"))
        except (OSError, subprocess.SubprocessError):
            pass
    seen = set()
    for python in candidates:
        key = str(python.resolve())
        if key in seen or not python.is_file():
            continue
        seen.add(key)
        ready = _probe(python)
        if ready:
            return {**ready, "setup": "reused"}
    if not install:
        return {"status": "dependency_missing", "package": PACKAGE, "policy": "oa_only",
                "reason": "no_existing_tool_with_audited_source", "can_prepare": True}
    if sys.version_info < (3, 11):
        return {"status": "dependency_missing", "reason": "python_3_11_required", "package": PACKAGE}
    root.mkdir(parents=True, exist_ok=True)
    marker = root / ("setup-" + PIN + ".json")
    if marker.exists():
        return {"status": "setup_failed_previous", "package": PACKAGE,
                "reason": "automatic_install_already_attempted; inspect_or_remove_cache_marker_to_retry"}
    lock = root / "setup.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        return {"status": "setup_in_progress", "package": PACKAGE}
    try:
        # Durable attempt marker BEFORE any subprocess: interruption cannot loop installs.
        with marker.open("x", encoding="utf-8") as f:
            json.dump({"package": PACKAGE, "status": "attempted"}, f)
        env = os.environ.copy()
        env.update({"UV_TOOL_DIR": str(root / "tools"), "UV_TOOL_BIN_DIR": str(root / "bin"),
                    "UV_CACHE_DIR": str(root / "uv-cache"), "SCANSCI_PDF_DATA_DIR": str(root / "data"),
                    "PIP_CACHE_DIR": str(root / "pip-cache"), "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
        if uv:
            result = _run([uv, "tool", "install", "--python", sys.executable, PACKAGE], env=env, timeout=360)
            python = _python_in(root / "tools/scansci-pdf")
            method = "uv_tool"
        else:
            python = _python_in(root / "venv")
            result = _run([sys.executable, "-m", "venv", root / "venv"], env=env, timeout=90)
            if result.returncode == 0:
                result = _run([python, "-m", "pip", "install", "--no-input", PACKAGE], env=env, timeout=360)
            method = "venv_pip"
        if result.returncode:
            return {"status": "setup_failed", "reason": "package_install_failed", "package": PACKAGE,
                    "method": method, "exit_code": result.returncode}
        ready = _probe(python)
        if ready:
            return {**ready, "setup": "installed", "method": method}
        return {"status": "unsupported_policy", "reason": "installed_source_not_audited", "package": PACKAGE}
    except FileExistsError:
        return {"status": "setup_failed_previous", "package": PACKAGE}
    except subprocess.TimeoutExpired:
        return {"status": "setup_failed", "reason": "setup_timeout", "package": PACKAGE}
    except OSError:
        return {"status": "setup_failed", "reason": "setup_filesystem_or_executable_error", "package": PACKAGE}
    finally:
        lock.rmdir()


def browser_handoff(doi=None, authorized_profile=None, use_tool_profile=False):
    """Read only allowlisted config paths; never read/export cookies or launch login.

    A profile on disk is NOT proof of a live session. Explicit user authorization
    permits handoff, with session validity still marked unverified.
    """
    data = Path(os.environ.get("SCANSCI_PDF_DATA_DIR", str(Path.home() / ".scansci-pdf")))
    candidate = Path(authorized_profile).expanduser() if authorized_profile else None
    origin = "user" if candidate else "none"
    config = {}
    try:
        config_path = data / "config.json"
        if config_path.stat().st_size <= MAX_JSON:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            config = raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        pass
    if candidate is None:
        value = config.get("chrome_profile_dir")
        if isinstance(value, str) and value:
            candidate, origin = Path(value).expanduser(), "scansci_config"
    profile_present = bool(candidate and candidate.is_dir())
    state_present = False
    try:
        cache = config.get("cache_dir") or str(data / "cache")
        state_present = (Path(cache) / "browser_state.json").is_file()
    except (TypeError, OSError):
        pass
    authorized = bool(profile_present and (authorized_profile or use_tool_profile))
    return {"status": "needs_browser_download" if authorized else "needs_browser_session",
            "publisher_url": "https://doi.org/" + urllib.parse.quote(doi, safe="/") if doi else None,
            "profile_present": profile_present, "profile_origin": origin,
            "tool_saved_state_present": state_present, "session_validity": "not_checked",
            "authorization": "user_asserted" if authorized else "required",
            "institutional_execution": "browser_handoff_only",
            "action": "Hand off publisher URL to the assistant's browser tool with the existing authorized profile; "
                      "obtain the download and use acquire.py --import-file FILE. "
                      "Reuse ScanSci's own discovered profile with --use-tool-profile; user handles interactive login if needed."}


def _helper(source, requested_doi):
    """No import of the upstream package; hash-checked function and narrow shims only."""
    captured = {}
    source_status = []

    def fetch_json(url, config):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or parts.hostname != "api.openalex.org" or parts.username or parts.password:
            raise ValueError("unsupported_policy")
        with urlopen(Request(url, headers={"User-Agent": "sanmu-paper-search/1.0"}), timeout=25) as response:
            if urllib.parse.urlsplit(response.url).hostname != "api.openalex.org":
                raise ValueError("unsupported_policy")
            data = response.read(MAX_JSON + 1)
        if len(data) > MAX_JSON:
            raise ValueError("metadata_too_large")
        payload = json.loads(data)
        captured["payload"] = payload
        return payload

    def download_pdf(url, output_path, config, provider):
        payload = captured.get("payload", {})
        if payload.get("open_access", {}).get("is_oa") is not True:
            raise ValueError("unsupported_policy")
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            return None
        locations = [payload.get("best_oa_location"), payload.get("primary_location")]
        locations += payload.get("locations") or []
        loc = next((v for v in locations if isinstance(v, dict) and v.get("is_oa") is True
                    and v.get("pdf_url") == url), None)
        if loc is None and payload.get("open_access", {}).get("oa_url") != url:
            return None
        loc = loc or {}
        return {"url": url, "provider": "scansci:OpenAlexOA", "is_oa": True,
                "license": loc.get("license"), "version": loc.get("version"),
                "evidence": "openalex_explicit_oa; audited_source_function"}

    # urllib.parse is already loaded; provide precisely the namespace the audited function expects.
    import urllib
    namespace = {"urllib": urllib, "Path": Path, "Any": object, "fetch_json": fetch_json,
                 "polite_delay": lambda config: None, "download_pdf": download_pdf,
                 "is_plausible_pdf_url": lambda u: bool(isinstance(u, str) and
                     (".pdf" in urllib.parse.urlsplit(u).path.lower() or "/pdf" in urllib.parse.urlsplit(u).path.lower()))}
    exec(compile(audited_function(source), "<audited-scansci-oa>", "exec"), namespace)
    candidate = namespace["try_openalex_oa"](requested_doi, Path("unused"), {})
    source_status.append({"source": "OpenAlexOA", "status": "ok" if candidate else
                          ("not_found" if "payload" in captured else "lookup_failed")})
    candidates = [candidate] if candidate else []
    called = ["OpenAlexOA"]
    if not candidates:
        called.append("EuropePMC_explicit_OA")
        europe_source = audited_function(Path(source).with_name("europepmc.py"),
                                        "extract_europepmc_pdf_candidates", EUROPEPMC_HASH)
        def plausible(u):
            return isinstance(u, str) and urllib.parse.urlsplit(u).scheme == "https" and (
                ".pdf" in urllib.parse.urlsplit(u).path.lower() or "/pdf" in urllib.parse.urlsplit(u).path.lower())
        scope = {"Any": object, "is_plausible_pdf_url": plausible,
                 "dedupe": lambda items: list(dict.fromkeys(items))}
        exec(compile(europe_source, "<audited-scansci-europepmc>", "exec"), scope)
        try:
            query = urllib.parse.quote('DOI:' + requested_doi)
            endpoint = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=" + query + "&format=json&resultType=core&pageSize=3"
            with urlopen(Request(endpoint, headers={"User-Agent": "sanmu-paper-search/1.0"}), timeout=15) as response:
                if urllib.parse.urlsplit(response.url).hostname != "www.ebi.ac.uk":
                    raise ValueError("unsupported_policy")
                data = response.read(MAX_JSON + 1)
            if len(data) > MAX_JSON:
                raise ValueError("metadata_too_large")
            payload = json.loads(data)
            for result in payload.get("resultList", {}).get("result", [])[:3]:
                if not isinstance(result, dict) or result.get("isOpenAccess") != "Y":
                    continue
                if str(result.get("doi", "")).casefold() != requested_doi.casefold():
                    continue
                fulltext = result.get("fullTextUrlList", {}).get("fullTextUrl", [])
                fulltext = fulltext if isinstance(fulltext, list) else [fulltext]
                eligible = [v for v in fulltext if isinstance(v, dict) and v.get("availabilityCode") == "OA"]
                filtered = {"resultList": {"result": [{"fullTextUrlList": {"fullTextUrl": eligible}}]}}
                for url in scope["extract_europepmc_pdf_candidates"](filtered)[:3]:
                    candidates.append({"url": url, "provider": "scansci:EuropePMC", "is_oa": True,
                                       "license": result.get("license"), "version": None,
                                       "evidence": "EuropePMC isOpenAccess=Y AND availabilityCode=OA; DOI exact match"})
            source_status.append({"source": "EuropePMC_explicit_OA", "status": "ok" if candidates else "not_found"})
        except (OSError, ValueError, TimeoutError):
            source_status.append({"source": "EuropePMC_explicit_OA", "status": "lookup_failed"})
    status = "ok" if candidates else ("backend_failed" if any(s["status"] == "lookup_failed" for s in source_status) else "not_found")
    return {"status": status, "policy": "oa_only", "candidates": candidates[:3],
            "supported_sources": SUPPORTED_SOURCES, "sources_called": called, "source_status": source_status,
            "institutional_execution": "browser_handoff_only", "retry": False}


def resolve(doi, runtime=None, cache=None, install=True):
    runtime = runtime or prepare(cache, install)
    if runtime.get("status") != "ready":
        return {**runtime, "candidates": []}
    try:
        audited_function(runtime["source"])
        result = _run([runtime["python"], "-I", "-S", Path(__file__).resolve(),
                       "--_helper", runtime["source"], "--doi", doi], timeout=50)
        if result.returncode:
            return {"status": "backend_failed", "reason": "isolated_helper_failed", "candidates": []}
        value = json.loads(result.stdout)
        if (value.get("policy") != "oa_only" or not value.get("sources_called")
                or any(s not in SUPPORTED_SOURCES for s in value["sources_called"])):
            raise ValueError("unsupported_policy")
        value["tool_version"] = runtime.get("version")
        return value
    except subprocess.TimeoutExpired:
        return {"status": "backend_failed", "reason": "oa_lookup_timeout", "candidates": []}
    except (ValueError, KeyError, SyntaxError):
        return {"status": "unsupported_policy", "reason": "source_or_helper_contract_changed", "candidates": []}
    except (OSError, subprocess.SubprocessError):
        return {"status": "backend_failed", "reason": "helper_unavailable", "candidates": []}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true", help="Reuse or install pinned isolated tool without downloads")
    parser.add_argument("--no-install", action="store_true")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--doi", help="Resolve a DOI using audited OpenAlex OA and explicitly OA EuropePMC sources")
    parser.add_argument("--_helper", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._helper:
        try:
            result = _helper(args._helper, args.doi)
        except (ValueError, SyntaxError, OSError):
            result = {"status": "unsupported_policy", "policy": "oa_only", "candidates": [], "sources_called": ["OpenAlexOA"]}
    elif args.prepare_only:
        result = prepare(args.cache_dir, not args.no_install)
        # Internal interpreter/source locations are not part of public diagnostics.
        result = {k: v for k, v in result.items() if k not in ("python", "source")}
    elif args.doi:
        result = resolve(args.doi, cache=args.cache_dir, install=not args.no_install)
    else:
        parser.error("use --prepare-only or --doi")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in ("ready", "ok", "not_found") else 2


if __name__ == "__main__":
    raise SystemExit(main())
