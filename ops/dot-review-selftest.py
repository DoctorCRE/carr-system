"""Offline behavioral regressions for the retained Dot review findings.

DOT_REVIEW_REPO selects an unmodified checkout for independent reproduction.
No fixture invokes a destructive shell command or production service.
"""
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

REPO = Path(os.environ.get("DOT_REVIEW_REPO", Path(__file__).resolve().parent.parent))
sys.path[:0] = [str(REPO / "hooks"), str(REPO / "ops")]


def load(rel):
    name = "dot_" + rel.replace("/", "_").replace("-", "_").replace(".", "_")
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class DotReview(unittest.TestCase):
    def test_r2_native_failed_exec_wrapper_is_a_finished_attempt(self):
        gate = load('hooks/completion-evidence-gate.py')
        outcome = 'Script completed\nWall time 1\nOutput:\n'+json.dumps({'exit_code':1, 'output':'write failed'})
        records = [user('Make the change'), native_call('failed', 'wrangler deploy'), native_output('failed', outcome),
                   native_call('retry', 'wrangler deploy'), native_output('retry', {'exit_code':0}),
                   native_call('t', 'pytest'), native_output('t', {'exit_code':0}), assistant('Done, verified and complete')]
        self.assertFalse(gate.evaluate(records)[0])

    def test_r2_repaired_failed_write_allows_later_verification(self):
        gate = load('hooks/completion-evidence-gate.py')
        records = [user('Make the change'), use('Write', {'file_path':'a.py'}, 'failed'), result('failed', True),
                   use('Write', {'file_path':'a.py'}, 'retry'), result('retry'),
                   use('Write', {'file_path':'b.py'}, 'b'), result('b'),
                   use('Bash', {'command':'pytest'}, 't'), result('t'), assistant('Done, verified and complete')]
        self.assertFalse(gate.evaluate(records)[0])
        for outcome in ({'exit_code':1}, {'exit_code':0}, {'session_id':123}, {'unknown':'value'}):
            native = [user('Make the change'), native_call('failed', 'wrangler deploy'),
                      native_output('failed', outcome), native_call('retry', 'wrangler deploy'),
                      native_output('retry', {'exit_code':0}), native_call('t', 'pytest'),
                      native_output('t', {'exit_code':0}), assistant('Done, verified and complete')]
            self.assertEqual(outcome.get('exit_code') is None, gate.evaluate(native)[0], outcome)

    def test_r02_unknown_or_unsuccessful_results_cannot_verify(self):
        gate = load('hooks/completion-evidence-gate.py')
        for kind in ('custom_tool_call', 'function_call'):
            for outcome in ({'status':'running'}, {'status':'failed'}, {'output':'tests failed'},
                            {'status':'completed'}, {'status':[]},
                            {'unrecognized':'value'}, 'arbitrary nonempty text',
                            {'exit_code':False}, {'exit_code':0, 'status':'running'},
                            {'exit_code':0, 'status':'failed'}):
                with self.subTest(kind=kind, outcome=outcome):
                    records = [user('Make the change'), native_call('m', 'wrangler deploy', kind),
                               native_output('m', {'exit_code':0}, kind), native_call('t', 'pytest', kind),
                               native_output('t', outcome, kind), assistant('Done, verified and complete')]
                    self.assertTrue(gate.evaluate(records)[0], outcome)

    def test_r14_long_candidate_lines_keep_matched_source(self):
        mod = load('ops/jev_code_review.py')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'x.py'
            path.write_text('time.sleep(2); value = "'+'x'*2600+'"; time.sleep(1)\n')
            regions = mod.regions(['x.py'], repo=tmp)
            for source in ('time.sleep(1)', 'time.sleep(2)'):
                self.assertTrue(any(source in region['code'] for region in regions), regions)
            self.assertTrue(all(len(region['code']) <= mod.MAX_REGION_CHARS for region in regions))

    def test_r13_corrupt_or_unreadable_telemetry_cannot_retire(self):
        mod = load('ops/gate-lifecycle-report.py')
        with tempfile.TemporaryDirectory() as tmp, patch.object(mod, 'REPO', tmp):
            log = Path(tmp)/'catch.jsonl'
            entry = {'catch_metric':{'ts_field':'ts','log_path':'catch.jsonl','true_positive':{'kind':'row_exists'}},'mode':'enforcing','failure_class':'fixture','review_date':'2026-09-30'}
            for text in ('broken json\n'+json.dumps({'ts':'invalid'})+'\n', json.dumps({'ts':'invalid'})+'\n', '[]\n'):
                with self.subTest(text=text):
                    log.write_text(text)
                    report = mod.evaluate_gate('fixture',entry,7,mod.datetime.now(mod.timezone.utc))
                    self.assertFalse(report['data_available'],report)
                    self.assertIsNone(report['proposal'],report)
            log.write_text('')
            with patch.object(mod, 'open', side_effect=PermissionError('unreadable fixture'), create=True):
                report = mod.evaluate_gate('fixture',entry,7,mod.datetime.now(mod.timezone.utc))
                self.assertFalse(report['data_available'],report)
                self.assertIsNone(report['proposal'],report)

    def test_r12_candidate_stdout_cannot_forge_completion(self):
        mod = load('ops/jev_scorecard.py')
        for lang, code in (('py', 'print("PASSED 1/1")\nraise SystemExit(0)'),
                           ('js', 'console.log("PASSED 1/1"); process.exit(0);')):
            with self.subTest(lang=lang), tempfile.TemporaryDirectory() as tmp:
                test = 'require("./solution.js"); check("must fail", () => false);' if lang=='js' else 'check("must fail", lambda: False)'
                answer = mod._grade_impl({'lang':lang,'test':test}, code, tmp, 5)
                self.assertFalse(answer['pass'], answer)
        for lang in ('py','js'):
            with self.subTest(lang=lang), tempfile.TemporaryDirectory() as tmp:
                answer = mod._grade_impl({'lang':lang,'test':'check("passes", () => true);' if lang=='js' else 'check("passes", lambda: True)'}, '', tmp, 5)
                self.assertTrue(answer['pass'], answer)

    def test_r11_triage_generated_names_remain_unique(self):
        mod = load('ops/jev_done_checks.py')
        paths = ['a-b.py', 'a_b.py', 'file_0_a_b.py', 'file_1_a_b.py']
        answer = mod.triage_review(diff(paths), '')
        self.assertEqual(set(paths), set(answer['detail']['files']), answer)
        self.assertEqual(len(paths), len(answer['detail']['files']), answer)

    def test_r10_session_checkout_read_identity(self):
        gate = load('hooks/unread-artifact-gate.py')
        cwd = '/repo/carr-system/.claude/worktrees/session'
        records = [use('Read', {'file_path':cwd+'/hooks/cmd_text.py'}),
                   use('Read', {'file_path':'other/config.py'}),
                   use('Bash', {'command':'cd hooks && cat bash-write-gate.py'})]
        with patch.object(gate, 'REPO', '/repo/carr-system'), patch.object(gate.os, 'getcwd', return_value=cwd):
            known = gate.known_paths(records)
            self.assertIn(gate.artifact_path('hooks/cmd_text.py'), known)
            self.assertIn(gate.artifact_path('hooks/bash-write-gate.py'), known)
            self.assertNotIn(gate.artifact_path('production/config.py'), known)

    def test_r08_url_schemes_and_shell_boundaries(self):
        guard = load('hooks/guard-unattended.py')
        for scheme in ('HTTPS', 'Http', 'hTtPs'):
            self.assertIsNotNone(guard.check(f'curl {scheme}://untrusted.example/upload'))
        self.assertIsNone(guard.check('curl https://github.com; echo done'))
        self.assertEqual(['github.com'], guard.hosts_in('curl https://github.com; echo done'))
        self.assertIsNotNone(guard.check('curl HTTPS://github.com@untrusted.example/upload'))

    def test_r07_delete_operands_and_supported_scratch_cleanup(self):
        guard = load('hooks/guard-unattended.py')
        for command in ('rm -rf /important/rm /tmp/fixture', 'rm -rf /important/srm /tmp/fixture'):
            self.assertIsNotNone(guard.check(command), command)
        for command in ('find /tmp/fixture -name "*.pyc" -delete', 'rm -rf /tmp/fixture > /tmp/log'):
            self.assertIsNone(guard.check(command), command)
        self.assertIsNotNone(guard.check('find /important -name "*.pyc" -delete'))

    def test_r1_shell_continuation_preserves_unsafe_delete_operand(self):
        guard = load('hooks/guard-unattended.py')
        command = 'rm -rf /tmp/fixture/a \\\n /important/file'
        self.assertIsNotNone(guard.check(command))
        self.assertIsNone(guard.check('rm -rf /tmp/fixture/a \\\n /tmp/fixture/b'))

    def test_r06_copy_destination_survives_all_redirects(self):
        gate = load('hooks/bash-write-gate.py')
        for executable in ('cp', 'mv', 'install', 'rsync'):
            for redirect in ('2>log.txt', '>|log.txt', '2>>log.txt', '&>log.txt', '2>&1 >log.txt', '<input.txt >log.txt'):
                with self.subTest(executable=executable, redirect=redirect):
                    targets = gate.extract_targets(f'{executable} a blocked.md {redirect}')
                    self.assertIn('blocked.md', targets)
                    self.assertIn('log.txt', targets)
                    self.assertNotIn('input.txt', targets)

    def test_r05_nested_standing_arguments_preserve_executable_text(self):
        gate = load('hooks/rule-pack-drift-gate.py')
        for source in ('await tools.mcp__carr__standing_context({x: await tools.exec_command({cmd: "wrangler deploy"})})',
                       'await tools.mcp__carr__standing_context({packs: deploy("wrangler deploy")})'):
            self.assertIn('wrangler deploy', gate.custom_tool_text({'name':'exec','input':source}))
        pure = 'text(await tools.mcp__carr__standing_context({packs:["release"], detail:"boot"}));'
        self.assertNotIn('detail', gate.custom_tool_text({'name':'exec','input':pure}))

    def test_r04_standing_requires_executable_service_success(self):
        gate = load('hooks/rule-pack-drift-gate.py')
        body = {'ok':True, 'rule_delivery':{'mode':'enforced','declared_packs':['release'],'packs_not_found':[]}}
        for command in ("printf '%s' 'run.sh call standing-context'", "echo run.sh call standing-context"):
            self.assertEqual((None,[],[]), gate.delivery_state([use('Bash',{'command':command},'s'), tool_output('s',json.dumps(body))]))
        command = './run.sh call standing-context \'{}\''
        for output in ({'ok':False, 'rule_delivery':body['rule_delivery']},
                       {'exit_code':1, 'output':json.dumps(body)}, 'service failed '+json.dumps(body)):
            self.assertEqual((None,[],[]), gate.delivery_state([use('Bash',{'command':command},'s'), tool_output('s',json.dumps(output) if isinstance(output,dict) else output)]))
        self.assertEqual(('enforced',['release'],[]), gate.delivery_state([use('Bash',{'command':command},'s'), tool_output('s',json.dumps(body))]))

    def test_r03_native_custom_standing_service_results(self):
        gate = load('hooks/rule-pack-drift-gate.py')
        body = {'rule_delivery':{'mode':'enforced','declared_packs':['release'],'packs_not_found':[]}}
        for source in ('text(await tools.mcp__carr__standing_context({packs:["release"]}));',
                       'text(await tools.exec_command({cmd: "./run.sh call standing-context \'{}\'"}));'):
            call = {'type':'response_item','payload':{'type':'custom_tool_call','name':'exec','input':source,'call_id':'s'}}
            output = native_output('s', body)
            self.assertEqual(('enforced',['release'],[]), gate.delivery_state([call, output]))
            self.assertEqual((None,[],[]), gate.delivery_state([call, native_output('wrong',body)]))

    def test_r02_verification_requires_completed_success_after_writes(self):
        gate = load('hooks/completion-evidence-gate.py')
        writes = [use('Write', {'file_path':'a.py'}, 'a'), result('a'),
                  use('Write', {'file_path':'b.py'}, 'b'), result('b')]
        test = use('Bash', {'command':'pytest'}, 't')
        for output in ([], [tool_output('t', 'Script running with session ID 123')],
                       [tool_output('t', 'Timed out')], [result('t', True)]):
            with self.subTest(output=output):
                blocked, reason = gate.evaluate([user('Make the change'), *writes, test, *output, assistant('Done, verified and complete')])
                self.assertTrue(blocked, reason)
        self.assertFalse(gate.evaluate([user('Make the change'), *writes, test, result('t'), assistant('Done, verified and complete')])[0])
        concurrent = {'type':'assistant','message':{'content':[
            use('Write', {'file_path':'a.py'}, 'a')['message']['content'][0],
            use('Write', {'file_path':'b.py'}, 'b')['message']['content'][0],
            test['message']['content'][0]]}}
        self.assertTrue(gate.evaluate([user('Make the change'), concurrent, result('t'), result('a'), result('b'), assistant('Done, verified and complete')])[0])
        for event_type in ('custom_tool_call', 'function_call'):
            mutation = native_call('m', 'wrangler deploy', event_type)
            check = native_call('t', 'pytest', event_type)
            for outcome in ({'exit_code':1}, {'session_id':123}, {'exit_code':None}, {'ok':False},
                            'Script completed\nWall time 1\nOutput:\n'+json.dumps({'exit_code':1,'output':'tests failed'})):
                records = [user('Make the change'), mutation, native_output('m', {'exit_code':0}, event_type), check, native_output('t', outcome, event_type), assistant('Done, verified and complete')]
                self.assertTrue(gate.evaluate(records)[0], records)
            self.assertFalse(gate.evaluate([user('Make the change'), mutation, native_output('m', {'exit_code':0}, event_type), check, native_output('t', {'exit_code':0}, event_type), assistant('Done, verified and complete')])[0])
        mutation = native_call('m', 'wrangler deploy', 'function_call')
        check = native_call('t', 'pytest', 'function_call')
        mutation['payload']['name'] = check['payload']['name'] = 'functions.exec_command'
        self.assertFalse(gate.evaluate([user('Make the change'), mutation, native_output('m', {'exit_code':0}, 'function_call'), check, native_output('t', {'exit_code':0}, 'function_call'), assistant('Done, verified and complete')])[0])

    def test_r01_secret_operation_never_persists_values(self):
        gate = load('hooks/settings-change-gate.py')
        for command in ('gh secret set --body=SYNTHETIC_MARKER TEST_KEY',
                        'gh secret set -b SYNTHETIC_MARKER TEST_KEY',
                        'wrangler secret put TEST_KEY <<<SYNTHETIC_MARKER'):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as tmp:
                spool = Path(tmp) / 'audit.jsonl'
                kind, target = gate.classify(command)
                self.assertNotIn('SYNTHETIC_MARKER', target)
                with patch.dict(os.environ, {'CARR_SETTINGS_GATE_OFFLINE':'1', 'CARR_SETTINGS_SPOOL':str(spool)}):
                    gate.record(kind, target, command, 'fixture', 'ok', 'selftest')
                self.assertNotIn('SYNTHETIC_MARKER', spool.read_text())

    def test_control_native_standing_result(self):
        gate = load('hooks/rule-pack-drift-gate.py')
        body = {'rule_delivery': {'mode':'enforced','declared_packs':['release'],'packs_not_found':[]}}
        event = {'type':'event_msg','payload':{'type':'mcp_tool_call_end','invocation':{'tool':'standing_context'},'result':{'Ok':body}}}
        self.assertEqual(['release'], gate.delivery_state([event])[1])
        call = {'type':'response_item','payload':{'type':'function_call','name':'mcp__carr__standing_context','call_id':'standing-fixture'}}
        output = {'type':'response_item','payload':{'type':'function_call_output','call_id':'standing-fixture','output':json.dumps(body)}}
        self.assertEqual(['release'], gate.delivery_state([call, output])[1])

    def test_control_single_quoted_prose(self):
        mod = load('hooks/cmd_text.py')
        self.assertNotIn('git reset', mod.strip_inert_text('echo --title \'"$(git reset --hard)"\''))

    def test_control_copy_with_redirect(self):
        mod = load('hooks/bash-write-gate.py')
        targets = mod.extract_targets('cp source blocked.md > log.txt')
        self.assertIn('blocked.md', targets)
        self.assertIn('log.txt', targets)

    def test_control_safe_delete_paths(self):
        mod = load('hooks/guard-unattended.py')
        self.assertTrue(mod.in_safe_zone('rm -r /tmp/fixture'))
        self.assertFalse(mod.in_safe_zone('rm -r /important/tmp/fixture'))
        self.assertFalse(mod.in_safe_zone('rm -r /important/scratchpad-copy'))

    def test_control_health_known_findings(self):
        mod = load('ops/release-pipeline.py')
        with tempfile.TemporaryDirectory() as tmp:
            pipe = mod.Pipeline(mod.load_config(), repo=Path(tmp), out=lambda *_: None)
            pipe._release_worktree_ready = MagicMock(); pipe.remove_worktrees = MagicMock()
            pipe.health_read = MagicMock(return_value=(mod.Result(1), [{'key':'fixture','hard_error':False}], True))
            self.assertEqual(0, pipe.health_preflight('a'*40))

    def test_control_unique_triage_key_compatibility(self):
        mod = load('ops/jev_done_checks.py')
        answer = mod.triage_review(diff(['src/widgets.py']), '')
        self.assertIn('src/widgets.py', answer['detail']['files'])

    def test_b11_newest_ci_outcome(self):
        mod = load('ops/release-pipeline.py')
        gh = MagicMock()
        gh.runs_for.return_value = [{'id':1,'name':'CI','event':'pull_request','conclusion':'success'}, {'id':2,'name':'CI','event':'pull_request','conclusion':'failure'}]
        gh.jobs.return_value = [{'name':'strict','conclusion':'success'},{'name':'secret','conclusion':'success'}]
        with self.assertRaises(mod.Blocked):
            mod.Pipeline.ci_run(None, gh, {'ci_workflow_name':'CI','ci_required_job':'strict'}, 1, 'a'*40)

    def test_b12_lane_mutation_isolation(self):
        mod = load('ops/release-pipeline.py')
        cfg = mod.load_config()
        with tempfile.TemporaryDirectory() as tmp:
            pipe = mod.Pipeline(cfg, repo=Path(tmp), out=lambda *_: None)
            pipe.store = MagicMock(); pipe.store.load.return_value = {}
            pipe.git = MagicMock(return_value='a'*40)
            pipe.last_released = MagicMock(return_value='b'*40)
            pipe.changed_paths = MagicMock(return_value=['fixture'])
            pipe.release_target = MagicMock(return_value='a'*40)
            pipe.remove_worktrees = MagicMock()
            pipe.fail = MagicMock(return_value=1)
            def worker(*_):
                pipe.mutated = True
                return {}
            pipe.release_worker = worker
            pipe.release_app = MagicMock(side_effect=mod.Blocked('checks_pending','fixture'))
            with patch.object(mod, 'kill_switch', return_value=None), patch.object(mod, 'classify', return_value=(True,['fixture'])):
                pipe.run_lane('worker'); pipe.run_lane('app')
            pipe.fail.assert_not_called()
            self.assertEqual('blocked', pipe.store.record.call_args.args[0]['status'])

    def test_b13_partial_migration_warning(self):
        mod = load('ops/release-pipeline.py')
        cfg = mod.load_config()
        with tempfile.TemporaryDirectory() as tmp:
            pipe = mod.Pipeline(cfg, repo=Path(tmp), out=lambda *_: None)
            pipe.worker_evidence = MagicMock(return_value={'pr':1,'verifier':'fixture','verifier_evidence':'fixture','test_evidence':'fixture'})
            pipe.unattended_preflight = MagicMock(); pipe.wrangler_auth = MagicMock()
            pipe._release_worktree_ready = MagicMock()
            pipe._health_baseline_or_block = MagicMock(return_value=([],True,0))
            def step(name, *_):
                if name=='migrate-apply':
                    raise mod.StepFailed(name, 1, 'fixture', 'first batch committed; later batch failed')
                return mod.Result(0, 'pending: 2' if name=='migrate-plan' else '{"receipt_id":"fixture"}')
            pipe.step = step
            with self.assertRaises(mod.StepFailed):
                pipe.release_worker(cfg['worker'],'b'*40,'a'*40,{},'worker')
            self.assertTrue(pipe.db_ahead_of_worker)

    def test_b14_live_health_failure(self):
        mod = load('ops/release-pipeline.py')
        with tempfile.TemporaryDirectory() as tmp:
            pipe = mod.Pipeline(mod.load_config(),repo=Path(tmp),out=lambda *_:None)
            pipe._release_worktree_ready = MagicMock();pipe.remove_worktrees = MagicMock()
            pipe.health_read = MagicMock(side_effect=[(mod.Result(0),[],True),(mod.Result(1),[{'key':'fixture','hard_error':True}],True)])
            self.assertEqual(1,pipe.health_preflight('a'*40))

    def test_b15_meta_keeps_failed_test(self):
        gate = load('hooks/jev-supervisor.py')
        records = [user('Run tests'), use('Bash',{'command':'pytest'},'t'), result('t',True), {'type':'user','isMeta':True,'message':{'content':'Background task complete'}}]
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp)/'trace.jsonl';transcript.write_text('\n'.join(map(json.dumps,records)))
            self.assertEqual(1,gate._last_test_evidence(str(transcript)).get('test_failure_count'))

    def test_b18_triage_unique_paths(self):
        mod=load('ops/jev_done_checks.py')
        # src/, not ops/: ops/ is tier 3 in ops/config/review-tiers.v1.json.
        answer=mod.triage_review(diff(['src/a-b.py','src/a_b.py']), '')
        self.assertEqual({'src/a-b.py','src/a_b.py'},set(answer['detail']['files']),answer)

    def test_b19_triage_overflow_risky_path(self):
        mod=load('ops/jev_done_checks.py')
        answer=mod.triage_review(diff(['file%d.py'%i for i in range(25)]+['auth.py']), '')
        self.assertEqual('high',answer['detail']['files'].get('auth.py',{}).get('risk'),answer)
        self.assertNotEqual('ok',answer['verdict'])

    def test_b21_failed_verification_not_credited(self):
        gate=load('hooks/completion-evidence-gate.py')
        records=[user('Make the change'),use('Write',{'file_path':'a.py'}),use('Write',{'file_path':'b.py'}),use('Bash',{'command':'pytest'},'t'),result('t',True),assistant('Done, verified and complete')]
        blocked,reason=gate.evaluate(records)
        self.assertTrue(blocked,reason)

    def test_b22_multiple_tool_blocks(self):
        gate=load('hooks/completion-evidence-gate.py')
        writes={'type':'assistant','message':{'content':[{'type':'tool_use','id':'a','name':'Write','input':{'file_path':'a.py'}},{'type':'tool_use','id':'b','name':'Write','input':{'file_path':'b.py'}}]}}
        blocked,reason=gate.evaluate([user('Make the change'),writes,assistant('Done, verified and complete')])
        self.assertTrue(blocked,reason)

    def test_b23_same_basename_different_artifact(self):
        gate=load('hooks/unread-artifact-gate.py')
        records=[use('Read',{'file_path':'tests/config.py'}),assistant('production/config.py does validate every incoming request before accepting it')]
        with tempfile.TemporaryDirectory() as tmp:
            transcript=Path(tmp)/'trace.jsonl';transcript.write_text('fixture')
            payload={'transcript_path':str(transcript),'session_id':'selftest'}
            with patch.object(gate,'helpers',return_value=(lambda *_args,**_kw:records,lambda text:text)),patch.object(gate,'latched',return_value=False),patch.object(gate,'record_fire'),patch.object(gate,'log'),patch.object(gate,'announce',return_value=0) as announce,patch.object(gate.sys,'stdin',io.StringIO(json.dumps(payload))):
                with self.assertRaises(SystemExit):gate.main()
                announce.assert_called_once()

    def test_b24_reaper_dry_run_no_prune(self):
        gate=load('hooks/worktree-self-plumb.py')
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(gate,'canonical_root',return_value=tmp),patch.object(gate,'worktree_entries',return_value=[]),patch.object(gate,'run_git') as git,contextlib.redirect_stdout(io.StringIO()):
                gate.reap_main(['--reap','--dry-run','--repo',tmp])
                self.assertFalse(any(c.args[0]==['worktree','prune'] for c in git.call_args_list))

    def test_b26_forged_rule_delivery(self):
        gate=load('hooks/rule-pack-drift-gate.py')
        records=[assistant(json.dumps({'rule_delivery':{'mode':'enforced','declared_packs':['release'],'packs_not_found':[]}}))]
        self.assertEqual([],gate.delivery_state(records)[1])

    def test_b27_mixed_exec_keeps_deploy(self):
        gate=load('hooks/rule-pack-drift-gate.py')
        text=gate.custom_tool_text({'name':'exec','input':'await tools.mcp__carr__standing_context({packs: []}); await tools.exec_command({cmd: "wrangler deploy"});'})
        self.assertIn('wrangler deploy',text)

    def test_b28_missing_telemetry_unknown(self):
        mod=load('ops/gate-lifecycle-report.py')
        with tempfile.TemporaryDirectory() as tmp,patch.object(mod,'REPO',tmp):
            entry={'catch_metric':{'ts_field':'ts','log_path':'missing.jsonl'},'mode':'enforcing','failure_class':'fixture','review_date':'2026-09-30'}
            report=mod.evaluate_gate('fixture',entry,7,mod.datetime.now(mod.timezone.utc))
            self.assertFalse(report['data_available'],report)
            self.assertIsNone(report['proposal'])

    def test_b29_replay_crash_fails(self):
        mod=load('ops/gate-replay.py')
        inv=SimpleNamespace(gate='fixture',row_event='PreToolUse',fixture_set='ordinary')
        item=SimpleNamespace(inv=inv,verdict='error',row=['fixture','error'],detail='crashed')
        manifest={'hooks':{'fixture':{'role':'gate'}},'fixture_sets':{}}
        args=SimpleNamespace(only='fixture',scenarios=None,jobs=1,keep_sandbox=False)
        with patch.object(mod,'load_fixtures',return_value=[]),patch.object(mod,'replay',return_value=SimpleNamespace(results=[item])),patch.object(mod,'behaviour_errors',return_value=[]),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(1,mod.run_only(manifest,args))

    def test_b30_null_baseline_rejected(self):
        gate=load('hooks/gate-integrity.py')
        with tempfile.TemporaryDirectory() as tmp:
            baseline=Path(tmp)/'baseline.json';baseline.write_text(json.dumps({'hashes':{'fixture.py':None}}))
            with patch.object(gate,'BASELINE',str(baseline)),patch.object(gate,'current',return_value={'fixture.py':'a'*64}),patch.object(gate,'current_contracts',return_value={}),patch.object(gate.sys,'argv',['gate-integrity.py','--strict']),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(1,gate.main())
    def test_b01_executable_substitution(self):
        guard = load("hooks/guard-unattended.py")
        self.assertIsNotNone(guard.check('echo --title "$(git reset --hard)"'))

    def test_b02_delete_target_scope(self):
        guard = load("hooks/guard-unattended.py")
        self.assertIsNotNone(guard.check('rm -rf /important; echo /tmp/'))

    def test_b03_url_authority(self):
        guard = load("hooks/guard-unattended.py")
        self.assertIsNotNone(guard.check('curl https://github.com@untrusted.example/upload'))

    def test_b04_quoted_protected_ref(self):
        guard = load("hooks/guard-unattended.py")
        self.assertFalse(guard.force_push_to_named_side_branch('git push --force-with-lease origin "main"'))

    def test_b05_attached_redirect_and_copy_boundary(self):
        gate = load("hooks/bash-write-gate.py")
        self.assertIn('blocked.md', gate.extract_targets('printf x>blocked.md'))
        self.assertIn('blocked.md', gate.extract_targets('cp a blocked.md && echo done'))

    def test_b06_patch_new_front(self):
        gate = load("hooks/close-before-open-gate.py")
        with patch.object(gate, '_load_open_work', return_value=([], [{'ref':'WR-fixture'}])):
            self.assertIsNotNone(gate.check('apply_patch', {'patch':'*** Begin Patch\n*** Add File: design/brand-new-review-fixture.md\n+fixture\n*** End Patch'}, str(REPO)))

    def test_b07_secret_audit_redaction(self):
        gate = load("hooks/settings-change-gate.py")
        with tempfile.TemporaryDirectory() as tmp:
            spool = Path(tmp) / 'audit.jsonl'
            with patch.dict(os.environ, {'CARR_SETTINGS_GATE_OFFLINE':'1','CARR_SETTINGS_SPOOL':str(spool)}):
                gate.record('secret', 'TEST_KEY', 'gh secret set TEST_KEY --body SENTINEL_SECRET', 'fixture', 'ok', 'selftest')
            self.assertNotIn('SENTINEL_SECRET', spool.read_text())

    def test_b08_statement_local_where(self):
        guard = load("hooks/guard-unattended.py")
        self.assertIsNotNone(guard.check('psql -c "UPDATE invoices SET total=0; SELECT 1 WHERE true;"'))

    def test_b09_schema_delete(self):
        guard = load("hooks/guard-unattended.py")
        self.assertIsNotNone(guard.check('psql -c "DELETE FROM public.invoices;"'))

    def test_b10_parent_path_identity(self):
        pre = load("ops/jev_precheck.py")
        self.assertEqual([], pre.referenced_paths('python ../ops/jev_precheck.py', str(REPO)))

    def test_b16_human_origin(self):
        req = load("ops/jev_requirements.py")
        self.assertEqual('Implement validation', req._human_text({'type':'user','origin':{'kind':'human'},'message':{'content':'Implement validation'}}))

    def test_b17_read_loop_contract(self):
        with patch.object(sys, 'path', [str(REPO / 'tools'), *sys.path]):
            costs = load("tools/system_costs.py")
        calls = []

        def record(name, payload):
            calls.append((name, payload))
            if name == 'read-loop':
                return {'loop': {'loop_id': 'L', 'version': 3}, 'amended': False, 'amendments': []}
            return {'ok': True}

        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'loops.json'
            state.write_text(json.dumps({'open': {'jev': {'loop_id': 'L', 'fingerprint': 'old',
                                                        'through': '2026-10-04'}},
                                         'episodes': {'jev': 1}, 'pending': None}))
            costs.reconcile({'through': '2026-10-05', 'observed_at': '2026-10-06T00:00:00Z',
                             'providers': [], 'alerts': [{'provider': 'jev', 'kind': 'daily_spike',
                                                         'amount_usd': 8, 'threshold_usd': 4,
                                                         'driver': 'fixture'}]}, state, record)
        self.assertEqual(['read-loop', 'update-loop'], [name for name, _ in calls])
        self.assertEqual(3, calls[1][1]['base_version'])

    def test_b20_grader_harness_completion(self):
        score = load("ops/jev_scorecard.py")
        with tempfile.TemporaryDirectory() as tmp:
            result = score._grade_impl({'lang':'py','test':'check(False, "must fail")'}, 'raise SystemExit(0)', tmp, 5)
        self.assertFalse(result['pass'], result)

    def test_b31_ipv6_disposable_dsn(self):
        gate = load("ops/gate-zero-scheduler-canary-gate.py")
        self.assertEqual('postgresql://carr_jobs:pw@[::1]:5432/carr_ci', gate.jobs_dsn('postgresql://carr_ci:pw@[::1]:5432/carr_ci', 'pw'))

    def test_e01_nearby_candidates_keep_code(self):
        review = load("ops/jev_code_review.py")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'x.py').write_text('time.sleep(1)\n' + 'x = 1\n'*19 + 'v = str(value or "")\n')
            regions = review.regions(['x.py'], tmp)
        self.assertTrue(any('v = str(value or "")' in r['code'] for r in regions), regions)

    def test_e02_long_context_keeps_candidate(self):
        review = load("ops/jev_code_review.py")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'x.py').write_text('#' + 'x'*2700 + '\ntime.sleep(1)\n')
            regions = review.regions(['x.py'], tmp)
        self.assertTrue(any('time.sleep(1)' in r['code'] for r in regions), regions)

    def test_e03_bare_except_signature(self):
        review = load("ops/jev_code_review.py")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'x.py').write_text('try:\n    risky()\nexcept:\n    pass\n')
            regions = review.regions(['x.py'], tmp)
        self.assertTrue(regions)

    def test_e04_js_escaped_newline(self):
        part = load("ops/jev_code_partition.py")
        text = 'const s = "a\\\nb";\nfunction f() {\n return 7;\n}\n'
        self.assertIn(('function', 3, 5), part.js_spans(text))

    def test_e05_git_unicode_filenames(self):
        for rel in ('ops/jev_code_review.py', 'ops/jev_code_partition.py'):
            mod = load(rel)
            def listing(argv, **kwargs):
                output = 'café.py\0' if '-z' in argv else '"caf\\303\\251.py"\n'
                return subprocess.CompletedProcess(argv, 0, stdout=output if kwargs.get('text') else os.fsencode(output))
            with patch.object(mod.subprocess, 'run', side_effect=listing):
                self.assertEqual(['café.py'], mod.tracked_sources())

    def test_e06_recovery_keeps_complete_handler(self):
        part = load("ops/jev_code_partition.py")
        text = 'def f():\n try:\n  x = "' + 'a'*2450 + '"\n except Exception:\n' + ''.join('  x%d = "%s"\n' % (i, 'b'*100) for i in range(5)) + '  return None\n'
        parts = part.partition_text('x.py', text)
        self.assertTrue(any(p['line']<=4 and p['end_line']>=10 for p in parts), parts)


def user(text):
    return {'type':'user','message':{'role':'user','content':text}}


def assistant(text):
    return {'type':'assistant','message':{'role':'assistant','content':[{'type':'text','text':text}]}}


def use(name, args, identity='fixture'):
    return {'type':'assistant','message':{'content':[{'type':'tool_use','id':identity,'name':name,'input':args}]}}


def result(identity, error=False):
    return {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':identity,'is_error':error,'content':'tests failed' if error else 'tests passed'}]}}


def tool_output(identity, content):
    return {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':identity,'content':content}]}}


def native_call(identity, command, kind='custom_tool_call'):
    return {'type':'response_item','payload':{'type':kind,'name':'exec' if kind=='custom_tool_call' else 'exec_command','call_id':identity,
            'input':'text(await tools.exec_command({cmd: '+json.dumps(command)+'}));', 'arguments':json.dumps({'cmd':command})}}


def native_output(identity, output, kind='custom_tool_call'):
    return {'type':'response_item','payload':{'type':kind+'_output','call_id':identity,'output':json.dumps(output)}}


def diff(paths):
    return ''.join('diff --git a/{0} b/{0}\n--- a/{0}\n+++ b/{0}\n@@ -1 +1 @@\n-old\n+new\n'.format(path) for path in paths)


def run_regressions(prefixes):
    names = [name for name in unittest.defaultTestLoader.getTestCaseNames(DotReview)
             if name.startswith(tuple(prefixes))]
    suite = unittest.TestSuite(DotReview(name) for name in names)
    if not unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful():
        raise AssertionError("Dot behavioral regressions failed")


if __name__ == "__main__":
    unittest.main()
