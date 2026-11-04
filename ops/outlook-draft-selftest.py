#!/usr/bin/env python3
"""Synthetic UI and store observations exercise the real draft helper orchestration."""
import importlib.util
from pathlib import Path
import subprocess
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
        self.row = dict(local_keys='42', recipients='client@example.test,partner@example.test',
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


if __name__ == '__main__':
    unittest.main()
