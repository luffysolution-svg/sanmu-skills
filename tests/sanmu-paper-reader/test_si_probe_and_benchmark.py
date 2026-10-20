"""Tests for SI Fast-Probe and Benchmark Collector scripts."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2] / "skills" / "sanmu-paper-reader"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.benchmark_collector import BenchmarkError, upsert_benchmark, load_benchmarks, export_markdown_table
from scripts.si_probe import KEYWORD_CATEGORIES, format_markdown_report


class BenchmarkCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.csv_path = Path(self.temp_dir.name) / "benchmarks.csv"

    def test_upsert_and_deduplicate(self):
        entry1 = {
            "doi": "10.1002/adma.202313513",
            "citation_key": "Yang-2024",
            "year": "2024",
            "journal": "Adv. Mater.",
            "catalyst_system": "Ni/NiOx@C / g-C3N4",
            "reaction_type": "Photocatalytic H2 evolution",
            "activity_rate": "10.71",
            "activity_unit": "mmol g-1 h-1",
            "aqy_percent": "40.78% @ 380 nm",
            "light_condition": "300W Xe (AM 1.5)",
            "scavenger_or_electrolyte": "10 vol% TEOA",
            "benchmark_comparison": "1.76x of 1.0 wt% Pt",
            "mechanism": "FLPs + Photothermal"
        }
        res1 = upsert_benchmark(self.csv_path, entry1)
        self.assertEqual(res1["status"], "added")
        self.assertEqual(res1["total_records"], 1)

        # Update the same entry with higher activity
        entry2 = dict(entry1)
        entry2["activity_rate"] = "10.75"
        res2 = upsert_benchmark(self.csv_path, entry2)
        self.assertEqual(res2["status"], "updated")
        self.assertEqual(res2["total_records"], 1)

        rows = load_benchmarks(self.csv_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["activity_rate"], "10.75")

    def test_patch_preserves_omitted_fields_and_explicit_clear(self):
        upsert_benchmark(self.csv_path, {"doi": "10.1/example", "activity_rate": "5",
                         "activity_unit": "mmol g-1 h-1", "source": "SI p.4 Table S1",
                         "record_date": "2024-01-01"})
        upsert_benchmark(self.csv_path, {"doi": "10.1/example", "activity_rate": "6"})
        row = load_benchmarks(self.csv_path)[0]
        self.assertEqual(row["activity_unit"], "mmol g-1 h-1")
        self.assertEqual(row["source"], "SI p.4 Table S1")
        self.assertEqual(row["record_date"], "2024-01-01")
        upsert_benchmark(self.csv_path, {"doi": "10.1/example", "activity_rate": None, "source": ""})
        row = load_benchmarks(self.csv_path)[0]
        self.assertEqual(row["activity_rate"], "")
        self.assertEqual(row["source"], "")
        self.assertEqual(row["activity_unit"], "mmol g-1 h-1")

    def test_same_paper_multiple_conditions_and_ambiguous_update_is_non_mutating(self):
        for condition, rate in [("AM1.5", "5"), ("420nm", "3")]:
            upsert_benchmark(self.csv_path, {"doi": "10.1/example", "condition_id": condition,
                             "activity_rate": rate, "light_condition": condition})
        before = self.csv_path.read_bytes()
        with self.assertRaisesRegex(BenchmarkError, "Ambiguous.*record_id"):
            upsert_benchmark(self.csv_path, {"doi": "10.1/example", "activity_rate": "99"})
        self.assertEqual(before, self.csv_path.read_bytes())
        upsert_benchmark(self.csv_path, {"doi": "10.1/example", "condition_id": "420nm", "activity_rate": "4"})
        rows = load_benchmarks(self.csv_path)
        self.assertEqual([r["activity_rate"] for r in rows], ["5", "4"])
        self.assertIn("420nm", export_markdown_table(self.csv_path))
        self.assertIn("10.1/example", export_markdown_table(self.csv_path))

    def test_record_id_targets_one_row_without_doi(self):
        for record in ["sample-A", "sample-B"]:
            upsert_benchmark(self.csv_path, {"doi": "10.1/example", "record_id": record, "source": "Fig. 2"})
        upsert_benchmark(self.csv_path, {"record_id": "sample-B", "activity_rate": "7"})
        rows = load_benchmarks(self.csv_path)
        self.assertEqual(rows[0]["activity_rate"], "")
        self.assertEqual(rows[1]["activity_rate"], "7")
        self.assertEqual(rows[1]["doi"], "10.1/example")
        self.assertEqual(rows[1]["source"], "Fig. 2")

    def test_legacy_csv_preserves_custom_columns(self):
        self.csv_path.write_text("doi,activity_rate,lab_note\n10.1/EXAMPLE,5,keep handwritten note\n", encoding="utf-8-sig")
        upsert_benchmark(self.csv_path, {"doi": "10.1/example", "activity_rate": "6"})
        row = load_benchmarks(self.csv_path)[0]
        self.assertEqual(row["lab_note"], "keep handwritten note")
        self.assertEqual(row["activity_rate"], "6")
        upsert_benchmark(self.csv_path, {"doi": "10.1/example", "condition_id": "420nm", "activity_rate": "4"})
        self.assertEqual(len(load_benchmarks(self.csv_path)), 2)

    def test_legacy_duplicates_and_conflicting_keys_refuse_without_writing(self):
        self.csv_path.write_text("doi,citation_key,activity_rate\n10.1/x,A,1\n10.1/x,B,2\n", encoding="utf-8")
        before = self.csv_path.read_bytes()
        with self.assertRaisesRegex(BenchmarkError, "reviewed copy"):
            upsert_benchmark(self.csv_path, {"doi": "10.1/x", "activity_rate": "99"})
        self.assertEqual(before, self.csv_path.read_bytes())
        self.csv_path.write_text("doi,citation_key\n10.1/x,A\n10.1/y,B\n", encoding="utf-8")
        before = self.csv_path.read_bytes()
        with self.assertRaisesRegex(BenchmarkError, "Conflicting paper identity"):
            upsert_benchmark(self.csv_path, {"doi": "10.1/x", "citation_key": "B"})
        self.assertEqual(before, self.csv_path.read_bytes())

    def assert_identity_rejected_without_write(self, patch, message):
        from unittest.mock import patch as mock_patch
        before = self.csv_path.read_bytes()
        with mock_patch("scripts.benchmark_collector.save_benchmarks") as save:
            with self.assertRaisesRegex(BenchmarkError, message):
                upsert_benchmark(self.csv_path, patch)
            save.assert_not_called()
        self.assertEqual(self.csv_path.read_bytes(), before)

    def test_record_id_cannot_reassign_paper_or_existing_condition(self):
        upsert_benchmark(self.csv_path, {"record_id": "rA", "doi": "A", "citation_key": "key-A",
                         "condition_id": "c1", "activity_rate": "1", "source": "A Fig. 1"})
        for identity in ({"doi": "B"}, {"citation_key": "key-B"}, {"condition_id": "c2"}, {"doi": None}):
            with self.subTest(identity=identity):
                self.assert_identity_rejected_without_write(
                    {"record_id": "rA", "activity_rate": "99", **identity}, "Identity change refused")

    def test_conflicting_paper_identifiers_are_checked_before_condition_filter(self):
        upsert_benchmark(self.csv_path, {"doi": "A", "citation_key": "key-A", "condition_id": "c1"})
        upsert_benchmark(self.csv_path, {"doi": "B", "citation_key": "key-B", "condition_id": "c2"})
        self.assert_identity_rejected_without_write(
            {"doi": "A", "citation_key": "key-B", "condition_id": "c2", "activity_rate": "99"},
            "Conflicting paper identity")

    def test_result_identity_collisions_refused_but_unrelated_legacy_duplicates_allowed(self):
        # Legacy r1 lacks a condition; filling it would collide with A/c2.
        self.csv_path.write_text(
            "doi,citation_key,record_id,condition_id,activity_rate\n"
            "A,key-A,r1,,1\nA,key-A,r2,c2,2\n"
            "old,old-key,,,3\nold,old-key,,,4\n", encoding="utf-8")
        self.assert_identity_rejected_without_write(
            {"record_id": "r1", "condition_id": "c2", "activity_rate": "99"}, "Duplicate resulting identity")
        self.assert_identity_rejected_without_write(
            {"doi": "A", "citation_key": "key-A", "record_id": "new", "condition_id": "c2"},
            "Duplicate resulting identity")
        # The requested r1 c1 -> c2 change must also fail, even before uniqueness checks.
        upsert_benchmark(self.csv_path, {"record_id": "r1", "condition_id": "c1"})
        self.assert_identity_rejected_without_write(
            {"record_id": "r1", "condition_id": "c2"}, "Identity change refused")
        upsert_benchmark(self.csv_path, {"record_id": "r1", "activity_rate": "7"})
        rows = load_benchmarks(self.csv_path)
        self.assertEqual(rows[0]["activity_rate"], "7")
        self.assertEqual([row["activity_rate"] for row in rows[2:]], ["3", "4"])
        # Filling an absent legacy paper ID must agree with peer conditions too.
        self.csv_path.write_text(
            "doi,citation_key,record_id,condition_id\n,key-A,r1,c1\nA,key-A,r2,c2\n", encoding="utf-8")
        self.assert_identity_rejected_without_write(
            {"record_id": "r1", "doi": "B"}, "Conflicting resulting paper identity")
        upsert_benchmark(self.csv_path, {"record_id": "r1", "doi": "A"})
        self.assertEqual(load_benchmarks(self.csv_path)[0]["doi"], "A")

    def test_export_markdown_table(self):
        entry = {
            "doi": "10.1002/adma.202313513",
            "catalyst_system": "Ni/NiOx@C / g-C3N4",
            "activity_rate": "10.71",
            "activity_unit": "mmol g-1 h-1"
        }
        upsert_benchmark(self.csv_path, entry)
        md = export_markdown_table(self.csv_path)
        self.assertIn("Ni/NiOx@C / g-C3N4", md)
        self.assertIn("10.71 mmol g-1 h-1", md)


class SIProbeTests(unittest.TestCase):
    def test_format_markdown_report(self):
        fake_result = {
            "file_name": "test_si.pdf",
            "total_pages": 42,
            "figures": [{"id": "Figure S1", "page": 5, "caption": "TGA curve"}],
            "tables": [{"id": "Table S1", "page": 30, "caption": "ESR summary"}],
            "category_matches": {
                "synthesis": [{"page": 2, "matched_keywords": ["synthesis"], "snippet": "..."}],
                "catalysis": [{"page": 4, "matched_keywords": ["her"], "snippet": "..."}],
                "electrochemical": [],
                "computation": [{"page": 6, "matched_keywords": ["dft"], "snippet": "..."}],
                "characterization": []
            }
        }
        md = format_markdown_report(fake_result)
        self.assertIn("test_si.pdf", md)
        self.assertIn("42 页", md)
        self.assertIn("Figure S1", md)
        self.assertIn("Table S1", md)
        self.assertIn("p.2", md)
        self.assertIn("p.6", md)


if __name__ == "__main__":
    unittest.main()
