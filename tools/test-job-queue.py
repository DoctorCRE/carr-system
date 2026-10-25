"""Exercise the queue and PR waiter through their command-line interfaces."""
import contextlib
import io
import http.client
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
    def release(self, body):
        response = io.BytesIO(body)
        response.status = 200
        return response

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
                snapshots = iter([full] + [free] * 15)
                processes = []
                def output(command, **kwargs):
                    nonlocal processes
                    if command[0] == 'pgrep':
                        processes = next(snapshots).splitlines()
                        return '\n'.join(map(str, range(len(processes))))
                    return processes[int(command[-1])]
                with patch('subprocess.check_output', side_effect=output), \
                        patch('subprocess.Popen', side_effect=launch), patch('fcntl.flock'), patch('time.sleep', side_effect=tick) as sleep:
                    code, out = self.invoke('codex-queue', str(spec))
                self.assertEqual(code, int(status != 0))
                self.assertIn('one0 last error' if status else 'one0 https://github.com/owner/repo/pull/42', out)
                self.assertIn('QUEUE-EMPTY', out)
                self.assertGreaterEqual(sleep.call_count, 2)
                self.assertEqual(len(out.splitlines()), 12)
                self.assertTrue((Path(tmp) / 'logs/one10.log').exists())

    def test_queue_counts_long_running_argv_before_launching(self):
        for names in ([str(i) for i in range(10)], ['queued']):
            with self.subTest(names=names), tempfile.TemporaryDirectory() as tmp:
                spec = Path(tmp) / 'queue.tsv'
                (Path(tmp) / 'brief').write_text('brief')
                spec.write_text('queued\towner/repo\tbrief\thigh\tdetails\n')
                released = False
                def output(command, **kwargs):
                    if command[0] == 'pgrep':
                        return '' if released else '\n'.join(map(str, range(len(names))))
                    if '-p' in command:
                        name = names[int(command[-1])]
                        return '/very/long/python3 /very/long/bin/codex-job owner/repo job/' + name + ' brief'
                    return '' if released else '/very/long/python3 /very/long/bin/codex-'
                def tick(_):
                    nonlocal released
                    released = True
                def launch(*args, **kwargs):
                    self.assertTrue(released, 'launched before running capacity or duplicate name cleared')
                    return Mock(wait=Mock(return_value=0))
                with patch('subprocess.check_output', side_effect=output), \
                        patch('subprocess.Popen', side_effect=launch), patch('fcntl.flock'), \
                        patch('time.sleep', side_effect=tick):
                    code, out = self.invoke('codex-queue', str(spec))
                self.assertEqual(code, 0)
                self.assertIn('QUEUE-EMPTY', out)

    def test_queue_handles_non_utf8_argv_and_reaps_children_on_failure(self):
        for failure in (None, OSError('process query failed')):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                spec = Path(tmp) / 'queue.tsv'
                (Path(tmp) / 'brief').write_text('brief')
                spec.write_text(''.join(f'{name}\towner/repo\tbrief\thigh\tdetails\n' for name in ('first', 'second')))
                children = []
                def launch(*args, **kwargs):
                    job = Mock(poll=Mock(return_value=0), wait=Mock(return_value=0))
                    children.append(job)
                    return job
                def output(command, **kwargs):
                    if command[0] == 'pgrep':
                        return '123' if children else ''
                    if failure:
                        raise failure
                    return b'python3 /repo/bin/codex-job owner/repo job/other \xff'.decode(
                        errors=kwargs.get('errors', 'strict'))
                with patch('subprocess.check_output', side_effect=output), \
                        patch('subprocess.Popen', side_effect=launch), patch('fcntl.flock'):
                    if failure:
                        with self.assertRaises(OSError):
                            self.invoke('codex-queue', str(spec))
                    else:
                        code, out = self.invoke('codex-queue', str(spec))
                        self.assertEqual(code, 0)
                        self.assertIn('QUEUE-EMPTY', out)
                self.assertTrue(children)
                self.assertTrue(all(job.wait.called for job in children), 'left launched children unwaited')

    def test_live_retries_truncated_http_body(self):
        sha = 'a' * 40
        broken = Mock()
        broken.read.side_effect = http.client.IncompleteRead(b'{')
        broken.__enter__ = Mock(return_value=broken)
        broken.__exit__ = Mock(return_value=False)
        good = self.release(json.dumps({
            'ok': True, 'env': {'value': 'production'}, 'git_sha': {'value': sha},
        }).encode())
        with patch('subprocess.check_output', return_value='MERGED\t' + sha), \
                patch('urllib.request.urlopen', side_effect=[broken, good]), patch('time.sleep'):
            code, out = self.invoke('wait-pr', 'owner/repo', '42', 'live')
        self.assertEqual(code, 0)
        self.assertIn('LIVE ' + sha, out)

    def test_live_requires_a_served_production_release(self):
        sha = 'a' * 40
        good = {'ok': True, 'env': {'value': 'production'}, 'git_sha': {'value': sha}}
        for body, status in [
            ({**good, 'ok': False}, 200),
            ({**good, 'ok': 1}, 200),
            ({**good, 'env': {'value': 'staging'}}, 200),
            ({**good, 'env': None}, 200),
            ({**good, 'env': 'production'}, 200),
            ({**good, 'git_sha': {'value': 'bad'}}, 200),
            (good, 503),
        ]:
            with self.subTest(body=body, status=status):
                response = io.BytesIO(json.dumps(body).encode())
                response.status = status
                with patch('subprocess.check_output', return_value='MERGED\t' + sha), \
                        patch('urllib.request.urlopen', return_value=response), patch('time.sleep'):
                    code, out = self.invoke('wait-pr', 'owner/repo', '42', 'live')
                self.assertEqual(code, 2)
                self.assertNotIn('LIVE', out)

    def test_waiter_retries_and_distinguishes_closed_merged_and_live(self):
        merged = 'MERGED\t' + 'a' * 40
        for mode, replies, releases, expected in [
            ('merged', ['', subprocess.CalledProcessError(1, 'gh'), merged], [], 0),
            ('merged', ['CLOSED\t'], [], 1),
            ('live', ['CLOSED\t'], [], 1),
            ('merged', ['[]', '{}', 'OPEN\t', 'MERGED\tbad', merged], [], 0),
            ('merged', [''] * 960, [], 2),
            ('live', [merged], [OSError('offline'), b'{}', b'{"git_sha":{"value":"old"}}',
                                json.dumps({'ok': True, 'env': {'value': 'production'}, 'git_sha': {'value': 'a' * 40}}).encode()], 0),
        ]:
            with self.subTest(mode=mode, expected=expected), \
                    patch('subprocess.check_output', side_effect=replies), \
                    patch('urllib.request.urlopen', side_effect=[self.release(r) if isinstance(r, bytes) else r for r in releases]) as release, \
                    patch('time.sleep'):
                code, out = self.invoke('wait-pr', 'owner/repo', '42', mode)
                self.assertEqual(code, expected)
                if mode == 'live' and expected == 0:
                    self.assertIn('a' * 40, out)
                    release.assert_called_with('https://api.doctorcre.com/release', timeout=30)


if __name__ == '__main__':
    unittest.main()
