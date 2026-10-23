"""Exercise the approval authority with synthetic stores, clocks and OS identities.

No privileged commands run. Only fixture ownership checks are replaced when
exercising durable storage; separate tests retain the real privilege checks.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import outlook_approval as approval

NOW = 1_800_000_000
BINDING = {'account': 'sender@example.test', 'kind': 'send', 'operation_key': 'synthetic-1',
           'fields': {'to': 'recipient@example.test', 'cc': '', 'bcc': '',
                      'subject': 'Synthetic subject', 'body': 'Synthetic body'}}
REFERENCE = 'decision:11111111-2222-4333-8444-555555555555'


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'authority'
        self.addCleanup(self.temp.cleanup)
        self.trust = patch.object(approval, '_trusted')
        self.trust.start()
        self.addCleanup(self.trust.stop)
        self.clock = patch.object(approval.time, 'time', return_value=NOW).start()
        self.addCleanup(patch.stopall)
        with patch.object(approval.os, 'geteuid', return_value=0):
            self.authority = approval.Authority(self.root)

    def test_one_consumer_wins_and_replay_fails_after_restart(self):
        receipt = self.authority.issue(BINDING, NOW + 60)

        def consume(_):
            try:
                return self.authority.consume(receipt, BINDING)
            except (approval.Refused, OSError):
                return None

        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(consume, range(8)))
        self.assertEqual(replies.count(NOW + 60), 1)
        with patch.object(approval.os, 'geteuid', return_value=0):
            restarted = approval.Authority(self.root)
        with self.assertRaises(FileNotFoundError):
            restarted.consume(receipt, BINDING)

    def test_expiry_boundary_and_clock_rollback_refuse(self):
        receipt = self.authority.issue(BINDING, NOW + 60)
        for now in (NOW - 1, NOW + 60, NOW + 61):
            self.clock.return_value = now
            with self.assertRaises(approval.Refused):
                self.authority.consume(receipt, BINDING)

    def test_invalid_lifetimes_are_not_issued(self):
        for expires in (NOW, NOW + 901, float('nan'), float('inf'), True):
            with self.subTest(expires=expires), self.assertRaises(approval.Refused):
                self.authority.issue(BINDING, expires)

    def test_operation_key_and_complete_binding_are_required(self):
        receipt = self.authority.issue(BINDING, NOW + 60)
        changed = deepcopy(BINDING)
        changed['operation_key'] = 'synthetic-2'
        with self.assertRaises(approval.Refused):
            self.authority.consume(receipt, changed)
        del changed['fields']['bcc']
        with self.assertRaises(approval.Refused):
            self.authority.consume(receipt, changed)
        self.assertEqual(self.authority.consume(receipt, BINDING), NOW + 60)

    def test_standing_instruction_scope_survives_new_operation_identity(self):
        instruction = self.authority.register(BINDING, REFERENCE, 'Synthetic repeated note', NOW + 3600)
        another = {**BINDING, 'operation_key': 'synthetic-2'}
        receipt = self.authority.issue(another, NOW + 60, instruction)
        self.assertEqual(self.authority.consume(receipt, another), NOW + 60)

    def test_standing_instruction_must_exist_match_and_remain_active(self):
        with self.assertRaises(OSError):
            self.authority.issue(BINDING, NOW + 60, str(uuid.uuid4()))
        instruction = self.authority.register(BINDING, REFERENCE, 'Synthetic exact note', NOW + 60)
        changed = deepcopy(BINDING)
        changed['fields']['to'] = 'different@example.test'
        with self.assertRaises(approval.Refused):
            self.authority.issue(changed, NOW + 60, instruction)
        receipt = self.authority.issue(BINDING, NOW + 60, instruction)
        (self.root / ('instruction-' + instruction)).unlink()
        with self.assertRaises(OSError):
            self.authority.consume(receipt, BINDING)

    def test_minimal_store_never_retains_message_or_addresses(self):
        instruction = self.authority.register(BINDING, REFERENCE, 'Synthetic exact note', NOW + 60)
        receipt = self.authority.issue(BINDING, NOW + 60, instruction)
        self.authority.consume(receipt, BINDING)
        text = ''.join(path.read_text() for path in self.root.iterdir())
        for sentinel in (BINDING['account'], *BINDING['fields'].values()):
            if sentinel:
                self.assertNotIn(sentinel, text)


class PrivilegeTests(unittest.TestCase):
    def test_unprivileged_sender_cannot_issue_or_open_authority(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(approval.os, 'geteuid', return_value=501):
            with self.assertRaises(approval.Refused):
                approval.Authority(Path(temp) / 'forged')
            for command in ('approve', 'register-instruction', 'issue-standing', 'consume'):
                stdout, stderr = StringIO(), StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    self.assertEqual(approval.main([command]), 1)
                self.assertEqual(stdout.getvalue(), '')
                self.assertEqual(stderr.getvalue(), 'approval_refused\n')
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_sender_owned_helper_or_receipt_cannot_be_trusted(self):
        with tempfile.TemporaryDirectory() as temp:
            helper = Path(temp) / 'forged-helper'
            helper.write_text('forged')
            helper.chmod(0o755)
            with patch.object(approval, 'HELPER', helper), patch.object(approval.subprocess, 'run') as run:
                with self.assertRaises(approval.Refused):
                    approval.consume(str(uuid.uuid4()), BINDING)
                run.assert_not_called()

    def test_client_sends_only_fixed_consume_command_and_private_stdin(self):
        receipt = str(uuid.uuid4())
        reply = subprocess.CompletedProcess([], 0, json.dumps({'expires_at': NOW + 60}))
        with patch.object(approval, '_trusted'), patch.object(approval.time, 'time', return_value=NOW), \
                patch.object(approval.subprocess, 'run', return_value=reply) as run:
            self.assertEqual(approval.consume(receipt, BINDING), NOW + 60)
        args, kwargs = run.call_args
        self.assertEqual(args[0], ['/usr/bin/sudo', '-n', '--', str(approval.HELPER), 'consume'])
        self.assertEqual(json.loads(kwargs['input']), {'approval_id': receipt, 'binding': BINDING})
        for sentinel in (BINDING['account'], 'Synthetic subject', 'Synthetic body'):
            self.assertNotIn(sentinel, ' '.join(args[0]))

    def test_bad_or_unavailable_helper_response_fails_closed(self):
        for response in ('{}', 'true', '[]', '{"expires_at":true}', '{"expires_at":1800000000}',
                         '{"expires_at":NaN}', 'not json'):
            with self.subTest(response=response), patch.object(approval, '_trusted'), \
                    patch.object(approval.time, 'time', return_value=NOW), \
                    patch.object(approval.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, response)):
                with self.assertRaises(approval.Refused):
                    approval.consume(str(uuid.uuid4()), BINDING)
        with patch.object(approval, '_trusted'), patch.object(approval.subprocess, 'run',
                side_effect=subprocess.TimeoutExpired(['helper'], 10, stderr='Synthetic body')):
            with self.assertRaises(approval.Refused):
                approval.consume(str(uuid.uuid4()), BINDING)


if __name__ == '__main__':
    unittest.main()
