import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import acquisition_store as store


PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def manifest(doi="10.1016/j.test", data=PDF):
    return {
        "schema": "sanmu-acquisition/v1",
        "paper": {"doi": doi},
        "main": {"file": "paper.pdf",
                 "sha256": hashlib.sha256(data).hexdigest()},
    }


class AcquisitionStoreTests(unittest.TestCase):
    def test_persist_then_reuse_same_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "KEY"
            first = store.persist(dest, PDF, manifest())
            self.assertEqual(first["status"], "file_obtained")
            again = store.inspect_existing(dest, "10.1016/j.test")
            self.assertEqual(again["status"], "already_obtained")
            self.assertEqual(again["sha256"], hashlib.sha256(PDF).hexdigest())
            same = store.persist(dest, PDF, manifest())
            self.assertEqual(same["status"], "already_obtained")

    def test_different_bytes_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "KEY"
            store.persist(dest, PDF, manifest())
            other = PDF + b"changed"
            with self.assertRaises(store.AcquisitionConflict):
                store.persist(dest, other, manifest(data=other))

    def test_different_doi_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "KEY"
            store.persist(dest, PDF, manifest())
            with self.assertRaises(store.AcquisitionConflict):
                store.inspect_existing(dest, "10.1002/other")

    def test_unmanaged_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "KEY"
            dest.mkdir()
            (dest / "paper.pdf").write_bytes(PDF)
            with self.assertRaises(store.AcquisitionConflict):
                store.inspect_existing(dest, "10.1016/j.test")

    def test_changed_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "KEY"
            store.persist(dest, PDF, manifest())
            (dest / "paper.pdf").write_bytes(PDF + b"tampered")
            with self.assertRaises(store.AcquisitionConflict):
                store.inspect_existing(dest, "10.1016/j.test")

    def test_ingest_status_and_success_semantics(self):
        self.assertEqual(store.ingest_status({"status": "success"}), "ingested")
        self.assertEqual(store.ingest_status({"status": "partial"}), "ingested_partial")
        self.assertEqual(store.ingest_status({"status": "planned"}), "file_obtained")
        self.assertTrue(store.is_success({"status": "file_obtained"}))
        self.assertTrue(store.is_success({"status": "skipped", "reason": "already_obtained"}))
        self.assertTrue(store.is_success({"status": "skipped", "reason": "main_pdf_exists"}))
        self.assertFalse(store.is_success({"status": "skipped",
                                           "reason": "api_key_not_configured"}))
        self.assertFalse(store.is_success({"status": "ingested_partial"}))
        self.assertFalse(store.is_success({"status": "needs_manual"}))
        self.assertFalse(store.is_success({
            "status": "file_obtained",
            "ingest": {"status": "pending_identity_review"}}))
        self.assertFalse(store.is_success({
            "status": "file_obtained", "ingest_error": "upload failed"}))

    def test_json_writer_is_atomic_and_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            store.write_json(path, {"ok": True})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"ok": True})
            self.assertEqual(list(Path(tmp).glob(".result.json-*")), [])


if __name__ == "__main__":
    unittest.main()
