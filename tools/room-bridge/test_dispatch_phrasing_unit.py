#!/usr/bin/env python3
"""Dispatch phrasing behavior without a live provider."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import desks
import dispatch
from test_codex_models_unit import catalog_fixture


class PhrasingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.enterContext(catalog_fixture())
        self.registry = desks.Registry(self.root / 'desks.json')
        self.registry.register('cx', 'codex-session', family='sol', effort='high',
                               cwd=str(self.root))
        self.results = self.root / 'results.jsonl'
        self.runner = Mock(side_effect=self.complete)

    @staticmethod
    def complete(argv, **options):
        Path(argv[argv.index('-o') + 1]).write_text('done')
        return subprocess.CompletedProcess(argv, 0, '{"type":"turn.completed"}', '')

    def send(self, task, **options):
        return dispatch.dispatch('cx', task, registry=self.registry,
                                 results_path=self.results, provider_run=self.runner,
                                 **options)

    def test_reported_phrases_refuse_before_checkout_or_provider(self):
        cases = [
            ('try to break it', 'confirm the expected invariants hold'),
            ('credential exposed', 'confirm sensitive values remain redacted'),
            ('overwrite the binary', 'confirm the executable remains unchanged'),
            ('receive the token', 'confirm the deploy step alone uses pipeline configuration'),
        ]
        for phrase, suggestion in cases:
            with self.subTest(phrase=phrase), patch.object(dispatch.codex_checkout, 'prepare') as prepare:
                with self.assertRaises(desks.DeskError) as caught:
                    self.send(f'Review this: {phrase}.', checkout='new:example')
                self.assertEqual(caught.exception.code, 'brief_phrasing')
                self.assertIn(phrase, str(caught.exception))
                self.assertIn(suggestion, str(caught.exception))
                self.assertIn('--allow-phrasing', str(caught.exception))
                prepare.assert_not_called()
        self.assertEqual(self.runner.call_count, 0)
        self.assertFalse(self.results.exists())

    def test_matching_tolerates_case_and_whitespace_and_reports_every_match(self):
        with self.assertRaises(desks.DeskError) as caught:
            self.send('TRY\nTO  BREAK IT; credential exposed; overwrite the binary.')
        for phrase in ('try to break it', 'credential exposed', 'overwrite the binary'):
            self.assertIn(phrase, str(caught.exception))
        self.assertEqual(self.runner.call_count, 0)

    def test_explicit_override_preserves_the_original_brief(self):
        row = self.send('try to break it', allow_phrasing=True)
        self.assertEqual(row['status'], 'completed')
        self.assertEqual(row['task'], 'try to break it')
        self.assertIn('try to break it', self.runner.call_args.args[0][-1])
        self.assertEqual(self.runner.call_count, 1)

    def test_cli_override_reaches_dispatch(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(dispatch.subprocess, 'run', self.runner), \
                patch.object(dispatch, '_codex_slot', return_value=contextlib.nullcontext()), \
                patch.object(dispatch.seat_gate, 'check', return_value=None), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = dispatch.main(['--registry', str(self.registry.path),
                                  '--results', str(self.results), 'send', 'cx',
                                  'try to break it', '--allow-phrasing'])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'completed')
        self.assertEqual(self.runner.call_count, 1)

    def test_cli_rejects_stdin_brief_before_waiting_for_a_slot(self):
        stderr = io.StringIO()
        with patch.object(dispatch.sys, 'stdin', io.StringIO('try to break it')), \
                patch.object(dispatch, '_codex_slot') as slot, \
                contextlib.redirect_stderr(stderr):
            code = dispatch.main(['--registry', str(self.registry.path),
                                  '--results', str(self.results), 'send', 'cx', '-'])
        self.assertEqual(code, 2)
        self.assertIn('try to break it', stderr.getvalue())
        self.assertEqual(slot.call_count, 0)
        self.assertFalse(self.results.exists())

    def test_provider_safety_refusal_prints_property_hint_without_retry(self):
        self.runner.side_effect = lambda argv, **options: subprocess.CompletedProcess(
            argv, 1, json.dumps({'type': 'turn.failed', 'error': {
                'message': "Your request was rejected by the safety classifier."}}), '')
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            row = self.send('confirm expected behavior')
        self.assertEqual(row['status'], 'failed')
        self.assertIn('safety classifier', row['detail'])
        self.assertIn('Describe checks as properties to confirm', stderr.getvalue())
        self.assertIn('confirm the expected invariants hold', stderr.getvalue())
        self.assertEqual(self.runner.call_count, 1)
        self.assertEqual(json.loads(self.results.read_text()), row)

    def test_property_wording_and_partial_words_are_sent(self):
        for task in (
            'confirm the expected invariants hold',
            'confirm sensitive values remain redacted',
            'confirm the executable remains unchanged',
            'retry to break items; credential exposedness; overwrite the binarylog',
        ):
            with self.subTest(task=task):
                row = self.send(task)
                self.assertEqual(row['status'], 'completed')
                self.assertEqual(row['task'], task)
        self.assertEqual(self.runner.call_count, 4)

    def test_refusal_after_override_uses_the_same_matched_hint(self):
        self.runner.side_effect = lambda argv, **options: subprocess.CompletedProcess(
            argv, 1, '', 'Request rejected by our safety system')
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            row = self.send('credential exposed', allow_phrasing=True)
        self.assertEqual(row['status'], 'failed')
        self.assertIn("'credential exposed' -> 'confirm sensitive values remain redacted'",
                      stderr.getvalue())
        self.assertNotIn('try to break it', stderr.getvalue())

    def test_other_failures_and_successful_reports_do_not_print_safety_hint(self):
        cases = [
            (1, 'connection lost', 'failed'),
            (1, 'selected model unavailable', 'failed'),
            (0, 'The safety classifier checks passed.', 'completed'),
        ]
        for code, message, status in cases:
            with self.subTest(message=message):
                def runner(argv, **options):
                    Path(argv[argv.index('-o') + 1]).write_text(message if code == 0 else '')
                    return subprocess.CompletedProcess(argv, code, '', message)
                self.runner.side_effect = runner
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    row = self.send('confirm expected behavior')
                self.assertEqual(row['status'], status)
                self.assertNotIn('Describe checks as properties to confirm', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
