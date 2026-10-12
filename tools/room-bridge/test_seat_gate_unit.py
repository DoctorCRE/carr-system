"""dispatch.py send refuses an offline host or a failed seat; a live seat still dispatches.

Hermetic: the tailnet read and the seat evidence are fixtures; no model, no network.
Run:  python3 tools/room-bridge/test_seat_gate_unit.py
"""
import contextlib
import io
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import desks  # noqa: E402
import dispatch  # noqa: E402
import seat_gate  # noqa: E402

host_liveness = seat_gate.host_liveness
seat_health = seat_gate.seat_health
CONFIG = host_liveness.load_config()
STUDIO = 'mac-studio.tailc8cc93.ts.net.'
MACBOOK = 'joes-macbook-pro.tailc8cc93.ts.net.'


def live(self_dns, studio_online=True, macbook_online=True):
    peers = {}
    for dns, online in ((STUDIO, studio_online), (MACBOOK, macbook_online)):
        if dns != self_dns:
            peers[dns] = {'DNSName': dns, 'Online': online, 'LastSeen': '2026-10-09T23:46:23.1Z'}
    return host_liveness.liveness({'BackendState': 'Running', 'Self': {'DNSName': self_dns},
                                   'Peer': peers}, CONFIG)


def report(**rows):
    return {'schema': 'carr-seat-health/v1', 'seats': rows}


def row(passed, hours_ago=1, layer=None):
    at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {'passed': passed, 'dispatchable': passed, 'failing_layer': layer, 'observed_at': at}


class SeatGateUnit(unittest.TestCase):
    def test_targets(self):
        mb, st = live(MACBOOK), live(STUDIO)
        self.assertEqual(seat_gate.target({'kind': 'codex-session'}, CONFIG, mb), ('sol-macbook', 'joes-macbook-pro'))
        self.assertEqual(seat_gate.target({'kind': 'codex-live'}, CONFIG, st), ('sol-studio', 'mac-studio'))
        self.assertEqual(seat_gate.target({'kind': 'claude-remote', 'host': 'booko@studio'}, CONFIG, mb),
                         ('opus-studio', 'mac-studio'))
        self.assertEqual(seat_gate.target({'kind': 'claude-remote', 'host': 'macbook'}, CONFIG, st),
                         (None, 'joes-macbook-pro'))
        self.assertEqual(seat_gate.target({'kind': 'grok-cli'}, CONFIG, st), ('grok', 'mac-studio'))
        self.assertEqual(seat_gate.target({'kind': 'grok-cli'}, CONFIG, mb), (None, 'joes-macbook-pro'))
        self.assertEqual(seat_gate.target({'kind': 'claude-session'}, CONFIG, mb), (None, 'joes-macbook-pro'))
        unknown = host_liveness.liveness(None, CONFIG)
        self.assertEqual(seat_gate.target({'kind': 'codex-session'}, CONFIG, unknown), (None, None))

    def test_offline_host_refuses_with_last_seen(self):
        calls = []
        def evidence():
            calls.append(1)
            return report()
        got = seat_gate.refusal({'kind': 'claude-remote', 'host': 'studio', 'name': 'studio-desk'},
                                CONFIG, live(MACBOOK, studio_online=False), evidence)
        self.assertEqual(got[0], 'seat_host_offline')
        self.assertIn('studio-desk runs on host mac-studio', got[1])
        self.assertIn('offline since 2026-10-09T23:46Z', got[1])
        self.assertEqual(calls, [])

    def test_fresh_failed_seat_refuses_stale_or_missing_does_not(self):
        entry = {'kind': 'codex-session', 'name': 'cx'}
        failed = seat_gate.refusal(entry, CONFIG, live(MACBOOK),
                                   lambda: report(**{'sol-macbook': row(False, layer='auth')}))
        self.assertEqual(failed[0], 'seat_failed')
        self.assertIn('cx is seat sol-macbook', failed[1])
        self.assertIn('layer auth', failed[1])
        self.assertIn('--seat sol-macbook --force', failed[1])
        for evidence in (report(), report(**{'sol-macbook': row(False, hours_ago=30, layer='auth')}),
                         report(**{'sol-macbook': row(True)}), {}, report(**{'sol-studio': row(False)})):
            self.assertIsNone(seat_gate.refusal(entry, CONFIG, live(MACBOOK), lambda: evidence), evidence)

    def test_unknown_liveness_never_refuses_on_host(self):
        unknown = host_liveness.liveness(None, CONFIG)
        self.assertIsNone(seat_gate.refusal({'kind': 'claude-remote', 'host': 'studio', 'model': 'x'},
                                            CONFIG, unknown, lambda: report()))

    def test_check_reads_this_repos_seat_evidence(self):
        with patch.object(host_liveness, 'observe', return_value=live(MACBOOK)), \
             patch.object(seat_health, 'current_report',
                          return_value=report(**{'sol-macbook': row(False, layer='network')})) as evidence:
            got = seat_gate.check({'kind': 'codex-session', 'name': 'cx'})
        self.assertEqual(got[0], 'seat_failed')
        self.assertEqual(evidence.call_args.args, (seat_gate.REPO,))


class SendThroughTheGate(unittest.TestCase):
    """The CLI door: a refused send launches nothing; a live seat still dispatches."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        Path(self.root, 'models_cache.json').write_text(json.dumps({'models': [
            {'slug': 'gpt-6.1-sol', 'display_name': 'GPT-6.1-Sol'}]}))
        env = patch.dict(os.environ, {'CODEX_HOME': str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        self.reg = desks.Registry(self.root / 'desks.json')
        self.reg.register('cx', 'codex-session', family='sol', effort='high')
        self.reg.register('studio-desk', 'claude-remote', host='studio', model='claude-opus-5-5',
                          effort='high', timeout_s=60)
        self.results = self.root / 'results.jsonl'

    def send(self, name, liveness, evidence):
        launched, err = [], io.StringIO()
        with patch.object(host_liveness, 'observe', return_value=liveness), \
             patch.object(seat_health, 'current_report', return_value=evidence), \
             patch.object(dispatch, '_to_codex', side_effect=lambda *a, **k: launched.append('codex')
                          or {'status': 'completed'}), \
             patch.object(dispatch.claude_remote_wire, 'run_task',
                          side_effect=lambda *a, **k: launched.append('remote') or {'status': 'completed'}), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = dispatch.main(['--registry', str(self.reg.path), '--results', str(self.results),
                                  'send', name, 'work'])
        return code, launched, err.getvalue()

    def test_failed_seat_is_refused_before_launch(self):
        code, launched, err = self.send('cx', live(MACBOOK), report(**{'sol-macbook': row(False, layer='auth')}))
        self.assertEqual((code, launched), (2, []))
        self.assertIn('refused (seat_failed)', err)
        self.assertFalse(self.results.exists() and self.results.read_text().strip())

    def test_offline_host_is_refused_before_launch(self):
        code, launched, err = self.send('studio-desk', live(MACBOOK, studio_online=False), report())
        self.assertEqual((code, launched), (2, []))
        self.assertIn('refused (seat_host_offline)', err)
        self.assertIn('mac-studio', err)

    def test_online_seat_still_dispatches(self):
        code, launched, _ = self.send('cx', live(MACBOOK, studio_online=False),
                                      report(**{'sol-macbook': row(True)}))
        self.assertEqual((code, launched), (0, ['codex']))
        code, launched, _ = self.send('studio-desk', live(MACBOOK), report(**{'opus-studio': row(True)}))
        self.assertEqual((code, launched), (0, ['remote']))
        # No evidence at all (STALE) is not a refusal.
        code, launched, _ = self.send('cx', live(MACBOOK), report())
        self.assertEqual((code, launched), (0, ['codex']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
