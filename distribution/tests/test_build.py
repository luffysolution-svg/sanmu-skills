import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile


SPEC = importlib.util.spec_from_file_location("distribution_build", Path(__file__).resolve().parents[1] / "build.py")
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class DistributionTests(unittest.TestCase):
    def test_skillhub_keeps_body_and_portable_name(self):
        release, _ = build.validate()
        for skill in release["skills"]:
            original = (build.ROOT / "skills" / skill["name"] / "SKILL.md").read_text(encoding="utf-8")
            result = build.skillhub_document(original, skill, release)
            source_fields, source_match = build.skill_header(original)
            fields, match = build.skill_header(result)
            self.assertEqual(result[match.end():], original[source_match.end():])
            self.assertEqual(fields["name"], source_fields["name"])
            for key in ("slug", "displayName", "version", "summary", "license"):
                self.assertIn(key, fields)
            self.assertNotIn("slug", source_fields)

    def test_archives_are_complete_deterministic_and_exclude_untracked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = build.build(root / "first")
            second = build.build(root / "second")
            self.assertEqual(len(first), 4)
            for left, right in zip(first, second):
                self.assertEqual(left.read_bytes(), right.read_bytes())
                is_skillhub = "skillhub" in left.parts
                with zipfile.ZipFile(left) as archive:
                    self.assertIsNone(archive.testzip())
                    names = archive.namelist()
                    self.assertTrue(any(name.endswith("/SKILL.md") or name == "SKILL.md" for name in names))
                    self.assertFalse(any(build.BLOCKED_PARTS.intersection(Path(name).parts) for name in names))
                    if is_skillhub:
                        self.assertFalse(any(Path(name).name == "LICENSE" for name in names))
            with zipfile.ZipFile(first[0]) as archive:
                self.assertIn("plugin.json", archive.namelist())
                self.assertIn("skills/sanmu-paper-search/LICENSE", archive.namelist())
                self.assertIn("skills/sanmu-paper-reader/requirements.txt", archive.namelist())
            with self.assertRaises(FileExistsError):
                build.build(root / "first")

    def fixture(self, root):
        _, files = build.validate()
        extra = ["distribution/manifest.json", ".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json"]
        for name in files + extra:
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(build.ROOT / name, destination)
        subprocess.run(["git", "init", "--quiet", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)

    def track(self, root, name, text="development fixture"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "--force", name], check=True)
        return path

    def test_development_trees_are_excluded_even_if_tracked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            paths = ["tests/sanmu-doc2md/test_demo.py", "examples/sanmu-doc2md/demo.md",
                     "docs/showcases/sanmu-paper-reader/images/demo.png",
                     "skills/sanmu-doc2md/tests/test_demo.py",
                     "skills/sanmu-doc2md/examples/demo.md", "skills/sanmu-doc2md/ROADMAP.md"]
            for path in paths:
                self.track(root, path)
            _, selected = build.validate(root)
            self.assertTrue(set(paths).isdisjoint(selected))
            for archive in build.build(root / "out", root):
                with zipfile.ZipFile(archive) as package:
                    for path in package.namelist():
                        self.assertFalse(build.DEVELOPMENT_PARTS.intersection(Path(path).parts))
                        self.assertNotIn("ROADMAP.md", Path(path).parts)

    def test_nested_test_cache_and_selftest_inputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            for suffix in ["scripts/tests/helper.py", "scripts/examples/demo.json", "scripts/cache/data.json",
                           "scripts/__pycache__/module.pyc", "scripts/_selftest.py", "scripts/test_demo.py", "scripts/ROADMAP.md"]:
                with self.subTest(suffix=suffix):
                    path = self.track(root, "skills/sanmu-doc2md/" + suffix)
                    with self.assertRaisesRegex(ValueError, "must not be packaged"):
                        build.validate(root)
                    path.unlink()
                    subprocess.run(["git", "-C", str(root), "add", "-u"], check=True)

    def test_missing_tracked_runtime_module_is_not_silently_omitted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            path = root / "skills/sanmu-doc2md/scripts/normalize_math.py"
            path.unlink()
            self.assertIn("skills/sanmu-doc2md/scripts/normalize_math.py", build.tracked_files(root))
            with self.assertRaisesRegex(ValueError, "not a regular file"):
                build.validate(root)

    def test_standard_frontmatter_rejects_platform_fields_duplicates_and_bad_values(self):
        for header in ["name: demo\ndescription: okay\nslug: demo", "name: demo\nname: demo\ndescription: okay",
                       "name: demo\ndescription: null", "name: demo\ndescription: <unsafe>"]:
            with self.subTest(header=header), self.assertRaises(ValueError):
                build.validate_standard_header("---\n" + header + "\n---\nbody", "demo")
        build.validate_standard_header('---\nname: "demo"\ndescription: "valid description"\n---\nbody', "demo")

    def test_missing_runtime_reference_fails_including_inline_and_nested_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            document = root / "skills/sanmu-doc2md/SKILL.md"
            original = document.read_text(encoding="utf-8")
            for link in ["[missing](references/not-present.md)", "`references/not-present.md`",
                         "[escape](../../README.md)", "[missing][target]\n[target]: references/not-present.md"]:
                with self.subTest(link=link):
                    document.write_text(original + "\n" + link, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "Runtime"):
                        build.validate(root)
            document.write_text(original, encoding="utf-8")
            reference = root / "skills/sanmu-doc2md/references/book-workflow.md"
            reference.write_text("[missing](missing.md)", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Runtime reference"):
                build.validate(root)

    def test_online_links_code_examples_wikilinks_and_chemical_states_are_ignored(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            document = root / "skills/sanmu-doc2md/SKILL.md"
            with document.open("a", encoding="utf-8") as stream:
                stream.write('\n[online](https://example.org/no-file.md)\n[[note]]\n[Fe(CN)6](aq)\n'
                             '`![example](images/missing.png)`\n  ```markdown\n  ![example](images/missing.png)\n  ```\n'
                             '~~~markdown\n![example](images/missing.png)\n~~~\n')
            build.validate(root)

    def test_untracked_runtime_reference_and_project_budget_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            document = root / "skills/sanmu-doc2md/SKILL.md"
            with document.open("a", encoding="utf-8") as stream:
                stream.write("\n[untracked](references/local.md)\n")
            untracked = root / "skills/sanmu-doc2md/references/local.md"
            untracked.write_text("not staged", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Runtime reference"):
                build.validate(root)
            subprocess.run(["git", "-C", str(root), "add", str(untracked)], check=True)
            manifest = root / "distribution/manifest.json"
            release = json.loads(manifest.read_text(encoding="utf-8"))
            for budget in [{"maxFiles": 1, "maxBytes": 5242880}, {"maxFiles": 200, "maxBytes": 1}]:
                release["packageBudget"] = budget
                manifest.write_text(json.dumps(release), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Project package budget exceeded"):
                    build.validate(root)

    def test_untracked_secret_is_not_selected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            (root / "tracked.txt").write_text("public", encoding="utf-8")
            (root / ".env").write_text("PRIVATE_TEST_VALUE=local-only", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True)
            self.assertEqual(build.tracked_files(root), ["tracked.txt"])


if __name__ == "__main__":
    unittest.main()
