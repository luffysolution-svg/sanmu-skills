from email.utils import formatdate
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rate_ledger


class Clock:
    def __init__(self, value=1_700_000_000.0):
        self.value = value

    def __call__(self):
        return self.value


class RateLedgerTests(unittest.TestCase):
    def test_window_limit_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            clock = Clock()
            ledger = rate_ledger.RateLedger(path, clock)
            policy = {"min_interval": 0, "window_seconds": 60,
                      "window_limit": 2, "daily_limit": 10}
            self.assertTrue(ledger.reserve("wiley", policy)["allowed"])
            self.assertTrue(ledger.reserve("wiley", policy)["allowed"])
            blocked = ledger.reserve("wiley", policy)
            self.assertFalse(blocked["allowed"])
            self.assertEqual(blocked["reason"], "window_limit")
            clock.value += 61
            self.assertTrue(ledger.reserve("wiley", policy)["allowed"])
            fresh = rate_ledger.RateLedger(path, clock)
            snapshot = fresh.snapshot(["wiley"], {"wiley": policy})["wiley"]
            self.assertEqual(snapshot["window_count"], 1)
            self.assertEqual(snapshot["day_count"], 3)

    def test_daily_limit_resets_on_next_utc_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = Clock()
            ledger = rate_ledger.RateLedger(Path(tmp) / "state.json", clock)
            policy = {"min_interval": 0, "window_seconds": 0,
                      "window_limit": 0, "daily_limit": 1}
            self.assertTrue(ledger.reserve("elsevier", policy)["allowed"])
            blocked = ledger.reserve("elsevier", policy)
            self.assertFalse(blocked["allowed"])
            self.assertEqual(blocked["reason"], "daily_limit")
            clock.value += 24 * 3600
            self.assertTrue(ledger.reserve("elsevier", policy)["allowed"])

    def test_cooldown_retry_after_and_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = Clock()
            ledger = rate_ledger.RateLedger(Path(tmp) / "state.json", clock)
            policy = {"min_interval": 0, "window_seconds": 600,
                      "window_limit": 0, "daily_limit": 10}
            ledger.note("elsevier", "rate_limited", retry_after="120")
            blocked = ledger.reserve("elsevier", policy)
            self.assertFalse(blocked["allowed"])
            self.assertEqual(blocked["reason"], "cooldown")
            self.assertAlmostEqual(blocked["wait_seconds"], 120.0, places=1)
            clock.value += 121
            self.assertTrue(ledger.reserve("elsevier", policy)["allowed"])
            ledger.note("elsevier", "publisher_challenge", cooldown_seconds=60)
            self.assertEqual(ledger.check("elsevier", policy)["reason"], "cooldown")
            ledger.clear("elsevier")
            self.assertTrue(ledger.check("elsevier", policy)["allowed"])

    def test_http_date_retry_after(self):
        value = formatdate(time.time() + 90, usegmt=True)
        seconds = rate_ledger.parse_retry_after(value)
        self.assertIsNotNone(seconds)
        self.assertGreaterEqual(seconds, 80)
        self.assertLessEqual(seconds, 95)
        self.assertEqual(rate_ledger.parse_retry_after("not-a-date"), None)

    def test_ledger_contains_no_doi_url_or_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            ledger = rate_ledger.RateLedger(path, Clock())
            ledger.reserve("api:elsevier", {"min_interval": 0, "daily_limit": 5})
            text = path.read_text(encoding="utf-8").lower()
            for forbidden in ("10.1016/", "api_key", "cookie", "http://", "https://"):
                self.assertNotIn(forbidden, text)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema"],
                             "sanmu-rate-ledger/v1")

    def test_disabled_ledger_never_crashes_or_writes(self):
        ledger = rate_ledger.RateLedger(None, Clock())
        self.assertTrue(ledger.reserve("elsevier", {"min_interval": 0})["allowed"])
        ledger.note("elsevier", 429, retry_after="10")
        self.assertEqual(ledger.check("elsevier", {"min_interval": 0})["reason"], "cooldown")
        ledger.clear("elsevier")


if __name__ == "__main__":
    unittest.main()
