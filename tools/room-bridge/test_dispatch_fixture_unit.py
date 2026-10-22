"""Exercise dispatch send through the detached worker without a model or network."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import desks
import dispatch
import work_runner


class DispatchFixtureTests(unittest.TestCase):
    def test_send_runs_job_and_records_result_without_a_room(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bins = root / 'bin'
            bins.mkdir()
            fake = bins / 'codex'
            fake.write_text('#!' + sys.executable + '\n' + '''
import json, os, sys
from pathlib import Path
root = Path(os.environ['DISPATCH_FIXTURE_ROOT'])
(root / 'prompt.txt').write_text(sys.argv[-1])
Path(sys.argv[sys.argv.index('-o') + 1]).write_text('fixture job completed')
print(json.dumps({'type': 'turn.completed'}))
''')
            fake.chmod(0o755)
            (root / 'models_cache.json').write_text(json.dumps({
                'models': [{'slug': 'gpt-6.1-sol'}]}))
            registry = desks.Registry(root / 'registry.json')
            registry.register('fixture-desk', 'codex-session', family='sol', effort='low', cwd=tmp)
            db, results = root / 'queue.sqlite3', root / 'results.jsonl'
            env = {'HOME': tmp, 'CODEX_HOME': tmp,
                   'PATH': str(bins) + os.pathsep + os.environ['PATH'],
                   'DISPATCH_FIXTURE_ROOT': tmp, 'CARR_WORK_QUEUE_DB': str(db),
                   'CARR_CODEX_SLOT_DIR': str(root / 'slots'),
                   'PROGRESS_BOARD_LOCAL_ONLY': '1'}
            children = []

            def kick(path, registry_path, results_path):
                children.append(subprocess.Popen([
                    sys.executable, str(Path(work_runner.__file__)), '--db', str(path),
                    '--registry', str(registry_path), '--results', str(results_path)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

            def command(*args):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = dispatch.main(['--registry', str(registry.path),
                                          '--results', str(results), *args])
                self.assertEqual(code, 0, output.getvalue())
                return json.loads(output.getvalue())

            try:
                with patch.dict(os.environ, env, clear=True), \
                     patch.object(dispatch.seat_gate, 'check', return_value=None), \
                     patch.object(work_runner, 'kick', side_effect=kick):
                    receipt = command('send', 'fixture-desk', 'Return a fixture completion.',
                                      '--fresh', '--card-id', 'fixture-job')
                    self.assertEqual(receipt['status'], 'queued')
                    deadline = time.monotonic() + 15
                    while True:
                        card = command('show', 'fixture-job')
                        if not card['active'] and card['stage'] != 'queued':
                            break
                        if time.monotonic() >= deadline:
                            self.fail(f'fixture did not finish: {card}')
                        time.sleep(0.05)
                    self.assertEqual(card['stage'], 'review', card)
                    self.assertEqual(card['model'], 'gpt-6.1-sol')
                    recorded = [json.loads(line) for line in results.read_text().splitlines()]
                    self.assertEqual(len(recorded), 1)
                    self.assertEqual(recorded[0]['result'], 'fixture job completed')
                    self.assertEqual(recorded[0]['status'], 'completed')
                    self.assertEqual(card['dispatch_ref'], recorded[0]['msg_id'])
                    command('details', 'fixture-job', '{"description": "fixture result recorded"}', '--reason', 'fixture result')
                    command('stage', 'fixture-job', '--stage', 'parked', '--reason', 'fixture verified')
                    self.assertEqual(command('queue')['items'][0]['stage'], 'parked')
                    prompt = (root / 'prompt.txt').read_text()
                    self.assertIn('write questions to ~/carr-system/out/orchestrator-inbox.md', prompt)
                    self.assertNotIn('send_message', prompt)
            finally:
                for child in children:
                    if child.poll() is None:
                        child.terminate()
                    child.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
