#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('migration_gates',ROOT/'ops/ci-migration-gates.py')
assert spec is not None and spec.loader is not None
runner=importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

class Isolation(unittest.TestCase):
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
        bins=runner.postgres_binaries()
        fixture=runner.DisposablePostgres('ci-gate-selftest-', bins/'pg_ctl')
        root=fixture.root
        port=runner.free_port()
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
                    f" assert c.execute('select inet_server_port()').fetchone()[0]!={port}\n"
                    " c.execute('alter role carr_proof bypassrls')\n"
                    f" pathlib.Path({str(root / ('ready'+str(i)))!r}).touch()\n"
                    f" peer=pathlib.Path({str(root / ('ready'+str(1-i)))!r})\n"
                    " deadline=time.monotonic()+30\n"
                    " while not peer.exists() and time.monotonic()<deadline: time.sleep(.05)\n"
                    " assert peer.exists(), 'the private gates did not overlap'\n"
                    " assert c.execute(\"select rolbypassrls from pg_roles where rolname='carr_proof'\").fetchone()==(True,)\n")
                gates.append(p)
            self.assertEqual(runner.run_gates(source,gates,root/'logs',rollback_only={p.name for p in gates}),0)
            result=subprocess.check_output([str(bins/'psql'),'-d',source,'-Atc',
                "select rolbypassrls from pg_roles where rolname='carr_proof'"],text=True)
            self.assertEqual(result.strip(),'f')
            self.assertEqual(len(list((root/'logs').glob('db-gate-*.log'))),2)
        finally:
            fixture.close()

if __name__=='__main__':unittest.main()
