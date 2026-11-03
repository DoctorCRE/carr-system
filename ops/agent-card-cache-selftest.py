#!/usr/bin/env python3
"""Prove cache integrity, isolation, and concurrent updates with synthetic evidence."""
from concurrent.futures import ProcessPoolExecutor
import fcntl
import json
import os
import runpy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.agent_card_cache import RegistrationCache, mint_card


def record_card(home, job):
    with patch.dict(os.environ, {'HOME': home}):
        return RegistrationCache(clock=lambda: 1000000).record(f'WR557.S6.J{job}', minted=True)


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
        self.cache.record(self.card, minted=True)
        self.now -= 1
        self.assertFalse(self.cache.consume_mint(self.card))
        self.now += 1
        self.cache.record(self.card, minted=True)
        self.now += 24 * 60 * 60
        self.assertFalse(self.cache.consume_mint(self.card))

    def test_scope_change_refuses(self):
        for name, value in [('CARR_MCP_URL', 'https://synthetic.invalid/mcp'),
                            ('CARR_MCP_CLIENT_PROFILE', 'hermes-projector'),
                            ('CARR_MCP_ENV', '/synthetic/other-client')]:
            self.cache.record(self.card, minted=True)
            with patch.dict(os.environ, {name: value}):
                self.assertFalse(RegistrationCache(clock=lambda: self.now).consume_mint(self.card))

    def test_user_home_isolation_and_private_modes(self):
        self.assertTrue(self.cache.record(self.card, minted=True))
        for path in self.cache.directory.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.cache.directory.stat().st_mode & 0o777, 0o700)
        with tempfile.TemporaryDirectory() as other:
            with patch.dict(os.environ, {'HOME': other}):
                self.assertFalse(RegistrationCache(clock=lambda: self.now).contains(self.card))

    def test_tampered_body_and_legacy_unsigned_entry_refuse(self):
        self.cache.record(self.card, minted=True)
        path = self.cache.directory / 'entries.json'
        entries = json.loads(path.read_text())
        entries[self.card]['body']['seen'] += 1
        path.write_text(json.dumps(entries))
        self.assertFalse(self.cache.contains(self.card))
        path.write_text(json.dumps({self.card: self.now}))
        self.assertFalse(self.cache.contains(self.card))

    def test_nonprivate_and_symlinked_files_refuse(self):
        self.cache.record(self.card, minted=True)
        path = self.cache.directory / 'entries.json'
        path.chmod(0o644)
        self.assertFalse(self.cache.consume_mint(self.card))
        path.unlink()
        target = self.home / 'outside'
        target.write_text('untouched')
        path.symlink_to(target)
        self.assertFalse(self.cache.record(self.card, minted=True))
        self.assertEqual(target.read_text(), 'untouched')

    def test_parallel_writers_preserve_all_cards_and_consumption(self):
        with ProcessPoolExecutor(max_workers=4) as pool:
            self.assertTrue(all(pool.map(record_card, [str(self.home)] * 12, range(1, 13))))
        for job in range(1, 13):
            card = f'WR557.S6.J{job}'
            self.assertTrue(self.cache.consume_mint(card))
            self.assertFalse(self.cache.consume_mint(card))
        self.cache.remove(self.card)
        self.cache.record('WR557.S6.J13')
        self.assertFalse(self.cache.contains(self.card))

    def test_mint_hit_cannot_outlive_launch_deadline(self):
        gate = runpy.run_path(str(Path(__file__).resolve().parent.parent / 'hooks/agent-card-gate.py'))
        cache = Mock(consume_mint=lambda card: True)
        with patch('lib.agent_card_cache.RegistrationCache', return_value=cache), \
                patch('time.monotonic', side_effect=[0, 9, 9]):
            result = gate['check_prompts']([f'Card: {self.card}'])
        self.assertEqual(result.code, 2)
        self.assertIn('deadline exceeded', result.stderr)

    def test_contended_lock_does_not_block_admission(self):
        self.cache.record(self.card, minted=True)
        with (self.cache.directory / 'lock').open() as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertFalse(self.cache.consume_mint(self.card))
        self.assertTrue(self.cache.consume_mint(self.card))

    def test_mint_response_binds_explicit_job_and_slice(self):
        reply = {'ok': True, 'card_id': self.card, 'priority': 1}
        for request in ({'work_request': 'WR557', 'slice': 6, 'job': 2},
                        {'work_request': 'WR557', 'slice': 7}, [], None):
            self.assertIsNone(mint_card(request, reply))
        self.assertEqual(mint_card({'work_request': 'WR-000557', 'slice': 6}, reply), self.card)


if __name__ == '__main__':
    unittest.main()
