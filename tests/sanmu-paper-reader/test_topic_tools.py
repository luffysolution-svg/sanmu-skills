"""Offline topic routing contracts, included in the skill's unittest suite."""
import ast
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "skills" / "sanmu-paper-reader" / "scripts/topic_tools.py"
SPEC = importlib.util.spec_from_file_location("topic_tools", SCRIPT)
tools = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tools)


class TopicToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "vault"
        self.root.mkdir()
        self.out = self.base / "index"

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    def invoke(self, *args, good=True):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = tools.main([str(a) for a in args])
        if good:
            self.assertEqual(code, 0, stderr.getvalue())
            self.assertEqual(stderr.getvalue(), "")
            return json.loads(stdout.getvalue())
        self.assertNotEqual(code, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("error:", stderr.getvalue())
        return stderr.getvalue()

    def build(self, *files, extra=(), good=True):
        return self.invoke("build", "--root", self.root, "--files", *files,
                           "--out", self.out, *extra, good=good)

    def query(self, text, *extra, good=True):
        return self.invoke("query", self.out, "--root", self.root,
                           "--query", text, *extra, good=good)

    def chunks(self):
        return [json.loads(line) for line in (self.out / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]

    def agent(self, rows):
        directory = self.base / "agent"
        directory.mkdir(exist_ok=True)
        (directory / "chunks.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        return ("--agent-index", directory)

    def test_build_only_explicit_sources_hash_navigation_and_no_mutation(self):
        text = "# 电池研究\r\n## 界面\r\n离子传输。\r\n"
        path = self.write("notes/笔记.md", text)
        self.write("sources/ignored.md", "# Never indexed\n")
        self.build("notes/笔记.md")
        index = json.loads((self.out / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(index["sources"], [{"path": "notes/笔记.md", "sha256": hashlib.sha256(text.encode()).hexdigest()}])
        self.assertEqual(path.read_bytes(), text.encode())
        navigation = (self.out / "00-主题路由.md").read_text(encoding="utf-8")
        self.assertIn("电池研究 / 界面", navigation)
        self.assertNotIn("#L", navigation)
        for c in self.chunks():
            self.assertIn(c["id"], navigation)
            self.assertEqual(c["text"], "".join(text.splitlines(keepends=True)[c["start_line"] - 1:c["end_line"]]))
            self.assertEqual(c["pages"], [])
            self.assertIsNone(c["mapping"])

    def test_chinese_natural_language_without_spaces_and_explicit_terms(self):
        self.write("a.md", "# 催化\n## 氧还原\n活性位点促进氧还原反应。\n## 传质\n扩散速率影响电极性能。\n")
        self.build("a.md")
        result = self.query("哪些段落讨论了氧还原反应的活性位点？")
        self.assertEqual(result["hits"][0]["section"], ["催化", "氧还原"])
        self.assertIn("候选", result["notice"])
        result = self.query("where is it", "--terms", "氧还原", "活性位点")
        self.assertIn("氧还原", result["hits"][0]["snippet"])
        self.assertGreater(result["hits"][0]["score"], 0)

    def test_english_case_heading_weight_and_repeated_prose(self):
        self.write("a.md", "# Results\n" + "catalysis " * 2000 + "\n\n# CATALYSIS\nShort evidence.\n")
        self.build("a.md")
        hits = self.query("CaTaLySiS")["hits"]
        self.assertEqual(hits[0]["section"], ["CATALYSIS"])
        self.assertEqual(len(hits), 2)
        scores = [h["score"] for h in hits]
        self.assertGreater(scores[0], scores[1])
        a = tools.query([dict(self.chunks()[0], text="catalysis")], type("Args", (), {"query": "catalysis", "terms": [], "limit": 5})())
        b = tools.query([dict(self.chunks()[0], text="catalysis " * 100)], type("Args", (), {"query": "catalysis", "terms": [], "limit": 5})())
        self.assertEqual(a["hits"][0]["score"], b["hits"][0]["score"])

    def test_frontmatter_fences_math_and_heading_breadcrumbs(self):
        self.write("a.md", "---\n# metadata\ntitle: secretmetadata\n---\n# Parent\nIntro\n\n"
                   "Child\n-----\n```python\n# code fake\n```\n~~~~\n## tilde fake\n~~~~\n"
                   "$$\n# dollar fake\nx=1\n$$\n\\[\n## bracket fake\n\\]\n"
                   "\\begin{align}\n# align fake\nx&=2\n\\end{align}\n## Real\nend\n")
        self.build("a.md")
        chunks = self.chunks()
        self.assertEqual([c["section"] for c in chunks], [["Parent"], ["Parent", "Child"], ["Parent", "Real"]])
        self.assertEqual(chunks[0]["start_line"], 5)
        self.assertEqual(self.query("secretmetadata")["hits"], [])
        self.assertIn("# dollar fake", chunks[1]["text"])
        self.assertIn("\\end{align}", chunks[1]["text"])

    def test_long_math_and_markdown_blocks_read_and_snippet_are_complete(self):
        formula = "$$\n" + "x = oxygen + 1\n\n" * 300 + "$$\n"
        table = "| key | value |\n| --- | --- |\n" + "| oxygen | 1 |\n" * 200
        self.write("a.md", "# Math\n\n" + formula + "\n" + table + "\n")
        self.build("a.md")
        hit = self.query("oxygen")["hits"][0]
        self.assertIn(formula, hit["snippet"])
        read = self.invoke("read", self.out, "--root", self.root, "--id", hit["id"])
        self.assertIn(formula, read["text"])
        self.assertTrue(any(table in c["text"] for c in self.chunks()))
        self.assertEqual(read["text"], self.chunks()[0]["text"])

    def test_no_match_and_limit(self):
        self.write("a.md", "# Alpha\nion transport\n# Beta\nion exchange\n")
        self.build("a.md")
        self.assertEqual(self.query("completelyunrelated")["hits"], [])
        self.assertEqual(self.query("无关词句")["hits"], [])
        self.assertEqual(len(self.query("ion", "--limit", 1)["hits"]), 1)

    def test_stale_query_and_read_verify_every_source(self):
        self.write("a.md", "# Target\noxygen\n")
        path = self.write("b.md", "# Other\nold\n")
        self.build("a.md", "b.md")
        before = (self.out / "index.json").read_bytes()
        cid = self.query("oxygen")["hits"][0]["id"]
        path.write_text("# Other\nnew\n", encoding="utf-8")
        self.assertIn("stale", self.query("oxygen", good=False))
        self.assertIn("stale", self.invoke("read", self.out, "--root", self.root, "--id", cid, good=False))
        self.assertEqual((self.out / "index.json").read_bytes(), before)

    def test_external_chunks_exact_text_pages_and_doc2md_aliases(self):
        text = "# Source\n\noxygen\n\n## Next\nwater\n"
        self.write("sources/a.md", text)
        rows = [{"file": "sources/a.md", "start_line": 1, "end_line": 4,
                 "text": "# Source\n\noxygen\n\n", "pages": [7, 8]},
                {"file": "sources/a.md", "start": 5, "end": 6,
                 "text": "## Next\nwater\n", "pages": [9]}]
        self.build("sources/a.md", extra=self.agent(rows))
        hit = self.query("oxygen")["hits"][0]
        self.assertEqual(hit["pages"], [7, 8])
        self.assertEqual(hit["file"], "sources/a.md")
        self.assertEqual(hit["mapping"], "external:agent/chunks.jsonl")
        read = self.invoke("read", self.out, "--root", self.root, "--id", hit["id"])
        self.assertEqual(read["text"], rows[0]["text"])
        self.assertEqual(read["pages"], [7, 8])
        self.assertEqual(self.chunks()[1]["pages"], [9])

    def test_external_mismatch_range_pages_overlap_and_math_split_rejected(self):
        self.write("a.md", "# A\n$$\nx=1\n$$\n")
        base = {"file": "a.md", "start": 1, "end": 4, "text": "# A\n$$\nx=1\n$$\n", "pages": [1]}
        bad_rows = [[dict(base, text="# A\n$$\nx=2\n$$\n")], [dict(base, start=0)],
                    [dict(base, end=True)], [dict(base, pages=[False])], [base, base],
                    [dict(base, start=3, end=3, text="x=1\n")]]
        for rows in bad_rows:
            with self.subTest(rows=rows):
                self.build("a.md", extra=self.agent(rows), good=False)
                self.assertFalse(self.out.exists())

    def test_external_only_explicit_files_and_fill_unmapped_gaps(self):
        self.write("a.md", "# A\noxygen\n\n# B\nwater\n")
        rows = [{"file": "not-selected.md", "start": 99, "end": 100, "text": "ignored"},
                {"file": "a.md", "start": 4, "end": 5, "text": "# B\nwater\n", "pages": [12]}]
        self.build("a.md", extra=self.agent(rows))
        chunks = self.chunks()
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["pages"], [])
        self.assertEqual(chunks[1]["pages"], [12])
        self.assertEqual({c["file"] for c in chunks}, {"a.md"})

    def test_path_traversal_absolute_files_and_wrong_extensions(self):
        self.write("a.md", "# A\n")
        for name in ("../a.md", "sub/../../a.md", "/tmp/a.md", "C:/a.md", "C:a.md",
                     "\\\\server\\a.md", "sub\\a.md", "a.txt", str(self.root / "a.md")):
            with self.subTest(name=name):
                self.assertIn("relative POSIX", self.build(name, good=False))
                self.assertFalse(self.out.exists())

    def test_existing_output_file_directory_and_source_protected(self):
        path = self.write("a.md", "# Protected\n")
        for out in (self.root, path):
            self.invoke("build", "--root", self.root, "--files", "a.md", "--out", out, good=False)
        self.out.mkdir()
        marker = self.out / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        self.build("a.md", good=False)
        self.assertEqual(marker.read_text(), "keep")
        self.assertEqual(path.read_text(), "# Protected\n")

    def test_output_inside_root_new_directory_and_deterministic_ids(self):
        self.write("a.md", "# A\noxygen\n")
        self.out = self.root / "routing"
        self.build("a.md", "a.md")
        first = self.chunks()
        self.out = self.base / "second"
        self.build("a.md")
        self.assertEqual(self.chunks(), first)

    def test_source_symlink_escape_if_supported(self):
        outside = self.base / "outside.md"
        outside.write_text("# Outside\n", encoding="utf-8")
        try:
            (self.root / "escape.md").symlink_to(outside)
        except OSError:
            self.skipTest("symlink creation not permitted")
        self.assertIn("inside root", self.build("escape.md", good=False))

    def test_missing_source_invalid_utf8_and_unclosed_yaml(self):
        for name, data in (("missing.md", None), ("invalid.md", b"\xff"), ("yaml.md", b"---\na: b\n")):
            with self.subTest(name=name):
                if data is not None:
                    (self.root / name).write_bytes(data)
                self.build(name, good=False)
                self.assertFalse(self.out.exists())

    def test_tampered_chunk_and_unknown_id(self):
        self.write("a.md", "# A\noxygen\n")
        self.build("a.md")
        self.assertIn("unknown chunk", self.invoke("read", self.out, "--root", self.root, "--id", "absent", good=False))
        c = self.chunks()[0]
        for changes in ({"text": "invented text"}, {"pages": [False]}, {"pages": [3]},
                        {"mapping": "invented"}, {"section": ["Invented"]}, {"id": "invented"}):
            with self.subTest(changes=changes):
                (self.out / "chunks.jsonl").write_text(json.dumps(dict(c, **changes)) + "\n", encoding="utf-8")
                self.query("oxygen", good=False)
        (self.out / "chunks.jsonl").write_text(json.dumps(c) + "\n", encoding="utf-8")
        path = self.out / "index.json"
        index = json.loads(path.read_text(encoding="utf-8"))
        index["sources"] *= 2
        path.write_text(json.dumps(index), encoding="utf-8")
        self.assertIn("duplicate source", self.query("oxygen", good=False))

    def test_real_cli_utf8_json_errors_and_python310_syntax(self):
        ast.parse(SCRIPT.read_text(encoding="utf-8"), feature_version=(3, 10))
        self.write("中文.md", "# 中文标题\n氧还原\n")
        args = [sys.executable, str(SCRIPT)]
        built = subprocess.run(args + ["build", "--root", str(self.root), "--files", "中文.md", "--out", str(self.out)], capture_output=True)
        self.assertEqual(built.returncode, 0, built.stderr)
        query = subprocess.run(args + ["query", str(self.out), "--root", str(self.root), "--query", "氧还原"], capture_output=True)
        self.assertEqual(query.returncode, 0, query.stderr)
        self.assertEqual(json.loads(query.stdout.decode("utf-8"))["hits"][0]["section"], ["中文标题"])
        bad = subprocess.run(args + ["query", str(self.out), "--root", str(self.root), "--query", "x", "--limit", "0"], capture_output=True)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn(b"limit must be positive", bad.stderr)


if __name__ == "__main__":
    unittest.main()
