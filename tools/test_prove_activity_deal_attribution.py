"""Verify attribution proof contracts and refusal before production transport access."""
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'tools/prove-activity-deal-attribution.py'
FIXTURE = ROOT / 'tools/fixtures/activity-deal-attribution.json'
spec = importlib.util.spec_from_file_location('proof', RUNNER)
assert spec is not None and spec.loader is not None
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


class AttributionProofTests(unittest.TestCase):
    def test_contract_checked_fixture_passes(self):
        self.assertEqual(proof.validate(json.loads(FIXTURE.read_text()))['direct_count'], 1)

    def test_missing_natural_evidence_fails(self):
        cohort = json.loads(FIXTURE.read_text())
        cohort['responses'] = cohort['responses'][:1]
        with self.assertRaises(ValueError):
            proof.validate(cohort)

    def test_production_parser_rejects_false_credit(self):
        cohort = json.loads(FIXTURE.read_text())
        cohort['responses'][1]['credited_activity_count'] = 1
        with self.assertRaises(subprocess.CalledProcessError):
            proof.validate(cohort)

    def test_canonical_mismatch_fails(self):
        cohort = json.loads(FIXTURE.read_text())
        cohort['canonical'][0]['deal_id'] = None
        with self.assertRaises(ValueError):
            proof.validate(cohort)

    def test_cli_pass_and_failure_exits(self):
        for extra, expected in [([], 0), (['--fixture-failure'], 78)]:
            r = subprocess.run(['python3', str(RUNNER), '--fixture', str(FIXTURE), *extra], capture_output=True)
            self.assertEqual(r.returncode, expected)

    def test_production_without_private_evidence_refuses(self):
        r = subprocess.run(['python3', str(RUNNER), '--production', '--natural'], capture_output=True)
        self.assertEqual(r.returncode, 78)
        self.assertNotIn(b'cookie', r.stdout)

    def test_missing_merge_identity_refuses_before_any_read(self):
        with patch.object(proof, 'read', side_effect=AssertionError('HTTP read')), \
                patch.object(proof.subprocess, 'Popen', side_effect=AssertionError('process call')):
            self.assertEqual(proof.main(['--production', '--natural']), 78)


class ActorTransportTests(unittest.TestCase):
    def test_deployed_actor_wrapper_uses_contract_parser_and_canonical_timeline(self):
        from types import SimpleNamespace
        import tempfile
        import sys
        bundle = json.loads(FIXTURE.read_text())
        release = SimpleNamespace(status=200, body=json.dumps({
            'ok': True, 'env': {'value': 'production'}, 'git_sha': {'value': 'a' * 40}}).encode())
        with tempfile.TemporaryDirectory() as directory:
            wrapper = Path(directory) / 'actor'
            calls = Path(directory) / 'calls'
            wrapper.write_text('#!' + sys.executable + '\nimport json,sys\nfrom pathlib import Path\n'
                              + 'bundle=' + repr(bundle) + '\n'
                              + 'verb=sys.argv[2];args=json.loads(sys.argv[3])\n'
                              + 'with Path(' + repr(str(calls)) + ').open("a") as f:f.write(verb+"\\n")\n'
                              + "if verb=='email-router-queue':reply={'schema_version':'email-router-queue.v2','emails':[{'activity_id':r['activity_id'],'deal':r['deal_id']} for r in bundle['canonical']]}\n"
                              + "elif verb=='read-activity-deal-attribution':reply=next(r for r in bundle['responses'] if r['activity']['id']==args['activity_id'])\n"
                              + "elif verb=='get-deal-room':reply={'schema_version':'deal-timeline.v1','deal_id':args['deal'],'activities':[{'id':bundle['canonical'][0]['activity_id']}]}\n"
                              + 'else:sys.exit(1)\nprint(json.dumps(reply))\n')
            original = wrapper.read_text()
            wrapper.chmod(0o700)
            args = SimpleNamespace(merge_sha='a' * 40, call_wrapper=str(wrapper))
            with patch.object(proof, 'read', return_value=release):
                result = proof.production(args)
            self.assertEqual(result['direct_count'], 1)
            self.assertEqual(result['unresolved_count'], 1)
            self.assertEqual(calls.read_text().splitlines(), [
                'email-router-queue', 'read-activity-deal-attribution',
                'get-deal-room', 'read-activity-deal-attribution'])
            for bad in [
                original.replace("reply={'schema_version':'email-router-queue.v2'", "reply={'schema_version':'wrong'"),
                original.replace("'deal_id':args['deal']", "'deal_id':None"),
                original.replace("print(json.dumps(reply))", "print(json.dumps({'error':'unauthorized'}))"),
                original.replace("print(json.dumps(reply))", "reply.update(credited_activity_count=9) if verb=='read-activity-deal-attribution' else None\nprint(json.dumps(reply))"),
                original.replace("print(json.dumps(reply))", "reply['activity'].update(id='44444444-4444-4444-8444-444444444444') if verb=='read-activity-deal-attribution' else None\nprint(json.dumps(reply))"),
            ]:
                wrapper.write_text(bad)
                with patch.object(proof, 'read', return_value=release):
                    with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                        proof.production(args)


    def test_release_and_auth_failures_refuse(self):
        from types import SimpleNamespace
        args = SimpleNamespace(merge_sha='a' * 40, call_wrapper=str(ROOT / 'run.sh'))
        for release in [
            {'ok': True, 'env': {'value': 'production'}, 'git_sha': {'value': 'b' * 40}},
            {'ok': True, 'env': {'value': 'staging'}, 'git_sha': {'value': 'a' * 40}},
        ]:
            with patch.object(proof, 'read', return_value=SimpleNamespace(status=200, body=json.dumps(release).encode())), \
                    patch.object(proof.subprocess, 'Popen') as launch:
                with self.assertRaises(ValueError):
                    proof.production(args)
                launch.assert_not_called()
        release = SimpleNamespace(status=200, body=json.dumps({
            'ok': True, 'env': {'value': 'production'}, 'git_sha': {'value': 'a' * 40}}).encode())
        with patch.object(proof, 'read', return_value=release), \
                patch.object(proof.subprocess, 'Popen', side_effect=OSError('unavailable')):
            self.assertEqual(proof.main(['--production', '--natural', '--merge-sha', 'a' * 40]), 78)

    def test_stalled_wrapper_exits_78_and_reaps_owned_process(self):
        from contextlib import redirect_stdout
        from types import SimpleNamespace
        import io
        import os
        import sys
        import tempfile
        release = SimpleNamespace(status=200, body=json.dumps({
            'ok': True, 'env': {'value': 'production'}, 'git_sha': {'value': 'a' * 40}}).encode())
        with tempfile.TemporaryDirectory() as directory:
            wrapper = Path(directory) / 'actor'
            pidfile = Path(directory) / 'pid'
            wrapper.write_text('#!' + sys.executable + '\nimport os,time\nfrom pathlib import Path\n'
                               + 'Path(' + repr(str(pidfile)) + ').write_text(str(os.getpid()))\ntime.sleep(30)\n')
            wrapper.chmod(0o700)
            with patch.object(proof, 'read', return_value=release), \
                    patch.object(proof.time, 'monotonic', side_effect=[0, 59]), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(proof.main(['--production', '--natural', '--merge-sha', 'a' * 40,
                                             '--call-wrapper', str(wrapper)]), 78)
            self.assertEqual(json.loads(output.getvalue())['state'], 'insufficient_evidence')
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pidfile.read_text()), 0)
