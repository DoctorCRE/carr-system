#!/usr/bin/env python3
"""Offline tests for the Outlook recovery dry run. Made-up store rows and candidates only."""
from contextlib import redirect_stderr, redirect_stdout
import csv
from io import StringIO
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import outlook_email_recovery as recovery

MAILBOX = 'owner@example.test'
SENT = 1_762_095_845  # 2025-11-02 15:04:05 UTC
HAPPENED = '2025-11-02 15:04:05+00'
# The decoded columns the dry run reads, as hxprobe's db command writes them.
SCHEMA = """CREATE TABLE messages (id INTEGER PRIMARY KEY, sender TEXT, subject TEXT, message_id TEXT,
  body TEXT, body_kind TEXT, body_completeness TEXT, sent_unix INTEGER, local_keys TEXT,
  authored TEXT, history TEXT, CHECK ((authored IS NULL) = (history IS NULL)));"""
CANDIDATE_COLUMNS = ('activity_id', 'user_id', 'slug', 'sender', 'happened_at', 'subject', 'body_sha256',
                     'message_id', 'needs', 'mailbox_account', 'item_locator', 'reason')


def item(key, sender='pat@example.test', subject='Suite question', message_id=None, kind='full',
         authored="Can we meet Thursday? O'Brien can too.", history='On Monday, Lee wrote:\n> Hello'):
    return dict(local_keys=key, sender=sender, subject=subject, message_id=message_id, body_kind=kind,
                body='Can we meet Thursday?', body_completeness='complete' if kind == 'full' else 'preview',
                sent_unix=SENT, authored=authored, history=history)


def candidate(n, key, reason='locator', mailbox=MAILBOX, **fields):
    row = dict(activity_id=f'00000000-0000-4000-8000-{n:012d}', user_id='00000000-0000-4000-8000-00000000aaaa',
               slug='owner', sender='pat@example.test', happened_at=HAPPENED, subject='Suite question',
               body_sha256='ab' * 32, message_id='', needs='both', mailbox_account=mailbox,
               item_locator=f'hxstore-key:{key}' if key else '', reason=reason)
    return {**row, **fields}


ITEMS = [item('101', message_id='<one@example.test>'), item('102'), item('103'), item('104'),
         item('105', message_id='<five@example.test>'), item('106', kind='preview'),
         item('107', authored=None, history=None), item('108'), item('108')]
CANDIDATES = [
    candidate(1, '101'),
    candidate(2, '102', sender='lee@example.test'),
    candidate(3, '103', happened_at='2025-11-02 15:04:06+00'),
    candidate(4, '104', subject='Another subject'),
    candidate(5, '105', message_id='other@example.test'),
    candidate(6, '106'),
    candidate(7, '107'),
    candidate(8, '108'),
    candidate(9, '199'),
    candidate(10, '101', mailbox='partner@example.test'),
    candidate(11, None, reason='no_locator', mailbox=''),
]


class DryRunTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='email-recovery-'))
        self.addCleanup(shutil.rmtree, self.dir)
        self.store_db = self.dir / 'decoded.db'
        con = sqlite3.connect(self.store_db)
        con.executescript(SCHEMA)
        for row in ITEMS:
            con.execute(f'INSERT INTO messages ({", ".join(row)}) VALUES ({", ".join("?" * len(row))})', tuple(row.values()))
        con.commit()
        con.close()
        self.candidates = self.dir / 'candidates.csv'
        with self.candidates.open('w', newline='') as handle:
            writer = csv.DictWriter(handle, CANDIDATE_COLUMNS)
            writer.writeheader()
            writer.writerows(CANDIDATES)

    def run_main(self, out, store=None):
        settings = SimpleNamespace(profile=self.dir / 'live-profile')
        decode = lambda copy, files, db: shutil.copyfile(self.store_db, db)
        stdout, stderr = StringIO(), StringIO()
        with patch.object(recovery, 'load_settings', return_value=settings), \
                patch.object(recovery.intake, '_decoder', return_value=decode), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = recovery.main(['--candidates', str(self.candidates), '--store', str(store or self.dir / 'copy/HxStore.hxd'),
                                  '--files', str(self.dir / 'copy/Files'), '--mailbox', MAILBOX, '--out', str(out)])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_only_the_corroborated_item_is_loaded_and_stdout_is_counts(self):
        out = self.dir / 'private/load.sql'
        out.parent.mkdir()
        code, stdout, _ = self.run_main(out)
        self.assertEqual(code, 0)
        self.assertEqual(stdout.strip(), 'outlook-email-recovery dry_run body_incomplete=1 candidates=11 '
                         'item_ambiguous=1 item_missing=1 message_id_differs=1 message_id_found=1 no_locator=1 '
                         'no_split=1 other_mailbox=1 sender_differs=1 sent_time_differs=1 subject_differs=1 verified=1')
        self.assertNotIn('example.test', stdout)
        self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)
        load = out.read_text()
        self.assertIn("('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-00000000aaaa', "
                      "'pat@example.test', '2025-11-02 15:04:05+00', 'Suite question', '" + 'ab' * 32 + "', "
                      "'Can we meet Thursday? O''Brien can too.', 'On Monday, Lee wrote:\n> Hello', "
                      "'one@example.test');", load)
        self.assertEqual(load.count('-0000-4000-8000-0000000000'), 1)
        self.assertEqual(sorted(p.name for p in out.parent.iterdir()), ['load.sql'])

    def test_no_verified_item_still_writes_the_empty_table(self):
        out = self.dir / 'load.sql'
        self.candidates.write_text(','.join(CANDIDATE_COLUMNS) + '\n')
        self.assertEqual(self.run_main(out)[0], 0)
        self.assertTrue(out.read_text().rstrip().endswith('message_id text);'))

    def test_refuses_a_load_file_inside_a_git_checkout(self):
        (self.dir / 'repo/.git').mkdir(parents=True)
        out = self.dir / 'repo/evidence/load.sql'
        code, stdout, stderr = self.run_main(out)
        self.assertEqual((code, stdout), (2, ''))
        self.assertIn('outside any git checkout', stderr)
        self.assertFalse(out.exists())

    def test_refuses_the_live_profile(self):
        out = self.dir / 'load.sql'
        code, _, stderr = self.run_main(out, store=self.dir / 'live-profile/HxStore.hxd')
        self.assertEqual(code, 2)
        self.assertIn('not the live Outlook profile', stderr)
        self.assertFalse(out.exists())

    def _one_candidate(self, n, key, **fields):
        out = self.dir / 'private/load.sql'
        out.parent.mkdir()
        with self.candidates.open('w', newline='') as handle:
            writer = csv.DictWriter(handle, CANDIDATE_COLUMNS)
            writer.writeheader()
            writer.writerow(candidate(n, key, needs='pair', **fields))
        return out

    def test_matching_bracketed_message_ids_are_loaded(self):
        out = self._one_candidate(12, '105', message_id='<five@example.test>')
        code, stdout, _ = self.run_main(out)
        self.assertEqual(code, 0)
        self.assertIn('verified=1', stdout)
        self.assertNotIn('message_id_differs', stdout)
        load = out.read_text()
        self.assertIn("'00000000-0000-4000-8000-000000000012'", load)
        self.assertIn("'five@example.test'", load)

    def test_candidate_message_id_without_a_store_id_is_omitted(self):
        out = self._one_candidate(13, '102', message_id='<five@example.test>')
        code, stdout, _ = self.run_main(out)
        self.assertEqual(code, 0)
        self.assertIn('message_id_missing=1', stdout)
        self.assertNotIn('verified', stdout)
        load = out.read_text()
        self.assertNotIn('000000000013', load)
        self.assertNotIn('null', load)


if __name__ == '__main__':
    unittest.main()
