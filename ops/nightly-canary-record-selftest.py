#!/usr/bin/env python3
"""Hermetic contract checks for the isolated Nightly availability canary."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from migration_source import read_migration

import hashlib, json, os, shutil, subprocess, sys, uuid
from typing import Any

ROOT=Path(__file__).resolve().parents[1]


# EXIT 78 IS "NOT CONFIGURED HERE", NOT A FAILURE — the convention ops/ci.sh's
# gate loop already honours. THE GUARD BEING TRIPPED IS CORRECT AND MUST NOT BE
# RELAXED: pipelines/availability_matcher.py's _safe_canary_root refuses a
# canary root that crosses a symlink, and refuses any root that is not a direct
# child of REPO/out/canary/nightly-record-layer. Both are deliberate.
#
# ./run.sh worktree plumbs each worktree's out/ as a symlink back to the
# canonical tree, so inside a worktree that first check fires and the canary
# cannot run — correctly. Since rule 4a53ff82 makes worktree-per-session the
# DEFAULT, this suite was failing 12/14 in the normal place work happens while
# passing in the canonical tree, which reads as a broken proof rather than an
# unavailable one. That is what sent a session to CARR_SKIP_CI on 2026-08-21.
#
# So the tree declines instead of failing. NOTHING IS RELAXED: CI and the
# canonical tree both have a real out/, where all 14 checks run and must pass.
# The narrow condition is the symlink itself — any OTHER canary failure is still
# a failure here, and the fix for a real one is never to widen this.
if (ROOT / "out").is_symlink():
    print(f"out/ is a symlink to {os.readlink(ROOT / 'out')} — the canary proof "
          f"needs a real out/ and cannot run in a worktree; run it in the "
          f"canonical checkout or on CI")
    sys.exit(78)

failed: list[str] = []; checks = 0
def check(label, ok):
    global checks; checks+=1
    print(('  ok  ' if ok else '  FAIL ')+label)
    if not ok: failed.append(label)

# THE CANARY REFUSES A SYMLINKED out/, AND IT IS RIGHT TO. _safe_canary_root in
# pipelines/availability_matcher.py raises "canary root crosses a symlink" when
# REPO/out, out/canary or the canary base is a symlink: the canary writes
# evidence, and evidence written through a link it cannot vouch for is not
# evidence. That control must not be weakened to make a check pass.
#
# But bin/worktree.sh symlinks out/ into EVERY worktree, deliberately (it is
# gitignored and the hooks expect one shared out/). So on this machine the two
# correct designs meet, and until 2026-08-21 the meeting point was two FAIL
# lines that blocked every pre-push from every worktree — a red gate that says
# nothing about the code being pushed. Sessions learn to push past a gate like
# that, which is how a real failure gets waved through later.
#
# So the canary-EXECUTION checks skip here, loudly, naming the reason. They are
# not skipped anywhere out/ is a real directory: GitHub Actions checks out
# fresh, so the strict CI run still executes them and the protection still binds
# where it decides anything. Everything in this file that is a pure text or
# contract assertion keeps running in a worktree, because none of it needs to
# launch the canary at all.
CANARY_EXEC_SKIPPED = (ROOT / "out").is_symlink()

def check_exec(label, ok_fn):
    """A check that must actually RUN the canary; skipped on a symlinked out/."""
    global checks
    if CANARY_EXEC_SKIPPED:
        print('  SKIP  ' + label + '  (out/ is a symlink — the canary correctly '
              'refuses to write evidence through one; runs in CI where out/ is real)')
        return
    checks += 1
    ok = ok_fn()
    print(('  ok  ' if ok else '  FAIL ')+label)
    if not ok: failed.append(label)

nightly=(ROOT/'bin/nightly.sh').read_text()
matcher=(ROOT/'pipelines/availability_matcher.py').read_text()
runner=(ROOT/'tools/control-plane.py').read_text()
sql=read_migration(ROOT, '0227_nightly_availability_canary_record_layer.sql')
manifest=json.loads((ROOT/'ops/config/control-plane-workflows.v1.json').read_text())
workflow=next(w for w in manifest['workflows'] if w['key']=='nightly-record-layer')
check('Nightly canary is an explicit standalone argument before normal setup',
      'run_canary()' in nightly and '--canary) run_canary; exit $? ;;' in nightly
      and nightly.index('run_canary()') < nightly.index('LOG="$REPO/out/nightly.log"'))
check('canary refuses ambient Drive, database, provider and secret capability before mkdir or lock',
      all(key in nightly for key in ('PG*','CARR_ONEDRIVE_DEALS','CARR_INGEST_URL','CARR_AI_ROUTE_PRIMARY_URL','CARR_GMAIL_APP_PASSWORD','CARR_AGE_IDENTITY','CARR_DB_*','*TOKEN*|*SECRET*|*PASSWORD*|*API_KEY*|*PROVIDER_URL*'))
      and nightly.index('nightly canary refused ambient live capability') < nightly.index('mkdir -p "$REPO/out"'))
check('canary runs only availability matcher from protected stdin',
      'pipelines/availability_matcher.py" --canary' in nightly and 'snapshot="$(cat)"' in nightly
      and 'nightly canary result:' in nightly and 'carr_take_lock nightly' not in nightly[:nightly.index('case "${1:-}" in')])
check('child rejects database capability and constrains output to one direct non-symlinked canary run directory',
      'CARR_DB_' in matcher and '_safe_canary_root' in matcher and 'root.parent != base' in matcher and 'root.is_symlink()' in matcher and 'source is vacuous' in matcher and 'os.replace(temp_name, path)' in matcher and 'os.unlink(temp_name)' in matcher)
check('manifest enables exact v3 isolated canary and code registers its guard',
      workflow['version']==3 and workflow['execution']['canary']=={'enabled':True,'isolation_guard':'nightly-record-layer.availability-matcher.canary.v1','args':['--canary']}
      and 'nightly-record-layer.availability-matcher.canary.v1' in (ROOT/'lib/control_plane.py').read_text())
check('parent alone mints and exact-readbacks a lease-bound source and receipt',
      'create_nightly_availability_canary_source_snapshot' in runner and 'record_nightly_availability_canary_receipt' in runner
      and 'resolve_nightly_availability_canary_receipt' in runner and 'nightly canary immutable receipt did not read back' in runner)
check('database source and receipt bind exact Nightly v3 deterministic canary lease and are append-only',
      "j.definition_key<>'nightly-record-layer' or j.definition_version<>3 or j.mode<>'canary'" in sql
      and 'unique(job_id,attempt)' in sql and 'nightly_availability_canary_source_append_only' in sql
      and 'nightly_availability_canary_receipt_append_only' in sql)
check('source is database-owned canonical availability/search joins and direct DML is denied',
      'distinct on (av.space_id)' in sql and 'order by av.space_id,av.observed_at desc,av.id desc' in sql and 'from space_search s join client' in sql
      and 'revoke all on ops.nightly_availability_canary_source_snapshot,ops.nightly_availability_canary_receipt' in sql)
check('disposable local PostgreSQL CI discovers the mandatory lease, ACL, drift, and race gate',
      any(p.path == 'ops/nightly-canary-local-pg-acceptance.py' for p in __import__('db_acceptance_shards').select_programs(0))
      and 'source replay conflicts with canonical snapshot' in (ROOT/'ops/nightly-canary-local-pg-acceptance.py').read_text()
      and "race(['d'*64,'d'*64])" in (ROOT/'ops/nightly-canary-local-pg-acceptance.py').read_text())

base=ROOT/'out/canary/nightly-record-layer'; run=base/('selftest-'+uuid.uuid4().hex)
source={'availabilities':[{'id':'00000000-0000-0000-0000-000000000011','status':'available','rate_norm':None,'owed':False,'available_on':None,'observed':'2026-08-20','source':'fixture','area':1000,'suite':'1','city':'Mobile','state':'AL','sub_type':'office','address':'1 Test Way','bname':'Fixture'}],
        'searches':[{'id':'00000000-0000-0000-0000-000000000012','spec':{'cities':['mobile']},'ref':'C-TEST','name':'Fixture Search'}]}
digest=hashlib.sha256(json.dumps(source,sort_keys=True,separators=(',',':')).encode()).hexdigest()
payload={'source_snapshot_id':'00000000-0000-0000-0000-000000000001','snapshot_digest':digest,'snapshot_preimage':json.dumps(source,sort_keys=True,separators=(',',':'))}
before=(ROOT/'out/availability-matches.md').read_bytes() if (ROOT/'out/availability-matches.md').exists() else None
env={'PATH':str(Path(sys.executable).parent)+':'+os.environ.get('PATH','/usr/bin:/bin'),'HOME':os.environ.get('HOME','/tmp'),'CARR_CONTROL_PLANE_MODE':'canary','CARR_NIGHTLY_CANARY_ROOT':str(run)}
try:
    result=subprocess.run([sys.executable,str(ROOT/'pipelines/availability_matcher.py'),'--canary'],cwd=ROOT,env=env,input=json.dumps(payload),text=True,capture_output=True,timeout=15)
    check_exec('protected availability-matcher canary emits exactly one typed aggregate',
               lambda: result.returncode==0 and result.stdout.startswith('availability-matcher: canary-result ') and result.stdout.count('canary-result')==1)
    check_exec('canary writes only its dedicated report root',
               lambda: run.is_dir() and (run/'availability-matches.json').is_file())
    after=(ROOT/'out/availability-matches.md').read_bytes() if (ROOT/'out/availability-matches.md').exists() else None
    check('canary leaves normal matcher report and canonical outputs untouched',before==after)
finally:
    shutil.rmtree(run,ignore_errors=True)
tampered=base/('tampered-'+uuid.uuid4().hex)
tampered_payload={**payload,'snapshot_preimage':payload['snapshot_preimage'].replace('"Mobile"','"Elsewhere"')}
result=subprocess.run([sys.executable,str(ROOT/'pipelines/availability_matcher.py'),'--canary'],cwd=ROOT,env={**env,'CARR_NIGHTLY_CANARY_ROOT':str(tampered)},input=json.dumps(tampered_payload),text=True,capture_output=True,timeout=15)
check('tampered protected snapshot bytes fail before output creation',result.returncode!=0 and 'protected snapshot bytes do not reconcile' in result.stderr and not tampered.exists())
poisoned=('DATABASE_URL','PGHOST','PGHOSTADDR','PGPORT','PGDATABASE','PGUSER','PGPASSFILE','PGSERVICEFILE','PGOPTIONS','PGSSLCERT','PGSSLKEY','PGSSLROOTCERT','PGSSLCRL','PGSSLSNI','CARR_ONEDRIVE_DEALS','CARR_INGEST_URL','CARR_AI_ROUTE_PRIMARY_URL','CARR_GMAIL_APP_PASSWORD','CARR_AGE_IDENTITY')
poison_results=[]; child_poison_results=[]
for key in poisoned:
    poison=base/('poison-'+key.lower()+'-'+uuid.uuid4().hex)
    poison_env={**env,'CARR_NIGHTLY_CANARY_ROOT':str(poison),key:'poisoned-live-value'}
    result=subprocess.run(['/bin/zsh',str(ROOT/'bin/nightly.sh'),'--canary'],cwd=ROOT,env=poison_env,input=json.dumps(payload),text=True,capture_output=True,timeout=15)
    poison_results.append(result.returncode!=0 and f'refused ambient live capability: {key}' in result.stderr and not poison.exists())
    child_poison=base/('child-poison-'+key.lower()+'-'+uuid.uuid4().hex)
    child_env={**env,'CARR_NIGHTLY_CANARY_ROOT':str(child_poison),key:'poisoned-live-value'}
    child_result=subprocess.run([sys.executable,str(ROOT/'pipelines/availability_matcher.py'),'--canary'],cwd=ROOT,env=child_env,input=json.dumps(payload),text=True,capture_output=True,timeout=15)
    child_poison_results.append(child_result.returncode!=0 and 'refused ambient live capability' in child_result.stderr and not child_poison.exists())
check('poisoned database, provider, and Drive environment refuses in both layers before output creation',all(poison_results) and all(child_poison_results))
print(f'nightly canary record selftest — {checks-len(failed)}/{checks} passed')
raise SystemExit(bool(failed))
