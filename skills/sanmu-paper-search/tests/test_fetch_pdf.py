import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_pdf.py"
spec = importlib.util.spec_from_file_location("fetch_pdf", SCRIPT)
fetch_pdf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch_pdf)


class PdfTests(unittest.TestCase):
    def test_html_and_truncated_pdf_rejected(self):
        for payload in (b"<html>login required</html>", b"%PDF-1.4\ntruncated"):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                fetch_pdf.inspect_pdf(payload)
        self.assertTrue(fetch_pdf.inspect_pdf(b"%PDF-1.4\n%%EOF")["trailer"])

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

    def test_non_https_and_embedded_password_rejected(self):
        for url in ("http://publisher.org/a.pdf", "https://user:pass@publisher.org/a.pdf"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                fetch_pdf.fetch(url, Path(tempfile.gettempdir()) / "unused-pdf-output")


if __name__ == "__main__":
    unittest.main()
