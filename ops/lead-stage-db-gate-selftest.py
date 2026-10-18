#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import subprocess
import unittest
spec=importlib.util.spec_from_file_location('lead_gate',Path(__file__).with_name('lead-stage-db-gate.py'))
assert spec is not None and spec.loader is not None
gate=importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
class GateTest(unittest.TestCase):
    def test_requires_disposable_database(self):
        for env in ({},{'DATABASE_URL':'postgresql://example.test/db'}):
            with self.assertRaises(ValueError): gate.verify(env,lambda *a,**kw:self.fail('must not connect'))
    def test_fixture_failure_is_not_green(self):
        seen=[]
        def run(args,**kwargs):
            seen.append(args)
            return subprocess.CompletedProcess(args,1)
        self.assertEqual(gate.verify({'DATABASE_URL':'postgresql://127.0.0.1/db'},run),1)
        self.assertTrue(seen[0][1].endswith('lead-automation.postgres.mjs'))
    def test_fixture_success_is_required(self):
        self.assertEqual(gate.verify({'DATABASE_URL':'postgresql://localhost/db'},lambda args,**kw:subprocess.CompletedProcess(args,0)),0)
if __name__=='__main__': unittest.main()
