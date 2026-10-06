#!/usr/bin/env python3
"""Bounded Grok retrieval suppresses session context, never effect guards."""
import importlib.util
import builtins
import json
import io
import os
from pathlib import Path
import sys
import subprocess
import shutil
import tempfile
import unittest
from unittest import mock
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GrokHookBoundaryTests(unittest.TestCase):
    def test_context_wrapper_reaches_protected_probe_without_process_readback(self):
        meter = load(ROOT / 'hooks/hook-meter-run.py')
        from hooks import grok_invocation as boundary
        probe = boundary.bounded_grok_read_only
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(boundary, 'bounded_grok_read_only', wraps=probe) as called, \
                mock.patch.object(boundary.subprocess, 'run') as ps:
            self.assertFalse(meter.bounded_grok_read_only())
            called.assert_called_once()
            ps.assert_not_called()

    def test_separate_session_owner_stops_inherited_exemption(self):
        from hooks import grok_invocation as boundary
        prefix = 'grok --model grok-4.7 --reasoning-effort high --max-turns 60 --always-approve --sandbox read-only --output-format streaming-json --print retrieval'
        for owner in ('claude --permission-mode acceptEdits task',
                      'codex exec --dangerously-bypass-hook-trust --sandbox workspace-write task',
                      'node /opt/bin/claude --permission-mode acceptEdits task',
                      'python unrelated-session.py',
                      'grok --sandbox workspace --print task'):
            with self.subTest(owner=owner), mock.patch.dict(os.environ, {'CARR_GROK_RUN_READ_ONLY': '1'}), \
                    mock.patch.object(boundary.subprocess, 'run', side_effect=[
                        subprocess.CompletedProcess([], 0, '123 /bin/sh -c hook', ''),
                        subprocess.CompletedProcess([], 0, '124 ' + owner, ''),
                        subprocess.CompletedProcess([], 0, '1 ' + prefix, '')]) as ps:
                self.assertFalse(boundary.bounded_grok_read_only())
                self.assertEqual(ps.call_count, 2)

    def test_total_probe_deadline_reserves_time_for_gate(self):
        from hooks import grok_invocation as boundary
        clock = [0.0]
        def delayed(*args, **kwargs):
            clock[0] += 0.68
            return subprocess.CompletedProcess([], 0, '123 /bin/sh -c hook', '')
        with mock.patch.dict(os.environ, {'CARR_GROK_RUN_READ_ONLY': '1'}), \
                mock.patch.object(time, 'monotonic', side_effect=lambda: clock[0]), \
                mock.patch.object(boundary.subprocess, 'run', side_effect=delayed) as ps:
            self.assertFalse(boundary.bounded_grok_read_only())
            self.assertLessEqual(ps.call_count, 2)
            timeouts = [call.kwargs['timeout'] for call in ps.call_args_list]
            self.assertLess(timeouts[0], 1)
            self.assertLess(timeouts[1], timeouts[0])
        # A ps timeout itself also leaves the gate enabled.
        with mock.patch.dict(os.environ, {'CARR_GROK_RUN_READ_ONLY': '1'}), \
                mock.patch.object(boundary.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ps', 0.75)):
            self.assertFalse(boundary.bounded_grok_read_only())

    def test_helper_is_protected_and_hash_drift_is_detected(self):
        from hooks import grok_invocation as boundary
        paths = load(ROOT / 'hooks/gate_paths.py')
        integrity = load(ROOT / 'hooks/gate-integrity.py')
        helper = Path(boundary.__file__)
        self.assertTrue(paths.is_enforcement(str(helper)))
        self.assertTrue(paths.enforcement_write('echo altered > ' + str(helper)))
        self.assertIn(helper.name, integrity.GATED)
        baseline = __import__('json').loads((ROOT / 'ops/config/gate-baseline.json').read_text())
        self.assertIn(helper.name, baseline['hashes'])
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            hooks = root / 'hooks'
            hooks.mkdir()
            copied = hooks / helper.name
            copied.write_bytes(helper.read_bytes())
            with mock.patch.object(integrity, 'HOOKS', str(hooks)):
                before = integrity.current()[helper.name]
                copied.write_text(copied.read_text() + '\n# changed predicate\n')
                self.assertNotEqual(integrity.current()[helper.name], before)
            # Drive the strict checker itself on a copied fixture. No mutation
            # of this checkout, live wiring, or provider state is needed.
            shutil.copy(ROOT / 'hooks/gate-integrity.py', hooks)
            config = root / 'ops/config'
            config.mkdir(parents=True)
            config.joinpath('gate-baseline.json').write_text(json.dumps({
                'hashes': {helper.name: before}, 'contracts': {}}))
            result = subprocess.run([sys.executable, str(hooks / 'gate-integrity.py'), '--strict'],
                                    capture_output=True, text=True, cwd=root, timeout=15)
            self.assertEqual(result.returncode, 1)
            self.assertIn('CHANGED: hooks/' + helper.name, result.stdout)

    def test_missing_optional_probe_retains_direct_hook_processing(self):
        original = builtins.__import__
        def missing(name, *args, **kwargs):
            if name.endswith('grok_invocation'):
                raise ImportError('fixture: optional probe unavailable')
            return original(name, *args, **kwargs)
        for name, downstream, value in (('hooks/worktree-self-plumb.py', 'resolve_cwd', None),
                                       ('hooks/machine-converge.py', 'machine_role_marker', 'primary'),
                                       ('ops/claude-continuity-hook.py', '_read_mode', 'disabled')):
            module = load(ROOT / name)
            for marker in ('1', '0'):
                with self.subTest(name=name, marker=marker), mock.patch.dict(os.environ, {'CARR_GROK_RUN_READ_ONLY': marker}), \
                        mock.patch('builtins.__import__', side_effect=missing), \
                        mock.patch.object(sys, 'stdin', io.StringIO('{}')), \
                        mock.patch.object(module, downstream, return_value=value) as ordinary:
                    self.assertEqual(module.main(), 0)
                    ordinary.assert_called_once()

    def test_real_process_ancestry_without_a_provider_call(self):
        # A native fixture keeps the same argv visible to ps as the Grok CLI.
        # It only launches our probe: no provider, hook side effects or network.
        compiler = shutil.which('cc')
        if compiler is None:
            self.skipTest('native process fixture requires cc')
        with tempfile.TemporaryDirectory(prefix='grok-boundary-') as directory:
            scratch = Path(directory)
            source = scratch / 'launcher.c'
            source.write_text('''
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
int main(int argc, char **argv) {
    pid_t child = fork();
    if (child < 0) return 2;
    if (child == 0) {
        const char *base = strrchr(argv[0], '/');
        base = base ? base + 1 : argv[0];
        if (getenv("BOUNDARY_OWNER") && strcmp(base, "grok") == 0) {
            execl(getenv("BOUNDARY_OWNER"), getenv("BOUNDARY_OWNER"),
                  "--permission-mode", "acceptEdits", "task", (char *)0);
            _exit(2);
        }
        execl(getenv("BOUNDARY_PYTHON"), getenv("BOUNDARY_PYTHON"),
              getenv("BOUNDARY_PROBE"), (char *)0);
        _exit(2);
    }
    int status;
    if (waitpid(child, &status, 0) < 0) return 2;
    return WIFEXITED(status) ? WEXITSTATUS(status) : 2;
}
''')
            binary = scratch / 'grok'
            subprocess.run([compiler, str(source), '-o', str(binary)],
                           check=True, capture_output=True, timeout=30)
            probe = scratch / 'probe.py'
            probe.write_text('import sys\nsys.path.insert(0, ' + repr(str(ROOT)) +
                             ')\nfrom hooks.grok_invocation import bounded_grok_read_only\n'
                             'print(bounded_grok_read_only())\n')
            env = dict(os.environ, CARR_GROK_RUN_READ_ONLY='1',
                       BOUNDARY_PYTHON=sys.executable, BOUNDARY_PROBE=str(probe))
            prefix = [str(binary), '--model', 'grok-4.7', '--reasoning-effort',
                      'high', '--max-turns', '60', '--always-approve', '--sandbox']
            for sandbox, expected in (('read-only', 'True'), ('workspace', 'False')):
                result = subprocess.run(prefix + [sandbox, '--output-format',
                    'streaming-json', '--print', 'long prompt ' * 300], env=env,
                    capture_output=True, text=True, check=True, timeout=10)
                self.assertEqual(result.stdout.strip(), expected)
            result = subprocess.run([str(binary)], env=env, capture_output=True,
                                    text=True, check=True, timeout=10)
            self.assertEqual(result.stdout.strip(), 'False')
            for owner in ('claude', 'codex'):
                nested = scratch / owner
                shutil.copy(binary, nested)
                result = subprocess.run(prefix + ['read-only', '--output-format',
                    'streaming-json', '--print', 'retrieval'],
                    env={**env, 'BOUNDARY_OWNER': str(nested)},
                    capture_output=True, text=True, check=True, timeout=10)
                self.assertEqual(result.stdout.strip(), 'False', owner)

    def test_marker_requires_exact_read_only_print_ancestor(self):
        from hooks import grok_invocation as boundary
        prefix = '/Users/example/.grok/bin/grok-1.0.46 --model grok-4.7 --reasoning-effort high --max-turns 60 --always-approve --sandbox read-only --output-format streaming-json'
        cases = [(prefix + ' --print test', True),
                 (prefix.replace('read-only', 'workspace') + ' --print test', False),
                 ('grok', False), (prefix + ' --printable test', False),
                 ('grok test mentioning --sandbox read-only --print test', False),
                 (prefix + ' --sandbox workspace --print test', False),
                 (prefix + ' --print test mentions --sandbox workspace', True)]
        for command, expected in cases:
            with self.subTest(command=command), mock.patch.dict(
                    os.environ, {'CARR_GROK_RUN_READ_ONLY': '1'}), \
                    mock.patch.object(boundary.subprocess, 'run', side_effect=[
                        subprocess.CompletedProcess([], 0, '123 /bin/sh -c hook', ''),
                        subprocess.CompletedProcess([], 0, '1 ' + command, '')]):
                self.assertEqual(boundary.bounded_grok_read_only(), expected)
                self.assertIn('-ww', boundary.subprocess.run.call_args.args[0])
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(boundary.subprocess, 'run') as ps:
            self.assertFalse(boundary.bounded_grok_read_only())
            ps.assert_not_called()
        with mock.patch.dict(os.environ, {'CARR_GROK_RUN_READ_ONLY': '1'}), \
                mock.patch.object(boundary.subprocess, 'run', side_effect=OSError):
            self.assertFalse(boundary.bounded_grok_read_only())

    def test_invocation_marker_is_read_only_and_not_inherited_by_writable(self):
        wire = load(ROOT / 'tools/room-bridge/grok_wire.py')
        for writable in (False, True):
            with self.subTest(writable=writable), mock.patch.dict(
                    os.environ, {'CARR_GROK_RUN_READ_ONLY': '1'}):
                provider = mock.Mock()
                wire.invoke_cli('test', writable=writable, run=provider)
                env = provider.call_args.kwargs.get('env', {})
                self.assertEqual(env.get('CARR_GROK_RUN_READ_ONLY'), None if writable else '1')

    def test_context_hooks_skip_before_reading_payload_or_running_target(self):
        meter = load(ROOT / 'hooks/hook-meter-run.py')
        for target in ('gate-integrity.py', 'rule-boot-gate.py', 'context-handoff-gate.py',
                       'session-presence-hook.py', 'rule-pack-preuse-reselection.py',
                       'rule-pack-drift-gate.py', 'chat-lint-carryover.py'):
            with self.subTest(target=target), mock.patch.object(sys, 'argv',
                    ['meter', str(ROOT / 'hooks' / target)]), \
                    mock.patch.object(meter, 'bounded_grok_read_only', return_value=True, create=True), \
                    mock.patch.object(sys, 'stdin', io.StringIO('invalid payload')), \
                    mock.patch('builtins.open', side_effect=AssertionError('context hook ran')):
                self.assertEqual(meter.main(), 0)

    def test_direct_session_hooks_skip_before_effects(self):
        for name, downstream in (('hooks/worktree-self-plumb.py', 'resolve_cwd'),
                                 ('hooks/machine-converge.py', 'machine_role_marker'),
                                 ('ops/claude-continuity-hook.py', '_read_mode')):
            module = load(ROOT / name)
            with self.subTest(name=name), mock.patch.dict(os.environ, {'CARR_GROK_RUN_READ_ONLY': '1'}), mock.patch(
                    'hooks.grok_invocation.bounded_grok_read_only', return_value=True), \
                    mock.patch.object(sys, 'stdin', io.StringIO('{"cwd":"/tmp"}')), \
                    mock.patch.object(module, downstream) as effect:
                self.assertEqual(module.main(), 0)
                effect.assert_not_called()

    def test_effect_guard_runs_and_context_hooks_run_without_exemption(self):
        meter = load(ROOT / 'hooks/hook-meter-run.py')
        for target, bounded in (('guard-unattended.py', True),
                                ('rule-boot-gate.py', False)):
            with self.subTest(target=target), mock.patch.object(sys, 'argv',
                    ['meter', str(ROOT / 'hooks' / target)]), \
                    mock.patch.object(meter, 'bounded_grok_read_only', return_value=bounded), \
                    mock.patch.object(sys, 'stdin', io.StringIO('{}')), \
                    mock.patch('builtins.open', mock.mock_open(read_data=b'raise SystemExit(2)')):
                self.assertEqual(meter.main(), 2)


if __name__ == '__main__':
    unittest.main()
