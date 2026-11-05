#!/usr/bin/env python3
"""Exercise evidence-held task candidates with synthetic stores."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import runpy
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
HELPERS = runpy.run_path(str(ROOT / 'ops/fixtures/outlook-builds/write-inputs.py'))
STORE = runpy.run_path(str(ROOT / 'ops/hxstore-observe-selftest.py'))['store']
GENERATION = '00000000-0000-4000-8000-000000000007'
REVISION = '1' * 40
BINARY = ROOT / 'vendor/hxprobe/target/debug/hxprobe'


def message(account=7, key=70, classifier='IPM.Task'):
    data = bytearray(1541)
    HELPERS['identity'](data, 0, 0xc9, key, 212, account)
    return HELPERS['envelope'](0x060f, HELPERS['slots'](data, [
        (1226, HELPERS['text'](classifier)), (1422, HELPERS['text']('Synthetic private title'))]))


def detail(account=7, key=70, flag=2, revision=1, due=None, layout=3):
    data = bytearray(1508)
    HELPERS['identity'](data, 0, 0xc9, key, 212, account)
    struct.pack_into('<I', data, 1418, revision)
    data[1474], data[1491] = flag, layout
    for at, seconds in ((294, due), (286, None)):
        struct.pack_into('<Q', data, at, 0x2bca2875f4373fff if seconds is None else
                         621355968000000000 + seconds * 10000000)
    return HELPERS['envelope'](0x060f, data)


class Candidates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def decode(self, payload, account=7, after=0):
        source, target = self.root / 'synthetic.hxd', self.root / 'evidence.db'
        source.write_bytes(STORE(payload) if isinstance(payload, (bytes, bytearray)) else
                           STORE(payload[0]) + b''.join(STORE(block)[64:] for block in payload[1:]))
        target.unlink(missing_ok=True)
        done = subprocess.run([str(BINARY), 'task-candidates', '--immutable-copy', str(source),
                               str(target), str(account), GENERATION, 'synthetic-build', REVISION,
                               str(after)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        receipt = json.loads(done.stdout)
        with closing(sqlite3.connect(target)) as db:
            rows = [json.loads(row[0]) for row in db.execute('select candidate_json from task_candidates order by local_key')]
            self.cursor = db.execute('select resume_after_key from task_context').fetchone()[0]
        return receipt, rows

    def mutate_candidate(self, change):
        with closing(sqlite3.connect(self.root / 'evidence.db')) as db:
            candidate = json.loads(db.execute('select candidate_json from task_candidates').fetchone()[0])
            change(candidate)
            candidate.pop('candidate_digest')
            candidate['candidate_digest'] = hashlib.sha256(json.dumps(
                candidate, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
            db.execute('update task_candidates set candidate_json=? where local_key=?',
                       (json.dumps(candidate), '70'))
            db.commit()

    def test_unproven_due_semantics_preserve_exact_raw_slots(self):
        for ticks294, ticks302 in ((621355968000000000 + 1793232000 * 10000000,
                                    621355968000000000 + 1793404800 * 10000000),
                                   (621355968000000000 + 1793232000 * 10000000 + 5000000, 0),
                                   (0x2bca2875f4373fff, 0x2bca2875f4373fff)):
            with self.subTest(ticks294=ticks294):
                data = bytearray(detail())
                struct.pack_into('<Q', data, 10 + 294, ticks294)
                struct.pack_into('<Q', data, 10 + 302, ticks302)
                receipt, rows = self.decode(message(classifier='IPM.Note') + data)
                self.assertEqual(rows[0]['due'], {'state': 'unknown', 'value': 'date_semantics_unverified'})
                spans = rows[0]['unresolved_spans']
                self.assertEqual([(s['object_offset'], s['raw_value']) for s in spans],
                                 [(294, {'state': 'known', 'value': str(ticks294)}),
                                  (302, {'state': 'known', 'value': str(ticks302)})])
                self.assertEqual(receipt['candidates_admitted'], 0)

    def test_intake_rejects_interpretation_without_observed_provenance(self):
        from lib.outlook_task_candidates import intake_evidence
        def forged(c):
            c['kind'] = 'mail_flag'
            c['due'] = {'state': 'known', 'value': {'kind': 'date_only', 'date': '2026-10-31'}}
            c['provenance'] = []
        self.decode(message())
        self.mutate_candidate(forged)
        with self.assertRaises(ValueError):
            intake_evidence(self.root / 'evidence.db', '7', GENERATION)

    def test_receipt_rejects_hostile_names_and_missing_counters(self):
        for key in ('Synthetic private title', 'Subject: private', 'Joe Example',
                    'candidates_seen'):
            with self.subTest(key=key):
                self.decode(message())
                with closing(sqlite3.connect(self.root / 'evidence.db')) as db:
                    original = db.execute('select receipt_json from task_census').fetchone()[0]
                    receipt = json.loads(original)
                    if key == 'candidates_seen':
                        receipt.pop(key)
                    else:
                        receipt[key] = 1
                    db.execute('update task_census set receipt_json=? where receipt_json=?',
                               (json.dumps(receipt), original))
                    db.commit()
                done = subprocess.run([sys.executable, str(ROOT / 'tools/outlook-task-evidence.py'),
                                       str(self.root / 'evidence.db'), '7', GENERATION],
                                      capture_output=True, text=True)
                self.assertEqual(done.returncode, 2)
                self.assertEqual(done.stdout, '')
                self.assertNotIn(key, done.stderr)

    def test_nonmail_classifier_is_visible_but_never_verified(self):
        receipt, rows = self.decode(message())
        self.assertEqual(len(rows), 1)
        c = rows[0]
        self.assertEqual(c['schema'], 'outlook-task-candidate/v1')
        self.assertEqual(c['kind'], 'standalone_task')
        self.assertEqual(c['source']['identity_basis'], 'unresolved')
        self.assertIsNone(c['source']['object_key'])
        for field in ('title', 'due', 'completion', 'deletion', 'reminder', 'revision'):
            self.assertEqual(c[field]['state'], 'unknown')
        self.assertEqual(c['decode_status'], 'unsupported')
        self.assertNotIn('Synthetic private title', json.dumps(c))
        self.assertEqual(receipt['candidates_admitted'], 0)
        self.assertEqual(receipt['writes_acknowledged'], 0)
        self.assertEqual(receipt['reviews_pending'], 0)
        digest = c.pop('candidate_digest')
        canonical = json.dumps(c, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        self.assertEqual(digest, hashlib.sha256(canonical.encode()).hexdigest())

    def test_foreign_account_collision_cannot_poison_selected_candidate(self):
        _, first = self.decode(message(classifier='IPM.Note') + detail())
        receipt, mixed = self.decode(message(classifier='IPM.Note') + detail() +
                                     message(account=9) + detail(account=9, flag=1, revision=99))
        self.assertEqual(first, mixed)
        self.assertEqual(receipt['foreign_account_excluded'], 2)
        self.assertTrue(all(c['source']['account_key'] == '7' for c in mixed))

    def test_dates_unknown_absent_and_clear_are_distinct(self):
        _, rows = self.decode(message(classifier="IPM.Note") + detail())
        self.assertEqual(rows[0]['due'], {'state': 'absent'})
        _, rows = self.decode(message(classifier="IPM.Note") + detail(due=1793232000))
        self.assertEqual(rows[0]['due']['value'], {'kind': 'date_only', 'date': '2026-10-29'})
        _, rows = self.decode(message(classifier="IPM.Note") + detail(due=-60000000000))
        self.assertEqual(rows[0]['due']['state'], 'unknown')
        _, rows = self.decode(message(classifier="IPM.Note") + detail(flag=0))
        self.assertIn('mail_flag_clear', rows[0]['reason_codes'])
        self.assertEqual(rows[0]['deletion']['state'], 'unknown')

    def test_revision_order_and_conflict_are_scan_independent(self):
        a, b = detail(flag=2, revision=3), detail(flag=1, revision=9)
        _, forward = self.decode(message(classifier="IPM.Note") + a + b)
        _, reverse = self.decode(message(classifier="IPM.Note") + b + a)
        self.assertEqual(forward, reverse)
        self.assertEqual(forward[0]['completion']['value']['kind'], 'completed')
        _, tied = self.decode(message(classifier="IPM.Note") + b + detail(flag=2, revision=9))
        self.assertEqual(tied[0]['decode_status'], 'conflict')
        self.assertEqual(tied[0]['completion']['state'], 'conflict')

    def test_unknown_layout_kind_tag_descriptor_and_truncation_stay_held(self):
        for payload in (detail(layout=4), message()[:80],
                        HELPERS['envelope'](0x9999, bytes(100))):
            receipt, rows = self.decode(payload)
            self.assertEqual(receipt['candidates_admitted'], 0)
            self.assertEqual(receipt['decoder_incomplete'], 1)
            self.assertTrue(all(c['due']['state'] == 'unknown' for c in rows))
        bad = bytearray(message())
        struct.pack_into('<I', bad, 10 + 1226, 0xffffffff)
        receipt, rows = self.decode(bad)
        self.assertEqual(rows, [])
        self.assertGreater(receipt['unreadable_candidates'], 0)
        bad = bytearray(message())
        struct.pack_into('<I', bad, 10 + 18, 213)
        receipt, rows = self.decode(bad)
        self.assertEqual(rows, [])
        self.assertGreater(receipt['account_unknown'], 0)

    def test_replay_absence_and_readonly_intake_have_no_writer(self):
        from lib.outlook_task_candidates import intake_evidence
        _, first = self.decode(message())
        receipt = intake_evidence(self.root / 'evidence.db', '7', GENERATION)
        self.assertEqual(receipt['candidates_held'], 1)
        self.assertEqual(receipt['candidates_admitted'], 0)
        self.assertTrue(all(type(value) is int for value in receipt.values()))
        _, repeated = self.decode(message() + message())
        self.assertEqual(first, repeated)
        _, absent = self.decode(b'')
        self.assertEqual(absent, [])
        receipt = intake_evidence(self.root / 'evidence.db', '7', GENERATION)
        self.assertEqual(receipt['candidates_admitted'], 0)

    def test_intake_rejects_foreign_context_and_tampered_payload(self):
        from lib.outlook_task_candidates import intake_evidence
        self.decode(message())
        for account, generation in [('9', GENERATION), ('7', 'bad')]:
            with self.assertRaises(ValueError):
                intake_evidence(self.root / 'evidence.db', account, generation)
        with closing(sqlite3.connect(self.root / 'evidence.db')) as db:
            original = db.execute('select receipt_json from task_census').fetchone()[0]
            for key in ('candidates_admitted', 'candidates_supported', 'reviews_pending', 'writes_acknowledged'):
                altered = {**json.loads(original), key: 1}
                db.execute('update task_census set receipt_json=? where receipt_json=?',
                           (json.dumps(altered), original))
                db.commit()
                with self.assertRaises(ValueError):
                    intake_evidence(self.root / 'evidence.db', '7', GENERATION)
                db.execute('update task_census set receipt_json=? where receipt_json=?',
                           (original, json.dumps(altered)))
                db.commit()
        with closing(sqlite3.connect(self.root / 'evidence.db')) as db:
            db.execute("update task_candidates set candidate_json='{}' where local_key='70'")
            db.commit()
        with self.assertRaises(ValueError):
            intake_evidence(self.root / 'evidence.db', '7', GENERATION)

    def test_orphan_detail_and_task_classifier_cannot_verify_mail_fields(self):
        for payload in (detail(), message() + detail(),
                        message() + message(classifier='IPM.Note') + detail()):
            receipt, rows = self.decode(payload)
            self.assertEqual(receipt['candidates_admitted'], 0)
            for candidate in rows:
                self.assertEqual(candidate['due']['state'], 'unknown')
                self.assertEqual(candidate['source']['identity_basis'], 'unresolved')

    def test_unknown_flag_and_equal_revision_due_conflict_are_distinct(self):
        note = message(classifier='IPM.Note')
        _, rows = self.decode(note + detail(flag=3))
        self.assertEqual(rows[0]['completion']['state'], 'unknown')
        _, rows = self.decode(note + detail(due=1793232000) + detail(due=None))
        self.assertEqual(rows[0]['due']['state'], 'conflict')
        self.assertEqual(rows[0]['decode_status'], 'conflict')

    def test_u64_keys_remain_decimal_text_and_never_enter_receipts(self):
        key = 2**63 - 1
        receipt, rows = self.decode(message(classifier='IPM.Note', key=key) + detail(key=key))
        self.assertEqual(rows[0]['source']['object_key'], str(key))
        self.assertEqual(self.cursor, str(key))
        self.assertNotIn(str(key), json.dumps(receipt))
        _, rows = self.decode(message(key=2**64 - 1))
        self.assertEqual(self.cursor, str(2**64 - 1))
        self.assertIsNone(rows[0]['source']['object_key'])

    def test_cli_intake_emits_only_counts_and_refuses_bad_context(self):
        self.decode(message())
        command = [sys.executable, str(ROOT / 'tools/outlook-task-evidence.py'),
                   str(self.root / 'evidence.db'), '7', GENERATION]
        done = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(all(type(n) is int for n in json.loads(done.stdout).values()))
        done = subprocess.run(command[:-1] + ['bad'], capture_output=True, text=True)
        self.assertEqual(done.returncode, 2)
        self.assertNotIn(str(self.root), done.stderr)

    def test_corrupt_block_and_live_store_name_fail_closed(self):
        source, target = self.root / 'synthetic.hxd', self.root / 'evidence.db'
        source.write_bytes(STORE(message()))
        command = [str(BINARY), 'task-candidates', '--immutable-copy', str(source),
                   str(target), '7', GENERATION, 'synthetic-build', REVISION]
        corrupt = bytearray(source.read_bytes())
        corrupt[-1] ^= 1
        source.write_bytes(corrupt)
        done = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        receipt = json.loads(done.stdout)
        self.assertEqual(receipt['block_failures'], 1)
        self.assertEqual(receipt['decoder_incomplete'], 1)
        self.assertEqual(receipt['candidates_held'], 0)
        self.assertNotIn(str(self.root), done.stdout + done.stderr)
        live = self.root / 'HxStore.hxd'
        live.write_bytes(STORE(message()))
        done = subprocess.run(command[:3] + [str(live)] + command[4:], capture_output=True, text=True)
        self.assertEqual(done.returncode, 2)
        self.assertNotIn(str(self.root), done.stdout + done.stderr)

    def test_cap_and_resume_on_large_synthetic_corpus(self):
        payload = [b''.join(message(key=k) for k in range(start, start + 1000))
                   for start in range(1, 100001, 1000)]
        receipt, rows = self.decode(payload)
        self.assertEqual(len(rows), 10000)
        self.assertEqual(receipt['candidates_deferred'], 90000)
        self.assertEqual(receipt['decoder_incomplete'], 1)
        receipt, rows = self.decode(payload, after=10000)
        self.assertEqual(len(rows), 10000)
        self.assertEqual(receipt['candidates_deferred'], 80000)
        self.assertEqual(self.cursor, '20000')
        self.assertNotIn('resume_after_key', receipt)


if __name__ == '__main__':
    unittest.main()
