"""Verify bounded mail proof reads, canonical evidence and process cleanup."""
import copy
from contextlib import redirect_stdout
import io
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'tools/prove-mail-needs-partner.py'
spec = importlib.util.spec_from_file_location('proof', SCRIPT)
assert spec is not None and spec.loader is not None
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


class MailProofTests(unittest.TestCase):
    def setUp(self):
        self.bundle = json.loads((ROOT / 'tools/fixtures/mail-needs-partner.json').read_text())

    def test_pass_and_failure_cli_exits(self):
        command = [sys.executable, str(SCRIPT), '--fixture', str(ROOT / 'tools/fixtures/mail-needs-partner.json')]
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
        self.assertEqual(subprocess.run(command + ['--fixture-failure'], capture_output=True).returncode, 78)

    def test_leak_resolved_unknown_and_unavailable_refuse(self):
        for defect in ['leak', 'resolved', 'unknown', 'unavailable']:
            bundle = copy.deepcopy(self.bundle)
            if defect == 'leak':
                bundle['other']['items'] = bundle['primary']['items']
                bundle['other']['state'] = 'ready'
            elif defect == 'resolved':
                bundle['canonical']['proposals'][0]['status'] = 'confirmed'
            elif defect == 'unknown':
                bundle['primary']['items'][0]['source']['version'] = None
            else:
                bundle['primary'].update(state='unavailable', items=[])
            with self.subTest(defect=defect), self.assertRaises(ValueError):
                proof.observe(bundle)


class FakeProcess:
    pid = 99999999

    def __init__(self, returncode, output):
        self.returncode, self.output = returncode, output

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def communicate(self, **kwargs):
        return self.output, None


class ProductionTransportTests(unittest.TestCase):
    def setUp(self):
        self.bundle = json.loads((ROOT / 'tools/fixtures/mail-needs-partner.json').read_text())
        self.args = SimpleNamespace(merged_sha='a' * 40, primary_call_wrapper=ROOT / 'run.sh',
                                    other_call_wrapper=ROOT / 'out/second-partner-wrapper')
        self.calls = []

    def run_production(self, defect=None):
        def invoke(command, **kwargs):
            self.calls.append(command)
            verb, payload = command[2], json.loads(command[3])
            if defect == 'auth':
                return FakeProcess(1, b'private diagnostic')
            if verb == 'read-mail-needs-partner':
                value = self.bundle['primary' if command[0] == str(ROOT / 'run.sh') else 'other']
            else:
                self.assertEqual(payload['status'], 'pending')
                self.assertTrue(payload['include_non_deal'])
                value = self.bundle['canonical']
                if defect == 'pagination' and 'cursor' not in payload:
                    value = dict(value, proposals=[], more=True, next_cursor='b' * 36)
                elif defect == 'cap':
                    value = dict(value, proposals=[], more=True, next_cursor=str(len(self.calls)))
                elif defect == 'resolved':
                    value = copy.deepcopy(value)
                    value['proposals'][0]['status'] = 'confirmed'
            return FakeProcess(0, json.dumps(value).encode())
        reply = SimpleNamespace(status=200, body=json.dumps({'git_sha': {'value': 'a' * 40}}).encode())
        with patch.object(proof, 'parsed', side_effect=lambda value: value), patch.object(proof.release_reader, 'read', return_value=reply) as read, patch.object(proof.subprocess, 'Popen', side_effect=invoke), patch.object(proof.os, 'killpg'):
            result = proof.production(self.args)
        read.assert_called_once_with('https://api.doctorcre.com/release', timeout=10, max_bytes=proof.MAX_BYTES)
        return result

    def test_existing_partner_wrappers_and_contemporaneous_canonical_paging(self):
        self.assertEqual(self.run_production('pagination')['state'], 'observed')
        self.assertEqual([c[2] for c in self.calls], ['read-mail-needs-partner', 'read-mail-needs-partner',
                                                     'list-email-proposals', 'list-email-proposals'])
        self.assertNotEqual(self.calls[0][0], self.calls[1][0])

    def test_auth_resolved_and_read_cap_fail_closed(self):
        for defect in ['auth', 'resolved', 'cap']:
            self.calls.clear()
            with self.subTest(defect=defect), self.assertRaises(ValueError):
                self.run_production(defect)
            self.assertLessEqual(len(self.calls), 22)

    def test_wrapper_timeout_disposes_owned_process_group(self):
        reply = SimpleNamespace(status=200, body=json.dumps({'git_sha': {'value': 'a' * 40}}).encode())
        process = FakeProcess(0, b'')
        with patch.object(proof.release_reader, 'read', return_value=reply), patch.object(proof.subprocess, 'Popen', return_value=process), patch.object(process, 'communicate', side_effect=subprocess.TimeoutExpired('synthetic-wrapper', 10)), patch.object(proof.os, 'killpg') as kill:
            with self.assertRaises(subprocess.TimeoutExpired):
                proof.production(self.args)
        kill.assert_called_once_with(process.pid, proof.signal.SIGKILL)

    def test_missing_second_partner_and_merge_identity_refuse(self):
        for missing in ['merged_sha', 'other_call_wrapper']:
            args = copy.copy(self.args)
            setattr(args, missing, None)
            reply = SimpleNamespace(status=200, body=json.dumps({'git_sha': {'value': 'a' * 40}}).encode())
            with self.subTest(missing=missing), patch.object(proof.release_reader, 'read', return_value=reply), patch.object(proof.subprocess, 'Popen') as launch:
                with self.assertRaises(ValueError):
                    proof.production(args)
                launch.assert_not_called()

    def test_real_wrappers_use_real_parser_and_timeout_reaps_process(self):
        reply = SimpleNamespace(status=200, body=json.dumps({'git_sha': {'value': 'a' * 40}}).encode())
        with tempfile.TemporaryDirectory(prefix='wr648-proof-') as directory:
            folder = Path(directory)
            fixture = folder / 'bundle.json'
            fixture.write_text(json.dumps(self.bundle))
            for partner in ['primary', 'other']:
                wrapper = folder / partner
                wrapper.write_text('#!' + sys.executable + '\nimport json,sys\nfrom pathlib import Path\nbundle=json.loads(Path(' + repr(str(fixture)) + ').read_text())\nprint(json.dumps(bundle["canonical" if sys.argv[2] == "list-email-proposals" else ' + repr(partner) + ']))\n')
                wrapper.chmod(0o700)
            args = SimpleNamespace(merged_sha='a' * 40, primary_call_wrapper=folder / 'primary', other_call_wrapper=folder / 'other')
            with patch.object(proof.release_reader, 'read', return_value=reply):
                self.assertEqual(proof.production(args)['state'], 'observed')
            pid_file = folder / 'pid'
            args.primary_call_wrapper.write_text('#!' + sys.executable + '\nimport os,time\nfrom pathlib import Path\nPath(' + repr(str(pid_file)) + ').write_text(str(os.getpid()))\ntime.sleep(30)\n')
            with patch.object(proof.release_reader, 'read', return_value=reply), patch.object(proof.time, 'monotonic', side_effect=[0, 59.5]), redirect_stdout(io.StringIO()) as receipt:
                self.assertEqual(proof.main(['--production', '--natural', '--merged-sha', args.merged_sha,
                                             '--primary-call-wrapper', str(args.primary_call_wrapper),
                                             '--other-call-wrapper', str(args.other_call_wrapper)]), 78)
            self.assertEqual(json.loads(receipt.getvalue())['state'], 'insufficient_evidence')
            pid = int(pid_file.read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
