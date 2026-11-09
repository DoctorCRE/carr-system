import importlib.util
from pathlib import Path
import unittest
import io
import sys
from contextlib import redirect_stderr
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('backfill_cli', Path(__file__).resolve().parents[1] / 'bin/backfill-deal-document-text.py')
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
        with patch.object(cli, 'wrangler_bin', return_value='wrangler'), patch.object(cli.subprocess, 'run') as run:
            run.return_value.returncode = 1
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
