"""Exa contracts use local fixtures, including billed errors and ambiguous content."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "sanmu-paper-search" / "scripts"))
from _core import check_bundle
from _providers import Client
from paper_search import main
from _exa import publications


def entry(title="Hydrogen paper", doi="10.1234/a"):
    return {"id": "https://exa.ai/library/publication/a", "title": title, "url": "https://doi.org/" + doi,
            "text": "Text mentions reference DOI 10.1234/not-this-paper",
            "entities": [{"type": "publication", "id": "pub-a", "properties": {
                "title": title, "doi": doi, "year": 2025, "authors": [{"name": "A Name", "id": None}],
                "citationCount": 0, "referenceCount": 20, "abstract": None}}]}


class Response:
    status = 200
    headers = {}
    def __init__(self, data): self.body = json.dumps(data).encode()
    def read(self, size): return self.body[:size]
    def __enter__(self): return self
    def __exit__(self, *args): pass


class ExaTests(unittest.TestCase):
    def invoke(self, argv, client):
        out = io.StringIO()
        with contextlib.redirect_stdout(out): code = main(argv, client)
        return code, json.loads(out.getvalue())

    def test_search_contract_identity_costs_redaction_and_integrity(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            self.assertEqual(request.full_url, "https://api.exa.ai/search")
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.get_header("X-api-key"), "EXA_PRIVATE_FIXTURE")
            payload = json.loads(request.data)
            self.assertEqual(payload["category"], "publication")
            self.assertEqual(payload["startPublishedDate"], "2024-01-01T00:00:00.000Z")
            self.assertEqual(payload["includeDomains"], ["doi.org"])
            self.assertEqual(payload["contents"]["text"]["maxCharacters"], 3000)
            return Response({"results": [entry(), {"title": "Web only", "url": "https://example.org"}],
                             "costDollars": {"total": .007}, "debug": "EXA_PRIVATE_FIXTURE"})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "run"
            code, run = self.invoke(["exa", "光催化 hydrogen", "--year-from", "2024", "--include-domain", "doi.org", "--out", str(root)],
                                    Client({"EXA_API_KEY": " EXA_PRIVATE_FIXTURE "}, opener, lambda _: None))
            self.assertEqual(code, 0, run)
            self.assertEqual(run["cost_usd_reported"], .007)
            self.assertEqual(run["cost_unknown_responses"], 0)
            self.assertEqual(run["result_count"], 2)
            self.assertEqual(run["publication_count"], 1)
            paper = json.loads((root / "papers.json").read_text(encoding="utf-8"))[0]
            self.assertEqual(paper["identifiers"]["doi"], ["10.1234/a"])
            self.assertEqual(paper["citation_count"], 0)
            self.assertIsNone(paper["abstract"])
            self.assertFalse(paper["read"])
            self.assertEqual(paper["identity_status"], "discovered")
            self.assertEqual(check_bundle(root)["status"], "success")
            self.assertNotIn("EXA_PRIVATE_FIXTURE", "".join(p.read_text(encoding="utf-8") for p in root.rglob("*") if p.is_file()))
            (root / "results.json").unlink()
            self.assertEqual(check_bundle(root)["status"], "failed")
            count = len(calls)
            code, _ = self.invoke(["exa", "again", "--out", str(root)], Client({"EXA_API_KEY": "dummy"}, opener))
            self.assertEqual(code, 2)
            self.assertEqual(len(calls), count)

    def test_entity_title_and_reference_doi_are_not_current_identity(self):
        row = entry()
        row["entities"][0]["properties"]["title"] = "Different reference paper"
        self.assertEqual(publications([row]), [])
        row = entry()
        row["entities"][0]["properties"]["doi"] = None
        paper = publications([row])[0]
        self.assertEqual(paper["identifiers"], {})
        self.assertIsNone(paper["abstract"])

    def test_invalid_inputs_fail_before_output_or_request(self):
        cases = [([], {}), (["--year-from", "2026", "--year-to", "2024"], {"EXA_API_KEY": "dummy"}),
                 (["--text-chars", "10001"], {"EXA_API_KEY": "dummy"}),
                 (["--include-domain", "https://example.org"], {"EXA_API_KEY": "dummy"}),
                 (["--type", "deep"], {"EXA_API_KEY": "dummy"})]
        with tempfile.TemporaryDirectory() as tmp:
            for i, (flags, env) in enumerate(cases):
                out = Path(tmp) / str(i)
                def opener(*args, **kwargs): self.fail("Unexpected request")
                code, _ = self.invoke(["exa", "query", *flags, "--out", str(out)], Client(env, opener))
                self.assertEqual(code, 2)
                self.assertFalse(out.exists())

    def test_retry_errors_unknown_cost_and_402_stop(self):
        for status, expected_calls in ((429, 3), (503, 3), (402, 1), (401, 1)):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                calls = []
                def opener(request, timeout):
                    calls.append(request)
                    raise HTTPError(request.full_url, status, "fixture", {"Retry-After": "0"},
                                    io.BytesIO(b'{"error":"dummy-secret", "costDollars":{"total":0.001}}'))
                code, run = self.invoke(["exa", "q", "--out", str(Path(tmp)/"run")],
                                        Client({"EXA_API_KEY": "dummy-secret"}, opener, lambda _: None))
                self.assertEqual(code, 3)
                self.assertEqual(len(calls), expected_calls)
                self.assertEqual(run["request_attempts"], expected_calls)
                self.assertAlmostEqual(run["cost_usd_reported"], .001 * expected_calls)
                self.assertEqual(run["cost_unknown_responses"], 0)
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            def opener(request, timeout):
                calls.append(request)
                raise URLError("connection reset")
            code, run = self.invoke(["exa", "q", "--out", str(Path(tmp)/"run")], Client({"EXA_API_KEY":"dummy"}, opener))
            self.assertEqual(code, 3)
            self.assertEqual(len(calls), 1)
            self.assertEqual(run["cost_unknown_responses"], 1)

    def test_malformed_results_and_nonfinite_cost(self):
        bodies = [{"results": {}}, {"results": [None]}, {"results": [{"title": "x"}]},
                  {"results": [], "costDollars": {"total": -1}},
                  {"results": [], "costDollars": {"total": float("inf")}}]
        with tempfile.TemporaryDirectory() as tmp:
            for i, body in enumerate(bodies):
                code, run = self.invoke(["exa", "q", "--out", str(Path(tmp)/str(i))],
                                        Client({"EXA_API_KEY":"dummy"}, lambda request, timeout: Response(body)))
                self.assertEqual(code, 3, run)
                self.assertEqual(check_bundle(Path(tmp)/str(i))["status"], "success")


if __name__ == "__main__": unittest.main()
