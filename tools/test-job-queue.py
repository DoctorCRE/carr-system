"""Exercise the queue and PR waiter through their command-line interfaces."""
import contextlib
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class JobToolsTests(unittest.TestCase):
    def invoke(self, name, *args):
        with patch.object(sys, 'argv', [name, *args]), contextlib.redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit) as exit:
                runpy.run_path(str(ROOT / 'bin' / name), run_name='__main__')
        return exit.exception.code, out.getvalue()

    def test_queue_waits_for_capacity_and_reports_each_job(self):
        for status in (0, 7):
            with tempfile.TemporaryDirectory() as tmp:
                spec = Path(tmp) / 'queue.tsv'
                (Path(tmp) / 'brief').write_text('Base brief')
                spec.write_text(''.join(f'one{i}\towner/repo\tbrief\thigh\tDo it\n' for i in range(11)))
                launched = []
                full = '\n'.join(f'python3 /repo/bin/codex-job owner/repo job/{i} brief' for i in range(10))
                free = '\n'.join(['python3 /repo/bin/codex-job owner/repo job/other brief'] * 20)
                def launch(command, **kw):
                    self.assertLess(sum(p.poll() is None for p in launched), 9)
                    self.assertEqual(command[1:], ['owner/repo', f'job/one{len(launched)}', 'Base brief\n\nTask details: Do it', '-c', 'model_reasoning_effort=high'])
                    def finish():
                        Path(kw['stdout'].name).write_text('https://github.com/owner/repo/pull/42\nlast error\n')
                        return status
                    job = Mock(poll=Mock(return_value=None), wait=Mock(side_effect=finish))
                    launched.append(job)
                    return job
                def tick(_):
                    for job in launched:
                        job.poll.return_value = status
                with patch('subprocess.check_output', side_effect=[full] + [free] * 15), \
                        patch('subprocess.Popen', side_effect=launch), patch('fcntl.flock'), patch('time.sleep', side_effect=tick) as sleep:
                    code, out = self.invoke('codex-queue', str(spec))
                self.assertEqual(code, int(status != 0))
                self.assertIn('one0 last error' if status else 'one0 https://github.com/owner/repo/pull/42', out)
                self.assertIn('QUEUE-EMPTY', out)
                self.assertGreaterEqual(sleep.call_count, 2)
                self.assertEqual(len(out.splitlines()), 12)
                self.assertTrue((Path(tmp) / 'logs/one10.log').exists())

    def test_waiter_retries_and_distinguishes_closed_merged_and_live(self):
        merged = 'MERGED\t' + 'a' * 40
        for mode, replies, releases, expected in [
            ('merged', ['', subprocess.CalledProcessError(1, 'gh'), merged], [], 0),
            ('merged', ['CLOSED\t'], [], 1),
            ('live', ['CLOSED\t'], [], 1),
            ('merged', ['[]', '{}', 'OPEN\t', 'MERGED\tbad', merged], [], 0),
            ('merged', [''] * 960, [], 2),
            ('live', [merged], [OSError('offline'), b'{}', b'{"git_sha":{"value":"old"}}',
                                json.dumps({'git_sha': {'value': 'a' * 40}}).encode()], 0),
        ]:
            with self.subTest(mode=mode, expected=expected), \
                    patch('subprocess.check_output', side_effect=replies), \
                    patch('urllib.request.urlopen', side_effect=[io.BytesIO(r) if isinstance(r, bytes) else r for r in releases]) as release, \
                    patch('time.sleep'):
                code, out = self.invoke('wait-pr', 'owner/repo', '42', mode)
                self.assertEqual(code, expected)
                if mode == 'live' and expected == 0:
                    self.assertIn('a' * 40, out)
                    release.assert_called_with('https://api.doctorcre.com/release', timeout=30)


if __name__ == '__main__':
    unittest.main()
