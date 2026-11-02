#!/usr/bin/env python3
"""Exercise census enablement with private synthetic settings and snapshots."""
from pathlib import Path
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import outlook_intake as intake
from outlook_test_support import write_settings


class CensusRequestTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'
        self.repo = Path(self.temp.name) / 'repo'
        write_settings(self.home, self.repo)

    def request(self, *args):
        return subprocess.run([sys.executable, str(ROOT / 'ops/outlook-census-request.py'), *args],
                              env={**os.environ, 'HOME': str(self.home)}, capture_output=True, text=True)

    def test_wrapper_arms_the_intake_snapshot_path_once(self):
        result = self.request()
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, 'coverage_census_requested=1\n', ''))
        out = self.repo / intake.OUT
        request = out / 'coverage-census.request'
        self.assertEqual((request.read_bytes(), stat.S_IMODE(request.stat().st_mode), request.stat().st_uid),
                         (b'', 0o600, os.getuid()))
        again = self.request()
        self.assertEqual((again.returncode, again.stdout, again.stderr),
                         (2, '', 'coverage_census_request_failed=exists\n'))
        def snapshot(home, work):
            (work / 'store.hxd').write_bytes(b'synthetic')
            (work / 'Files').mkdir()
        decoder = Path(self.temp.name) / 'synthetic-decoder'
        decoder.write_text('#!' + sys.executable + '\nprint(\'{"totals":{"messages":2}}\')\n')
        decoder.chmod(0o700)
        def decode(*args):
            raise intake.DecodeError('synthetic')
        with patch.object(intake, '_snapshot', snapshot), patch.object(intake, 'load_settings',
                return_value=SimpleNamespace(decoder=decoder)):
            with self.assertRaises(intake.DecodeError), intake._decode(self.home, out, decode, 7, 'synthetic@example.test'):
                self.fail('decoder must fail')
        self.assertFalse(request.exists())
        self.assertFalse(list(out.glob('work-*')))
        self.assertEqual(json.loads((out / 'coverage-census.json').read_text()), {'totals': {'messages': 2}})

    def test_wrapper_rejects_existing_symlink_without_touching_target(self):
        out = self.repo / intake.OUT
        out.mkdir(parents=True)
        target = Path(self.temp.name) / 'PRIVATE-TOKEN'
        target.write_text('PRIVATE-TOKEN')
        (out / 'coverage-census.request').symlink_to(target)
        result = self.request()
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (2, '', 'coverage_census_request_failed=exists\n'))
        self.assertEqual(target.read_text(), 'PRIVATE-TOKEN')

    def test_wrapper_errors_never_echo_settings_or_arguments(self):
        bad = self.request('PRIVATE-TOKEN')
        self.assertEqual((bad.returncode, bad.stdout, bad.stderr), (2, '', 'coverage_census_request_failed=usage\n'))
        (self.home / '.config/carr/outlook.json').write_text('PRIVATE-TOKEN')
        bad = self.request()
        self.assertEqual((bad.returncode, bad.stdout, bad.stderr), (2, '', 'coverage_census_request_failed=setup\n'))


if __name__ == '__main__':
    unittest.main()
