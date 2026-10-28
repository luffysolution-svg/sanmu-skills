import importlib.util
from fnmatch import fnmatchcase
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import zipfile


SPEC = importlib.util.spec_from_file_location("distribution_build", Path(__file__).resolve().parents[1] / "build.py")
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class DistributionTests(unittest.TestCase):
    def test_ci_artifact_includes_every_checksummed_distribution(self):
        workflow = (build.ROOT / ".github/workflows/distribution.yml").read_text(encoding="utf-8")
        upload = workflow.split("uses: actions/upload-artifact@", 1)[1]
        patterns = [line.strip() for line in upload.split("path: |", 1)[1].splitlines() if line.strip()]
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "dist"
            archives = build.build(output)
            self.assertTrue(any("workbuddy" in path.parts for path in archives))
            self.assertTrue(any("zcode" in path.parts for path in archives))
            self.assertTrue(any(path.suffix == ".tgz" for path in archives))
            paths = ["dist/" + path.relative_to(output).as_posix() for path in archives]
            paths.append("dist/SHA256SUMS")
            for path in paths:
                self.assertTrue(any(len(path.split("/")) == len(pattern.split("/")) and fnmatchcase(path, pattern)
                                    for pattern in patterns), f"CI omits {path}")

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

    def test_workbuddy_adds_required_fields_and_keeps_body(self):
        release, _ = build.validate()
        for skill in release["skills"]:
            original = (build.ROOT / "skills" / skill["name"] / "SKILL.md").read_text(encoding="utf-8")
            result = build.workbuddy_document(original, skill, release)
            source_fields, source_match = build.skill_header(original)
            fields, match = build.skill_header(result)
            self.assertEqual(result[match.end():], original[source_match.end():])
            self.assertEqual(fields["description"], source_fields["description"])
            for key in ("description_zh", "description_en", "version", "author", "display_name", "display_name_en"):
                self.assertTrue(json.loads(fields[key]))

    def test_workbuddy_paid_names_are_separate_from_skillhub_names(self):
        release, _ = build.validate()
        expected = {
            "sanmu-doc2md": ("三木学术文档转Markdown", "Sanmu-Academic-Docs-to-Markdown"),
            "sanmu-paper-reader": ("三木文献精读", "Sanmu-Paper-Reader"),
            "sanmu-paper-search": ("三木文献检索", "Sanmu-Paper-Search"),
        }
        for skill in release["skills"]:
            original = (build.ROOT / "skills" / skill["name"] / "SKILL.md").read_text(encoding="utf-8")
            fields, _ = build.skill_header(build.workbuddy_document(original, skill, release))
            self.assertEqual((json.loads(fields["display_name"]), json.loads(fields["display_name_en"])),
                             expected[skill["name"]])
            skillhub_fields, _ = build.skill_header(build.skillhub_document(original, skill, release))
            self.assertEqual(json.loads(skillhub_fields["displayName"]), skill["displayName"])
            self.assertEqual(fields["name"], skill["name"] + "-paid")
            self.assertEqual(skillhub_fields["name"], skill["name"])
            self.assertEqual(json.loads(fields["version"]), release["workbuddyVersion"])
            self.assertEqual(json.loads(skillhub_fields["version"]), release["version"])

    def test_workbuddy_paid_names_reject_spaces_and_special_characters(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            manifest = root / "distribution/manifest.json"
            release = json.loads(manifest.read_text(encoding="utf-8"))
            for field in ("workbuddyDisplayName", "workbuddyDisplayNameEn"):
                original = release["skills"][0][field]
                for invalid in ("三木 · 文献检索", "Sanmu Paper Search", "Sanmu/Paper", "", None):
                    with self.subTest(field=field, invalid=invalid):
                        release["skills"][0][field] = invalid
                        manifest.write_text(json.dumps(release), encoding="utf-8")
                        with self.assertRaisesRegex(ValueError, "WorkBuddy paid display name"):
                            build.validate(root)
                release["skills"][0][field] = original

    def archive_entries(self, path):
        if path.suffix == ".tgz":
            with tarfile.open(path, "r:gz") as archive:
                return {item.name.removeprefix("package/"): archive.extractfile(item).read()
                        for item in archive.getmembers()}
        with zipfile.ZipFile(path) as archive:
            self.assertIsNone(archive.testzip())
            return {name: archive.read(name) for name in archive.namelist()}

    def assert_clean_entries(self, entries):
        for name in entries:
            with self.subTest(name=name):
                parts = Path(name).parts
                self.assertFalse(build.BLOCKED_PARTS.intersection(part.lower() for part in parts))
                self.assertFalse(any(part == ".env" or part.startswith(".env.") for part in parts))
                self.assertNotIn(Path(name).suffix.lower(), {".pyc", ".pyo"})
                self.assertNotIn(Path(name).name, {"_selftest.py", "ROADMAP.md"})
                self.assertFalse(Path(name).name.startswith("test_"))

    def test_archives_are_complete_deterministic_and_exclude_untracked(self):
        release, files = build.validate()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = build.build(root / "first")
            second = build.build(root / "second")
            self.assertEqual(len(first), 9)
            self.assertEqual(sum(path.suffix == ".zip" for path in first), 8)
            self.assertEqual(sum(path.suffix == ".tgz" for path in first), 1)
            checksums = (root / "first" / "SHA256SUMS").read_bytes()
            self.assertNotIn(b"\r", checksums)
            self.assertEqual(len(checksums.splitlines()), 9)
            for left, right in zip(first, second):
                self.assertEqual(left.read_bytes(), right.read_bytes())
                self.assertIn(release["version"], left.name)
                digest = hashlib.sha256(left.read_bytes()).hexdigest()
                self.assertIn(f"{digest}  {left.relative_to(root / 'first').as_posix()}\n".encode(), checksums)
                entries = self.archive_entries(left)
                self.assertTrue(any(name.endswith("/SKILL.md") or name == "SKILL.md" for name in entries))
                self.assert_clean_entries(entries)
                if left.parent.name in {"skillhub", "workbuddy"}:
                    self.assertFalse(any(Path(name).name == "LICENSE" for name in entries))
                elif left.parent.name == "zcode":
                    self.assertTrue(set(build.ZCODE_SHELL_FILES).issubset(entries))
                    self.assertTrue(set(build.ROOT_RUNTIME_FILES).issubset(entries))
                    self.assertIn("scripts/paper_search.py", entries)
                    self.assertEqual(set(entries), set(build.zcode_sources(files)))
                else:
                    self.assertEqual(set(entries), set(files))
                if left.parent.name not in {"skillhub", "workbuddy"}:
                    for path in files:
                        if path.startswith("skills/"):
                            self.assertEqual(entries[path], (build.ROOT / path).read_bytes())
            with self.assertRaises(FileExistsError):
                build.build(root / "first")

    def test_npm_tarball_has_standard_prefix_permissions_and_file_allowlist(self):
        _, files = build.validate()
        with tempfile.TemporaryDirectory() as temporary:
            archives = build.build(Path(temporary) / "out")
            path = next(path for path in archives if path.suffix == ".tgz")
            with tarfile.open(path, "r:gz") as archive:
                members = archive.getmembers()
                self.assertEqual(len(members), len(files))
                self.assertEqual(len({item.name for item in members}), len(members))
                for member in members:
                    self.assertTrue(member.name.startswith("package/"))
                    self.assertTrue(member.isfile())
                    self.assertNotIn("..", Path(member.name).parts)
                    expected_mode = 0o755 if member.name == "package/scripts/sanmu.mjs" else 0o644
                    self.assertEqual(member.mode, expected_mode)
                    self.assertEqual(member.mtime, build.ARCHIVE_MTIME)
                    self.assertEqual((member.uid, member.gid, member.uname, member.gname), (0, 0, "", ""))
                self.assertEqual({item.name[8:] for item in members}, set(build.npm_files(build.ROOT, files)))
            entries = self.archive_entries(path)
            package = json.loads(entries["package.json"])
            self.assertEqual(package["bin"]["sanmu"], "./scripts/sanmu.mjs")
            self.assertEqual(package["pi"]["skills"], ["./skills"])
            self.assertEqual(package["pi"]["extensions"], ["./extensions/pi.ts"])
            self.assertIn(package["dsh"]["bundle"]["patch"].removeprefix("./"), entries)

    def test_three_complete_skills_references_and_host_entrypoints_survive_archiving(self):
        release, _ = build.validate()
        expected_skills = {f"skills/{skill['name']}/SKILL.md" for skill in release["skills"]}
        self.assertEqual(len(expected_skills), 3)
        with tempfile.TemporaryDirectory() as temporary:
            archives = build.build(Path(temporary) / "out")
            for path in archives:
                if path.parent.name in {"skillhub", "workbuddy"}:
                    continue
                entries = self.archive_entries(path)
                self.assertEqual({name for name in entries if name.endswith("/SKILL.md")}, expected_skills)
                extracted = Path(temporary) / path.stem
                for name, data in entries.items():
                    destination = extracted / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(data)
                build.validate_runtime_references(extracted, entries, [skill["name"] for skill in release["skills"]])
                for name in ("sanmu-doc2md", "sanmu-paper-reader"):
                    self.assertIn(f"skills/{name}/requirements.txt", entries)
                if path.parent.name == "zcode":
                    for manifest in (".zcode-plugin/plugin.json", ".claude-plugin/plugin.json"):
                        metadata = json.loads(entries[manifest])
                        self.assertEqual(metadata["skills"], "./skills")
                        self.assertEqual(metadata["version"], release["version"])
                        build.validate_mcp(metadata, entries, manifest, "CLAUDE_PLUGIN_ROOT", "scripts/mcp-server.mjs")
                    self.assertIn("assets/panel.html", entries)
                else:
                    build.validate_hosts(extracted, release, entries)
                    self.assertTrue(set(build.PLUGIN_FILES).issubset(entries))
                    for name in ("plugin.json", ".claude-plugin/plugin.json", ".codex-plugin/plugin.json", "package.json"):
                        self.assertEqual(json.loads(entries[name])["version"], release["version"])

    def fixture(self, root):
        _, files = build.validate()
        extra = ["distribution/manifest.json"]
        extra += [f"{build.ZCODE_SHELL_DIR}/{name}" for name in build.ZCODE_SHELL_FILES]
        for name in files + extra:
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(build.ROOT / name, destination)
        subprocess.run(["git", "init", "--quiet", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "core.autocrlf", "false"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)

    def track(self, root, name, text="development fixture"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "--force", name], check=True)
        return path

    def test_zcode_shell_is_required_tracked_and_versioned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            panel = root / build.ZCODE_SHELL_DIR / "assets/panel.html"
            original = panel.read_text(encoding="utf-8")
            panel.write_text(original.replace('class="brand-sub">v', 'class="brand-sub">v0.0.0-'), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "server/panel version"):
                build.validate(root)
            panel.write_text(original, encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "update-index", "--force-remove", "--", f"{build.ZCODE_SHELL_DIR}/assets/panel.html"],
                           check=True, stdout=subprocess.PIPE)
            with self.assertRaisesRegex(ValueError, "must be tracked"):
                build.validate(root)
            subprocess.run(["git", "-C", str(root), "add", "--", f"{build.ZCODE_SHELL_DIR}/assets/panel.html"], check=True)
            panel.unlink()
            with self.assertRaisesRegex(ValueError, "Missing or invalid ZCode|not a regular file"):
                build.validate(root)

    def test_zcode_archive_uses_identical_shared_engine_files(self):
        _, files = build.validate()
        with tempfile.TemporaryDirectory() as temporary:
            archives = build.build(Path(temporary) / "out")
            zcode = next(path for path in archives if path.parent.name == "zcode")
            with zipfile.ZipFile(zcode) as archive:
                for target, source in build.zcode_sources(files).items():
                    self.assertEqual(archive.read(target), (build.ROOT / source).read_bytes())
                for path in files:
                    if path.startswith("skills/sanmu-paper-search/scripts/"):
                        suffix = path.removeprefix("skills/sanmu-paper-search/scripts/")
                        self.assertEqual(archive.read("scripts/" + suffix), archive.read(path))
                metadata = json.loads(archive.read(".zcode-plugin/plugin.json"))
                self.assertEqual(metadata["version"], build.read_json(build.ROOT / "distribution/manifest.json")["version"])

    def test_all_host_and_marketplace_versions_must_match_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            paths = ["plugin.json", ".claude-plugin/plugin.json", ".codex-plugin/plugin.json", "package.json",
                     f"{build.ZCODE_SHELL_DIR}/.zcode-plugin/plugin.json",
                     f"{build.ZCODE_SHELL_DIR}/.claude-plugin/plugin.json",
                     f"{build.ZCODE_SHELL_DIR}/package.json"]
            for name in paths + [".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json"]:
                with self.subTest(path=name):
                    path = root / name
                    original = path.read_text(encoding="utf-8")
                    data = json.loads(original)
                    if "marketplace" in name:
                        data["plugins"][0]["version"] = "0.0.0"
                    else:
                        data["version"] = "0.0.0"
                    path.write_text(json.dumps(data), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "version differs"):
                        build.validate(root)
                    path.write_text(original, encoding="utf-8")
            path = root / "distribution/manifest.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["workbuddyVersion"] = "0.0.0"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "WorkBuddy version differs"):
                build.validate(root)

    def test_root_host_schemas_and_paths_are_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            mutations = [
                ("plugin.json", lambda data: data.update({"$schema": "https://example.org/invalid"})),
                ("mcp.json", lambda data: data.update({"$schema": "https://example.org/invalid"})),
                ("mcp.json", lambda data: next(iter(data["mcpServers"].values())).update({"args": ["missing.mjs"]})),
                (".mcp.json", lambda data: next(iter(data["mcpServers"].values())).update({"command": "python"})),
                (".claude-plugin/plugin.json", lambda data: data.update({"skills": "./skills/sanmu-paper-search"})),
                (".codex-plugin/plugin.json", lambda data: data.update({"mcpServers": "./.mcp.json"})),
                ("package.json", lambda data: data.update({"bin": {"sanmu": "./scripts/missing.mjs"}})),
                ("package.json", lambda data: data.update({"main": "../outside.mjs"})),
                ("package.json", lambda data: data["pi"].update({"skills": ["./skills/sanmu-paper-search"]})),
                ("package.json", lambda data: data["pi"].update({"extensions": ["./extensions/missing.ts"]})),
                ("package.json", lambda data: data["dsh"]["bundle"].update({"patch": "./missing.yml"})),
                ("package.json", lambda data: data.update({"files": ["scripts", "skills"]})),
                (f"{build.ZCODE_SHELL_DIR}/package.json", lambda data: data.update({"main": "./missing.mjs"})),
            ]
            for name, mutate in mutations:
                with self.subTest(path=name, mutation=mutate):
                    path = root / name
                    original = path.read_text(encoding="utf-8")
                    data = json.loads(original)
                    mutate(data)
                    path.write_text(json.dumps(data), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        build.validate(root)
                    path.write_text(original, encoding="utf-8")
            patch = root / "cordis.patch.yml"
            patch.write_text('- insert:\n    - name: "./missing.mjs"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Missing packaged path"):
                build.validate(root)

    def test_skillhub_limits_are_independent_of_project_budget(self):
        build.validate_skillhub_budget({f"{index}.txt": b"" for index in range(200)}, "demo")
        build.validate_skillhub_budget({"data.txt": b"x" * (10 * 1024 * 1024)}, "demo")
        for entries in ({f"{index}.txt": b"" for index in range(201)},
                        {"data.txt": b"x" * (10 * 1024 * 1024 + 1)}):
            with self.assertRaisesRegex(ValueError, "SkillHub package limit exceeded"):
                build.validate_skillhub_budget(entries, "demo")

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
                for path in self.archive_entries(archive):
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
            package_path = root / "package.json"
            package = json.loads(package_path.read_text(encoding="utf-8"))
            package["files"].append("skills/sanmu-doc2md/references/local.md")
            package_path.write_text(json.dumps(package), encoding="utf-8")
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

    def test_zcode_panel_and_server_contracts_for_graph_timeline_and_clipboard(self):
        panel_text = (build.ROOT / build.ZCODE_SHELL_DIR / "assets/panel.html").read_text(encoding="utf-8")
        server_text = (build.ROOT / build.ZCODE_SHELL_DIR / "scripts/mcp-server.mjs").read_text(encoding="utf-8")
        self.assertNotIn("btnReheatGraph", panel_text)
        self.assertNotIn("激发流动", panel_text)
        self.assertNotIn('<div class="snippet-wrap" onclick="event.stopPropagation();">', panel_text)
        for token in (
            "legacyExecCopy",
            "selectElementContents",
            "copy_clipboard",
            "graphTimelineChronicle",
            "renderTimelineChronicle",
            "btnGraphNodeOpenDetail",
            "btnGraphTooltipInspect",
            "drwCopySingleCite",
        ):
            self.assertIn(token, panel_text)
        for token in ("copyTextToSystemClipboard", 'name: "copy_clipboard"', "integratedRunIds"):
            self.assertIn(token, server_text)


if __name__ == "__main__":
    unittest.main()
