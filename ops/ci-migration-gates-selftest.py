#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('migration_gates',ROOT/'ops/ci-migration-gates.py')
assert spec is not None and spec.loader is not None
runner=importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
from lib.disposable_pg_fixture import postgres_fixture_group

class Isolation(unittest.TestCase):
    def test_snapshots_complete_before_private_clusters_start(self):
        completed=0
        started=threading.Event()
        def snapshot(*args):
            nonlocal completed
            if completed==1:
                started.wait(1)
            completed+=1
            return None, Mock(), None
        def proof(gate, image, log, env):
            started.set()
            self.assertEqual(completed, 2, 'source still needed while private clusters start')
            log.write_text('')
            return gate, 0, 0, log
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(runner, 'snapshot', side_effect=snapshot), \
                patch.object(runner, 'run_isolated', side_effect=proof):
            gates=[Path('a.py'), Path('b.py')]
            self.assertEqual(runner.run_gates('postgres://owner@127.0.0.1:9/carr_ci',
                gates, Path(directory), rollback_only={p.name for p in gates}), 0)
    def test_startup_failure_keeps_diagnostics_after_fixture_cleanup(self):
        bins=runner.postgres_binaries()
        fixture=runner.DisposablePostgres('ci-gate-diagnostic-', bins/'pg_ctl')
        (fixture.root/'postgres.log').write_text('server startup details')
        with tempfile.TemporaryDirectory() as directory:
            log=Path(directory)/'gate.log'
            failure=subprocess.CalledProcessError(1, ['initdb'], stderr=b'resource unavailable')
            with patch.object(fixture, 'run', side_effect=failure):
                with self.assertRaises(RuntimeError):
                    runner.run_isolated(Path('gate.py'), (bins, fixture, 'owner'), log, {})
            self.assertFalse(fixture.root.exists())
            self.assertIn('resource unavailable', log.read_text())
            self.assertIn('server startup details', log.read_text())
    def test_isolated_proofs_hold_the_host_resource_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            gate, logdir = Path(directory) / 'gate.py', Path(directory) / 'logs'
            def proof(gate, image, log, env):
                probe = subprocess.run([runner.sys.executable, '-c',
                    "import fcntl\nfrom lib.disposable_pg_fixture import LOCK_PATH\n"
                    "with LOCK_PATH.open('a') as h:\n"
                    " try: fcntl.flock(h, fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
                    " except BlockingIOError: raise SystemExit(42)\n"],
                    cwd=ROOT, capture_output=True)
                self.assertEqual(probe.returncode, 42, 'host resource lease is not held: ' + probe.stderr.decode())
                log.write_text('')
                return gate, 0, 0, log
            with patch.object(runner, 'snapshot', return_value=(None, Mock(), None)), \
                    patch.object(runner, 'run_isolated', side_effect=proof):
                self.assertEqual(runner.run_gates('postgres://owner@127.0.0.1:9/carr_ci',
                    [gate], logdir, rollback_only={gate.name}), 0)
    def test_non_loopback_source_is_refused_before_any_process(self):
        with self.assertRaises(ValueError):
            runner.run_gates('postgres://owner@example.invalid/carr_ci', [], Path('/tmp'))
    def test_a_red_gate_is_not_hidden_by_other_green_gates(self):
        root=Path(tempfile.mkdtemp(prefix='ci-gate-failure-'))
        try:
            good,bad=root/'good.py',root/'bad.py'
            good.write_text('raise SystemExit(0)\n')
            bad.write_text('raise SystemExit(9)\n')
            self.assertEqual(runner.run_gates('postgres://carr_ci@127.0.0.1:9/carr_ci',
                [good,bad],root/'logs',rollback_only=set()),1)
        finally:runner.quarantine(root)

    def test_two_concurrent_gates_get_snapshot_data_and_private_role_catalogs(self):
        with postgres_fixture_group():
            self.prove_two_concurrent_gates()

    def prove_two_concurrent_gates(self):
        bins=runner.postgres_binaries()
        fixture=runner.DisposablePostgres('ci-gate-selftest-', bins/'pg_ctl')
        root=fixture.root
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port=sock.getsockname()[1]
        source=f'postgres://carr_ci@127.0.0.1:{port}/carr_ci'
        def run(*args):
            fixture.run(args,check=True,capture_output=True)
        try:
            run(bins/'initdb','-D',root/'data','-U','carr_ci','--auth=trust','--no-locale','--encoding=UTF8')
            run(bins/'pg_ctl','-D',root/'data','-l',root/'postgres.log','-o',f'-h 127.0.0.1 -p {port} -k {root}', '-w','start')
            run(bins/'createdb','--maintenance-db',f'postgres://carr_ci@127.0.0.1:{port}/postgres','carr_ci')
            run(bins/'psql','-d',source,'-v','ON_ERROR_STOP=1','-c',
                'create table proof(n int); insert into proof values(42); create role carr_proof; create role carr_peer; grant carr_proof to carr_peer granted by carr_ci;')
            gates=[]
            for i in range(2):
                p=root/f'gate{i}.py'
                p.write_text("import os,psycopg,time,pathlib\n"
                    "with psycopg.connect(os.environ['DATABASE_URL']) as c:\n"
                    " assert c.execute('select n from proof').fetchone()==(42,)\n"
                    " assert c.execute('select inet_server_port()').fetchone()[0] is None\n"
                    " c.execute('alter role carr_proof bypassrls')\n"
                    f" pathlib.Path({str(root / ('ready'+str(i)))!r}).touch()\n"
                    f" peer=pathlib.Path({str(root / ('ready'+str(1-i)))!r})\n"
                    " deadline=time.monotonic()+30\n"
                    " while not peer.exists() and time.monotonic()<deadline: time.sleep(.05)\n"
                    " assert peer.exists(), 'the private gates did not overlap'\n"
                    " assert c.execute(\"select rolbypassrls from pg_roles where rolname='carr_proof'\").fetchone()==(True,)\n")
                gates.append(p)
            snapshot=runner.snapshot
            copies=0
            paused=False
            def capture(*args):
                nonlocal copies, paused
                image=snapshot(*args)
                copies+=1
                if copies==len(gates):
                    run(bins/'pg_ctl','-D',root/'data','-m','fast','-w','stop')
                    paused=True
                return image
            with patch.object(runner, 'snapshot', side_effect=capture):
                try:
                    self.assertEqual(runner.run_gates(source,gates,root/'logs',rollback_only={p.name for p in gates}),0)
                finally:
                    if paused:
                        run(bins/'pg_ctl','-D',root/'data','-l',root/'postgres.log','-o',f'-h 127.0.0.1 -p {port} -k {root}', '-w','start')
            result=subprocess.check_output([str(bins/'psql'),'-d',source,'-Atc',
                "select rolbypassrls from pg_roles where rolname='carr_proof'"],text=True)
            self.assertEqual(result.strip(),'f')
            self.assertEqual(len(list((root/'logs').glob('db-gate-*.log'))),2)
        finally:
            fixture.close()

class Wr258Overlap(unittest.TestCase):
    """WR-000258: isolated gates run beside the serial gates they do not depend on."""
    def gates(self, root, committing=()):  # committing = gates marked as needed by the isolated gates
        out=[]
        for name in ('a-gate.py','b-gate.py','c-gate.py','d-gate.py'):
            p=root/name
            p.write_text(('# ci: db-gate-before-isolated\n' if name in committing else '# ci: db-gate\n')+'raise SystemExit(0)\n')
            out.append(p)
        return out
    def fake_isolated(self, order, release, code=0):
        def proof(gate, image, log, env):
            order.append('isolated-start')
            release.wait(5)
            order.append('isolated-end')
            log.write_text('')
            return gate, code, 0, log
        return proof
    def test_committing_gates_run_first_then_isolated_gates_overlap_the_rest(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); gates=self.gates(root, committing=('b-gate.py',))
            iso=root/'zz-iso-gate.py'; iso.write_text('# ci: db-gate\n')
            order=[]; release=threading.Event()
            real=runner.run_gate
            def run_gate(gate, dsn, log, env):
                order.append(f'serial:{gate.name}')
                if gate.name=='d-gate.py':
                    # the tail's last gate: the isolated gate must already be running
                    self.assertIn('isolated-start', order)
                    release.set()
                return real(gate, dsn, log, env)
            with patch.object(runner,'snapshot',return_value=(None,Mock(),None)), \
                    patch.object(runner,'run_isolated',side_effect=self.fake_isolated(order,release)), \
                    patch.object(runner,'run_gate',side_effect=run_gate):
                self.assertEqual(runner.run_gates('postgres://owner@127.0.0.1:9/carr_ci',
                    gates+[iso], root/'logs', rollback_only={iso.name}), 0)
            # a and b (up to the committing gate) finish before the snapshot starts
            self.assertLess(order.index('serial:b-gate.py'), order.index('isolated-start'))
            self.assertLess(order.index('isolated-start'), order.index('serial:a-gate.py'))
            self.assertLess(order.index('serial:a-gate.py'), order.index('serial:c-gate.py'), 'rollback-only gates keep glob order')
    def test_without_isolated_gates_everything_stays_serial_in_glob_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); gates=self.gates(root)
            with patch.object(runner,'snapshot',side_effect=AssertionError('no isolated gate, no snapshot')), \
                    patch.object(runner,'run_gate',side_effect=lambda g,d,l,e:(l.write_text('') or (g,0,0,l))):
                self.assertEqual(runner.run_gates('postgres://owner@127.0.0.1:9/carr_ci', gates, root/'logs', rollback_only=set()),0)

class Wr258Coverage(unittest.TestCase):
    """A sharded run that skips, repeats or invents a scenario or call is a failure."""
    DECLARED='gate-scenarios-declared: a b c\n'
    def test_scenarios_must_be_run_exactly_once(self):
        ok=[self.DECLARED+'gate-scenario-ran: a\ngate-scenario-ran: b\n', self.DECLARED+'gate-scenario-ran: c\n']
        self.assertEqual(runner.scenario_coverage(ok),'')
        self.assertIn('not run: c', runner.scenario_coverage(ok[:1]))           # a shard went missing
        self.assertIn('not run: b', runner.scenario_coverage([ok[0].replace('gate-scenario-ran: b\n',''),ok[1]]))
        self.assertIn('run more than once: a', runner.scenario_coverage(ok+[self.DECLARED+'gate-scenario-ran: a\n']))
        self.assertIn('not declared: z', runner.scenario_coverage(ok+[self.DECLARED+'gate-scenario-ran: z\n']))
        self.assertIn('no shard declared', runner.scenario_coverage(['gate-scenario-ran: a\n']))
        self.assertIn('disagree', runner.scenario_coverage([self.DECLARED, 'gate-scenarios-declared: a b\n']))
    def test_a_red_shard_makes_the_sharded_gate_red_and_the_log_names_each_shard(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); log=root/'db-gate-x.log'; gate=root/'siep12-policy-epoch-local-pg-gate.py'
            outcomes={'0/2':0,'1/2':3}
            def run_gate(g,dsn,l,env):
                l.write_text(self.DECLARED+('gate-scenario-ran: a\ngate-scenario-ran: b\ngate-scenario-ran: c\n' if env['CARR_GATE_SHARD']=='0/2' else ''))
                return g,outcomes[env['CARR_GATE_SHARD']],1,l
            with patch.object(runner,'checked'), patch.object(runner,'run_gate',side_effect=run_gate):
                _,rc,_,_=runner.run_sharded(gate,2,Path('/bin'),'owner','postgres://owner@/{database}',{},log)
            self.assertEqual(rc,3)
            self.assertIn('==== shard 1/2 ====',log.read_text())
    def test_a_missing_scenario_fails_a_sharded_gate_that_every_shard_passed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); log=root/'db-gate-x.log'; gate=root/'siep12-policy-epoch-local-pg-gate.py'
            def run_gate(g,dsn,l,env):
                l.write_text(self.DECLARED+'gate-scenario-ran: a\ngate-scenario-ran: b\n')   # nobody ran c
                return g,0,1,l
            with patch.object(runner,'checked'), patch.object(runner,'run_gate',side_effect=run_gate):
                _,rc,_,_=runner.run_sharded(gate,1,Path('/bin'),'owner','postgres://owner@/{database}',{},log)
            self.assertEqual(rc,1)
            self.assertIn('SHARD COVERAGE FAILURE',log.read_text())
    def test_a_sql_proof_speaks_the_same_protocol_through_psql_notices(self):
        notice=lambda text:f'psql:dot.sql:9: NOTICE:  {text}\n'
        declared=notice('gate-scenarios-declared: f(int) g(text,text)')
        ok=[declared+notice('gate-scenario-ran: f(int)'), declared+notice('gate-scenario-ran: g(text,text)')]
        self.assertEqual(runner.scenario_coverage(ok),'')
        self.assertIn('not run: g(text,text)',runner.scenario_coverage(ok[:1]))
        self.assertIn('run more than once: f(int)',runner.scenario_coverage(ok+[declared+notice('gate-scenario-ran: f(int)')]))

class Wr258Siep12Plan(unittest.TestCase):
    def test_every_scenario_lands_in_exactly_one_shard_for_any_shard_count(self):
        import sys as _sys
        _sys.path.insert(0,str(ROOT/'ops'))
        gspec=importlib.util.spec_from_file_location('siep12_gate',ROOT/'ops/siep12-policy-epoch-local-pg-gate.py')
        gate=importlib.util.module_from_spec(gspec); gspec.loader.exec_module(gate)
        self.assertEqual(len(set(gate.ALL_SCENARIO_NAMES)),len(gate.ALL_SCENARIO_NAMES))
        self.assertEqual(gate.ALL_SCENARIO_NAMES[-1],'runtime_role_privileges','the privilege scenario leaves grants behind and must stay last')
        for shards in range(1,6):
            plan=gate.plan_shards(shards)
            flat=[n for shard in plan for n in shard]
            self.assertEqual(sorted(flat),sorted(gate.ALL_SCENARIO_NAMES),f'{shards} shards')
            self.assertIn(gate.STATIC_SCENARIO[0],plan[0])
        self.assertEqual(gate.parse_shard('1/3'),(1,3))
        with self.assertRaises(ValueError): gate.parse_shard('3/3')

class Wr258SqlProofRunner(unittest.TestCase):
    PROOF="""\\if :{?shards}\n\\else\n\\set shards 1\n\\set shard 0\n\\endif\nbegin;\ninsert into marks values (:shard);\n"""
    def test_shards_run_on_private_copies_that_are_dropped_and_coverage_is_enforced(self):
        with postgres_fixture_group():
            bins=runner.postgres_binaries()
            fixture=runner.DisposablePostgres('ci-sql-proof-selftest-', bins/'pg_ctl')
            root=fixture.root
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port=sock.getsockname()[1]
            source=f'postgres://carr_ci@127.0.0.1:{port}/carr_ci'
            def run(*args): fixture.run(args,check=True,capture_output=True)
            try:
                run(bins/'initdb','-D',root/'data','-U','carr_ci','--auth=trust','--no-locale','--encoding=UTF8')
                run(bins/'pg_ctl','-D',root/'data','-l',root/'postgres.log','-o',f'-h 127.0.0.1 -p {port} -k {root}','-w','start')
                run(bins/'createdb','--maintenance-db',f'postgres://carr_ci@127.0.0.1:{port}/postgres','carr_ci')
                run(bins/'psql','-d',source,'-v','ON_ERROR_STOP=1','-c','create table marks(n int)')
                good=root/'good.sql'
                good.write_text(self.PROOF+"commit;\n\\echo gate-scenarios-declared: s0 s1\n\\echo gate-scenario-ran: s:shard\n")
                # every shard commits a row into ITS OWN copy; the source must stay empty
                self.assertEqual(runner.run_sql_proofs(source,[(good,2)],root/'logs',str(bins/'psql')),0)
                rows=subprocess.check_output([str(bins/'psql'),'-d',source,'-Atc','select count(*) from marks'],text=True).strip()
                self.assertEqual(rows,'1','shard 0 uses the database itself, the others private copies')
                names=subprocess.check_output([str(bins/'psql'),'-d',source,'-Atc','select datname from pg_database'],text=True).split()
                self.assertFalse([n for n in names if n.startswith('ci_proof_')],'copies were not dropped')
                # mutation: a shard that skips its calls (executed 0) must turn the run red
                skip=root/'skip.sql'
                skip.write_text("\\if :{?shards}\n\\else\n\\set shards 1\n\\set shard 0\n\\endif\n\\echo gate-scenarios-declared: s0 s1\n\\echo gate-scenario-ran: s0\n")
                self.assertEqual(runner.run_sql_proofs(source,[(skip,2)],root/'logs2',str(bins/'psql')),1)
                # mutation: a failing statement in any shard is red
                bad=root/'bad.sql'
                bad.write_text("\\set ON_ERROR_STOP on\nselect 1/0;\n")
                self.assertEqual(runner.run_sql_proofs(source,[(bad,1)],root/'logs3',str(bins/'psql')),1)
            finally:
                fixture.close()

if __name__=='__main__':unittest.main()
