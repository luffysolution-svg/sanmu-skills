import argparse
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import webvpn_acquire as wa


class FakeClient:
    def get(self, key, library="user"):
        return {"data": {"title": "A paper", "DOI": "10.1016/j.test"}}

    def children(self, key, library="user"):
        return {"items": []}


class FakeApi:
    def provider_for_doi(self, doi):
        return "elsevier"

    def configured(self, provider):
        return True

    def policy_for(self, provider, args):
        return {"min_interval": 0, "daily_limit": 10}

    def _ledger_for(self, args):
        return wa.rate_ledger.RateLedger(None)

    def _fetch_one(self, client, item, key, out, args, ledger=None):
        return {"key": key, "title": "A paper", "doi": "10.1016/j.test",
                "api_provider": "elsevier", "backend": "api",
                "status": "file_obtained", "file": "paper.pdf"}


class DeniedApi(FakeApi):
    def _fetch_one(self, client, item, key, out, args, ledger=None):
        return {"key": key, "title": "A paper", "doi": "10.1016/j.test",
                "api_provider": "elsevier", "backend": "api",
                "status": "failed", "reason": "access_denied"}


class UnsupportedApi(FakeApi):
    def provider_for_doi(self, doi):
        return "unsupported"


def args_for(out):
    return argparse.Namespace(
        keys="KEY", keys_file=None, out=out, library="user",
        base_url="http://127.0.0.1:23119", backend="auto",
        delay=15.0, jitter=0.0, api_delay=None, max_per_host=20,
        stop_after=2, max_total=50, daily_per_host=60, daily_limit=0,
        state=None, no_state=True, max_items=1000, max_pages=10,
        headless=True, dry_run=False, apply=False, force=False,
        identity_confirmed=False, profile="unused",
        portal=wa.DEFAULT_PORTAL, keep_open=False,
        manual_cleared=False,
        challenge_timeout=1, challenge_cooldown=1,
    )


class UnifiedAcquisitionTests(unittest.TestCase):
    def test_api_success_skips_browser_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(wa, "ZoteroLocal", return_value=FakeClient()):
                with patch.object(wa, "_load_publisher_api", return_value=FakeApi()):
                    with patch.object(wa, "_launch",
                                      side_effect=AssertionError("browser must not launch")):
                        code = wa.run_fetch(args_for(tmp))
            self.assertEqual(code, 0)
            self.assertTrue((Path(tmp) / "results.json").is_file())

    def test_api_access_denied_is_not_retried_in_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(wa, "ZoteroLocal", return_value=FakeClient()):
                with patch.object(wa, "_load_publisher_api", return_value=DeniedApi()):
                    with patch.object(wa, "_launch",
                                      side_effect=AssertionError("browser must not launch")):
                        code = wa.run_fetch(args_for(tmp))
            self.assertEqual(code, 1)

    def test_dry_run_does_not_launch_browser(self):
        args = args_for("unused")
        args.dry_run = True
        with tempfile.TemporaryDirectory() as tmp:
            args.out = tmp
            with patch.object(wa, "ZoteroLocal", return_value=FakeClient()):
                with patch.object(wa, "_load_publisher_api", return_value=FakeApi()):
                    with patch.object(wa, "_launch",
                                      side_effect=AssertionError("browser must not launch")):
                        code = wa.run_fetch(args)
            self.assertEqual(code, 0)

    def test_unsupported_api_falls_back_to_browser(self):
        class FakePage:
            url = "https://doi.org/10.1016/j.test"

        class FakeContext:
            def close(self):
                pass

        class FakePlaywright:
            def stop(self):
                pass

        args = args_for("unused")
        args.backend = "auto"
        with tempfile.TemporaryDirectory() as tmp:
            args.out = tmp
            with patch.object(wa, "ZoteroLocal", return_value=FakeClient()):
                with patch.object(wa, "_load_publisher_api",
                                  return_value=UnsupportedApi()):
                    with patch.object(wa, "_launch",
                                      return_value=(FakePlaywright(), FakeContext(),
                                                    FakePage())):
                        with patch.object(wa, "fetch_item",
                                          return_value={"key": "KEY",
                                                        "status": "file_obtained",
                                                        "file": "paper.pdf"}) as fetch:
                            code = wa.run_fetch(args)
            self.assertEqual(code, 0)
            self.assertEqual(fetch.call_count, 1)

    def test_manual_cleared_resets_host_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = wa.rate_ledger.RateLedger(Path(tmp) / "state.json")
            policy = {"min_interval": 0, "daily_limit": 10}
            ledger.note("webvpn:doi.org", "publisher_challenge",
                        cooldown_seconds=3600)
            self.assertEqual(ledger.check("webvpn:doi.org", policy)["reason"],
                             "cooldown")
            wa._clear_batch_cooldowns(
                [{"data": {"DOI": "10.1016/j.test"}}], ledger)
            self.assertTrue(ledger.check("webvpn:doi.org", policy)["allowed"])
            ledger.note("webvpn:doi.org", "rate_limited", retry_after="120")
            wa._clear_batch_cooldowns(
                [{"data": {"DOI": "10.1016/j.test"}}], ledger)
            self.assertEqual(ledger.check("webvpn:doi.org", policy)["reason"],
                             "cooldown")

    def test_keep_open_retries_current_item_after_clearance(self):
        class FakePage:
            url = "https://publisher.example/article"

            def is_closed(self):
                return False

            def bring_to_front(self):
                pass

            def wait_for_timeout(self, milliseconds):
                pass

            def content(self):
                return "<html>article</html>"

        class FakeContext:
            def close(self):
                pass

        class FakePlaywright:
            def stop(self):
                pass

        args = args_for("unused")
        args.backend = "browser"
        args.keep_open = True
        args.headless = False
        first = {"key": "KEY", "status": "needs_manual",
                 "reason": "publisher_challenge",
                 "manual_url": "https://publisher.example/article"}
        second = {"key": "KEY", "status": "file_obtained", "file": "paper.pdf"}
        with tempfile.TemporaryDirectory() as tmp:
            args.out = tmp
            with patch.object(wa, "ZoteroLocal", return_value=FakeClient()):
                with patch.object(wa, "_launch",
                                  return_value=(FakePlaywright(), FakeContext(), FakePage())):
                    with patch.object(wa, "fetch_item", side_effect=[first, second]) as fetch:
                        with patch.object(wa, "wait_for_manual_clearance",
                                          return_value={"status": "cleared"}):
                            code = wa.run_fetch(args)
            self.assertEqual(code, 0)
            self.assertEqual(fetch.call_count, 2)


if __name__ == "__main__":
    unittest.main()
