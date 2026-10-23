#!/usr/bin/env python3
"""Check the scheduler declarations without loading agents or opening a database."""
import json
from pathlib import Path
import plistlib
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeSchedulingTests(unittest.TestCase):
    def test_installer_declares_real_jobs_without_the_tick(self):
        services = json.loads((ROOT / 'ops/config/services.json').read_text())
        self.assertNotIn('control-plane-tick', {row['key'] for row in services['services']})
        jobs = {
            'com.carr.calendar-eventkit': 'calendar-eventkit-capture.sh',
            'com.carr.nightly-record-layer': 'nightly.sh',
            'com.carr.notes-sweep': 'notes-sweep-post.sh',
        }
        for label, entrypoint in jobs.items():
            with self.subTest(label=label):
                plist = plistlib.loads((ROOT / 'ops/launchd' / (label + '.plist')).read_bytes())
                self.assertTrue(plist.get('StartCalendarInterval') or plist.get('StartInterval'))
                self.assertIn(entrypoint, ' '.join(plist['ProgramArguments']))


if __name__ == '__main__':
    unittest.main()
