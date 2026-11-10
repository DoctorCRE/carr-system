#!/usr/bin/env python3
"""Exercise child-output cleanup failures only inside disposable checkout copies."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import disposable_pg_fixture as pg


class CleanupSafety(unittest.TestCase):
    def test_parser_rejects_untrusted_roots(self):
        with tempfile.TemporaryDirectory(prefix='cleanup-parser-') as directory:
            base = Path(directory)
            valid = base / 'killed-parent-valid'
            valid.mkdir()
            file = base / 'killed-parent-file'
            file.touch()
            link = base / 'killed-parent-link'
            link.symlink_to(Path.home(), target_is_directory=True)
            ancestor = base / 'alias'
            ancestor.symlink_to(Path.home(), target_is_directory=True)
            invalid = ['', ' ', '.', 'relative', str(Path.home()), str(ROOT),
                       str(base), str(base / 'killed-parent-missing'), str(file),
                       str(link), str(ancestor / 'killed-parent-escape')]
            for line in invalid:
                with self.subTest(line=line), self.assertRaises(ValueError):
                    pg.fixture_root_from_line(line, 'killed-parent-')
            self.assertEqual(pg.fixture_root_from_line(str(valid) + '\n', 'killed-parent-'), valid.resolve())
            with self.assertRaises(ValueError):
                pg.fixture_root_from_line(str(valid), '')

    def consumer_refuses_empty_output(self, consumer):
        with tempfile.TemporaryDirectory(prefix='cleanup-checkout-') as directory:
            checkout = Path(directory) / 'checkout'
            checkout.mkdir()
            for folder in ('lib', 'ops', 'tools'):
                (checkout / folder).mkdir()
                for source in (ROOT / folder).glob('*.py'):
                    shutil.copyfile(source, checkout / folder / source.name)
            for relative in ('mcp-server/test', 'bin'):
                (checkout / relative).mkdir(parents=True)
                for source in (ROOT / relative).iterdir():
                    if source.is_file() and not source.is_symlink():
                        shutil.copyfile(source, checkout / relative / source.name)
            shutil.copyfile(ROOT / 'run.sh', checkout / 'run.sh')
            sentinel = checkout / 'checkout-must-survive'
            sentinel.write_text('owned checkout\n')
            wrapper = r'''
import runpy, subprocess, sys
from unittest.mock import patch
real_popen = subprocess.Popen
def empty_child(args, *positional, **kwargs):
    if '-c' in args and ('killed-parent-' in args[args.index('-c') + 1]
                         or 'local_pg_ci_child' in args[args.index('-c') + 1]):
        args = [sys.executable, '-c', 'pass']
    return real_popen(args, *positional, **kwargs)
with patch('subprocess.Popen', side_effect=empty_child):
    try:
        if sys.argv[1] == 'fixture':
            module = runpy.run_path('tools/test-disposable-pg-fixture.py')
            module['KilledParent']().kill_parent(False)
        else:
            runpy.run_path('ops/local-pg-ci-selftest.py', run_name='__main__')
    except ValueError:
        print('REFUSED EMPTY FIXTURE ROOT')
    else:
        raise AssertionError('empty child output was accepted')
'''
            result = subprocess.run([sys.executable, '-c', wrapper, consumer], cwd=checkout,
                                    capture_output=True, text=True, timeout=30,
                                    env={**os.environ, 'PYTHONPATH': str(checkout)})
            self.assertTrue(sentinel.is_file(), f'{consumer} deleted the checkout')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('REFUSED EMPTY FIXTURE ROOT', result.stdout)

    def test_fixture_consumer_preserves_checkout_on_empty_output(self):
        self.consumer_refuses_empty_output('fixture')

    def test_local_ci_consumer_preserves_checkout_on_empty_output(self):
        self.consumer_refuses_empty_output('local-ci')


if __name__ == '__main__':
    unittest.main()
