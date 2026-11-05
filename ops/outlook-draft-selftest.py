#!/usr/bin/env python3
"""Synthetic UI and store observations exercise the real draft helper orchestration."""
import importlib.util
from pathlib import Path
import subprocess
import sqlite3
import tempfile
from datetime import timedelta
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('draft_helper', ROOT / 'bin/outlook-draft.py')
assert spec is not None and spec.loader is not None
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.payload = dict(to=['client@example.test'], cc=['partner@example.test'],
                            subject='Exact subject', body='Exact body')
        self.mail = helper.send.Mail(tuple(self.payload['to']), tuple(self.payload['cc']), (),
                                     self.payload['subject'], self.payload['body'])
        self.row = dict(local_keys='42', recipients='client@example.test', cc='partner@example.test',
                        subject='Exact subject', body='Exact body', body_kind='full')
        self.form = Mock(window_id=123)
        self.form.read.return_value = helper.send.fields(self.mail)
        self.form.closed.return_value = True
        self.command = self.enterContext(patch.object(helper.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)))
        self.enterContext(patch.object(helper.send, '_main_window', return_value=(99, {})))
        self.enterContext(patch.object(helper.send, '_new_window', return_value=123))
        self.enterContext(patch.object(helper.send, 'ComposeWindow', return_value=self.form))
        self.cua = self.enterContext(patch.object(helper.send, '_cua', return_value={'windows': [{'window_id': 12}]}))
        self.observe = self.enterContext(patch.object(helper, 'draft_observations', side_effect=[[], [self.row]]))
        self.enterContext(patch.object(helper.time, 'monotonic', side_effect=[0, 31]))

    def invoke(self):
        return helper.save_draft(self.payload, SimpleNamespace())

    def test_launch_failure_never_accepts_later_ok(self):
        self.command.return_value = subprocess.CompletedProcess([], 1, 'OK', '')
        with self.assertRaises(SystemExit):
            self.invoke()
        self.form.read.assert_not_called()

    def test_saved_requires_new_identity_and_full_exact_store_body(self):
        self.assertEqual(self.invoke(), '42')
        self.assertEqual(self.cua.call_args_list[-1].args[1]['window_id'], 123)
        self.form.closed.assert_called_once()

    def test_existing_or_ambiguous_draft_is_not_new_persistence(self):
        for before, after in [([self.row], [self.row]), ([], [self.row, dict(self.row, local_keys='43')])]:
            with self.subTest(before=before):
                self.observe.side_effect = [before, after]
                with patch.object(helper.time, 'monotonic', side_effect=[0, 31]), self.assertRaises(SystemExit):
                    self.invoke()

    def test_mismatch_preview_or_missing_identity_never_passes(self):
        for change in [dict(body='Changed'), dict(body='Exact body\n'), dict(subject='Exact subject suffix'),
                       dict(body_kind='preview'), dict(local_keys=None), dict(recipients='other@example.test')]:
            with self.subTest(change=change):
                self.observe.side_effect = [[], [dict(self.row, **change)]]
                with patch.object(helper.time, 'monotonic', side_effect=[0, 31]), self.assertRaises(SystemExit):
                    self.invoke()

    def test_exact_ui_fields_and_account_are_required_before_save(self):
        for change in [dict(subject='Exact subject old window'), dict(cc=''), dict(body='Changed')]:
            self.form.read.return_value = dict(helper.send.fields(self.mail), **change)
            self.observe.side_effect = [[]]
            with self.subTest(change=change), self.assertRaises(SystemExit):
                self.invoke()
        self.form.read.side_effect = helper.send.UiFailure('wrong_account')
        self.observe.side_effect = [[]]
        with self.assertRaises(helper.send.UiFailure):
            self.invoke()

    def test_save_failure_cannot_claim_persistence(self):
        self.cua.side_effect = [{'windows': []}, helper.send.UiFailure('save_refused')]
        with self.assertRaises(helper.send.UiFailure):
            self.invoke()


class DecodedDraftTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT / 'ops'))
        spec = importlib.util.spec_from_file_location('draft_intake_fixture', ROOT / 'ops/outlook-intake-selftest.py')
        self.fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.fixture)
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.settings = SimpleNamespace(repo=Path(self.tmp), folder_account_id=7, account='owner@example.test')
        self.raw = 'https://nam01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fexample.test%2F&data=synthetic'
        self.to, self.cc, self.kind = 'client@example.test', 'partner@example.test', 'full'
        from lib import outlook_intake as intake
        self.enterContext(patch.object(intake, '_snapshot'))
        self.enterContext(patch.object(intake, '_decoder', return_value=self.decode))

    def decode(self, src, files, db):
        f = self.fixture
        f.write_db(db, [f.mail(self.settings.account, timedelta(), local_keys='1'),
                        f.mail(self.settings.account, timedelta(), subject='Draft', body=self.raw,
                               kind=self.kind, local_keys='42')])
        with sqlite3.connect(db) as c:
            c.execute("update folders set name='Sent Items' where id=7")
            c.execute("insert into folders values(8,7,'Drafts','top',null)")
            c.execute('update message_folders set folder_id=8 where message_id=2')
            c.executescript('CREATE TABLE message_keys(message_id,local_key); '
                            'CREATE TABLE message_objects(local_key,subject,conversation_id,recipients_status); '
                            'CREATE TABLE recipients(local_key,kind,address,position);')
            c.execute('insert into message_keys values(2,42)')
            c.execute("insert into message_objects values(42,'Draft',null,'listed')")
            c.executemany('insert into recipients values(42,?,?,0)', [('to', self.to), ('cc', self.cc)])

    def mail(self, body=None, **changes):
        args = dict(to=('client@example.test',), cc=('partner@example.test',), bcc=(),
                    subject='Draft', body=self.raw if body is None else body)
        return helper.send.Mail(**dict(args, **changes))

    def test_transformed_body_is_not_durable_persistence(self):
        self.cc = ''
        rows = helper.draft_observations(self.settings)
        self.assertEqual(helper.matching_draft_ids(rows, self.mail('<https://example.test/>', cc=())), set())

    def test_raw_full_body_and_separate_cc_establish_persistence(self):
        rows = helper.draft_observations(self.settings)
        self.assertEqual(helper.matching_draft_ids(rows, self.mail()), {'42'})
        self.assertEqual(helper.matching_draft_ids(rows, self.mail(self.raw + '\n')), set())

    def test_separate_cc_with_plain_body_establishes_persistence(self):
        self.raw = 'Exact body'
        rows = helper.draft_observations(self.settings)
        self.assertEqual(helper.matching_draft_ids(rows, self.mail()), {'42'})

    def test_preview_does_not_establish_persistence(self):
        self.kind = 'preview'
        rows = helper.draft_observations(self.settings)
        self.assertEqual(helper.matching_draft_ids(rows, self.mail()), set())

    def test_canonical_recipients_preserve_roles_and_complete_addresses(self):
        self.raw = 'Exact body'
        for cc, accepted in [('Partner <PARTNER@EXAMPLE.TEST>', True),
                             ('notpartner@example.test', False),
                             ('partner@example.test.extra.test', False),
                             ('partner+alias@example.test', False),
                             ('"partner@example.test" <other@example.test>', False),
                             ('partner@example.test, extra@example.test', False)]:
            with self.subTest(cc=cc):
                self.cc = cc
                rows = helper.draft_observations(self.settings)
                self.assertEqual(helper.matching_draft_ids(rows, self.mail()), {'42'} if accepted else set())
        self.to, self.cc = 'partner@example.test', 'client@example.test'
        rows = helper.draft_observations(self.settings)
        self.assertEqual(helper.matching_draft_ids(rows, self.mail()), set())

    def test_save_draft_uses_raw_decoded_body_and_cc_for_success(self):
        for body, accepted in [(self.raw, True), ('<https://example.test/>', False)]:
            with self.subTest(body=body):
                rows = helper.draft_observations(self.settings)
                mail = self.mail(body)
                form = Mock(window_id=123)
                form.read.return_value = helper.send.fields(mail)
                form.closed.return_value = True
                with patch.object(helper, 'draft_observations', side_effect=[[], rows]), \
                     patch.object(helper.send, '_main_window', return_value=(99, {})), \
                     patch.object(helper.send, '_new_window', return_value=123), \
                     patch.object(helper.send, 'ComposeWindow', return_value=form), \
                     patch.object(helper.send, '_cua', return_value={'windows': []}), \
                     patch.object(helper.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)), \
                     patch.object(helper.time, 'monotonic', side_effect=[0, 31]):
                    payload = dict(to=mail.to, cc=mail.cc, subject=mail.subject, body=mail.body)
                    if accepted:
                        self.assertEqual(helper.save_draft(payload, self.settings), '42')
                    else:
                        with self.assertRaisesRegex(SystemExit, 'persistence unverified'):
                            helper.save_draft(payload, self.settings)

    def test_unobserved_bcc_cannot_establish_persistence(self):
        rows = helper.draft_observations(self.settings)
        self.assertEqual(helper.matching_draft_ids(rows, self.mail(bcc=('hidden@example.test',))), set())


if __name__ == '__main__':
    unittest.main()
