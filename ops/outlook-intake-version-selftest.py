#!/usr/bin/env python3
"""Synthetic receipt tests at the intake consumer seam. No real bundle or store."""
from datetime import timedelta
import importlib.util
import hashlib
import json
from pathlib import Path
import plistlib
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from lib import outlook_version_tripwire as tripwire
spec=importlib.util.spec_from_file_location('intake_fixture',ROOT/'ops/outlook-intake-selftest.py')
assert spec and spec.loader
fixture=importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
VERSION=fixture.VERSION
PIN=fixture.PIN
accept=fixture.accept_version

class IntakeVersionTest(unittest.TestCase):
    def setUp(self):
        self.t=fixture.IntakeTest();self.t.setUp();self.addCleanup(self.t.doCleanups)
        self.state=self.t.tmp/'consumer-version'
        self.version=dict(VERSION)
        self.pin=PIN
        self.ports=self.t.fake.ports()
        self.ports.version_gate=lambda:tripwire.require_accepted(self.state,self.version,self.pin)
        fixture.write_db(self.t.fake.fixture,
            [fixture.mail('pat@example.test',-timedelta(hours=1))],
            [fixture.event('Synthetic GOID',-timedelta(hours=2),attendees=[('Pat Example','pat@example.test')])])

    def run_intake(self):
        return fixture.intake.run(self.t.repo,self.t.home,self.ports)

    def assert_held(self):
        result=self.run_intake()
        self.assertEqual((result.state,result.reason),('failed','version_not_accepted'))
        self.assertEqual(self.t.fake.decodes,0)
        self.assertEqual(self.t.launch_calls,[])
        self.assertEqual(self.t.fake.calls,[])
        self.assertEqual(self.t.fake.model_calls,[])
        self.assertFalse(self.t.out().exists())

    def test_missing_receipt_blocks_before_snapshot_export_or_intake(self):
        self.assert_held()

    def test_exact_complete_accepted_receipt_allows_mail_and_event(self):
        accept(self.state)
        result=self.run_intake()
        self.assertEqual(result.state,'succeeded',result)
        self.assertEqual(len(self.t.fake.writes('log-activity')),2)
        self.assertEqual(len(self.t.fake.model_calls),1)

    def test_changed_version_or_source_cannot_reuse_acceptance(self):
        accept(self.state)
        self.version['build']='25101417'
        self.assert_held()
        self.version=dict(VERSION);self.pin='b'*64
        self.assert_held()

    def test_incomplete_or_modified_observation_blocks(self):
        accept(self.state)
        accepted=json.loads((self.state/'accepted.json').read_text())
        path=self.state/'snapshots'/accepted['id']/'observation.json'
        report=json.loads(path.read_text());report['coverage']['unverified_blocks']=1
        path.write_text(json.dumps(report))
        self.assert_held()

    def test_changed_version_blocks_unchanged_store_refresh_and_retry(self):
        accept(self.state)
        self.assertEqual(self.run_intake().state,'succeeded')
        path=self.t.out()/fixture.intake.STATE
        before=path.read_bytes();decodes=self.t.fake.decodes
        self.t.fake.calls.clear();self.t.fake.model_calls.clear();self.t.launch_calls.clear()
        self.version['version']='16.103'
        result=self.run_intake()
        self.assertEqual(result.reason,'version_not_accepted')
        self.assertEqual(self.t.fake.decodes,decodes)
        self.assertEqual(self.t.launch_calls,[])
        self.assertEqual(self.t.fake.calls,[])
        self.assertEqual(self.t.fake.model_calls,[])
        self.assertEqual(path.read_bytes(),before)

    def test_version_change_after_snapshot_blocks_export(self):
        accept(self.state)
        launch=self.ports.launchctl
        def changed(*args,**kwargs):
            result=launch(*args,**kwargs)
            self.version['build']='25101417'
            return result
        self.ports.launchctl=changed
        result=self.run_intake()
        self.assertEqual(result.reason,'version_not_accepted')
        self.assertEqual(self.t.fake.decodes,0)
        self.assertEqual(self.t.fake.calls,[])
        self.assertEqual(self.t.fake.model_calls,[])
        self.assertEqual(list(self.t.out().glob('work-*')),[])
        self.assertFalse((self.t.out()/fixture.intake.STATE).exists())

    def test_source_change_during_export_blocks_intake(self):
        accept(self.state)
        self.t.fake.on_decode=lambda _:setattr(self,'pin','b'*64)
        result=self.run_intake()
        self.assertEqual(result.reason,'version_not_accepted')
        self.assertEqual(self.t.fake.decodes,1)
        self.assertEqual(self.t.fake.calls,[])
        self.assertEqual(self.t.fake.model_calls,[])
        self.assertFalse((self.t.out()/fixture.intake.STATE).exists())

    def test_changed_version_holds_pending_write_preview_and_model_retry(self):
        accept(self.state)
        self.t.fake.fixture.unlink()
        fixture.write_db(self.t.fake.fixture,[fixture.mail('pat@example.test',-timedelta(hours=1),kind='preview')])
        self.t.fake.fail_verbs.add('log-activity')
        self.assertEqual(self.run_intake().reason,'writes_failed')
        path=self.t.out()/fixture.intake.STATE
        saved=json.loads(path.read_text())
        self.assertTrue(saved['pending_full'])
        self.assertTrue(saved['steps'])
        self.assertTrue(any(not r['acknowledged'] for r in saved['requests'].values()))
        before=path.read_bytes();decodes=self.t.fake.decodes
        self.version['build']='25101417'
        self.t.fake.calls.clear();self.t.fake.model_calls.clear();self.t.launch_calls.clear()
        result=self.run_intake()
        self.assertEqual(result.reason,'version_not_accepted')
        self.assertEqual(self.t.fake.decodes,decodes)
        self.assertEqual(self.t.fake.calls,[])
        self.assertEqual(self.t.fake.model_calls,[])
        self.assertEqual(self.t.launch_calls,[])
        self.assertEqual(path.read_bytes(),before)

    def test_source_change_before_record_write_blocks_persistence(self):
        accept(self.state)
        contacts=self.ports.contacts
        def changed():
            self.pin='b'*64
            return contacts()
        self.ports.contacts=changed
        result=self.run_intake()
        self.assertEqual(result.reason,'version_not_accepted')
        self.assertEqual(self.t.fake.writes(),[])
        self.assertEqual(self.t.fake.model_calls,[])
        self.assertFalse((self.t.out()/fixture.intake.STATE).exists())

    def test_source_change_during_model_call_does_not_save_reply_or_set_ball(self):
        accept(self.state)
        def changed(mail,today):
            self.pin='b'*64
            return {'next_step':'Synthetic follow-up','due_on':None}
        self.ports.next_step=changed
        result=self.run_intake()
        self.assertEqual(result.reason,'version_not_accepted')
        self.assertEqual(self.t.fake.writes('set-next-action'),[])
        saved=json.loads((self.t.out()/fixture.intake.STATE).read_text())
        self.assertTrue(saved['steps'])
        self.assertTrue(all('reply' not in s for s in saved['steps'].values()))

    def configure_consumer(self):
        from lib.outlook_version_ports import source_binding
        state=self.t.repo/'out/outlook-version-tripwire'
        state.mkdir(parents=True)
        app=self.t.tmp/'Synthetic.app'
        (app/'Contents').mkdir(parents=True)
        (app/'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleShortVersionString':VERSION['version'],'CFBundleVersion':VERSION['build']}))
        binary=self.t.home/fixture.intake.HXPROBE
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'Synthetic observer; never executed')
        sha=hashlib.sha256(binary.read_bytes()).hexdigest()
        config={'app':str(app),'observer':str(binary),'observer_sha256':sha}
        (state/'consumer.json').write_text(json.dumps(config))
        pin,_=source_binding(binary,sha)
        accept(state,pin=pin)
        self.ports.version_gate=fixture.intake._version_gate(self.t.repo,self.t.home)
        return state,config,binary,app

    def test_production_adapter_requires_explicit_consumer_config(self):
        self.ports.version_gate=fixture.intake._version_gate(self.t.repo,self.t.home)
        self.assert_held()

    def test_production_adapter_binds_installed_metadata_decoder_and_current_sources(self):
        _,_,binary,_=self.configure_consumer()
        result=self.run_intake()
        self.assertEqual(result.state,'succeeded',result)
        self.assertEqual(len(self.t.fake.writes('log-activity')),2)
        before=(self.t.out()/fixture.intake.STATE).read_bytes()
        binary.write_bytes(b'Changed synthetic observer')
        self.t.fake.calls.clear();self.t.fake.model_calls.clear()
        result=self.run_intake()
        self.assertEqual(result.reason,'version_not_accepted')
        self.assertEqual(self.t.fake.calls,[])
        self.assertEqual(self.t.fake.model_calls,[])
        self.assertEqual((self.t.out()/fixture.intake.STATE).read_bytes(),before)

    def test_production_adapter_rejects_other_decoder_and_version(self):
        state,config,_,app=self.configure_consumer()
        config['observer']=str(self.t.tmp/'different-observer')
        (state/'consumer.json').write_text(json.dumps(config))
        self.assert_held()
        config['observer']=str(self.t.home/fixture.intake.HXPROBE)
        (state/'consumer.json').write_text(json.dumps(config))
        (app/'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleShortVersionString':'16.103','CFBundleVersion':VERSION['build']}))
        self.assert_held()

    def test_monitor_and_intake_share_the_snapshot_executor_seam(self):
        from lib.outlook_version_ports import OperationalPorts
        _,config,binary,app=self.configure_consumer()
        adapter=OperationalPorts(self.t.home,app,binary,config['observer_sha256'])
        work=self.t.out()/'work-monitor'
        work.mkdir(parents=True)
        with patch.object(fixture.intake.subprocess,'run',side_effect=self.t.launchctl):
            adapter.snapshot(work)
        self.assertEqual((work/'store.hxd').read_bytes(),self.t.store.read_bytes())
        self.assertEqual([c[1] for c in self.t.launch_calls],['bootstrap','print','bootout'])



if __name__=='__main__':
    unittest.main()
