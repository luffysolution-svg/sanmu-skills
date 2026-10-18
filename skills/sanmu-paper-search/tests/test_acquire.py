import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import acquire as a

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def paper():
    return {"title": "A paper", "authors": [{"name": "A Researcher"}], "year": 2025,
            "abstract": "Evidence", "doi": "10.1234/a", "identifiers": {"doi": ["10.1234/a"]},
            "oa_locations": [{"url": "https://publisher.test/a.pdf", "provider": "test",
                              "is_oa": True, "license": None, "version": "acceptedVersion"}]}


class Response:
    def __init__(self, data=PDF, url="https://publisher.test/a.pdf"):
        self.data, self.url = data, url
        from email.message import Message
        self.headers = Message()
        self.headers["Content-Type"] = "application/pdf"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, n=-1):
        return self.data[:n] if n >= 0 else self.data


class AcquireTests(unittest.TestCase):
    def test_select_preserves_complete_normalized_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "papers.json"
            p.write_text(json.dumps([paper(), {**paper(), "identifiers": {"doi": ["10.1234/b"]}}]))
            chosen = a.select_paper(p, index=1)
            self.assertEqual(chosen["authors"], paper()["authors"])
            self.assertEqual(chosen["abstract"], "Evidence")
            with self.assertRaises(a.InputError):
                a.select_paper(p)
            with self.assertRaises(a.InputError):
                a.select_paper(p, index=1, doi="10.1234/b")
            self.assertEqual(a.select_paper(doi="https://doi.org/10.1234/a")["doi"], "10.1234/a")

    def test_metadata_conflicting_doi_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "meta.json"
            p.write_text(json.dumps(paper()))
            with self.assertRaises(a.InputError):
                a.select_paper(doi="10.1234/b", metadata=p)

    def test_oa_true_only_pdf_candidates_bounded_deduplicated(self):
        p = paper()
        p["oa_locations"] = [
            {"url": "https://p.test/unknown.pdf", "is_oa": None},
            {"url": "https://p.test/closed.pdf", "is_oa": False},
            {"url": "http://p.test/insecure.pdf", "is_oa": True},
            {"url": "https://p.test/landing", "is_oa": True},
            *p["oa_locations"], *p["oa_locations"],
            {"url": "https://p.test/b.pdf", "is_oa": True}]
        self.assertEqual(len(a.oa_candidates(p, 1)), 1)
        self.assertEqual(len(a.oa_candidates(p)), 2)

    def test_real_fetch_code_manifest_and_retry_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d, patch.object(a.fetch_pdf, "urlopen", return_value=Response()) as net, patch.object(a.scansci_backend, "resolve") as backend:
            root = Path(d) / "run"
            result = a.acquire(paper(), root)
            item = result["main"]
            self.assertEqual(item["status"], "file_obtained")
            self.assertEqual(item["sha256"], hashlib.sha256(PDF).hexdigest())
            self.assertEqual(item["identity"]["status"], "needs_review")
            self.assertEqual(item["source"]["license_status"], "unknown")
            self.assertEqual(item["source"]["version"], "acceptedVersion")
            self.assertFalse(item["read"])
            self.assertEqual(json.loads((root / "manifest.json").read_text(encoding="utf-8"))["main"], item)
            a.acquire(paper(), root)
            self.assertEqual(net.call_count, 1)
            backend.assert_not_called()
            (root / item["file"]).write_bytes(b"changed")
            with self.assertRaises(a.InputError):
                a.acquire(paper(), root)

    def test_failure_survives_and_backend_status_handoff(self):
        with tempfile.TemporaryDirectory() as d, patch.object(a.fetch_pdf, "urlopen", side_effect=URLError("secret")), patch.object(a.scansci_backend, "resolve", return_value={"status": "unsupported_policy", "candidates": []}):
            result = a.acquire(paper(), d)
            self.assertEqual(result["main"]["status"], "failed")
            self.assertEqual(result["backend"]["status"], "unsupported_policy")
            self.assertEqual(result["handoff"]["status"], "needs_browser_session")
            self.assertEqual(result["main"]["attempts"][0]["reason"], "network_error")
            self.assertNotIn("secret", (Path(d) / "manifest.json").read_text(encoding="utf-8"))

    def test_backend_candidate_policy_is_rechecked(self):
        p = paper()
        p["oa_locations"] = []
        with tempfile.TemporaryDirectory() as d, patch.object(a.scansci_backend, "resolve", return_value={"status": "ok", "candidates": [{"url": "https://p.test/a.pdf", "is_oa": False}]}), patch.object(a.fetch_pdf, "fetch") as fetch:
            result = a.acquire(p, d)
            fetch.assert_not_called()
            self.assertEqual(result["backend"]["status"], "unsupported_policy")

    def test_import_authorized_pdf_unknown_oa_and_version(self):
        with tempfile.TemporaryDirectory() as d, patch.object(a.fetch_pdf, "fetch") as fetch:
            src = Path(d) / "browser.pdf"
            src.write_bytes(PDF)
            result = a.acquire(paper(), Path(d) / "out", import_file=src,
                               import_source={"url": "https://p.test/a.pdf?token=SECRET", "version": "publisherVersion"})
            fetch.assert_not_called()
            item = result["main"]
            self.assertEqual(item["source"]["provider"], "authorized_browser_import")
            self.assertIsNone(item["source"]["is_oa"])
            self.assertEqual(item["source"]["version"], "publisherVersion")
            self.assertEqual(src.read_bytes(), PDF)
            self.assertNotIn("SECRET", (Path(d) / "out/manifest.json").read_text(encoding="utf-8"))

    def test_bad_import_and_nonempty_output_protected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "login.pdf"
            src.write_text("<html>login</html>")
            with self.assertRaises(a.InputError):
                a.acquire(paper(), root, backend="none")
            p = paper()
            p["oa_locations"] = []
            result = a.acquire(p, root / "run", backend="none", import_file=src)
            self.assertEqual(result["main"]["status"], "failed")
            self.assertIsNone(result["main"]["file"])
            with self.assertRaises(a.InputError):
                a.acquire({**p, "title": "Different"}, root / "run", backend="none")

    def test_si_discovery_actual_links_and_no_ads_or_guesses(self):
        html = '<a href="/ad.pdf">Advertisement</a><a href="/si.zip">Supporting Information</a><a href="/si.zip">Supplementary</a><a href="javascript:x">Supplementary PDF</a><a href="/landing">Supporting Information</a><a href="/table.csv">Supplementary table</a>'
        found = a.discover_si(html, "https://p.test/article")
        self.assertEqual([url for _, url in found], ["https://p.test/si.zip", "https://p.test/table.csv"])
        self.assertEqual(found[0][0]["relation"]["target"], "main")
        self.assertEqual(found[0][0]["source"]["license_status"], "unknown")
        self.assertFalse(found[0][0]["selected"])

    def test_si_selection_and_separate_manifest(self):
        html = '<a href="/table.csv">Supplementary table</a>'
        sid = a.discover_si(html, "https://p.test/a")[0][0]["id"]
        p = paper()
        p["oa_locations"] = []
        with tempfile.TemporaryDirectory() as d, patch.object(a, "urlopen", return_value=Response(b"a,b\n1,2\n", "https://p.test/table.csv")) as net:
            src = Path(d) / "publisher.html"
            src.write_text(html)
            root = Path(d) / "out"
            result = a.acquire(p, root, backend="none", publisher_html=src, publisher_url="https://p.test/a")
            net.assert_not_called()
            self.assertEqual(result["supplementary"][0]["status"], "discovered")
            result = a.acquire(p, root, backend="none", publisher_html=src, publisher_url="https://p.test/a", select_si=[sid])
            si = result["supplementary"][0]
            self.assertEqual(si["status"], "file_obtained")
            self.assertEqual(si["attachment_type"], "csv")
            self.assertEqual(si["identity"]["status"], "needs_review")
            self.assertEqual(result["main"]["status"], "not_obtained")
            self.assertIsNone(si["source"]["is_oa"])
            a.acquire(p, root, backend="none", publisher_html=src, publisher_url="https://p.test/a", select_si=[sid])
            self.assertEqual(net.call_count, 1)

    def test_import_zip_not_extracted(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "si.zip"
            src.write_bytes(b"PK\x03\x04opaque-archive")
            p = paper()
            p["oa_locations"] = []
            result = a.acquire(p, Path(d) / "run", backend="none", import_file=src,
                               import_kind="supplementary", import_type="zip", import_id="si-user")
            si = result["supplementary"][0]
            self.assertFalse(si["format_checks"]["archive_extracted"])
            self.assertEqual(si["status"], "file_obtained")
            self.assertEqual(si["kind"], "supplementary")

    def test_signatures_and_html_login_rejected(self):
        for typ in ("zip", "png", "jpeg", "pdf", "csv", "txt"):
            with self.subTest(typ=typ), self.assertRaises(ValueError):
                a.inspect_attachment(b"<html>login</html>", typ)

    def test_atomic_manifest_failure_preserves_old(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            a.write_manifest(root, {"old": True})
            with patch.object(a.os, "replace", side_effect=OSError("disk")):
                with self.assertRaises(OSError):
                    a.write_manifest(root, {"new": True})
            self.assertEqual(json.loads((root / "manifest.json").read_text()), {"old": True})
            self.assertEqual(len(list(root.iterdir())), 1)

    def test_selection_and_main_import_parameter_boundaries(self):
        with tempfile.TemporaryDirectory() as d:
            for kwargs in ({"select_si": ["si-missing"]}, {"import_type": "csv"},
                           {"import_kind": "supplementary", "import_id": "../escape"}):
                with self.subTest(kwargs=kwargs), self.assertRaises(a.InputError):
                    a.acquire(paper(), Path(d) / "out", **kwargs)

    def test_si_import_without_id_is_repeatable_and_conflicts_protected(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "si.csv"
            src.write_bytes(b"x,y\n1,2\n")
            p = paper()
            p["oa_locations"] = []
            root = Path(d) / "out"
            for _ in range(2):
                result = a.acquire(p, root, backend="none", import_file=src,
                                   import_kind="supplementary", import_type="csv")
            self.assertEqual(len(result["supplementary"]), 1)
            si = result["supplementary"][0]
            self.assertEqual(len(si["attempts"]), 1)
            src.write_bytes(b"changed")
            with self.assertRaises(a.InputError):
                a.acquire(p, root, backend="none", import_file=src, import_kind="supplementary",
                          import_type="csv", import_id=si["id"])

    def test_si_failure_or_unknown_selection_makes_main_success_partial(self):
        html = '<a href="/si.csv">Supporting information table</a>'
        sid = a.discover_si(html, "https://p.test/a")[0][0]["id"]
        with tempfile.TemporaryDirectory() as d:
            src, hp = Path(d) / "paper.pdf", Path(d) / "publisher.html"
            src.write_bytes(PDF)
            hp.write_text(html)
            with patch.object(a, "urlopen", side_effect=URLError("failed")):
                result = a.acquire(paper(), Path(d) / "one", import_file=src,
                                   publisher_html=hp, publisher_url="https://p.test/a", select_si=[sid])
            self.assertEqual(result["main"]["status"], "file_obtained")
            self.assertEqual(result["status"], "partial")
            result = a.acquire(paper(), Path(d) / "two", import_file=src,
                               publisher_html=hp, publisher_url="https://p.test/a", select_si=["si-unknown"])
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["discovery"][0]["unmatched_selected_ids"], ["si-unknown"])
            with patch.object(a, "urlopen", side_effect=URLError("failed")):
                result = a.acquire(paper(), Path(d) / "three", import_file=src, publisher_url="https://p.test/a")
            self.assertEqual(result["status"], "partial")

    def test_cli_partial_exitcode_and_help(self):
        with tempfile.TemporaryDirectory() as d:
            src, hp = Path(d) / "paper.pdf", Path(d) / "publisher.html"
            src.write_bytes(PDF)
            hp.write_text('<a href="/si.csv">Supplementary table</a>')
            with patch("builtins.print"):
                code = a.main(["--doi", "10.1234/a", "--out", str(Path(d) / "out"),
                               "--import-file", str(src), "--publisher-html", str(hp),
                               "--publisher-url", "https://p.test/a", "--select-si", "si-unknown"])
            self.assertEqual(code, 2)

    def test_existing_lock_prevents_acquisition(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".acquire.lock").mkdir()
            with self.assertRaises(a.InputError):
                a.acquire(paper(), d)


if __name__ == "__main__":
    unittest.main()
