"""Exercise delivery evidence through the bounded CLI without network or historical objects."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from git_env import fixture_env

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'ops/web-push-delivery-proof.py'


class DeliveryProofTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.repo = Path(cls.temp.name)
        def git(*args):
            return subprocess.check_output(['git', *args], cwd=cls.repo, env=fixture_env(), text=True).strip()
        git('init', '-q')
        git('config', 'user.name', 'Fixture')
        git('config', 'user.email', 'fixture@example.invalid')
        git('commit', '--allow-empty', '-qm', 'merge')
        cls.merge = git('rev-parse', 'HEAD')
        git('commit', '--allow-empty', '-qm', 'descendant')
        cls.descendant = git('rev-parse', 'HEAD')
        git('checkout', '--orphan', 'unrelated')
        git('commit', '--allow-empty', '-qm', 'unrelated')
        cls.unrelated = git('rev-parse', 'HEAD')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def bundle(self, sha=None):
        sha = sha or self.descendant
        uid = lambda n: f'20000000-0000-4000-8000-{n:012d}'
        row = dict(id=uid(1), notification_id=uid(2), installation_id=uid(3), generation=2,
                   state='provider_accepted', reason='provider_accepted', attempt_id=uid(4), attempt_count=1,
                   created_at='2026-11-01T00:00:00Z', due_at='2026-11-01T00:00:00Z', expires_at='2026-11-02T00:00:00Z')
        revoked = dict(row, id=uid(5), generation=1, state='cancelled', reason='revoked', attempt_id=None, attempt_count=0)
        return dict(release=dict(ok=True, git_sha=dict(value=sha), env=dict(value='production')),
                    provenance=dict(release_sha=sha, actor_slug='joe', transport='signed-in-app-origin/mcp',
                                    app_origin='https://app.doctorcre.com',
                                    queries=[dict(verb='read-web-push-delivery-status-v1', args={})]),
                    status=dict(schema_version='web-push-delivery.v1', deliveries=[row, revoked]),
                    phone=dict(release_sha=sha, actor_slug='joe', outbox_id=row['id'], notification_id=row['notification_id'],
                               attempt_id=row['attempt_id'], installation_id=row['installation_id'], generation=2,
                               notification_observed=True, exact_card_opened=True,
                               observed_at='2026-11-01T00:01:00Z'),
                    revoked=dict(outbox_id=revoked['id'], installation_id=revoked['installation_id'], generation=1,
                                 submissions=0))

    def run_cli(self, bundle, expected, merge=None, extra=(), environment=None):
        file = self.repo / 'evidence.json'
        file.write_text(json.dumps(bundle))
        result = subprocess.run([sys.executable, str(CLI), '--fixture', '--merge-sha', merge or self.merge,
                                 '--repository', str(self.repo), '--evidence', str(file), *extra],
                                env=fixture_env() if environment is None else environment,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_containing_equal_and_descendant(self):
        for sha in (self.merge, self.descendant):
            self.run_cli(self.bundle(sha), 0)

    def test_inherited_git_location_cannot_redirect_ancestry(self):
        self.run_cli(self.bundle(), 0, environment={**fixture_env(), 'GIT_DIR': str(ROOT / '.git')})

    def test_older_unrelated_unknown_release(self):
        self.run_cli(self.bundle(self.merge), 78, merge=self.descendant)
        self.run_cli(self.bundle(self.unrelated), 78)
        self.run_cli(self.bundle('f' * 40), 78)

    def test_phone_identity_and_observation_must_match(self):
        for field, value in [('attempt_id', '20000000-0000-4000-8000-000000000099'),
                             ('outbox_id', '20000000-0000-4000-8000-000000000099'),
                             ('notification_id', '20000000-0000-4000-8000-000000000099'),
                             ('installation_id', '20000000-0000-4000-8000-000000000099'),
                             ('generation', 3), ('notification_observed', False), ('exact_card_opened', False)]:
            bundle = self.bundle()
            bundle['phone'][field] = value
            self.run_cli(bundle, 1)

    def test_revoked_generation_never_attempted(self):
        for mutation in ('submissions', 'attempt', 'generation', 'missing'):
            bundle = self.bundle()
            if mutation == 'submissions': bundle['revoked']['submissions'] = 1
            if mutation == 'attempt': bundle['status']['deliveries'][1]['attempt_count'] = 1
            if mutation == 'generation': bundle['revoked']['generation'] = 2
            if mutation == 'missing': bundle['status']['deliveries'].pop()
            self.run_cli(bundle, 1)

    def test_contract_failure_on_containing_release(self):
        bundle = self.bundle()
        bundle['status']['deliveries'][0]['endpoint'] = 'secret'
        self.run_cli(bundle, 1)

    def test_missing_evidence_auth_and_deadline(self):
        for field in ('phone', 'revoked', 'status', 'provenance'):
            bundle = self.bundle()
            del bundle[field]
            self.run_cli(bundle, 78)
        bundle = self.bundle()
        bundle['provenance']['transport'] = 'machine-cli'
        self.run_cli(bundle, 78)
        self.run_cli(self.bundle(), 78, extra=('--timeout', '0.000001'))
        bundle = self.bundle()
        bundle['phone']['release_sha'] = self.merge
        self.run_cli(bundle, 78)


if __name__ == '__main__':
    unittest.main()
