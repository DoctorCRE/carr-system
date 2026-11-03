#!/usr/bin/env python3
"""Neon authentication regressions using a local provider executable only."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class NeonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.marker = self.home / 'calls'
        self.browser = self.home / 'browser'
        self.fake = self.home / 'neonctl'
        self.fake.write_text('#!' + sys.executable + '''
import os
from pathlib import Path
Path(os.environ['CALLS']).open('a').write('call\\n')
if not os.environ.get('NEON_API_KEY', '').strip():
    Path(os.environ['BROWSER']).touch()
    raise SystemExit(1)
print('fixture-dsn')
''')
        self.fake.chmod(0o755)
        self.env = dict(HOME=str(self.home), PATH=str(self.home) + os.pathsep + os.defpath,
                        CALLS=str(self.marker), BROWSER=str(self.browser))
        self.patch = patch.dict(os.environ, self.env, clear=True)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.tap = load('test_neon_db_tap', 'tools/db-tap.py')
        self.tap.NEONCTL = str(self.fake)

    def test_missing_key_never_starts_provider(self):
        for invoke in (lambda: self.tap.dsn(),
                       lambda: self.tap._project_id_by_name('fixture', dict(os.environ)),
                       lambda: self.tap._staging_runtime_target(dict(os.environ))):
            with self.assertRaises(SystemExit) as error:
                invoke()
            self.assertIn('~/.config/carr/db.env', str(error.exception))
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.browser.exists())

    def test_present_key_preserves_output_and_cache(self):
        os.environ['NEON_API_KEY'] = 'synthetic-key'
        self.assertEqual(self.tap.dsn(), 'fixture-dsn')
        self.assertEqual(self.tap.dsn(), 'fixture-dsn')
        self.assertEqual(self.marker.read_text().splitlines(), ['call'])
        self.assertFalse(self.browser.exists())

    def test_stored_key_is_used(self):
        config = self.home / '.config/carr'
        config.mkdir(parents=True)
        (config / 'db.env').write_text('NEON_API_KEY=synthetic-stored-key\n')
        self.assertEqual(self.tap.dsn(), 'fixture-dsn')
        self.assertFalse(self.browser.exists())


if __name__ == '__main__':
    unittest.main()
