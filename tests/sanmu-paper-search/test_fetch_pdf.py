import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = (Path(__file__).resolve().parents[2] / 'skills' / 'sanmu-paper-search') / "scripts" / "fetch_pdf.py"
spec = importlib.util.spec_from_file_location("fetch_pdf", SCRIPT)
fetch_pdf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch_pdf)


class PdfTests(unittest.TestCase):
    def test_html_and_truncated_pdf_rejected(self):
        for payload in (b"<html>login required</html>", b"%PDF-1.4\ntruncated"):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                fetch_pdf.inspect_pdf(payload)
        self.assertTrue(fetch_pdf.inspect_pdf(b"  \n%PDF-1.4\n%%EOF\n" + b"x" * 10000)["trailer"])

    def test_redacts_credentials_and_does_not_invent_identity(self):
        url = fetch_pdf.public_url("https://publisher.org/a.pdf?token=SECRET&email=private%40mail.org&download=1#token")
        self.assertNotIn("SECRET", url)
        self.assertNotIn("private", url)
        self.assertIn("download=1", url)
        self.assertEqual(fetch_pdf.identity_check(Path("unused"))["status"], "not_checked")

    def test_existing_output_protected_before_request(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "user-work"
            target.mkdir()
            note = target / "note.txt"
            note.write_text("keep", encoding="utf-8")
            with patch.object(fetch_pdf, "urlopen") as network:
                with self.assertRaises(ValueError):
                    fetch_pdf.fetch("https://publisher.org/a.pdf", target)
                network.assert_not_called()
            self.assertEqual(note.read_text(encoding="utf-8"), "keep")

    def test_identity_exact_doi_title_and_reference_boundaries(self):
        check = fetch_pdf.assess_identity_text
        self.assertEqual(check('Paper\n10.1234/abcdef', '10.1234/abc')["status"], 'needs_review')
        self.assertEqual(check('Expected title\n10.1234/wrong', '10.1234/right', 'Expected title')["status"], 'needs_review')
        self.assertEqual(check('Other paper\nReferences\nExpected title 10.1234/right', '10.1234/right', 'Expected title')["status"], 'needs_review')
        self.assertEqual(check('Expected title\nhttps://doi.org/10.1234/right\nAbstract\nBody', '10.1234/right', 'Expected title')["status"], 'matched')
        self.assertEqual(check('Expected title\nAbstract\nBody text\nFooter DOI: 10.1234/right.', '10.1234/right', 'Expected title')["status"], 'matched')

    def test_identity_exposes_conflicting_doi_for_browser_claim(self):
        result = fetch_pdf.assess_identity_text('Other study\nDOI: 10.1234/wrong',
                                                '10.1234/right', 'Expected title')
        self.assertEqual(result['status'], 'needs_review')
        self.assertTrue(result.get('has_conflicting_doi'))
        correct = fetch_pdf.assess_identity_text('Expected title\nDOI: 10.1234/right',
                                                 '10.1234/right', 'Expected title')
        self.assertFalse(correct.get('has_conflicting_doi'))

    def test_signed_url_redaction_preserves_ports_and_ipv6(self):
        url = fetch_pdf.public_url("https://user:pass@[2001:db8::1]:8443/paper.pdf?api%2Dkey=SECRET&authorization=AUTH&download=1")
        self.assertIn("[2001:db8::1]:8443", url)
        self.assertNotIn("SECRET", url)
        self.assertNotIn("AUTH", url)
        self.assertNotIn("pass", url)
        self.assertIn("download=1", url)
        self.assertEqual(fetch_pdf.assess_identity_text("Any unrelated heading", title="!!!")["status"], "needs_review")

    def test_download_redirect_format_identity_and_fallback_selection(self):
        from email.message import Message
        class Response:
            def __init__(self, payload, url):
                self.payload, self.url = payload, url
                self.headers = Message()
                self.headers["Content-Type"] = "application/pdf"
            def read(self, size): return self.payload[:size]
            def __enter__(self): return self
            def __exit__(self, *args): pass
        with patch.object(fetch_pdf, "urlopen", return_value=Response(b"%PDF-1.4\n%%EOF", "http://example.org/file.pdf")), \
                self.assertRaisesRegex(ValueError, "insecure_redirect"):
            fetch_pdf._download_pdf_bytes("https://example.org/file.pdf")
        with tempfile.TemporaryDirectory() as tmp:
            payloads = [b"<html>login</html>", b"%PDF-1.4\nwrong\n%%EOF", b"%PDF-1.4\ncorrect\n%%EOF"]
            urls = ["https://example.org/landing", "https://example.org/wrong.pdf", "https://example.org/correct.pdf"]
            def opener(req, timeout):
                return Response(payloads[urls.index(req.full_url)], req.full_url)
            def identity(path, doi, title):
                return {"status": "matched" if b"correct" in path.read_bytes() else "needs_review", "method": "fixture"}
            with patch.object(fetch_pdf, "urlopen", side_effect=opener) as network, \
                    patch.object(fetch_pdf, "identity_check", side_effect=identity):
                record = fetch_pdf.fetch(urls[0], Path(tmp) / "run", doi="10.1234/right", fallback_urls=urls[1:])
                self.assertEqual(network.call_count, 3)
            self.assertEqual(record["identity"]["status"], "matched")
            self.assertEqual(record["source_url"], urls[-1])
            self.assertEqual((Path(tmp) / "run/paper.pdf").read_bytes(), payloads[-1])
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(fetch_pdf, "urlopen", return_value=Response(b"%PDF-1.4\n%%EOF", "https://example.org/file.pdf")), \
                patch.object(fetch_pdf, "identity_check", return_value={"status": "needs_review"}):
            record = fetch_pdf.fetch("https://example.org/file.pdf", Path(tmp) / "run", doi="10.1234/right")
            self.assertEqual(record["identity"]["status"], "needs_review")
            self.assertTrue((Path(tmp) / "run/paper.pdf").exists())

    def test_doi_resolution_uses_only_configured_unpaywall_contact(self):
        for env, expected in (({}, False), ({"UNPAYWALL_EMAIL": "reader@example.org"}, True)):
            with self.subTest(configured=expected), patch.object(fetch_pdf, "urlopen", side_effect=OSError("offline")) as network:
                fetch_pdf.resolve_doi_pdf_candidates("10.1234/example", env=env)
                requests = [call.args[0].full_url for call in network.call_args_list]
                unpaywall = [url for url in requests if "api.unpaywall.org/" in url]
                self.assertEqual(bool(unpaywall), expected)
                if expected:
                    self.assertIn("email=reader%40example.org", unpaywall[0])

    def test_non_https_and_embedded_password_rejected(self):
        for url in ("http://publisher.org/a.pdf", "https://user:pass@publisher.org/a.pdf"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                fetch_pdf.fetch(url, Path(tempfile.gettempdir()) / "unused-pdf-output")

    def test_403_and_proxy_error_fallback_to_direct_no_proxy_and_local_check(self):
        from email.message import Message
        import io
        import json
        from urllib.error import HTTPError, URLError

        class Response:
            def __init__(self, payload, url):
                self.payload, self.url = payload, url
                self.headers = Message()
                self.headers["Content-Type"] = "application/pdf"
            def read(self, size): return self.payload[:size]
            def __enter__(self): return self
            def __exit__(self, *args): pass

        class DirectOpener:
            def __init__(self, payload):
                self.payload = payload
                self.calls = 0
            def open(self, req, timeout=40):
                self.calls += 1
                return Response(self.payload, req.full_url)

        pdf_bytes = b"%PDF-1.7\n10.1002/adma.202313513 Single Atom Ni Photocatalysis\n%%EOF"
        with tempfile.TemporaryDirectory() as tmp:
            direct = DirectOpener(pdf_bytes)
            def proxy_blocked(req, timeout=40):
                raise HTTPError(req.full_url, 403, "Forbidden by Cloudflare proxy", {}, io.BytesIO(b"<html>403</html>"))
            with patch.object(fetch_pdf, "urlopen", side_effect=proxy_blocked), \
                    patch.object(fetch_pdf, "build_opener", return_value=direct), \
                    patch.object(fetch_pdf, "identity_check", return_value={"status": "matched", "doi_found": True, "title_found": True}):
                out_dir = Path(tmp) / "run-403"
                record = fetch_pdf.fetch("https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/adma.202313513", out_dir, doi="10.1002/adma.202313513")
                self.assertEqual(direct.calls, 1)
                self.assertTrue(record.get("direct_no_proxy_fallback"))
                acq_json = json.loads((out_dir / "acquisition.json").read_text(encoding="utf-8"))
                self.assertTrue(acq_json.get("direct_no_proxy_fallback"))
                local_res = fetch_pdf.check_local_pdf(out_dir / "paper.pdf", doi="10.1002/adma.202313513")
                self.assertTrue(local_res["ok"])
                self.assertTrue(local_res["format_checks"]["header"])


if __name__ == "__main__":
    unittest.main()
