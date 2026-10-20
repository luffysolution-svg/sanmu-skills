"""Offline publisher preflight and transactional replacement regressions."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = (Path(__file__).resolve().parents[2] / 'skills' / 'sanmu-doc2md')
sys.path.insert(0, str(ROOT / 'scripts'))
import build_support as support
from build_support import OWNERSHIP, Publisher, contained, digest, load_book


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='test-publisher-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def write(self, root, files):
        for name, data in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def snapshot(self, root):
        return {p.relative_to(root).as_posix(): p.read_bytes()
                for p in root.rglob('*') if p.is_file()} if root.exists() else {}

    def fixture(self, base, *, existing=True):
        output, stage = base / 'output', base / 'stage'
        self.write(stage, {'00-目录.md': b'new navigation\n',
                           'chapter.md': b'new chapter\n',
                           'new.md': b'new file\n',
                           'images/new.png': b'new image\x00\xff',
                           'build-report.json': b'{"chapters": [], "review": {}, "new": true}\n'})
        publisher = Publisher(output)
        if existing:
            old = {'00-目录.md': b'old navigation\r\n', 'chapter.md': b'old chapter\x00\xff'}
            self.write(output, {**old, 'manual.md': b'manual text',
                                '.unrelated.tmp': b'keep unrelated temporary file'})
            self.write(publisher.state_root, {
                OWNERSHIP: (json.dumps({'version': 1, 'files': {
                    name: digest(data) for name, data in old.items()}}, indent=4) + '\r\n').encode(),
                'build-report.json': b'{"chapters":[], "review":{}}\r\n',
                '.unrelated.tmp': b'keep state temporary file'})
            publisher = Publisher(output)
        return publisher, stage

    @unittest.skipUnless(os.name == 'nt', 'Windows extended path regression')
    def test_long_paths_publish_rebuild_and_detect_manual_edit(self):
        from portable_paths import safe_path, walk_files
        root = self.base / ('long-' + 'a' * 100) / ('b' * 100) / ('c' * 60)
        output, stage = root / 'output', self.base / 'long-stage'
        self.write(stage, {'00-目录.md': b'navigation', 'chapter.md': b'content'})
        publisher = Publisher(output)
        publisher.publish(stage)
        self.assertEqual(safe_path(output / 'chapter.md').read_bytes(), b'content')
        self.assertEqual(len(walk_files(output, '*.md')), 2)
        Publisher(output).publish(stage)
        safe_path(output / 'chapter.md').write_bytes(b'user edit')
        with self.assertRaisesRegex(ValueError, '人工修改'):
            Publisher(output).publish(stage)
        self.assertEqual(safe_path(output / 'chapter.md').read_bytes(), b'user edit')
        import shutil
        shutil.rmtree(safe_path(self.base / ('long-' + 'a' * 100)))

    def test_reserved_chapter_root_names_are_casefolded(self):
        raw = self.base / 'raw'
        self.write(raw, {'part/book_content_list.json': b'[]'})
        manifest = {'pages': 1, 'parts': [{'id': 'part', 'start_page': 1, 'end_page': 1}]}
        for name in ('00-目录.MD', '00-目录.MD/chapter.md',
                     'Agent/chapter.md', 'AGENT/chapter.md', 'aGeNt/chapter.md',
                     'IMAGES/chapter.md', 'Build-Report.JSON/chapter.md',
                     '.BUILD-OWNERSHIP.JSON/chapter.md'):
            with self.subTest(name=name):
                sections = [{'id': 'ch', 'start': 1, 'end': 1, 'title': 'Chapter', 'file': name}]
                with self.assertRaisesRegex(ValueError, '保留'):
                    load_book(raw, manifest, sections, None)
        self.assertEqual(load_book(raw, manifest, [
            {'id': 'ch', 'start': 1, 'end': 1, 'title': 'Chapter', 'file': 'chapters/AGENT.md'}], None), [])

    def test_state_ancestor_file_conflicts_precede_all_delivery_writes(self):
        for depth in (0, 1, 3):
            with self.subTest(depth=depth):
                base = self.base / str(depth)
                publisher, stage = self.fixture(base)
                blocker = base / 'existing_file'
                blocker.write_bytes(b'do not touch')
                publisher = Publisher(publisher.root, state_root=blocker.joinpath(*(['state'] * depth)))
                before = self.snapshot(publisher.root)
                owned_before = publisher.owned.copy()
                with patch.object(support.os, 'replace') as replace:
                    with self.assertRaisesRegex(ValueError, '不是目录'):
                        publisher.publish(stage)
                    replace.assert_not_called()
                self.assertEqual(self.snapshot(publisher.root), before)
                self.assertEqual(publisher.owned, owned_before)
                self.assertEqual(blocker.read_bytes(), b'do not touch')

    def test_state_file_directory_conflicts_precede_delivery_writes(self):
        for name in (OWNERSHIP, 'build-report.json'):
            with self.subTest(name=name):
                publisher, stage = self.fixture(self.base / name)
                (publisher.state_root / name).unlink()
                (publisher.state_root / name).mkdir()
                self.write(publisher.state_root / name, {'manual': b'keep'})
                output_before = self.snapshot(publisher.root)
                state_before = self.snapshot(publisher.state_root)
                with patch.object(support.os, 'replace') as replace:
                    with self.assertRaisesRegex(ValueError, '构建状态不是文件'):
                        publisher.publish(stage)
                    replace.assert_not_called()
                self.assertEqual(self.snapshot(publisher.root), output_before)
                self.assertEqual(self.snapshot(publisher.state_root), state_before)

    def test_replace_failure_restores_every_byte_and_memory(self):
        # Four delivery files, report, then ownership. Fail every commit position
        # with and without a previous build, including an error after replacement.
        real_replace = os.replace
        for existing in (False, True):
            for after_replace in (False, True):
                for fail_at in range(1, 7):
                    with self.subTest(existing=existing, after_replace=after_replace, fail_at=fail_at):
                        base = self.base / f'{existing}-{after_replace}-{fail_at}'
                        publisher, stage = self.fixture(base, existing=existing)
                        output_before = self.snapshot(publisher.root)
                        state_before = self.snapshot(publisher.state_root)
                        owned_before = publisher.owned.copy()
                        owned_object = publisher.owned
                        existence = (publisher.root.exists(), publisher.state_root.exists())
                        calls = 0

                        def failing_replace(source, target):
                            nonlocal calls
                            calls += 1
                            self.assertEqual(Path(source).parent, Path(target).parent)
                            if calls == fail_at:
                                if after_replace:
                                    real_replace(source, target)
                                raise OSError('injected replacement failure')
                            return real_replace(source, target)

                        with patch.object(support.os, 'replace', side_effect=failing_replace):
                            with self.assertRaisesRegex(OSError, 'injected replacement failure'):
                                publisher.publish(stage)
                        self.assertEqual(self.snapshot(publisher.root), output_before)
                        self.assertEqual(self.snapshot(publisher.state_root), state_before)
                        self.assertEqual(publisher.owned, owned_before)
                        self.assertIs(publisher.owned, owned_object)
                        self.assertEqual((publisher.root.exists(), publisher.state_root.exists()), existence)
                        self.assertEqual(set(base.rglob('*.tmp')),
                                         set(base.rglob('.unrelated.tmp')))

    def test_default_success_keeps_state_outside_delivery_and_can_republish(self):
        publisher, stage = self.fixture(self.base, existing=False)
        publisher.publish(stage)
        self.assertEqual({p.name for p in publisher.root.iterdir()},
                         {'00-目录.md', 'chapter.md', 'new.md', 'images'})
        delivered = self.snapshot(publisher.root)
        self.assertEqual(delivered, {name: data for name, data in self.snapshot(stage).items()
                                    if name != 'build-report.json'})
        self.assertEqual((publisher.state_root / 'build-report.json').read_bytes(),
                         (stage / 'build-report.json').read_bytes())
        self.assertEqual(publisher.owned, {name: digest(data) for name, data in delivered.items()})
        self.assertEqual(json.loads((publisher.state_root / OWNERSHIP).read_bytes())['files'], publisher.owned)
        (stage / 'chapter.md').write_bytes(b'another version')
        Publisher(publisher.root).publish(stage)
        self.assertEqual((publisher.root / 'chapter.md').read_bytes(), b'another version')
        self.assertEqual(list(self.base.rglob('*.tmp')), [])

    def test_output_conflict_preflight_does_not_change_delivery(self):
        publisher, stage = self.fixture(self.base)
        (publisher.root / 'new.md').write_bytes(b'user file')
        before = self.snapshot(publisher.root)
        state_before = self.snapshot(publisher.state_root)
        with self.assertRaisesRegex(ValueError, '非生成文件'):
            publisher.publish(stage)
        self.assertEqual(self.snapshot(publisher.root), before)
        self.assertEqual(self.snapshot(publisher.state_root), state_before)

    def test_symlink_targets_remain_rejected_by_contained(self):
        outside = self.base / 'outside'
        outside.mkdir()
        link = self.base / 'probe'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Creating symlinks requires permission on this platform')
        link.unlink()
        publisher, stage = self.fixture(self.base / 'case')
        (publisher.root / 'images').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '链接'):
            contained(publisher.root, 'images/new.png')
        before = self.snapshot(publisher.root)
        with self.assertRaisesRegex(ValueError, '链接'):
            publisher.publish(stage)
        self.assertEqual(self.snapshot(publisher.root), before)
        self.assertEqual(list(outside.iterdir()), [])

    def test_state_symlinks_and_symlink_ancestors_fail_before_delivery(self):
        outside = self.base / 'outside'
        outside.mkdir()
        probe = self.base / 'probe'
        try:
            probe.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Creating symlinks requires permission on this platform')
        probe.unlink()
        for name in (OWNERSHIP, 'build-report.json', 'ancestor'):
            with self.subTest(name=name):
                publisher, stage = self.fixture(self.base / name)
                if name == 'ancestor':
                    link = publisher.state_root.parent / 'state-link'
                    link.symlink_to(outside, target_is_directory=True)
                    publisher.state_root = link / 'nested' / 'state'
                else:
                    target = outside / name
                    target.write_bytes(b'user data')
                    (publisher.state_root / name).unlink()
                    (publisher.state_root / name).symlink_to(target)
                before = self.snapshot(publisher.root)
                with self.assertRaisesRegex(ValueError, '链接'):
                    publisher.publish(stage)
                self.assertEqual(self.snapshot(publisher.root), before)
                if name != 'ancestor':
                    self.assertEqual(target.read_bytes(), b'user data')


if __name__ == '__main__':
    unittest.main()
