import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "sanmu-paper-search" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _rate_limit
from _rate_limit import RateLimiter


class LockDirTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        patcher = mock.patch.object(_rate_limit.tempfile, "gettempdir", return_value=str(self.root))
        patcher.start()
        self.addCleanup(patcher.stop)

    def limiter(self, directory):
        return RateLimiter(directory, clock=lambda: 0.0, sleep=lambda _: None)

    def test_owned_directory_is_used(self):
        primary = self.root / "shared"
        primary.mkdir()
        uid = os.lstat(primary).st_uid
        with mock.patch.object(_rate_limit.os, "getuid", return_value=uid, create=True):
            limiter = self.limiter(primary)
            limiter.wait("s2", "key")
            self.assertEqual(limiter._lock_dir(), primary)
        self.assertEqual(len(list(primary.glob("*.lock"))), 1)

    def test_foreign_owned_directory_falls_back_without_crashing(self):
        primary = self.root / "shared"
        primary.mkdir()
        foreign = os.lstat(primary).st_uid + 4242
        with mock.patch.object(_rate_limit.os, "getuid", return_value=foreign, create=True):
            limiter = self.limiter(primary)
            limiter.wait("s2", "key")
            chosen = limiter._lock_dir()
        self.assertNotEqual(chosen, primary)
        self.assertFalse(list(primary.glob("*.lock")))
        self.assertTrue(list(chosen.glob("*.lock")))

    @unittest.skipUnless(hasattr(os, "getuid") and hasattr(os, "symlink"), "POSIX only")
    def test_symlinked_directory_is_rejected(self):
        real = self.root / "elsewhere"
        real.mkdir()
        link = self.root / "shared"
        os.symlink(real, link)
        limiter = self.limiter(link)
        limiter.wait("s2", "key")
        self.assertEqual(limiter._lock_dir(), self.root / f"sanmu-paper-search-uid{os.getuid()}")
        self.assertFalse(list(real.glob("*.lock")))
        self.assertEqual(os.stat(limiter._lock_dir()).st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
