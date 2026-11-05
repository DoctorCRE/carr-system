#!/usr/bin/env python3
"""Export original natural-cohort bytes using the sanctioned read-only db-tap."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import selectors
import time
import stat
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from lib.email_touch_journal import Refused, canonical, digest, require


def private_file(directory, name, raw):
    path=directory/name
    flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW
    try:
        fd=os.open(path,flags,0o600)
    except FileExistsError:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            info=os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid()
                    and stat.S_IMODE(info.st_mode)==0o600, 'unsafe_export')
            with os.fdopen(fd,'rb',closefd=False) as stream:
                require(stream.read(1572865)==raw,'export_conflict')
        finally: os.close(fd)
    else:
        with os.fdopen(fd,'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    return path


def export(row, directory, consumer):
    raw=bytes(row['raw'])
    sha=hashlib.sha256(raw).hexdigest()
    require(0<len(raw)<=1048576 and sha==row['digest'],'digest_mismatch')
    j=json.loads(raw)
    require(j['release_sha']==row['producer_sha'] and j['principal_slug']==row['principal_slug']
            and j['actor_id']==str(row['actor_id']) and j['tenant']==row['tenant'], 'custody_mismatch')
    require(row['provenance']['selected_keys']==[e['original_key'] for e in j['expected']], 'key_mismatch')
    require(j['expected_count']==len(j['expected'])>0,'empty_journal')
    require(all(e['source_id']==row['provenance']['source_id']
                and e['source_version']==row['provenance']['source_version'] for e in j['expected']), 'source_mismatch')
    directory=directory.absolute()
    for anchor in ('/tmp','/var'):
        if directory.is_relative_to(anchor) and Path(anchor).is_symlink():
            directory=Path(anchor).resolve()/directory.relative_to(anchor)
    require(not any(p.is_symlink() for p in [directory,*directory.parents]),'unsafe_export')
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    info=directory.stat()
    require(stat.S_IMODE(info.st_mode)==0o700 and info.st_uid==os.getuid(),'unsafe_export')
    journal=private_file(directory,'journal.json',raw)
    argv=[sys.executable,str(consumer),'--journal',str(journal),'--journal-ref','sha256:'+sha,'--journal-sha256',sha]
    manifest=dict(schema='email-touch-journal-acquisition.v1',journal=str(journal),digest=sha,
                  reference='sha256:'+sha,capture_key=str(row['capture_key']),captured_at=str(row['captured_at']),
                  actor_id=str(row['actor_id']),principal_slug=row['principal_slug'],producer_sha=row['producer_sha'],
                  mailbox_scope_hash=row['mailbox_scope_hash'],input_digest=row['input_digest'],consumer_argv=argv,
                  consumer_sha256=hashlib.sha256(consumer.read_bytes()).hexdigest() if consumer.is_file() else None)
    private_file(directory,'manifest.json',canonical(manifest))
    return argv,j['expected_count']


def acquire(conn, identity, mailbox):
    conn.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
    conn.execute("SET LOCAL statement_timeout='10s'")
    conn.execute("SET LOCAL lock_timeout='2s'")
    row=conn.execute('''select * from ops.email_touch_expectation_journal
      where principal_slug=%s and producer_sha=%s and mailbox_scope_hash=%s
      order by captured_at,capture_key limit 1''',
      (identity['principal_slug'],identity['producer_sha'],mailbox)).fetchone()
    require(row is not None,'waiting_for_natural_email')
    require(identity['producer_sha']==identity['release_sha'],'release_mismatch')
    for table in ('ops.email_touch_expectation_journal','ops.email_touch_journal_outcome'):
        unsafe=conn.execute('''select exists(select 1 from pg_roles r where r.rolname in
          ('carr_writer','carr_authority','carr_reader','carr_jobs','dot_reader') and
          (has_table_privilege(r.oid,%s,'INSERT') or has_table_privilege(r.oid,%s,'UPDATE')
           or has_table_privilege(r.oid,%s,'DELETE') or has_table_privilege(r.oid,%s,'TRUNCATE'))) as unsafe''',
          (table,table,table,table)).fetchone()
        require(not unsafe['unsafe'],'mutation_privileges')
    keys=row['provenance']['selected_keys']
    premature=conn.execute('''select exists(select 1 from event where verb='log-activity'
       and actor_id=%s and idempotency_key=any(%s) and recorded_at<=%s) as premature''',
       (row['actor_id'],keys,row['captured_at'])).fetchone()
    require(not premature['premature'],'capture_after_application')
    conn.rollback()
    return row


def reconcile(argv):
    streams={}
    with subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE) as process:
        deadline=time.monotonic()+65
        try:
            with selectors.DefaultSelector() as selector:
                for stream in (process.stdout,process.stderr):
                    streams[stream]=bytearray()
                    selector.register(stream,selectors.EVENT_READ)
                while selector.get_map():
                    remaining=deadline-time.monotonic()
                    require(remaining>0,'consumer_deadline')
                    for key,_ in selector.select(min(remaining,1)):
                        chunk=os.read(key.fd,4097-len(streams[key.fileobj]))
                        streams[key.fileobj].extend(chunk)
                        require(len(streams[key.fileobj])<=4096,'consumer_output_cap')
                        if not chunk:
                            selector.unregister(key.fileobj)
            returncode=process.wait(timeout=max(0.01,deadline-time.monotonic()))
            report=json.loads(streams[process.stdout])
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    require(returncode==0 and report.get('schema')=='email-touch-cohort-proof.v1'
            and report.get('mode')=='production' and report.get('verdict')=='proven'
            and report.get('reason')=='matched','consumer_refused')
    counts=report['counts']
    require(counts['expected']==counts['applied']>0 and all(counts[k]==0 for k in
       ('missing','dangling','conflicting','tombstoned','out_of_window','extra','orphan')),'consumer_refused')
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--first-natural-for-installed-runtime',action='store_true',required=True)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--capture-only',action='store_true')
    mode.add_argument('--reconcile',action='store_true')
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args(argv)
    def expired(*_): raise Refused('deadline')
    signal.signal(signal.SIGALRM,expired)
    signal.alarm(120)
    try:
        from lib.outlook_settings import load_settings
        from lib.email_touch_journal import runtime_identity
        import psycopg
        from psycopg.rows import dict_row
        settings=load_settings(Path.home())
        identity=runtime_identity(settings.repo)
        mailbox=digest([settings.owner,settings.account.strip().lower()])
        with psycopg.connect(os.environ['DATABASE_URL'],connect_timeout=5,autocommit=True,row_factory=dict_row) as conn:
            row=acquire(conn,identity,mailbox)
        command,count=export(row,args.output_dir,ROOT/'ops/email-touch-cohort-proof.py')
        report=reconcile(command) if args.reconcile else dict(schema='email-touch-journal-proof.v1',mode='production',
            verdict='captured',reason='exact_bytes_exported',counts=dict(expected=count))
    except Refused as error:
        report=dict(schema='email-touch-journal-proof.v1',verdict='refused',reason=str(error))
    except Exception:
        report=dict(schema='email-touch-journal-proof.v1',verdict='refused',reason='acquisition_failed')
    finally: signal.alarm(0)
    print(json.dumps(report))
    return 0 if report['verdict'] in ('captured','proven') else 1

if __name__=='__main__': sys.exit(main())
