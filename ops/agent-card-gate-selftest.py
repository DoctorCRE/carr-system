#!/usr/bin/env python3
"""Exercise launch admission through the hook's JSON stdin boundary."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock

from git_env import fixture_env

REPO = Path(__file__).resolve().parent.parent


class AgentCardGate(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ('hooks', 'lib', 'tools', 'ops'):
            (self.root / directory).mkdir()
        for relative in ('hooks/agent-card-gate.py', 'hooks/cmd_text.py', 'hooks/guard-unattended.py',
                         'hooks/gate_paths.py', 'hooks/refused_content.py', 'lib/hook_runtime.py',
                         'lib/agent_card_cache.py', 'tools/call-verb.py', 'ops/git_env.py'):
            if (REPO / relative).exists():
                shutil.copyfile(REPO / relative, self.root / relative)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, env=fixture_env())
        subprocess.run(['git', 'remote', 'add', 'origin',
                        'https://github.com/DoctorCRE/carr-system.git'], cwd=self.root, check=True, env=fixture_env())
        self.now = None
        self.home = self.root / 'home'
        self.home.mkdir()
        self.lookup = self.root / 'tools/call-verb.py'
        self.reply_fixture(False)

    def reply_fixture(self, registered):
        self.lookup.write_text('import json, sys\n'
                               'assert sys.argv[1] == "job-card-registration"\n'
                               'card = json.loads(sys.argv[2])["card_id"]\n'
                               'print(json.dumps({"ok": True, "card_id": card, '
                               f'"registered": {registered!r} and card in '
                               '("WR557.S6.J1", "WR557.S6.J2"), '
                               '"executor": "codex", "model": "synthetic", '
                               '"repository": "DoctorCRE/carr-system", "work_request_state": "captured"}))\n')


    def run_hook(self, tool='Agent', **inputs):
        if tool == 'Agent':
            inputs.setdefault('run_in_background', True)
            if inputs['run_in_background'] is None:
                del inputs['run_in_background']
        hook = self.root / 'hooks/agent-card-gate.py'
        command = [sys.executable, hook]
        if self.now is not None:
            command = [sys.executable, '-c',
                       f'import runpy, time; time.time = lambda: {self.now!r}; '
                       f'runpy.run_path({str(hook)!r}, run_name="__main__")']
        return subprocess.run(command,
                              input=json.dumps({'hook_event_name': 'PreToolUse',
                                                'cwd': str(self.root), 'tool_name': tool, 'tool_input': inputs}),
                              text=True, capture_output=True, env=dict(fixture_env(), HOME=str(self.home)), timeout=10)

    def mint_fixture(self, reply=None, rc=0, args=None, stdin=False):
        wrapper = self.root / 'tools/mint-wrapper.py'
        shutil.copyfile(REPO / 'tools/call-verb.py', wrapper)
        (self.root / 'mcp-server').mkdir(exist_ok=True)
        fake = self.root / 'mcp-server/local-verb.mjs'
        reply = reply if reply is not None else {'ok': True, 'card_id': 'WR557.S6.J1', 'priority': 1}
        fake.write_text('import sys\nprint(' + repr(json.dumps(reply)) + ')\nraise SystemExit(' + str(rc) + ')')
        args = args if args is not None else {'work_request': 'WR-000557', 'slice': 6,
                                             'executor': 'codex', 'model': 'synthetic',
                                             'repository': 'DoctorCRE/carr-system'}
        argument = '-' if stdin else json.dumps(args)
        code = (f'import runpy, sys, time; time.time = lambda: {self.now or 1000000}; '
                f'm = runpy.run_path({str(wrapper)!r}); '
                'm["main"].__globals__["NODE_CANDIDATES"] = [sys.executable]; '
                f'sys.argv = ["call-verb.py", "dispatch-job", {argument!r}]; m["main"]()')
        return subprocess.run([sys.executable, '-c', code], input=json.dumps(args) if stdin else None,
                              capture_output=True, text=True,
                              env=dict(fixture_env(), HOME=str(self.home)), timeout=10)

    def test_fresh_mint_first_launch_does_not_lookup(self):
        self.now = 1000000
        minted = self.mint_fixture()
        self.assertEqual(minted.returncode, 0, minted.stderr)
        marker = self.root / 'lookup-attempted'
        self.lookup.write_text('from pathlib import Path\nimport time\nPath(' + repr(str(marker))
                               + ').touch()\ntime.sleep(30)\n')
        result = self.run_hook(prompt='Card: WR557.S6.J1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists(), 'first admission started a registration lookup')

    def test_mint_from_stdin_preserves_response_and_seeds(self):
        self.now = 1000000
        minted = self.mint_fixture(stdin=True)
        self.assertEqual(minted.returncode, 0, minted.stderr)
        self.assertEqual(json.loads(minted.stdout), {'ok': True, 'card_id': 'WR557.S6.J1', 'priority': 1})
        self.lookup.write_text('raise SystemExit(1)')
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 0)

    def test_mint_cache_refuses_unverified_replies_and_wrong_work_request(self):
        self.now = 1000000
        for reply, rc in [({'ok': False, 'card_id': 'WR557.S6.J1', 'priority': 1}, 0),
                          ({'ok': True, 'card_id': 'WR558.S6.J1', 'priority': 1}, 0),
                          ({'ok': True, 'card_id': 'WR557.S7.J1', 'priority': 1}, 0),
                          ({'ok': True, 'card_id': 'WR557.S6.J1'}, 0),
                          ({'ok': True, 'card_id': 'WR557.S6.J1', 'priority': 1}, 1)]:
            with self.subTest(reply=reply, rc=rc):
                self.mint_fixture(reply=reply, rc=rc)
                self.lookup.write_text('raise SystemExit(1)')
                self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 2)
                self.assertEqual(self.run_hook(prompt='Card: WR558.S6.J1').returncode, 2)

    def test_mint_cache_is_card_bound_and_one_use_before_live_checks(self):
        self.now = 1000000
        self.assertEqual(self.mint_fixture().returncode, 0)
        self.lookup.write_text('raise SystemExit(1)')
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J2').returncode, 2)
        self.assertEqual(self.run_hook(prompt='Card: WR558.S6.J1').returncode, 2)
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 0)
        self.reply_fixture(False)
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 2)
        self.lookup.write_text('raise SystemExit(1)')
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 2)

    def test_minted_cache_expiry_and_tampering_refuse(self):
        self.now = 1000000
        self.mint_fixture()
        self.lookup.write_text('raise SystemExit(1)')
        self.now += 24 * 60 * 60
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 2)
        self.mint_fixture()
        cache = self.home / '.cache/carr/agent-card-cache-v1/entries.json'
        entries = json.loads(cache.read_text())
        entry = entries.pop('WR557.S6.J1')
        entry['body']['card_id'] = 'WR558.S6.J1'
        entries['WR558.S6.J1'] = entry
        cache.write_text(json.dumps(entries))
        self.assertEqual(self.run_hook(prompt='Card: WR558.S6.J1').returncode, 2)

    def test_minted_card_still_requires_exactly_one_card_line(self):
        self.now = 1000000
        self.mint_fixture()
        self.lookup.write_text('raise SystemExit(1)')
        for prompt in ('Review.', 'Card: WR557.S6.J1\nCard: WR557.S6.J2',
                       'Card: WR557.S6.J1\nCard: malformed', 'Card: WR557.S6.J0'):
            self.assertEqual(self.run_hook(prompt=prompt).returncode, 2)
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 0)

    def test_negative_eviction_survives_lock_contention_and_outage(self):
        self.now = 1000000
        self.register_fixture()
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 0)
        lock_path = self.home / '.cache/carr/agent-card-cache-v1/lock'
        with lock_path.open() as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.reply_fixture(False)
            self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 2)
        self.lookup.write_text('raise SystemExit(1)')
        result = self.run_hook(prompt='Card: WR557.S6.J1')
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_changed_credential_refuses_mint_and_outage_evidence(self):
        self.now = 1000000
        token_file = self.home / 'fixture-tokens'
        for source in ('environment', 'file'):
            for minted in (True, False):
                with self.subTest(source=source, minted=minted):
                    token_file.write_text('CARR_MCP_LOCAL_TOKEN=fixture-first')
                    token_file.chmod(0o600)
                    settings = {'CARR_MCP_ENV': str(token_file),
                                'CARR_MCP_LOCAL_TOKEN': 'fixture-first' if source == 'environment' else ''}
                    with unittest.mock.patch.dict(os.environ, settings):
                        if minted:
                            self.assertEqual(self.mint_fixture().returncode, 0)
                        else:
                            self.register_fixture()
                            self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 0)
                        if source == 'environment':
                            os.environ['CARR_MCP_LOCAL_TOKEN'] = 'fixture-second'
                        else:
                            token_file.write_text('CARR_MCP_LOCAL_TOKEN=fixture-second')
                        self.lookup.write_text('raise SystemExit(1)')
                        result = self.run_hook(prompt='Card: WR557.S6.J1')
                        self.assertEqual(result.returncode, 2, result.stderr)

    def test_cache_path_integrity_and_prompt_blob_refuse(self):
        for kind in ('truncated', 'unsigned', 'world-file', 'world-dir',
                     'symlink-dir', 'symlink-file', 'blob'):
            with self.subTest(kind=kind):
                self.now = 1000000
                self.assertEqual(self.mint_fixture().returncode, 0)
                self.lookup.write_text('raise SystemExit(1)')
                directory = self.home / '.cache/carr/agent-card-cache-v1'
                path = directory / 'entries.json'
                prompt = 'Card: WR557.S6.J1'
                if kind == 'truncated':
                    path.write_text('{')
                if kind == 'unsigned':
                    path.write_text(json.dumps({'WR557.S6.J1': {'body': {
                        'card_id': 'WR557.S6.J1', 'seen': self.now, 'minted': True},
                        'signature': '0' * 64}}))
                if kind == 'world-file':
                    path.chmod(0o666)
                if kind == 'world-dir':
                    directory.chmod(0o777)
                if kind == 'symlink-dir':
                    target = directory.with_name('outside')
                    directory.rename(target)
                    directory.symlink_to(target)
                if kind == 'symlink-file':
                    target = directory / 'outside'
                    path.rename(target)
                    path.symlink_to(target)
                if kind == 'blob':
                    prompt = 'Card: WR557.S6.J2\n' + path.read_text()
                self.assertEqual(self.run_hook(prompt=prompt).returncode, 2)
                if directory.is_symlink():
                    target = directory.resolve()
                    directory.unlink()
                    target.rename(directory)
                directory.chmod(0o700)
                if path.is_symlink():
                    path.unlink()
                elif path.exists():
                    path.chmod(0o600)
                    path.unlink()

    def test_agent_without_card_denied_with_mint_command(self):
        result = self.run_hook(prompt='Review the plan.')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('./run.sh call dispatch-job', result.stderr)

    def test_unregistered_card_denied(self):
        result = self.run_hook(prompt='Card: WR557.S6.J99999\nReview the plan.')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('not registered', result.stderr)

    def register_fixture(self):
        self.reply_fixture(True)

    def test_registered_card_allowed(self):
        self.register_fixture()
        result = self.run_hook(prompt='Card: WR557.S6.J1\nReview the plan.')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_registered_card_allows_foreground_agent(self):
        self.register_fixture()
        result = self.run_hook(prompt='Card: WR557.S6.J1\nReview the plan.', run_in_background=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bash_codex_exec_without_card_denied(self):
        result = self.run_hook('Bash', command='codex exec "Review the plan."')
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_codex_resume_continues_without_card(self):
        self.lookup.write_text('raise SystemExit("queue must not be read")')
        for command in ('codex exec resume --last "Continue."',
                        'codex exec resume session-id "Continue."',
                        'codex -m default e resume --last --full-auto',
                        'codex exec resume --last; codex exec resume session-id'):
            with self.subTest(command=command):
                result = self.run_hook('Bash', command=command)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_resume_does_not_authorize_a_new_session(self):
        result = self.run_hook('Bash', command='codex exec resume --last; codex exec "New job."')
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_other_bash_and_launcher_untouched(self):
        for command in ('rg Card hooks',
                        "printf '%s' 'codex exec review'", "cat <<'EOF'\ncodex exec review\nEOF"):
            with self.subTest(command=command):
                self.assertEqual(self.run_hook('Bash', command=command).returncode, 0)

    def test_unknown_codex_option_requires_inline_prompt(self):
        result = self.run_hook('Bash', command='codex exec --unknown value')
        self.assertEqual(result.returncode, 2)
        self.assertIn('use a literal inline prompt', result.stderr)

    def test_bash_registered_prompt_allowed(self):
        self.register_fixture()
        for command in ("codex exec 'Card: WR557.S6.J1\nReview.'",
                        "timeout 30 /opt/bin/codex exec -c 'model=default' 'Card: WR557.S6.J1\nReview.'",
                        "codex -m default exec --skip-git-repo-check 'Card: WR557.S6.J1\nReview.'",
                        "codex cloud exec --env 6ae0a2a8 --branch main --attempts 2 'Card: WR557.S6.J1\nReview.'"):
            with self.subTest(command=command):
                result = self.run_hook('Bash', command=command)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_codex_cloud_exec_needs_card(self):
        result = self.run_hook('Bash', command="codex cloud exec --env 6ae0a2a8 --branch main 'Review.'")
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_codex_help_is_not_a_launch(self):
        for command in ('codex cloud exec --help', 'codex exec -h'):
            with self.subTest(command=command):
                self.assertEqual(self.run_hook('Bash', command=command).returncode, 0)

    def test_card_outside_launch_prompt_does_not_authorize(self):
        self.register_fixture()
        for command in ("printf 'Card: WR557.S6.J1\n'; codex exec Review",
                        "codex exec -c 'Card: WR557.S6.J1\n' Review",
                        "codex exec 'Card: WR557.S6.J1\nReview.'; codex exec Review",
                        "codex -m default exec Review"):
            with self.subTest(command=command):
                result = self.run_hook('Bash', command=command)
                self.assertEqual(result.returncode, 2, result.stderr)

    def test_foreground_agents_need_card(self):
        for kind in ('Plan', 'general-purpose'):
            with self.subTest(kind=kind):
                result = self.run_hook(prompt='Search code.', subagent_type=kind,
                                       run_in_background=False)
                self.assertEqual(result.returncode, 2, result.stderr)

    def test_agent_default_foreground_needs_card(self):
        self.assertEqual(self.run_hook(prompt='Search code.', run_in_background=None).returncode, 2)

    def test_read_only_agents_need_no_card(self):
        for kind in ('web-fetch', 'Explore'):
            for background in (False, True):
                with self.subTest(kind=kind, background=background):
                    result = self.run_hook(prompt='Read.', subagent_type=kind, run_in_background=background)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_registered_card_survives_outage(self):
        self.register_fixture()
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 0)
        self.lookup.write_text('raise SystemExit(1)')
        result = self.run_hook(prompt='Card: WR557.S6.J1')
        self.assertEqual(result.returncode, 0, result.stderr)
        unseen = self.run_hook(prompt='Card: WR557.S6.J2')
        self.assertEqual(unseen.returncode, 2, unseen.stderr)
        self.assertIn('lookup unreachable or invalid', unseen.stderr)

    def test_cached_registration_expires_after_24_hours(self):
        self.now = 1000000
        self.register_fixture()
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 0)
        self.lookup.write_text('raise SystemExit(1)')
        self.now += 24 * 60 * 60 - 1
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 0)
        self.now += 1
        expired = self.run_hook(prompt='Card: WR557.S6.J1')
        self.assertEqual(expired.returncode, 2, expired.stderr)
        self.assertIn('lookup unreachable or invalid', expired.stderr)
        self.assertIn('WR557.S6.J1', expired.stderr)

    def test_live_unregistered_result_overrides_cache(self):
        self.register_fixture()
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 0)
        self.reply_fixture(False)
        result = self.run_hook(prompt='Card: WR557.S6.J1')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('not registered', result.stderr)
        self.lookup.write_text('raise SystemExit(1)')
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 2)

    def test_cached_card_does_not_cover_other_launches(self):
        self.register_fixture()
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 0)
        self.lookup.write_text('raise SystemExit(1)')
        result = self.run_hook('Bash', command="codex exec 'Card: WR557.S6.J1'; codex exec 'Card: WR557.S6.J2'")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('lookup unreachable or invalid', result.stderr)

    def test_invalid_queue_uses_cached_registration(self):
        self.register_fixture()
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 0)
        for source in ('print("invalid JSON")', 'print("[]")',
                       'print(\'{"ok": true, "jobs": [{}]}\')'):
            with self.subTest(source=source):
                self.lookup.write_text(source)
                result = self.run_hook(prompt='Card: WR557.S6.J1')
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_lookup_failure_denies(self):
        for source in ('raise SystemExit(1)', 'print("invalid JSON")',
                       '''print('{"ok": false, "jobs": []}')''', '''print('{"ok": true}')'''):
            with self.subTest(source=source):
                self.lookup.write_text(source)
                result = self.run_hook(prompt='Card: WR557.S6.J1\nReview.')
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('lookup unreachable or invalid', result.stderr)

    def test_registration_lookup_avoids_slow_dashboard(self):
        self.lookup.write_text('import json, sys, time\n'
                               'if sys.argv[1] == "work-queue": time.sleep(8)\n'
                               'assert sys.argv[1] == "job-card-registration"\n'
                               'card = json.loads(sys.argv[2])["card_id"]\n'
                               'print(json.dumps({"ok": True, "card_id": card, "registered": True, '
                               '"executor": "codex", "model": "synthetic", '
                               '"repository": "DoctorCRE/carr-system", "work_request_state": "captured"}))\n')
        result = self.run_hook(prompt='Card: WR557.S6.J1')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_mismatched_or_duplicate_registration_response_denied(self):
        for reply in (
            {'ok': True, 'card_id': 'WR557.S6.J2', 'registered': True},
            {'ok': True, 'card_id': 'WR557.S6.J1', 'registered': 1},
            {'ok': True, 'card_id': 'WR557.S6.J1', 'registered': True},
            [{'card_id': 'WR557.S6.J1'}, {'card_id': 'WR557.S6.J1'}],
        ):
            with self.subTest(reply=reply):
                self.lookup.write_text('print(' + repr(json.dumps(reply)) + ')')
                result = self.run_hook(prompt='Card: WR557.S6.J1')
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('invalid registration', result.stderr)

    def test_lookup_exit_does_not_assume_deployment_gap(self):
        self.lookup.write_text('raise SystemExit(1)')
        result = self.run_hook(prompt='Card: WR557.S6.J1')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('registration call exited 1', result.stderr)
        self.assertNotIn('deployed', result.stderr)

    def test_single_card_refresh_preserves_other_cards_without_extending_ttl(self):
        self.now = 1000000
        self.register_fixture()
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 0)
        self.now += 100
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J2').returncode, 0)
        self.lookup.write_text('raise SystemExit(1)')
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 0)
        self.now = 1000000 + 24 * 60 * 60
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 2)
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J2').returncode, 0)

    def test_successful_registration_repairs_corrupt_cache(self):
        cache = self.home / '.cache/carr/agent-card-cache-v1/entries.json'
        self.now = 1000000
        self.assertEqual(self.mint_fixture().returncode, 0)
        cache.write_text('invalid')
        cache.chmod(0o600)
        self.register_fixture()
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 0)
        self.lookup.write_text('raise SystemExit(1)')
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 0)

    def test_project_settings_guard_both_launch_tools(self):
        groups = json.loads((REPO / '.claude/settings.json').read_text())['hooks']['PreToolUse']
        for tool in ('Agent', 'Bash'):
            self.assertTrue(any(tool in group.get('matcher', '').split('|') and
                                any('agent-card-gate.py' in hook['command']
                                    for hook in group['hooks']) for group in groups))

    def test_queue_timeout_denies(self):
        self.lookup.write_text('import time\ntime.sleep(8)\n')
        result = self.run_hook(prompt='Card: WR557.S6.J1\nReview.')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('TimeoutExpired', result.stderr)

    def test_three_slow_cards_refused_before_hook_timeout(self):
        self.register_fixture()
        self.lookup.write_text('import time\ntime.sleep(3.5)\n' + self.lookup.read_text())
        started = time.monotonic()
        result = self.run_hook('mcp__t3-code__create_threads', threads=[
            {'prompt': f'Card: WR557.S6.J{job}'} for job in (1, 2, 3)
        ])
        self.assertLess(time.monotonic() - started, 9)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('agent-card-gate: Card registration lookup deadline exceeded', result.stderr)

    def test_shared_deadline_refuses_even_with_cached_cards(self):
        self.register_fixture()
        for job in (1, 2):
            self.assertEqual(self.run_hook(prompt=f'Card: WR557.S6.J{job}').returncode, 0)
        self.lookup.write_text('import time\ntime.sleep(30)\n')
        started = time.monotonic()
        result = self.run_hook('mcp__t3-code__create_threads', threads=[
            {'prompt': f'Card: WR557.S6.J{job}'} for job in (1, 2, 3)
        ])
        self.assertLess(time.monotonic() - started, 9)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('Card registration lookup deadline exceeded', result.stderr)

    def test_malformed_or_duplicate_card_denied(self):
        self.register_fixture()
        for prompt in ('Card: WR0.S6.J1', 'Card: WR557.S6.J0',
                       'Card: WR557.S6.J1 extra', 'text Card: WR557.S6.J1',
                       'Card: WR557.S6.J1\nCard: WR557.S6.J1'):
            with self.subTest(prompt=prompt):
                self.assertEqual(self.run_hook(prompt=prompt).returncode, 2)

    def test_real_process_interleaving(self):
        self.now = 1000000
        self.assertEqual(self.mint_fixture().returncode, 0)
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 0)
        config = dict(fixture_env(), HOME=str(self.home))
        writer_code = f'''
import sys
sys.path.insert(0, {str(REPO)!r})
from lib.agent_card_cache import RegistrationCache
from pathlib import Path
original = Path.replace
def replace(path, target):
    print('READY', flush=True)
    sys.stdin.readline()
    return original(path, target)
Path.replace = replace
print('WRITER contains', RegistrationCache(clock=lambda:1000000).contains('WR557.S6.J1'), flush=True)
'''
        writer = subprocess.Popen([sys.executable, '-c', writer_code], env=config, cwd=self.root,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: writer.poll() is None and writer.kill())
        self.assertEqual(writer.stdout.readline().strip(), 'READY')
        self.reply_fixture(False)
        negative_code = f'''
import sys, runpy, time, errno
sys.path.insert(0, {str(self.root)!r})
from lib.agent_card_cache import RegistrationCache
from pathlib import Path
original = RegistrationCache._open
def open_file(path, flags):
    if Path(path).name.startswith('revoked-'):
        raise OSError(errno.ENOSPC, 'synthetic marker disk full')
    return original(path, flags)
RegistrationCache._open = staticmethod(open_file)
time.time = lambda:1000000
runpy.run_path({str(self.root/'hooks/agent-card-gate.py')!r}, run_name='__main__')
'''
        event = {'hook_event_name': 'PreToolUse', 'tool_name': 'Agent',
                 'cwd': str(self.root), 'tool_input': {'prompt': 'Card: WR557.S6.J1'}}
        negative = subprocess.run([sys.executable, '-c', negative_code], input=json.dumps(event),
                                  text=True, capture_output=True, env=config, timeout=10)
        self.assertEqual(negative.returncode, 2, negative.stderr)
        out, err = writer.communicate(input='resume\n', timeout=10)
        self.assertEqual(writer.returncode, 0, err)
        self.lookup.write_text('raise SystemExit(1)')
        later = self.run_hook(prompt='Card: WR557.S6.J1')
        print('TWO_PROCESSES negative rc=', negative.returncode, 'writer=', out.strip(),
              'later outage rc=', later.returncode)
        self.assertEqual(later.returncode, 2, later.stderr)

    def test_repository_reuse(self):
        self.now = 1000000
        args = {'work_request': 'WR-000557', 'slice': 6, 'executor': 'codex',
                'model': 'synthetic', 'repository': 'DoctorCRE/carr-system'}
        self.assertEqual(self.mint_fixture(args=args).returncode, 0)
        other = self.root / 'doctorcre-app'
        for directory in ('hooks', 'lib', 'tools', 'ops'):
            shutil.copytree(self.root / directory, other / directory)
        (other / 'tools/call-verb.py').write_text('raise SystemExit(1)')
        subprocess.run(['git', 'init', '-q', str(other)], check=True, env=fixture_env())
        subprocess.run(['git', 'remote', 'add', 'origin',
                        'https://github.com/jbookout/doctorcre-app.git'], cwd=other, check=True, env=fixture_env())
        event = {'hook_event_name': 'PreToolUse', 'tool_name': 'Agent', 'cwd': str(other),
                 'tool_input': {'prompt': 'Card: WR557.S6.J1'}}
        command = [sys.executable, '-c', f'import time,runpy;time.time=lambda:1000000;'
                   f'runpy.run_path({str(other/"hooks/agent-card-gate.py")!r},run_name="__main__")']
        result = subprocess.run(command, input=json.dumps(event), text=True, capture_output=True,
                                cwd=other, env=dict(fixture_env(), HOME=str(self.home)))
        print('CROSS_REPOSITORY gate rc=', result.returncode)
        self.assertEqual(result.returncode, 2, result.stderr)


    def test_same_repository_other_worktree_accepts(self):
        self.now = 1000000
        self.assertEqual(self.mint_fixture().returncode, 0)
        subprocess.run(['git', '-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid',
                        'commit', '-q', '--allow-empty', '-m', 'fixture'], cwd=self.root, check=True, env=fixture_env())
        worktree = self.root / 'other-worktree'
        subprocess.run(['git', 'worktree', 'add', '-q', '--detach', str(worktree)], cwd=self.root, check=True, env=fixture_env())
        self.lookup.write_text('raise SystemExit(1)')
        hook = self.root / 'hooks/agent-card-gate.py'
        event = {'hook_event_name': 'PreToolUse', 'tool_name': 'Agent', 'cwd': str(worktree),
                 'tool_input': {'prompt': 'Card: WR557.S6.J1'}}
        result = subprocess.run([sys.executable, '-c', 'import runpy,time;time.time=lambda:1000000;'
                                 f'runpy.run_path({str(hook)!r},run_name="__main__")'],
                                input=json.dumps(event), capture_output=True, text=True, cwd=worktree,
                                env=dict(fixture_env(), HOME=str(self.home)), timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_null_repository_mint_requires_live_lookup(self):
        self.now = 1000000
        args = {'work_request': 'WR-000557', 'slice': 6, 'executor': 'codex', 'model': 'synthetic',
                'repository': None}
        self.assertEqual(self.mint_fixture(args=args).returncode, 0)
        self.lookup.write_text('raise SystemExit(1)')
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 2)
        self.reply_fixture(True)
        self.lookup.write_text(self.lookup.read_text().replace('"DoctorCRE/carr-system"', 'None'))
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 0)
        self.lookup.write_text('raise SystemExit(1)')
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 2)

    def test_live_other_repository_refuses_without_seeding(self):
        self.now = 1000000
        self.reply_fixture(True)
        self.lookup.write_text(self.lookup.read_text().replace('DoctorCRE/carr-system', 'jbookout/doctorcre-app'))
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 2)
        self.lookup.write_text('raise SystemExit(1)')
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 2)


if __name__ == '__main__':
    unittest.main()
