"""Offline contract tests: no installed Zotero, credentials or dependencies needed."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "zotero_local.py"
spec = importlib.util.spec_from_file_location("sanmu_test_zotero_local", SCRIPT)
z = importlib.util.module_from_spec(spec)
spec.loader.exec_module(z)
PARENT, ATTACH, OTHER = "ABCDEFGH", "JKLMNPQR", "STUVWXYZ"
COLL1, COLL2, COLL3 = "ABCD2345", "EFGH2345", "JKLM2345"
TOKEN = "k" * 32


def response(data=None, status=200, headers=None):
    return status, headers or {}, data if isinstance(data, bytes) else json.dumps(data).encode()


def item(key=PARENT, version=1, **data):
    return {"key": key, "version": version,
            "data": {"itemType": "journalArticle", "title": "Original", **data}}


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = z.ZoteroLocal(cache_path=self.root / "cache" / "session.json")
        self.source = self.root / "support.csv"
        self.source.write_bytes(b"column,value\nA,1\n")
        self.md5 = hashlib.md5(self.source.read_bytes()).hexdigest()

    def mock_reads(self):
        self.client.get = Mock(return_value=item())
        self.client.children = Mock(return_value={"items": [], "truncated": False})

    def upload_setup(self, failure=None):
        self.mock_reads()
        self.client._create = Mock(return_value=ATTACH)
        grant = {"url": "http://127.0.0.1:23119/api/local/uploads/abc123", "uploadKey": "upload123"}
        self.client._write = Mock(side_effect=[response(grant), response(status=204)])
        self.client._request = Mock(return_value=response(status=failure or 201))
        self.client.get = Mock(side_effect=lambda key, library="user": item(key, **({"itemType": "attachment", "md5": self.md5} if key == ATTACH else {})))
        self.client.file = Mock(return_value={"status": "available", "path": str(self.source)})

    def assert_code(self, code, call, *args, **kwargs):
        with self.assertRaises(z.ZoteroError) as caught:
            call(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_loopback_validation(self):
        for url in ("https://127.0.0.1:23119", "http://example.org", "http://127.0.0.1.evil", "http://user:secret@localhost", "http://localhost/x", "http://localhost/?key=x"):
            self.assert_code("unsafe_url", z.ZoteroLocal, url)
        for url in ("http://localhost:23119", "http://[::1]:23119", "http://127.0.0.2:23119"):
            z.ZoteroLocal(url)
        self.assert_code("unsafe_url", self.client._request, "POST", "http://example.org/api/local/uploads/x")
        self.assert_code("unsafe_url", self.client._request, "GET", "//example.org/x")

    def test_doctor_is_read_only_and_redacted(self):
        self.client._request = Mock(return_value=response({}))
        result = self.client.doctor()
        self.assertFalse(result["authorized"])
        self.assertEqual(self.client._request.call_args.args, ("GET", "/api/"))
        self.assertNotIn("key", result)

    def test_authorization_exact_contract_and_denial(self):
        self.client.doctor = Mock()
        self.client.server_id = "server-1"
        self.client._request = Mock(return_value=response({"key": TOKEN, "remember": False}))
        result = self.client.authorize(apply=True)
        args = self.client._request.call_args
        self.assertEqual(args.args[:2], ("POST", "/api/local/authorize"))
        self.assertEqual(json.loads(args.args[2]), {"appName": z.APP_NAME})
        self.assertEqual(args.args[3]["Zotero-Server-ID"], "server-1")
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertFalse(self.client.cache_path.exists())
        self.client._request = Mock(return_value=response(b"denied", status=403))
        self.assert_code("write_denied", self.client.authorize, apply=True)
        self.assertEqual(self.client._request.call_count, 1)

    def test_authorization_rate_limited(self):
        self.client.doctor = Mock()
        self.client.server_id = "server-1"
        self.client._request = Mock(return_value=response(status=429, headers={"retry-after": "60"}))
        error = self.assert_code("rate_limited", self.client.authorize, apply=True)
        self.assertEqual(error.details, {"retry_after": "60"})

    def test_remembered_cache_and_identity_invalidation(self):
        self.client.server_id, self.client._session_key, self.client._remember = "server-1", TOKEN, True
        self.client._save_cache()
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.client.cache_path.stat().st_mode), 0o600)
        other = z.ZoteroLocal(cache_path=self.client.cache_path)
        other.server_id = "server-1"
        other._load_cache()
        self.assertEqual(other._session_key, TOKEN)
        changed = z.ZoteroLocal(cache_path=self.client.cache_path)
        changed.server_id = "server-2"
        changed._load_cache()
        self.assertIsNone(changed._session_key)
        self.assertFalse(self.client.cache_path.exists())

    def test_single_use_key_consumed_and_revoked_key_not_retried(self):
        self.client.doctor = Mock()
        self.client.server_id, self.client._session_key = "server-1", TOKEN
        self.client._request = Mock(return_value=response(status=204))
        self.client._write("PATCH", "/api/users/0/items/" + PARENT, {})
        self.assertIsNone(self.client._session_key)
        self.assertEqual(self.client._request.call_args.args[3]["Zotero-API-Key"], TOKEN)
        self.client._session_key, self.client._remember = TOKEN, True
        self.client._request = Mock(return_value=response(status=401))
        self.client._write("PATCH", "/api/users/0/items/" + PARENT, {})
        self.assertIsNone(self.client._session_key)
        self.assertEqual(self.client._request.call_count, 1)

    def test_read_only_previews(self):
        self.mock_reads()
        self.client._write = Mock(side_effect=AssertionError("No writes permitted"))
        self.assertEqual(self.client.update(PARENT, {"title": "New"})["status"], "planned")
        self.assertEqual(self.client.attach(PARENT, self.source)["status"], "planned")
        self.assertEqual(self.client.note(PARENT, "<p>Note</p>")["status"], "planned")
        self.assertEqual(self.client.authorize()["status"], "planned")
        self.client.search = Mock(return_value={"items": [], "truncated": False})
        self.assertEqual(self.client.upsert({"title": "New", "itemType": "journalArticle", "DOI": "10.1234/test"})["status"], "planned")
        self.client._write.assert_not_called()

    def test_pagination_bounded_and_truncated(self):
        self.client = z.ZoteroLocal(max_items=5, max_pages=2)
        self.client._request = Mock(side_effect=[response([item()] * 5, headers={"total-results": "9"})])
        result = self.client.search(limit=5)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["next_start"], 5)
        self.assertEqual(result["total"], 9)
        self.assertEqual(self.client._request.call_count, 1)
        self.assert_code("invalid_input", self.client.search, limit=6)

    def test_pagination_multiple_pages(self):
        self.client._request = Mock(side_effect=[response([item()] * 100, headers={"total-results": "101"}),
                                                       response([item(OTHER)], headers={"total-results": "101"})])
        result = self.client.search(limit=101)
        self.assertFalse(result["truncated"])
        second = self.client._request.call_args.args[1]
        self.assertEqual(parse_qs(urlsplit(second).query)["start"], ["100"])
        self.assertEqual(result["count"], 101)

    def test_unknown_total_full_page_is_conservatively_truncated(self):
        self.client._request = Mock(return_value=response([item()]))
        self.assertTrue(self.client.search(limit=1)["truncated"])
        self.client._request = Mock(return_value=response([]))
        self.assertFalse(self.client.search(limit=1)["truncated"])

    def test_patch_412_reapplies_sets_preserving_concurrent_changes(self):
        first = item(tags=[{"tag": "remove"}], collections=[COLL1])
        latest = item(version=2, tags=[{"tag": "remove"}, {"tag": "concurrent", "type": 1}], collections=[COLL1, COLL3])
        self.client.get = Mock(side_effect=[first, latest, item(version=3)])
        self.client._write = Mock(side_effect=[response(status=412), response(status=204)])
        result = self.client.update(PARENT, add_tags=["new"], remove_tags=["remove"],
                                    add_collections=[COLL2], remove_collections=[COLL1], apply=True)
        self.assertEqual(result["status"], "updated")
        call = self.client._write.call_args
        self.assertEqual(call.args[2]["tags"], [{"tag": "concurrent", "type": 1}, {"tag": "new"}])
        self.assertEqual(call.args[2]["collections"], [COLL3, COLL2])
        self.assertEqual(call.kwargs["headers"]["If-Unmodified-Since-Version"], "2")

    def test_field_conflict_refuses_overwrite(self):
        self.client.get = Mock(side_effect=[item(), item(version=2, title="Someone else's title")])
        self.client._write = Mock(return_value=response(status=412))
        error = self.assert_code("field_conflict", self.client.update, PARENT, {"title": "Mine"}, apply=True)
        self.assertEqual(error.details["fields"], ["title"])
        self.assertEqual(self.client._write.call_count, 1)

    def test_412_retry_budget(self):
        self.client.get = Mock(return_value=item())
        self.client._write = Mock(return_value=response(status=412))
        self.assert_code("version_conflict", self.client.update, PARENT, {"title": "Mine"}, apply=True)
        self.assertEqual(self.client._write.call_count, 3)

    def test_reserved_fields_refused(self):
        self.assert_code("invalid_input", self.client.update, PARENT, {"filename": "rename.pdf"})

    def test_upsert_exact_duplicate_and_default_no_changes(self):
        exact = item(DOI="https://doi.org/10.1234/ABC")
        self.client.search = Mock(return_value={"items": [item(OTHER, DOI="10.1234/abcd"), exact], "truncated": False})
        self.client.update = Mock(side_effect=AssertionError("No metadata overwrites"))
        result = self.client.upsert({"title": "New"}, "doi:10.1234/abc", apply=True)
        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(result["key"], PARENT)
        self.client.update.assert_not_called()

    def test_upsert_ambiguity_and_truncation_block_creation(self):
        self.client.search = Mock(return_value={"items": [item(DOI="10.1234/a"), item(OTHER, DOI="10.1234/a")], "truncated": False})
        self.assert_code("ambiguous_identifier", self.client.upsert, {}, "10.1234/a", apply=True)
        self.client.search = Mock(return_value={"items": [], "truncated": True, "count": 100, "next_start": 100})
        self.assert_code("incomplete_dedup", self.client.upsert, {}, "10.1234/a", apply=True)

    def test_arxiv_version_exact_dedup(self):
        self.client.search = Mock(return_value={"items": [item(extra="arXiv: 2401.12345v2")], "truncated": False})
        result = self.client.upsert({}, "https://arxiv.org/abs/2401.12345v3")
        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(z.normalize_identifier("arXiv:hep-th/9901001v2"), ("arxiv", "hep-th/9901001"))

    def test_upsert_creation_persists_identifier(self):
        self.client.search = Mock(return_value={"items": [], "truncated": False})
        self.client._create = Mock(return_value=PARENT)
        self.client.get = Mock(return_value=item())
        result = self.client.upsert({"title": "New", "itemType": "preprint"}, "2401.12345", apply=True)
        self.assertEqual(result["status"], "created")
        self.assertEqual(self.client._create.call_args.args[0]["extra"], "arXiv: 2401.12345")

    def test_file_exact_api_url_and_missing(self):
        self.client._request = Mock(return_value=response(self.source.as_uri().encode()))
        result = self.client.file(ATTACH)
        self.assertEqual(Path(result["path"]), self.source)
        self.assertEqual(result["status"], "available")
        self.assertTrue(self.client._request.call_args.args[1].endswith("/file/view/url"))
        self.client._request = Mock(return_value=response((self.root / "missing.pdf").as_uri().encode()))
        self.assertEqual(self.client.file(ATTACH)["status"], "missing")
        self.client._request = Mock(return_value=response(status=400))
        self.assertEqual(self.client.file(ATTACH)["status"], "not_file")
        for unsafe in (b"https://evil/x", b"file://remote/share/x", b"file:relative.pdf", b"file:///tmp/a?x=y", b"file:////remote/share"):
            self.client._request = Mock(return_value=response(unsafe))
            self.assert_code("unsafe_file_url", self.client.file, ATTACH)

    def test_same_attachment_hash_skips(self):
        self.mock_reads()
        self.client.children.return_value = {"items": [item(ATTACH, itemType="attachment", md5=self.md5)], "truncated": False}
        self.client.file = Mock(return_value={"status": "available", "path": str(self.source)})
        self.client._create = Mock(side_effect=AssertionError("No duplicate creation"))
        result = self.client.attach(PARENT, self.source, apply=True)
        self.assertEqual(result["status"], "skipped")

    def test_upload_contract_md5_actual_mtime_and_no_credentials(self):
        self.upload_setup()
        result = self.client.attach(PARENT, self.source, apply=True)
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["md5"], self.md5)
        first = self.client._write.call_args_list[0]
        form = parse_qs(first.args[2].decode())
        self.assertEqual(form["md5"], [self.md5])
        self.assertEqual(form["mtime"], [str(self.source.stat().st_mtime_ns // 1000000)])
        self.assertEqual(form["contentType"], ["text/csv"])
        upload = self.client._request.call_args
        self.assertEqual(upload.args[0], "POST")
        self.assertEqual(upload.args[2], self.source.read_bytes())
        self.assertEqual(upload.args[3], {"Content-Type": "text/csv"})
        self.assertEqual(parse_qs(self.client._write.call_args.args[2].decode()), {"upload": ["upload123"]})

    def test_upload_failure_partial_no_registration(self):
        self.upload_setup(failure=500)
        error = self.assert_code("http_error", self.client.attach, PARENT, self.source, apply=True)
        self.assertEqual(error.partial["attachment_key"], ATTACH)
        self.assertEqual(error.partial["stage"], "upload")
        self.assertEqual(self.client._write.call_count, 1)
        self.assertNotIn(TOKEN, json.dumps(error.to_dict()))

    def test_registration_failure_partial(self):
        self.upload_setup()
        self.client._write.side_effect = [response({"url": "http://127.0.0.1:23119/api/local/uploads/abc", "uploadKey": "x"}), response(status=500)]
        error = self.assert_code("http_error", self.client.attach, PARENT, self.source, apply=True)
        self.assertEqual(error.partial["stage"], "register")

    def test_upload_remote_grant_rejected_before_network(self):
        self.upload_setup()
        self.client._request = z.ZoteroLocal._request.__get__(self.client)
        self.client._write.side_effect = [response({"url": "http://example.org/api/local/uploads/abc", "uploadKey": "x"})]
        error = self.assert_code("unsafe_url", self.client.attach, PARENT, self.source, apply=True)
        self.assertEqual(error.partial["attachment_key"], ATTACH)

    def test_empty_attachment_auto_resume_and_ambiguity(self):
        self.upload_setup()
        empty = item(ATTACH, itemType="attachment", linkMode="imported_file", filename=self.source.name, title=self.source.name)
        self.client.children.return_value = {"items": [empty], "truncated": False}
        self.client.file = Mock(side_effect=[{"status": "missing"}, {"status": "available", "path": str(self.source)}])
        result = self.client.attach(PARENT, self.source, apply=True)
        self.assertEqual(result["status"], "repaired")
        self.client._create.assert_not_called()
        self.client.children.return_value = {"items": [empty, {**empty, "key": OTHER}], "truncated": False}
        self.client.file = Mock(return_value={"status": "missing"})
        self.assert_code("ambiguous_attachment", self.client.attach, PARENT, self.source, apply=True)

    def test_invalid_attachment_parent_and_hash_disagreement(self):
        self.mock_reads()
        self.client.get.return_value = item(itemType="attachment")
        self.assert_code("invalid_parent", self.client.attach, PARENT, self.source, apply=True)
        self.client.get.return_value = item()
        self.client.children.return_value = {"items": [item(ATTACH, itemType="attachment", md5=self.md5)], "truncated": False}
        other_file = self.root / "changed.csv"
        other_file.write_bytes(b"external edit")
        self.client.file = Mock(return_value={"status": "available", "path": str(other_file)})
        self.assert_code("attachment_conflict", self.client.attach, PARENT, self.source, apply=True)

    def test_upload_readback_missing_is_partial(self):
        self.upload_setup()
        self.client.file = Mock(return_value={"status": "missing"})
        error = self.assert_code("file_missing", self.client.attach, PARENT, self.source, apply=True)
        self.assertEqual(error.partial["stage"], "verify")
        self.assertEqual(error.partial["attachment_key"], ATTACH)

    def test_create_failure_and_readback_partial(self):
        self.client._write = Mock(return_value=response({"failed": {"0": {"code": 403, "message": TOKEN}}}))
        error = self.assert_code("write_failed", self.client._create, {"title": "New"})
        self.assertNotIn(TOKEN, json.dumps(error.to_dict()))
        self.client.get = Mock(side_effect=[item(), z.ZoteroError("connection_failed", "Unavailable")])
        self.client._write = Mock(return_value=response(status=204))
        error = self.assert_code("connection_failed", self.client.update, PARENT, {"title": "New"}, apply=True)
        self.assertEqual(error.partial, {"key": PARENT, "stage": "readback", "updated": True})

    def test_collections_notes_libraries_export(self):
        self.client.collections = Mock(return_value={"items": [], "truncated": False})
        self.assertEqual(self.client.create_collection("New")["status"], "planned")
        self.client._create = Mock(return_value=ATTACH)
        self.assertEqual(self.client.create_collection("New", apply=True)["key"], ATTACH)
        self.client.get = Mock(return_value=item())
        self.assertEqual(self.client.note(PARENT, "<p>Note</p>", apply=True)["key"], ATTACH)
        self.client._request = Mock(return_value=response([{"id": 123, "data": {"name": "Group"}}]))
        result = self.client.libraries()
        self.assertEqual([i["id"] for i in result["items"]], ["user", "g123"])
        self.client._request = Mock(return_value=response(b"@article{test}"))
        self.assertEqual(self.client.export(PARENT)["entries"], ["@article{test}"])

    def test_cli_structured_error_and_apply_dispatch(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(z.main(["get", "invalid"]), 2)
        self.assertFalse(json.loads(output.getvalue())["ok"])
        output = io.StringIO()
        with patch.object(z.ZoteroLocal, "update", return_value={"status": "planned"}) as update:
            with contextlib.redirect_stdout(output):
                self.assertEqual(z.main(["update", PARENT, "--data", '{"title":"New"}']), 0)
            self.assertFalse(update.call_args.kwargs["apply"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(z.main(["not-a-command"]), 2)


class HTTPTransportTests(unittest.TestCase):
    """Actual loopback HTTP mock proves urllib redirects and headers, not just mocks."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.calls = []
        calls = self.calls

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                calls.append((self.path, dict(self.headers)))
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://example.org/leak")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Zotero-Server-ID", "server-new")
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = z.ZoteroLocal("http://127.0.0.1:" + str(self.server.server_port),
                                    cache_path=Path(self.temp.name) / "cache.json")
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_api_v3_no_redirect_and_no_read_key(self):
        self.client.doctor()
        self.assertEqual(self.calls[0][1]["Zotero-Api-Version"], "3")
        self.assertNotIn("Zotero-Api-Key", self.calls[0][1])
        with self.assertRaises(z.ZoteroError) as error:
            self.client._request("GET", "/redirect", headers={"Zotero-API-Key": TOKEN})
        self.assertEqual(error.exception.code, "redirect_blocked")
        self.assertEqual(len(self.calls), 2)

    def test_server_restart_invalidates_memory_and_cache(self):
        self.client.server_id, self.client._session_key, self.client._remember = "old-server", TOKEN, True
        self.client.cache_path.write_text("{}")
        self.client._request("GET", "/api/")
        self.assertIsNone(self.client._session_key)
        self.assertEqual(self.client.server_id, "server-new")
        self.assertFalse(self.client.cache_path.exists())


if __name__ == "__main__":
    unittest.main()
