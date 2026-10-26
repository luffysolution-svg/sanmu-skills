from email.message import Message
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "sanmu-paper-search" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import _ai4scholar
import _core
import _easyscholar
import _providers
import fetch_pdf
import paper_search


class FakeResponse:
    def __init__(self, body, status=200, headers=None, url="https://ai4scholar.net/test"):
        self.payload = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body)).encode("utf-8")
        self.status = status
        self.url = url
        self.headers = Message()
        for k, v in (headers or {"Content-Type": "application/json"}).items():
            self.headers[k] = str(v)

    def read(self, size=None):
        return self.payload if size is None else self.payload[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class EasyScholarAndAi4ScholarTests(unittest.TestCase):
    def test_easyscholar_key_whitespace_alias_cache_and_redaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "es_cache.json"
            calls = []

            def opener(req, timeout=20):
                calls.append(req.full_url)
                if "Angewandte+Chemie+International+Edition" in req.full_url:
                    return FakeResponse({
                        "code": 200,
                        "msg": "SUCCESS",
                        "data": {
                            "officialRank": {
                                "all": {
                                    "sciif": "17.6",
                                    "sciif5": "16.7",
                                    "sci": "Q1",
                                    "sciUp": "化学1区",
                                    "sciUpTop": "化学TOP",
                                    "jci": "2.54",
                                },
                                "select": {},
                            },
                            "customRank": {"rankInfo": [], "rank": []},
                        },
                    }, url=req.full_url)
                return FakeResponse({"code": 200, "msg": "SUCCESS", "data": {"officialRank": {"all": None}}}, url=req.full_url)

            env = {
                "EASYSCHOLAR_SECRET_KEY": "a7a 46efdbc9b4809b02945927ab88dd0",
                "EASYSCHOLAR_CACHE_PATH": str(cache_file),
            }
            client = _providers.Client(env=env, opener=opener, sleep=lambda _: None)
            out_dir = Path(tmp) / "rank-run"
            code = paper_search.main(["rank", "Angewandte Chemie", "--out", str(out_dir)], client=client)
            self.assertEqual(code, 0)
            self.assertEqual(len(calls), 1)
            self.assertIn("secretKey=a7a46efdbc9b4809b02945927ab88dd0", calls[0])

            run_data = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))
            vr = run_data["venue_ranks"][0]["venue_rank"]
            self.assertTrue(vr["is_top_tier"])
            self.assertEqual(vr["if"], 17.6)
            self.assertIn("🏆 化学1区 TOP", vr["badges"])
            self.assertEqual(_core.check_bundle(out_dir)["status"], "success")

            # Verify secret redaction in raw audit files
            raw_text = (out_dir / "raw/0001.json").read_text(encoding="utf-8")
            self.assertNotIn("a7a46efdbc9b4809b02945927ab88dd0", raw_text)
            self.assertTrue("[REDACTED]" in raw_text or "%5BREDACTED%5D" in raw_text)

            # Second lookup hits persistent cache with 0 network calls
            out_dir2 = Path(tmp) / "rank-run-cached"
            code2 = paper_search.main(["rank", "Angewandte Chemie", "--out", str(out_dir2)], client=client)
            self.assertEqual(code2, 0)
            self.assertEqual(len(calls), 1)

    def test_rank_cache_ttl_refreshes_positive_negative_and_alias_entries(self):
        expired = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        fresh = datetime.now(timezone.utc).isoformat()
        client = _providers.Client(env={"EASYSCHOLAR_SECRET_KEY": "test"}, sleep=lambda _: None)
        response = {"officialRank": {"all": {"sciif": "20", "sci": "Q1"}}}
        for previous in ({"if": 1}, None):
            with self.subTest(previous=previous):
                cache = {"advanced materials": {"rank": previous, "updated_at": expired}}
                with patch.object(_easyscholar, "request_publication_rank", return_value=response) as request:
                    rank = _easyscholar.lookup_venue_rank(client, "Advanced Materials", cache=cache)
                self.assertEqual(rank["if"], 20)
                request.assert_called_once()
                with patch.object(_easyscholar, "request_publication_rank", side_effect=AssertionError("fresh cache requested")):
                    self.assertEqual(_easyscholar.lookup_venue_rank(client, "Advanced Materials", cache=cache)["if"], 20)
        cache = {"journal of the american chemical society": {"rank": {"if": 1}, "updated_at": expired}}
        with patch.object(_easyscholar, "request_publication_rank", return_value=response) as request:
            self.assertEqual(_easyscholar.lookup_venue_rank(client, "JACS", cache=cache)["if"], 20)
            request.assert_called_once()
        for stamp in (expired, "invalid", None):
            cache = {"advanced materials": {"rank": None, "updated_at": stamp}}
            papers = [{"venue": "Advanced Materials"}]
            with patch.object(_easyscholar, "request_publication_rank", return_value=response) as request:
                _easyscholar.enrich_papers_with_ranks(client, papers, cache=cache)
            self.assertEqual(papers[0]["venue_rank"]["if"], 20)
            request.assert_called_once()
        cache = {"advanced materials": {"rank": None, "updated_at": fresh}}
        with patch.object(_easyscholar, "request_publication_rank", side_effect=AssertionError("fresh miss requested")):
            self.assertIsNone(_easyscholar.lookup_venue_rank(client, "Advanced Materials", cache=cache))

    def test_rank_run_persists_shared_cache_and_reports_source_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            env = {"EASYSCHOLAR_SECRET_KEY": "test", "EASYSCHOLAR_CACHE_PATH": str(base / "cache.json")}
            paper = _core.record("crossref", "test", "Example", 2025, {}, [], None, "journal-article")
            paper["venue"] = "Advanced Materials"
            input_file = base / "input.json"
            input_file.write_text(json.dumps([paper]), encoding="utf-8")
            response = {"officialRank": {"all": {"sciif": "20", "sci": "Q1"}}}
            client = _providers.Client(env=env, sleep=lambda _: None)
            args = paper_search.parser().parse_args(["rank", "--run", str(input_file), "--out", str(base / "ranked")])
            with patch.object(_easyscholar, "request_publication_rank", return_value=response):
                run, code = _easyscholar.execute(args, client)
            self.assertEqual(code, 0)
            cache = _easyscholar.load_rank_cache(env)
            self.assertEqual(cache["advanced materials"]["rank"]["if"], 20)
            # --no-cache refreshes and persists new values in the same shared cache.
            args = paper_search.parser().parse_args(["rank", "--run", str(input_file), "--no-cache", "--out", str(base / "refresh")])
            with patch.object(_easyscholar, "request_publication_rank", return_value={"officialRank": {"all": {"sciif": "21"}}}):
                _easyscholar.execute(args, client)
            self.assertEqual(_easyscholar.load_rank_cache(env)["advanced materials"]["rank"]["if"], 21)
            for venues, has_run, expected in (([], True, "partial"), (["Advanced Materials"], False, "failed"), (["Advanced Materials", "ACS Catalysis"], False, "partial")):
                argv = ["rank", *venues, "--no-cache", "--out", str(base / expected / str(has_run))]
                if has_run:
                    argv.extend(["--run", str(input_file)])
                args = paper_search.parser().parse_args(argv)
                def lookup(client, key, venue):
                    if len(venues) == 2 and venue == "Advanced Materials":
                        return response
                    raise _providers.SourceError("invalid key", 403)
                with patch.object(_easyscholar, "request_publication_rank", side_effect=lookup):
                    run, code = _easyscholar.execute(args, client)
                self.assertEqual(run["status"], expected)
                self.assertNotEqual(code, 0)
                self.assertEqual(run["source_status"]["easyscholar"]["status"], "error")
                self.assertIn("invalid key", run["source_status"]["easyscholar"]["error"])
                self.assertEqual(_core.check_bundle(args.out)["status"], "success")

    def test_ai4scholar_gs_patents_pubmed_snippets_draw_and_autocite(self):
        with tempfile.TemporaryDirectory() as tmp:
            def opener(req, timeout=40):
                u = req.full_url
                if "/google-scholar/v1/search" in u:
                    return FakeResponse({
                        "query": "NiO",
                        "page": 1,
                        "resultsCount": 1,
                        "results": [{
                            "id": "gs123",
                            "title": "Single-atom Ni on g-C3N4 for H2 evolution",
                            "link": "https://onlinelibrary.wiley.com/doi/10.1002/adma.202313513",
                            "publicationInfo": "W Sen, Z Li\u00a0- Advanced Materials, 2024 - Wiley Online Library",
                            "year": 2024,
                            "citedBy": 118,
                            "snippet": "Frustrated Lewis pairs on Ni/NiOx...",
                            "pdfUrl": "https://arxiv.org/pdf/2401.00001.pdf",
                        }],
                    }, headers={"Content-Type": "application/json", "X-Credits-Charged": "1", "X-Credits-Remaining": "999"})
                if "/google-scholar/v1/patents" in u:
                    return FakeResponse({
                        "query": "photocatalyst",
                        "page": 1,
                        "resultsCount": 1,
                        "results": [{
                            "patentId": "patent/CN116020450B/en",
                            "publicationNumber": "CN116020450B",
                            "title": "Single-atom photocatalyst preparation method",
                            "inventor": "Zhang San, Li Si",
                            "assignee": "South China Agricultural University",
                            "publicationDate": "2024-05-01",
                            "snippet": "A method for preparing single atom catalyst...",
                            "pdfUrl": "https://patentimages.storage.googleapis.com/test.pdf",
                        }],
                    }, headers={"Content-Type": "application/json", "X-Credits-Charged": "1", "X-Credits-Remaining": "998"})
                if "/pubmed/v1/paper/search" in u:
                    return FakeResponse({
                        "total": 1,
                        "papers": [{
                            "pmid": "39575807",
                            "title": "Single-atom nanozyme therapy",
                            "abstract": "Nanozyme abstract text.",
                            "authors": [{"name": "Alice Smith", "affiliation": "MIT"}],
                            "journal": {"title": "Nature Catalysis", "pubDate": "2025-02-10"},
                            "doi": "10.1038/s41929-025-00001-1",
                            "pmcid": "PMC1234567",
                            "meshTerms": ["Catalysis", "Nanoparticles"],
                        }],
                    }, headers={"Content-Type": "application/json", "X-Credits-Charged": "1", "X-Credits-Remaining": "997"})
                if "/api/proxy/nano/generate" in u:
                    return FakeResponse({
                        "success": True,
                        "imageUrl": "https://ai4scholar.net/fake-fig.png",
                        "optimizedPrompt": "Optimized schematic prompt",
                        "creditCost": 3,
                    }, headers={"Content-Type": "application/json", "X-Credits-Charged": "3", "X-Credits-Remaining": "994"})
                if "fake-fig.png" in u:
                    return FakeResponse(b"\x89PNG\r\n\x1a\nfakeimagebytes", headers={"Content-Type": "image/png"})
                if "/api/proxy/auto-cite" in u:
                    sse_body = (
                        'event: progress\ndata: {"step": "searching"}\n\n'
                        'event: result\ndata: {"annotatedText": "Single-atom catalysts exhibit high activity [1].", '
                        '"references": [{"number": 1, "formatted": "[1] Wu S, et al. Adv. Mater. 2024.", "title": "Ni/NiOx Photocatalyst", "year": 2024, "doi": "10.1002/adma.202313513", "venue": "Advanced Materials", "citedBy": 118}], '
                        '"bibtex": "@article{wu2024, title={Ni/NiOx Photocatalyst}}"}\n\n'
                    )
                    return FakeResponse(sse_body, headers={"Content-Type": "text/event-stream", "X-Credits-Charged": "2", "X-Credits-Remaining": "992"})
                raise AssertionError("Unexpected URL: " + u)

            env = {"AI4SCHOLAR_API_KEY": "sk-user-secret-test-key"}
            client = _providers.Client(env=env, opener=opener, sleep=lambda _: None)

            # 1. Google Scholar search with non-breaking space publicationInfo
            gs_out = Path(tmp) / "gs"
            self.assertEqual(paper_search.main(["ai4scholar", "NiO", "--mode", "gs-search", "--out", str(gs_out)], client=client), 0)
            gs_papers = json.loads((gs_out / "papers.json").read_text(encoding="utf-8"))
            self.assertEqual(gs_papers[0]["venue"], "Advanced Materials")
            self.assertEqual(gs_papers[0]["authors"][0]["name"], "W Sen")
            self.assertEqual(gs_papers[0]["identifiers"]["doi"], ["10.1002/adma.202313513"])
            self.assertEqual(_core.check_bundle(gs_out)["status"], "success")

            # 2. Google Patents search
            pat_out = Path(tmp) / "pat"
            self.assertEqual(paper_search.main(["ai4scholar", "photocatalyst", "--mode", "patents", "--country", "CN", "--out", str(pat_out)], client=client), 0)
            pat_papers = json.loads((pat_out / "papers.json").read_text(encoding="utf-8"))
            self.assertEqual(pat_papers[0]["identifiers"]["patent"], ["CN116020450B"])
            self.assertEqual(pat_papers[0]["authors"][0]["affiliations"], ["South China Agricultural University"])
            self.assertEqual(_core.check_bundle(pat_out)["status"], "success")

            # 3. PubMed search
            pm_out = Path(tmp) / "pm"
            self.assertEqual(paper_search.main(["ai4scholar", "nanozyme", "--mode", "pubmed-search", "--out", str(pm_out)], client=client), 0)
            pm_papers = json.loads((pm_out / "papers.json").read_text(encoding="utf-8"))
            self.assertEqual(pm_papers[0]["identifiers"]["pmid"], ["39575807"])
            self.assertEqual(pm_papers[0]["venue"], "Nature Catalysis")
            self.assertIn("Catalysis", pm_papers[0]["mesh_terms"])
            self.assertEqual(_core.check_bundle(pm_out)["status"], "success")

            # 4. Nano Draw
            draw_out = Path(tmp) / "draw"
            self.assertEqual(paper_search.main(["ai4scholar", "NiOx schematic", "--mode", "draw", "--draw-action", "smart", "--out", str(draw_out)], client=client), 0)
            self.assertTrue((draw_out / "figure.png").is_file())
            self.assertEqual(_core.check_bundle(draw_out)["status"], "success")

            # 5. Auto-Cite SSE
            cite_out = Path(tmp) / "cite"
            long_text = "Single-atom catalysts with frustrated Lewis pairs have attracted wide attention in solar-driven photocatalytic hydrogen evolution reactions over carbon nitride nanosheets."
            self.assertEqual(paper_search.main(["ai4scholar", long_text, "--mode", "auto-cite", "--out", str(cite_out)], client=client), 0)
            self.assertTrue((cite_out / "annotated_text.md").is_file())
            self.assertTrue((cite_out / "references.bib").is_file())
            cite_papers = json.loads((cite_out / "papers.json").read_text(encoding="utf-8"))
            self.assertEqual(cite_papers[0]["identifiers"]["doi"], ["10.1002/adma.202313513"])
            self.assertEqual(_core.check_bundle(cite_out)["status"], "success")

    def test_publisher_pdf_candidates_from_landing_urls(self):
        cands = fetch_pdf.publisher_pdf_candidates("https://onlinelibrary.wiley.com/doi/full/10.1002/adma.202313513")
        self.assertIn("https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/adma.202313513?download=true", cands)
        sp_cands = fetch_pdf.publisher_pdf_candidates("https://www.nature.com/articles/s41586-024-07487-w")
        self.assertIn("https://www.nature.com/content/pdf/10.1038/s41586-024-07487-w.pdf" if "10.1038" in "s41586" else "https://www.nature.com/articles/s41586-024-07487-w.pdf", sp_cands)


if __name__ == "__main__":
    unittest.main()
