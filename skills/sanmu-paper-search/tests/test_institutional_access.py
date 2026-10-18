import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import institutional_access as ia


class InstitutionalAccessTests(unittest.TestCase):
    def test_detect_proxy_type(self):
        self.assertEqual(ia.detect_proxy_type("https://webvpn.univ.edu.cn/https/"), "webvpn_path")
        self.assertEqual(ia.detect_proxy_type("https://webvpn.sysu.edu.cn/"), "webvpn_path")
        self.assertEqual(ia.detect_proxy_type("https://libproxy.univ.edu/login?url="), "ezproxy_prefix")
        self.assertEqual(ia.detect_proxy_type("https://ezproxy.univ.edu/url="), "ezproxy_prefix")
        self.assertEqual(ia.detect_proxy_type("https://vpn.univ.edu.cn/{url}"), "template")
        self.assertEqual(ia.detect_proxy_type("https://vpn.univ.edu.cn/https/{host}{path}"), "template")
        self.assertEqual(ia.detect_proxy_type("https://lib.univ.edu/proxy"), "ezproxy_prefix")
        self.assertEqual(ia.detect_proxy_type(""), "none")

    def test_ezproxy_transform(self):
        target = "https://www.nature.com/articles/s41586-023-06792-0"
        proxy = "https://libproxy.univ.edu/login?url="
        res = ia.transform_url(target, proxy)
        self.assertEqual(res["status"], "configured")
        self.assertEqual(res["proxy_type"], "ezproxy_prefix")
        self.assertEqual(res["institutional_url"], "https://libproxy.univ.edu/login?url=https://www.nature.com/articles/s41586-023-06792-0")

        # Test bare base URL
        res2 = ia.transform_url(target, "https://libproxy.univ.edu")
        self.assertEqual(res2["institutional_url"], "https://libproxy.univ.edu/login?url=https://www.nature.com/articles/s41586-023-06792-0")

    def test_webvpn_transform(self):
        target = "https://www.science.org/doi/10.1126/science.aat2663?param=1"
        proxy = "https://webvpn.univ.edu.cn"
        res = ia.transform_url(target, proxy)
        self.assertEqual(res["status"], "configured")
        self.assertEqual(res["proxy_type"], "webvpn_path")
        self.assertEqual(res["institutional_url"], "https://webvpn.univ.edu.cn/https/www.science.org/doi/10.1126/science.aat2663?param=1")

    def test_template_transform(self):
        target = "https://www.sciencedirect.com/science/article/pii/S123"
        template = "https://vpn.univ.edu.cn/http/80/{host}{path}?doi={doi}"
        res = ia.transform_url(target, template, doi="10.1016/j.test")
        self.assertEqual(res["status"], "configured")
        self.assertEqual(res["proxy_type"], "template")
        self.assertEqual(res["institutional_url"], "https://vpn.univ.edu.cn/http/80/www.sciencedirect.com/science/article/pii/S123?doi=10.1016/j.test")

    def test_sensitive_param_redacted(self):
        target = "https://publisher.org/a.pdf?token=SECRET&key=KEY123&keep=yes"
        proxy = "https://libproxy.univ.edu/login?url="
        res = ia.transform_url(target, proxy)
        self.assertNotIn("SECRET", res["institutional_url"])
        self.assertNotIn("KEY123", res["institutional_url"])
        self.assertIn("keep=yes", res["institutional_url"])

    def test_invalid_target_and_disabled(self):
        self.assertEqual(ia.transform_url("javascript:alert(1)", "https://proxy")["status"], "invalid_target_url")
        self.assertEqual(ia.transform_url("https://example.com", "")["status"], "disabled")
        self.assertEqual(ia.transform_url("https://example.com", None)["status"], "disabled")


if __name__ == "__main__":
    unittest.main()
