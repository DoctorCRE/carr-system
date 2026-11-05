#!/usr/bin/env python3
"""Exercise the contact capability gate with synthetic stores only."""
import json
import runpy
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / 'vendor/hxprobe'
STORE = runpy.run_path(str(ROOT / 'ops/hxstore-observe-selftest.py'))['store']


class ContactDecoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['cargo', 'build', '--locked'], cwd=CRATE, check=True, timeout=180)

    def export(self, version):
        synthetic = STORE('Synthetic Contact +1-555-0199 pat@example.test'.encode('utf-16le'))
        synthetic[8] = version
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-contact-')
        self.addCleanup(self.temp.cleanup)
        source = Path(self.temp.name) / 'synthetic.hxd'
        target = Path(self.temp.name) / 'synthetic.db'
        source.write_bytes(synthetic)
        subprocess.run([str(CRATE / 'target/debug/hxprobe'), 'db', str(source), str(target)],
                       check=True, capture_output=True, timeout=30)
        self.assertEqual(source.read_bytes(), synthetic)
        db = sqlite3.connect(target)
        self.addCleanup(db.close)
        return db

    def test_known_container_does_not_prove_contact_layout(self):
        for version in (ord('i'), ord('h')):
            with self.subTest(version=version):
                db = self.export(version)
                capability = dict(zip([column[0] for column in db.execute('SELECT * FROM contact_capability').description],
                                      db.execute('SELECT * FROM contact_capability').fetchone()))
                capability.pop('singleton')
                self.assertEqual(capability['container_version'], version)
                self.assertEqual(capability['schema'], 'outlook-contact-observation/v1')
                base_digest = runpy.run_path(str(ROOT / 'ops/hxprobe-source-digest.py'))['digest'](CRATE)
                self.assertEqual(capability['source_digest'], base_digest)
                self.validate_cases([{'entry': None, 'value': {'capability': capability, 'observations': []}, 'valid': True}])
                self.assertEqual(db.execute('SELECT count(*) FROM contact_observations').fetchone(), (0,))
                with self.assertRaisesRegex(sqlite3.IntegrityError, 'contact_layout_unverified'):
                    db.execute('INSERT INTO contact_observations VALUES (?)', ('{}',))
                self.assertEqual(db.execute('SELECT count(*) FROM contact_observations').fetchone(), (0,))

    def validate_cases(self, cases):
        script = r"""
import { readFileSync } from 'node:fs';
import { compileSchema } from './workspace/contracts/schema-validator.mjs';
const schema = JSON.parse(readFileSync('spec/outlook/contact-observation.v1.json', 'utf8'));
const cases = JSON.parse(readFileSync(0, 'utf8'));
for (const item of cases) {
    const result = compileSchema(schema, item.entry ? schema.$defs[item.entry] : schema)(item.value);
    if (result.valid !== item.valid) throw Error(JSON.stringify({item, result}));
}
"""
        result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=ROOT,
                                input=json.dumps(cases), capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bounded_field_states_and_evidence(self):
        evidence = dict(layout_evidence_id='synthetic-layout', snapshot_id='synthetic-snapshot',
                        object_key='18446744073709551615', record_fingerprint='a' * 64,
                        logical_field='phone', span=dict(offset=0, length=64), raw_value_digest='b' * 64)
        present = dict(state='present', raw_value='synthetic phone', normalized_value='synthetic phone', evidence=evidence)
        cases = []
        def case(value, valid, entry='field_observation'):
            cases.append(dict(entry=entry, value=value, valid=valid))
        case(present, True)
        for state in ('empty', 'absent'):
            case(dict(state=state, evidence=evidence), True)
            case(dict(state=state, evidence=evidence, raw_value='must not clear'), False)
        for reason in ('unsupported', 'unread', 'malformed', 'conflict'):
            case(dict(state='unknown', reason=reason), True)
            case(dict(state='unknown', reason=reason, raw_value='plausible'), False)
        case(dict(state='unknown', reason='guess'), False)
        case(dict(state='present', raw_value='x', normalized_value='x'), False)
        case({**present, 'raw_value': ''}, False)
        case({**present, 'raw_value': 'x' * 4097}, False)
        case({**present, 'evidence': {**evidence, 'span': dict(offset=0, length=65537)}}, False)
        case({**present, 'evidence': {**evidence, 'object_key': 18446744073709551615}}, False)
        case(dict(state='unknown', reason='unsupported'), True, 'revision')
        case(dict(state='unknown', reason='missing'), True, 'revision')
        case(dict(state='unknown', reason='conflicting'), True, 'revision')
        case(dict(state='unknown', reason='latest'), False, 'revision')
        case({**present, 'evidence': {**evidence, 'logical_field': 'guess'}}, False)
        case({**present, 'evidence': {**evidence, 'record_fingerprint': 'not-a-digest'}}, False)
        case(dict(state='proven', fingerprint='a' * 64, ordering=dict(state='unknown'),
                  identity_evidence={k: evidence[k] for k in ('layout_evidence_id', 'snapshot_id', 'object_key')} | {'owner_key': 'synthetic-owner'}), True, 'revision')
        self.validate_cases(cases)

    def test_observation_identity_and_field_allowlist(self):
        observation = dict(schema='outlook-contact-observation/v1',
            mailbox=dict(owner='joe', account_key='synthetic-account', profile_epoch='synthetic-profile'),
            source=dict(kind='outlook_contact_record', key='18446744073709551615', key_status='unknown'),
            revision=dict(state='unknown', reason='unsupported'),
            decoder=dict(schema='outlook-contact-observation/v1', source_digest='a' * 64,
                         container_version=105, outlook_build=None, layout_id=None,
                         capability='unsupported', reason='contact_layout_unverified'),
            observed_at='2026-01-01T00:00:00Z', fields={'phone': dict(state='unknown', reason='unsupported')})
        cases = [dict(entry='observation', value=observation, valid=True)]
        for change in (dict(fields={'guessed_field': dict(state='unknown', reason='unsupported')}),
                       dict(source={**observation['source'], 'key': 18446744073709551615}),
                       dict(mailbox={**observation['mailbox'], 'owner': 'unknown'}),
                       dict(mailbox=dict(owner='joe', account_key='synthetic-account'))):
            cases.append(dict(entry='observation', value={**observation, **change}, valid=False))
        cases.append(dict(entry=None, value=dict(capability=observation['decoder'], observations=[observation]), valid=False))
        self.validate_cases(cases)

    def test_mail_export_stays_available(self):
        fixtures = runpy.run_path(str(ROOT / 'ops/hxstore-local-key-selftest.py'))
        with sqlite3.connect(fixtures['decode'](fixtures['message'](42))) as db:
            self.assertEqual(db.execute('SELECT sender FROM messages').fetchall(), [('pat@example.test',)])
            self.assertEqual(db.execute('SELECT count(*) FROM contact_observations').fetchone(), (0,))

    def test_unknown_build_exports_unsupported_with_no_contact_values(self):
        db = self.export(ord('z'))
        self.assertEqual(db.execute('SELECT capability, reason, outlook_build, layout_id '
                                    'FROM contact_capability').fetchall(),
                         [('unsupported', 'contact_layout_unverified', None, None)])
        self.assertEqual(db.execute('SELECT count(*) FROM contact_observations').fetchone(), (0,))


if __name__ == '__main__':
    unittest.main()
