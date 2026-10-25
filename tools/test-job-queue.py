"""Exercise the queue through its command-line interface."""
import contextlib
import io
from pathlib import Path
import runpy
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
import skill_picker


class JobToolsTests(unittest.TestCase):
    def invoke(self, name, *args):
        with patch.object(sys, 'argv', [name, *args]), contextlib.redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit) as exit:
                runpy.run_path(str(ROOT / 'bin' / name), run_name='__main__')
        return exit.exception.code, out.getvalue()

    def test_queue_preserves_a_leading_hyphen_brief_through_codex_exec(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / 'queue.tsv'
            (Path(tmp) / 'brief').write_text('---\nChecklist')
            spec.write_text('one\towner/repo\tbrief\thigh\tdetails\n')
            brief = '---\nChecklist\n\nTask details: details'
            commands = []
            def run(command, **kwargs):
                commands.append(command)
                return subprocess.CompletedProcess(command, 0)
            def launch(command, **kwargs):
                self.assertEqual(command[3:], ['-c', 'model_reasoning_effort=high', '--', brief])
                with patch.object(sys, 'argv', command), \
                        patch.object(sys, 'prefix', str(ROOT / '.venv')), \
                        patch('signal.signal'), patch('subprocess.run', side_effect=run), \
                        patch.object(skill_picker, 'prepare', side_effect=lambda brief, _: (brief, [])):
                    with self.assertRaises(SystemExit) as exit:
                        runpy.run_path(str(ROOT / 'bin/codex-job'), run_name='__main__')
                self.assertEqual(exit.exception.code, 0)
                return Mock(wait=Mock(return_value=0))
            with patch('subprocess.check_output', return_value=''), \
                    patch('subprocess.Popen', side_effect=launch), patch('fcntl.flock'):
                code, out = self.invoke('codex-queue', str(spec))
            self.assertEqual(code, 0)
            self.assertEqual(commands[-1][-4:], ['-c', 'model_reasoning_effort=high', '--', brief])
            self.assertIn('QUEUE-EMPTY', out)

    def test_queue_rejects_casefolded_name_collisions_before_launch(self):
        for names in (('Foo', 'foo'), ('Straße', 'STRASSE')):
            with self.subTest(names=names), tempfile.TemporaryDirectory() as tmp:
                spec = Path(tmp) / 'queue.tsv'
                (Path(tmp) / 'brief').write_text('brief')
                spec.write_text(''.join(f'{name}\towner/repo\tbrief\thigh\tdetails\n' for name in names))
                with patch('subprocess.check_output', return_value=''), \
                        patch('subprocess.Popen', return_value=Mock(wait=Mock(return_value=0))) as launch, \
                        patch('fcntl.flock'), contextlib.redirect_stderr(io.StringIO()) as error:
                    code, _ = self.invoke('codex-queue', str(spec))
                self.assertEqual(code, 2)
                self.assertIn('unique job names', error.getvalue())
                launch.assert_not_called()
                self.assertFalse((Path(tmp) / 'logs').exists())

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
                    self.assertEqual(command[1:], ['owner/repo', f'job/one{len(launched)}', '-c', 'model_reasoning_effort=high', '--', 'Base brief\n\nTask details: Do it'])
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

    def test_queue_counts_relative_jobs_and_launches_an_absolute_path(self):
        for names in ([str(i) for i in range(10)], ['queued']):
            with self.subTest(names=names), tempfile.TemporaryDirectory() as tmp:
                spec = Path(tmp) / 'queue.tsv'
                (Path(tmp) / 'brief').write_text('brief')
                spec.write_text('queued\towner/repo\tbrief\thigh\tdetails\n')
                released = False
                commands = [f'python3 bin/codex-job owner/repo job/{name} brief' for name in names]
                def output(command, **kwargs):
                    if command[0] == 'pgrep':
                        return '\n'.join(str(i) for i, argv in enumerate(commands)
                                         if not released and re.search(command[-1], argv))
                    return commands[int(command[-1])]
                def tick(_):
                    nonlocal released
                    released = True
                def launch(command, **kwargs):
                    self.assertTrue(released, 'relative jobs did not block capacity or duplicate name')
                    self.assertEqual(command[0], str(ROOT / 'bin/codex-job'))
                    return Mock(wait=Mock(return_value=0))
                with patch('subprocess.check_output', side_effect=output), \
                        patch('subprocess.Popen', side_effect=launch), patch('fcntl.flock'), \
                        patch('time.sleep', side_effect=tick), \
                        patch.object(sys, 'argv', ['bin/codex-queue', str(spec)]), \
                        contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit) as exit:
                        runpy.run_path('bin/codex-queue', run_name='__main__')
                self.assertEqual(exit.exception.code, 0)

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


if __name__ == '__main__':
    unittest.main()
