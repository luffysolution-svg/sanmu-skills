#!/usr/bin/env python
"""Run all test_*.py regression tests; assertion failures fail CI."""
from pathlib import Path
import sys
import unittest


def main() -> None:
    tests = Path(__file__).resolve().parent
    suite = unittest.defaultTestLoader.discover(str(tests), pattern='test_*.py')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == '__main__':
    main()
