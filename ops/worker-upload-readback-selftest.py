#!/usr/bin/env python3
"""Offline readback and the real deployment upload branch with a fake provider."""
import json
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from git_env import fixture_env

ENV = fixture_env()
ROOT = Path(__file__).resolve().parents[1]
VERSION = '11111111-2222-4333-8444-555555555555'
OTHER = '22222222-2222-4333-8444-555555555555'
SHA = 'a' * 40
DIGEST = 'sha256:' + 'b' * 64


def detail(identifier=VERSION, sha=SHA, digest=DIGEST):
    return {'id': identifier, 'resources': {'bindings': [
        {'name': 'GIT_SHA', 'type': 'plain_text', 'text': sha},
        {'name': 'CANDIDATE_MANIFEST_DIGEST', 'type': 'plain_text', 'text': digest}]}}


class UploadReadback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.calls = self.root / 'calls'
        self.state = self.root / 'provider.json'
        self.wrangler = self.root / 'wrangler'
        self.env = dict(ENV, PATH=str(self.root) + os.pathsep + ENV['PATH'])
        (self.root / 'wrangler.toml').write_text('name="fixture-worker"\naccount_id="' + 'a' * 32 + '"\n')
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, env=ENV)

    def tearDown(self):
        self.tmp.cleanup()

    def provider(self, versions, *, mode='normal'):
        self.state.write_text(json.dumps(versions))
        self.wrangler.write_text('#!' + sys.executable + '\n'
            'import sys,json\nfrom pathlib import Path\nfrom urllib.parse import urlparse,parse_qs\n'
            + f'state=Path({str(self.state)!r}); mode={mode!r}\n'
            + f'open({str(self.calls)!r}, "a").write(" ".join(sys.argv[1:])+"\\n")\n'
            + 'versions=json.loads(state.read_text())\n'
            + 'if Path(sys.argv[0]).name=="curl":\n'
            + ' sys.stdin.read()\n'
            + ' if mode=="unreadable-after-upload" and versions: raise SystemExit(1)\n'
            + ' if mode=="unavailable": raise SystemExit(1)\n'
            + ' if mode=="malformed": print("not JSON"); raise SystemExit(0)\n'
            + ' query=parse_qs(urlparse(sys.argv[-1]).query); page=int(query["page"][0]); size=int(query["per_page"][0])\n'
            + ' rows=[{"id":v["id"],"annotations":v.get("annotations",{})} for v in versions]\n'
            + ' if mode=="repeated-page": page=1\n'
            + ' print(json.dumps({"success":True,"result":{"items":rows[(page-1)*size:page*size]}})); raise SystemExit(0)\n'
            + 'if sys.argv[1:3]==["auth","token"]: print(json.dumps({"type":"api_token","token":"fixture-bearer"})); raise SystemExit(0)\n'
            + 'if mode=="unavailable": raise SystemExit(1)\n'
            + 'if mode=="malformed": print("not JSON"); raise SystemExit(0)\n'
            + 'if sys.argv[2]=="list": print(json.dumps([{"id":v["id"]} for v in versions[-10:]]))\n'
            + 'elif sys.argv[2]=="view": print(json.dumps(next(v for v in versions if v["id"]==sys.argv[3])))\n'
            + 'elif sys.argv[2]=="upload":\n'
            + f' attempts=Path({str(self.root / "attempts")!r}); count=int(attempts.read_text()) if attempts.exists() else 0; attempts.write_text(str(count+1))\n'
            + f' if mode not in ("never","rejected") and not (mode.startswith("flaky") and count==0): state.write_text(json.dumps([{detail()!r}]))\n'
            + ' if mode in ("lost","never","unreadable-after-upload") or (mode.startswith("flaky") and count==0): print(mode.split(":")[1] if ":" in mode else "fetch failed"); raise SystemExit(1)\n'
            + ' if mode=="rejected": print("invalid configuration"); raise SystemExit(1)\n'
            + f' print("Worker Version ID: {VERSION}")\n'
            + 'else: raise SystemExit(9)\n')
        self.wrangler.chmod(0o755)
        curl = self.root / 'curl'
        curl.write_text(self.wrangler.read_text())
        curl.chmod(0o755)

    def readback(self, *extra):
        return subprocess.run([sys.executable, str(ROOT / 'tools/worker-upload-readback.py'),
                               '--wrangler', str(self.wrangler), '--sha', SHA,
                               '--digest', DIGEST, *extra], capture_output=True, text=True, env=self.env, cwd=self.root)

    def deploy(self):
        source = (ROOT / 'bin/deploy-worker.sh').read_text()
        start = source.index('  if [ -n "$DO_MIGRATION_VERSION_ID" ]; then', source.index('# -- provider-version upload --'))
        end = source.index('  if [ -n "$FOUNDATION_ASSURANCE_STAGING_PROVIDER" ]; then', start)
        script = ('set -eu\nfail() { echo "$*" >&2; exit 1; }\n'
                  + f'REPO={ROOT}\nPY={sys.executable}\nWRANGLER={self.wrangler}\n'
                  + f'DO_MIGRATION_VERSION_ID=\nPROBE_TOKENS_FILE=\nHEAD_SHA={SHA}\n'
                  + f'CANDIDATE_MANIFEST_DIGEST={DIGEST}\nCANDIDATE_MANIFEST=fixture\n'
                  + source[start:end] + '\nprintf "%s" "$PROVIDER_VERSION_ID"\n')
        return subprocess.run(['sh', '-c', script], cwd=self.root, capture_output=True, text=True, env=self.env)

    def test_published_candidate_is_reconciled_without_upload(self):
        self.provider([detail()])
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.endswith(VERSION))
        self.assertNotIn('upload', self.calls.read_text())

    def test_lost_upload_response_reconciles_without_a_second_upload(self):
        self.provider([], mode='lost')
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.endswith(VERSION))
        self.assertEqual(self.calls.read_text().count('versions upload'), 1)
        self.assertEqual(self.deploy().returncode, 0)
        self.assertEqual(self.calls.read_text().count('versions upload'), 1)

    def test_unverifiable_claim_never_reuploads_even_on_a_new_invocation(self):
        self.provider([], mode='unreadable-after-upload')
        self.assertNotEqual(self.deploy().returncode, 0)
        self.assertNotEqual(self.deploy().returncode, 0)
        self.assertEqual(self.calls.read_text().count('versions upload'), 1)

    def test_network_failure_retries_only_after_verified_absence(self):
        self.provider([], mode='flaky')
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls.read_text().count('versions upload'), 2)

    def test_dns_connectivity_failure_keeps_the_existing_retry_contract(self):
        self.provider([], mode='flaky:EAI_AGAIN')
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls.read_text().count('versions upload'), 2)

    def test_live_claim_blocks_concurrent_upload_and_expires_with_owner(self):
        self.provider([])
        locks = self.root / '.git/carr-upload-locks'
        locks.mkdir()
        with (locks / DIGEST).open('a') as claim:
            fcntl.flock(claim, fcntl.LOCK_EX)
            self.assertNotEqual(self.deploy().returncode, 0)
            self.assertFalse(self.calls.exists())
        self.assertEqual(self.deploy().returncode, 0)
        self.assertEqual(self.calls.read_text().count('versions upload'), 1)

    def test_exhausted_network_retry_releases_claim_for_next_invocation(self):
        self.provider([], mode='never')
        self.assertNotEqual(self.deploy().returncode, 0)
        self.assertEqual(self.calls.read_text().count('versions upload'), 3)
        self.provider([])
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls.read_text().count('versions upload'), 4)

    def test_non_network_rejection_is_not_retried_and_releases_claim(self):
        self.provider([], mode='rejected')
        self.assertNotEqual(self.deploy().returncode, 0)
        self.assertEqual(self.calls.read_text().count('versions upload'), 1)
        self.provider([])
        self.assertEqual(self.deploy().returncode, 0)

    def test_candidate_older_than_latest_ten_and_first_api_page_is_reconciled(self):
        rows = [{'id': f'{i:08x}-2222-4333-8444-555555555555',
                 'annotations': {'workers/tag': 'carr-' + 'c' * 40}}
                for i in range(100)]
        self.provider(rows + [detail()])
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.endswith(VERSION))
        self.assertIn('page=2', self.calls.read_text())
        self.assertNotIn('versions upload', self.calls.read_text())

    def test_repeated_provider_page_fails_closed(self):
        rows = [{'id': f'{i:08x}-2222-4333-8444-555555555555',
                 'annotations': {'workers/tag': 'carr-' + 'c' * 40}}
                for i in range(100)]
        self.provider(rows, mode='repeated-page')
        self.assertNotEqual(self.deploy().returncode, 0)
        self.assertNotIn('versions upload', self.calls.read_text())

    def test_fresh_upload_requires_provider_readback(self):
        self.provider([])
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.endswith(VERSION))
        self.assertIn('versions view ' + VERSION, self.calls.read_text())

    def test_ambiguous_published_candidates_fail_before_upload(self):
        self.provider([detail(), detail(OTHER)])
        self.assertNotEqual(self.deploy().returncode, 0)
        self.assertNotIn('upload', self.calls.read_text())

    def test_unavailable_and_malformed_provider_reads_fail_before_upload(self):
        for mode in ('unavailable', 'malformed'):
            with self.subTest(mode=mode):
                self.provider([], mode=mode)
                self.assertNotEqual(self.deploy().returncode, 0)
                self.assertNotIn('upload', self.calls.read_text())

    def test_exact_version_must_match_both_source_and_contract(self):
        for version in (detail(sha='c' * 40), detail(digest='c' * 64)):
            with self.subTest(version=version):
                self.provider([version])
                self.assertNotEqual(self.readback('--version', VERSION).returncode, 0)

    def test_duplicate_identity_bindings_are_unverifiable(self):
        version = detail()
        version['resources']['bindings'].insert(0, {'name': 'GIT_SHA', 'type': 'plain_text', 'text': 'c' * 40})
        self.provider([version])
        self.assertNotEqual(self.readback('--version', VERSION).returncode, 0)

    def test_absent_candidate_is_distinct_from_unavailable_read(self):
        self.provider([detail(sha='c' * 40)])
        result = self.readback()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
