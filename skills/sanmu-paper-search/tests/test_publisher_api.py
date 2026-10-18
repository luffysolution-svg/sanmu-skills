import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import acquisition_store
import publisher_api as pa


PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


class FakeResponse:
    def __init__(self, status=200, content=PDF, content_type="application/pdf", headers=None):
        self.status_code = status
        self._content = content
        self.headers = {"content-type": content_type}
        if headers:
            self.headers.update(headers)

    def iter_content(self, chunk_size):
        yield self._content

    def close(self):
        pass


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class PublisherApiTests(unittest.TestCase):
    def test_provider_mapping(self):
        self.assertEqual(pa.provider_for_doi("10.1016/j.test"), "elsevier")
        self.assertEqual(pa.provider_for_doi("10.1002/adma.1"), "wiley")
        self.assertEqual(pa.provider_for_doi("10.1111/test"), "wiley")
        self.assertEqual(pa.provider_for_doi("10.1038/nature1"), "unsupported")

    def test_key_is_env_only_and_not_configured(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(pa.configured("elsevier"))
            result = pa.fetch_api_pdf("10.1016/j.test")
            self.assertEqual(result["status"], "not_configured")
            self.assertNotIn("data", result)

    def test_elsevier_pdf_contract(self):
        session = FakeSession(FakeResponse())
        with patch.dict(os.environ, {"ELSEVIER_API_KEY": "dummy-elsevier"}, clear=True):
            result = pa.fetch_api_pdf("10.1016/j.test", session=session)
        self.assertEqual(result["status"], "file_obtained")
        self.assertEqual(result["provider"], "elsevier")
        self.assertEqual(result["data"], PDF)
        self.assertEqual(session.calls[0][1]["headers"]["X-ELS-APIKey"], "dummy-elsevier")
        self.assertEqual(session.calls[0][1]["params"]["httpAccept"], "application/pdf")
        self.assertIn("api.elsevier.com", session.calls[0][0])
        self.assertNotIn("dummy-elsevier", json.dumps({k: v for k, v in result.items() if k != "data"}))

    def test_wiley_pdf_contract(self):
        session = FakeSession(FakeResponse())
        with patch.dict(os.environ, {"WILEY_TDM_KEY": "dummy-wiley"}, clear=True):
            result = pa.fetch_api_pdf("10.1002/adma.1", session=session)
        self.assertEqual(result["status"], "file_obtained")
        self.assertEqual(result["provider"], "wiley")
        self.assertEqual(session.calls[0][1]["headers"]["Wiley-TDM-Client-Token"], "dummy-wiley")
        self.assertIn("api.wiley.com", session.calls[0][0])
        self.assertNotIn("dummy-wiley", json.dumps({k: v for k, v in result.items() if k != "data"}))

    def test_non_pdf_is_rejected(self):
        session = FakeSession(FakeResponse(content=b"<html>login</html>",
                                           content_type="text/html"))
        with patch.dict(os.environ, {"ELSEVIER_API_KEY": "dummy"}, clear=True):
            result = pa.fetch_api_pdf("10.1016/j.test", session=session)
        self.assertEqual(result["status"], "not_pdf")
        self.assertNotIn("data", result)

    def test_access_denied_and_rate_limit(self):
        with patch.dict(os.environ, {"WILEY_TDM_KEY": "dummy"}, clear=True):
            denied = pa.fetch_api_pdf("10.1002/adma.1", session=FakeSession(FakeResponse(status=403)))
            limited = pa.fetch_api_pdf("10.1002/adma.1", session=FakeSession(
                FakeResponse(status=429, headers={"retry-after": "60"})))
        self.assertEqual(denied["status"], "access_denied")
        self.assertEqual(limited["status"], "rate_limited")
        self.assertEqual(limited["retry_after"], "60")

    def test_unsupported_doi(self):
        result = pa.fetch_api_pdf("10.1038/nature1")
        self.assertEqual(result["status"], "unsupported")


class PublisherApiSafetyTests(unittest.TestCase):
    def test_doi_dry_run_does_not_fetch_or_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"ELSEVIER_API_KEY": "dummy"}, clear=True):
                with patch.object(pa, "fetch_api_pdf",
                                  side_effect=AssertionError("network must not run")):
                    code = pa.main(["fetch", "--doi", "10.1016/j.test",
                                    "--out", tmp, "--dry-run"])
            self.assertEqual(code, 0)
            self.assertEqual(list(Path(tmp).rglob("paper.pdf")), [])

    def test_existing_acquisition_is_skipped_without_replacement(self):
        item = {"data": {"title": "A paper", "DOI": "10.1016/j.test"}}
        args = argparse.Namespace(apply=False, identity_confirmed=False, library="user")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(pa.fetch_pdf, "identity_check",
                              return_value={"status": "matched"}):
                first = pa._save_record({"title": "A paper"}, PDF, {"header": True},
                                        item, "KEY", Path(tmp), object(), args,
                                        "elsevier")
                second = pa._save_record({"title": "A paper"}, PDF, {"header": True},
                                         item, "KEY", Path(tmp), object(), args,
                                         "elsevier")
            self.assertEqual(first["status"], "file_obtained")
            self.assertEqual(second["status"], "skipped")
            self.assertEqual(second["reason"], "already_obtained")
            self.assertEqual((Path(tmp) / "KEY" / "paper.pdf").read_bytes(), PDF)

    def test_save_record_passes_library_and_keeps_partial_ingest_truthful(self):
        item = {"data": {"title": "A paper", "DOI": "10.1016/j.test"}}
        args = argparse.Namespace(apply=True, identity_confirmed=False, library="g123")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(pa.fetch_pdf, "identity_check",
                              return_value={"status": "matched"}):
                with patch.object(pa, "ingest_manifest",
                                  return_value={"status": "partial"}) as ingest:
                    record = pa._save_record({"title": "A paper"}, PDF,
                                             {"header": True}, item, "KEY",
                                             Path(tmp), object(), args, "elsevier")
        self.assertEqual(record["status"], "ingested_partial")
        self.assertEqual(ingest.call_args.kwargs["library"], "g123")
        self.assertEqual(ingest.call_args.kwargs["apply"], True)

    def test_batch_success_requires_truthful_skips(self):
        self.assertTrue(pa._record_is_success({"status": "file_obtained"}))
        self.assertTrue(pa._record_is_success({"status": "skipped",
                                               "reason": "already_obtained"}))
        self.assertFalse(pa._record_is_success({"status": "skipped",
                                                "reason": "api_key_not_configured"}))
        self.assertFalse(pa._record_is_success({"status": "ingested_partial"}))
        self.assertFalse(pa._record_is_success({"status": "needs_manual"}))

    def test_wiley_policy_enforces_official_window(self):
        policy = pa.policy_for("wiley", argparse.Namespace(delay=2.0, daily_limit=0))
        self.assertEqual(policy["min_interval"], 10.0)
        self.assertEqual(policy["window_limit"], 60)
        self.assertEqual(policy["window_seconds"], 600.0)
        policy = pa.policy_for("wiley", argparse.Namespace(api_delay=None,
                                                            daily_limit=5))
        self.assertEqual(policy["min_interval"], 10.0)
        self.assertEqual(policy["daily_limit"], 5)

    def test_api_rate_limit_creates_persistent_cooldown(self):
        item = {"data": {"title": "A paper", "DOI": "10.1016/j.test"}}
        args = argparse.Namespace(apply=False, identity_confirmed=False,
                                  library="user", delay=0, daily_limit=0)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = pa.rate_ledger.RateLedger(Path(tmp) / "state.json")
            with patch.dict(os.environ, {"ELSEVIER_API_KEY": "dummy"}, clear=True):
                with patch.object(pa, "fetch_api_pdf",
                                  return_value={"status": "rate_limited",
                                                "retry_after": "60"}):
                    record = pa._fetch_one(None, item, "KEY", Path(tmp), args,
                                           ledger=ledger)
            self.assertEqual(record["status"], "failed")
            check = ledger.check("api:elsevier",
                                 pa.policy_for("elsevier", args))
            self.assertEqual(check["reason"], "cooldown")
            self.assertGreater(check["wait_seconds"], 50)

    def test_existing_file_bytes_are_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "KEY"
            first = acquisition_store.persist(
                dest, PDF, {"schema": "sanmu-acquisition/v1",
                            "paper": {"doi": "10.1016/j.test"},
                            "main": {"file": "paper.pdf",
                                     "sha256": pa.hashlib.sha256(PDF).hexdigest()}})
            self.assertEqual(first["status"], "file_obtained")
            with self.assertRaises(acquisition_store.AcquisitionConflict):
                acquisition_store.persist(
                    dest, PDF + b"different",
                    {"schema": "sanmu-acquisition/v1",
                     "paper": {"doi": "10.1016/j.test"},
                     "main": {"file": "paper.pdf",
                              "sha256": pa.hashlib.sha256(PDF + b"different").hexdigest()}})


if __name__ == "__main__":
    unittest.main()
