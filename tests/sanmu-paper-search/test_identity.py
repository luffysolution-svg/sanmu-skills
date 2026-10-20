"""Offline regression tests for identity, author alignment and credential redaction."""
import copy
import itertools
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / 'skills' / 'sanmu-paper-search') / "scripts"))
from _core import Redactor, check_bundle, deduplicate, dumps, ids, record, write_bundle


def author(name="Jane Doe", **identifiers):
    return {"name": name, "identifiers": ids(**identifiers), "affiliations": []}


def paper(identifier="10.1234/one", arxiv=None, kind="journal-article", authors=None, **extra):
    result = record("fixture", identifier, "Shared paper title", 2025,
                    ids(doi=identifier, arxiv=arxiv),
                    [author()] if authors is None else authors, kind=kind)
    result.update(extra)
    return result


class IdentityTests(unittest.TestCase):
    def test_arxiv_versions_and_old_style_category_preserved(self):
        self.assertEqual(ids(arxiv=["https://arxiv.org/abs/2401.12345v1",
                                    "arXiv:hep-th/9901001v2"]),
                         {"arxiv": ["2401.12345v1", "hep-th/9901001v2"]})
        a = paper(arxiv="hep-th/9901001v1")
        b = paper(arxiv="hep-th/9901001v2")
        result = deduplicate([a, b])
        self.assertEqual(len(result), 2)  # Even a shared DOI cannot erase explicit versions.
        self.assertEqual(result[0]["identifiers"]["arxiv"], ["hep-th/9901001v1"])
        self.assertEqual(result[1]["identifiers"]["arxiv"], ["hep-th/9901001v2"])
        for source, target in ((result[0], result[1]), (result[1], result[0])):
            relation = source["relations"][0]
            self.assertEqual(relation["target_identifiers"], target["identifiers"])
            self.assertEqual(relation["evidence"]["shared_arxiv_families"], ["hep-th/9901001"])
            self.assertIn("different_arxiv_version", relation["evidence"]["reasons"])
        self.assertEqual(deduplicate(result), result)

    def test_different_doi_and_publication_types_are_related_not_collapsed(self):
        for a, b, reason in (
            (paper("10.1234/preprint", "2401.12345", "preprint"),
             paper("10.1234/published", "2401.12345"), "different_doi"),
            (paper(None, "2401.12345", "posted-content"),
             paper(None, "2401.12345", "JournalArticle"), "preprint_publication_type"),
        ):
            with self.subTest(reason=reason):
                result = deduplicate([a, b])
                self.assertEqual(len(result), 2)
                self.assertIn(reason, result[0]["relations"][0]["evidence"]["reasons"])
                self.assertEqual(result[0]["identifiers"], a["identifiers"])
                self.assertEqual(result[1]["identifiers"], b["identifiers"])

    def test_bridge_cannot_reunite_distinct_versions(self):
        a = paper("10.1234/preprint", "2401.12345v1", "preprint")
        b = paper("10.1234/published", "2401.12345v2")
        bridge = paper(None, "2401.12345")
        bridge["identifiers"].update(ids(openalex="W1"))
        a["identifiers"].update(ids(openalex="W1"))
        b["identifiers"].update(ids(openalex="W1"))
        for order in itertools.permutations([a, b, bridge]):
            result = deduplicate(order)
            self.assertEqual(len(result), 2)
            self.assertTrue(all(len(p["identifiers"].get("doi", [])) <= 1 for p in result))
            self.assertTrue(all(p["relations"] for p in result))

    def test_same_identity_merges_and_api_version_does_not_split_papers(self):
        a = paper(arxiv="2401.12345v1", schema_version="1.0", api_version="v1", version="v1")
        b = paper(arxiv="2401.12345v1", schema_version="2.0", api_version="v2", version="v2")
        a["provenance"][0]["provider"] = "openalex"
        b["provenance"][0]["provider"] = "semantic-scholar"
        result = deduplicate([a, b])
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["provenance"]), 2)
        self.assertEqual(result[0]["relations"], [])
        self.assertEqual(len(result[0]["versions"]), 2)
        self.assertEqual(result[0]["identifiers"]["arxiv"], ["2401.12345v1"])
        self.assertFalse(any("api_version" in version for version in result[0]["versions"]))

    def test_aligned_authors_fuse_ids_and_keep_original_evidence(self):
        a = paper(authors=[author(openalex="A1", orcid="0000-0001-0002-0003"),
                           author("John Smith", openalex="A2")])
        b = paper(authors=[author(" Jane   Doe ", semantic_scholar="42"),
                           author("John Smith", semantic_scholar="43")], abstract="Actual abstract")
        b["authors"][0]["affiliations"] = [{"name": "University"}]
        original = copy.deepcopy([a, b])
        for order in ([a, b], [b, a]):
            merged = deduplicate(order)[0]
            jane = merged["authors"][0]
            self.assertEqual(jane["identifiers"]["openalex"], ["A1"])
            self.assertEqual(jane["identifiers"]["semantic_scholar"], ["42"])
            self.assertEqual(jane["identifiers"]["orcid"], ["0000-0001-0002-0003"])
            self.assertEqual(jane["affiliations"], [{"name": "University"}])
            self.assertEqual(merged["abstract"], "Actual abstract")
            self.assertEqual(merged["evidence_level"], "abstract")
            self.assertFalse(merged["read"])
            self.assertTrue(any(c["field"] == "authors" for c in merged["conflicts"]))
            self.assertEqual(len(merged["versions"]), 2)
        self.assertEqual([a, b], original)

    def test_ambiguous_authors_not_fused_and_evidence_retained(self):
        cases = [
            ([author("Jane Doe", openalex="A1"), author("John Smith", openalex="A2")],
             [author("John Smith", semantic_scholar="43"), author("Jane Doe", semantic_scholar="42")]),
            ([author("Jane Doe", openalex="A1"), author("Jane Doe", openalex="A2")],
             [author("Jane Doe", semantic_scholar="42"), author("Jane Doe", semantic_scholar="43")]),
            ([author("J. Doe", openalex="A1")], [author("J. Doe", semantic_scholar="42")]),
            ([author(openalex="A1", orcid="0000-0001-0002-0003")],
             [author(semantic_scholar="42", orcid="0000-0001-0002-0004")]),
            ([author(openalex="A1"), author("John Smith", openalex="A2")],
             [author(semantic_scholar="42")]),
        ]
        for first, second in cases:
            with self.subTest(first=first, second=second):
                merged = deduplicate([paper(authors=first), paper(authors=second)])[0]
                self.assertEqual(merged["authors"], first)
                self.assertTrue(any(v["authors"] == second for v in merged["versions"]))
                self.assertTrue(any(c["field"] == "authors" for c in merged["conflicts"]))


class RedactionTests(unittest.TestCase):
    def test_parsed_signed_queries_without_env_and_structured_fields(self):
        url = ("https://publisher.example/paper.pdf?X-Amz-Credential=CREDENTIAL_SECRET"
               "&x-AMZ-Signature=SIGNATURE_SECRET&access%5Ftoken=ACCESS_SECRET"
               "&X-Amz-Security-Token=SESSION_SECRET&api%5Fkey=KEY_SECRET"
               "&download=1&blank=&part=one&part=two#page=3")
        redactor = Redactor({})
        result = redactor.text(url)
        pairs = parse_qs(urlsplit(result).query, keep_blank_values=True)
        for key in ("X-Amz-Credential", "x-AMZ-Signature", "access_token", "X-Amz-Security-Token", "api_key"):
            self.assertEqual(pairs[key], ["[REDACTED]"])
        self.assertEqual(pairs["part"], ["one", "two"])
        self.assertEqual(pairs["blank"], [""])
        self.assertEqual(pairs["download"], ["1"])
        self.assertEqual(urlsplit(result).fragment, "page=3")
        self.assertEqual(redactor.text(result), result)
        cleaned = redactor.clean({"url": url, "debug": {"access_token": "FIELD_SECRET", "X-Amz-Signature": "OTHER_SECRET"}})
        self.assertEqual(cleaned["debug"], {"access_token": "[REDACTED]", "X-Amz-Signature": "[REDACTED]"})
        self.assertEqual(redactor.clean({"local_matches": [{"key": "ITEMKEY"}]}),
                         {"local_matches": [{"key": "ITEMKEY"}]})
        self.assertEqual(parse_qs(urlsplit(redactor.text("https://example.org/?key=SECRET")).query)["key"],
                         ["[REDACTED]"])
        text = redactor.text('Failure ' + url + '\n' + dumps({"url": url, "ok": True}))
        self.assertNotIn("_SECRET", text)
        self.assertEqual(json.loads(text.split('\n', 1)[1])["ok"], True)
        self.assertNotIn("SECRET", redactor.text("request failed ?access_token=SECRET&limit=10"))

    def test_bundle_remains_parseable_and_hashed_after_redaction(self):
        url = "https://publisher.example/a.pdf?X-Amz-Signature=SIGNATURE_SECRET&access_token=ACCESS_SECRET"
        p = paper(oa_locations=[{"url": url}])
        with tempfile.TemporaryDirectory() as root:
            out = Path(root)
            write_bundle(out, "search", {"text": "query"}, [p],
                         {"fixture": {"status": "ok", "fetched": 1}},
                         [{"body": {"url": url, "access_token": "FIELD_SECRET"}}], Redactor({}))
            for name in ("run.json", "papers.json", "raw/0001.json"):
                text = (out / name).read_text(encoding="utf-8")
                self.assertNotIn("_SECRET", text)
                json.loads(text)
            self.assertEqual(check_bundle(out)["status"], "success")
        self.assertEqual(Redactor({"S2_API_KEY": "known key"}).text("known%20key known+key known key"),
                         "[REDACTED] [REDACTED] [REDACTED]")


if __name__ == "__main__":
    unittest.main()
