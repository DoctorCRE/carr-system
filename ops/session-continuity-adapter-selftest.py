"""Synthetic transport tests; fixture receipts never claim CARR persistence."""
import copy
import hashlib
import importlib.util
import json
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
            self.record = dict(binding=args['binding'], envelope=args['envelope'], generation=args['envelope']['expected_generation'] + 1,
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
        with self.assertRaisesRegex(ValueError, 'changed_checkpoint_replay'):
            self.run_event(changed)
        stale = copy.deepcopy(self.event)
        stale['cursor'] += 1
        with self.assertRaisesRegex(ValueError, 'cas_conflict'):
            self.run_event(stale)
        with self.assertRaises(OSError):
            self.run_event(call=lambda *_: (_ for _ in ()).throw(OSError('synthetic outage')))
        self.record['envelope']['cursor'] = 9
        with self.assertRaises(ValueError):
            self.run_event(call=lambda verb, args: (_ for _ in ()).throw(TimeoutError()) if verb.startswith('write-') else self.call(verb, args))

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
        # Below threshold produces no authority effect and claims no protection.
        self.assertEqual(self.run_event(reset)['status'], 'continue')

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
