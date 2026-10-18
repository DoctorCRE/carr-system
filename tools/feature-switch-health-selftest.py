#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import unittest
import json
import subprocess
import sys
import tempfile
p=Path(__file__).with_name('feature-switch-health.py')
spec=importlib.util.spec_from_file_location('feature_health',p)
assert spec is not None and spec.loader is not None
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Health(unittest.TestCase):
    def test_malformed_rows_are_rejected_at_render_interface(self):
        for row in ('x', None, [], 1):
            with self.subTest(row=row):
                self.assertRaises(ValueError,m.render,{'ok':True,'overdue':[row],'line':m.BOUND})

    def test_malformed_fixture_is_unknown_with_exit_two(self):
        for fixture in ([], {'feature_switches':{'ok':True,'overdue':['x'],'line':m.BOUND}}):
            with self.subTest(fixture=fixture), tempfile.TemporaryDirectory() as directory:
                fixture_path=Path(directory)/'health.json'
                fixture_path.write_text(json.dumps(fixture))
                result=subprocess.run([sys.executable,str(p),'--fixture',str(fixture_path)],capture_output=True,text=True)
                self.assertEqual(result.returncode,2,result.stderr)
                self.assertIn('UNKNOWN feature switches:',result.stdout)
                self.assertIn(m.BOUND,result.stdout)
                self.assertNotIn('Traceback',result.stderr)

    def test_bound_response_and_unreadable_never_zero(self):
        self.assertRaises(ValueError,m.render,{'ok':False})
        self.assertRaises(ValueError,m.render,{'ok':True,'overdue':[]})
        payload={'ok':True,'overdue':[],'line':'OK feature switches: 0 overdue · on breach: owner loop; retire; verify; auto-clear'}
        self.assertEqual(m.render(payload),(0,[payload['line']]))
        payload['overdue']=[{'line':'WARN first · on breach: loop 1, owner claude; retire; verify; auto-clear','loop_id':'test'}]
        self.assertEqual(m.render(payload)[0],1)
        self.assertEqual(len(m.render(payload)[1]),2)
if __name__=='__main__':unittest.main()
