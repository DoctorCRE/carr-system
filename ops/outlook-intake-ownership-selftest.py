#!/usr/bin/env python3
"""Binary-derived synthetic ownership qualification. No Outlook or live ports."""
from datetime import timedelta
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import outlook_intake as intake
spec = importlib.util.spec_from_file_location('intake_fixture', ROOT / 'ops/outlook-intake-selftest.py')
assert spec and spec.loader
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
ALLOWED = 'joe.bookout@carr.us'
TESTER = 'joe.b.tester@outlook.com'
BINARY = Path(os.environ['HXPROBE_TEST_BINARY']) if 'HXPROBE_TEST_BINARY' in os.environ else None


def put(d, p, n):
    d[p:p+8] = struct.pack('<Q', n)


def root(n):
    d = bytearray(46)
    d[0] = 0x49
    put(d, 10, n)
    d[18] = 1
    put(d, 30, n)
    put(d, 38, 2**64-2)
    return d


def envelope(tag, obj):
    return struct.pack('<HHIH', 5, tag, len(obj)+10, 0) + obj


def mailbox(n=9, owner=7, identity=ALLOWED):
    d = bytearray(1748)
    d[0] = 0x4a
    for p in (10, 30, 38):
        put(d, p, n)
    d[48:94] = root(owner)
    d[112:158] = root(owner)
    b = identity.encode('utf-16le')
    d.extend((struct.pack('<IB', 1, len(b)) + b) * 2)
    return envelope(0x6ca, d)


def text(s):
    return s.encode('utf-16le') + b'\0\0'


def revision(owner=7, label='allowed', preview=False, n=12, body_marker=None, conversation=None, sent_hours=-1, preview_text='A synthetic preview asking for the floor plan.'):
    d = bytearray(600)
    d[0] = 0xca
    for p in (10, 30, 38):
        put(d, p, n)
    if owner:
        d[112:158] = root(owner)
    put(d, 480, 621355968000000000 + fixture.ts(timedelta(hours=sent_hours))*10000000)
    for s in ['pat@example.test', 'Pat Example', 'IPM.Note', '<synthetic@example.test>',
              preview_text, label, label, ALLOWED]:
        d.extend(text(s))
    if conversation:
        d.extend(text(conversation))
    if not preview:
        d.extend(f'<html><body>Synthetic {body_marker or label} body asks for the floor plan.</body></html>'.encode())
    return envelope(0x74e, d)


def event(owner=7, label='allowed', upcoming=False, n=14, body_marker=None):
    d = bytearray(1107)
    d[0] = 0x6b
    for p in (10, 30, 38):
        put(d, p, n)
    if owner:
        d[112:158] = root(owner)
    start = fixture.ts(timedelta(days=1) if upcoming else -timedelta(hours=2))
    for p, t in [(574,start),(582,start+3600)]:
        put(d,p,621355968000000000+t*10000000)
    d.extend(text('040000008200E00074C5B7101A82E00800000000AAAA'))
    for s in ['America/Chicago','US/Central']:
        b=s.encode('utf-16le'); d.extend(bytes([len(b)])+b)
    for s in [label, 'Example Room', 'Owner Example', ALLOWED, 'Owner Example',
              (body_marker or 'Synthetic agenda line one\r\nline two'), label]:
        d.extend(text(s))
    d.extend(struct.pack('<I',1))
    for s in ['Pat Example','pat@example.test']:
        b=s.encode('utf-16le'); d.extend(bytes([len(b)])+b)
    d.extend(bytes(12))
    return envelope(0x455,d)


def conflicting(frame):
    data=bytearray(frame)
    data[310:356]=root(8)
    return bytes(data)


def store(payload):
    h=bytearray(4096);h[:8]=b'Nostromo';h[8]=ord('i');put(h,0x38,4096)
    n=len(payload); compressed=bytearray([min(n,15)<<4])
    if n>=15:
        q=n-15
        while q>=255:
            compressed.append(255);q-=255
        compressed.append(q)
    compressed.extend(payload)
    b=bytearray(40)
    put(b,8,0x5d0245643b706a05)
    struct.pack_into('<IIII',b,16,8,len(compressed),n,4)
    b.extend(compressed)
    struct.pack_into('<I',b,4,zlib.crc32(b[8:]))
    struct.pack_into('<I',b,0,zlib.crc32(b[4:32]))
    return h+b


class OwnershipTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global BINARY, BINARY_SHA
        if BINARY is None:
            target=tempfile.TemporaryDirectory(prefix='hx-owned-tests-')
            cls.addClassCleanup(target.cleanup)
            subprocess.run(['cargo','build','--locked','--manifest-path',str(ROOT/'vendor/hxprobe/Cargo.toml'),
                            '--target-dir',target.name],check=True,capture_output=True)
            BINARY=Path(target.name)/'debug/hxprobe'
        BINARY_SHA=hashlib.sha256(BINARY.read_bytes()).hexdigest()

    def decode(self, payload):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        base=Path(tmp.name);src=base/'synthetic.hxd';db=base/'owned.db'
        src.write_bytes(store(payload))
        p=subprocess.run([str(BINARY),'owned-db',str(src),str(db),ALLOWED],capture_output=True)
        self.assertEqual(p.returncode,0,p.stderr.decode())
        return src,db

    def test_binary_positive_mail_and_event_and_unnamed_owner(self):
        _,db=self.decode(mailbox()+revision()+event()+revision(999,'UNAUTHORIZED')+event(999,'UNAUTHORIZED'))
        with sqlite3.connect(db) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM messages').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT count(*) FROM events').fetchone()[0],1)
            self.assertFalse(c.execute("SELECT count(*) FROM messages WHERE body LIKE '%UNAUTHORIZED%'").fetchone()[0])
            self.assertEqual(c.execute('SELECT root_key,mailbox_key,identity FROM ownership').fetchone(),(7,9,ALLOWED))

    def test_owned_export_uses_reviewed_nonoverlapping_block_framing(self):
        _,db=self.decode(mailbox()+revision()+event()+store(b'unframed')[4096:])
        with sqlite3.connect(db) as c:
            meta=dict(c.execute('SELECT key,value FROM meta'))
            self.assertEqual(meta['blocks_found'],2)
            self.assertEqual(meta['blocks_verified'],1)
            self.assertEqual({r[0].split(':')[4] for r in c.execute('SELECT source_id FROM source_provenance')},{'4096'})

    def test_malformed_compressed_blocks_have_no_intake_effects(self):
        for variant in ('trailing_input', 'oversized_match'):
            with self.subTest(variant=variant):
                payload=mailbox()+revision()+event()
                if variant=='oversized_match':
                    payload+=b'X'
                raw=store(payload);block=bytearray(raw[4096:])
                block.extend(b'\x00' if variant=='trailing_input' else b'\x01\x00')
                struct.pack_into('<I',block,20,len(block)-40)
                if variant=='oversized_match':
                    struct.pack_into('<I',block,24,len(payload)+1)
                struct.pack_into('<I',block,4,zlib.crc32(block[8:]))
                struct.pack_into('<I',block,0,zlib.crc32(block[4:32]))
                t=fixture.IntakeTest();t.setUp();self.addCleanup(t.doCleanups)
                t.store.write_bytes(raw[:4096]+block)
                binary=t.home/intake.HXPROBE;binary.parent.mkdir(parents=True);binary.symlink_to(BINARY)
                ports=t.fake.ports();ports.decode=intake._decoder(t.home)
                result=intake.run(t.repo,t.home,ports)
                self.assertEqual(result.reason,'decode_failed')
                self.assertEqual(t.fake.calls,[])
                self.assertEqual(t.fake.model_calls,[])
                self.assertFalse((t.out()/intake.STATE).exists())

    def test_well_formed_saved_data_changes_and_missing_evidence_are_held(self):
        for variant in ('payload', 'reply', 'pending_full', 'refresh', 'source', 'receipt'):
            with self.subTest(variant=variant):
                t,_=self.observe(mailbox()+revision(preview=True),fail=True)
                path=t.out()/intake.STATE;state=json.loads(path.read_text())
                if variant=='payload':
                    request=next(iter(state['requests'].values()))
                    args=json.loads(request['payload']);args['detail']='FORGED_SYNTHETIC'
                    request['payload']=json.dumps(args)
                elif variant=='reply':
                    step=next(iter(state['steps'].values()))
                    step['reply']={'next_step':'FORGED_SYNTHETIC','due_on':None,'ownership':step['ownership']}
                elif variant=='pending_full':
                    next(iter(state['pending_full'].values()))['refs']=['C-999']
                elif variant=='refresh':
                    refresh=t.out()/intake.REFRESH_LIST;rows=json.loads(refresh.read_text())
                    rows[0]['sender_email']='forged@example.test';refresh.write_text(json.dumps(rows))
                elif variant=='receipt':
                    (t.out()/f"receipt-{state['receipt_sha256']}.json").unlink()
                else:
                    next(t.out().glob('source-*.json')).unlink()
                path.write_text(json.dumps(state));before=path.read_bytes()
                t.fake.calls.clear();t.fake.model_calls.clear()
                result=intake.run(t.repo,t.home,t.fake.ports())
                self.assertEqual(result.reason,'legacy_ownership_held')
                self.assertEqual(t.fake.calls,[])
                self.assertEqual(t.fake.model_calls,[])
                self.assertEqual(path.read_bytes(),before)

    def test_forged_retry_payload_and_well_formed_provenance_are_held(self):
        t,_=self.observe(mailbox()+revision(preview=True),fail=True)
        path=t.out()/intake.STATE
        state=json.loads(path.read_text())
        request=next(iter(state['requests'].values()))
        request['ownership']['store_sha256']='0'*64
        request['ownership']['binding_sources']='1:1'
        request['ownership']['source_ids']=['hx-owner-v1:105:7:9:1:1:1:1']
        payload=json.loads(request['payload']);payload['detail']='FORGED_SYNTHETIC'
        request['payload']=json.dumps(payload)
        path.write_text(json.dumps(state));before=path.read_bytes()
        t.fake.calls.clear();t.fake.model_calls.clear();t.fake.fail_verbs.clear()
        result=intake.run(t.repo,t.home,t.fake.ports())
        self.assertEqual(result.reason,'legacy_ownership_held')
        self.assertEqual(t.fake.writes(),[])
        self.assertEqual(t.fake.model_calls,[])
        self.assertEqual(path.read_bytes(),before)

    def test_legacy_retry_is_held_before_any_effect(self):
        t=fixture.IntakeTest();t.setUp();self.addCleanup(t.doCleanups)
        out=t.out();out.mkdir(parents=True)
        state={'requests': {'unproven': {'verb':'log-activity','payload':json.dumps({'ref':'C-901','idempotency_key':'unproven'}),'acknowledged':False,'created_at':1}}}
        (out/intake.STATE).write_text(json.dumps(state))
        before=(out/intake.STATE).read_bytes()
        result=intake.run(t.repo,t.home,t.fake.ports())
        self.assertEqual(result.reason,'legacy_ownership_held')
        self.assertEqual(t.fake.calls,[])
        self.assertEqual(t.fake.decodes,0)
        self.assertEqual((out/intake.STATE).read_bytes(),before)
        globals().setdefault('OBSERVATIONS',{})['legacy_retry']={
            'outcome':result.reason,'source_rows_on_retry':0,'decodes':t.fake.decodes,
            'replayed_writes':len(t.fake.writes()),'models':len(t.fake.model_calls),
            'reports':len(list(out.glob('report-*.md')))}

    def observe(self, payload, *, fail=False):
        src,db=self.decode(payload)
        t=fixture.IntakeTest();t.setUp();self.addCleanup(t.doCleanups)
        t.store.write_bytes(src.read_bytes())
        t.fake.fixture.write_bytes(db.read_bytes())
        binary=t.home/intake.HXPROBE
        binary.parent.mkdir(parents=True)
        binary.symlink_to(BINARY)
        production_decoder=intake._decoder(t.home)
        def decode(copy, files, output):
            t.fake.decodes+=1
            production_decoder(copy,files,output)
        t.fake.decode=decode
        contacts=[]
        original=t.fake.contacts
        t.fake.contacts=lambda: (contacts.append(True) or original())
        if fail:
            t.fake.fail_verbs={'log-activity'}
        result=intake.run(t.repo,t.home,t.fake.ports())
        writes=t.fake.writes()
        refresh=t.out()/intake.REFRESH_LIST
        observations={
            'outcome':result.reason,
            'emails':sum(a.get('kind')=='email_in' for v,a in writes if v=='log-activity'),
            'meetings':sum(a.get('kind')=='meeting' for v,a in writes if v=='log-activity'),
            'actions':len(t.fake.writes('set-next-action')),
            'models':len(t.fake.model_calls),
            'contacts':len(contacts),
            'reads':sum(v not in intake.WRITE_VERBS for v,a in t.fake.calls),
            'reports':len(list(t.out().glob('report-*.md'))),
            'refresh':len(json.loads(refresh.read_text())) if refresh.exists() else 0,
        }
        return t,observations

    def test_original_owner_threats_through_binary_and_intake(self):
        observations={}
        for label,owner in [('allowed',7),('tester',8),('other',999),('missing',0),('ambiguous',7)]:
            r=revision(owner,label)
            if label=='ambiguous':
                r=bytearray(r);r[310:356]=root(8);r=bytes(r)
            e=event(owner,label)
            if label=='ambiguous':
                e=bytearray(e);e[310:356]=root(8);e=bytes(e)
            t,o=self.observe(mailbox()+mailbox(11,8,TESTER)+r+e)
            observations[label]=o
            if label=='allowed':
                self.assertEqual((o['emails'],o['meetings'],o['models']),(1,1,1),o)
                state=json.loads((t.out()/intake.STATE).read_text())
                self.assertTrue(intake._state_owned(state,t.out()))
                self.assertTrue(all(r['ownership']['source_ids'] for r in state['requests'].values()))
            else:
                self.assertTrue(all(o[k]==0 for k in ('emails','meetings','actions','models','contacts','reads','reports','refresh')),o)
        for label,payload,expected in [
            ('mixed',revision()+event()+revision(8,'UNAUTHORIZED')+event(8,'UNAUTHORIZED')+revision(999,'UNAUTHORIZED')+event(999,'UNAUTHORIZED')+revision(0,'UNAUTHORIZED')+event(0,'UNAUTHORIZED')+conflicting(revision(7,'UNAUTHORIZED'))+conflicting(event(7,'UNAUTHORIZED')),(1,1,1)),
            ('colliding_allowed_and_tester',revision(8,'allowed',body_marker='UNAUTHORIZED'*30)+event(8,'allowed',body_marker='UNAUTHORIZED'*30)+revision()+event(),(1,1,1)),
            ('missing_preview',revision(0,'missing',preview=True),(0,0,0)),
            ('tester_upcoming',revision(8,'tester')+event(8,'tester',upcoming=True),(0,0,0)),
        ]:
            t,o=self.observe(mailbox()+mailbox(11,8,TESTER)+payload)
            observations[label]=o
            self.assertEqual((o['emails'],o['meetings'],o['models']),expected,o)
            self.assertEqual(o['actions'],0,o)
            self.assertEqual(o['refresh'],0,o)
            serialized=json.dumps(t.fake.calls)+json.dumps(t.fake.model_calls)+t.report_text()
            self.assertNotIn('UNAUTHORIZED',serialized)
        globals().setdefault('OBSERVATIONS',{}).update(observations)

    def test_allowed_preview_retry_and_saved_reply_keep_ownership(self):
        t,o=self.observe(mailbox()+revision(preview=True),fail=True)
        self.assertEqual(o['emails'],1)
        state=json.loads((t.out()/intake.STATE).read_text())
        self.assertTrue(intake._state_owned(state,t.out()))
        self.assertEqual(len(state['pending_full']),1)
        t.fake.fail_verbs.clear();t.fake.calls.clear()
        t.fake.model_reply={'next_step':'Send synthetic plan','due_on':None}
        second=intake.run(t.repo,t.home,t.fake.ports())
        self.assertEqual(second.state,'succeeded',second)
        self.assertEqual(len(t.fake.writes('log-activity')),1)
        self.assertEqual(len(t.fake.writes('set-next-action')),1)
        refresh=json.loads((t.out()/intake.REFRESH_LIST).read_text())
        self.assertTrue(intake._valid_provenance(refresh[0]['ownership'],state['ownership']))
        after=json.loads((t.out()/intake.STATE).read_text())
        self.assertTrue(intake._state_owned(after,t.out()))

    def test_unproven_requests_steps_replies_and_refresh_are_held_unchanged(self):
        for collection in ('requests','steps','pending_full','reply','refresh'):
            with self.subTest(collection=collection):
                t,_=self.observe(mailbox()+revision(preview=True),fail=True)
                path=t.out()/intake.STATE
                state=json.loads(path.read_text())
                if collection=='reply':
                    entry=next(iter(state['steps'].values()))
                    entry['reply']={'next_step':'UNAUTHORIZED','due_on':'2026-10-22'}
                elif collection=='refresh':
                    refresh=t.out()/intake.REFRESH_LIST
                    rows=json.loads(refresh.read_text());rows[0].pop('ownership');refresh.write_text(json.dumps(rows))
                else:
                    next(iter(state[collection].values())).pop('ownership')
                path.write_text(json.dumps(state))
                before=path.read_bytes();decodes=t.fake.decodes
                t.fake.calls.clear();t.fake.model_calls.clear()
                result=intake.run(t.repo,t.home,t.fake.ports())
                self.assertEqual(result.reason,'legacy_ownership_held')
                self.assertEqual(t.fake.decodes,decodes)
                self.assertEqual(t.fake.calls,[])
                self.assertEqual(t.fake.model_calls,[])
                self.assertEqual(path.read_bytes(),before)

    def test_export_with_wrong_root_provenance_has_no_effects(self):
        src,db=self.decode(mailbox()+revision()+event())
        with sqlite3.connect(db) as c:
            c.execute("UPDATE source_provenance SET source_id=replace(source_id,':7:9:',':999:9:') WHERE kind IN ('mail','event') AND row_id=1")
        c.close()
        t=fixture.IntakeTest();t.setUp();self.addCleanup(t.doCleanups)
        t.store.write_bytes(src.read_bytes());t.fake.fixture.write_bytes(db.read_bytes())
        result=intake.run(t.repo,t.home,t.fake.ports())
        self.assertEqual(result.reason,'decode_failed')
        self.assertEqual(t.fake.calls,[])
        self.assertEqual(t.fake.model_calls,[])

    def test_missing_copy_keys_do_not_collapse_distinct_owned_mail(self):
        t=fixture.IntakeTest();t.setUp();self.addCleanup(t.doCleanups)
        mails=[fixture.mail('pat@example.test',-timedelta(hours=1),subject='First synthetic'),
               fixture.mail('lee@example.test',-timedelta(hours=2),subject='Second synthetic')]
        for m in mails:
            m['copy_key']=None
        result=t.run_intake(mails)
        self.assertEqual(result.state,'succeeded')
        self.assertEqual(len(t.fake.writes('log-activity')),2)

    def test_unproven_pending_refresh_is_held_before_unchanged_shortcut(self):
        t,_=self.observe(mailbox()+revision(preview=True))
        path=t.out()/intake.STATE
        state=json.loads(path.read_text())
        self.assertFalse(state['steps'])
        next(iter(state['pending_full'].values())).pop('ownership')
        path.write_text(json.dumps(state));before=path.read_bytes()
        t.fake.calls.clear();t.fake.model_calls.clear();decodes=t.fake.decodes
        result=intake.run(t.repo,t.home,t.fake.ports())
        self.assertEqual(result.reason,'legacy_ownership_held')
        self.assertEqual(t.fake.decodes,decodes)
        self.assertEqual(t.fake.calls,[])
        self.assertEqual(path.read_bytes(),before)

    def test_owned_saved_reply_replays_with_empty_current_content(self):
        t,_=self.observe(mailbox()+revision())
        # A fresh owned message asks for a next action, then its write fails.
        t.fake.model_reply={'next_step':'Send synthetic plan','due_on':None}
        t.fake.fail_verbs={'set-next-action'}
        t.store.write_bytes(store(mailbox()+revision(label='Second synthetic',n=13)))
        first=intake.run(t.repo,t.home,t.fake.ports())
        self.assertEqual(first.reason,'writes_failed')
        state=json.loads((t.out()/intake.STATE).read_text())
        step=next(iter(state['steps'].values()))
        self.assertTrue(intake._valid_provenance(step['reply']['ownership'],state['ownership']))
        t.store.write_bytes(store(mailbox()))
        t.fake.calls.clear();t.fake.model_calls.clear();t.fake.fail_verbs.clear()
        second=intake.run(t.repo,t.home,t.fake.ports())
        self.assertEqual(second.state,'succeeded',second)
        self.assertEqual(t.fake.model_calls,[])
        self.assertEqual(len(t.fake.writes('set-next-action')),1)

    def test_conversation_recovery_and_merged_sources_stay_owned(self):
        conversation='0123456789ABCDEF0123456789ABCDEF'
        target=revision(label='',body_marker='Target synthetic content',conversation=conversation,preview_text='lowercase synthetic preview content')
        foreign=revision(8,'UNAUTHORIZED subject',body_marker='UNAUTHORIZED'*40,conversation=conversation,sent_hours=-2)
        _,db=self.decode(mailbox()+mailbox(11,8,TESTER)+target+foreign)
        with sqlite3.connect(db) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM messages').fetchone()[0],1)
            self.assertNotIn('UNAUTHORIZED',json.dumps(c.execute('SELECT subject,body FROM messages').fetchall()))
        donor=revision(label='Allowed donor subject',body_marker='Donor synthetic content',conversation=conversation,sent_hours=-2,n=13)
        _,db=self.decode(mailbox()+target+donor+event()+event(n=15))
        with sqlite3.connect(db) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM messages').fetchone()[0],2)
            inherited=c.execute('SELECT id,subject FROM messages WHERE subject_inherited=1').fetchall()
            self.assertEqual(len(inherited),1)
            self.assertEqual(inherited[0][1],'Allowed donor subject')
            self.assertEqual(c.execute("SELECT count(*) FROM source_provenance WHERE kind='mail' AND row_id=?",(inherited[0][0],)).fetchone()[0],2)
            self.assertEqual(c.execute('SELECT count(*) FROM events').fetchone()[0],1)
            self.assertEqual(c.execute("SELECT count(*) FROM source_provenance WHERE kind='event'").fetchone()[0],2)


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(OwnershipTest)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    if path:=os.environ.get('OWNERSHIP_AUDIT'):
        Path(path).write_text(json.dumps({'schema':'pr2050-ownership-qualification/v2',
            'synthetic_contract_tests_passed':result.wasSuccessful(), 'tests_run':result.testsRun,
            'runtime_source_changed':True,'binary_sha256':globals().get('BINARY_SHA'),
            'source_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in [
                'lib/outlook_intake.py','vendor/hxprobe/src/main.rs','vendor/hxprobe/src/ownership.rs',
                'vendor/hxprobe/src/calendar.rs','vendor/hxprobe/src/record.rs',
                'ops/outlook-intake-ownership-selftest.py','ops/outlook-intake-selftest.py']},
            'independently_qualified':False,'installed_runtime_qualified':False,
            'original_red_sha256':hashlib.sha256((ROOT/'audits/pr2050-account-qualification.json').read_bytes()).hexdigest(),
            'observations':globals().get('OBSERVATIONS',{})},indent=2)+'\n')
    sys.exit(0 if result.wasSuccessful() else 1)
