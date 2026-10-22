"""Audit exact PR heads and render safe reports without live GitHub writes."""
import importlib.util
from pathlib import Path
import unittest
import json
import os
import subprocess
import tempfile
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
SPEC = importlib.util.spec_from_file_location('audit_cli', ROOT / 'tools/skill-audit.py')
assert SPEC is not None and SPEC.loader is not None
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


class PrAuditTests(unittest.TestCase):
    def window(self, command='git rev-parse HEAD', output=None, code=0):
        return dict(id='session-1', prs=['example/repo#12'], brief='Sizing: small',
                    events=[dict(kind='tool', timestamp='2026-10-22T12:00:00Z',
                                 command=command, output=output or 'a' * 40, exit_code=code)])

    def test_unattributed_pr_never_passes(self):
        report = cli.audit_pr('example/repo#12', {'head': {'sha': 'a' * 40, 'ref': 'feature'}}, '', [])
        self.assertEqual(report['verdict'], 'UNATTRIBUTED')
        self.assertNotEqual(report['verdict'], 'VERIFIED')

    def test_head_hash_in_echo_or_failed_command_is_not_binding(self):
        for window in [self.window(command='echo current head'), self.window(code=1),
                       self.window(output='b' * 40)]:
            with self.subTest(window=window):
                report = cli.audit_pr('example/repo#12', {'head': {'sha': 'a' * 40}}, '', [window])
                self.assertEqual(report['verdict'], 'UNATTRIBUTED')

    def test_exact_head_is_required_before_a_session_can_pass(self):
        with patch.object(cli, 'audit_events', return_value={'verdict': 'VERIFIED', 'checks': {}}, create=True):
            report = cli.audit_pr('example/repo#12', {'head': {'sha': 'a' * 40}}, '', [self.window()])
        self.assertEqual(report['verdict'], 'VERIFIED')
        self.assertEqual(report['checks']['head_binding']['verdict'], 'VERIFIED')

    def test_another_pr_cannot_lend_its_session(self):
        window = self.window()
        window['prs'] = ['other/repo#12']
        report = cli.audit_pr('example/repo#12', {'head': {'sha': 'a' * 40}}, '', [window])
        self.assertEqual(report['verdict'], 'UNATTRIBUTED')

    def test_html_escapes_evidence_and_does_not_publish_raw_outputs(self):
        report = dict(subject='example/repo#12', head='a' * 40, session='fixture-session', verdict='MISSING',
                      checks={'tdd': dict(verdict='MISSING', reason='<script>alert(1)</script>',
                                          evidence=[dict(timestamp='2026-10-22T12:00:00Z', command='echo "<tag>"')])},
                      output='private transcript data')
        page = cli.render_html('A safe summary', [report])
        self.assertNotIn('<script>', page)
        self.assertNotIn('private transcript data', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertIn('a' * 40, page)
        self.assertIn('fixture-session', page)

    def test_job_windows_do_not_borrow_previous_job_skills(self):
        session = dict(id='thread', cwd='/checkout', brief='', events=[
            dict(timestamp='2026-10-22T11:00:00Z', kind='tool', command='cat /skills/tdd/SKILL.md'),
            dict(timestamp='2026-10-22T12:30:00Z', kind='tool', command='git rev-parse HEAD')])
        jobs=[dict(thread_id='thread', msg_id='first', dispatched_at='2026-10-22T10:00:00Z', status='completed', task='Sizing: small'),
              dict(thread_id='thread', msg_id='second', dispatched_at='2026-10-22T12:00:00Z', status='completed', task='Sizing: medium')]
        windows=cli.session_windows([session], jobs)
        second=next(w for w in windows if w.get('job_id')=='second')
        self.assertEqual([e['command'] for e in second['events']], ['git rev-parse HEAD'])

    def test_cli_unattributed_head_and_stale_approval_both_refuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            gh=root/'gh'
            gh.write_text('#!/usr/bin/env python3\nimport json,sys\nprint("" if "--header" in sys.argv else json.dumps({"head":{"sha":"' + 'a'*40 + '","ref":"feature"},"base":{"sha":"cccccccccccccccccccccccccccccccccccccccc"},"created_at":"2026-10-22T12:00:00Z"}))\n')
            gh.chmod(0o755)
            env=dict(os.environ, HOME=str(root), PATH=str(root)+os.pathsep+os.environ['PATH'])
            for expected,word in [('a'*40,'UNATTRIBUTED'),('b'*40,'head')]:
                result=subprocess.run(['python3',str(ROOT/'tools/skill-audit.py'),'--pr','example/repo#12','--expected-head',expected],env=env,capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0)
                self.assertIn(word,result.stdout+result.stderr)

    def test_cli_uses_real_rollout_to_verify_a_head(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / '.codex/sessions/2026/10/22/rollout-fixture.jsonl'
            log.parent.mkdir(parents=True)
            rows = [dict(type='session_meta', payload=dict(id='thread', cwd='/repo')),
                    dict(type='response_item', timestamp='2026-10-22T12:00:00Z',
                         payload=dict(type='message', role='user', content=[dict(type='input_text', text='Sizing: medium')]))]
            commands = ['cat /pstack/poteto-mode/SKILL.md',
                        'cat /pstack/principle-prove-it-works/SKILL.md',
                        'cat /mattpocock-skills/tdd/SKILL.md',
                        'cat /skills/grill-with-docs/SKILL.md',
                        'cat /skills/codebase-design/SKILL.md',
                        'python3 tests/test_widget.py',
                        'sed -i s/old/new/ src/widget.py',
                        'python3 tests/test_widget.py',
                        'git rev-parse HEAD',
                        'gh pr view 12 --repo example/repo']
            for i, command in enumerate(commands, 1):
                rows += [dict(type='response_item', timestamp=f'2026-10-22T12:00:{i*2:02d}Z',
                              payload=dict(type='function_call', name='exec_command', call_id=str(i), arguments=json.dumps(dict(cmd=command)))),
                         dict(type='response_item', timestamp=f'2026-10-22T12:00:{i*2+1:02d}Z',
                              payload=dict(type='function_call_output', call_id=str(i), output=json.dumps(dict(exit_code=1 if i==6 else 0, output='a'*40 if i==9 else 'ok'))))]
            log.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            gh = root / 'gh'
            diff = 'diff --git a/src/widget.py b/src/widget.py\n--- a/src/widget.py\n+++ b/src/widget.py\n@@ -1 +1 @@\n-old\n+new\n'
            info = dict(head=dict(sha='a'*40), base=dict(sha='c'*40), created_at='2026-10-22T12:00:00Z')
            gh.write_text('#!/usr/bin/env python3\nimport sys\nprint(' + repr(diff) + ' if "--header" in sys.argv else ' + repr(json.dumps(info)) + ')\n')
            gh.chmod(0o755)
            env = dict(os.environ, HOME=str(root), PATH=str(root)+os.pathsep+os.environ['PATH'])
            result = subprocess.run(['python3', str(ROOT/'tools/skill-audit.py'), '--pr', 'example/repo#12'], env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            self.assertIn('tdd_behavior: VERIFIED', result.stdout)
            self.assertIn('prove_behavior: VERIFIED', result.stdout)

    def test_daily_summary_counts_prs_and_completed_jobs_separately(self):
        reports=[dict(subject='example/repo#1',verdict='VERIFIED',checks={}),
                 dict(subject='example/repo#2',verdict='UNATTRIBUTED',checks={}),
                 dict(subject='example/repo#3',verdict='MISSING',checks={'tdd':dict(verdict='MISSING')})]
        summary=cli.daily_summary('2026-10-22',reports,[dict(verdict='MISSING')])
        self.assertIn('3 PRs',summary)
        self.assertIn('1 verified',summary)
        self.assertIn('1 unattributed',summary)
        self.assertIn('1 missing TDD',summary)
        self.assertIn('1 completed jobs',summary)

    def test_new_job_header_ends_a_resumed_job_without_a_completion_row(self):
        events=[dict(timestamp='2026-10-22T10:00:01Z',kind='message',role='user',text='Job checkout: /old'),
                dict(timestamp='2026-10-22T10:01:00Z',kind='tool',command='cat /skills/tdd/SKILL.md'),
                dict(timestamp='2026-10-22T12:00:00Z',kind='message',role='user',text='Job checkout: /new\nSizing: medium'),
                dict(timestamp='2026-10-22T12:01:00Z',kind='tool',command='git rev-parse HEAD')]
        jobs=[dict(thread_id='thread',msg_id='old',checkout_path='/old',dispatched_at='2026-10-22T10:00:00Z',status='completed',task='Sizing: small')]
        windows=cli.session_windows([dict(id='thread',events=events,brief='',cwd='/old')],jobs)
        old=next(w for w in windows if w.get('job_id')=='old')
        self.assertFalse(any(e.get('command')=='git rev-parse HEAD' for e in old['events']))
        self.assertTrue(any('Sizing: medium' in w['brief'] and not w.get('job_id') for w in windows))

    def test_git_head_observation_before_later_source_edit_cannot_pass(self):
        window=self.window()
        window['events'].append(dict(timestamp='2026-10-22T12:01:00Z',source_edit=True,kind='tool',command='apply patch'))
        report=cli.audit_pr('example/repo#12',{'head':{'sha':'a'*40}},'', [window])
        self.assertEqual(report['verdict'],'UNATTRIBUTED')

    def test_head_moving_during_audit_invalidates_an_otherwise_verified_session(self):
        class Forge:
            def get(self,path,*,diff=False):
                if diff:
                    self.diff_path=path
                    return ''
                return {'head':{'sha':'b'*40}}
        forge=Forge()
        with patch.object(cli,'audit_pr',return_value=dict(verdict='VERIFIED',checks={})):
            result=cli.audit_remote_pr(forge,'example/repo#12',{'head':{'sha':'a'*40},'base':{'sha':'c'*40}},[])
        self.assertNotEqual(result['verdict'],'VERIFIED')
        self.assertIn('c'*40+'...'+'a'*40,forge.diff_path)


if __name__ == '__main__':
    unittest.main()
