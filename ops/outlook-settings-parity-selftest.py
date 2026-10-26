#!/usr/bin/env python3
"""Check configured intake artifacts with synthetic Studio fixtures."""
from datetime import timedelta
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import outlook_intake as current
from lib.outlook_settings import SettingsError, load_settings
from outlook_test_support import write_settings


BASELINE = '712ee39b8d6b61f986d210107a83ad07846a32ba'


def baseline_module():
    source = subprocess.run(['git', 'show', f'{BASELINE}:lib/outlook_intake.py'], cwd=ROOT,
                            check=True, capture_output=True, text=True).stdout
    module = ModuleType('outlook_before_settings')
    sys.modules[module.__name__] = module
    exec(compile(source, f'{BASELINE}:lib/outlook_intake.py', 'exec'), module.__dict__)
    return module


def fixture_module():
    spec = importlib.util.spec_from_file_location('outlook_parity_fixtures', ROOT / 'ops/outlook-intake-selftest.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ParityTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.home, self.repo = self.root / 'home', self.root / 'repo'
        self.settings_path = write_settings(self.home, self.repo, owner='joe',
                                            account='broker@carr.us', internal_domain='carr.us')
        self.settings = load_settings(self.home)
        self.store = self.settings.profile / 'HxStore.hxd'
        self.store.parent.mkdir(parents=True)
        self.store.write_bytes(b'synthetic-store')
        os.utime(self.store, ns=(1_800_000_000_000_000_000, 1_800_000_000_000_000_000))
        sidecars = self.store.parent / 'Files/S0/845/EFMData'
        sidecars.mkdir(parents=True)
        (sidecars / '7.dat').write_bytes(b'gzip-body')
        self.fixtures = fixture_module()
        self.fixture = self.root / 'decoded.db'
        self.compile_helper()
        home = patch('pathlib.Path.home', return_value=self.home)
        home.start()
        self.addCleanup(home.stop)

    def compile_helper(self):
        helper = self.settings.copy_helper
        helper.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                        f'-DOUTLOOK_PROFILE={json.dumps(str(self.settings.profile))}',
                        f'-DOUTLOOK_WORK_ROOT={json.dumps(str(self.repo / current.OUT))}',
                        f'-DOUTLOOK_REPO={json.dumps(str(self.repo))}',
                        str(ROOT / 'tools/outlook-intake-copy.c'), '-o', str(helper)],
                       capture_output=True, check=True)

    def exercise(self, module=current):
        out = self.repo / current.OUT
        shutil.rmtree(out, ignore_errors=True)
        self.fixture.unlink(missing_ok=True)
        f = self.fixtures
        sender = self.settings.account
        mails = [f.mail('pat@example.test', -timedelta(hours=3), recipients=sender),
                 f.mail(sender, -timedelta(hours=2), recipients='lee@example.test', subject='Sent suite details'),
                 f.mail('pat@example.test', -timedelta(hours=1), recipients=sender,
                        subject='Preview follow-up', kind='preview')]
        events = [f.event('past-visit', -timedelta(hours=5), organizer=('Broker', sender),
                          attendees=[('Pat Example', 'pat@example.test')]),
                  f.event('upcoming-visit', timedelta(days=2), organizer=('Broker', sender),
                          attendees=[('Unmapped Visitor', 'visitor@example.test')])]
        f.write_db(self.fixture, mails, events)
        fake = f.Fake(self)
        fake.fixture = self.fixture
        fake.model_reply = {'next_step': 'Confirm the visit.', 'due_on': '2026-10-23'}
        with patch.object(f, 'intake', module), patch('uuid.uuid4', return_value=uuid.UUID(int=1)):
            result = module.run(self.repo, self.home, fake.ports())
        artifacts = {path.name: path.read_bytes() for path in sorted(out.glob('*')) if path.is_file()}
        return result, fake.calls, artifacts, module.status_line(result).encode()

    def test_mail_record_calls_are_byte_identical_to_baseline_without_calendar_writes(self):
        before = self.exercise(baseline_module())
        after = self.exercise()
        calendar_activity = current.key('log-activity', 'C-901',
                                        f'past-visit|{self.fixtures.ts(-timedelta(hours=5))}')
        before_calls = [(verb, args) for verb, args in before[1]
                        if args.get('idempotency_key') != calendar_activity]
        self.assertEqual(json.dumps(before_calls).encode(), json.dumps(after[1]).encode())

    def test_studio_settings_preserve_mail_artifacts_and_quarantine_calendar(self):
        result, calls, artifacts, status = self.exercise()
        self.assertEqual(result.state, 'succeeded')
        self.assertEqual(result.reason, 'processed')
        activities = [args['kind'] for verb, args in calls if verb == 'log-activity']
        self.assertEqual(activities, ['email_in', 'email_out', 'email_in'])
        actions = [args for verb, args in calls if verb == 'set-next-action']
        self.assertTrue(actions)
        self.assertEqual({args['owner'] for args in actions}, {'joe'})
        self.assertTrue(any(name.startswith('report-') for name in artifacts))
        self.assertIn('needs-refresh.json', artifacts)
        state = json.loads(artifacts['state.json'])
        self.assertEqual(len(state['calendar_evidence']), 2)
        self.assertTrue(all(row['status'] == 'unsupported' for row in state['calendar_evidence'].values()))
        self.assertIn(b'calendar_lifecycle_unsupported', status)

    def test_second_person_uses_other_profile_owner_and_outbound_address(self):
        other = self.home / 'Other Profile'
        self.settings.profile.rename(other)
        self.settings_path = write_settings(self.home, self.repo, owner='other-person',
                                            account='broker@other.test', internal_domain='other.test', profile=str(other))
        self.settings = load_settings(self.home)
        self.store = other / 'HxStore.hxd'
        self.compile_helper()
        result, calls, _, _ = self.exercise()
        self.assertEqual(result.state, 'succeeded')
        writes = [args for verb, args in calls if verb == 'set-next-action']
        self.assertTrue(writes)
        self.assertEqual({args['owner'] for args in writes}, {'other-person'})
        outgoing = [args for verb, args in calls if verb == 'log-activity' and args['kind'] == 'email_out']
        self.assertEqual(len(outgoing), 1)
        self.assertEqual(outgoing[0]['ref'], 'L-902')
        self.assertTrue(other.is_dir())

    def test_absent_settings_refuse_before_snapshot_decoder_or_record_effects(self):
        self.settings_path.unlink()
        fake = self.fixtures.Fake(self)
        with self.assertRaises(SettingsError) as error:
            current.run(self.repo, self.home, fake.ports())
        self.assertIn(str(self.settings_path), str(error.exception))
        self.assertEqual(fake.decodes, 0)
        self.assertEqual(fake.calls, [])
        self.assertFalse((self.repo / current.OUT).exists())


if __name__ == '__main__':
    unittest.main()
