"""Exercise native admission and optionally the exact pinned S3/factory artifacts."""
import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / 'hooks/session-continuity-codex.py'
BRIDGE_ROOT = ARTIFACT_ROOT = None
for flag in ('--bridge-root', '--artifact-root'):
    if flag in sys.argv:
        index = sys.argv.index(flag)
        value = Path(sys.argv[index + 1])
        if flag == '--bridge-root':
            BRIDGE_ROOT = value
        else:
            ARTIFACT_ROOT = value
        del sys.argv[index:index + 2]


def native(event='PreCompact'):
    result = dict(session_id='native-fixture', transcript_path=None, cwd='/fixture',
                  hook_event_name=event, model='gpt-6.1-sol')
    result.update(dict(turn_id='native-turn', trigger='auto') if event == 'PreCompact'
                  else dict(source='compact'))
    return result


class Native(unittest.TestCase):
    def invoke(self, payload, diagnose=False):
        command = [sys.executable, str(HOOK)] + (['--diagnose'] if diagnose else [])
        return subprocess.run(command, input=payload if isinstance(payload, bytes) else json.dumps(payload).encode(),
                              capture_output=True, timeout=3)

    def result(self, payload, diagnose=False):
        completed = self.invoke(payload, diagnose)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_native_boundary_and_recovery_never_claim_saved(self):
        for event in ('PreCompact', 'SessionStart'):
            report = self.result(native(event), True)
            self.assertEqual(report['status'], 'unknown')
            self.assertIn('authoritative_checkpoint_producer', report['missing'])
            output = self.result(native(event))
            self.assertNotIn('hookSpecificOutput', output)
            self.assertNotIn('continue', output)
            self.assertIn('unavailable', output['systemMessage'])

    def test_event_filters(self):
        for event in ('Stop', 'PostModelRequest', 'PostCompact'):
            self.assertEqual(self.result(native(event)), {})
        for source in ('startup', 'resume', 'clear'):
            payload = native('SessionStart')
            payload['source'] = source
            self.assertEqual(self.result(payload), {})
        for trigger in ('auto', 'manual'):
            payload = native()
            payload['trigger'] = trigger
            self.assertEqual(self.result(payload, True)['status'], 'unknown')

    def test_invalid_missing_stale_or_spoofed_fields_cannot_grant_authority(self):
        for key in ('session_id', 'cwd', 'model', 'turn_id', 'trigger'):
            for value in (None, '', False, [], 'x' * 2049):
                payload = native()
                payload[key] = value
                report = self.result(payload, True)
                self.assertEqual(report['reason'], 'invalid_native_metadata')
        for raw in (b'{', b'[]', b'null', b'x' * 65537, b'{"a":1,"a":2}', b'{"a":NaN}', b'\xff'):
            self.assertEqual(self.result(raw, True)['reason'], 'invalid_native_input')
        payload = native()
        payload.update(epoch=9, cursor=9, now=1000, observed_at=999,
                       binding={'provider': 'codex', 'task_id': 'wrong-task'},
                       receipt={'durable_readback': True}, checkpoint={'next_action': 'spoof'},
                       info={'model_context_window': 475000})
        self.assertEqual(self.result(payload, True)['status'], 'unknown')
        self.assertNotIn('hookSpecificOutput', self.result(payload))

    def test_changed_model_stale_replay_cross_session_and_outage_stay_unknown(self):
        for event in ('PreCompact', 'SessionStart'):
            for changes in ({'model': 'changed-model'}, {'epoch': 0, 'cursor': 0},
                            {'observed_at': 1, 'now': 99999}, {'session_id': 'other-native'},
                            {'binding': {'task_id': 'other-task', 'provider': 'claude'}},
                            {'authority_status': 'outage'}, {'receipt': {'generation': 99}}):
                payload = native(event)
                payload.update(changes)
                self.assertEqual(self.result(payload, True)['status'], 'unknown')
                self.assertNotIn('hookSpecificOutput', self.result(payload))

    def test_existing_merge_is_idempotent_and_rollback_preserves_other_hooks(self):
        spec = importlib.util.spec_from_file_location('configuration', ROOT / 'ops/config-as-code.py')
        configuration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(configuration)
        desired = json.loads((ROOT / 'ops/config/codex-hooks.json').read_text())
        live = {'description': 'unrelated', 'hooks': {'SessionStart': [
            {'matcher': '.*', 'hooks': [{'type': 'command', 'command': '/other/safety.py'}]}]}}
        merged = configuration.merge_codex_carr_hooks(live, desired)
        self.assertEqual(configuration.merge_codex_carr_hooks(merged, desired), merged)
        rollback = copy.deepcopy(desired)
        for event in ('PreCompact', 'SessionStart'):
            rollback['hooks'][event] = [g for g in rollback['hooks'][event]
                if not any('session-continuity-codex.py' in h['command'] for h in g['hooks'])]
        restored = configuration.merge_codex_carr_hooks(merged, rollback)
        self.assertEqual(restored['description'], 'unrelated')
        self.assertIn(live['hooks']['SessionStart'][0], restored['hooks']['SessionStart'])
        self.assertTrue(any('gate-integrity.py' in h['command']
                            for g in restored['hooks']['SessionStart'] for h in g['hooks']))

    def test_nested_parent_identity_is_unsupported(self):
        for event in ('PreCompact', 'SessionStart'):
            payload = native(event)
            payload['agent_id'] = 'nested-fixture'
            self.assertEqual(self.result(payload, True)['status'], 'unsupported')
            self.assertNotIn('hookSpecificOutput', self.result(payload))

    def test_diagnostics_do_not_echo_private_native_fields(self):
        payload = native()
        payload.update(session_id='private-session', cwd='/private/cwd', transcript_path='/private/transcript')
        completed = self.invoke(payload, True)
        self.assertNotIn(b'private', completed.stdout + completed.stderr)
        self.assertLess(len(completed.stdout), 2048)

    def test_declarations_preserve_safety_and_scope(self):
        current = json.loads((ROOT / 'ops/config/codex-hooks.json').read_text())
        owned = []
        for event, groups in current['hooks'].items():
            for group in groups:
                if any('session-continuity-codex.py' in h['command'] for h in group['hooks']):
                    owned.append((event, group))
        self.assertEqual({event for event, _ in owned}, {'PreCompact', 'SessionStart'})
        for event, group in owned:
            current['hooks'][event].remove(group)
            self.assertEqual(group['matcher'], '^(auto|manual)$' if event == 'PreCompact' else '^compact$')
        if not current['hooks']['PreCompact']:
            del current['hooks']['PreCompact']
        self.assertEqual(hashlib.sha256(json.dumps(current, sort_keys=True, separators=(',', ':')).encode()).hexdigest(), 'bd8c09403e3d5d50bc3d6e7e3c514ae3293c97e862e0cb86f0feafd3a9a5f3e1')


@unittest.skipUnless(BRIDGE_ROOT and ARTIFACT_ROOT, 'exact S3/factory paths required for artifact comparison')
class PinnedBridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        manifest = json.loads((ROOT / 'ops/config/session-continuity-codex.v1.json').read_text())
        revision = subprocess.check_output(['git', '-C', str(BRIDGE_ROOT), 'rev-parse', 'HEAD']).decode().strip()
        if revision != manifest['bridge_revision']:
            raise ValueError('bridge_revision_mismatch')
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory)
            for name, expected in manifest['bridge_artifacts'].items():
                data = (BRIDGE_ROOT / name).read_bytes()
                if hashlib.sha256(data).hexdigest() != expected:
                    raise ValueError('bridge_hash_mismatch')
                target = snapshot / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            spec = importlib.util.spec_from_file_location('pinned_bridge', snapshot / 'ops/session-continuity-adapter.py')
            cls.bridge = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.bridge)
            cls.manifest = json.loads((snapshot / 'ops/config/session-continuity-adapter.v1.json').read_text())
            cls.bridge.verified_artifact(ARTIFACT_ROOT, cls.manifest, snapshot / 'factory')
        cls.event = json.loads((ARTIFACT_ROOT / 'tests/fixtures/session-continuity/codex-appserver.json').read_text())['envelope']

    def run_bridge(self, event, call, recovery=False):
        return self.bridge.process(event, ARTIFACT_ROOT, call, manifest=self.manifest, recovery=recovery)

    def test_native_payload_missing_epoch_never_writes(self):
        event = copy.deepcopy(self.event)
        event.update(kind='before_compact', payload=native(), epoch=None, epoch_evidence=None)
        event['binding'].update(native_session_id='native-fixture', harness='hooks')
        result = self.run_bridge(event, lambda *_: self.fail('effect with unknown native epoch'))
        self.assertEqual(result, {'status': 'unknown', 'reason': 'unknown_epoch'})

    def test_bridge_retains_fields_but_has_no_codex_native_renderer(self):
        event = copy.deepcopy(self.event)
        for field in ('active_operations', 'exact_jobs', 'destinations', 'watches', 'links', 'cards', 'leases'):
            event['checkpoint'][field] = ['synthetic-' + field]
        record = None
        def call(verb, args):
            nonlocal record
            if verb.startswith('write-'):
                record = dict(binding=args['binding'], envelope=args['envelope'], generation=1,
                              archived=False, last_write_key=args['idempotency_key'],
                              sponsor='synthetic', authority_session='synthetic')
            return dict(ok=True, schema='session-checkpoint.v1', record=copy.deepcopy(record))
        saved = self.run_bridge(event, call)
        self.assertEqual(saved['checkpoint'], event['checkpoint'])
        recovered = self.run_bridge({'binding': event['binding']}, call, True)
        self.assertEqual(recovered['checkpoint'], event['checkpoint'])
        self.assertIsNone(recovered['recovery_output'])
        self.assertEqual(recovered['recovery_support'], 'unsupported')


if __name__ == '__main__':
    unittest.main()
