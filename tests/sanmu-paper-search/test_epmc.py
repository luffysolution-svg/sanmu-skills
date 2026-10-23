"""Europe PMC contracts: search, filters, retraction relations, XML & SI fetch, and integrity."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "sanmu-paper-search" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from _core import check_bundle
from _providers import Client, parse_id
from paper_search import main
from _epmc import build_query, normalize_epmc, parse_xml_summary, safe_extract_zip


def epmc_entry(doi="10.1002/adma.202313513", pmid="38639018", pmcid="PMC12710600",
               title="Single-atom catalysts on carbon nitride", year="2024",
               has_suppl="Y", has_ft="Y", has_pdf="Y", is_oa="Y", is_retracted=False):
    comments = []
    if is_retracted:
        comments.append({
            "id": "40748572",
            "source": "MED",
            "type": "Retraction in",
            "note": "Article retracted due to data integrity concerns."
        })
    return {
        "id": pmid,
        "source": "MED",
        "pmid": pmid,
        "pmcid": pmcid,
        "doi": doi,
        "title": title,
        "pubYear": year,
        "journalTitle": "Advanced Materials",
        "pubType": "journal article",
        "isOpenAccess": is_oa,
        "inEPMC": "Y",
        "hasPDF": has_pdf,
        "hasSuppl": has_suppl,
        "hasFT": has_ft,
        "citedByCount": 42,
        "abstractText": "Carbon nitride supported single atoms achieve high photocatalytic efficiency.",
        "authorString": "Jane Doe, John Smith, Alice Brown",
        "authorList": {
            "author": [
                {"fullName": "Jane Doe", "affiliation": "Sun Yat-sen University",
                 "authorId": {"type": "ORCID", "value": "0000-0001-2345-6789"}},
                {"fullName": "John Smith", "affiliation": "Tsinghua University"}
            ]
        },
        "commentCorrectionList": {"commentCorrection": comments} if comments else {}
    }


class MockResponse:
    def __init__(self, data, status=200, headers=None):
        self.status = status
        self.headers = headers or {}
        if isinstance(data, (dict, list)):
            self.payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        elif isinstance(data, str):
            self.payload = data.encode("utf-8")
        else:
            self.payload = data

    def read(self, size=-1):
        return self.payload[:size] if size >= 0 else self.payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def make_test_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("test_si.pdf", b"%PDF-1.4 dummy pdf bytes")
        zf.writestr("figure1.jpg", b"\xff\xd8\xff\xe0 dummy jpeg bytes")
        zf.writestr("data_table.xlsx", b"PK\x03\x04 dummy excel bytes")
        zf.writestr("clip.mp4", b"ftypisom dummy mp4 bytes")
        # Traversal attempt (must be skipped safely)
        zf.writestr("../dangerous.txt", b"evil")
    return buf.getvalue()


class EPMCTests(unittest.TestCase):
    def invoke(self, argv, client):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(argv, client)
        return code, json.loads(out.getvalue())

    def test_parse_id_and_query_builder(self):
        self.assertEqual(parse_id("PMC12710600"), ("pmcid", "PMC12710600"))
        self.assertEqual(parse_id("pmcid:PMC12710600"), ("pmcid", "PMC12710600"))
        
        class Args:
            id = None
            query = "photocatalysis"
            has_suppl = True
            open_access = True
            has_data = True
            preprints = True
            has_fulltext = True
            year_from = 2023
            year_to = 2025

        q, is_id = build_query(Args())
        self.assertFalse(is_id)
        self.assertIn("HAS_SUPPL:y", q)
        self.assertIn("OPEN_ACCESS:y", q)
        self.assertIn("HAS_DATA:y", q)
        self.assertIn("SRC:PPR", q)
        self.assertIn("HAS_FT:y", q)
        self.assertIn("(PUB_YEAR:[2023 TO 2025])", q)

        args_id = Args()
        args_id.id = "PMC12710600"
        q_id, is_id2 = build_query(args_id)
        self.assertTrue(is_id2)
        self.assertEqual(q_id, 'PMCID:"PMC12710600"')

        args_doi = Args()
        args_doi.id = "10.1002/adma.202313513"
        q_doi, is_id3 = build_query(args_doi)
        self.assertTrue(is_id3)
        self.assertEqual(q_doi, 'DOI:"10.1002/adma.202313513"')

    def test_search_and_retraction_relation_normalization(self):
        row = epmc_entry(is_retracted=True)
        paper = normalize_epmc(row)
        self.assertEqual(paper["identifiers"]["pmcid"], ["PMC12710600"])
        self.assertEqual(paper["identifiers"]["pmid"], ["38639018"])
        self.assertEqual(paper["identifiers"]["doi"], ["10.1002/adma.202313513"])
        self.assertEqual(paper["year"], 2024)
        self.assertEqual(paper["citation_count"], 42)
        self.assertEqual(paper["authors"][0]["name"], "Jane Doe")
        self.assertEqual(paper["authors"][0]["identifiers"]["orcid"], ["0000-0001-2345-6789"])
        self.assertEqual(paper["authors"][0]["affiliations"][0]["name"], "Sun Yat-sen University")
        self.assertTrue(paper["extra_epmc"]["has_suppl"])
        self.assertTrue(paper["extra_epmc"]["has_fulltext_xml"])
        self.assertTrue(paper["extra_epmc"]["is_open_access"])
        self.assertEqual(len(paper["relations"]), 1)
        self.assertEqual(paper["relations"][0]["type"], "retraction")
        self.assertEqual(paper["relations"][0]["id"], "40748572")

    def test_epmc_search_run_bundle_and_integrity(self):
        hit = epmc_entry()
        search_payload = {
            "version": "6.9",
            "hitCount": 1,
            "resultList": {"result": [hit]}
        }
        calls = []

        def opener(req, timeout):
            calls.append(req.full_url)
            self.assertIn("europepmc", req.full_url)
            return MockResponse(search_payload)

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run-epmc"
            code, run = self.invoke(["epmc", "single atom photocatalysis", "--limit", "5", "--out", str(out)],
                                    Client({}, opener))
            self.assertEqual(code, 0)
            self.assertEqual(run["command"], "epmc")
            self.assertEqual(run["status"], "success")
            self.assertEqual(run["counts"]["papers"], 1)
            self.assertTrue((out / "run.json").is_file())
            self.assertTrue((out / "papers.json").is_file())
            self.assertTrue((out / "results.json").is_file())
            self.assertTrue((out / "response.json").is_file())
            self.assertTrue((out / "summary.md").is_file())
            self.assertTrue((out / "report.md").is_file())
            self.assertTrue((out / "manifest.json").is_file())
            
            # Verify bundle integrity
            check = check_bundle(out)
            self.assertEqual(check["status"], "success", check["issues"])

    def test_epmc_fetch_xml_and_supplementary_bundle(self):
        hit = epmc_entry()
        search_payload = {"version": "6.9", "hitCount": 1, "resultList": {"result": [hit]}}
        xml_data = """<?xml version="1.0" encoding="UTF-8"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink">
  <front>
    <article-meta>
      <article-title>Single-atom catalysts on carbon nitride</article-title>
    </article-meta>
  </front>
  <body>
    <sec id="sec1"><title>Introduction</title><p>Catalyst details.</p></sec>
    <sec id="sec2"><title>Results</title>
      <fig id="fig1"><label>Figure 1</label><caption><p>TEM and HAADF images.</p></caption></fig>
      <table-wrap id="tbl1"><label>Table 1</label><caption><p>Hydrogen evolution rates.</p></caption></table-wrap>
      <disp-formula id="eq1"><math><mi>E</mi><mo>=</mo><mi>m</mi><msup><mi>c</mi><mn>2</mn></msup></math></disp-formula>
    </sec>
  </body>
  <back>
    <ref-list><ref id="b1"><element-citation publication-type="journal"><article-title>Ref 1</article-title></element-citation></ref></ref-list>
  </back>
</article>"""
        zip_bytes = make_test_zip()

        def opener(req, timeout):
            url = req.full_url
            if "/search?" in url:
                return MockResponse(search_payload)
            if "/fullTextXML" in url:
                return MockResponse(xml_data, headers={"Content-Type": "application/xml"})
            if "/supplementaryFiles" in url:
                return MockResponse(zip_bytes, headers={"Content-Type": "application/zip"})
            raise ValueError("Unexpected URL: " + url)

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run-epmc-fetch"
            code, run = self.invoke(["epmc", "--id", "PMC12710600", "--fetch", "all", "--out", str(out)],
                                    Client({}, opener))
            self.assertEqual(code, 0)
            self.assertTrue(run["fulltext_xml_fetched"])
            self.assertTrue(run["supplementary_fetched"])
            self.assertTrue((out / "fulltext.xml").is_file())
            self.assertTrue((out / "fulltext_summary.json").is_file())
            self.assertTrue((out / "supplementary_manifest.json").is_file())
            
            # Check XML summary
            summary = json.loads((out / "fulltext_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["section_count"], 2)
            self.assertEqual(summary["figure_count"], 1)
            self.assertEqual(summary["table_count"], 1)
            self.assertEqual(summary["formula_count"], 1)
            self.assertEqual(summary["reference_count"], 1)

            # Check extracted supplementary files
            suppl_manifest = json.loads((out / "supplementary_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(suppl_manifest["total_files"], 4)  # dangerous.txt was safely skipped!
            self.assertEqual(suppl_manifest["categories"]["si_pdf"], 1)
            self.assertEqual(suppl_manifest["categories"]["figure_image"], 1)
            self.assertEqual(suppl_manifest["categories"]["data_document"], 1)
            self.assertEqual(suppl_manifest["categories"]["multimedia"], 1)
            self.assertTrue((out / "supplementary" / "test_si.pdf").is_file())
            self.assertTrue((out / "supplementary" / "figure1.jpg").is_file())
            self.assertFalse((out / "dangerous.txt").exists())

            # Check full bundle integrity (all extracted files must be in manifest!)
            check = check_bundle(out)
            self.assertEqual(check["status"], "success", check["issues"])

    def test_epmc_input_validation(self):
        cases = [
            ([], "Provide a search query or --id"),
            (["--year-from", "2026", "--year-to", "2024", "query"], "--year-from must not exceed --year-to"),
            (["--year-from", "999", "query"], "Years must be between 1000 and 9999"),
            (["--limit", "25", "query"], "limit must be between 1 and 20")
        ]
        for flags, expected_err in cases:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "err"
                code, res = self.invoke(["epmc", *flags, "--out", str(out)], Client({}, lambda *args: None))
                self.assertEqual(code, 2)
                err_msg = res["error"]["message"] if isinstance(res.get("error"), dict) else str(res.get("error"))
                self.assertIn(expected_err, err_msg)


if __name__ == "__main__":
    unittest.main()
