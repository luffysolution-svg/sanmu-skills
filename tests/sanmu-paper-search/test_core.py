"""Offline contract tests; no credentials, network, Zotero or third-party packages."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

SCRIPTS = (Path(__file__).resolve().parents[2] / 'skills' / 'sanmu-paper-search') / "scripts"
sys.path.insert(0, str(SCRIPTS))
from _core import deduplicate, ids, record, check_bundle
from _providers import Client, Providers, SourceError, normalize
from paper_search import main


class Response:
    def __init__(self, data):
        self.status = 200
        self.headers = {}
        self.payload = json.dumps(data, ensure_ascii=False).encode("utf-8")

    def read(self, size=-1):
        return self.payload[:size] if size >= 0 else self.payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def s2(number, **extra):
    return {"paperId": str(number), "title": "论文 " + str(number), "year": 2025,
            "externalIds": {"DOI": "10.1234/" + str(number)},
            "authors": [{"authorId": "42", "name": "张三"}], **extra}


class CoreTests(unittest.TestCase):
    def invoke(self, argv, client):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(argv, client)
        return code, json.loads(output.getvalue())

    def test_transitive_ids_conservative_fallback_and_conflicts(self):
        authors = [{"name": "A Name", "identifiers": {}, "affiliations": []}]
        def paper(title, identifiers, kind="journal-article"):
            return record("mock", title, title, 2025, identifiers, authors, kind=kind)
        a = paper("Original", ids(doi="https://doi.org/10.1234/A", openalex="W1"))
        b = paper("Changed title", ids(doi="10.1234/a", semantic_scholar="S1"))
        c = paper("Third title", ids(semantic_scholar="S1", pmid="123"))
        merged = deduplicate([c, a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["identifiers"]["doi"], ["10.1234/a"])
        self.assertEqual(len(merged[0]["versions"]), 3)
        self.assertTrue(any(v["field"] == "title" for v in merged[0]["conflicts"]))
        distinct = [paper("Same", ids(doi="10.1234/one")), paper("Same", ids(doi="10.1234/two"))]
        self.assertEqual(len(deduplicate(distinct)), 2)
        self.assertEqual(len(deduplicate([distinct[0], paper("Same", {})])), 1)
        self.assertEqual(len(deduplicate([distinct[0], paper("Same", {}, "preprint")])), 2)
        bridge = paper("Bridge", ids(doi="10.1234/two", openalex="W1"))
        conflicts = deduplicate([a, bridge])
        self.assertEqual(len(conflicts), 2)
        self.assertTrue(all(len(p["identifiers"]["doi"]) == 1 for p in conflicts))
        self.assertTrue(all(p["relations"][0]["type"] == "identity_conflict" for p in conflicts))

    def test_pagination_source_errors_redaction_and_bundle_integrity(self):
        env = {"SEMANTIC_SCHOLAR_API_KEY": "FAKE_PRIVATE_KEY", "OPENALEX_API_KEY": "FAKE_OTHER_KEY"}
        calls, sleeps = [], []
        def opener(request, timeout):
            self.assertEqual(request.method, "GET")
            calls.append(request.full_url)
            query = parse_qs(urlsplit(request.full_url).query)
            if "openalex" in request.full_url:
                raise HTTPError(request.full_url, 401, "denied", {}, io.BytesIO(b'{"error":"FAKE_OTHER_KEY"}'))
            offset = int(query.get("offset", ["0"])[0])
            if len([c for c in calls if "semanticscholar" in c]) == 1:
                raise HTTPError(request.full_url, 429, "retry", {"Retry-After": "0"}, io.BytesIO(b'{"token":"FAKE_PRIVATE_KEY"}'))
            if offset == 0:
                return Response({"data": [s2(n) for n in range(10)], "total": 50, "next": 10,
                                 "debug": {"api_key": "NOT_IN_ENV", "authorization": "Bearer X"}})
            return Response({"data": [s2(n) for n in range(10, 12)], "total": 50, "next": 12})
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "run"
            client = Client(env, opener, sleeps.append)
            code, run = self.invoke(["search", "催化", "--sources", "semantic-scholar,openalex", "--limit", "12", "--out", str(out)], client)
            self.assertEqual(code, 1)
            self.assertEqual(run["status"], "partial")
            self.assertEqual(run["counts"]["papers"], 12)
            self.assertEqual(run["source_status"]["semantic-scholar"]["pages"], 2)
            self.assertTrue(run["source_status"]["semantic-scholar"]["truncated"])
            self.assertEqual(run["source_status"]["openalex"]["status"], "error")
            self.assertIn("offset=10", calls[-2])
            self.assertEqual(len(calls), 4)
            text = "".join(p.read_text(encoding="utf-8") for p in out.rglob("*") if p.is_file())
            for secret in ("FAKE_PRIVATE_KEY", "FAKE_OTHER_KEY", "NOT_IN_ENV", "Bearer X"):
                self.assertNotIn(secret, text)
            self.assertEqual(check_bundle(out)["status"], "success")
            count = len(calls)
            code, error = self.invoke(["search", "again", "--out", str(out)], client)
            self.assertEqual(code, 2)
            self.assertEqual(len(calls), count)
            self.assertEqual(error["error"]["kind"], "input")
            (out / "papers.json").write_text("[]", encoding="utf-8")
            self.assertEqual(check_bundle(out)["status"], "failed")
            def failing_page(request, timeout):
                query = parse_qs(urlsplit(request.full_url).query)
                if int(query.get("offset", ["0"])[0]) == 0:
                    return Response({"data": [s2(n) for n in range(10)], "total": 30, "next": 10})
                raise HTTPError(request.full_url, 503, "down", {}, io.BytesIO(b'{}'))
            partial_out = Path(directory) / "partial-page"
            code, run = self.invoke(["search", "x", "--sources", "semantic-scholar", "--limit", "12", "--out", str(partial_out)],
                                    Client({}, failing_page, lambda _: None))
            self.assertEqual(code, 1)
            self.assertEqual(run["counts"]["papers"], 10)
            self.assertEqual(run["source_status"]["semantic-scholar"]["fetched"], 10)

    def test_malformed_second_pages_preserve_completed_pages(self):
        cases = [("search", provider, None) for provider in ("openalex", "semantic-scholar", "crossref")]
        cases += [("author", provider, None) for provider in ("openalex", "semantic-scholar")]
        cases += [("related", "openalex", "citations"),
                  ("related", "semantic-scholar", "references"),
                  ("related", "semantic-scholar", "citations")]
        with tempfile.TemporaryDirectory() as directory:
            for index, (command, provider, relation) in enumerate(cases):
                for malformed in ("missing_rows", "invalid_row", "bad_normalization", "bad_envelope"):
                    with self.subTest(command=command, provider=provider, relation=relation, malformed=malformed):
                        def opener(request, timeout):
                            url = urlsplit(request.full_url)
                            params = parse_qs(url.query)
                            if provider == "crossref":
                                self.assertEqual(params.get("query.title"), ["test"])
                                self.assertEqual(params.get("sort"), ["score"])
                                self.assertEqual(params.get("order"), ["desc"])
                                self.assertNotIn("query.bibliographic", params)
                            if provider == "openalex" and url.path.startswith("/works/"):
                                return Response({"id": "https://openalex.org/W99"})
                            second = params.get("cursor", ["*"])[0] != "*" or params.get("offset", ["0"])[0] != "0"
                            rows = []
                            for number in range(10):
                                if provider == "openalex":
                                    row = {"id": f"https://openalex.org/W{number}", "title": str(number), "publication_year": 2025}
                                elif provider == "crossref":
                                    row = {"DOI": f"10.1234/{number}", "title": [str(number)]}
                                else:
                                    row = s2(number)
                                rows.append(row)
                            key = "citedPaper" if relation == "references" else "citingPaper"
                            if second:
                                if malformed == "missing_rows":
                                    return Response({"message": {}} if provider == "crossref" else {})
                                if malformed == "bad_envelope":
                                    return Response({"message": []} if provider == "crossref" else {"meta": []})
                                if malformed == "invalid_row":
                                    rows = [None]
                                else:
                                    bad = {"authorships": [None]} if provider == "openalex" else {"author": [None]} if provider == "crossref" else {"authors": [None]}
                                    rows = [bad]
                            if provider == "openalex":
                                return Response({"results": rows, "meta": {"count": 30, "next_cursor": "page-two"}})
                            if provider == "crossref":
                                return Response({"message": {"items": rows, "total-results": 30, "next-cursor": "page-two"}})
                            if command == "related":
                                rows = [{key: row} if isinstance(row, dict) else row for row in rows]
                            return Response({"data": rows, "total": 30, "next": 10})
                        out = Path(directory) / f"{index}-{malformed}"
                        args = [command]
                        if command == "search":
                            args += ["test", "--sources", provider]
                        elif command == "author":
                            args += ["--author-id", "A42" if provider == "openalex" else "42", "--provider", provider]
                        else:
                            args += ["openalex:W99" if provider == "openalex" else "s2:99", "--relation", relation, "--sources", provider]
                        code, run = self.invoke(args + ["--limit", "12", "--out", str(out)],
                                                Client({}, opener, lambda _: None))
                        self.assertEqual(code, 1, run)
                        self.assertEqual(run["counts"]["papers"], 10)
                        status = run["source_status"][provider]
                        self.assertEqual(status["status"], "error")
                        self.assertEqual(status["fetched"], 10)
                        self.assertEqual(status["pages"], 1)
                        self.assertTrue(status["truncated"])
                        if provider == "crossref":
                            self.assertEqual(status["query_strategy"], "title_only")
                            self.assertEqual(run["query"]["source_query_strategy"]["crossref"], "title_only")
                        self.assertEqual(check_bundle(out)["status"], "success")

    def test_command_contracts_lookup_authors_relations_oa_and_private_merge(self):
        markup = normalize("crossref", {"DOI": "10.1234/1.", "title": ["Ni/NiO\n <sub>x</sub>\n &amp; H<sub>2</sub>"],
                                         "published": {"date-parts": [[2024, 5, 19]]},
                                         "abstract": "<jats:p>H<sub>2</sub> &amp; CO<sub>2</sub></jats:p>"})
        self.assertEqual(markup["title"], "Ni/NiO x & H2")
        self.assertEqual(markup["abstract"], "H2 & CO2")
        self.assertEqual(markup["identifiers"]["doi"], ["10.1234/1"])
        self.assertEqual(markup["publication_date"], "2024-05-19")
        oa_arx = normalize("openalex", {"id": "https://openalex.org/W42", "doi": "https://doi.org/10.48550/arXiv.2401.12345v2",
                                         "locations": [{"landing_page_url": "https://arxiv.org/abs/2401.12345v2", "is_oa": True}]})
        self.assertEqual(oa_arx["identifiers"]["arxiv"], ["2401.12345v2"])
        self.assertTrue(any(loc["url"] == "https://arxiv.org/pdf/2401.12345v2.pdf" for loc in oa_arx["oa_locations"]))
        seen = []
        def opener(request, timeout):
            seen.append(request.full_url)
            url = urlsplit(request.full_url)
            if "/paper/search" in url.path:
                return Response({"data": [s2(1)], "total": 1})
            if "/author/search" in url.path:
                return Response({"data": [{"authorId": "42", "name": "A Name", "paperCount": 2}], "total": 1})
            if "/author/42/papers" in url.path:
                return Response({"data": [s2(1)]})
            if url.path.endswith("/references"):
                return Response({"data": [{"citedPaper": s2(2)}]})
            if url.path.endswith("/citations"):
                return Response({"data": [{"citingPaper": s2(2)}]})
            if "/recommendations/" in url.path:
                return Response({"recommendedPapers": [s2(3)]})
            if "unpaywall" in url.netloc:
                return Response({"doi": "10.1234/1", "is_oa": True, "oa_locations": [
                    {"url_for_pdf": "https://example.org/open.pdf", "license": "cc-by"}]})
            return Response(s2(1, abstract="Returned abstract", openAccessPdf={"url": "https://example.org/unknown.pdf"}))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = Client({"UNPAYWALL_EMAIL": "test@example.invalid"}, opener, lambda _: None)
            def invoke(command, *arguments):
                out = root / (command + str(len(list(root.iterdir()))))
                code, result = self.invoke([command, *arguments, "--out", str(out)], client)
                self.assertEqual(code, 0, result)
                self.assertEqual(check_bundle(out)["status"], "success")
                return out, result
            lookup, result = invoke("lookup", "doi:10.1234/1", "--sources", "semantic-scholar")
            self.assertTrue(result["source_status"]["semantic-scholar"]["id_verified"])
            _, result = invoke("author", "A Name", "--provider", "semantic-scholar")
            self.assertEqual(result["author_candidates"][0]["author_id"], "42")
            self.assertEqual(result["counts"]["papers"], 0)
            _, dated = invoke("author", "--author-id", "42", "--provider", "semantic-scholar", "--year-from", "2026")
            self.assertEqual(dated["counts"]["papers"], 0)
            self.assertEqual(dated["year_filter"]["locally_removed"], 1)
            self.assertEqual(dated["year_filter"]["mode"], "provider_and_local")
            _, dated = invoke("search", "test", "--sources", "semantic-scholar", "--year-from", "2024", "--year-to", "2026")
            self.assertTrue(any("year=2024-2026" in u for u in seen))
            self.assertEqual(dated["limit_scope"], "per_source_candidate_budget")
            for relation, direction in (("references", "seed_to_result"), ("citations", "result_to_seed"),
                                        ("recommendations", "similarity_not_citation")):
                related_out, _ = invoke("related", "s2:1", "--relation", relation, "--sources", "semantic-scholar")
                related_papers = json.loads((related_out / "papers.json").read_text(encoding="utf-8"))
                self.assertEqual(related_papers[0]["relations"], [{"type": relation, "seed": "s2:1", "direction": direction}])
            oa, result = invoke("oa", str(lookup))
            self.assertFalse(result["downloaded"])
            papers = json.loads((oa / "papers.json").read_text(encoding="utf-8"))
            self.assertIsNone(papers[0]["oa_locations"][0]["is_oa"])
            self.assertEqual(papers[0]["oa_locations"][-1]["license_status"], "open")
            self.assertFalse(papers[0]["read"])
            host = root / "host.json"
            host.write_text(json.dumps({"library": {"id": 1, "type": "user", "name": "PRIVATE"}, "items": [
                {"key": "ITEMKEY", "data": {"title": "论文 1", "DOI": "10.1234/1", "itemType": "journalArticle", "notes": "PRIVATE NOTE"},
                 "attachments": [{"path": "PRIVATE STORAGE", "status": "file_obtained"}], "annotations": "PRIVATE ANNOTATION"}]}), encoding="utf-8")
            merged, result = invoke("merge", str(lookup), "--zotero", str(host))
            self.assertEqual(result["local_match_count"], 1)
            contents = (merged / "papers.json").read_text(encoding="utf-8")
            self.assertNotIn("PRIVATE", contents)
            match = json.loads(contents)[0]["local_matches"][0]
            self.assertEqual(match["key"], "ITEMKEY")
            self.assertEqual(match["attachment_status"], "listed_not_checked")
            invoke("diff", str(lookup), str(merged))
            self.assertTrue(any("/recommendations/v1/" in u for u in seen))
            doctor_code, doctor = self.invoke(["doctor"], client)
            self.assertEqual(doctor_code, 0)
            self.assertTrue(all(isinstance(v, bool) for v in doctor["configuration"].values()))
            code, failure = self.invoke(["lookup", "not-a-doi", "--out", str(root / "invalid")], client)
            self.assertEqual(code, 2)
            self.assertFalse((root / "invalid").exists())
            with self.assertRaises(SourceError):
                Providers(client).lookup("semantic-scholar", "10.1234/different")

    def test_identifier_validation_normalization_and_preflight(self):
        from _providers import parse_id
        self.assertEqual(parse_id("pmcid:123"), ("pmcid", "PMC123"))
        self.assertEqual(parse_id("pmcid:pmc123"), ("pmcid", "PMC123"))
        self.assertEqual(parse_id("w123"), ("openalex", "W123"))
        self.assertEqual(parse_id("38639018"), ("pmid", "38639018"))
        self.assertEqual(parse_id("arxiv:hep-th/9901001v2"), ("arxiv", "hep-th/9901001v2"))
        a = normalize("semantic-scholar", {"paperId": "abc", "externalIds": {"PubMedCentral": "123"}})
        b = record("europepmc", "PMC123", "Title", None, ids(pmcid="PMC123"), [])
        self.assertEqual(len(deduplicate([a, b])), 1)
        with tempfile.TemporaryDirectory() as tmp:
            for index, value in enumerate(("pmid:abc", "openalex:A123", "openalex:W1/path", "corpusid:abc", "arxiv:wrong", "pmcid:../x")):
                out = Path(tmp) / str(index)
                code, _ = self.invoke(["lookup", value, "--out", str(out)],
                                      Client({}, lambda *a, **kw: self.fail("Unexpected network")))
                self.assertEqual(code, 2, value)
                self.assertFalse(out.exists())

    def test_nonfinite_get_json_is_source_failure_with_valid_bundle(self):
        for body in (b'{"results":[],"meta":{"count":NaN}}', b'{"results":[],"meta":{"count":1e999}}'):
            with tempfile.TemporaryDirectory() as tmp:
                response = Response({})
                response.payload = body
                out = Path(tmp) / "run"
                code, run = self.invoke(["search", "x", "--sources", "openalex", "--out", str(out)],
                                        Client({}, lambda *a, **kw: response))
                self.assertEqual(code, 3, run)
                self.assertEqual(run["source_status"]["openalex"]["status"], "error")
                self.assertEqual(check_bundle(out)["status"], "success")
                self.assertEqual(json.loads((out / "raw/0001.json").read_text(encoding="utf-8"))["body"]["unparsed_body"], body.decode())

    def test_unpaywall_fallback_email_and_trimmed_credentials(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            params = parse_qs(urlsplit(request.full_url).query)
            if "unpaywall" in request.full_url:
                self.assertEqual(params["email"], ["contact@example.invalid"])
                return Response({"doi": "10.1234/1", "is_oa": False, "oa_locations": []})
            self.assertEqual(request.get_header("X-api-key"), "test-key")
            return Response(s2(1))
        with tempfile.TemporaryDirectory() as tmp:
            code, run = self.invoke(["oa", "10.1234/1", "--sources", "semantic-scholar", "--unpaywall", "required", "--out", str(Path(tmp) / "run")],
                Client({"POLITE_EMAIL": " contact@example.invalid ", "S2_API_KEY": " test-key "}, opener, lambda _: None))
            self.assertEqual(code, 0, run)
            self.assertEqual(run["source_status"]["unpaywall:10.1234/1"]["status"], "ok")
            self.assertEqual(len(calls), 2)

    def test_openalex_partial_batch_recovers_missing_targets(self):
        calls = []
        def work(number):
            return {"id": "https://openalex.org/W" + str(number), "title": str(number)}
        def opener(request, timeout):
            calls.append(request.full_url)
            parsed = urlsplit(request.full_url)
            if parsed.path == "/works/W1":
                return Response(dict(work(1), referenced_works=["https://openalex.org/W2", "https://openalex.org/W3"]))
            if parsed.path == "/works":
                return Response({"results": [work(2)]})
            if parsed.path == "/works/W3":
                return Response(work(3))
            self.fail("Unexpected request " + request.full_url)
        found, status = Providers(Client({}, opener, lambda _: None)).related("openalex", "W1", "references", 10)
        self.assertEqual([p["identifiers"]["openalex"][0] for p in found], ["W2", "W3"])
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["pages"], 3)
        self.assertEqual(len(calls), 3)

    def test_related_fallback_retains_original_failure_and_never_uses_unrelated_audit(self):
        from unittest.mock import patch
        client = Client({}, sleep=lambda _: None)
        client.raw = [{"body": {"externalIds": {"DOI": "10.1234/unrelated"}}}]
        adapter = Providers(client)
        with patch.object(adapter, "lookup", side_effect=SourceError("not found")):
            self.assertIsNone(adapter._extract_seed_doi("s2:abc"))
        restricted = SourceError("publisher elided references", 200, "publisher_restricted")
        with patch.object(adapter, "_page", side_effect=restricted), \
                patch.object(adapter, "_crossref_references_fallback", return_value=[]), \
                patch.object(client, "get", side_effect=SourceError("OpenAlex unavailable")):
            with self.assertRaises(SourceError):
                adapter.related("semantic-scholar", "10.1234/seed", "references", 10)
        fallback = record("crossref", "10.1234/ref", "Reference", 2025, ids(doi="10.1234/ref"), [])
        with patch.object(adapter, "_page", side_effect=restricted), \
                patch.object(adapter, "_crossref_references_fallback", return_value=[fallback]):
            found, status = adapter.related("semantic-scholar", "10.1234/seed", "references", 10)
            self.assertEqual(found[0]["identifiers"]["doi"], ["10.1234/ref"])
            self.assertEqual(status["status"], "error")
            self.assertEqual(status["reason"], "publisher_restricted")
            self.assertEqual(status["fallback_used"], "crossref+openalex")

    def test_author_from_paper_resolves_seed_ids_and_writes_summary(self):
        seed = s2(1, authors=[{"authorId": "42", "name": "Tian Xie", "externalIds": {"ORCID": "0000-0002-0987-4666"}},
                              {"authorId": "43", "name": "Xiang Fu"}])
        seen = []

        def opener(request, timeout):
            seen.append(request.full_url)
            url = urlsplit(request.full_url)
            query = parse_qs(url.query)
            if "/paper/" in url.path and not url.path.endswith("/papers"):
                return Response(seed)
            if url.path.endswith("/author/42/papers"):
                return Response({"data": [s2(5)]})
            if url.path.endswith("/author/43/papers"):
                return Response({"data": [s2(6)]})
            if "/author/search" in url.path:
                return Response({"data": [], "total": 0})
            return Response({"data": []})

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = Client({"S2_API_KEY": "FAKE_KEY"}, opener, lambda _: None)
            out = root / "from-paper"
            code, run = self.invoke(["author", "Tian Xie", "--provider", "semantic-scholar",
                                     "--from-paper", "doi:10.1234/1", "--out", str(out)], client)
            self.assertEqual(code, 0, run)
            self.assertEqual(run["author_mode"], "papers")
            self.assertEqual(run["counts"]["papers"], 1)
            self.assertEqual(run["source_status"]["semantic-scholar"]["author_id"], "42")
            self.assertTrue((out / "summary.md").exists())
            summary = (out / "summary.md").read_text(encoding="utf-8")
            self.assertIn("| # | Year | Title | Authors |", summary)
            self.assertIn("论文 5", summary)
            self.assertEqual(check_bundle(out)["status"], "success")

            listed = root / "list-authors"
            code, run = self.invoke(["author", "--provider", "semantic-scholar",
                                     "--from-paper", "doi:10.1234/1", "--out", str(listed)], client)
            self.assertEqual(code, 0, run)
            self.assertEqual(run["author_mode"], "paper_authors")
            self.assertEqual({c["author_id"] for c in run["author_candidates"]}, {"42", "43"})
            self.assertEqual(run["counts"]["papers"], 0)

            named = root / "no-match"
            code, run = self.invoke(["author", "Nobody Here", "--provider", "semantic-scholar",
                                     "--from-paper", "doi:10.1234/1", "--out", str(named)], client)
            self.assertEqual(code, 0, run)
            self.assertEqual(run["source_status"]["semantic-scholar"]["status"], "not_found")
            self.assertEqual(run["counts"]["papers"], 0)

            doctor_code, doctor = self.invoke(["doctor"], client)
            self.assertEqual(doctor_code, 0)
            self.assertIn("polite_email", doctor["configuration"])


if __name__ == "__main__":
    unittest.main()
