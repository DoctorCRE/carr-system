"""Test backfill reader preflight and failure reporting."""
import importlib.util
import json
from pathlib import Path
import unittest
import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('backfill_cli', Path(__file__).resolve().parents[1] / 'bin/backfill-deal-document-text.py')
assert spec is not None and spec.loader is not None
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)

class PreflightTests(unittest.TestCase):
    def test_missing_reader_is_typed(self):
        with patch.object(cli, 'wrangler_bin', return_value=None):
            with self.assertRaises(cli.StorageReaderError) as caught:
                cli.preflight_reader()
        self.assertEqual(caught.exception.code, 'storage_reader_missing')
        self.assertIn('npm --prefix mcp-server ci', str(caught.exception))

    def test_failed_reader_is_typed(self):
        with patch.object(cli, 'wrangler_bin', return_value='wrangler'), patch.object(cli.subprocess, 'run') as run, patch('shutil.which', return_value='pdftotext'):
            run.side_effect = [type('Result', (), {'returncode': 0})(), type('Result', (), {'returncode': 1})()]
            with self.assertRaises(cli.StorageReaderError) as caught:
                cli.preflight_reader()
        self.assertEqual(caught.exception.code, 'storage_reader_unavailable')

    def test_cli_refuses_before_scan(self):
        output = io.StringIO()
        with patch.object(sys, 'argv', ['backfill']), patch.object(cli, 'wrangler_bin', return_value=None), patch.object(cli, 'backfill') as scan, redirect_stderr(output):
            self.assertEqual(cli.main(), 1)
        scan.assert_not_called()
        self.assertIn('storage_reader_missing', output.getvalue())
        self.assertIn('npm --prefix mcp-server ci', output.getvalue())

    def test_cli_reports_document_failures_and_nonzero(self):
        output = io.StringIO()
        result = {'errors': [{'document_ref': 'doc-1', 'code': 'document_processing_failed'}], 'produced': 1}
        with patch.object(sys, 'argv', ['backfill']), patch.object(cli, 'preflight_reader'), patch.object(cli, 'backfill', return_value=result), redirect_stdout(output):
            self.assertEqual(cli.main(), 1)
        self.assertEqual(json.loads(output.getvalue()), {'ok': False, **result})

    def test_parser_refusal_before_document_work(self):
        for available, side_effect, expected in [
            (None, None, 'parser_missing'),
            ('pdftotext', OSError('PRIVATE_SENTINEL'), 'parser_unavailable'),
        ]:
            with self.subTest(expected=expected):
                output = io.StringIO()
                with patch.object(sys, 'argv', ['backfill']), patch('shutil.which', return_value=available), patch.object(cli, 'wrangler_bin', return_value='wrangler'), patch.object(cli.subprocess, 'run', side_effect=side_effect) as run, patch('lib.record_call.call_verb') as call, redirect_stderr(output):
                    run.return_value.returncode = 0
                    call.return_value.ok = True
                    call.return_value.reply = {'documents': []}
                    self.assertEqual(cli.main(), 1)
                call.assert_not_called()
                reply = json.loads(output.getvalue())
                self.assertEqual(reply['error'], expected)
                self.assertIn('poppler', reply['remedy'])
                self.assertNotIn('PRIVATE_SENTINEL', output.getvalue())

    def test_dry_run_needs_no_tools(self):
        output = io.StringIO()
        with patch.object(sys, 'argv', ['backfill', '--dry-run']), patch('shutil.which', return_value=None), patch.object(cli, 'wrangler_bin', return_value=None), patch('lib.record_call.call_verb') as call, redirect_stdout(output):
            call.return_value.ok = True
            call.return_value.reply = {'documents': []}
            self.assertEqual(cli.main(), 0)
        self.assertEqual(json.loads(output.getvalue())['scanned'], 0)

if __name__ == '__main__':
    unittest.main()
