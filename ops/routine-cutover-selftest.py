#!/usr/bin/env python3
import json
import plistlib
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class CutoverTests(unittest.TestCase):
    def test_only_proven_npi_cutover_retires_a_routine(self):
        manifest = json.loads((ROOT / 'ops/routines/jobs.v1.json').read_text())
        self.assertEqual([job['id'] for job in manifest['jobs']], ['lead-signals-weekly'])
        self.assertEqual(manifest['jobs'][0]['replaces'], ['npi-sweep-weekly'])
        self.assertEqual(manifest.get('retired', []), [])

    def test_native_job_records_actual_exit_and_preserves_schedule(self):
        job = json.loads((ROOT / 'ops/routines/jobs.v1.json').read_text())['jobs'][0]
        path = ROOT / 'ops/launchd' / (job['label'] + '.plist')
        plist = plistlib.loads(path.read_bytes())
        self.assertEqual(plist['Label'], 'com.carr.routine-lead-signals-weekly')
        self.assertEqual(plist['StartCalendarInterval'], {'Weekday': 1, 'Hour': 7, 'Minute': 30})
        self.assertEqual(plist['ProgramArguments'], ['/bin/zsh', '{{REPO}}/bin/run-scheduled.sh',
            'lead-signals-weekly', 'routine.weekly', '/bin/sh', '{{REPO}}/bin/routine-run.sh', 'lead-signals-weekly'])
        self.assertFalse(plist['RunAtLoad'])
        self.assertEqual(plist['Umask'], 0o77)
        self.assertFalse((ROOT / 'ops/headless-tasks/com.carr.headless.npi-sweep-weekly.plist').exists())
        from lib.launchd_scope import allowed_on_machine
        self.assertFalse(allowed_on_machine(path.name, False))
        self.assertTrue(allowed_on_machine(path.name, True))
        services = json.loads((ROOT / 'ops/config/services.json').read_text())['services']
        keys = {row['key'] for row in services}
        self.assertNotIn('npi-sweep-weekly', keys)
        self.assertIn('radar-weekly', keys)
        tasks = json.loads((ROOT / 'ops/headless-tasks/tasks.json').read_text())['tasks']
        self.assertNotIn('npi-sweep-weekly', tasks)
        self.assertIn('radar-weekly', tasks)


if __name__ == '__main__':
    unittest.main()
