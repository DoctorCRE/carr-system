#!/usr/bin/env python3
"""Offline characterization of the line-oriented record command contract."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.record_call import call_command


class RecordCommandTests(unittest.TestCase):
    def invoke(self, stdout='', code=0, error=None):
        calls = []

        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            if error:
                raise error
            return subprocess.CompletedProcess(argv, code, stdout, 'ignored stderr')

        result = call_command(['fixture-command', 'fixture-option'], 'fixture-verb',
                              {'value': 'fake'}, timeout=60, runner=run)
        self.assertEqual(calls, [(['fixture-command', 'fixture-option', 'fixture-verb',
                                  json.dumps({'value': 'fake'})],
                                 {'capture_output': True, 'text': True, 'timeout': 60,
                                  'check': False})])
        return result

    def test_last_dictionary_wins_past_noise_and_other_json(self):
        self.assertEqual(self.invoke('{"ok": false}\nnoise\n{"ok": true, "id": "fake"}\n[]\n42'),
                         (True, {'ok': True, 'id': 'fake'}))

    def test_nonzero_exit_preserves_reply(self):
        self.assertEqual(self.invoke('{"ok": true}', code=7), (False, {'ok': True}))

    def test_only_boolean_true_succeeds(self):
        for value in (False, 1, 'true', None):
            with self.subTest(value=value):
                self.assertEqual(self.invoke(json.dumps({'ok': value})), (False, {'ok': value}))

    def test_missing_dictionary_is_empty_reply(self):
        for stdout in ('', 'noise', '[]\nnull', '{\n"ok": true\n}'):
            with self.subTest(stdout=stdout):
                self.assertEqual(self.invoke(stdout), (False, {}))

    def test_os_error_keeps_exception_class_only(self):
        self.assertEqual(self.invoke(error=OSError('fake private detail')), (False, {'error': 'OSError'}))

    def test_subprocess_error_keeps_exception_class_only(self):
        self.assertEqual(self.invoke(error=subprocess.TimeoutExpired('fixture', 60)),
                         (False, {'error': 'TimeoutExpired'}))

    def test_unexpected_errors_still_raise(self):
        with self.assertRaises(ValueError):
            self.invoke(error=ValueError('fake'))


if __name__ == '__main__':
    unittest.main()
