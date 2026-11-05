#!/usr/bin/env python3
"""Synthetic transition tests for immutable natural email intent."""
import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.email_touch_journal import Cohort, canonical, projection, capture_key, Refused

SHA = 'a' * 40
KEY = str(uuid4())
ACTOR = str(uuid4())
TARGET = str(uuid4())

class JournalTest(unittest.TestCase):
    def setUp(self):
        self.state = {}
        self.saves = []
        self.calls = []
        self.request = dict(idempotency_key=KEY, ref='C-901', kind='email_in',
                            occurred_at='2026-11-01T12:00:00.000Z', summary='private', detail='secret')
        self.meta = dict(principal_slug='joe', producer_sha=SHA, mailbox_scope_hash='b'*64,
                         execution_id=str(uuid4()), source_id='outlook-intake:v1:'+'c'*64,
                         source_version='sha256:'+'d'*64, projection_digest='d'*64,
                         message_hash='e'*64)
        self.cohort = Cohort(self.state, lambda: self.saves.append(json.loads(json.dumps(self.state))))

    def call(self, verb, args):
        self.calls.append((verb, args))
        if verb == 'capture-email-touch-journal':
            effect = dict(source_id=args['source_id'], source_version=args['source_version'],
                          original_key=KEY, role='client', target_type='client', target_id=TARGET,
                          occurred_at=self.request['occurred_at'])
            raw = canonical(dict(schema='email-touch-expectations.v1', principal_slug='joe', actor_id=ACTOR,
                actor_slug='joe-local', tenant='carr-internal', release_sha=SHA, start=effect['occurred_at'],
                end='2026-11-01T12:00:00.001Z', expected_count=1, expected=[effect]))
            digest = hashlib.sha256(raw).hexdigest()
            return dict(ok=True, raw_base64=base64.b64encode(raw).decode(), digest=digest,
                        reference='sha256:'+digest, actor_id=ACTOR, actor_slug='joe-local')
        return dict(ok=True)

    def select(self):
        self.cohort.select(self.meta, [self.request])

    def test_waiting_frozen_filing_acknowledged_available_and_read(self):
        self.select()
        self.assertNotIn('requests', self.state)
        self.assertTrue(self.cohort.recover(self.call, lambda p: self.call('log-activity', p), SHA))
        self.assertEqual([x[0] for x in self.calls], ['capture-email-touch-journal', 'log-activity', 'record-email-touch-journal-outcome'])
        self.assertEqual(self.state['email_cohort']['phase'], 'available')
        self.assertEqual(self.calls[1][1]['email_touch']['role'], 'client')
        self.assertTrue(self.cohort.recover(self.call, lambda p: self.fail('duplicate application'), SHA))
        self.assertEqual(len(self.calls), 3)

    def test_capture_failure_never_files_and_second_failure_opens_breaker(self):
        self.select()
        def down(*_): raise OSError('synthetic timeout')
        for _ in range(2):
            self.assertFalse(self.cohort.recover(down, lambda p: self.fail('uncustodied application'), SHA))
        self.assertEqual(self.state['email_cohort']['phase'], 'held')
        self.assertFalse(self.cohort.recover(self.call, lambda p: self.fail('open breaker'), SHA))
        self.assertEqual(self.calls, [])

    def test_uncertain_retries_same_keys_then_holds(self):
        self.select()
        def apply(p):
            self.calls.append(('apply', p)); return False
        for _ in range(2): self.cohort.recover(self.call, apply, SHA)
        self.assertEqual(self.state['email_cohort']['phase'], 'held')
        self.assertEqual([x[1]['idempotency_key'] for x in self.calls if x[0]=='apply'], [KEY, KEY])

    def test_release_and_integrity_refuse(self):
        self.select()
        self.assertFalse(self.cohort.recover(self.call, lambda p: self.fail('stale'), 'f'*40))
        self.assertEqual(self.state['email_cohort']['phase'], 'held')
        self.assertEqual(self.calls, [])
        self.state['email_cohort']['phase'] = 'pending'
        def forged(v,a):
            r=self.call(v,a); r['digest']='0'*64; return r
        self.assertFalse(self.cohort.recover(forged, lambda p: self.fail('bad bytes'), SHA))

    def test_outcome_failure_preserves_expectations_and_no_refiling(self):
        self.select()
        def fail_outcome(v,a):
            if v=='record-email-touch-journal-outcome': raise OSError('timeout')
            return self.call(v,a)
        self.cohort.recover(fail_outcome, lambda p: True, SHA)
        before=json.dumps(self.state['email_cohort']['receipt'], sort_keys=True)
        self.assertTrue(Cohort(self.state, lambda: None).recover(self.call, lambda p: self.fail('already applied'), SHA))
        self.assertEqual(before, json.dumps(self.state['email_cohort']['receipt'], sort_keys=True))

    def test_canonical_refusal_holds_and_keeps_full_cohort(self):
        self.select()
        def refused(p):
            self.state.setdefault('requests',{})[KEY]={'disposition':'blocked'}
            return False
        self.assertFalse(self.cohort.recover(self.call,refused,SHA))
        self.assertEqual(self.state['email_cohort']['phase'],'held')
        self.assertEqual(len(self.state['email_cohort']['proposals']),1)
        self.assertEqual(self.calls[-1][1]['observations'][0]['status'],'refused')

    def test_acknowledgement_with_refused_outcome_holds(self):
        self.select()
        def transport(v,a):
            if v=='record-email-touch-journal-outcome':return dict(ok=False)
            return self.call(v,a)
        self.assertFalse(self.cohort.recover(transport,lambda p:True,SHA))
        self.assertEqual(self.state['email_cohort']['phase'],'held')

    def test_diagnosed_recovery_and_available_integrity_failure(self):
        self.select()
        self.cohort.recover(self.call,lambda p: True,'f'*40)
        self.cohort.resume_after_diagnosis('Installed and Worker release now agree')
        self.assertTrue(self.cohort.recover(self.call,lambda p: True,SHA))
        self.state['email_cohort']['receipt']['digest']='0'*64
        self.assertFalse(self.cohort.recover(self.call,lambda p: self.fail('duplicate'),SHA))
        self.assertEqual(self.state['email_cohort']['phase'],'held')

    def test_real_intake_write_uses_frozen_payload_and_saves_before_transport(self):
        from datetime import datetime,timezone
        from types import SimpleNamespace
        from lib import outlook_intake as intake
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            mail=dict(ident='natural:1793534400',local_keys=['101'],sent_unix=1793534400,sender='a@example.test',
                      recipients='b@example.test',subject='secret subject',text='secret body',full=True)
            def transport(verb,args):
                saved=json.loads((out/intake.STATE).read_text())
                if verb=='log-activity':
                    self.assertIn('receipt',saved['email_cohort'])
                    self.assertEqual(json.loads(saved['requests'][KEY]['payload']),args)
                    self.assertEqual(args['ref'],TARGET)
                    self.assertIn('email_touch',args)
                    return dict(ok=True,activity_id=str(uuid4()))
                return self.call(verb,args)
            identity=lambda:dict(principal_slug='joe',producer_sha=SHA,release_sha=SHA)
            ports=intake.Ports(carr=transport,contacts=lambda:[],decode=lambda *a:None,
                              now=lambda:datetime.now(timezone.utc),journal_identity=identity)
            settings=SimpleNamespace(owner='joe',account='demo@example.test',timezone=timezone.utc)
            work=intake.Run(ports,self.state,ports.now(),out,settings)
            result=work.journal_email(mail,['C-901'],[self.request],False,[])
            self.assertEqual(result,[True])
            self.assertEqual(self.state['email_cohort']['phase'],'available')
            self.assertTrue(work.journal_recover())
            self.assertEqual(len(self.state['requests']),1)

    def test_selection_is_immutable_and_bounds(self):
        self.select()
        with self.assertRaises(Refused): self.cohort.select(self.meta, [{**self.request,'ref':'C-902'}])
        for proposals in ([], [self.request]*501):
            with self.assertRaises(Refused): Cohort({},lambda:None).select(self.meta, proposals)
        self.assertEqual(capture_key('b'*64,SHA),capture_key('b'*64,SHA))

    def test_projection_does_not_depend_on_targets_or_outcomes(self):
        mail=dict(ident='natural',local_keys=['store:1'],sent_unix=1793534400,sender='a@example.test',
                  recipients='b@example.test',subject='secret subject',text='secret body',full=True)
        a=projection('joe','TEST@example.test',mail)
        b=projection('joe','test@example.test',{**mail,'targets':['C-9'],'acknowledged':True})
        self.assertEqual(a,b)
        self.assertNotIn('secret',json.dumps(a))

class ExportTest(unittest.TestCase):
    def setUp(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('journal_proof',Path(__file__).with_name('email-touch-journal-proof.py'))
        self.proof=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.proof)

    def test_consumer_nonzero_cannot_become_success(self):
        with tempfile.TemporaryDirectory() as directory:
            script=Path(directory)/'consumer.py'
            script.write_text('import json\nprint(json.dumps(dict(schema="email-touch-cohort-proof.v1",mode="production",verdict="proven",reason="matched",counts=dict(expected=1,applied=1))))\nraise SystemExit(1)\n')
            with self.assertRaisesRegex(Refused,'consumer_refused'):
                self.proof.reconcile([sys.executable,str(script)])

    def test_consumer_output_is_bounded_during_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            script=Path(directory)/'consumer.py'
            script.write_text('print("x"*5000)\n')
            with self.assertRaisesRegex(Refused,'consumer_output_cap'):
                self.proof.reconcile([sys.executable,str(script)])

    def test_private_exact_byte_export_and_conflict_refusal(self):
        import os
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'export'
            case=JournalTest();case.setUp();case.select()
            case.cohort.recover(case.call,lambda p:True,SHA)
            receipt=case.state['email_cohort']['receipt']
            raw=base64.b64decode(receipt['raw_base64'])
            row=dict(raw=raw,digest=receipt['digest'],producer_sha=SHA,principal_slug='joe',actor_id=ACTOR,
                tenant='carr-internal',capture_key=str(uuid4()),captured_at='2026-11-01T12:01:00Z',
                mailbox_scope_hash='b'*64,input_digest='c'*64,provenance=dict(selected_keys=[KEY],source_id=case.meta['source_id'],source_version=case.meta['source_version']))
            argv,count=self.proof.export(row,target,Path('/synthetic/consumer.py'))
            self.assertEqual(count,1)
            self.assertEqual((target/'journal.json').read_bytes(),raw)
            self.assertEqual((target/'journal.json').stat().st_mode & 0o777,0o600)
            self.assertEqual(target.stat().st_mode & 0o777,0o700)
            self.assertEqual(self.proof.export(row,target,Path('/synthetic/consumer.py')),(argv,count))
            (target/'journal.json').write_bytes(b'changed')
            with self.assertRaisesRegex(Refused,'export_conflict'):
                self.proof.export(row,target,Path('/synthetic/consumer.py'))
            link=Path(directory)/'link'
            link.symlink_to(target,target_is_directory=True)
            with self.assertRaisesRegex(Refused,'unsafe_export'):
                self.proof.export(row,link,Path('/synthetic/consumer.py'))

if __name__ == '__main__': unittest.main()
