import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import scansci_backend as s


class BackendTests(unittest.TestCase):
    def test_unknown_source_rejected_without_import(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "source.py"
            p.write_text('raise RuntimeError("MUST NOT EXECUTE")\ndef try_openalex_oa(doi, output_path, config):\n    return None\n')
            with self.assertRaisesRegex(ValueError, "unsupported_policy"):
                s.audited_function(p)
            with patch.object(s, "_run") as run:
                result = s.resolve("10.1234/a", runtime={"status": "ready", "source": str(p), "python": sys.executable})
                self.assertEqual(result["status"], "unsupported_policy")
                run.assert_not_called()

    def test_missing_dependency_no_install_is_explicit(self):
        with tempfile.TemporaryDirectory() as d, patch.object(s, "_probe", return_value=None), patch.object(s.shutil, "which", return_value=None), patch.object(s, "_run") as run:
            result = s.prepare(d, install=False)
            self.assertEqual(result["status"], "dependency_missing")
            self.assertFalse(list(Path(d).iterdir()))
            run.assert_not_called()

    def test_prefer_existing_tool_does_not_install(self):
        with tempfile.TemporaryDirectory() as d, patch.object(s, "_probe", return_value={"status": "ready", "version": "1.5.0"}), patch.object(s.shutil, "which", return_value=None), patch.object(s, "_run") as run:
            result = s.prepare(d)
            self.assertEqual(result["setup"], "reused")
            run.assert_not_called()
            self.assertFalse(list(Path(d).iterdir()))

    def test_uv_isolated_pinned_install_failure_not_retried(self):
        calls = []
        def run(command, **kwargs):
            calls.append((list(map(str, command)), kwargs))
            return SimpleNamespace(returncode=1, stdout="", stderr="private secret")
        with tempfile.TemporaryDirectory() as d, patch.object(s, "_probe", return_value=None), patch.object(s.shutil, "which", side_effect=lambda name: "/tool/uv" if name == "uv" else None), patch.object(s, "_run", side_effect=run):
            first = s.prepare(d)
            second = s.prepare(d)
            installs = [v for v in calls if v[0][1:3] == ["tool", "install"]]
            self.assertEqual(len(installs), 1)
            command, kwargs = installs[0]
            self.assertIn("scansci-pdf==1.18.2", command)
            self.assertEqual(kwargs["env"]["UV_TOOL_DIR"], str(Path(d) / "tools"))
            self.assertEqual(kwargs["env"]["SCANSCI_PDF_DATA_DIR"], str(Path(d) / "data"))
            self.assertEqual(first["status"], "setup_failed")
            self.assertEqual(second["status"], "setup_failed_previous")
            self.assertNotIn("secret", json.dumps(first))

    def test_venv_fallback_pinned_never_system_pip(self):
        calls = []
        def run(command, **kwargs):
            command = list(map(str, command))
            calls.append(command)
            return SimpleNamespace(returncode=1 if "pip" in command else 0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as d, patch.object(s, "_probe", return_value=None), patch.object(s.shutil, "which", return_value=None), patch.object(s, "_run", side_effect=run):
            result = s.prepare(d)
            self.assertEqual(result["status"], "setup_failed")
            self.assertEqual(calls[0][1:3], ["-m", "venv"])
            self.assertEqual(calls[1][0], str(s._python_in(Path(d) / "venv")))
            self.assertIn(s.PACKAGE, calls[1])
            self.assertNotEqual(calls[1][0], sys.executable)

    def test_setup_timeout_marker_prevents_loop(self):
        with tempfile.TemporaryDirectory() as d, patch.object(s, "_probe", return_value=None), patch.object(s.shutil, "which", return_value=None), patch.object(s, "_run", side_effect=subprocess.TimeoutExpired("venv", 90)):
            self.assertEqual(s.prepare(d)["reason"], "setup_timeout")
            self.assertEqual(s.prepare(d)["status"], "setup_failed_previous")
            self.assertFalse((Path(d) / "setup.lock").exists())

    def test_install_lock_is_bounded(self):
        with tempfile.TemporaryDirectory() as d, patch.object(s, "_probe", return_value=None), patch.object(s.shutil, "which", return_value=None), patch.object(s, "_run") as run:
            (Path(d) / "setup.lock").mkdir()
            self.assertEqual(s.prepare(d)["status"], "setup_in_progress")
            run.assert_not_called()

    def test_helper_policy_checks_and_isolation_arguments(self):
        runtime = {"status": "ready", "source": "audited.py", "python": sys.executable, "version": "1.18.2"}
        for payload, expected in [
            ({"status": "ok", "policy": "legal_only", "sources_called": ["SciHub"]}, "unsupported_policy"),
            ({"status": "ok", "policy": "oa_only", "sources_called": ["OpenAlexOA"], "candidates": []}, "ok")]:
            with patch.object(s, "audited_function", return_value=""), patch.object(s, "_run", return_value=SimpleNamespace(returncode=0, stdout=json.dumps(payload))) as run:
                result = s.resolve("10.1234/a", runtime=runtime)
                self.assertEqual(result["status"], expected)
                command = list(map(str, run.call_args.args[0]))
                self.assertIn("-I", command)
                self.assertIn("-S", command)
                self.assertNotIn("download", command)
                self.assertNotIn("batch", command)
                self.assertNotIn("legal_only", command)

    def test_backend_dependency_failure_not_disguised_as_not_found(self):
        with patch.object(s, "prepare", return_value={"status": "setup_failed", "reason": "package_install_failed"}):
            result = s.resolve("10.1234/a")
            self.assertEqual(result["status"], "setup_failed")
            self.assertEqual(result["candidates"], [])

    def test_no_session_and_profile_discovery_do_not_read_cookies(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"SCANSCI_PDF_DATA_DIR": d}):
            self.assertEqual(s.browser_handoff("10.1234/a")["status"], "needs_browser_session")
            profile = Path(d) / "profile"
            profile.mkdir()
            cache = Path(d) / "cache"
            cache.mkdir()
            (cache / "browser_state.json").write_text("SECRET COOKIE DO NOT READ")
            (Path(d) / "config.json").write_text(json.dumps({"chrome_profile_dir": str(profile), "password": "SECRET PASSWORD"}))
            result = s.browser_handoff("10.1234/a")
            self.assertEqual(result["status"], "needs_browser_session")
            self.assertTrue(result["profile_present"])
            self.assertTrue(result["tool_saved_state_present"])
            self.assertNotIn("SECRET", json.dumps(result))
            self.assertEqual(s.browser_handoff("10.1234/a", use_tool_profile=True)["status"], "needs_browser_download")
            explicit = s.browser_handoff("10.1234/a", authorized_profile=profile)
            self.assertEqual(explicit["status"], "needs_browser_download")
            self.assertEqual(explicit["session_validity"], "not_checked")
            self.assertEqual(explicit["institutional_execution"], "browser_handoff_only")

    def test_shim_never_imports_package_and_returns_candidate(self):
        function = '''def try_openalex_oa(doi, output_path, config):
    payload = fetch_json("https://api.openalex.org/works/doi:" + doi, config)
    return download_pdf(payload["open_access"]["oa_url"], output_path, config, "OpenAlexOA")'''
        class Response:
            url = "https://api.openalex.org/works/doi:10.1234/a"
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, n):
                return json.dumps({"open_access": {"is_oa": True, "oa_url": "https://p.test/a.pdf"}, "primary_location": {"pdf_url": "https://p.test/a.pdf", "is_oa": True, "license": "cc-by", "version": "publishedVersion"}}).encode()
        before = set(sys.modules)
        with patch.object(s, "audited_function", return_value=function), patch.object(s, "urlopen", return_value=Response()) as net:
            result = s._helper("source.py", "10.1234/a")
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["candidates"][0]["version"], "publishedVersion")
            self.assertEqual(net.call_count, 1)
            self.assertEqual(result["sources_called"], ["OpenAlexOA"])
            self.assertFalse(any(k.startswith("scansci_pdf") for k in set(sys.modules) - before))

    def test_europepmc_filters_explicit_oa_and_doi(self):
        extraction = '''def extract_europepmc_pdf_candidates(payload):
    return [v["url"] for r in payload["resultList"]["result"] for v in r["fullTextUrlList"]["fullTextUrl"]]'''
        payload = {"resultList": {"result": [
            {"doi": "10.1234/a", "isOpenAccess": "N", "fullTextUrlList": {"fullTextUrl": [{"url": "https://p.test/closed.pdf", "availabilityCode": "OA"}]}},
            {"doi": "10.1234/wrong", "isOpenAccess": "Y", "fullTextUrlList": {"fullTextUrl": [{"url": "https://p.test/wrong.pdf", "availabilityCode": "OA"}]}},
            {"doi": "10.1234/a", "isOpenAccess": "Y", "fullTextUrlList": {"fullTextUrl": [{"url": "https://p.test/ok.pdf", "availabilityCode": "OA"}, {"url": "https://p.test/pay.pdf", "availabilityCode": "F"}]}}]}}
        class Response:
            url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, n): return json.dumps(payload).encode()
        with patch.object(s, "audited_function", side_effect=["def try_openalex_oa(*args): return None", extraction]), patch.object(s, "urlopen", return_value=Response()):
            result = s._helper("source.py", "10.1234/a")
            self.assertEqual([v["url"] for v in result["candidates"]], ["https://p.test/ok.pdf"])
            self.assertEqual(result["source_status"][0]["status"], "lookup_failed")
            self.assertEqual(result["candidates"][0]["is_oa"], True)


if __name__ == "__main__":
    unittest.main()
