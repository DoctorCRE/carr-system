"""Synthetic transport tests; fixture receipts never claim CARR persistence."""
import copy
import hashlib
import importlib.util
import json
import io
import urllib.error
from email.message import Message
from unittest.mock import patch
from pathlib import Path
import sys
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('bridge', Path(__file__).with_name('session-continuity-adapter.py'))
assert SPEC is not None and SPEC.loader is not None
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)
ROOT = Path(sys.argv[sys.argv.index('--artifact-root') + 1]) if '--artifact-root' in sys.argv else None
if ROOT:
    del sys.argv[sys.argv.index('--artifact-root'):sys.argv.index('--artifact-root') + 2]


class Pins(unittest.TestCase):
    def test_missing_revision_or_file_fails_before_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises((FileNotFoundError, ValueError, bridge.subprocess.CalledProcessError)):
                bridge.process({}, directory, lambda *_: self.fail('effect before verification'))

    def test_transport_budget_and_no_redirect(self):
        with self.assertRaises(ValueError):
            bridge.McpTransport('https://other.invalid', 'synthetic')
        transport = bridge.McpTransport('https://api.doctorcre.com/mcp', 'synthetic')
        transport.requests = 3
        with self.assertRaisesRegex(ValueError, 'request_budget'):
            transport('read-session-checkpoint-v1', {})

    def test_redirect_never_forwards_credential(self):
        for code in (307, 303):
            requests = []
            def open_request(opener, request, timeout=None):
                requests.append(request)
                request.timeout = timeout
                if len(requests) > 1:
                    return io.BytesIO(b'{}')
                # urllib already refuses POST 307. Probe GET as well to ensure
                # this policy survives any method without forwarding headers.
                if code == 307:
                    request.method = 'GET'
                headers = Message()
                headers['Location'] = 'https://redirect.invalid/target'
                return opener.error('http', request, io.BytesIO(), code, 'redirect', headers)
            with patch.object(bridge.urllib.request.OpenerDirector, 'open', new=open_request):
                with self.assertRaises(ValueError):
                    bridge.McpTransport('https://api.doctorcre.com/mcp', 'synthetic')('read-session-checkpoint-v1', {})
            self.assertEqual(len(requests), 1, 'credential forwarded to redirect host')
            self.assertEqual(requests[0].get_header('Authorization'), 'Bearer synthetic')

    def test_socket_and_slow_drip_deadlines(self):
        clock = [0]
        class SlowResponse(io.BytesIO):
            def read1(self, size):
                clock[0] += 3
                return super().read1(1)
        def open_request(request, timeout):
            self.assertEqual(timeout, 8)
            return SlowResponse(bridge.encoded({'result': {'structuredContent': {'ok': True}}}))
        with patch.object(bridge.urllib.request.OpenerDirector, 'open', side_effect=open_request), patch.object(bridge.time, 'monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(TimeoutError):
                bridge.McpTransport('https://api.doctorcre.com/mcp', 'synthetic')('read-session-checkpoint-v1', {})


@unittest.skipUnless(ROOT, 'supply --artifact-root for pinned factory executable proof; no download')
class Artifact(unittest.TestCase):
    def setUp(self):
        self.event = json.loads((ROOT / 'tests/fixtures/session-continuity/codex-appserver.json').read_text())['envelope']
        self.record = None
        self.calls = []
        self.timeout = False

    def call(self, verb, args):
        self.calls.append((verb, copy.deepcopy(args)))
        if verb.startswith('write-'):
            if self.record:
                if self.record['last_write_key'] == args['idempotency_key']:
                    if self.record['envelope'] != args['envelope']:
                        raise ValueError('changed_checkpoint_replay')
                elif self.record['generation'] != args['envelope']['expected_generation']:
                    raise ValueError('checkpoint_cas_conflict')
            self.record = dict(binding=copy.deepcopy(args['binding']), envelope=copy.deepcopy(args['envelope']), generation=args['envelope']['expected_generation'] + 1,
                               archived=False, last_write_key=args['idempotency_key'], sponsor='synthetic', authority_session='synthetic')
            if self.timeout:
                raise TimeoutError('synthetic ambiguous response')
        return {'ok': True, 'schema': 'session-checkpoint.v1', 'record': copy.deepcopy(self.record)}

    def run_event(self, event=None, call=None, **kwargs):
        return bridge.process(event or self.event, ROOT, call or self.call, **kwargs)

    def test_exact_artifact_save_replay_recovery_and_archive(self):
        first = self.run_event()
        self.assertEqual(first['status'], 'saved')
        self.assertEqual(self.run_event(), first)
        self.assertEqual(self.run_event({'binding': self.event['binding']}, recovery=True)['checkpoint'], first['checkpoint'])
        self.record['archived'] = True
        self.assertEqual(self.run_event()['reason'], 'checkpoint_archived')
        self.assertTrue(self.run_event({'binding': self.event['binding']}, recovery=True)['receipt']['archived'])

    def test_changed_replay_stale_cas_outage_and_ambiguous_exact_readback(self):
        self.timeout = True
        self.assertEqual(self.run_event()['status'], 'saved')
        self.assertEqual(sum(v.startswith('write-') for v, _ in self.calls), 1)
        self.timeout = False
        changed = copy.deepcopy(self.event)
        changed['checkpoint']['next_action'] = 'changed'
        self.assertEqual(self.run_event(changed), {'status': 'degraded', 'reason': 'changed_replay'})
        stale = copy.deepcopy(self.event)
        stale['cursor'] += 1
        with self.assertRaisesRegex(ValueError, 'cas_conflict'):
            self.run_event(stale)
        with self.assertRaises(OSError):
            self.run_event(call=lambda *_: (_ for _ in ()).throw(OSError('synthetic outage')))
        self.record['envelope']['cursor'] = 9
        self.assertEqual(self.run_event()['reason'], 'old_replay')

    def test_countermodel_contexts(self):
        for mutate, expected in [
            (lambda e: e['payload']['tokenUsage']['last'].update(totalTokens=100), 'continue'),
            (lambda e: e.update(now=1061), 'unknown'),
            (lambda e: e.update(usage_model_fingerprint='changed'), 'unknown'),
            (lambda e: e['payload']['tokenUsage']['last'].update(totalTokens=None), 'unknown'),
            (lambda e: e.update(epoch_evidence=''), 'unknown'),
        ]:
            event = copy.deepcopy(self.event)
            mutate(event)
            self.assertEqual(self.run_event(event)['status'], expected)
        self.run_event()
        reset = copy.deepcopy(self.event)
        reset.update(cursor=2, expected_generation=1)
        reset['payload']['tokenUsage']['last']['totalTokens'] = 424999
        before = copy.deepcopy(self.record)
        self.calls.clear()
        self.assertEqual(self.run_event(reset), {'status': 'unknown', 'reason': 'counter_reset_without_new_epoch'})
        self.assertEqual(self.record, before)
        self.assertEqual([v for v, _ in self.calls], ['read-session-checkpoint-v1'])

    def test_missing_or_unequal_post_write_readback_refuses_protection(self):
        faults = {
            'missing': lambda record: None,
            'wrong_key': lambda record: dict(record, last_write_key='different'),
            'wrong_envelope': lambda record: dict(record, envelope=dict(record['envelope'], cursor=9)),
            'wrong_generation': lambda record: dict(record, generation=record['generation'] + 1),
            'archived': lambda record: dict(record, archived=True),
        }
        for ambiguous in (False, True):
            for fault, corrupt in faults.items():
                with self.subTest(ambiguous=ambiguous, readback=fault):
                    self.record = None
                    self.calls.clear()
                    self.timeout = ambiguous
                    transport = bridge.McpTransport('https://api.doctorcre.com/mcp', 'synthetic')

                    def open_request(request, timeout):
                        payload = json.loads(request.data)['params']
                        verb, args = payload['name'], payload['arguments']
                        result = self.call(verb, args)
                        if verb == 'read-session-checkpoint-v1' and len(self.calls) > 1:
                            result['record'] = corrupt(result['record'])
                        return io.BytesIO(bridge.encoded({'result': {'structuredContent': result}}))

                    with patch.object(bridge.urllib.request.OpenerDirector, 'open', side_effect=open_request):
                        with self.assertRaisesRegex(ValueError, '^authority_readback_mismatch$'):
                            self.run_event(call=transport)
                    self.assertEqual([v for v, _ in self.calls], [
                        'read-session-checkpoint-v1', 'write-session-checkpoint-v1', 'read-session-checkpoint-v1'])
                    self.assertEqual(transport.requests, 3)

    def test_old_and_changed_events_do_not_write(self):
        self.run_event()
        newer = copy.deepcopy(self.event)
        newer.update(cursor=2, expected_generation=1)
        self.run_event(newer)
        before = copy.deepcopy(self.record)
        for event, reason in [(self.event, 'old_replay'),
                              (dict(newer, checkpoint=dict(newer['checkpoint'], next_action='changed')), 'changed_replay')]:
            self.calls.clear()
            self.assertEqual(self.run_event(event), {'status': 'degraded', 'reason': reason})
            self.assertEqual(self.record, before)
            self.assertEqual([v for v, _ in self.calls], ['read-session-checkpoint-v1'])

    def test_below_threshold_reads_without_advancing_and_checks_generation(self):
        self.run_event()
        event = copy.deepcopy(self.event)
        event.update(cursor=2, epoch=2, expected_generation=1)
        event['payload']['tokenUsage']['last']['totalTokens'] = 100
        before = copy.deepcopy(self.record)
        self.calls.clear()
        self.assertEqual(self.run_event(event)['status'], 'continue')
        self.assertEqual(self.record, before)
        event['expected_generation'] = 0
        self.assertEqual(self.run_event(event), {'status': 'degraded', 'reason': 'cas_conflict', 'generation': 1})
        self.assertEqual([v for v, _ in self.calls], ['read-session-checkpoint-v1'] * 2)

    def test_http_refusals_do_not_retry(self):
        for verb, code in [('read-session-checkpoint-v1', 502), ('write-session-checkpoint-v1', 400)]:
            transport = bridge.McpTransport('https://api.doctorcre.com/mcp', 'synthetic')
            error = urllib.error.HTTPError(transport.endpoint, code, 'synthetic', {}, None)
            with patch.object(bridge.urllib.request.OpenerDirector, 'open', side_effect=error) as opener:
                with self.assertRaisesRegex(ValueError, 'authority_http_refusal'):
                    transport(verb, {})
            self.assertEqual(opener.call_count, 1)

    def test_5xx_write_uses_single_exact_readback(self):
        transport = bridge.McpTransport('https://api.doctorcre.com/mcp', 'synthetic')
        verbs = []
        def open_request(request, timeout):
            self.assertEqual(timeout, 8)
            payload = json.loads(request.data)['params']
            verb, args = payload['name'], payload['arguments']
            verbs.append(verb)
            result = self.call(verb, args)
            if verb.startswith('write-'):
                raise urllib.error.HTTPError(request.full_url, 502, 'synthetic', {}, None)
            return io.BytesIO(bridge.encoded({'result': {'structuredContent': result}}))
        with patch.object(bridge.urllib.request.OpenerDirector, 'open', side_effect=open_request):
            self.assertEqual(self.run_event(call=transport)['status'], 'saved')
        self.assertEqual(verbs, ['read-session-checkpoint-v1', 'write-session-checkpoint-v1', 'read-session-checkpoint-v1'])
        self.assertEqual(transport.requests, 3)

    def test_pin_inventory_and_changed_bytes(self):
        manifest = json.loads(bridge.MANIFEST.read_text())
        expected = {'contracts/session-continuity.v1.json','tools/session-continuity.py','lib/session_continuity.py',
                    'lib/session_adapters/claude.py','lib/session_adapters/codex.py','lib/session_adapters/grok.py'}
        self.assertEqual(set(manifest['artifacts']), expected)
        for name in expected:
            self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(), manifest['artifacts'][name])
        manifest['artifacts']['lib/session_adapters/codex.py'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'artifact_hash_mismatch'):
            self.run_event(manifest=manifest, call=lambda *_: self.fail('effect after wrong pin'))
        manifest['revision'] = '0' * 40
        with self.assertRaisesRegex(ValueError, 'artifact_revision_mismatch'):
            self.run_event(manifest=manifest)


if __name__ == '__main__':
    unittest.main()
