"""Selftest for ops/host_liveness.py and its health row. Hermetic: no real tailnet read."""
import ast
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import host_liveness as hl  # noqa: E402

STUDIO = 'mac-studio.tailc8cc93.ts.net.'
MACBOOK = 'joes-macbook-pro.tailc8cc93.ts.net.'


def status(self_dns=MACBOOK, peers=(), backend='Running'):
    return {'BackendState': backend, 'Self': {'DNSName': self_dns, 'HostName': 'SECRET-self'},
            'Peer': {f'key{i}': peer for i, peer in enumerate(peers)}}


def peer(dns, online, last='2026-10-09T23:46:23.1Z'):
    return {'DNSName': dns, 'Online': online, 'LastSeen': last, 'HostName': 'SECRET-peer',
            'TailscaleIPs': ['100.64.0.9']}


class HostLiveness(unittest.TestCase):
    config = hl.load_config()

    def test_config_names_every_seat_and_both_macs(self):
        import seat_health
        self.assertEqual(set(self.config['seats']), set(seat_health.SEATS))
        self.assertEqual(set(self.config['hosts']), {'mac-studio', 'joes-macbook-pro'})
        self.assertIsNone(hl.seat_host('jev', self.config))
        self.assertEqual(hl.seat_host('sol-macbook', self.config), 'joes-macbook-pro')

    def test_offline_peer_is_red_with_last_seen_and_bound_action(self):
        live = hl.liveness(status(peers=[peer(STUDIO, False)]), self.config)
        self.assertEqual(live['self'], 'joes-macbook-pro')
        self.assertEqual(live['hosts']['mac-studio'], {'state': 'offline', 'last_seen': '2026-10-09T23:46Z',
                                                       'self': False})
        rows = {host: (line, failed) for line, failed, host in hl.rows(live, self.config)}
        line, failed = rows['mac-studio']
        self.assertTrue(failed)
        self.assertIn('RED host mac-studio', line)
        self.assertIn('host offline since 2026-10-09T23:46Z', line)
        self.assertIn('seats skipped: e2e-auth, grok, opus-studio, sol-studio', line)
        for word in ('on breach:', 'owner', 'fix:', 'verify:', 'auto-clear'):
            self.assertIn(word, line)
        self.assertEqual(rows['joes-macbook-pro'][1], False)
        self.assertIn('this Mac', rows['joes-macbook-pro'][0])
        self.assertEqual(hl.offline_seats(live, self.config),
                         {'opus-studio': 'mac-studio', 'sol-studio': 'mac-studio',
                          'grok': 'mac-studio', 'e2e-auth': 'mac-studio'})
        self.assertNotIn('SECRET', json.dumps([r[0] for r in hl.rows(live, self.config)]))
        self.assertNotIn('100.64', json.dumps(live))

    def test_online_peer_and_never_seen(self):
        live = hl.liveness(status(self_dns=STUDIO, peers=[peer(MACBOOK, True)]), self.config)
        self.assertEqual(live['self'], 'mac-studio')
        self.assertEqual(live['hosts']['joes-macbook-pro']['state'], 'online')
        self.assertFalse(any(failed for _, failed, _ in hl.rows(live, self.config)))
        self.assertEqual(hl.offline_seats(live, self.config), {})
        never = hl.liveness(status(peers=[peer(STUDIO, False, '0001-01-01T00:00:00Z')]), self.config)
        line = hl.rows(never, self.config)[0][0]
        self.assertIn('host offline since never seen', line)

    def test_unknown_is_never_offline(self):
        cases = [None, {}, [], status(backend='Stopped', peers=[peer(STUDIO, False)]),
                 status(backend='NeedsLogin', peers=[peer(STUDIO, False)]),
                 status(peers=[{'DNSName': STUDIO, 'Online': 'false'}]),
                 status(peers=[peer('other.example.', False)]), status(self_dns=None)]
        for case in cases:
            with self.subTest(case=case):
                live = hl.liveness(case, self.config)
                self.assertEqual(hl.offline_seats(live, self.config), {})
                self.assertFalse(any(failed for _, failed, _ in hl.rows(live, self.config)))
                self.assertNotEqual(live['hosts']['mac-studio']['state'], 'offline')

    def test_self_row_is_not_overridden_by_a_peer_entry(self):
        live = hl.liveness(status(peers=[peer(MACBOOK, False)]), self.config)
        self.assertEqual(live['hosts']['joes-macbook-pro']['state'], 'online')

    def test_resolve_host_accepts_aliases_users_and_fqdns(self):
        for alias, host in [('studio', 'mac-studio'), ('booko@studio', 'mac-studio'),
                            ('mac-studio.tailc8cc93.ts.net.', 'mac-studio'), ('MacBook', 'joes-macbook-pro'),
                            ('joes-macbook-pro', 'joes-macbook-pro'), ('elsewhere', None), ('', None), (None, None)]:
            self.assertEqual(hl.resolve_host(alias, self.config), host, alias)

    def fake(self, root, body, rc=0):
        path = Path(root) / 'Tailscale'
        path.write_text(f"#!/bin/sh\ncat <<'EOF'\n{body}\nEOF\nexit {rc}\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return str(path)

    def test_read_status_through_the_binary(self):
        with tempfile.TemporaryDirectory() as d:
            doc = status(peers=[peer(STUDIO, False)])
            live = hl.observe(self.fake(d, json.dumps(doc)), self.config)
            self.assertEqual(live['hosts']['mac-studio']['state'], 'offline')
            self.assertIsNone(hl.read_status(self.fake(d, 'not json')))
            self.assertIsNone(hl.read_status(self.fake(d, '[]')))
        self.assertIsNone(hl.read_status('/nonexistent/Tailscale'))
        self.assertEqual(hl.observe('/nonexistent/Tailscale', self.config)['backend'], 'unreadable')

    def health_branch(self, rows_fn):
        """Execute the exact host-liveness branch of tools/health-check.py."""
        tree = ast.parse((REPO / 'tools/health-check.py').read_text())
        canonical = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_canonical_health')
        branches = [n for n in canonical.body if isinstance(n, ast.If) and '_host_liveness_rows' in ast.unparse(n)]
        self.assertEqual(len(branches), 1)
        recorders = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ('_canonical_finding', '_red')]
        ns = {'CANONICAL_SECTION': 'tailscale', 'rc': 0, '_FINDINGS': [], '_host_liveness_rows': rows_fn}
        with redirect_stdout(io.StringIO()) as out:
            exec(compile(ast.Module(body=recorders + branches, type_ignores=[]), 'host-branch', 'exec'), ns)
        return ns, out.getvalue()

    def test_health_check_reds_only_the_offline_host(self):
        live = hl.liveness(status(peers=[peer(STUDIO, False)]), self.config)
        ns, out = self.health_branch(lambda: hl.rows(live, self.config))
        self.assertEqual(ns['rc'], 1)
        self.assertEqual([(f['key'], f['subject'], f['hard_error']) for f in ns['_FINDINGS']],
                         [('host_liveness', 'mac-studio', False)])
        self.assertIn('host offline since 2026-10-09T23:46Z', ns['_FINDINGS'][0]['detail'])
        self.assertIn('OK  host joes-macbook-pro', out)
        ok, _ = self.health_branch(lambda: hl.rows(hl.liveness(status(peers=[peer(STUDIO, True)]), self.config),
                                                   self.config))
        self.assertEqual((ok['rc'], ok['_FINDINGS']), (0, []))

    def test_health_check_reds_a_broken_checker(self):
        def broken():
            raise ValueError('config unreadable')
        ns, out = self.health_branch(broken)
        self.assertEqual(ns['rc'], 1)
        self.assertEqual([(f['key'], f['subject']) for f in ns['_FINDINGS']], [('host_liveness', 'checker')])
        self.assertIn('on breach:', out)


if __name__ == '__main__':
    unittest.main()
