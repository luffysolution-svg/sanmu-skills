import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import webvpn_acquire as wa


class WebVpnAcquireTests(unittest.TestCase):
    def test_publisher_mapping(self):
        self.assertEqual(wa.publisher_of("https://www.sciencedirect.com/science/article/pii/S123"), "elsevier")
        self.assertEqual(wa.publisher_of("https://www.cell.com/matter/fulltext/S123"), "elsevier")
        self.assertEqual(wa.publisher_of("https://onlinelibrary.wiley.com/doi/10.1002/x"), "wiley")
        self.assertEqual(wa.publisher_of("https://www.nature.com/articles/x"), "springer")
        self.assertEqual(wa.publisher_of("https://pubs.acs.org/doi/10.1021/x"), "acs")
        self.assertEqual(wa.publisher_of("https://pubs.rsc.org/en/content/articlelanding/2024/x"), "rsc")
        self.assertEqual(wa.publisher_of("https://ieeexplore.ieee.org/document/123"), "ieee")
        self.assertEqual(wa.publisher_of("https://www.science.org/doi/10.1126/x"), "science")
        self.assertEqual(wa.publisher_of("https://kns.cnki.net/kcms2/article/abstract?v=x"), "cnki")
        self.assertEqual(wa.publisher_of("https://example.org/article"), "direct")
        self.assertEqual(wa.publisher_for_doi("10.1016/j.matt.2019.08.017"), "elsevier")
        self.assertEqual(wa.publisher_for_doi("10.1002/adma.202313513"), "wiley")
        self.assertEqual(wa.publisher_for_doi("10.1038/x"), "springer")
        self.assertEqual(wa.publisher_for_doi("10.1021/jacs.1c1"), "acs")
        self.assertEqual(wa.publisher_for_doi("10.1039/d4cc00001a"), "rsc")
        self.assertEqual(wa.publisher_for_doi("10.1109/tpel.2024.1"), "ieee")
        self.assertEqual(wa.publisher_for_doi("10.1126/science.x"), "science")
        self.assertEqual(wa.publisher_for_doi("10.9999/other"), "direct")

    def test_pdf_candidates_prefers_direct_and_deduplicates(self):
        html = """
        <meta name="citation_pdf_url" content="/article/S123.pdf">
        <a href="/article/S123.pdf">PDF</a>
        <a href="/doi/pdfdirect/10.1000/test">Download PDF</a>
        <a href="/article/S123-supplementary.pdf">Supplementary</a>
        <a href="/about">About</a>
        """
        links = wa.pdf_candidates(html, "https://www-sciencedirect-com-s.vpn.scau.edu.cn/article/S123")
        self.assertEqual(links[0], "https://www-sciencedirect-com-s.vpn.scau.edu.cn/doi/pdfdirect/10.1000/test")
        self.assertEqual(links[1], "https://www-sciencedirect-com-s.vpn.scau.edu.cn/article/S123.pdf")
        self.assertEqual(len(links), 2)

    def test_acs_adapter_and_si_exclusion(self):
        html = '<meta name="citation_pdf_url" content="/doi/pdf/10.1021/jacs.1c1">'
        links = wa.pdf_candidates(html, "https://pubs.acs.org/doi/10.1021/jacs.1c1",
                                  "acs", "10.1021/jacs.1c1")
        self.assertIn("https://pubs.acs.org/doi/pdf/10.1021/jacs.1c1", links)
        si = '<a href="/doi/suppl/10.1021/jacs.1c1/suppl_file/ja1c1_si_001.pdf">SI</a>'
        si_links = wa.pdf_candidates(si, "https://pubs.acs.org/doi/10.1021/jacs.1c1",
                                     "acs", "10.1021/jacs.1c1")
        self.assertFalse(any("suppl_file" in link for link in si_links))
        self.assertIn("https://pubs.acs.org/doi/pdf/10.1021/jacs.1c1", si_links)

    def test_rsc_ieee_science_and_cnki_candidates(self):
        rsc = '<a href="/en/content/articlepdf/2024/d4cc00001a">PDF</a>'
        self.assertTrue(wa.pdf_candidates(rsc, "https://pubs.rsc.org/en/content/articlelanding/2024/d4cc00001a",
                                          "rsc", "10.1039/d4cc00001a"))
        ieee = '<div data-arnumber="987654"></div><iframe src="/stamp/stamp.jsp?tp=&arnumber=987654"></iframe>'
        links = wa.pdf_candidates(ieee, "https://ieeexplore.ieee.org/document/987654",
                                  "ieee", "10.1109/tpel.2024.1")
        self.assertIn("https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=987654", links)
        science = '<a href="/doi/epdf/10.1126/science.x">ePDF</a>'
        self.assertIn("https://www.science.org/doi/epdf/10.1126/science.x",
                      wa.pdf_candidates(science, "https://www.science.org/doi/10.1126/science.x",
                                        "science", "10.1126/science.x"))
        cnki = '<a href="https://download.cnki.net/kcms/detail/detail.aspx?filename=ABC.pdf">PDF</a>'
        self.assertTrue(wa.pdf_candidates(cnki, "https://kns.cnki.net/kcms2/article/abstract?v=x",
                                          "cnki"))

    def test_institutional_login_detection(self):
        self.assertTrue(wa._institutional_login_page(
            "<a>Access through your institution</a>"))
        self.assertTrue(wa._institutional_login_page("<div>CARSI login</div>"))
        self.assertFalse(wa._institutional_login_page("<h1>Article</h1>"))

    def test_wait_for_manual_clearance(self):
        class Page:
            def __init__(self, pages):
                self.pages = iter(pages)
                self.url = "https://publisher.example/article"

            def is_closed(self):
                return False

            def bring_to_front(self):
                pass

            def wait_for_timeout(self, milliseconds):
                pass

            def content(self):
                return next(self.pages)

        cleared = wa.wait_for_manual_clearance(
            Page(["<html>verify you are human</html>", "<html>article</html>"]), 10, poll=0)
        self.assertEqual(cleared["status"], "cleared")

        timed_out = wa.wait_for_manual_clearance(
            Page(["<html>verify you are human</html>"] * 20), 1, poll=0)
        self.assertEqual(timed_out["status"], "timeout")

    def test_host_limiter_paces_and_stops_after_blocks(self):
        clock = [0.0]
        slept = []
        limiter = wa.HostLimiter(interval=10, jitter=0, max_per_host=2, stop_after=2,
                                 clock=lambda: clock[0], sleep=slept.append)
        limiter.wait("a")
        limiter.note("a")
        clock[0] += 1
        limiter.wait("a")
        self.assertAlmostEqual(slept[0], 9.0)
        limiter.note("a", 403)
        limiter.note("a", 429)
        self.assertTrue(limiter.blocked("a"))
        self.assertFalse(limiter.allowed("a"))

    def test_resolve_target_prefers_publisher_url(self):
        self.assertEqual(wa.resolve_target({"data": {
            "url": "https://www.nature.com/articles/x", "DOI": "10.1038/x"}}),
            "https://www.nature.com/articles/x")
        self.assertEqual(wa.resolve_target({"data": {
            "url": "https://doi.org/10.1038/x", "DOI": "10.1038/x"}}),
            "https://doi.org/10.1038/x")
        self.assertEqual(wa.resolve_target({"data": {"DOI": "10.1038/x"}}),
                         "https://doi.org/10.1038/x")
        self.assertIsNone(wa.resolve_target({"data": {}}))

    def test_has_main_pdf_ignores_supplementary_only(self):
        class Client:
            def __init__(self, children):
                self._children = children

            def children(self, key, library="user"):
                return {"items": self._children}

            def file(self, key, library="user"):
                return {"status": "available"}

        si = {"data": {"key": "SI1", "itemType": "attachment", "contentType": "application/pdf",
                       "title": "Supplementary Information"}}
        main = {"data": {"key": "PDF1", "itemType": "attachment", "contentType": "application/pdf",
                         "title": "[PDF] Full text"}}
        self.assertFalse(wa.has_main_pdf(Client([si]), "KEY"))
        self.assertTrue(wa.has_main_pdf(Client([si, main]), "KEY"))

    def test_manifest_shape_is_ingest_compatible(self):
        item = {"data": {
            "title": "A paper", "DOI": "10.1038/x", "date": "2025-03",
            "creators": [{"firstName": "Ada", "lastName": "Lovelace"}]}}
        manifest = wa._manifest(item, "paper.pdf", "a" * 64, {"header": True, "trailer": True},
                                {"status": "matched"}, "https://www.nature.com/articles/x")
        self.assertEqual(manifest["schema"], "sanmu-acquisition/v1")
        self.assertEqual(manifest["paper"]["title"], "A paper")
        self.assertEqual(manifest["paper"]["identifiers"]["doi"], ["10.1038/x"])
        self.assertEqual(manifest["paper"]["authors"], [{"name": "Ada Lovelace"}])
        self.assertEqual(manifest["main"]["file"], "paper.pdf")
        self.assertEqual(manifest["main"]["sha256"], "a" * 64)


if __name__ == "__main__":
    unittest.main()
