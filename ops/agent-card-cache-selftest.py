#!/usr/bin/env python3
"""Prove cache integrity, isolation, and concurrent updates with synthetic evidence."""
from concurrent.futures import ProcessPoolExecutor
import fcntl
import json
import os
import runpy
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.agent_card_cache import MintClaim, RegistrationCache, mint_card


def record_card(home, job):
    with patch.dict(os.environ, {'HOME': home}):
        return RegistrationCache(clock=lambda: 1000000).record_mint(f'WR557.S6.J{job}')


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {'HOME': str(self.home), 'CARR_MCP_URL': '',
                                                  'CARR_MCP_CLIENT_PROFILE': ''})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.now = 1000000
        self.cache = RegistrationCache(clock=lambda: self.now)
        self.card = 'WR557.S6.J1'

    def test_ttl_and_future_entries_fail_closed(self):
        self.cache.record_mint(self.card)
        self.now -= 1
        self.assertIsNot(self.cache.claim_mint(self.card), MintClaim.FRESH)
        self.now += 1
        self.cache.record_mint(self.card)
        self.now += 24 * 60 * 60
        self.assertIsNot(self.cache.claim_mint(self.card), MintClaim.FRESH)

    def test_explicit_default_profile_matches_client_default(self):
        self.cache.record_mint(self.card)
        with patch.dict(os.environ, {'CARR_MCP_CLIENT_PROFILE': 'local'}):
            self.assertIs(RegistrationCache(clock=lambda: self.now).claim_mint(self.card), MintClaim.FRESH)

    def test_scope_change_refuses(self):
        for name, value in [('CARR_MCP_URL', 'https://synthetic.invalid/mcp'),
                            ('CARR_MCP_CLIENT_PROFILE', 'hermes-projector'),
                            ('CARR_MCP_ENV', '/synthetic/other-client')]:
            self.cache.record_mint(self.card)
            with patch.dict(os.environ, {name: value}):
                self.assertIsNot(RegistrationCache(clock=lambda: self.now).claim_mint(self.card), MintClaim.FRESH)

    def test_user_home_isolation_and_private_modes(self):
        self.assertTrue(self.cache.record_mint(self.card))
        for path in self.cache.directory.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.cache.directory.stat().st_mode & 0o777, 0o700)
        with tempfile.TemporaryDirectory() as other:
            with patch.dict(os.environ, {'HOME': other}):
                self.assertIsNot(RegistrationCache(clock=lambda: self.now).claim_mint(self.card), MintClaim.FRESH)

    def test_tampered_body_and_legacy_unsigned_entry_refuse(self):
        self.cache.record_mint(self.card)
        path = self.cache.directory / 'entries.json'
        entries = json.loads(path.read_text())
        entries[self.card]['body']['seen'] += 1
        path.write_text(json.dumps(entries))
        self.assertIsNot(self.cache.claim_mint(self.card), MintClaim.FRESH)
        path.write_text(json.dumps({self.card: self.now}))
        self.assertIsNot(self.cache.claim_mint(self.card), MintClaim.FRESH)

    def test_nonprivate_and_symlinked_files_refuse(self):
        self.cache.record_mint(self.card)
        path = self.cache.directory / 'entries.json'
        path.chmod(0o644)
        self.assertIsNot(self.cache.claim_mint(self.card), MintClaim.FRESH)
        path.unlink()
        target = self.home / 'outside'
        target.write_text('untouched')
        path.symlink_to(target)
        self.assertFalse(self.cache.record_mint(self.card))
        self.assertEqual(target.read_text(), 'untouched')

    def test_parallel_writers_preserve_all_cards_and_consumption(self):
        with ProcessPoolExecutor(max_workers=4) as pool:
            self.assertTrue(all(pool.map(record_card, [str(self.home)] * 12, range(1, 13))))
        for job in range(1, 13):
            card = f'WR557.S6.J{job}'
            self.assertIs(self.cache.claim_mint(card), MintClaim.FRESH)
            self.assertIsNot(self.cache.claim_mint(card), MintClaim.FRESH)
        self.cache.record_mint('WR557.S6.J13')
        self.assertIsNot(self.cache.claim_mint(self.card), MintClaim.FRESH)

    def test_mint_hit_cannot_outlive_launch_deadline(self):
        gate = runpy.run_path(str(Path(__file__).resolve().parent.parent / 'hooks/agent-card-gate.py'))
        cache = Mock(claim_mint=lambda card: MintClaim.FRESH)
        with patch('lib.agent_card_cache.RegistrationCache', return_value=cache), \
                patch('time.monotonic', side_effect=[0, 9, 9]):
            result = gate['check_prompts']([f'Card: {self.card}'])
        self.assertEqual(result.code, 2)
        self.assertIn('deadline exceeded', result.stderr)

    def test_mint_writer_retries_a_short_lock_stall(self):
        with patch('fcntl.flock', side_effect=[BlockingIOError(), None]), \
                patch('time.monotonic', side_effect=[0, 0.11]), patch('time.sleep'):
            recorded = self.cache.record_mint(self.card)
        self.assertTrue(recorded)
        self.assertIs(self.cache.claim_mint(self.card), MintClaim.FRESH)

    def test_contended_lock_does_not_block_admission(self):
        self.cache.record_mint(self.card)
        with (self.cache.directory / 'lock').open() as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertIsNot(self.cache.claim_mint(self.card), MintClaim.FRESH)
        self.assertIs(self.cache.claim_mint(self.card), MintClaim.FRESH)

    def test_failed_consumption_never_returns_fresh(self):
        self.assertTrue(self.cache.record_mint(self.card))
        for target in ('lib.agent_card_cache.os.fsync', 'pathlib.Path.replace'):
            with self.subTest(target=target), patch(target, side_effect=OSError('synthetic storage failure')):
                self.assertIs(self.cache.claim_mint(self.card), MintClaim.INCOMPLETE)
        self.assertIs(self.cache.claim_mint(self.card), MintClaim.FRESH)
        self.assertIs(self.cache.claim_mint(self.card), MintClaim.ONLINE_REQUIRED)

    def test_unresolved_repository_cannot_bypass_retirement(self):
        self.assertTrue(self.cache.record_mint(self.card))
        unknown = RegistrationCache(clock=lambda: self.now, repository=None)
        self.assertIs(unknown.claim_mint(self.card), MintClaim.INCOMPLETE)

    def test_old_contract_receipt_requires_online(self):
        self.cache.scope.remove('consume-before-lookup-v2')
        self.assertTrue(self.cache.record_mint(self.card))
        current = RegistrationCache(clock=lambda: self.now)
        self.assertIs(current.claim_mint(self.card), MintClaim.ONLINE_REQUIRED)

    def test_launch_repository_ignores_inherited_git_location(self):
        from lib.agent_card_cache import launch_repository
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from git_env import fixture_env

        for name, origin in (('mint', 'DoctorCRE/carr-system'), ('launch', 'jbookout/doctorcre-app')):
            path = self.home / name
            subprocess.run(['git', 'init', '-q', str(path)], check=True, env=fixture_env())
            subprocess.run(['git', 'remote', 'add', 'origin', 'https://github.com/' + origin + '.git'],
                           cwd=path, check=True, env=fixture_env())
        with patch.dict(os.environ, {'GIT_DIR': str(self.home / 'mint/.git')}):
            self.assertEqual(launch_repository(self.home / 'launch'), 'jbookout/doctorcre-app')

    def test_identity_change_during_mint_cannot_seed(self):
        with patch.dict(os.environ, {'CARR_MCP_LOCAL_TOKEN': 'fixture-first'}):
            cache = RegistrationCache(clock=lambda: self.now)
            os.environ['CARR_MCP_LOCAL_TOKEN'] = 'fixture-second'
            self.assertFalse(cache.record_mint(self.card))
        self.assertIsNot(self.cache.claim_mint(self.card), MintClaim.FRESH)

    def test_mint_response_binds_explicit_job_and_slice(self):
        reply = {'ok': True, 'card_id': self.card, 'priority': 1}
        for request in ({'work_request': 'WR557', 'slice': 6, 'job': 2},
                        {'work_request': 'WR557', 'slice': 7}, [], None):
            self.assertIsNone(mint_card(request, reply))
        self.assertEqual(mint_card({'work_request': 'WR-000557', 'slice': 6}, reply), self.card)


if __name__ == '__main__':
    unittest.main()
