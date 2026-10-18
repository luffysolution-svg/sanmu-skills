"""Offline source contracts: direct repositories, unchanged defaults and email fallback."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from _core import DEFAULT_PROVIDERS, PROVIDERS, InputError, check_bundle, deduplicate, ids, record
from _providers import Client, Providers, SourceError, UnsupportedSource, normalize, parse_id
from paper_search import main, parser


class Response:
    status = 200
    headers = {}

    def __init__(self, body):
        self.body = body.encode() if isinstance(body, str) else json.dumps(body).encode()

    def read(self, size=-1):
        return self.body[:size] if size >= 0 else self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def entry(number=1, version=2, year=2024):
    return f'''<entry><id>http://arxiv.org/abs/2401.{number:05d}v{version}</id>
<title>A multiline\n title {number}</title><summary> An abstract &amp; evidence. </summary>
<published>{year}-01-02T00:00:00Z</published><updated>2025-01-01T00:00:00Z</updated>
<author><name>Alice Smith</name><arxiv:affiliation>Institute</arxiv:affiliation></author>
<arxiv:doi>10.1234/published</arxiv:doi><arxiv:journal_ref>Journal 2025</arxiv:journal_ref>
<link href="https://arxiv.org/abs/2401.{number:05d}v{version}" rel="alternate"/>
<link href="https://arxiv.org/pdf/2401.{number:05d}v{version}" title="pdf" type="application/pdf"/>
</entry>'''


def feed(entries="", total=1):
    return f'''<feed xmlns="http://www.w3.org/2005/Atom"
xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
<opensearch:totalResults>{total}</opensearch:totalResults>{entries}</feed>'''


def epmc(number=1, **extra):
    return {"id": str(number), "source": "MED", "pmid": str(number), "pmcid": f"PMC{number}",
            "doi": f"10.1234/{number}", "title": "Article &amp; evidence", "pubYear": "2024",
            "abstractText": "<p>Abstract <i>text</i>.</p>", "pubTypeList": {"pubType": ["Journal Article"]},
            "authorList": {"author": [{"fullName": "Alice Smith", "authorId": {"type": "ORCID", "value": "0000-0001"},
                "authorAffiliationDetailsList": {"authorAffiliation": [{"affiliation": "Institute"}]}}]},
            "isOpenAccess": "N", "inPMC": "Y", "versionNumber": "1",
            "fullTextUrlList": {"fullTextUrl": [{"url": "https://example.org/free", "availabilityCode": "F",
                                               "availability": "Free"}]}, **extra}


def epmc_page(rows, total=1, following=None):
    result = {"resultList": {"result": rows}, "hitCount": total}
    if following is not None:
        result["nextCursorMark"] = following
    return result


class SourceTests(unittest.TestCase):
    def invoke(self, args, client):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(args, client)
        return code, json.loads(output.getvalue())

    def test_defaults_and_explicit_sources(self):
        self.assertEqual(DEFAULT_PROVIDERS, ("openalex", "semantic-scholar", "crossref"))
        self.assertEqual(PROVIDERS[:2], ("openalex", "semantic-scholar"))
        for command in ("search", "lookup"):
            args = parser().parse_args([command, "x", "--out", "unused"])
            self.assertEqual(args.sources, list(DEFAULT_PROVIDERS))
        args = parser().parse_args(["related", "x", "--out", "unused"])
        self.assertEqual(args.sources, list(PROVIDERS[:2]))
        args = parser().parse_args(["search", "x", "--sources", "arxiv,europe-pmc", "--out", "unused"])
        self.assertEqual(args.sources, ["arxiv", "europe-pmc"])
        seen = []
        def opener(request, timeout):
            seen.append(urlsplit(request.full_url).netloc)
            if "openalex" in request.full_url:
                return Response({"results": [], "meta": {"count": 0}})
            if "crossref" in request.full_url:
                return Response({"message": {"items": [], "total-results": 0}})
            return Response({"data": [], "total": 0})
        with tempfile.TemporaryDirectory() as temp:
            code, _ = self.invoke(["search", "test", "--out", str(Path(temp) / "run")], Client({}, opener))
        self.assertEqual(code, 0)
        self.assertEqual(seen, ["api.openalex.org", "api.semanticscholar.org", "api.crossref.org"])

    def test_arxiv_parse_lookup_and_version_identity(self):
        seen = []
        def opener(request, timeout):
            seen.append(parse_qs(urlsplit(request.full_url).query))
            self.assertEqual(request.get_header("Accept"), "application/atom+xml")
            return Response(feed(entry()))
        client = Client({}, opener, lambda _: None)
        provider = Providers(client)
        for identifier in ("arxiv:2401.00001", "https://arxiv.org/abs/2401.00001v2", "2401.00001v2"):
            papers, status = provider.lookup("arxiv", identifier)
            self.assertTrue(status["id_verified"])
        p = papers[0]
        self.assertEqual(p["year"], 2024)
        self.assertEqual(p["authors"][0]["affiliations"], ["Institute"])
        self.assertEqual(p["abstract"], "An abstract & evidence.")
        self.assertEqual(p["type"], "preprint")
        self.assertNotIn("doi", p["identifiers"])
        self.assertEqual(p["relations"][0]["target_identifiers"], ids(doi="10.1234/published"))
        self.assertEqual(p["versions"][0]["version"], "v2")
        self.assertEqual(p["oa_locations"][0]["version"], "submittedVersion")
        self.assertEqual(p["oa_locations"][0]["license_status"], "unknown")
        published = record("crossref", "doi", p["title"], 2024, ids(doi="10.1234/published"), p["authors"], kind="journal-article")
        self.assertEqual(len(deduplicate([p, published])), 2)
        # An aggregate carrying both IDs must not transitively join the two versions.
        bridge = record("semantic-scholar", "S1", p["title"], 2024,
                        ids(doi="10.1234/published", arxiv="2401.00001v2"), p["authors"])
        for rows in ([p, bridge, published], [bridge, published, p], [published, p, bridge]):
            self.assertEqual(len(deduplicate(rows)), 2)
        with self.assertRaisesRegex(SourceError, "mismatch"):
            provider.lookup("arxiv", "arxiv:2401.00001v1")
        self.assertIn("xml", client.raw[0]["body"])

    def test_identifiers_unsupported_lookup_and_related_never_request(self):
        self.assertEqual(parse_id("https://arxiv.org/pdf/hep-th/9901001v3.pdf"), ("arxiv", "hep-th/9901001v3"))
        self.assertEqual(parse_id("pmcid:PMC123"), ("pmcid", "PMC123"))
        self.assertEqual(parse_id("PMC123"), ("pmcid", "PMC123"))
        for bad in ("arxiv:nonsense", "arxiv:2401.00001v0", "pmid:foo", "pmcid:foo"):
            with self.subTest(bad=bad), self.assertRaises(InputError):
                parse_id(bad)
        def forbidden(*args, **kwargs):
            self.fail("Unsupported operation issued a network request")
        client = Client({}, forbidden)
        provider = Providers(client)
        for source, identifier in (("arxiv", "10.1234/a"), ("europe-pmc", "arxiv:2401.00001"),
                                   ("crossref", "pmid:1"), ("semantic-scholar", "PMC1")):
            with self.assertRaises(UnsupportedSource):
                provider.lookup(source, identifier)
        for source in ("arxiv", "europe-pmc", "crossref"):
            for relation in ("references", "citations", "recommendations"):
                with self.assertRaises(UnsupportedSource):
                    provider.related(source, "10.1234/a", relation, 10)
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "unsupported"
            code, run = self.invoke(["related", "10.1234/a", "--sources", "arxiv,europe-pmc", "--out", str(out)], client)
            self.assertEqual(code, 3)
            self.assertEqual(run["status"], "failed")
            self.assertEqual({s["status"] for s in run["source_status"].values()}, {"unsupported"})
            self.assertEqual(check_bundle(out)["status"], "success")

    def test_europe_pmc_metadata_lookup_and_license(self):
        seen = []
        def opener(request, timeout):
            seen.append(parse_qs(urlsplit(request.full_url).query))
            return Response(epmc_page([epmc()]))
        provider = Providers(Client({}, opener, lambda _: None))
        for identifier in ("10.1234/1", "pmid:1", "pmcid:PMC1"):
            papers, status = provider.lookup("europe-pmc", identifier)
            self.assertTrue(status["id_verified"])
        self.assertEqual([v["query"][0] for v in seen], ['DOI:"10.1234/1"', 'EXT_ID:1 AND SRC:MED', 'PMCID:PMC1'])
        self.assertTrue(all(v["resultType"] == ["core"] for v in seen))
        p = papers[0]
        self.assertEqual(p["year"], 2024)
        self.assertEqual(p["abstract"], "Abstract text.")
        self.assertEqual(p["authors"][0]["identifiers"]["orcid"], ["0000-0001"])
        self.assertEqual(p["type"], "Journal Article")
        self.assertEqual(p["versions"][0]["version"], "1")
        self.assertTrue(p["oa_locations"][0]["is_oa"])
        self.assertEqual(p["oa_locations"][0]["license_status"], "unknown")
        self.assertEqual(normalize("europe-pmc", epmc(license="CC BY-NC"))["oa_locations"][0]["license_status"], "restricted")
        self.assertEqual(normalize("europe-pmc", epmc(license="CC-BY"))["oa_locations"][0]["license_status"], "open")
        with self.assertRaisesRegex(SourceError, "mismatch"):
            provider.lookup("europe-pmc", "pmid:2")
        empty = Providers(Client({}, lambda *a, **k: Response(epmc_page([], 0))))
        self.assertEqual(empty.lookup("europe-pmc", "PMC1")[1]["status"], "not_found")

    def test_pagination_budgets_years_and_repeated_cursor(self):
        for source in ("arxiv", "europe-pmc"):
            calls = []
            def opener(request, timeout):
                params = parse_qs(urlsplit(request.full_url).query)
                calls.append(params)
                start = 0 if len(calls) == 1 else 10
                size = int(params.get("max_results", params.get("pageSize"))[0])
                if source == "arxiv":
                    return Response(feed("".join(entry(n) for n in range(start + 1, start + size + 1)), 30))
                return Response(epmc_page([epmc(n) for n in range(start + 1, start + size + 1)], 30, "next"))
            with self.subTest(source=source):
                papers, status = Providers(Client({}, opener, lambda _: None)).search(source, "cancer", 12, 2024, 2025)
                self.assertEqual(len(papers), 12)
                self.assertEqual(status["pages"], 2)
                self.assertTrue(status["truncated"])
                self.assertEqual(calls[1].get("start", calls[1].get("cursorMark")), ["10"] if source == "arxiv" else ["next"])
                query = calls[0].get("search_query", calls[0].get("query"))[0]
                self.assertIn("submittedDate:[202401010000 TO 202512312359]" if source == "arxiv" else
                              "FIRST_PDATE:[2024-01-01 TO 2025-12-31]", query)
        repeated = Providers(Client({}, lambda *a, **k: Response(epmc_page([epmc()], 100, "*"))))
        self.assertEqual(repeated.search("europe-pmc", "x", 20)[1]["pages"], 1)
        for source in ("arxiv", "europe-pmc"):
            body = feed(entry(), 1) if source == "arxiv" else epmc_page([epmc()], 1, "next")
            p, status = Providers(Client({}, lambda *a, **k: Response(body))).search(source, "x", 1)
            self.assertFalse(status["truncated"])

    def test_repository_empty_pages_open_year_bounds_and_lookup_bundles(self):
        for source in ("arxiv", "europe-pmc"):
            for start, end, fragment in ((2024, None, "2024"), (None, 2025, "2025")):
                seen = []
                def opener(request, timeout):
                    seen.append(parse_qs(urlsplit(request.full_url).query))
                    return Response(feed("", 0) if source == "arxiv" else epmc_page([], 0))
                papers, status = Providers(Client({}, opener)).search(source, "x", 20, start, end)
                self.assertEqual(papers, [])
                self.assertEqual(status["pages"], 1)
                self.assertFalse(status["truncated"])
                query = seen[0].get("search_query", seen[0].get("query"))[0]
                self.assertIn(fragment, query)
                self.assertIn("9999" if end is None else "1000", query)
        with tempfile.TemporaryDirectory() as temp:
            for source, identifier in (("arxiv", "arxiv:2401.00001"), ("europe-pmc", "pmid:1")):
                body = feed(entry()) if source == "arxiv" else epmc_page([epmc()])
                out = Path(temp) / source
                code, run = self.invoke(["lookup", identifier, "--sources", source, "--out", str(out)],
                                        Client({}, lambda *a, **k: Response(body)))
                self.assertEqual(code, 0)
                self.assertTrue(run["source_status"][source]["id_verified"])
                self.assertEqual(check_bundle(out)["status"], "success")

    def test_invalid_second_page_preserves_previous_rows(self):
        for source in ("arxiv", "europe-pmc"):
            calls = []
            def opener(request, timeout):
                calls.append(request.full_url)
                if len(calls) == 2:
                    return Response("<broken" if source == "arxiv" else {"resultList": {"result": [None]}})
                return Response(feed("".join(entry(n) for n in range(1, 11)), 30) if source == "arxiv"
                                else epmc_page([epmc(n) for n in range(1, 11)], 30, "next"))
            with self.subTest(source=source), self.assertRaises(SourceError) as raised:
                Providers(Client({}, opener, lambda _: None)).search(source, "x", 12)
            self.assertEqual(len(raised.exception.papers), 10)
            self.assertEqual(raised.exception.pages, 1)

    def test_xml_retry_raw_redaction_body_limit_and_invalid_feed(self):
        calls, sleeps = [], []
        secret = "fallback@example.invalid"
        def opener(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise HTTPError(request.full_url, 429, "retry", {"Retry-After": "0"}, io.BytesIO(secret.encode()))
            return Response(feed(entry().replace("Institute", secret)))
        client = Client({"POLITE_EMAIL": secret}, opener, sleeps.append, clock=lambda: 0)
        Providers(client).search("arxiv", "x", 1)
        self.assertEqual(len(client.raw), 2)
        self.assertNotIn(secret, json.dumps(client.raw))
        self.assertIn(3.0, sleeps)
        with patch("_providers.MAX_BODY", 10), self.assertRaisesRegex(SourceError, "limit"):
            Client({}, lambda *a, **k: Response("x" * 11)).get("arxiv", "/api/query")
        for body in ("<feed>", '<!DOCTYPE feed [<!ENTITY a "b">]><feed/>', feed('<entry><title>Error</title><summary>bad query</summary></entry>')):
            client = Client({}, lambda *a, **k: Response(body))
            with self.assertRaisesRegex(SourceError, "Invalid arXiv feed"):
                client.get("arxiv", "/api/query")
            self.assertEqual(len(client.raw), 1)

    def test_local_year_guard_and_polite_unpaywall_doctor(self):
        seen = []
        secret = "polite@example.invalid"
        def opener(request, timeout):
            params = parse_qs(urlsplit(request.full_url).query)
            seen.append(request.full_url)
            if "unpaywall" in request.full_url:
                self.assertEqual(params["email"], [secret])
                return Response({"doi": "10.1234/1", "oa_locations": []})
            return Response(epmc_page([epmc(pubYear="2020"), epmc(2, pubYear="")], 2))
        client = Client({"POLITE_EMAIL": secret}, opener, lambda _: None)
        _, doctor = self.invoke(["doctor"], client)
        self.assertFalse(doctor["configuration"]["unpaywall_email"])
        self.assertTrue(doctor["configuration"]["unpaywall_email_effective"])
        self.assertTrue(doctor["configuration"]["crossref_email_effective"])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            code, run = self.invoke(["search", "x", "--sources", "europe-pmc", "--year-from", "2024", "--out", str(root / "search")], client)
            self.assertEqual(code, 0)
            self.assertEqual(run["year_filter"]["locally_removed"], 1)
            self.assertEqual(run["year_filter"]["unknown_year_count"], 1)
            source = root / "papers.json"
            source.write_text(json.dumps([record("mock", "1", "Title", 2024, ids(doi="10.1234/1"), [])]), encoding="utf-8")
            code, run = self.invoke(["oa", str(source), "--out", str(root / "oa")], client)
            self.assertEqual(code, 0)
            self.assertEqual(run["source_status"]["unpaywall:10.1234/1"]["status"], "ok")
            self.assertEqual(check_bundle(root / "oa")["status"], "success")
            self.assertNotIn(secret, "".join(p.read_text(encoding="utf-8") for p in (root / "oa").rglob("*") if p.is_file()))
        self.assertTrue(any("unpaywall" in url for url in seen))


if __name__ == "__main__":
    unittest.main()
