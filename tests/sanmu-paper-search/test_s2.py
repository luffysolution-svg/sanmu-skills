"""Offline S2 schema, retry and process-shared rate-limit boundary tests."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "sanmu-paper-search" / "scripts"
sys.path.insert(0, str(SCRIPTS))
from _providers import Client, Providers, SourceError, normalize
from _rate_limit import RateLimiter


class Response:
    status = 200
    headers = {}

    def __init__(self, data=None, payload=None):
        self.payload = json.dumps(data).encode() if payload is None else payload

    def read(self, size=-1):
        return self.payload if size < 0 else self.payload[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def paper(number=1, **extra):
    return {"paperId": str(number), "title": str(number), "year": 2025, **extra}


class S2Tests(unittest.TestCase):
    def test_fields_years_and_page_size(self):
        calls = []
        def opener(request, timeout):
            path = urlsplit(request.full_url).path
            params = parse_qs(urlsplit(request.full_url).query)
            calls.append((path, params))
            if "/recommendations/" in path:
                return Response({"recommendedPapers": [paper()]})
            if path.endswith("/references"):
                return Response({"data": [{"citedPaper": paper()}]})
            if path.endswith("/citations"):
                return Response({"data": [{"citingPaper": paper()}]})
            if path.endswith("/search") or path.endswith("/papers"):
                return Response({"data": [paper(n) for n in range(20)], "total": 20})
            return Response(paper())
        providers = Providers(Client({}, opener, lambda _: None))
        for low, high, expected in ((2020, 2025, "2020-2025"), (2020, None, "2020-"), (None, 2025, "-2025")):
            papers, status = providers.search("semantic-scholar", "test", 20, low, high)
            self.assertEqual(len(papers), 20)
            self.assertEqual(status["pages"], 1)
            self.assertEqual(calls[-1][1]["year"], [expected])
            self.assertEqual(calls[-1][1]["limit"], ["20"])
            self.assertIn("tldr", calls[-1][1]["fields"][0].split(","))
        providers.lookup("semantic-scholar", "s2:1")
        self.assertIn("tldr", calls[-1][1]["fields"][0].split(","))
        for low, high, expected in ((2020, 2025, "2020:2025"), (2020, None, "2020:"), (None, 2025, ":2025")):
            providers.authors("semantic-scholar", None, "42", 20, low, high)
            self.assertEqual(calls[-1][1]["publicationDateOrYear"], [expected])
            self.assertNotIn("year", calls[-1][1])
            self.assertNotIn("tldr", calls[-1][1]["fields"][0].split(","))
        for relation in ("references", "citations", "recommendations"):
            providers.related("semantic-scholar", "s2:1", relation, 20)
            fields = calls[-1][1]["fields"][0].split(",")
            self.assertNotIn("tldr", fields)
            self.assertTrue({"citationCount", "referenceCount", "venue", "journal", "publicationDate", "url"} <= set(fields))

    def test_metadata_and_author_candidates_preserve_zero_and_null(self):
        metadata = {"url": "https://example.org/paper", "venue": "Nature", "journal": {"name": "Nature"},
                    "citationCount": 0, "referenceCount": 3, "publicationDate": "2025-01-02",
                    "tldr": {"model": "test", "text": "Summary"}}
        normalized = normalize("semantic-scholar", paper(**metadata))
        for local, remote in (("url", "url"), ("venue", "venue"), ("journal", "journal"),
                              ("citation_count", "citationCount"), ("reference_count", "referenceCount"),
                              ("publication_date", "publicationDate"), ("tldr", "tldr")):
            self.assertEqual(normalized[local], metadata[remote])
        self.assertIsNone(normalize("semantic-scholar", paper(tldr=None))["tldr"])
        client = Client({}, lambda *a, **kw: Response({"data": [{"authorId": "42", "name": "Author", "citationCount": 0, "url": "https://example.org/author"}]}))
        _, _, candidates = Providers(client).authors("semantic-scholar", "Author", None, 20)
        self.assertEqual(candidates[0]["citation_count"], 0)
        self.assertEqual(candidates[0]["url"], "https://example.org/author")

    def test_publisher_restriction_and_partial_page(self):
        calls = []
        def opener(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                return Response({"data": [{"citedPaper": paper()}], "next": 1})
            return Response({"data": None, "citingPaperInfo": {"openAccessPdf": {
                "url": "", "status": None, "license": None,
                "disclaimer": "The following fields have been elided by the publisher: {'references'}"}}})
        with self.assertRaises(SourceError) as raised:
            Providers(Client({}, opener, lambda _: None)).related("semantic-scholar", "s2:1", "references", 20)
        self.assertEqual(raised.exception.kind, "publisher_restricted")
        self.assertIn("publisher", str(raised.exception))
        self.assertEqual(raised.exception.pages, 1)
        self.assertEqual(len(raised.exception.papers), 1)
        for payload in ({"data": None}, {"data": None, "citingPaperInfo": {"disclaimer": "Unavailable"}},
                        {"data": None, "citingPaperInfo": None},
                        {"data": None, "citingPaperInfo": {"openAccessPdf": None}},
                        {"data": None, "citingPaperInfo": {"openAccessPdf": {"disclaimer": "Unavailable"}}}):
            with self.subTest(payload=payload), self.assertRaises(SourceError) as error:
                Providers(Client({}, lambda *a, **kw: Response(payload))).related("semantic-scholar", "s2:1", "references", 20)
            self.assertNotEqual(error.exception.kind, "publisher_restricted")
        papers, _ = Providers(Client({}, lambda *a, **kw: Response({"data": []}))).related("semantic-scholar", "s2:1", "references", 20)
        self.assertEqual(papers, [])

    def test_retry_after_numeric_date_invalid_and_unpaywall(self):
        date = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=3))
        for source, retry in (("semantic-scholar", "2"), ("unpaywall", date), ("semantic-scholar", "bad"), ("semantic-scholar", "nan")):
            calls, sleeps = [], []
            def opener(request, timeout):
                calls.append(request)
                if len(calls) == 1:
                    raise HTTPError(request.full_url, 429, "slow", {"rEtRy-AfTeR": retry}, io.BytesIO(b'{}'))
                return Response({"ok": True})
            with self.subTest(source=source, retry=retry), patch("_providers.random.uniform", return_value=0):
                client = Client({"UNPAYWALL_EMAIL": "test@example.invalid"}, opener, sleeps.append)
                self.assertTrue(client.get(source, "/test")["ok"])
                self.assertEqual(len(calls), 2)
                self.assertLessEqual(sum(sleeps), 10)
                self.assertEqual([row["status"] for row in client.raw], [429, 200])
                self.assertEqual(len(sleeps), 1)
        self.assertEqual(Client.retry_after({"Retry-After": "-3"}), 0)
        self.assertEqual(Client.retry_after({"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}), 0)

    def test_retryable_statuses_and_permanent_network_failure(self):
        for status in (429, 502, 503, 504, 400, 401, 404, 500):
            calls, sleeps = [], []
            def opener(request, timeout):
                calls.append(request)
                raise HTTPError(request.full_url, status, "error", {}, io.BytesIO(b'{}'))
            with self.subTest(status=status), patch("_providers.random.uniform", return_value=0), self.assertRaises(SourceError) as raised:
                Client({}, opener, sleeps.append).get("semantic-scholar", "/test")
            self.assertEqual(raised.exception.status, status)
            self.assertEqual(len(calls), 3 if status in (429, 502, 503, 504) else 1)
            self.assertLessEqual(sum(sleeps), 10)
        calls = []
        def disconnected(*a, **kw):
            calls.append(1)
            raise URLError("offline")
        client = Client({}, disconnected)
        with self.assertRaisesRegex(SourceError, "Network error"):
            client.get("semantic-scholar", "/test")
        self.assertEqual(len(calls), 1)
        self.assertIsNone(client.raw[0]["status"])

    def test_final_429_and_excess_budget_persist_cooldown(self):
        for retry, expected_calls in (("30", 1), ("6", 2)):
            with self.subTest(retry=retry), tempfile.TemporaryDirectory() as directory:
                stamp, calls = [100.0], []
                def sleep(seconds):
                    stamp[0] += seconds
                def opener(request, timeout):
                    calls.append(1)
                    raise HTTPError(request.full_url, 429, "slow", {"Retry-After": retry}, io.BytesIO(b'{}'))
                limiter = RateLimiter(directory, clock=lambda: stamp[0], sleep=sleep)
                with self.assertRaisesRegex(SourceError, "10 second"):
                    Client({}, opener, sleep, rate_limiter=limiter).get("semantic-scholar", "/test")
                self.assertEqual(len(calls), expected_calls)
                other = Client({}, lambda *a, **kw: self.fail("Cooldown ignored"), sleep,
                               rate_limiter=RateLimiter(directory, clock=lambda: stamp[0], sleep=sleep))
                with self.assertRaises(SourceError):
                    other.wait("semantic-scholar", max_wait=0)
        sleeps = []
        client = Client({}, lambda request, **kw: (_ for _ in ()).throw(HTTPError(request.full_url, 429, "slow", {"Retry-After": "1"}, io.BytesIO(b'{}'))), sleeps.append)
        with self.assertRaises(SourceError):
            client.get("semantic-scholar", "/test")
        with self.assertRaises(SourceError):
            client.wait("semantic-scholar", max_wait=0)

    def test_shared_across_clients_keys_hashes_and_injection_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            stamp, sleeps = [100.0], []
            def sleep(seconds):
                sleeps.append(seconds)
                stamp[0] += seconds
            def client():
                return Client({}, lambda *a, **kw: Response({}), sleep,
                              rate_limiter=RateLimiter(directory, clock=lambda: stamp[0], sleep=sleep))
            first, second = client(), client()
            first.wait("semantic-scholar", "fake-key")
            second.wait("semantic-scholar", "fake-key")
            self.assertAlmostEqual(sleeps[0], 1.05)
            second.wait("semantic-scholar", "different-key")
            self.assertEqual(len(sleeps), 1)
            first.cooldown("semantic-scholar", "fake-key", 30)
            with self.assertRaises(SourceError):
                second.wait("semantic-scholar", "fake-key")
            for path in Path(directory).iterdir():
                self.assertNotIn("fake-key", path.name + path.read_text())
            self.assertFalse(Client({}, lambda *a, **kw: Response({})).rate_limiter.shared)
            self.assertFalse(Client({}, sleep=lambda _: None).rate_limiter.shared)
            self.assertTrue(Client({}).rate_limiter.shared)

    def test_real_process_lock_reserves_unique_start_times(self):
        with tempfile.TemporaryDirectory() as directory:
            code = ("import sys,time;sys.path.insert(0,sys.argv[1]);from _rate_limit import RateLimiter;"
                    "r=RateLimiter(sys.argv[2]);r.wait('semantic-scholar','fake-process-key');print(time.time(),flush=True)")
            processes = [subprocess.Popen([sys.executable, "-c", code, str(SCRIPTS), directory], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(3)]
            stamps = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, stderr)
                stamps.append(float(stdout.strip()))
            stamps.sort()
            self.assertTrue(all(b - a >= 1.02 for a, b in zip(stamps, stamps[1:])), stamps)

    def test_process_lock_wait_budget_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            limiter = RateLimiter(directory)
            code = ("import sys;sys.path.insert(0,sys.argv[1]);from _rate_limit import RateLimiter,RateLimitError;"
                    "r=RateLimiter(sys.argv[2]);\ntry:r.wait('test','fake-lock-key',max_wait=0)\n"
                    "except RateLimitError:sys.exit(0)\nelse:sys.exit(1)")
            with limiter._state("test", "fake-lock-key"):
                result = subprocess.run([sys.executable, "-c", code, str(SCRIPTS), directory],
                                        capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_corrupt_state_and_response_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            limiter = RateLimiter(directory)
            limiter.wait("test", "fake-key", interval=0)
            next(Path(directory).iterdir()).write_text('{"next": "bad"}')
            with self.assertRaisesRegex(SourceError, "Invalid shared"):
                Client({}, opener=lambda *a, **kw: Response({}), rate_limiter=limiter).wait("test", "fake-key")
        for payload, message in ((b'[]', "Invalid JSON"), (b'broken', "Invalid JSON"), (b'\xff', "Network error"), (b'x' * (16 * 1024 * 1024 + 1), "16 MiB")):
            with self.subTest(message=message), self.assertRaisesRegex(SourceError, message):
                Client({}, lambda *a, **kw: Response(payload=payload)).get("semantic-scholar", "/test")


if __name__ == "__main__":
    unittest.main()
