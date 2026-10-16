#!/usr/bin/env python3
"""Behavioral replays for the default-off setup experiment."""
import importlib.util
import json
import math
import os
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from git_env import fixture_env

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('setup_shadow', ROOT / 'ops/ci-setup-shadow.py')
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class SetupReplays(unittest.TestCase):
    def identity(self):
        return dict(os='linux', architecture='arm64', runtime='26.5.1',
                    installer='npm-11', lock='a'*64, package_path='mcp-server',
                    source_tree='b'*40)

    def test_each_incompatible_input_misses_with_compatible_control(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root/'source'; source.mkdir()
            (source/'module').write_text('locked dependency')
            mod.save_tree(source, root/'cache', self.identity())
            self.assertTrue(mod.restore_tree(root/'cache', root/'clean', self.identity()))
            for key in self.identity():
                identity = self.identity(); identity[key] += '-changed'
                target = root/key
                self.assertFalse(mod.restore_tree(root/'cache', target, identity), key)
                self.assertFalse(target.exists())

    def test_corrupt_partial_and_missing_ack_restore_miss(self):
        for fault in ('corrupt', 'partial', 'ack'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as d:
                root = Path(d); source = root/'source'; source.mkdir()
                (source/'module').write_text('good')
                mod.save_tree(source, root/'cache', self.identity())
                if fault == 'corrupt': (root/'cache/tree/module').write_text('bad')
                if fault == 'partial': (root/'cache/tree/module').unlink()
                if fault == 'ack': (root/'cache/manifest.json').unlink()
                self.assertFalse(mod.restore_tree(root/'cache', root/'target', self.identity()))
                self.assertFalse((root/'target').exists())

    def test_clones_are_independent_and_cache_contains_no_cluster(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root/'source'; source.mkdir()
            (source/'module').write_text('good')
            mod.save_tree(source, root/'cache', self.identity())
            for name in ('a', 'b'):
                self.assertTrue(mod.restore_tree(root/'cache', root/name, self.identity()))
            (root/'a/module').write_text('mutation')
            self.assertEqual((root/'b/module').read_text(), 'good')
            self.assertEqual((root/'cache/tree/module').read_text(), 'good')
            (source/'PG_VERSION').write_text('17')
            with self.assertRaises(mod.Refusal): mod.save_tree(source, root/'cluster', self.identity())

    def test_each_trial_has_an_independent_home_config_and_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            first=mod.trial_environment({'PATH':os.environ.get('PATH','')},root/'first')
            second=mod.trial_environment({'PATH':os.environ.get('PATH','')},root/'second')
            for key in ('HOME','XDG_CONFIG_HOME','TMPDIR'):
                Path(first[key],'state').write_text('fixture mutation')
                self.assertFalse(Path(second[key],'state').exists())

    def test_external_symlink_cannot_be_saved(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); source=root/'source'; source.mkdir()
            (root/'credential').write_text('canary-private')
            (source/'link').symlink_to(root/'credential')
            with self.assertRaises(mod.Refusal): mod.save_tree(source, root/'cache', self.identity())

    def row(self, cold, setup, total, dependency='dep', check='check'):
        return dict(cold=cold, setup_seconds=setup, job_seconds=total,
                    dependency_digest=dependency, check_digest=check, passed=True,
                    identity=self.identity())

    def test_matching_outputs_under_different_inputs_cannot_qualify(self):
        baseline=[self.row(cold, 10, 30) for _ in range(10) for cold in (True, False)]
        for key in self.identity():
            candidate=[self.row(cold, 8, 28) for _ in range(10) for cold in (True, False)]
            candidate[0]['identity'][key] += '-changed'
            self.assertEqual(mod.compare(baseline,candidate)['action'], 'remove-candidate-cache', key)

    def test_malformed_and_unpaired_samples_cannot_qualify(self):
        baseline=[self.row(cold, 10, 30) for _ in range(10) for cold in (True, False)]
        for fault in ('cold', 'identity', 'row'):
            candidate=[self.row(cold, 8, 28) for _ in range(10) for cold in (True, False)]
            if fault == 'row': candidate[0]=None
            else: candidate[0].pop(fault)
            self.assertEqual(mod.compare(baseline,candidate)['action'], 'remove-candidate-cache', fault)

    def test_timeout_disposes_owned_grandchild(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            pidfile=root/'child.pid'
            code=("import subprocess,sys,time; child=subprocess.Popen([sys.executable,'-c',"
                  "'import time; time.sleep(30)']); "
                  "open(sys.argv[1],'w').write(str(child.pid)); time.sleep(30)")
            with self.assertRaises(mod.Refusal):
                mod.command([sys.executable,'-c',code,str(pidfile)],root,{},timeout=1)
            pid=int(pidfile.read_text())
            # A zombie has exited and cannot consume another trial's capacity.
            state=subprocess.run(['ps','-o','stat=','-p',str(pid)],capture_output=True,text=True).stdout.strip()
            try:
                self.assertTrue(not state or state.startswith('Z'),state)
            finally:
                if state and not state.startswith('Z'): os.kill(pid,9)

    def test_parent_exit_disposes_redirected_descendants(self):
        for exit_code in (0, 7):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); pidfile = root/'child.pid'
                code = ("import subprocess,sys; child=subprocess.Popen([sys.executable,'-c',"
                        "'import time; time.sleep(30)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
                        "open(sys.argv[1],'w').write(str(child.pid)); sys.exit(int(sys.argv[2]))")
                try:
                    if exit_code:
                        with self.assertRaises(mod.Refusal):
                            mod.command([sys.executable,'-c',code,str(pidfile),str(exit_code)],root,{})
                    else:
                        mod.command([sys.executable,'-c',code,str(pidfile),str(exit_code)],root,{})
                    pid = int(pidfile.read_text())
                    state = subprocess.run(['ps','-o','stat=','-p',str(pid)],capture_output=True,text=True).stdout.strip()
                    self.assertTrue(not state or state.startswith('Z'), state)
                finally:
                    if pidfile.exists():
                        try: os.kill(int(pidfile.read_text()),9)
                        except ProcessLookupError: pass

    def test_digest_drift_in_both_arms_cannot_qualify(self):
        for field in ('dependency_digest', 'check_digest'):
            baseline = [self.row(cold,10,30) for _ in range(10) for cold in (True,False)]
            candidate = [self.row(cold,8,28) for _ in range(10) for cold in (True,False)]
            for index, (before, after) in enumerate(zip(baseline,candidate)):
                before[field] = after[field] = f'drift-{index}'
            self.assertEqual(mod.compare(baseline,candidate)['action'],'remove-candidate-cache',field)

    def test_output_never_overwrites_occupied_or_symlink_sink(self):
        for fault in ('existing', 'dangling', 'race-file', 'race-link'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); output = root/'output.json'; target = root/'target'
                if fault != 'dangling': target.write_text('preserve me')
                if fault == 'existing': output.write_text('preserve output')
                if fault == 'dangling': output.symlink_to(target)
                def benchmark(*args):
                    if fault == 'race-file': output.write_text('preserve output')
                    if fault == 'race-link': output.symlink_to(target)
                    return {'healthy': True}
                with patch.object(sys,'argv',['shadow','--enabled','--output',str(output)]), patch.object(mod,'benchmark',side_effect=benchmark):
                    try: result = mod.main()
                    except SystemExit as error: result = error.code
                self.assertNotEqual(result,0)
                if fault == 'dangling': self.assertFalse(target.exists())
                else: self.assertEqual(target.read_text(),'preserve me')
                if fault in ('existing','race-file'): self.assertEqual(output.read_text(),'preserve output')

    def test_new_output_contains_complete_report(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'output.json'
            with patch.object(sys,'argv',['shadow','--enabled','--output',str(output)]), patch.object(mod,'benchmark',return_value={'healthy': True}):
                self.assertEqual(mod.main(),0)
            self.assertEqual(json.loads(output.read_text()),{'healthy': True})

    def test_benchmark_inputs_are_bound_to_immutable_head(self):
        for target in ('practice-plugin','pip'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); repo = root/'source'; repo.mkdir()
                paths = mod.inventory(ROOT)['source_sha256']
                for relative in paths:
                    path = repo/relative; path.parent.mkdir(parents=True,exist_ok=True)
                    path.write_bytes((ROOT/relative).read_bytes())
                lock = repo/('requirements.lock' if target == 'pip' else target+'/package-lock.json')
                lock.parent.mkdir(parents=True,exist_ok=True); lock.write_text('immutable lock')
                def git(*args):
                    return subprocess.run(['git',*args],cwd=repo,env=fixture_env(),
                                          check=True,capture_output=True,text=True).stdout.strip()
                git('init','-q'); git('add','ops/ci.sh','ops/local-pg-ci.py','ops/stale-config-check.py',
                    'ops/atomic-rule-compat-migration-gate.py','.github/workflows/ci.yml',
                    '.github/workflows/db-acceptance.yml','.github/workflows/main-canary.yml',str(lock.relative_to(repo)))
                git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture')
                head = git('rev-parse','HEAD'); tree = git('rev-parse','HEAD^{tree}')
                real_command = mod.command; mutated = False
                def stub(argv,cwd,env,**kwargs):
                    nonlocal mutated
                    if argv[0] == 'git':
                        result = real_command(argv,cwd,fixture_env(env),**kwargs)
                        if argv[1] == 'checkout' and Path(cwd).name.startswith('trial-') and not mutated:
                            lock.write_text('mutated live lock')
                            (repo/'ops/ci.sh').write_text('mutated live inventory')
                            mutated = True
                        return result
                    if argv[:2] == ['node','--version']: return 'v26.5.1\n'
                    if argv[:2] == ['npm','--version']: return '11\n'
                    if argv[:2] == ['npm','test']:
                        return 'TAP version 13\nok 1 - fixture\n1..1\n# tests 1\n# pass 1\n# fail 0\n# cancelled 0\n'
                    if argv[0] == 'npm' and 'ci' in argv:
                        for path in (Path(argv[argv.index('--cache')+1]),Path(cwd)/target/'node_modules'):
                            path.mkdir(exist_ok=True); (path/'module').write_text('installed')
                        return ''
                    if argv[0] == 'npm' and 'ls' in argv:
                        return '{"name":"fixture","dependencies":{"module":{"version":"1"}}}'
                    if argv[1:3] == ['-m','venv']: return ''
                    if '-m' in argv and 'pip' in argv:
                        if '--version' in argv: return 'pip 26 fixture'
                        if 'install' in argv:
                            store = Path(argv[argv.index('--cache-dir')+1]); store.mkdir(exist_ok=True)
                            (store/'module').write_text('installed'); return ''
                        if 'list' in argv: return '[{"name":"fixture","version":"1"}]'
                        if 'check' in argv: return ''
                    if '-c' in argv: return '{"imports":"passed"}'
                    self.fail(f'unexpected command: {argv}')
                with patch.object(mod,'command',side_effect=stub): report = mod.benchmark(repo,1,target)
                self.assertTrue(mutated)
                self.assertEqual((report['head'],report['tree']),(head,tree))
                self.assertEqual(report['inventory']['source_sha256'],paths)
                expected_lock = hashlib.sha256(b'immutable lock').hexdigest()
                for rows in report['trials'].values():
                    for row in rows: self.assertEqual(row['identity']['lock'],expected_lock)

    def test_acceptance_p95_total_and_output_parity(self):
        baseline=[self.row(cold, 10, 30) for _ in range(10) for cold in (True, False)]
        winner=[self.row(cold, 8, 28) for _ in range(10) for cold in (True, False)]
        self.assertEqual(mod.compare(baseline, winner)['action'], 'eligible-for-full-lane-proof')
        for fault in ('slow-cold', 'slow-warm', 'total', 'dependency', 'check', 'missing', 'nan', 'failure'):
            bad=[dict(x) for x in winner]
            if fault == 'slow-cold': bad[0]['setup_seconds']=20
            if fault == 'slow-warm': bad[1]['setup_seconds']=20
            if fault == 'total': bad[0]['job_seconds']=100
            if fault in ('dependency','check'): bad[0][fault+'_digest']='changed'
            if fault == 'missing': bad.pop()
            if fault == 'nan': bad[0]['setup_seconds']=math.nan
            if fault == 'failure': bad[0]['passed']=False
            self.assertEqual(mod.compare(baseline,bad)['action'], 'remove-candidate-cache', fault)
        self.assertEqual(mod.compare([],[])['action'],'remove-candidate-cache')

    def test_result_failures_never_become_success_or_expose_child_text(self):
        canary='canary-client-private-token'
        for code in ("pass", "print('partial')", "import sys; print('refused'); sys.exit(75)",
                     "import sys; sys.exit(1)", "raise Exception('"+canary+"')"):
            with self.subTest(code=code), self.assertRaises(mod.Refusal) as failure:
                mod.command([sys.executable, '-c', code], Path.cwd(), {}, require_output=True)
            self.assertNotIn(canary,str(failure.exception))
        self.assertEqual(mod.command([sys.executable,'-c',"print('{}')"],Path.cwd(),{},require_output=True),'{}\n')

    def test_empty_dependency_ack_is_not_parity(self):
        for value in ('{}', '{"name":"package"}', '[]'):
            with self.assertRaises(mod.Refusal): mod.npm_dependencies(value)
        self.assertTrue(mod.npm_dependencies('{"name":"package","dependencies":{"a":{"version":"1"}}}'))

    def test_exit_zero_failed_or_incomplete_test_ack_refuses(self):
        healthy='TAP version 13\nok 1 - fixture\n1..1\n# tests 1\n# pass 1\n# fail 0\n# cancelled 0\n'
        with patch.object(mod,'command',return_value=healthy):
            self.assertTrue(mod.check_npm(Path.cwd(),{}))
        for output in ('', healthy.replace('# fail 0', '# fail 1'),
                       healthy.replace('ok 1', 'not ok 1'),
                       healthy.replace('# cancelled 0', '# cancelled 1'),
                       healthy.replace('# fail 0\n','')):
            with self.subTest(output=output), patch.object(mod,'command',return_value=output):
                with self.assertRaises(mod.Refusal): mod.check_npm(Path.cwd(),{})

    def test_complete_nested_and_multiple_tap_batches(self):
        nested = ('TAP version 13\n# Subtest: suite\n'
                  '    ok 1 - first\n    ok 2 - second\n    1..2\n'
                  "ok 1 - suite\n  ---\n  type: 'suite'\n  ...\n1..1\n# tests 2\n# pass 2\n# fail 0\n# cancelled 0\n")
        for output in (nested, nested+nested, nested.replace('    1..2\n','').replace('    ok 1','    1..2\n    ok 1')):
            with patch.object(mod,'command',return_value=output): self.assertTrue(mod.check_npm(Path.cwd(),{}))
        for output in (nested+'Bail out! fixture\n', nested.replace('    ok 2 - second\n',''),
                       nested.replace('1..1\n','1..2\n'), nested.replace('    1..2\n',''),
                       nested.replace('# tests 2','# tests 3').replace('# pass 2','# pass 3'),
                       nested.replace('    ok 2','    ok 1'), nested.replace('1..1\n',''),
                       nested+'# Subtest: truncated next test\n',
                       'ok 1 - fixture\n# tests 2\n# pass 2\n# fail 0\n# cancelled 0\n',
                       nested+nested.replace('# cancelled 0\n','')):
            with self.subTest(output=output), patch.object(mod,'command',return_value=output):
                with self.assertRaises(mod.Refusal): mod.check_npm(Path.cwd(),{})

    def test_real_node_tap_counts_suites_and_parent_tests(self):
        code = ("const {test,describe,it}=require('node:test'); "
                "test('parent',async t=>{await t.test('leaf',()=>{})}); "
                "describe('suite',()=>{it('leaf',()=>{}); it.skip('skip',()=>{})})")
        output = subprocess.run(['node','--test-reporter=tap','-e',code],check=True,
                                capture_output=True,text=True).stdout
        with patch.object(mod,'command',return_value=output):
            self.assertTrue(mod.check_npm(Path.cwd(),{}))

    def test_workflow_is_only_default_off_private_measurement(self):
        source=(ROOT/'.github/workflows/ci-setup-shadow.yml').read_text()
        self.assertIn('default: false',source)
        self.assertIn('if: ${{ inputs.enabled }}',source)
        self.assertIn('persist-credentials: false',source)
        self.assertIn('fetch-depth: 0',source)
        self.assertIn('contents: read',source)
        self.assertNotIn('actions/cache',source)
        self.assertNotIn('pull_request:',source)
        self.assertNotIn('schedule:',source)

    def test_default_off_has_no_install_or_output_file(self):
        result=subprocess.run([sys.executable,str(ROOT/'ops/ci-setup-shadow.py')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0)
        self.assertIn('disabled',result.stdout)

    def test_inventory_covers_classes_and_history_obligations(self):
        inventory=mod.inventory(ROOT)
        self.assertEqual(set(inventory['classes']),set(mod.class_names(ROOT)))
        self.assertIn('full-history',inventory['classes']['freshness']['retain'])
        self.assertIn('historic-schema-blob',inventory['classes']['migration']['retain'])
        self.assertEqual(inventory['removals'],[])
        self.assertFalse(inventory['database_reuse'])


if __name__ == '__main__': unittest.main()
