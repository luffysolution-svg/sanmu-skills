"""Run from repository root: python -m unittest discover -s tests/sanmu-paper-reader -v."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[2] / "skills" / "sanmu-paper-reader" / "scripts" / "evidence_tools.py"
SPEC = importlib.util.spec_from_file_location("evidence_tools", SCRIPT)
tools = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tools)


class EvidenceToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "root"
        self.root.mkdir()
        self.source = self.root / "a.md"
        self.source.write_text("---\ntitle: hidden metadata\n---\n# Study\n## Results\n"
                               "Voltage −50 mV; Fe³⁺; x²; Na-Cl.\nAcross\n  two lines.\n"
                               "```python\nsecret code evidence\n```\n"
                               "~~~~\nother code evidence\n~~~~\n", encoding="utf-8")
        self.ledger = self.base / "ledger.json"
        self.entry = {"id": "E1", "claim": "A human-assessed claim", "kind": "reported",
                      "support": "supports", "source": {
                          "path": "a.md", "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
                          "quote": "Voltage −50 mV; Fe³⁺; x²; Na-Cl.",
                          "locator": "主文 p.3 Fig.2（人工定位）"},
                      "measurements": [{"value": "50", "unit": "mV", "conditions": "not_reported"}]}

    def ledger_with(self, evidence=None):
        document = {"schema_version": 1, "evidence": [copy.deepcopy(self.entry)] if evidence is None else evidence}
        self.ledger.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        return document

    def invoke(self, *args):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = tools.main([str(arg) for arg in args])
        text = stdout.getvalue()
        self.assertNotIn(str(self.root), text)
        return code, json.loads(text) if text else None

    def check(self, *args):
        return self.invoke("check", self.ledger, "--root", self.root, *args)

    def assert_bad(self, phrase=None):
        code, result = self.check()
        self.assertEqual(code, 1)
        self.assertFalse(result["mechanical_pass"])
        if phrase:
            self.assertIn(phrase, " ".join(result["errors"]))
        return result

    def test_inventory_contract_and_only_explicit_root(self):
        (self.base / "parent.md").write_text("# Outside", encoding="utf-8")
        (self.root / "ignored.txt").write_text("# Ignored", encoding="utf-8")
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "b.md").write_text("Title\n=====\nSub\n---\n", encoding="utf-8")
        out = self.base / "new" / "inventory.json"
        self.assertEqual(self.invoke("inventory", self.root, "--out", out)[0], 0)
        result = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual([s["path"] for s in result["sources"]], ["a.md", "nested/b.md"])
        item = result["sources"][0]
        self.assertEqual(item["id"], "S-" + hashlib.sha256(b"a.md").hexdigest()[:12])
        self.assertEqual(item["sha256"], self.entry["source"]["sha256"])
        self.assertEqual(item["title"], "Study")
        self.assertEqual(item["headings"], [{"level": 1, "text": "Study"}, {"level": 2, "text": "Results"}])
        self.assertEqual(result["sources"][1]["title"], "Title")
        original = out.read_bytes()
        self.assertEqual(self.invoke("inventory", self.root, "--out", out)[0], 0)
        self.assertEqual(out.read_bytes(), original)

    def test_good_multiline_quote_and_scientific_scope(self):
        self.entry["source"]["quote"] = "Across\t two   lines."
        self.entry["measurements"][0]["unit"] = "dimensionless"
        self.ledger_with()
        code, result = self.check()
        self.assertEqual(code, 0)
        self.assertEqual(result["checked_evidence"], 1)
        self.assertEqual(result["scientific_status"], "not_verified")
        self.assertIn("human verification", result["verification_scope"])
        self.assertIn("Locator pages/figures", result["verification_scope"])
        out = self.base / "reports" / "check.json"
        self.assertEqual(self.check("--out", out), (0, None))
        self.assertEqual(json.loads(out.read_text(encoding="utf-8")), result)

    def test_stale_hash_and_corrupt_utf8(self):
        self.ledger_with()
        self.source.write_bytes(self.source.read_bytes() + b"changed")
        self.assert_bad("SHA-256 mismatch")
        self.source.write_bytes(b"\xff\xfe")
        self.assert_bad("UTF-8")

    def test_formula_signs_and_superscripts_are_not_folded(self):
        for quote in ("Voltage 50 mV", "Voltage -50 mV", "Fe3+", "x2", "NaCl"):
            with self.subTest(quote=quote):
                self.entry["source"]["quote"] = quote
                self.ledger_with()
                self.assert_bad("quote not found")

    def test_frontmatter_and_fenced_code_are_not_evidence(self):
        for quote in ("hidden metadata", "secret code evidence", "other code evidence"):
            with self.subTest(quote=quote):
                self.entry["source"]["quote"] = quote
                self.ledger_with()
                self.assert_bad("quote not found")

    def test_malformed_top_level_and_lists(self):
        for document in (None, [], "text", 12, {}, {"schema_version": True, "evidence": []},
                         {"schema_version": 1, "evidence": {}},
                         {"schema_version": 1, "evidence": [None, [], "x", 1]}):
            with self.subTest(document=document):
                self.ledger.write_text(json.dumps(document), encoding="utf-8")
                self.assert_bad()
        self.ledger.write_text('{"broken":', encoding="utf-8")
        self.assert_bad("invalid JSON")

    def test_required_fields_types_enums_and_measurements(self):
        for key in ("id", "claim", "kind", "support", "source"):
            for value in (None, [], {}, " ", 2):
                entry = copy.deepcopy(self.entry)
                entry[key] = value
                self.ledger_with([entry])
                self.assert_bad()
        for key in ("path", "sha256", "quote", "locator"):
            for value in (None, [], " ", 2):
                entry = copy.deepcopy(self.entry)
                entry["source"][key] = value
                self.ledger_with([entry])
                self.assert_bad()
        for value in (None, {}, "x", [None], [{}], [{"value": 50, "unit": "mV", "conditions": "x"}]):
            entry = copy.deepcopy(self.entry)
            entry["measurements"] = value
            self.ledger_with([entry])
            self.assert_bad("measurements")
        for key in ("kind", "support"):
            entry = copy.deepcopy(self.entry)
            entry[key] = "unknown"
            self.ledger_with([entry])
            self.assert_bad("invalid " + key)
        self.entry.pop("measurements")
        self.ledger_with()
        self.assertEqual(self.check()[0], 0)

    def test_missing_fields_and_invalid_hashes(self):
        for key in ("id", "claim", "kind", "support", "source"):
            entry = copy.deepcopy(self.entry)
            del entry[key]
            self.ledger_with([entry])
            self.assert_bad()
        for key in ("path", "sha256", "quote", "locator"):
            entry = copy.deepcopy(self.entry)
            del entry["source"][key]
            self.ledger_with([entry])
            self.assert_bad()
        for digest in ("corrupt", "g" * 64, "0" * 63):
            self.entry["source"]["sha256"] = digest
            self.ledger_with()
            self.assert_bad("64 hexadecimal")

    def test_unclosed_frontmatter_is_rejected(self):
        self.source.write_text("---\nhidden content\n", encoding="utf-8")
        self.entry["source"]["sha256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.entry["source"]["quote"] = "hidden content"
        self.ledger_with()
        self.assert_bad("unclosed YAML")

    def test_duplicate_ids(self):
        self.ledger_with([self.entry, self.entry])
        self.assert_bad("duplicate id")

    def test_source_escape_absolute_paths_and_missing_file(self):
        for path in ("../a.md", "sub/../../a.md", str(self.source), "C:/a.md", "C:a.md",
                     "\\\\host\\share\\a.md", "/a.md", "missing.md", "a.txt"):
            with self.subTest(path=path):
                self.entry["source"]["path"] = path
                self.ledger_with()
                self.assert_bad()

    def symlink(self, link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("symlinks unavailable: " + str(exc))

    def test_external_file_symlink(self):
        outside = self.base / "outside.md"
        outside.write_bytes(self.source.read_bytes())
        self.symlink(self.root / "link.md", outside)
        self.entry["source"]["path"] = "link.md"
        self.ledger_with()
        self.assert_bad("outside root")
        self.assertEqual(self.invoke("inventory", self.root, "--out", self.base / "out.json")[0], 1)

    def test_directory_symlink_escape_and_cycle(self):
        self.symlink(self.root / "external", self.base, directory=True)
        self.assertEqual(self.invoke("inventory", self.root, "--out", self.base / "out.json")[0], 1)
        (self.root / "external").unlink()
        self.symlink(self.root / "loop", self.root, directory=True)
        self.assertEqual(self.invoke("inventory", self.root, "--out", self.base / "out.json")[0], 1)

    def test_conflicting_output_and_protected_source_and_ledger(self):
        self.ledger_with()
        out = self.base / "existing.json"
        out.write_bytes(b"keep me")
        for command in (("inventory", self.root, "--out", out),
                        ("check", self.ledger, "--root", self.root, "--out", out),
                        ("inventory", self.root, "--out", self.source),
                        ("check", self.ledger, "--root", self.root, "--out", self.source),
                        ("check", self.ledger, "--root", self.root, "--out", self.ledger)):
            with self.subTest(command=command):
                before = Path(command[-1]).read_bytes()
                self.assertEqual(self.invoke(*command)[0], 1)
                self.assertEqual(Path(command[-1]).read_bytes(), before)

    def test_hardlink_output_aliases(self):
        self.ledger_with()
        for index, target in enumerate((self.ledger, self.source)):
            alias = self.base / ("alias%d.json" % index)
            try:
                os.link(target, alias)
            except OSError as exc:
                self.skipTest("hardlinks unavailable: " + str(exc))
            original = target.read_bytes()
            self.assertEqual(self.check("--out", alias)[0], 1)
            self.assertEqual(target.read_bytes(), original)
        self.assertEqual(self.invoke("inventory", self.root, "--out", alias)[0], 1)

    def test_symlink_output_alias(self):
        self.ledger_with()
        alias = self.base / "alias.json"
        self.symlink(alias, self.source)
        self.assertEqual(self.check("--out", alias)[0], 1)
        self.assertEqual(self.invoke("inventory", self.root, "--out", alias)[0], 1)

    def test_access_errors_and_size_limit(self):
        self.ledger_with()
        with mock.patch.object(tools.os, "scandir", side_effect=PermissionError(13, "denied", str(self.root))):
            code, result = self.invoke("inventory", self.root, "--out", self.base / "out.json")
            self.assertEqual(code, 1)
            self.assertIn("PermissionError", result["errors"][0])
        with mock.patch.object(tools, "MAX_BYTES", 1):
            self.assert_bad("limit")
        self.source.unlink()
        self.assert_bad("FileNotFoundError")

    def test_source_read_access_error_and_embedded_null(self):
        self.ledger_with()
        original = tools.read_bytes
        def read(path):
            if path.resolve() == self.source.resolve():
                raise PermissionError(13, "denied", str(path))
            return original(path)
        with mock.patch.object(tools, "read_bytes", side_effect=read):
            self.assert_bad("PermissionError")
        self.entry["source"]["path"] = "bad\x00.md"
        self.ledger_with()
        self.assert_bad()

    def test_cli_usage_errors_return_one_without_path_leaks(self):
        for arguments in ((), ("inventory", self.root), ("unknown", str(self.root))):
            code, result = self.invoke(*arguments)
            self.assertEqual(code, 1)
            self.assertIn("CLI arguments", result["errors"][0])

    def test_failure_report_written_and_real_cli(self):
        self.entry["source"]["sha256"] = "0" * 64
        self.ledger_with()
        out = self.base / "failure.json"
        self.assertEqual(self.check("--out", out), (1, None))
        self.assertFalse(json.loads(out.read_text(encoding="utf-8"))["mechanical_pass"])
        process = subprocess.run([sys.executable, str(SCRIPT), "check", str(self.ledger),
                                  "--root", str(self.root)], capture_output=True, encoding="utf-8")
        self.assertEqual(process.returncode, 1)
        self.assertEqual(process.stderr, "")
        self.assertFalse(json.loads(process.stdout)["mechanical_pass"])


if __name__ == "__main__":
    unittest.main()
