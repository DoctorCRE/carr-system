#!/usr/bin/env python3
"""lib/loadpy must load modules that use dataclasses, which look themselves up in sys.modules."""
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from lib.loadpy import load_module_from_path  # noqa: E402

DATACLASS_MODULE = '''
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class Point:
    x: int
'''


class LoadPyTests(unittest.TestCase):
    def test_dataclass_module_loads(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'hyphen-name.py'
            path.write_text(DATACLASS_MODULE)
            module = load_module_from_path('loadpy_dataclass_probe', str(path))
            self.assertEqual(module.Point(3).x, 3)
            self.assertIs(sys.modules['loadpy_dataclass_probe'], module)

    def test_missing_path_names_the_file(self):
        with self.assertRaisesRegex((ImportError, OSError), 'no-such-file.py'):
            load_module_from_path('loadpy_missing_probe', '/nonexistent/no-such-file.py')
        self.assertNotIn('loadpy_missing_probe', sys.modules)

    def test_failed_load_leaves_no_half_module(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'broken.py'
            path.write_text('raise RuntimeError("boom")\n')
            with self.assertRaises(RuntimeError):
                load_module_from_path('loadpy_broken_probe', str(path))
            self.assertNotIn('loadpy_broken_probe', sys.modules)


if __name__ == '__main__':
    unittest.main()
