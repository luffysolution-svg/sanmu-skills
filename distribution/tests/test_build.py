import importlib.util
from pathlib import Path
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
                with zipfile.ZipFile(left) as archive:
                    self.assertIsNone(archive.testzip())
                    names = archive.namelist()
                    self.assertTrue(any(name.endswith("/SKILL.md") or name == "SKILL.md" for name in names))
                    self.assertFalse(any(build.BLOCKED_PARTS.intersection(Path(name).parts) for name in names))
            with zipfile.ZipFile(first[0]) as archive:
                self.assertIn("plugin.json", archive.namelist())
                self.assertIn("skills/sanmu-paper-search/LICENSE", archive.namelist())
                self.assertIn("skills/sanmu-paper-reader/requirements.txt", archive.namelist())
            with self.assertRaises(FileExistsError):
                build.build(root / "first")

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
