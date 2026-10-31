#!/usr/bin/env python3
"""Exercise the Stop hook through its JSON stdin and transcript interface."""
import json
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

    def checkout(self, name, origin='https://github.com/DoctorCRE/carr-system.git'):
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
                        'git@github.com:DoctorCRE/carr-system.git'], check=True, env=ENV)
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
            'https://example.com/DoctorCRE/carr-system.git',
            'https://github.com/DoctorCRE/carr-system-fake.git',
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
        root = self.checkout('external', 'ssh://git@github.com/DoctorCRE/carr-system.git')
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

    def test_infrastructure_rules_do_not_require_guidance_enforcement(self):
        for text in [
            'I added a Cloudflare firewall rule that blocks the control room…',
            'I added a firewall rule.',
            'The WAF rule is now in place.',
            'I created a rate-limit rule.',
            'I saved a rate limit rule.',
            'I added a ruleset rule.',
            'I created a route rule.',
            'The DNS rule has been created.',
            'I added a Cloudflare ruleset.',
            'I added a rule to the Cloudflare firewall.',
            'I created a rule in the WAF.',
            'I added a rule to the Cloudflare firewall and a DNS rule.',
        ]:
            with self.subTest(text=text):
                self.assertNotEqual(self.run_hook(self.turn(assistant(text)))
                                    .get('decision'), 'block')

    def test_reworded_infrastructure_guidance_claims_block(self):
        for text in [
            'I added a DNS rule: check Cloudflare first before asking Joe.',
            'I added a route rule: ask Joe before merging anything.',
            'I added a DNS rule so that I always check Cloudflare first.',
            'I saved a rate limit rule: from now on I check the quota before running jobs.',
            'I added a route rule so that we check screenshots.',
            'I added a route rule and we ask Joe before merging.',
            'I added a route rule, ask Joe before merging.',
            'I added a DNS rule check Cloudflare first.',
            'I added a route rule that I follow before merging.',
        ]:
            with self.subTest(text=text):
                self.assertEqual(self.run_hook(self.turn(assistant(text)))
                                 .get('decision'), 'block')

    def test_infrastructure_rules_do_not_exempt_guidance_claims(self):
        for text in [
            'I saved a rule on DNS questions: always check Cloudflare before asking Joe.',
            'I added a rule for route reviews: never merge without a screenshot.',
            'I created a rule for rate limit incidents: always page Joe first.',
            'I saved a DNS rule in memory: always check Cloudflare before asking Joe.',
            'I added a route rule so I always run the tests before merging.',
            'I added a rule in the ruleset of standing instructions: always ask first.',
            'I added a guidance and firewall rule.',
            'I saved a DNS rule: always check Cloudflare first.',
            'I saved a DNS rule in `MEMORY.md`.',
            'I added a firewall rule and a durable rule.',
            'I added a WAF rule and saved a lesson.',
            'I created a DNS rule and added a taught rule.',
            "The rate-limit rule is now in place and I'll remember this.",
            'I added a route rule. The rule is saved.',
            'I saved a lesson after creating the firewall rule.',
            'I added a guidance rule about firewall configuration.',
            'I added a rule to CLAUDE.md about WAF configuration.',
            'I added a rule to AGENTS.md about DNS configuration.',
            'I added a skill rule about rate-limit configuration.',
            'I created a doctrine rule about route configuration.',
            'I added a firewall rule to AGENTS.md.',
            'I added a firewall rule to `AGENTS.md`.',
            'I added a WAF rule to `docs/CLAUDE.md`.',
            'I added a DNS rule to `.agents/skills/check/SKILL.md`.',
            'I added a WAF rule to CLAUDE.md.',
            'I added a doctrine firewall rule.',
            'I added a behavioural DNS rule.',
            'I added a taught WAF rule.',
            'I added a skill firewall rule.',
            'I added a rule to the Cloudflare firewall and a durable rule.',
        ]:
            with self.subTest(text=text):
                self.assertEqual(self.run_hook(self.turn(assistant(text)))
                                 .get('decision'), 'block')

    def test_email_rule_reports_do_not_require_local_enforcement_code(self):
        for text in [
            'I created an Outlook mail rule. I checked Gmail delivery.',
            'I added a Gmail rule to forward incoming mail.',
            'I saved an email rule.',
            'I created an inbox rule.',
            'I added a forwarding rule.',
            'The Outlook rule is saved.',
            'The Outlook mail rule is now in place.',
            'The Gmail rule has been created.',
        ]:
            with self.subTest(text=text):
                self.assertNotEqual(self.run_hook(self.turn(assistant(text)))
                                    .get('decision'), 'block')

    def test_email_rules_do_not_exempt_durable_claims(self):
        for text in [
            'I created a mail rule and a lesson.',
            'I added a mail rule and a durable rule.',
            'I added an Outlook mail rule and saved a lesson.',
            'I created a Gmail rule and added a durable rule.',
            "The Outlook rule is now in place and I'll remember this.",
            'I saved a lesson after creating the Outlook mail rule.',
            'I added an Outlook mail rule. The rule is saved.',
            'I added a rule to remember to check Outlook.',
            'The Gmail lesson is saved.',
            'I added an Outlook mail rule and the lesson is now in place.',
            "I created a mail rule. It won't happen again.",
        ]:
            with self.subTest(text=text):
                self.assertEqual(self.run_hook(self.turn(assistant(text)))
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

    def test_fires_once_per_stop_from_the_managed_hooks(self):
        managed = json.loads((REPO / 'ops/config/hooks.json').read_text())['Stop']
        self.assertEqual(sum('rule-claim-gate.py' in hook['command']
                             for group in managed for hook in group['hooks']), 1)
        # A second project-level entry made every Mac Stop run the gate twice.
        self.assertNotIn('rule-claim-gate.py', (REPO / '.claude/settings.json').read_text())

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
