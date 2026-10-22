"""Verify skill evidence through recorded session files."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import warnings
import shlex

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('skill_audit', ROOT / 'tools/skill_audit.py')
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class SessionAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'rollout-fixture.jsonl'

    def write(self, rows):
        self.path.write_text(''.join(json.dumps(row) + '\n' for row in rows))

    def codex(self, command, code=0, ident='c1', output='ok'):
        return [
            dict(timestamp='2026-10-22T12:00:00Z', type='response_item',
                 payload=dict(type='function_call', name='exec_command', call_id=ident,
                              arguments=json.dumps({'cmd': command}))),
            dict(timestamp='2026-10-22T12:00:01Z', type='response_item',
                 payload=dict(type='function_call_output', call_id=ident,
                              output=json.dumps({'exit_code': code, 'output': output})))
        ]

    def test_codex_function_call_normalizes_command_result_and_metadata(self):
        self.write([dict(type='session_meta', payload=dict(id='session-1', cwd='/repo')),
                    dict(timestamp='2026-10-22T11:59:00Z', type='response_item',
                         payload=dict(type='message', role='user', content=[
                             dict(type='input_text', text='Sizing: medium. Build widget.')]))]
                   + self.codex('cat /skills/poteto-mode/SKILL.md'))
        session = audit.load_session(self.path)
        self.assertEqual(session['id'], 'session-1')
        self.assertEqual(session['cwd'], '/repo')
        self.assertIn('Build widget', session['brief'])
        self.assertEqual(session['events'][-1]['command'], 'cat /skills/poteto-mode/SKILL.md')
        self.assertEqual(session['events'][-1]['exit_code'], 0)
        self.assertEqual(session['events'][-1]['output'], 'ok')
        self.assertEqual(session['events'][0]['role'], 'user')

    def test_claude_read_and_failed_bash_are_recorded(self):
        self.write([
            dict(timestamp='a', type='assistant', message=dict(content=[
                dict(type='tool_use', id='r1', name='Read',
                     input={'file_path': '/skills/poteto-mode/SKILL.md'})])),
            dict(timestamp='b', type='user', message=dict(content=[
                dict(type='tool_result', tool_use_id='r1', content='skill text')])),
            dict(timestamp='c', type='assistant', message=dict(content=[
                dict(type='tool_use', id='b1', name='Bash', input={'command': 'false'})])),
            dict(timestamp='d', type='user', message=dict(content=[
                dict(type='tool_result', tool_use_id='b1', content='Exit code 1', is_error=True)]))])
        events = audit.load_session(self.path)['events']
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]['exit_code'], 0)
        self.assertEqual(events[1]['exit_code'], 1)
        result = audit.audit_events(events, diff='', brief='Sizing: small')
        self.assertEqual(result['checks']['poteto_mode']['verdict'], 'VERIFIED')

    def event(self, command, code=0, tool='Bash'):
        return dict(timestamp='2026-10-22T12:00:00Z', command=command, exit_code=code,
                    output='ok', kind='tool', tool=tool)

    def skill_events(self):
        return [self.event('cat /pstack/poteto-mode/SKILL.md'),
                self.event('cat /pstack/principle-prove-it-works/SKILL.md'),
                self.event('cat /mattpocock-skills/skills/engineering/tdd/SKILL.md'),
                self.event('cat /mattpocock-skills/skills/engineering/grill-with-docs/SKILL.md'),
                self.event('cat .agents/skills/codebase-design/SKILL.md')]

    def source_diff(self):
        return 'diff --git a/src/widget.py b/src/widget.py\n--- a/src/widget.py\n+++ b/src/widget.py\n@@ -1 +1 @@\n-old\n+new\n'

    def test_successful_skill_opens_before_edit_are_verified(self):
        events = self.skill_events() + [self.event('sed -i s/old/new/ src/widget.py')]
        checks = audit.audit_events(events, diff=self.source_diff(), brief='Sizing: medium')['checks']
        for name in ('poteto_mode', 'prove_it_works', 'tdd', 'grill_with_docs', 'codebase_design'):
            self.assertEqual(checks[name]['verdict'], 'VERIFIED', name)

    def test_failed_echoed_and_late_skill_opens_do_not_count(self):
        for open_event in (self.event('cat /pstack/poteto-mode/SKILL.md', 1),
                           self.event('echo cat /pstack/poteto-mode/SKILL.md'),
                           self.event('python3 -c "print(\'cat /pstack/poteto-mode/SKILL.md\')"')):
            checks = audit.audit_events([open_event], diff='', brief='Sizing: small')['checks']
            self.assertEqual(checks['poteto_mode']['verdict'], 'MISSING')
        events = [self.event('cat > notes.txt <<EOF\ntext\nEOF')] + self.skill_events()
        checks = audit.audit_events(events, diff=self.source_diff(), brief='Sizing: medium')['checks']
        self.assertEqual(checks['poteto_mode']['verdict'], 'MISSING')
        self.assertEqual(checks['prove_it_works']['verdict'], 'MISSING')

    def test_tdd_is_pocock_and_before_source_but_can_follow_test_edit(self):
        events = self.skill_events()
        events.insert(0, self.event('cat > tests/test_widget.py <<EOF\npass\nEOF'))
        checks = audit.audit_events(events, diff=self.source_diff(), brief='Sizing: tiny')['checks']
        self.assertEqual(checks['tdd']['verdict'], 'VERIFIED')
        events[3] = self.event('cat /pstack/tdd/SKILL.md')
        checks = audit.audit_events(events, diff=self.source_diff(), brief='Sizing: tiny')['checks']
        self.assertEqual(checks['tdd']['verdict'], 'MISSING')
        events = [self.event('git commit -F /tmp/message')] + self.skill_events()
        checks = audit.audit_events(events, diff=self.source_diff(), brief='Sizing: small')['checks']
        self.assertEqual(checks['poteto_mode']['verdict'], 'MISSING')

    def test_unknown_sizing_fails_and_docs_or_deletion_exempt_tdd(self):
        checks = audit.audit_events(self.skill_events(), diff='', brief='Unspecified work')['checks']
        self.assertEqual(checks['grill_with_docs']['verdict'], 'MISSING')
        for diff in ('diff --git a/README.md b/README.md\n+++ b/README.md\n+hello\n',
                     'diff --git a/src/old.py b/src/old.py\ndeleted file mode 100644\n--- a/src/old.py\n+++ /dev/null\n-old\n'):
            checks = audit.audit_events(self.skill_events(), diff=diff, brief='Sizing: small')['checks']
            self.assertEqual(checks['tdd']['verdict'], 'NOT_APPLICABLE')
            self.assertEqual(checks['tdd_behavior']['verdict'], 'NOT_APPLICABLE')
            self.assertEqual(checks['codebase_design']['verdict'], 'NOT_APPLICABLE')

    def test_red_edit_green_same_suite_and_post_edit_proof(self):
        events = self.skill_events() + [self.event('python3 -m pytest tests/test_widget.py', 1),
            self.event('sed -i s/old/new/ src/widget.py'),
            self.event('python3 -m pytest tests/test_widget.py', 0)]
        result = audit.audit_events(events, diff=self.source_diff(), brief='Sizing: medium')
        self.assertEqual(result['verdict'], 'VERIFIED')
        self.assertEqual(result['checks']['tdd_behavior']['verdict'], 'VERIFIED')
        self.assertEqual(result['checks']['prove_behavior']['verdict'], 'VERIFIED')
        for altered in (events[:5] + events[6:],
                        events[:-1] + [self.event('python3 -m pytest tests/test_other.py')],
                        events + [self.event('sed -i s/new/newer/ src/widget.py')]):
            result = audit.audit_events(altered, diff=self.source_diff(), brief='Sizing: medium')
            self.assertEqual(result['verdict'], 'MISSING')
        self.assertEqual(audit.audit_events(events[:5]+events[6:], diff=self.source_diff(),
                                           brief='Sizing: medium')['checks']['tdd_behavior']['verdict'], 'MISSING')

    def test_test_fixture_contents_are_not_edits_tests_or_skill_opens(self):
        command = "cat > tests/test_fixture.py <<'PY'\ncat /pstack/poteto-mode/SKILL.md\npython3 -m pytest tests/test_widget.py\nsed -i s/a/b/ src/widget.py\nPY"
        checks = audit.audit_events([self.event(command)], diff=self.source_diff(), brief='Sizing: small')['checks']
        self.assertEqual(checks['poteto_mode']['verdict'], 'MISSING')
        self.assertEqual(checks['tdd_behavior']['verdict'], 'MISSING')
        self.assertEqual(checks['prove_behavior']['verdict'], 'MISSING')

    def test_deletion_still_requires_test_and_docs_have_no_proof_requirement(self):
        deleted = 'diff --git a/src/old.py b/src/old.py\ndeleted file mode 100644\n--- a/src/old.py\n+++ /dev/null\n-old\n'
        events = self.skill_events() + [self.event('rm src/old.py'), self.event('git status')]
        self.assertEqual(audit.audit_events(events, diff=deleted, brief='Sizing: small')['checks']['prove_behavior']['verdict'], 'MISSING')
        events += [self.event('python3 -m pytest')]
        self.assertEqual(audit.audit_events(events, diff=deleted, brief='Sizing: small')['checks']['prove_behavior']['verdict'], 'VERIFIED')
        checks = audit.audit_events(self.skill_events(), diff='diff --git a/README.md b/README.md\n+++ b/README.md\n+hi', brief='Sizing: small')['checks']
        self.assertEqual(checks['prove_behavior']['verdict'], 'NOT_APPLICABLE')

    def test_redaction_applies_to_evidence_commands(self):
        events = self.skill_events() + [self.event('API_KEY=secret123 python3 -m pytest tests/test_widget.py', 1),
            self.event('sed -i s/old/new/ src/widget.py'),
            self.event('API_KEY=secret123 python3 -m pytest tests/test_widget.py', 0)]
        result = audit.audit_events(events, diff=self.source_diff(), brief='Sizing: medium')
        self.assertNotIn('secret123', json.dumps(result))
        self.assertNotIn('secret123', audit.redact('Authorization: Bearer secret123'))

    def test_raw_exec_js_decodes_only_real_calls_and_nested_results(self):
        command = 'cat /pstack/poteto-mode/SKILL.md'
        js = 'const r = await tools.exec_command({cmd: ' + json.dumps(command) + ', max_output_tokens: 100}); text(r);'
        self.write([dict(type='response_item', timestamp='a', payload=dict(
            type='custom_tool_call', name='exec', call_id='x', input=js)),
            dict(type='response_item', timestamp='b', payload=dict(
                type='custom_tool_call_output', call_id='x', output=json.dumps([
                    dict(type='text', text=json.dumps({'exit_code': 0, 'output': 'skill text'}))])))])
        events = audit.load_session(self.path)['events']
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['command'], command)
        self.assertEqual(events[0]['exit_code'], 0)
        self.assertEqual(audit.audit_events(events, diff='', brief='Sizing: small')['checks']['poteto_mode']['verdict'], 'VERIFIED')
        quoted = 'text(' + json.dumps('await tools.exec_command({cmd:"cat /pstack/poteto-mode/SKILL.md"})') + ');'
        self.write([dict(type='response_item', timestamp='a', payload=dict(
            type='custom_tool_call', name='exec', call_id='x', input=quoted))])
        self.assertEqual(audit.load_session(self.path)['events'], [])

    def test_multiple_js_commands_cannot_borrow_one_exit_status(self):
        js = 'await tools.exec_command({cmd:"cat /pstack/poteto-mode/SKILL.md"}); await tools.exec_command({cmd:"true"});'
        self.write([dict(type='response_item', timestamp='a', payload=dict(
            type='custom_tool_call', name='exec', call_id='x', input=js)),
            dict(type='response_item', timestamp='b', payload=dict(
                type='custom_tool_call_output', call_id='x', output=json.dumps({'exit_code': 0, 'output': 'ok'})))])
        events = audit.load_session(self.path)['events']
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e['exit_code'] is None for e in events))

    def test_deferred_exec_tracks_final_result_and_window_excludes_outside_open(self):
        rows = self.codex('python3 -m pytest tests/test_widget.py', None, output='running')
        rows[1]['payload']['output'] = json.dumps({'session_id': 42, 'output': 'started'})
        rows += [dict(type='response_item', timestamp='2026-10-22T12:00:02Z', payload=dict(
            type='function_call', name='write_stdin', call_id='poll', arguments=json.dumps({'session_id': 42}))),
            dict(type='response_item', timestamp='2026-10-22T12:00:03Z', payload=dict(
                type='function_call_output', call_id='poll', output=json.dumps({'exit_code': 1, 'output': 'FAILED'})))]
        self.write(rows)
        events = audit.load_session(self.path)['events']
        self.assertEqual(events[0]['exit_code'], 1)
        self.assertIn('FAILED', events[0]['output'])
        self.assertIsNone(audit.load_session(self.path, end='2026-10-22T12:00:01Z')['events'][0]['exit_code'])
        self.assertEqual(audit.load_session(self.path, start='2026-10-22T12:00:02Z')['events'][0]['command'], '')

    def test_skill_tool_pocock_namespace_and_apply_patch_edit(self):
        events = self.skill_events()
        events[2] = self.event('Skill mattpocock-skills:tdd', tool='Skill')
        events += [self.event('python3 tests/test_widget.py', 1),
            self.event('*** Begin Patch\n*** Update File: src/widget.py\n@@\n-old\n+new\n*** End Patch', tool='apply_patch'),
            self.event('python3 tests/test_widget.py', 0)]
        self.assertEqual(audit.audit_events(events, diff=self.source_diff(), brief='Sizing: medium')['verdict'], 'VERIFIED')

    def test_source_path_writes_and_fixture_data_are_distinguished(self):
        command = "python3 - <<'PY'\nfrom pathlib import Path\np = Path('src/widget.py')\np.write_text('new')\nPY"
        events = self.skill_events() + [self.event('python3 tests/test_widget.py', 1),
            self.event(command), self.event('python3 tests/test_widget.py')]
        self.assertEqual(audit.audit_events(events, diff=self.source_diff(), brief='Sizing: medium')['verdict'], 'VERIFIED')
        embedded = self.event("python3 -c \"print('Path(\\\"src/widget.py\\\").write_text(\\\"new\\\")')\"")
        self.assertFalse(audit.is_source_edit(embedded))

    def test_async_skill_open_and_test_completion_must_precede_edit(self):
        events = self.skill_events()
        for i, event in enumerate(events):
            event['order'] = i
            event['completed_order'] = i
        events[0]['completed_order'] = 10
        events += [dict(self.event('sed -i s/old/new/ src/widget.py'), order=5),
                   dict(self.event('python3 tests/test_widget.py'), order=6)]
        self.assertEqual(audit.audit_events(events, diff=self.source_diff(), brief='Sizing: medium')['checks']['poteto_mode']['verdict'], 'MISSING')
        events = self.skill_events() + [dict(self.event('python3 tests/test_widget.py', 1), order=5, completed_order=9),
            dict(self.event('sed -i s/old/new/ src/widget.py'), order=6),
            dict(self.event('python3 tests/test_widget.py'), order=7)]
        self.assertEqual(audit.audit_events(events, diff=self.source_diff(), brief='Sizing: medium')['checks']['tdd_behavior']['verdict'], 'MISSING')

    def test_last_edit_must_finish_before_proof_starts(self):
        events = self.skill_events() + [dict(self.event('sed -i s/old/new/ src/widget.py'), order=5, completed_order=10),
            dict(self.event('python3 tests/test_widget.py'), order=6, completed_order=7)]
        self.assertEqual(audit.audit_events(events, diff=self.source_diff(), brief='Sizing: medium')['checks']['prove_behavior']['verdict'], 'MISSING')

    def test_failed_edit_after_green_does_not_leave_verified_proof(self):
        events = self.skill_events() + [self.event('sed -i s/old/new/ src/widget.py'),
            self.event('python3 tests/test_widget.py'),
            self.event('sed -i s/new/newer/ src/widget.py', 1)]
        self.assertEqual(audit.audit_events(events, diff=self.source_diff(), brief='Sizing: medium')['checks']['prove_behavior']['verdict'], 'MISSING')

    def test_large_tool_output_is_bounded_and_git_sha_survives(self):
        output = 'x' * 50000 + '\n' + 'a' * 40 + '\n' + 'y' * 50000
        self.write(self.codex('git log --format=%H', output=output))
        event = audit.load_session(self.path)['events'][0]
        self.assertLess(len(event['output']), 17000)
        self.assertIn('a' * 40, event['output'])
        self.write(self.codex('python3 tests/test_widget.py', output=output))
        self.assertLess(len(audit.load_session(self.path)['events'][0]['output']), 17000)

    def test_claude_session_id_is_retained(self):
        self.write([dict(type='user', sessionId='claude-session', cwd='/repo', timestamp='a',
                         message=dict(content='Sizing: small'))])
        self.assertEqual(audit.load_session(self.path)['id'], 'claude-session')

    def test_native_exec_wrapper_unwraps_one_json_result(self):
        rows = self.codex('git rev-parse HEAD')
        rows[1]['payload']['output'] = [
            {'type': 'input_text', 'text': 'Script completed\nWall time 0.5 seconds\nOutput:\n'},
            {'type': 'input_text', 'text': json.dumps({'exit_code': 0, 'output': 'b' * 40})}]
        self.write(rows)
        event = audit.load_session(self.path)['events'][0]
        self.assertEqual(event['exit_code'], 0)
        self.assertEqual(event['output'], 'b' * 40)

    def test_stdout_only_cannot_claim_exit_status_from_embedded_fixture(self):
        rows = self.codex('cat /pstack/poteto-mode/SKILL.md')
        rows[1]['payload']['output'] = [
            {'type': 'input_text', 'text': 'Script completed\nWall time 0.5 seconds\nOutput:\n'},
            {'type': 'input_text', 'text': 'Example fixture has exit_code: 0 and is_error: false.'}]
        self.write(rows)
        self.assertIsNone(audit.load_session(self.path)['events'][0]['exit_code'])

    def test_open_commands_in_shell_segments_and_head_sed_count(self):
        for command in ('head -100 /pstack/poteto-mode/SKILL.md',
                        "sed -n '1,200p' /pstack/poteto-mode/SKILL.md",
                        'pwd; cat AGENTS.md; cat /pstack/poteto-mode/SKILL.md',
                        'pwd\ncat /pstack/poteto-mode/SKILL.md'):
            checks = audit.audit_events([self.event(command)], diff='', brief='Sizing: small')['checks']
            self.assertEqual(checks['poteto_mode']['verdict'], 'VERIFIED', command)

    def test_dynamic_js_is_unknown_without_crashing(self):
        self.write([dict(type='response_item', timestamp='a', payload=dict(
            type='custom_tool_call', name='exec', call_id='x',
            input='await tools.exec_command({cmd: `cat ${path}/SKILL.md`, other: foo(`x${y}`)});'))])
        events = audit.load_session(self.path)['events']
        self.assertFalse(any(e['exit_code'] == 0 for e in events))

    def test_proof_targets_changed_tests_or_broad_suite(self):
        diff = self.source_diff() + 'diff --git a/tests/test_widget.py b/tests/test_widget.py\n+++ b/tests/test_widget.py\n+assert True\n'
        prefix = self.skill_events() + [self.event('sed -i s/old/new/ src/widget.py')]
        for command in ('python3 tests/test_other.py', 'python3 -m pytest tests/test_other.py'):
            checks = audit.audit_events(prefix+[self.event(command)], diff=diff, brief='Sizing: medium')['checks']
            self.assertEqual(checks['prove_behavior']['verdict'], 'MISSING', command)
        for command in ('python3 tests/test_widget.py', 'python3 -m pytest', './ops/ci.sh --strict',
                        'python3 src/widget.py'):
            checks = audit.audit_events(prefix+[self.event(command)], diff=diff, brief='Sizing: medium')['checks']
            self.assertEqual(checks['prove_behavior']['verdict'], 'VERIFIED', command)

    def test_node_tests_and_python_selftests_are_behavior_commands(self):
        for command in ('node --test tests/widget.test.js', 'python3 ops/widget-selftest.py'):
            events = self.skill_events() + [self.event(command, 1),
                self.event('sed -i s/old/new/ src/widget.py'), self.event(command)]
            checks = audit.audit_events(events, diff=self.source_diff(), brief='Sizing: medium')['checks']
            self.assertEqual(checks['tdd_behavior']['verdict'], 'VERIFIED', command)
            self.assertEqual(checks['prove_behavior']['verdict'], 'VERIFIED', command)

    def test_redaction_covers_urls_pats_jwt_and_spaced_quoted_secrets(self):
        examples = ['postgres://fixture:password@example.org/db', 'https://fixture:password@example.org/',  # ci-secret-scan: allow - documented placeholder password
                    'github_pat_' + 'a'*50, 'eyJabc1234567890.eyJabc1234567890.abcdef1234567890',
                    'PASSWORD="long secret phrase"']
        for example in examples:
            self.assertNotEqual(audit.redact(example), example)
            self.assertNotIn('fixture:password@', audit.redact(example))
            self.assertNotIn('secret phrase', audit.redact(example))

    def test_unrelated_source_edit_cannot_supply_tdd_for_diff(self):
        events = self.skill_events() + [self.event('python3 tests/test_widget.py', 1),
            self.event('sed -i s/old/new/ src/other.py'), self.event('python3 tests/test_widget.py')]
        self.assertEqual(audit.audit_events(events, diff=self.source_diff(), brief='Sizing: medium')['checks']['tdd_behavior']['verdict'], 'MISSING')
        events[6] = self.event('sed -i s/old/new/ /tmp/repo/src/widget.py')
        self.assertEqual(audit.audit_events(events, diff=self.source_diff(), brief='Sizing: medium')['checks']['tdd_behavior']['verdict'], 'VERIFIED')

    def test_test_status_cannot_be_borrowed_from_following_shell_command(self):
        events = self.skill_events() + [self.event('python3 -m pytest tests/test_widget.py && false', 1),
            self.event('sed -i s/old/new/ src/widget.py'), self.event('python3 -m pytest tests/test_widget.py')]
        self.assertEqual(audit.audit_events(events, diff=self.source_diff(), brief='Sizing: medium')['checks']['tdd_behavior']['verdict'], 'MISSING')

    def test_basic_authorization_is_redacted(self):
        self.assertNotIn('dXNlcjpwYXNz', audit.redact('Authorization: Basic dXNlcjpwYXNz'))

    def test_compound_skill_reads_do_not_hide_missing_or_skipped_opens(self):
        for event in (dict(self.event('cat /pstack/poteto-mode/SKILL.md; true'),
                           output='cat: /pstack/poteto-mode/SKILL.md: No such file or directory'),
                      self.event('false && cat /pstack/poteto-mode/SKILL.md; true'),
                      self.event('test -f missing && cat /pstack/poteto-mode/SKILL.md; true')):
            check = audit.audit_events([event], diff='', brief='Sizing: small')['checks']['poteto_mode']
            self.assertEqual(check['verdict'], 'MISSING', event['command'])

    def test_open_writes_and_shell_file_replacements_invalidate_proof(self):
        prefix = self.skill_events() + [self.event('sed -i s/old/new/ src/widget.py'),
                                      self.event('python3 tests/test_widget.py')]
        commands = ["python3 -c \"open('src/widget.py', 'w').write('new')\"",
                    "python3 -c \"with open('src/widget.py', mode='a') as f: f.write('new')\"",
                    'printf new | tee src/widget.py', 'cp /tmp/new.py src/widget.py',
                    'mv /tmp/new.py src/widget.py']
        for command in commands:
            event = self.event(command)
            self.assertTrue(audit.is_source_edit(event), command)
            result = audit.audit_events(prefix+[event], diff=self.source_diff(), brief='Sizing: medium')
            self.assertEqual(result['checks']['prove_behavior']['verdict'], 'MISSING', command)
        checks = audit.audit_events([self.event(commands[0])]+self.skill_events(),
                                    diff=self.source_diff(), brief='Sizing: medium')['checks']
        self.assertEqual(checks['poteto_mode']['verdict'], 'MISSING')
        self.assertFalse(audit.is_source_edit(self.event("python3 -c \"print(\\\"open('src/widget.py','w')\\\")\"")))

    def test_edit_evidence_omits_payload_for_patch_heredoc_and_inline_python(self):
        marker = 'private-fixture-body'
        edits = [self.event('*** Begin Patch\n*** Update File: src/widget.py\n@@\n-old\n+'+marker+'\n*** End Patch', tool='apply_patch'),
                 self.event("cat > src/widget.py <<'PY'\n"+marker+'\nPY'),
                 self.event("python3 -c \"open('src/widget.py','w').write('"+marker+"')\""),
                 self.event("sed -i s/old/"+marker+"/ src/widget.py")]
        for edit in edits:
            result = audit.audit_events(self.skill_events()+[self.event('python3 tests/test_widget.py',1),
                edit,self.event('python3 tests/test_widget.py')], diff=self.source_diff(), brief='Sizing: medium')
            self.assertEqual(result['checks']['tdd_behavior']['verdict'], 'VERIFIED')
            self.assertNotIn(marker, json.dumps(result))
            self.assertIn('src/widget.py', json.dumps(result))

    def test_tdd_edit_must_complete_before_green_starts(self):
        events = self.skill_events() + [dict(self.event('python3 tests/test_widget.py',1), order=5, completed_order=6),
            dict(self.event('sed -i s/old/new/ src/widget.py'), order=7, completed_order=10),
            dict(self.event('python3 tests/test_widget.py'), order=8, completed_order=9)]
        self.assertEqual(audit.audit_events(events, diff=self.source_diff(), brief='Sizing: medium')['checks']['tdd_behavior']['verdict'], 'MISSING')

    def test_parsing_transcript_data_does_not_emit_source_warnings(self):
        command = 'python3 -c ' + shlex.quote(r"pattern = '\z'")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            audit.audit_events([self.event(command)], diff='', brief='Sizing: small')
        self.assertEqual(caught, [])

    def test_skill_menu_is_not_a_tool_open(self):
        self.write([dict(timestamp='2026-10-22T12:00:00Z', type='response_item',
                         payload=dict(type='message', role='developer', content=[
                             dict(type='input_text', text='Skills: /skills/poteto-mode/SKILL.md')]))])
        session = audit.load_session(self.path)
        result = audit.audit_events(session['events'], diff='', brief='Sizing: small')
        self.assertEqual(result['checks']['poteto_mode']['verdict'], 'MISSING')
        self.assertEqual(result['verdict'], 'MISSING')


if __name__ == '__main__':
    unittest.main()
