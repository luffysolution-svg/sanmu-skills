import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ingest import ingest, load_manifest, metadata_for, verify_parent
from zotero_local import ZoteroError


class FakeZotero:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def get(self, key, library="user"):
        return {"data": {"itemType": "journalArticle", "title": "A paper", "DOI": "10.1234/test"}}

    def upsert(self, metadata, **kwargs):
        self.calls.append(("upsert", metadata, kwargs))
        return {"status": "unchanged", "key": "ABCDEFGH"}

    def attach(self, key, path, **kwargs):
        self.calls.append(("attach", key, path, kwargs))
        if self.fail:
            raise ZoteroError("upload_failed", "Upload failed")
        return {"status": "skipped", "key": "ATTACH01"}


class IngestTests(unittest.TestCase):
    def fixture(self, directory, matched=True, si=False):
        directory = Path(directory)
        data = b"%PDF-1.4\nexample\n%%EOF"
        (directory / "paper.pdf").write_bytes(data)
        entry = {"id": "main", "kind": "main", "status": "file_obtained", "file": "paper.pdf",
                 "sha256": hashlib.sha256(data).hexdigest(), "identity": {"status": "matched" if matched else "needs_review"}}
        manifest = {"schema": "sanmu-acquisition/v1", "paper": {"title": "A paper", "doi": "10.1234/test"}, "main": entry, "supplementary": []}
        if si:
            manifest["supplementary"].append({**entry, "id": "si-1", "kind": "supplementary"})
        path = directory / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path, manifest

    def test_default_preview_and_existing_parent_never_update_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.fixture(folder)
            client = FakeZotero()
            result = ingest(path, client=client, parent_key="ABCDEFGH")
            self.assertEqual(result["status"], "planned")
            self.assertEqual([call[0] for call in client.calls], ["attach"])
            self.assertFalse(client.calls[0][3]["apply"])

    def test_no_mutation_until_all_files_are_validated(self):
        with tempfile.TemporaryDirectory() as folder:
            path, manifest = self.fixture(folder, si=True)
            manifest["supplementary"][0]["sha256"] = "invalid"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            client = FakeZotero()
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                ingest(path, client=client, apply=True, si_confirmed=True)
            self.assertEqual(client.calls, [])

    def test_identity_and_si_require_review(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.fixture(folder, matched=False, si=True)
            with self.assertRaisesRegex(ValueError, "identity"):
                load_manifest(path)
            with self.assertRaisesRegex(ValueError, "SI relationship"):
                load_manifest(path, identity_confirmed=True)
            self.assertEqual(len(load_manifest(path, True, True)[1]), 2)

    def test_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path, manifest = self.fixture(folder)
            manifest["main"]["file"] = "../other.pdf"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "inside"):
                load_manifest(path)

    def test_parent_identity_conflict(self):
        with self.assertRaisesRegex(ValueError, "DOI conflicts"):
            verify_parent({"data": {"DOI": "10.9999/other"}}, {"doi": "10.1234/test"})
        with self.assertRaisesRegex(ValueError, "bibliographic"):
            verify_parent({"data": {"itemType": "attachment"}}, {})

    def test_parent_same_title_conflicting_arxiv_is_rejected(self):
        parent = {"data": {"itemType": "preprint", "title": "A paper",
                            "url": "https://arxiv.org/abs/2405.04967"}}
        paper = {"title": "A paper", "identifiers": {"arxiv": ["2110.06197v3"]}}
        with self.assertRaisesRegex(ValueError, "arXiv identifier conflicts"):
            verify_parent(parent, paper)
        paper["identifiers"]["arxiv"] = ["2405.04967v2"]
        verify_parent(parent, paper)

    def test_cli_passes_explicit_scan_budget(self):
        from unittest.mock import patch
        import ingest as module
        with patch.object(module, "ZoteroLocal", return_value=FakeZotero()) as factory:
            with patch.object(module, "ingest", return_value={"status": "planned"}):
                with patch("builtins.print"):
                    code = module.main(["manifest.json", "--max-items", "5000", "--max-pages", "50"])
        self.assertEqual(code, 0)
        self.assertEqual(factory.call_args.kwargs["max_items"], 5000)
        self.assertEqual(factory.call_args.kwargs["max_pages"], 50)

    def test_ingest_is_repeatable_and_partial_is_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.fixture(folder)
            result = ingest(path, client=FakeZotero(), apply=True)
            self.assertEqual(result["attachments"][0]["status"], "skipped")
            result = ingest(path, client=FakeZotero(fail=True), apply=True)
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["parent"]["key"], "ABCDEFGH")

    def test_unselected_si_does_not_make_ingest_partial(self):
        with tempfile.TemporaryDirectory() as folder:
            path, manifest = self.fixture(folder)
            manifest["supplementary"].append({"id": "si-unselected", "kind": "supplementary",
                                               "status": "discovered", "selected": False})
            path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(ingest(path, client=FakeZotero(), apply=True)["status"], "success")
            manifest["supplementary"][0]["selected"] = True
            path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(ingest(path, client=FakeZotero(), apply=True)["status"], "partial")

    def test_metadata_uses_stable_identity(self):
        data = metadata_for({"title": "Preprint", "identifiers": {"arxiv": ["2405.04967"]}})
        self.assertEqual(data["itemType"], "preprint")
        self.assertEqual(data["archiveID"], "2405.04967")


if __name__ == "__main__":
    unittest.main()
