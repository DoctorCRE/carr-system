#!/usr/bin/env python3
"""Replay ordered seals using the real Git, allocator, generator guard and DB adapter."""
import importlib.util
import io
from contextlib import redirect_stdout, redirect_stderr
import json
import os
import subprocess
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'tools'))
import integration_candidate as integration
from migration_number_contract import MigrationNumberError

class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='integration-candidate-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.repo=self.root/'repo'; self.repo.mkdir()
        from git_env import fixture_env
        self.env=fixture_env()
        self.g('init','-q'); self.g('config','user.name','Fixture'); self.g('config','user.email','fixture@example.invalid')
        (self.repo/'migrations').mkdir(); (self.repo/'mcp-server/src').mkdir(parents=True)
        (self.repo/'db').mkdir(); (self.repo/'db/schema.sql').write_text('-- fixture restore\n')
        self.write('migrations/0748_base.sql','select 1;')
        self.registry(97)
        self.commit(); self.base=self.g('rev-parse','HEAD')
        self.g('update-ref','refs/remotes/origin/main',self.base)
        self.receipt=self.root/'receipt.json'
    def g(self,*args):
        return subprocess.check_output(['git',*args],cwd=self.repo,env=self.env,text=True,stderr=subprocess.DEVNULL).strip()
    def write(self,p,value): (self.repo/p).write_text(value)
    def registry(self,v): self.write(f'mcp-server/src/scac-mutation-registry.v{v}.generated.js',f'export const SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry.v{v}";\n')
    def commit(self):
        self.g('add','migrations','mcp-server','db'); self.g('commit','-qm','Fixture source')
    def test_manifest_allocates_after_archived_runtime_files(self):
        import hashlib
        current = 'mcp-server/src/scac-mutation-registry.current.generated.js'
        self.write(current, (self.repo/'mcp-server/src/scac-mutation-registry.v97.generated.js').read_text())
        (self.repo/'ops/config').mkdir(parents=True)
        pin = {'number':97,'version':'scac-mutation-registry.v97','artifact_sha256':hashlib.sha256((self.repo/current).read_bytes()).hexdigest()}
        self.write('ops/config/scac-registry-chain.json', json.dumps({'schema':'scac-registry-chain.v1','versions':[pin]}))
        (self.repo/'mcp-server/src/scac-mutation-registry.v97.generated.js').rename(self.root/'archived-v97')
        self.g('add','ops/config/scac-registry-chain.json'); self.commit()
        base = self.g('rev-parse','HEAD'); self.g('update-ref','refs/remotes/origin/main',base)
        plan = integration.allocation_plan(self.repo,base,['0749_example.sql'])
        self.assertEqual((plan['registry_predecessor'],plan['registry_successor']),(97,98))
        self.assertEqual(plan['predecessor_sha256'],pin['artifact_sha256'])
        self.assertEqual(integration.validate_candidate(self.repo,base)['registry_predecessor'],97)
        successor = b'export const SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry.v98";\n'
        target = self.repo/current
        original = target.read_bytes()
        with self.assertRaises(MigrationNumberError):
            integration.write_generated_artifact(self.repo,target,original+b'// resealed\n')
        with self.assertRaises(MigrationNumberError):
            integration.write_generated_artifact(self.repo,target,successor.replace(b'v98',b'v99'))
        target.write_bytes(original+b'// foreign edit\n')
        with self.assertRaises(MigrationNumberError):
            integration.write_generated_artifact(self.repo,target,successor)
        self.assertEqual(target.read_bytes(),original+b'// foreign edit\n')
        target.write_bytes(original)
        integration.write_generated_artifact(self.repo,target,successor)
        self.assertEqual(target.read_bytes(),successor)
        inode = target.stat().st_ino
        integration.write_generated_artifact(self.repo,target,successor)
        self.assertEqual(target.stat().st_ino,inode)
        self.assertEqual(integration.main_registry_pins(self.repo,base),[pin])
        with self.assertRaises(MigrationNumberError):
            integration.check_generated_write(self.repo,self.repo/current,b'edited',base)

    def test_archived_history_validates_against_a_main_without_the_manifest(self):
        import hashlib
        current = 'mcp-server/src/scac-mutation-registry.current.generated.js'
        old = self.repo/'mcp-server/src/scac-mutation-registry.v97.generated.js'
        original = old.read_bytes()
        self.write(current, original.decode())
        (self.repo/'ops/config').mkdir(parents=True)
        pin = {'number':97,'version':'scac-mutation-registry.v97',
               'artifact_sha256':hashlib.sha256(original).hexdigest()}
        self.write('ops/config/scac-registry-chain.json',json.dumps({'schema':'scac-registry-chain.v1','versions':[pin]}))
        old.rename(self.root/'archived-v97')
        self.g('add','ops/config/scac-registry-chain.json'); self.commit()
        result = integration.validate_candidate(self.repo,self.base)
        self.assertEqual(result['registry_predecessor'],97)
        pin['artifact_sha256'] = '0'*64
        self.write('ops/config/scac-registry-chain.json',json.dumps({'schema':'scac-registry-chain.v1','versions':[pin]}))
        self.g('add','ops/config/scac-registry-chain.json'); self.commit()
        with self.assertRaisesRegex(MigrationNumberError,'history pin'):
            integration.validate_candidate(self.repo,self.base)

    def test_generation_accepts_exact_pending_main_merge_but_proof_requires_commit(self):
        self.g('checkout', '-qb', 'feature')
        self.write('migrations/0752_feature.sql', 'select 2;')
        self.commit()
        self.g('checkout', '-qb', 'main-next', self.base)
        self.write('migrations/0751_main.sql', 'select 3;')
        self.registry(98)
        self.commit()
        latest = self.g('rev-parse', 'HEAD')
        self.g('update-ref', 'refs/remotes/origin/main', latest)
        self.g('checkout', 'feature')
        self.g('merge', '--no-commit', 'main-next')
        target = self.repo/'mcp-server/src/scac-mutation-registry.v99.generated.js'
        content = b'export const SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry.v99";\n'
        integration.write_generated_artifact(self.repo, target, content)
        self.assertEqual(target.read_bytes(), content)
        with self.assertRaises(MigrationNumberError):
            integration.validate_candidate(self.repo, latest)
        self.write('migrations/0751_main.sql', 'select 4;')
        with self.assertRaises(MigrationNumberError):
            integration.check_generated_write(self.repo, target, content, latest)

    def test_actual_generator_sink_rejects_reseal_and_wrong_successor(self):
        target=self.repo/'mcp-server/src/scac-mutation-registry.v97.generated.js'
        integration.check_generated_write(self.repo,target,target.read_bytes(),self.base)
        for path,data in [(target,b'edited'),(self.repo/'mcp-server/src/scac-mutation-registry.v99.generated.js',b'wrong')]:
            with self.assertRaises(MigrationNumberError): integration.check_generated_write(self.repo,path,data,self.base)
    def test_inventory_write_caller_preserves_sealed_bytes_and_sanitizes_errors(self):
        self.install_sink()
        target=self.repo/'mcp-server/src/scac-mutation-registry.v97.generated.js'
        original=target.read_bytes()
        script="import {writeIntegratedArtifact} from './ops/integration-generation.mjs'; await writeIntegratedArtifact(process.argv[1],process.argv[2]);"
        def call(content):
            return subprocess.run(['node','--input-type=module','-e',script,str(target),content],cwd=self.repo,env=self.env,capture_output=True,text=True)
        self.assertEqual(call(original.decode()).returncode,0)
        refusal=call('private-canary-123')
        self.assertNotEqual(refusal.returncode,0)
        self.assertNotIn('private-canary-123',refusal.stdout+refusal.stderr)
        self.assertEqual(target.read_bytes(),original)

    def install_sink(self):
        (self.repo/'ops').mkdir(exist_ok=True); (self.repo/'tools').mkdir(exist_ok=True)
        for name in ['integration_candidate.py','migration_number_contract.py']:
            shutil.copyfile(REPO/'tools'/name,self.repo/'tools'/name)
        for name in ['git_env.py','integration-generation.mjs']:
            shutil.copyfile(REPO/'ops'/name,self.repo/'ops'/name)
    def sink(self,target_expr,content,env=None):
        script=("import {writeIntegratedArtifact} from './ops/integration-generation.mjs';"
                f"await writeIntegratedArtifact({target_expr},process.argv[1]);")
        return subprocess.run(['node','--input-type=module','-e',script,content],cwd=self.repo,
                              env={**self.env,**(env or {})},capture_output=True,text=True)
    def test_export_targets_outside_canonical_paths_need_no_git_context(self):
        self.install_sink()
        export=self.root/'export'/'migrations'/'0001_historical.sql'; export.parent.mkdir(parents=True)
        # Exports outside the repository render without a current integration base.
        self.g('update-ref','-d','refs/remotes/origin/main')
        result=self.sink(json.dumps(str(export)),'historical bytes')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(export.read_text(),'historical bytes')
    def test_python_cli_accepts_absolute_artifact_paths_through_filesystem_aliases(self):
        self.install_sink()
        target = self.repo/'migrations/0749_pending.sql'
        result = subprocess.run(['python3',str(self.repo/'tools/integration_candidate.py'),
                                 '--write',str(target)],input=b'select 2;\n',cwd=self.repo,
                                env=self.env,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(target.read_bytes(),b'select 2;\n')

    def test_file_url_targets_keep_the_filesystem_writer_contract(self):
        self.install_sink()
        target=self.repo/'mcp-server/src/scac-mutation-registry.v97.generated.js'
        result=self.sink(f'new URL({json.dumps(target.as_uri())})',target.read_text())
        self.assertEqual(result.returncode,0,result.stderr)
        seals=self.repo/'ops/config/seals.json'; seals.parent.mkdir()
        result=self.sink(f'new URL({json.dumps(seals.as_uri())})','{}\n')
        self.assertEqual(result.returncode,0,result.stderr); self.assertEqual(seals.read_text(),'{}\n')
    def test_nonseal_source_fixture_keeps_the_plain_writer_contract(self):
        self.install_sink()
        target=self.repo/'mcp-server/src/synthetic-render-fixture.js'
        self.g('update-ref','-d','refs/remotes/origin/main')
        for content in ['first fixture','updated fixture']:
            result=self.sink(json.dumps(str(target)),content)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(target.read_text(),content)

    def test_sink_refuses_while_another_owner_holds_the_generation_lock(self):
        import fcntl
        self.install_sink()
        target=self.repo/'mcp-server/src/scac-mutation-registry.v98.generated.js'
        content='export const SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry.v98";\n'
        with (self.repo/'.git/integration-generation.lock').open('a') as held:
            fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
            refused=self.sink(json.dumps(str(target)),content)
            forged=self.sink(json.dumps(str(target)),content,{'CARR_INTEGRATION_OWNER':'forged'})
        self.assertNotEqual(refused.returncode,0); self.assertNotEqual(forged.returncode,0)
        self.assertFalse(target.exists())
        self.assertEqual(self.sink(json.dumps(str(target)),content).returncode,0)
        self.assertEqual(target.read_text(),content)
    def test_base_advance_between_check_and_write_cannot_overwrite_a_new_seal(self):
        target=self.repo/'mcp-server/src/scac-mutation-registry.v98.generated.js'
        stale=b'export const SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry.v98";\n// stale\n'
        sealed=b'export const SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry.v98";\n'
        original=integration.check_generated_write
        def advance_after_check(*args):
            original(*args)
            target.write_bytes(sealed); self.commit()
            self.g('update-ref','refs/remotes/origin/main',self.g('rev-parse','HEAD'))
        with patch.object(integration,'check_generated_write',side_effect=advance_after_check):
            with self.assertRaises(MigrationNumberError): integration.write_generated_artifact(self.repo,target,stale)
        self.assertEqual(target.read_bytes(),sealed)
    def test_final_publish_seam_never_replaces_a_new_seal(self):
        target=self.repo/'mcp-server/src/scac-mutation-registry.v98.generated.js'
        stale=b'export const SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry.v98";\n// stale\n'
        sealed=b'export const SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry.v98";\n'
        replace=os.replace; link=os.link
        def promote_then_publish(publish):
            def interleave(source,destination,*args,**kwargs):
                target.write_bytes(sealed); self.commit()
                self.g('update-ref','refs/remotes/origin/main',self.g('rev-parse','HEAD'))
                return publish(source,destination,*args,**kwargs)
            return interleave
        with (patch.object(integration.os,'replace',side_effect=promote_then_publish(replace)),
              patch.object(integration.os,'link',side_effect=promote_then_publish(link))):
            with self.assertRaises(MigrationNumberError):
                integration.write_generated_artifact(self.repo,target,stale)
        self.assertEqual(target.read_bytes(),sealed)

    def test_arbitrary_renderer_cannot_launch_detached_source_writers(self):
        self.install_sink()
        draft=self.repo/'migrations/0749_pending.sql'
        self.write('migrations/0749_pending.sql','select 2;')
        child='import time;time.sleep(0.6);open("migrations/0749_pending.sql","a").write("late")'
        code=f'import subprocess,sys;subprocess.Popen([sys.executable,"-c",{child!r}],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);sys.exit(2)'
        result=subprocess.run([sys.executable,str(self.repo/'tools/integration_candidate.py'),
                               '--base',self.base,'--pending','0749_pending.sql',
                               '--regenerate',json.dumps([sys.executable,'-c',code,'synthetic-private-canary']),
                               '--receipt',str(self.receipt)],cwd=self.repo,env=self.env,capture_output=True,text=True,timeout=10)
        import time; time.sleep(1.0)
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('synthetic-private-canary',result.stdout+result.stderr)
        self.assertEqual(draft.read_text(),'select 2;')
        self.assertFalse(self.receipt.exists())

    def test_poisoned_git_environment_cannot_move_the_bound_repository(self):
        with patch.dict(os.environ,{'GIT_DIR':'/nonexistent-poison','GIT_INDEX_FILE':'/nonexistent-index'}):
            self.assertEqual(integration.allocation_plan(self.repo,self.base,['0749_pending.sql'])['base'],self.base)

    def test_byte_exact_artifact_does_not_replace_its_inode(self):
        target=self.repo/'mcp-server/src/scac-mutation-registry.v97.generated.js'
        before=target.stat()
        integration.write_generated_artifact(self.repo,target,target.read_bytes())
        self.assertEqual(target.stat().st_ino,before.st_ino)
        self.assertEqual(target.stat().st_mtime_ns,before.st_mtime_ns)

    def test_existing_pending_artifact_requires_a_fresh_successor(self):
        target=self.repo/'mcp-server/src/scac-mutation-registry.v98.generated.js'
        self.registry(98); original=target.read_bytes()
        with self.assertRaises(MigrationNumberError):
            integration.write_generated_artifact(self.repo,target,original+b'// new bytes\n')
        self.assertEqual(target.read_bytes(),original)

    def test_ordered_and_overlapping_allocations_preserve_distinct_inputs(self):
        self.write('migrations/0749_main.sql','select 0;');self.commit()
        self.base=self.g('rev-parse','HEAD');self.g('update-ref','refs/remotes/origin/main',self.base)
        drafts={'0749_same.sql':b'select 49;','0750_same.sql':b'select 50;'}
        plan=integration.allocation_plan(self.repo,self.base,list(drafts))
        self.assertEqual(plan['migration_names'],{'0749_same.sql':'0750_same.sql','0750_same.sql':'0751_same.sql'})
        for old,new in plan['migration_names'].items():
            integration.write_generated_artifact(self.repo,self.repo/'migrations'/new,drafts[old])
        integration.write_generated_artifact(self.repo,self.repo/'mcp-server/src/scac-mutation-registry.v98.generated.js',
                                            b'export const SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry.v98";\n')
        for old,new in plan['migration_names'].items():
            self.assertEqual((self.repo/'migrations'/new).read_bytes(),drafts[old])
        self.commit()
        self.assertEqual(integration.validate_candidate(self.repo,self.base)['pending_migrations'],['0750_same.sql','0751_same.sql'])

    def test_wrong_base_and_uncommitted_proof_refuse(self):
        with self.assertRaises(MigrationNumberError):integration.validate_candidate(self.repo,'f'*40)
        self.write('migrations/0749_pending.sql','select 2;')
        with self.assertRaises(MigrationNumberError):integration.validate_candidate(self.repo,self.base)

    def test_machine_codex_hook_projection_does_not_hide_uncommitted_source(self):
        shutil.copyfile(REPO/'.gitignore', self.repo/'.gitignore')
        self.g('add', '.gitignore'); self.g('commit', '-qm', 'Fixture ignore policy')
        hooks = self.repo/'.codex/hooks.json'
        hooks.parent.mkdir(); hooks.write_text('{}\n')
        self.assertEqual(integration.validate_candidate(self.repo, self.base)['pending_migrations'], [])
        for name in ['migrations/0749_pending.sql', '.codex/source.js', 'nested/.codex/hooks.json']:
            source = self.repo/name; source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text('uncommitted source\n')
            with self.subTest(source=name), self.assertRaises(MigrationNumberError):
                integration.validate_candidate(self.repo, self.base)
            source.unlink()


class RestoreForwardTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('integration_local_pg',REPO/'ops/local-pg-ci.py')
        self.pg=importlib.util.module_from_spec(spec);sys.modules[spec.name]=self.pg;spec.loader.exec_module(self.pg)
        self.tmp=tempfile.TemporaryDirectory(prefix='integration-db-adapter-');self.addCleanup(self.tmp.cleanup)
        self.events=[];self.envs=[]
        self.bins=self.pg.PostgresBinaries(*[Path('/fake')/n for n in ['initdb','pg_ctl','createdb','psql']])
    def run_case(self,fail=None,moved=False):
        outer=self;pg=self.pg
        class Runner:
            def run(self,command,*,env=None,cwd=None,capture=False):
                args=tuple(map(str,command));outer.events.append(args);outer.envs.append(dict(env or {}))
                if fail and fail(args,env): return pg.CommandResult(4,'','private-canary-123')
                return pg.CommandResult(0,'{}' if args[-1]=='--fingerprint-only' else '','')
        binding={'base':'a'*40,'head':'b'*40,'tree':'c'*40}
        source=([binding,binding,{**binding,'tree':'d'*40}] if moved=='canonical' else
                [binding,{**binding,'tree':'d'*40}] if moved else [binding,binding,binding])
        with (patch.object(pg,'find_postgres_binaries',return_value=self.bins),
              patch.object(pg,'port_is_available',return_value=True),
              patch.object(pg,'refuse_hosted_execution'),
              patch.object(pg.tempfile,'mkdtemp',return_value=self.tmp.name),
              patch.object(integration,'validate_candidate',side_effect=source),
              patch.object(integration,'git',return_value=b'-- exact current main schema'),
              patch.dict(os.environ,{'CANARY_TOKEN':'private-canary-123'})):
            return pg.run_local_ci(repo=REPO,ci_class='migration',port=55432,runner=Runner(),integration_base='a'*40)
    def test_restore_forward_consumers_precede_canonical_candidate_proof(self):
        self.assertEqual(self.run_case(),0)
        restore=next(i for i,a in enumerate(self.events) if a[-1].endswith('integration-main-schema.sql'))
        forward=next(i for i,a in enumerate(self.events) if a[-3:] == (str(REPO/'tools/migrate.py'),'--apply','--yes'))
        consumers=[i for i,a in enumerate(self.events) if a[-1].endswith(('find-rule-supersedes.test.mjs','catch-me-up-writer-route.test.mjs'))]
        canonical=next(i for i,a in enumerate(self.events) if str(REPO/'ops/ci.sh') in a)
        self.assertLess(restore,forward);self.assertEqual(len(consumers),2)
        # Database names do not isolate cluster-global roles created by migrations.
        self.assertEqual(sum(a[0]=='/fake/initdb' for a in self.events),2)
        self.assertIn(':55433/',self.envs[forward]['DATABASE_URL'])
        self.assertIn(':55432/',self.envs[canonical]['CARR_CI_DATABASE_URL'])
        self.assertEqual(sum(a[0]=='/fake/pg_ctl' and a[-1]=='stop' for a in self.events),3)
        stop_primary=next(i for i,a in enumerate(self.events) if a[0]=='/fake/pg_ctl' and a[-1]=='stop')
        init_integration=[i for i,a in enumerate(self.events) if a[0]=='/fake/initdb'][1]
        self.assertLess(stop_primary,init_integration)
        stop_integration=next(i for i,a in enumerate(self.events) if a[0]=='/fake/pg_ctl' and a[-1]=='stop' and 'integration-data' in ' '.join(a))
        self.assertLess(consumers[-1],stop_integration)
        self.assertLess(stop_integration,canonical)
        self.assertTrue(all(forward<i<canonical for i in consumers))
        self.assertTrue(all('CANARY_TOKEN' not in env for env in self.envs))
    def test_restore_forward_or_consumer_failure_stops_and_disposes(self):
        predicates=[lambda a,e:a[0]=='/fake/pg_ctl' and a[-1]=='stop' and 'integration-data' not in ' '.join(a),
                    lambda a,e:a[0]=='/fake/initdb' and 'integration-data' in ' '.join(a),
                    lambda a,e:a[0]=='/fake/pg_ctl' and a[-1]=='start' and 'integration-data' in ' '.join(a),
                    lambda a,e:a[0]=='/fake/pg_ctl' and a[-1]=='stop' and 'integration-data' in ' '.join(a),
                    lambda a,e:a[-1].endswith('integration-main-schema.sql'),
                    lambda a,e:a[-3:]==(str(REPO/'tools/migrate.py'),'--apply','--yes'),
                    lambda a,e:a[-1].endswith('find-rule-supersedes.test.mjs'),
                    lambda a,e:a[-1].endswith('catch-me-up-writer-route.test.mjs')]
        # Each case gets a new temporary cluster directory after disposal.
        for fail in predicates:
            with self.subTest(fail=fail):
                self.events.clear();self.envs.clear();Path(self.tmp.name).mkdir(exist_ok=True)
                output=io.StringIO()
                with redirect_stdout(output),redirect_stderr(output): self.assertEqual(self.run_case(fail),4)
                self.assertNotIn('private-canary-123',output.getvalue())
                self.assertFalse(any(str(REPO/'ops/ci.sh') in a for a in self.events))
                self.assertTrue(any(a[0]=='/fake/pg_ctl' and a[-1]=='stop' for a in self.events))
    def test_changed_source_refuses_after_canonical_proof(self):
        with self.assertRaises(self.pg.LocalPGRefusal): self.run_case(moved='canonical')
        self.assertTrue(any(str(REPO/'ops/ci.sh') in a for a in self.events))
        self.assertTrue(any(a[0]=='/fake/pg_ctl' and a[-1]=='stop' for a in self.events))

    def test_changed_source_refuses_after_consumer_proof(self):
        with self.assertRaises(self.pg.LocalPGRefusal): self.run_case(moved=True)
        self.assertFalse(any(str(REPO/'ops/ci.sh') in a for a in self.events))
        self.assertTrue(any(a[0]=='/fake/pg_ctl' and a[-1]=='stop' for a in self.events))

if __name__=='__main__': unittest.main()
