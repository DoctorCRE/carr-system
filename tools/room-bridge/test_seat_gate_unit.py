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
import desks
import dispatch
import seat_gate
host_liveness = seat_gate.host_liveness
CONFIG = host_liveness.load_config()
STUDIO = 'mac-studio.tailc8cc93.ts.net.'
MACBOOK = 'joes-macbook-pro.tailc8cc93.ts.net.'

def live(self_dns, studio_online=True, macbook_online=True):
    peers = {}
    for dns, online in ((STUDIO, studio_online), (MACBOOK, macbook_online)):
        if dns != self_dns:
            peers[dns] = {'DNSName': dns, 'Online': online, 'LastSeen': '2026-10-09T23:46:23.1Z'}
    return host_liveness.liveness({'BackendState': 'Running', 'Self': {'DNSName': self_dns}, 'Peer': peers}, CONFIG)

class SeatGateUnit(unittest.TestCase):

    def test_offline_host_refuses_with_last_seen(self):
        calls = []

        def evidence():
            calls.append(1)
            return {}
        got = seat_gate.refusal({'kind': 'claude-remote', 'host': 'studio', 'name': 'studio-desk'}, CONFIG, live(MACBOOK, studio_online=False))
        self.assertEqual(got[0], 'seat_host_offline')
        self.assertIn('studio-desk runs on host mac-studio', got[1])
        self.assertIn('offline since 2026-10-09T23:46Z', got[1])
        self.assertEqual(calls, [])

    def test_unknown_liveness_never_refuses_on_host(self):
        unknown = host_liveness.liveness(None, CONFIG)
        self.assertIsNone(seat_gate.refusal({'kind': 'claude-remote', 'host': 'studio', 'model': 'x'}, CONFIG, unknown))

    def test_unreadable_config_is_a_refusal_naming_file_and_error_class(self):
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d) / 'seat-hosts.v1.json'
            bad = Path(d) / 'bad.json'
            bad.write_text('{not json')
            wrong = Path(d) / 'wrong.json'
            wrong.write_text(json.dumps({'schema': 'carr-seat-hosts/v1', 'hosts': {}, 'seats': []}))
            for path, error in ((missing, 'FileNotFoundError'), (bad, 'JSONDecodeError'), (wrong, 'ValueError')):
                with self.subTest(error=error), patch.object(host_liveness, 'CONFIG', path), patch.object(host_liveness, 'observe') as observe:
                    got = seat_gate.check({'kind': 'codex-session', 'name': 'cx'})
                    self.assertEqual(got[0], 'seat_gate_unreadable')
                    self.assertIn(str(path), got[1])
                    self.assertIn(f'({error})', got[1])
                    observe.assert_not_called()
        broken = dict(CONFIG, seats=CONFIG['seats'])
        broken.pop('hosts')
        with patch.object(host_liveness, 'load_config', return_value=broken), patch.object(host_liveness, 'observe', side_effect=KeyError('hosts')):
            got = seat_gate.check({'kind': 'codex-session', 'name': 'cx'})
        self.assertEqual(got[0], 'seat_gate_unreadable')
        self.assertIn('(KeyError)', got[1])

class SendThroughTheGate(unittest.TestCase):
    """The CLI door: a refused send launches nothing; a live seat still dispatches."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        Path(self.root, 'models_cache.json').write_text(json.dumps({'models': [{'slug': 'gpt-6.1-sol', 'display_name': 'GPT-6.1-Sol'}]}))
        env = patch.dict(os.environ, {'CODEX_HOME': str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        self.reg = desks.Registry(self.root / 'desks.json')
        self.reg.register('cx', 'codex-session', family='sol', effort='high')
        self.reg.register('studio-desk', 'claude-remote', host='studio', model='claude-opus-5-5', effort='high', timeout_s=60)
        self.results = self.root / 'results.jsonl'

    def send(self, name, liveness):
        launched, err = ([], io.StringIO())
        with patch.object(host_liveness, 'observe', return_value=liveness) as self.observe, patch.object(dispatch, '_to_codex', side_effect=lambda *a, **k: launched.append('codex') or {'status': 'completed'}), patch.object(dispatch.claude_remote_wire, 'run_task', side_effect=lambda *a, **k: launched.append('remote') or {'status': 'completed'}), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = dispatch.main(['--registry', str(self.reg.path), '--results', str(self.results), 'send', name, 'work'])
        return (code, launched, err.getvalue())

    def test_offline_host_is_refused_before_launch(self):
        code, launched, err = self.send('studio-desk', live(MACBOOK, studio_online=False))
        self.assertEqual((code, launched), (2, []))
        self.assertIn('refused (seat_host_offline)', err)
        self.assertIn('mac-studio', err)

    def test_online_seat_still_dispatches(self):
        code, launched, _ = self.send('cx', live(MACBOOK, studio_online=False))
        self.assertEqual((code, launched), (0, ['codex']))
        code, launched, _ = self.send('studio-desk', live(MACBOOK))
        self.assertEqual((code, launched), (0, ['remote']))
        code, launched, _ = self.send('cx', live(MACBOOK))
        self.assertEqual((code, launched), (0, ['codex']))

    def test_each_send_reads_the_tailnet_once(self):
        self.send('cx', live(MACBOOK))
        self.assertEqual(self.observe.call_count, 1)
        self.send('studio-desk', live(MACBOOK, studio_online=False))
        self.assertEqual(self.observe.call_count, 1)

    def test_missing_config_refuses_without_a_traceback(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(host_liveness, 'CONFIG', Path(d) / 'absent.json'):
                code, launched, err = self.send('cx', live(MACBOOK))
        self.assertEqual((code, launched), (2, []))
        self.assertIn('refused (seat_gate_unreadable)', err)
        self.assertIn('absent.json', err)
        self.assertIn('FileNotFoundError', err)
        self.assertNotIn('Traceback', err)
if __name__ == '__main__':
    unittest.main(verbosity=2)
