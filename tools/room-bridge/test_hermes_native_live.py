"""Opt-in installed-runtime cancellation check; no provider/model is ever called."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest

import dispatch
import hermes_wire


@unittest.skipUnless(os.environ.get('CARR_TEST_INSTALLED_HERMES') == '1', 'installed Hermes opt-in')
class InstalledNativeTest(unittest.TestCase):
    def test_cancelled_native_oneshot_and_child_leave_fake_gateway_untouched(self):
        repo = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(prefix='hermes-probe-', dir=repo / '.git') as tmp, \
                socket.socket() as gateway:
            root = Path(tmp)
            gateway.bind(('127.0.0.1', 0))
            gateway.listen()
            gateway.settimeout(0.1)
            connections, done = [], threading.Event()
            def fake_gateway():
                while not done.is_set():
                    try:
                        peer, _ = gateway.accept()
                    except TimeoutError:
                        continue
                    connections.append(True)
                    peer.close()
            listener = threading.Thread(target=fake_gateway)
            listener.start()
            try:
                env = {**os.environ, 'HERMES_HOME': str(root / 'home'),
                       'PYTHONDONTWRITEBYTECODE': '1', 'HERMES_IGNORE_USER_CONFIG': '1',
                       'HERMES_TUI_GATEWAY_URL': 'ws://127.0.0.1:' + str(gateway.getsockname()[1])}
                discovered = subprocess.run(['hermes', '--print-runtime-command', '--module',
                    'hermes_cli.gateway_client'], env=env, capture_output=True, text=True, check=True, timeout=20)
                prepared = dispatch._run_headless_process(json.loads(discovered.stdout),
                    env, 45, None, False, cwd=root)
                self.assertEqual(prepared.returncode, 0, prepared.stderr)
                env['HERMES_DISABLE_LAZY_INSTALLS'] = '1'
                command = hermes_wire.native_command(discovered.stdout)
                # Insert the hold before the EXACT native runner used by dispatch. The installed
                # run_oneshot executes, but its _run_agent cannot construct or call a model.
                setup = """
import hermes_cli.oneshot as oneshot, subprocess, time
from pathlib import Path
def hold(*args, **kwargs):
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    Path('child.pid').write_text(str(child.pid))
    Path('before-model').write_text('no inference')
    time.sleep(60)
oneshot._run_agent = hold
"""
                marker = '; import runpy; runpy.run_path('
                before, after = command[3].rsplit(marker, 1)
                command[3] = before + '; exec(' + repr(setup) + ')' + marker + after
                with self.assertRaises(subprocess.TimeoutExpired) as caught:
                    dispatch._run_headless_process([*command, '-z', 'hold before model', '-m', 'probe',
                        '--reasoning', 'high', '--usage-file', str(root / 'usage.json'),
                        '--allowance-usd', '1.00', '--price-json', json.dumps({
                            'input_usd_per_token': '0.000002', 'output_usd_per_token': '0.000006'})],
                        env, 8, None, False, cwd=root)
                self.assertTrue((root / 'before-model').exists())
                self.assertTrue(caught.exception.termination_confirmed)
                child = (root / 'child.pid').read_text()
                state = subprocess.run(['ps', '-o', 'stat=', '-p', child], capture_output=True, text=True)
                self.assertTrue(state.returncode or state.stdout.strip().startswith('Z'), state.stdout)
                self.assertEqual(connections, [])
            finally:
                done.set()
                listener.join(2)


if __name__ == '__main__':
    unittest.main()
