#!/usr/bin/env python3
"""Exercise the Stop hook through its JSON stdin and transcript interface."""
import json
import shlex
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from git_env import fixture_env

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / 'hooks/rule-claim-gate.py'
ENV = fixture_env()


def message(role, content, **extra):
    return {'type': role, 'message': {'role': role, 'content': content}, **extra}


def assistant(text):
    return message('assistant', [{'type': 'text', 'text': text}])


def call(name, **inputs):
    return message('assistant', [{'type': 'tool_use', 'id': 'tool1',
                                  'name': name, 'input': inputs}])


class RuleClaimGateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.checkout('.')
        self.transcript = self.root / 'transcript.jsonl'
        self.invocations = 0

    def run_hook(self, records, **overrides):
        self.invocations += 1
        self.transcript.write_text(''.join(json.dumps(r) + '\n' for r in records))
        payload = {'hook_event_name': 'Stop', 'session_id': f'fixture-{self.invocations}',
                   'cwd': str(self.root), 'transcript_path': str(self.transcript),
                   **overrides}
        env = ENV | getattr(self, 'hook_env', {})
        result = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), env=env,
                                text=True, capture_output=True, cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def turn(self, *records):
        return [message('user', 'Please apply the correction.', uuid='human1'), *records]

    def checkout(self, name, origin='https://github.com/jbookout/carr-system.git'):
        root = self.root / name
        root.mkdir(exist_ok=True)
        subprocess.run(['git', 'init', '-q', str(root)], check=True, capture_output=True, env=ENV)
        subprocess.run(['git', '-C', str(root), 'remote', 'add', 'origin', origin],
                       check=True, env=ENV)
        return root

    def test_enforcement_edits_in_external_clone_and_worktree_pass(self):
        source = self.checkout('source')
        subprocess.run(['git', '-C', str(source), '-c', 'user.name=Fixture',
                        '-c', 'user.email=jbookout@users.noreply.github.com',
                        'commit', '-q', '--allow-empty', '-m', 'Fixture'], check=True, env=ENV)
        clone = self.root / 'scratchpad/clone'
        subprocess.run(['git', 'clone', '-q', str(source), str(clone)], check=True, env=ENV)
        subprocess.run(['git', '-C', str(clone), 'remote', 'set-url', 'origin',
                        'git@github.com:jbookout/carr-system.git'], check=True, env=ENV)
        worktree = self.root / 'scratchpad/worktree'
        subprocess.run(['git', '-C', str(source), 'worktree', 'add', '-q', '-b',
                        'fixture', str(worktree)], check=True, env=ENV)
        event_cwd = self.root / 'session'
        event_cwd.mkdir()
        for root in [clone, worktree]:
            for tool in ['Edit', 'Write']:
                with self.subTest(checkout=root.name, tool=tool):
                    self.assertNotEqual(self.run_hook(self.turn(
                        call(tool, file_path=str(root / 'hooks/check.py')),
                        assistant('I created a rule.')), cwd=str(event_cwd))
                        .get('decision'), 'block')

    def test_memory_only_blocks_with_claim_and_remedy(self):
        claim = 'I saved a rule to remember this.'
        verdict = self.run_hook(self.turn(call('Write', file_path='memory/lesson.md'),
                                          assistant(claim)))
        self.assertEqual(verdict.get('decision'), 'block')
        self.assertIn(claim, verdict['reason'])
        self.assertIn('not enforced', verdict['reason'])

    def test_enforcement_edit_passes(self):
        for path in ['hooks/check.py', 'tools/check.py', 'tests/test_check.py',
                     '.github/workflows/check.yml', '.claude/settings.json',
                     'mcp-server/src/check.js']:
            with self.subTest(path=path):
                self.assertNotEqual(self.run_hook(self.turn(
                    call('Edit', file_path=str(self.root / path)),
                    assistant('I created a rule.'))).get('decision'), 'block')

    def test_checkout_root_not_event_subdirectory_controls_paths(self):
        nested = self.root / 'session'
        nested.mkdir()
        self.assertNotEqual(self.run_hook(self.turn(
            call('Edit', file_path='../hooks/check.py'), assistant('I added a rule.')),
            cwd=str(nested)).get('decision'), 'block')

    def test_foreign_checkout_and_origin_lookalikes_do_not_count(self):
        for index, origin in enumerate([
            'https://github.com/jbookout/other.git',
            'https://example.com/jbookout/carr-system.git',
            'https://github.com/jbookout/carr-system-fake.git',
            'https://github.com/other/carr-system.git',
        ]):
            with self.subTest(origin=origin):
                root = self.checkout(f'foreign-{index}', origin)
                self.assertEqual(self.run_hook(self.turn(
                    call('Edit', file_path=str(root / 'hooks/check.py')),
                    assistant('I saved a rule.'))).get('decision'), 'block')

    def test_inherited_git_directory_does_not_override_edited_checkout(self):
        foreign = self.checkout('foreign', 'https://github.com/jbookout/other.git')
        self.hook_env = {'GIT_DIR': str(foreign / '.git')}
        self.assertNotEqual(self.run_hook(self.turn(
            call('Edit', file_path=str(self.root / 'hooks/check.py')),
            assistant('I saved a rule.'))).get('decision'), 'block')

    def test_external_checkout_preserves_enforcement_path_restrictions(self):
        root = self.checkout('external', 'ssh://git@github.com/jbookout/carr-system.git')
        for path in ['hooks/check.py', 'tools/check.py', 'tests/check.py',
                     '.github/workflows/check.yml', '.claude/settings.json',
                     'mcp-server/src/check.js', 'hooks/lesson.md', 'tools/memory/check.py',
                     'AGENTS.md', 'other/hooks/check.py']:
            with self.subTest(path=path):
                expected = 'block' if path in ['hooks/lesson.md', 'tools/memory/check.py',
                                             'AGENTS.md', 'other/hooks/check.py'] else None
                self.assertEqual(self.run_hook(self.turn(
                    call('Write', file_path=str(root / path)),
                    assistant('I saved a rule.'))).get('decision'), expected)

    def test_previous_turn_edit_does_not_count(self):
        records = [message('user', 'Old task'), call('Edit', file_path='hooks/old.py'),
                   *self.turn(assistant("I'll remember this."))]
        self.assertEqual(self.run_hook(records).get('decision'), 'block')

    def test_teach_only_blocks(self):
        self.assertEqual(self.run_hook(self.turn(call('mcp__carr__teach', rule='Remember'),
            assistant('The rule is now in place.'))).get('decision'), 'block')

    def test_document_writes_never_count(self):
        for path in ['hooks/rule.md', 'tools/memory/rule.py', 'CLAUDE.md', 'AGENTS.md',
                     '../hooks/check.py', 'other/hooks/check.py']:
            with self.subTest(path=path):
                self.assertEqual(self.run_hook(self.turn(call('Write', file_path=path),
                    assistant("It won't happen again."))).get('decision'), 'block')

    def test_markdown_and_memory_aliases_never_count(self):
        (self.root / 'hooks').mkdir()
        target = self.root / 'hooks/check.py'
        target.touch()
        (self.root / 'hooks/lesson.md').symlink_to(target)
        (self.root / 'hooks/memory').symlink_to(self.root / 'hooks', target_is_directory=True)
        for path in ['hooks/lesson.md', 'hooks/memory/check.py']:
            with self.subTest(path=path):
                self.assertEqual(self.run_hook(self.turn(call('Edit', file_path=path),
                    assistant('I saved a rule.'))).get('decision'), 'block')

    def test_final_reply_only(self):
        self.assertNotEqual(self.run_hook(self.turn(assistant("I'll remember."),
            call('Read', file_path='file'), assistant('I checked the file.'))).get('decision'), 'block')

    def test_queued_dispatch_requires_honest_reply(self):
        command = 'python3 tools/room-bridge/dispatch.py send builder ' + shlex.quote(
            'Build a hook that checks this rule.')
        honest = 'I added a rule. It is queued and not yet enforced.'
        self.assertNotEqual(self.run_hook(self.turn(call('Bash', command=command),
            assistant(honest))).get('decision'), 'block')
        self.assertEqual(self.run_hook(self.turn(call('Bash', command=command),
            assistant('I added a rule.'))).get('decision'), 'block')

    def test_fake_dispatch_does_not_count(self):
        for command in [
            'echo "python3 tools/room-bridge/dispatch.py send builder Build a hook"',
            'python3 tools/room-bridge/dispatch.py send builder "Review the hook"',
            'python3 other/dispatch.py send builder "Build a gate"',
        ]:
            with self.subTest(command=command):
                self.assertEqual(self.run_hook(self.turn(call('Bash', command=command),
                    assistant('I added a rule. It is queued and not yet enforced.'))
                    ).get('decision'), 'block')

    def test_wrapped_dispatch_segments_count(self):
        dispatch = ('python3 tools/room-bridge/dispatch.py send builder '
                    '"Build a hook that checks this rule." --family sol --timeout 14400')
        prefix = 'cd ~/carr-system && export CARR_HERMES_CODEX_TIMEOUT=14400 && '
        wait = ('F=/tmp/desk-ready; for i in ' + '\n'.join(map(str, range(1, 481)))
                + '; do grep -q ready "$F" && break; sleep 60; done; sleep 30; ')
        for command in [prefix + dispatch, wait + prefix + dispatch,
                        'export CARR_HERMES_CODEX_TIMEOUT=14400;\n' + dispatch]:
            with self.subTest(command=command[:100]):
                self.assertNotEqual(self.run_hook(self.turn(call('Bash', command=command),
                    assistant('I added a rule. It is queued and not yet enforced.')))
                    .get('decision'), 'block')

    def test_wrapped_dispatch_still_requires_build_brief(self):
        for brief in ['Review the hook.', 'Build a report.', 'Explain the CI check.']:
            command = ('cd ~/carr-system && export CARR_HERMES_CODEX_TIMEOUT=14400 && '
                       'python3 tools/room-bridge/dispatch.py send builder ' + shlex.quote(brief))
            with self.subTest(brief=brief):
                self.assertEqual(self.run_hook(self.turn(call('Bash', command=command),
                    assistant('I added a rule. It is queued and not yet enforced.')))
                    .get('decision'), 'block')

    def test_honest_uncheckable_rule_passes(self):
        self.assertNotEqual(self.run_hook(self.turn(assistant(
            "I'll remember this preference. The rule is not enforced because tone "
            'depends on human judgment and cannot be checked mechanically.'))
            ).get('decision'), 'block')

    def test_honest_reason_can_use_plain_language(self):
        for reason in ['Not enforced: this is subjective and requires human judgment.',
                       'The rule is not enforced because tone requires human judgment.',
                       "The rule is not enforced because I can't mechanically check tone."]:
            with self.subTest(reason=reason):
                self.assertNotEqual(self.run_hook(self.turn(assistant(
                    "I'll remember. " + reason))).get('decision'), 'block')

    def test_unexplained_not_enforced_still_blocks(self):
        self.assertEqual(self.run_hook(self.turn(assistant(
            "I'll remember. The rule is not enforced."))).get('decision'), 'block')

    def test_quotes_code_and_user_echoes_are_ignored(self):
        for text in ['> I saved a rule.\nI checked the report.',
                     'The user said "I saved a rule."',
                     "The example says ‘I’ll remember’. I checked it.",
                     '```text\nI saved a rule.\n```\nI checked it.',
                     '~~~\nI saved a rule.\n~~~',
                     '`I saved a rule.` is an example.',
                     '    I saved a rule.\n\nI checked it.']:
            with self.subTest(text=text):
                self.assertNotEqual(self.run_hook(self.turn(assistant(text))).get('decision'), 'block')
        self.assertNotEqual(self.run_hook([
            message('user', "You said I'll remember this."),
            assistant("I'll remember this.")]).get('decision'), 'block')

    def test_summary_is_ignored_and_does_not_supply_evidence(self):
        self.assertNotEqual(self.run_hook([
            message('user', 'Continue'),
            assistant('I saved a rule.') | {'isCompactSummary': True}
        ]).get('decision'), 'block')
        self.assertNotEqual(self.run_hook(self.turn(assistant(
            '<summary>I saved a rule.</summary>\nI checked the file.'))).get('decision'), 'block')
        self.assertNotEqual(self.run_hook([
            message('user', 'This session is being continued from a previous conversation. '
                    'Summary: I saved a rule.', isCompactSummary=True),
            assistant('I checked the file.')]).get('decision'), 'block')

    def test_tool_result_is_not_a_human_turn(self):
        self.assertNotEqual(self.run_hook(self.turn(call('Edit', file_path='hooks/check.py'),
            message('user', [{'type': 'tool_result', 'tool_use_id': 'tool1', 'content': 'OK'}]),
            assistant('I added a lesson.'))).get('decision'), 'block')

    def test_claim_phrase_variants(self):
        for text in ['I saved a lesson.', 'Added a rule.', 'Created a lesson.',
                     'The rule is saved.', 'The lesson was added.',
                     "I'll remember.", 'From now on I will check.',
                     'The rule is now in place.', "It won't happen again."]:
            with self.subTest(text=text):
                self.assertEqual(self.run_hook(self.turn(assistant(text))).get('decision'), 'block')

    def test_conditional_dispatch_segment_counts(self):
        command = 'false && python3 tools/room-bridge/dispatch.py send builder "Build a gate"'
        self.assertNotEqual(self.run_hook(self.turn(call('Bash', command=command),
            assistant('I saved a rule. It is queued and not yet enforced.')))
            .get('decision'), 'block')

    def test_repeat_stop_allows_and_logs_even_after_reply_changes(self):
        records = self.turn(assistant("I'll remember this."))
        self.assertEqual(self.run_hook(records, session_id='retry').get('decision'), 'block')
        records.append(assistant('The rule is now in place.'))
        self.assertNotEqual(self.run_hook(records, session_id='retry', stop_hook_active=True
                                         ).get('decision'), 'block')
        log = (self.root / 'out/rule-claim-gate.jsonl').read_text()
        self.assertIn('repeat_stop_allowed', log)
        records += [message('user', 'Next correction', uuid='human2'), assistant("I'll remember.")]
        self.assertEqual(self.run_hook(records, session_id='retry', stop_hook_active=True
                                      ).get('decision'), 'block')

    def test_parse_error_fails_open_and_logs(self):
        self.transcript.write_text('{broken json\n')
        result = subprocess.run([sys.executable, str(HOOK)], text=True, capture_output=True,
            cwd=self.root, env=ENV, input=json.dumps({'cwd': str(self.root), 'hook_event_name': 'Stop',
                                             'transcript_path': str(self.transcript)}))
        self.assertEqual(result.returncode, 0)
        self.assertNotIn('"block"', result.stdout)
        self.assertIn('parse_error', result.stderr)
        self.assertIn('parse_error', (self.root / 'out/rule-claim-gate.jsonl').read_text())

    def test_bad_stdin_fails_open_with_log_line(self):
        result = subprocess.run([sys.executable, str(HOOK)], input='{', text=True,
                                capture_output=True, cwd=self.root, env=ENV)
        self.assertEqual(result.returncode, 0)
        self.assertIn('parse_error', result.stderr)

    def test_registered_in_project_and_managed_stop_hooks(self):
        for path in ['.claude/settings.json', 'ops/config/hooks.json']:
            with self.subTest(path=path):
                document = json.loads((REPO / path).read_text())
                groups = document.get('hooks', document)['Stop']
                commands = [hook['command'] for group in groups for hook in group['hooks']]
                self.assertEqual(sum('rule-claim-gate.py' in c for c in commands), 1)

    def test_string_assistant_content_still_blocks(self):
        self.assertEqual(self.run_hook(self.turn(message('assistant', "I'll remember.")))
                         .get('decision'), 'block')

    def test_corrected_retry_is_logged(self):
        records = self.turn(assistant("I'll remember."))
        self.assertEqual(self.run_hook(records, session_id='fixed').get('decision'), 'block')
        self.assertNotEqual(self.run_hook(records + [assistant('I checked the file.')],
            session_id='fixed').get('decision'), 'block')
        self.assertIn('repeat_stop_allowed', (self.root / 'out/rule-claim-gate.jsonl').read_text())

    def test_hook_notification_is_not_a_human_turn(self):
        self.assertNotEqual(self.run_hook(self.turn(call('Write', file_path='hooks/check.py'),
            message('user', '<system-reminder>Check the rules.</system-reminder>'),
            assistant('I saved a rule.'))).get('decision'), 'block')

    def test_negated_conditional_and_quoted_claims_are_not_promises(self):
        for text in ['I have not saved a rule or lesson.',
                     'If I saved a rule, it would need enforcement.',
                     'Have I saved a rule?',
                     '"I saved a rule\nfor this task." I checked the claim.',
                     "The example is 'I'll remember this.' I checked it."]:
            with self.subTest(text=text):
                self.assertNotEqual(self.run_hook(self.turn(assistant(text)))
                                    .get('decision'), 'block')


if __name__ == '__main__':
    unittest.main()
