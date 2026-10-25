#!/usr/bin/env python3
"""Capability proofs must never borrow an ambient database connection."""
import contextlib
import io
import os
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
proof = runpy.run_path(str(ROOT / 'ops/capability-program-selftest.py'))


class CapabilityDatabaseSafety(unittest.TestCase):
    def run_proof(self, target=None, **ambient):
        environment = {'DATABASE_URL': 'postgresql://fixture@remote.invalid/client', **ambient}
        if target is not None:
            environment['CARR_CI_DATABASE_URL'] = target
        proof['FAILED'].clear()
        with patch.dict(os.environ, environment, clear=True), \
                patch('psycopg.connect', side_effect=InterruptedError('connection boundary')) as connect, \
                contextlib.redirect_stdout(io.StringIO()):
            try:
                proof['tier2']()
            except InterruptedError:
                pass
        return connect

    def test_ambient_database_does_not_enable_writes(self):
        self.run_proof().assert_not_called()

    def test_explicit_unsafe_targets_fail_before_connecting(self):
        for target in (
            'postgresql://fixture@remote.invalid/client',
            'postgresql://fixture@127.0.0.1/test?host=remote.invalid',
            'postgresql://fixture@127.0.0.1/test?hostaddr=192.0.2.1',
            'postgresql://fixture@127.0.0.1,remote.invalid/test',
            'postgresql:///test',
            'postgresql://fixture@127.0.0.1/test?service=production',
            'not a DSN',
        ):
            with self.subTest(target=target):
                self.run_proof(target).assert_not_called()
                self.assertTrue(proof['FAILED'], 'unsafe explicit targets must fail')

    def test_literal_loopback_reaches_the_connection_without_ambient_redirects(self):
        for host in ('127.0.0.1', '[::1]'):
            target = f'postgresql://fixture@{host}:55432/test'
            connect = self.run_proof(target, PGHOSTADDR='192.0.2.1', PGSERVICE='production')
            connect.assert_called_once()
            from psycopg.conninfo import conninfo_to_dict
            info = conninfo_to_dict(connect.call_args.args[0], **connect.call_args.kwargs)
            self.assertEqual(info['hostaddr'], host.strip('[]'))
            self.assertEqual(info['service'], '')
            self.assertFalse(proof['FAILED'])


if __name__ == '__main__':
    unittest.main()
