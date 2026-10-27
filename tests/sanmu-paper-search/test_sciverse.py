import contextlib
from email.message import Message
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "sanmu-paper-search" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import _core
import _providers
import _sciverse
import paper_search


def run_cli(argv, client):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return paper_search.main(argv, client=client)


class FakeResponse:
    def __init__(self, body, status=200, headers=None, url="https://api.sciverse.space/test"):
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


class SciVerseTests(unittest.TestCase):
    def test_meta_search_papers_authors_sources_and_secret_redaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []

            def opener(req, timeout=30):
                body = json.loads(req.data.decode("utf-8")) if req.data else None
                calls.append((req.get_method(), req.full_url, body, dict(req.header_items())))
                if body and body.get("collection") == "authors":
                    return FakeResponse({
                        "total_count": 1,
                        "results": [{
                            "id": "https://openalex.org/A5001",
                            "display_name": "Jane Catalyst",
                            "works_count": 42,
                            "cited_by_count": 1800,
                            "orcid": "https://orcid.org/0000-0001-2345-6789",
                            "last_known_institutions": ["SCAU"],
                            "summary_stats": {"h_index": 24},
                        }],
                    })
                return FakeResponse({
                    "total_count": 1,
                    "results": [{
                        "unique_id": "paper:10.1002/adma.202313513",
                        "doc_id": "a" * 64,
                        "doi": "10.1002/adma.202313513",
                        "title": "Single-Atom Ni/NiOx Photocatalyst on g-C3N4",
                        "abstract": "Detailed abstract with sci_test_secret_token_999 inside",
                        "publication_published_year": 2024,
                        "publication_published_date": "2024-03-15",
                        "publication_venue_name_unified": "Advanced Materials",
                        "citation_count": 88,
                        "influential_citation_count": 12,
                        "reference_count": 54,
                        "fwci": 4.25,
                        "access_is_oa": "true",
                        "access_oa_url": "https://example.org/oa.pdf",
                        "access_license": "cc-by",
                        "is_content_accessible": True,
                        "author": [
                            {"name": "Sanmu Wu", "orcid": "0000-0002-0000-1111"},
                            {"name": "Sanmu Wu", "orcid": "0000-0002-0000-1111"},
                        ],
                    }],
                })

            env = {
                "SCIVERSE_API_KEY": "sci_test_secret_token_999",
                "SANMU_DISABLE_SCIVERSE_CRED_FILE": "1",
            }
            client = _providers.Client(env=env, opener=opener, sleep=lambda _: None)
            out_dir = Path(tmp) / "sv-meta"
            code = run_cli([
                "sciverse", "single atom photocatalyst",
                "--mode", "meta-search",
                "--freshness-boost", "MILD",
                "--impact-boost", "STRONG",
                "--oa-only",
                "--year-from", "2022",
                "--limit", "5",
                "--out", str(out_dir),
            ], client=client)
            self.assertEqual(code, 0)
            self.assertEqual(calls[0][2]["freshness_boost"], "MILD")
            self.assertEqual(calls[0][2]["impact_boost"], "STRONG")
            papers = json.loads((out_dir / "papers.json").read_text(encoding="utf-8"))
            self.assertEqual(len(papers), 1)
            p0 = papers[0]
            self.assertEqual(p0["sciverse_doc_id"], "a" * 64)
            self.assertEqual(p0["sciverse_unique_id"], "paper:10.1002/adma.202313513")
            self.assertEqual(p0["fwci"], 4.25)
            self.assertEqual(len(p0["authors"]), 1)
            self.assertIn("[REDACTED]", p0["abstract"])
            self.assertNotIn("sci_test_secret_token_999", (out_dir / "raw/0001.json").read_text(encoding="utf-8"))
            self.assertEqual(_core.check_bundle(out_dir)["status"], "success")

            out_authors = Path(tmp) / "sv-authors"
            code_a = run_cli([
                "sciverse", "Jane Catalyst",
                "--mode", "meta-search",
                "--collection", "authors",
                "--out", str(out_authors),
            ], client=_providers.Client(env=env, opener=opener, sleep=lambda _: None))
            self.assertEqual(code_a, 0)
            run_a = json.loads((out_authors / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_a["author_candidates"][0]["author_id"], "A5001")
            self.assertEqual(run_a["author_candidates"][0]["h_index"], 24)

    def test_semantic_search_merges_chunks_per_paper_and_supports_content_and_figures(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc_hash = "b" * 64

            def opener(req, timeout=30):
                url = req.full_url
                if "/agentic-search" in url:
                    body = json.loads(req.data.decode("utf-8"))
                    self.assertEqual(body["retrieval"], "hybrid")
                    self.assertEqual(body["sub_queries"], 3)
                    return FakeResponse({
                        "hits": [
                            {
                                "chunk_id": "c1",
                                "doc_id": doc_hash,
                                "title": "Photothermal H2 Evolution on Carbon Nitride",
                                "abstract": "Full abstract text.",
                                "chunk": "Chunk 1: Ni single atoms lower the H* adsorption free energy.",
                                "offset": 1200,
                                "page_no": 3,
                                "score": 0.9123,
                                "publication_published_year": 2025,
                                "publication_venue_name_unified": "ACS Catalysis",
                                "citation_count": 31,
                                "author": ["Alice Zhang", "Bob Li"],
                            },
                            {
                                "chunk_id": "c2",
                                "doc_id": doc_hash,
                                "title": "Photothermal H2 Evolution on Carbon Nitride",
                                "abstract": "Full abstract text.",
                                "chunk": "Chunk 2: EXAFS confirms Ni-N4 coordination.",
                                "offset": 3400,
                                "page_no": 5,
                                "score": 0.875,
                                "publication_published_year": 2025,
                                "publication_venue_name_unified": "ACS Catalysis",
                                "citation_count": 31,
                                "author": ["Alice Zhang", "Bob Li"],
                            },
                        ]
                    })
                if "/content" in url:
                    return FakeResponse({
                        "code": 0,
                        "text": "## Results\nHere is Figure 1:\n![Figure 1](dt=2026/fig1.jpg)\nAnd unsafe ![bad](../secret.png)\n",
                        "bytes_returned": 96,
                        "next_offset": 2100,
                        "more": True,
                    })
                if "/resource" in url:
                    self.assertIn("file_name=dt%3D2026%2Ffig1.jpg", url)
                    return FakeResponse(b"\xff\xd8\xff\xe0fake-jpeg-bytes", headers={"Content-Type": "image/jpeg"})
                raise AssertionError(f"Unexpected URL: {url}")

            env = {"SCIVERSE_API_TOKEN": "sci_token_abc", "SANMU_DISABLE_SCIVERSE_CRED_FILE": "1"}
            out_sem = Path(tmp) / "sv-sem"
            code = run_cli([
                "sciverse", "How do Ni single atoms lower H* adsorption energy?",
                "--mode", "semantic-search",
                "--semantic-mode", "quality",
                "--limit", "5",
                "--out", str(out_sem),
            ], client=_providers.Client(env=env, opener=opener, sleep=lambda _: None))
            self.assertEqual(code, 0)
            sem_papers = json.loads((out_sem / "papers.json").read_text(encoding="utf-8"))
            self.assertEqual(len(sem_papers), 1)
            self.assertEqual(len(sem_papers[0]["sciverse_chunks"]), 2)
            self.assertEqual(_core.check_bundle(out_sem)["status"], "success")

            out_cnt = Path(tmp) / "sv-content"
            code_c = run_cli([
                "sciverse",
                "--mode", "content",
                "--doc-id", doc_hash,
                "--offset", "900",
                "--chars", "1200",
                "--download-figures",
                "--out", str(out_cnt),
            ], client=_providers.Client(env=env, opener=opener, sleep=lambda _: None))
            self.assertEqual(code_c, 0)
            run_c = json.loads((out_cnt / "run.json").read_text(encoding="utf-8"))
            sc_info = run_c["sciverse_content"]
            self.assertEqual(sc_info["doc_id"], doc_hash)
            self.assertEqual(sc_info["offset"], 900)
            self.assertEqual(sc_info["next_offset"], 2100)
            self.assertEqual(len(sc_info["figure_refs"]), 1)
            self.assertEqual(len(sc_info["downloaded_figures"]), 1)
            fig_rel = sc_info["downloaded_figures"][0]["local_path"]
            self.assertTrue((out_cnt / fig_rel).is_file())
            # Raw audit log must not store full verbatim text field
            raw_audit = json.loads((out_cnt / "raw/0001.json").read_text(encoding="utf-8"))
            self.assertNotIn("text", raw_audit["body"])
            self.assertIn("text_sha256", raw_audit["body"])
            self.assertEqual(_core.check_bundle(out_cnt)["status"], "success")

    def test_relations_hydrates_unique_ids_and_paper_schema_materials_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            def opener(req, timeout=30):
                url = req.full_url
                if "/meta-paper-relations" in url:
                    return FakeResponse({
                        "total_count": 2,
                        "page": 1,
                        "page_size": 10,
                        "items": [
                            {"id": "paper:10.1038/s41467-021-24079-8", "id_type": "sciverse", "title": "Pt single-atom catalyst"},
                            {"id": "https://openalex.org/W1979187276", "id_type": "openalex", "title": "Mott-Schottky photocatalyst"},
                        ],
                    })
                if "/meta-search" in url:
                    return FakeResponse({
                        "total_count": 1,
                        "results": [{
                            "unique_id": "paper:10.1038/s41467-021-24079-8",
                            "doi": "10.1038/s41467-021-24079-8",
                            "title": "Pt single-atom catalyst coupled with transition metal oxide",
                            "publication_published_year": 2021,
                            "publication_venue_name_unified": "Nature Communications",
                            "citation_count": 320,
                            "author": [{"name": "Wei Chen"}],
                        }],
                    })
                if "/paper-schema/materials" in url:
                    return FakeResponse({
                        "items": [{
                            "schema_id": "2008.12187v1-f78968c8e105",
                            "found": True,
                            "schema_paper": {
                                "schema_id": "2008.12187v1-f78968c8e105",
                                "doi": "10.48550/arxiv.2008.12187",
                                "title": "Graph Neural Network Architecture Search for Molecular Property Prediction",
                                "year": 2020,
                                "venue": "arXiv",
                                "authors": ["Shengli Jiang"],
                                "research_problem": "Manual GNN design is labor-intensive.",
                                "central_contribution": "Neural Architecture Search for MPNNs.",
                                "headline_result": "Outperforms baseline GNNs on QM9.",
                                "has_code": True,
                            },
                            "counts": {"entities": {"total": 10, "returned": 1, "truncated": False}},
                            "entities": [{"entity_type": "Component", "entity_subtype": "algorithm", "name": "Gather Node", "section": "method", "text": "Graph pooling node."}],
                            "relations": [{"relation_type": "part_of", "source_local_id": "cmp:gather", "target_local_id": "con:mpnn"}],
                            "evidence": [{"hot_groups": ["core_claim_result"], "key": "MAE", "value_text": "0.0079 eV"}],
                            "terms": [{"term": "gnn"}],
                        }]
                    })
                raise AssertionError(f"Unexpected URL: {url}")

            env = {"SCIVERSE_API_KEY": "sci_key_123", "SANMU_DISABLE_SCIVERSE_CRED_FILE": "1"}
            out_rel = Path(tmp) / "sv-rel"
            code_r = run_cli([
                "sciverse",
                "--mode", "relations",
                "--id", "10.3390/catal12111304",
                "--relation", "references",
                "--out", str(out_rel),
            ], client=_providers.Client(env=env, opener=opener, sleep=lambda _: None))
            self.assertEqual(code_r, 0)
            rel_papers = json.loads((out_rel / "papers.json").read_text(encoding="utf-8"))
            self.assertEqual(len(rel_papers), 2)
            self.assertEqual(rel_papers[0]["year"], 2021)
            self.assertEqual(rel_papers[0]["relations"][0]["type"], "references")
            self.assertEqual(_core.check_bundle(out_rel)["status"], "success")

            out_mat = Path(tmp) / "sv-mat"
            code_m = run_cli([
                "sciverse",
                "--mode", "schema-materials",
                "--schema-id", "2008.12187v1-f78968c8e105",
                "--goal", "reproduction",
                "--out", str(out_mat),
            ], client=_providers.Client(env=env, opener=opener, sleep=lambda _: None))
            self.assertEqual(code_m, 0)
            self.assertTrue((out_mat / "schema_materials.md").is_file())
            mat_papers = json.loads((out_mat / "papers.json").read_text(encoding="utf-8"))
            self.assertEqual(mat_papers[0]["sciverse_schema_id"], "2008.12187v1-f78968c8e105")
            self.assertTrue(mat_papers[0]["sciverse_schema"]["has_code"])
            self.assertEqual(_core.check_bundle(out_mat)["status"], "success")

            # Anti-leak base URL validation before creating output directory
            bad_out = Path(tmp) / "never-created"
            bad_env = {**env, "SCIVERSE_BASE_URL": "https://evil.example.com"}
            code_bad = run_cli(["sciverse", "test", "--out", str(bad_out)], client=_providers.Client(env=bad_env, opener=opener, sleep=lambda _: None))
            self.assertEqual(code_bad, 2)
            self.assertFalse(bad_out.exists())


if __name__ == "__main__":
    unittest.main()
