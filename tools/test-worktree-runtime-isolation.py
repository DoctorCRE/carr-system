"""Fake-only tests for the disabled R09 runtime-isolation contract."""
import json, os, sys, tempfile, threading, time
from pathlib import Path
ROOT=Path(__file__).resolve().parent; sys.path.insert(0,str(ROOT / "room-bridge")); sys.path.insert(0,str(ROOT.parent / "ops"))
from git_env import fixture_env
from worktree_runtime_isolation import Bundle, IsolationRefusal, Owner, RuntimeIsolation, read_lock_posture
def owner(name): return Owner(name, f"fixture-{name}", "fixture-source")
def tree_snapshot(root):
  return {(path.relative_to(root).as_posix(),path.is_dir()):(None if path.is_dir() else path.read_bytes(),path.stat().st_mode) for path in root.rglob("*")}
def run_race(iterations):
    begun=time.monotonic(); witnessed=0
    with tempfile.TemporaryDirectory() as tmp:
      for i in range(iterations):
        if time.monotonic()-begun>120: return {"outcome":"blocked","reason":"wall_cap","witnessed":witnessed}
        root=Path(tmp)/str(i); gate=threading.Barrier(2, timeout=5); attempts=[]; results=[]; errors=[]; deadline=time.monotonic()+10
        def contender(n):
          s=RuntimeIsolation(root,owner(f"{i}-{n}")); started=None
          try:
            gate.wait(); started=time.monotonic(); a=s.allocate(Bundle((31000+i,),f"c{i}",f"d{i}"),idempotency_key=f"same-{n}"); results.append((s,a,time.monotonic()))
          except Exception as e: errors.append(e)
          finally:
            if started is not None: attempts.append((n, started, time.monotonic()))
        ts=[threading.Thread(target=contender,args=(n,),daemon=True) for n in range(2)]
        [t.start() for t in ts]
        for t in ts: t.join(max(0,deadline-time.monotonic()))
        if time.monotonic()-begun>120: return {"outcome":"blocked","reason":"wall_cap","witnessed":witnessed}
        if any(t.is_alive() for t in ts) or len(results)!=1 or len(errors)!=1 or not isinstance(errors[0],IsolationRefusal): return {"outcome":"failed","reason":"collision","witnessed":witnessed}
        # Each attempt begins after the barrier and ends after its allocation
        # returns or refuses. Intersecting intervals are a timestamped witness
        # of allocation contention rather than structural barrier overlap.
        if len(attempts)==2 and max(item[1] for item in attempts)<=min(item[2] for item in attempts): witnessed+=1
        state=json.loads((root/"r09-state.json").read_text());
        if len(state["allocations"])!=1 or len(list((root/"runs").iterdir()))!=1: return {"outcome":"failed","reason":"partial","witnessed":witnessed}
        results[0][0].teardown(results[0][1])
    minimum=8 if iterations>=64 else 1
    return {"outcome":"pass" if witnessed>=minimum else "failed","witnessed":witnessed}
def test_secrets_and_lifecycle():
  with tempfile.TemporaryDirectory() as tmp:
    s=RuntimeIsolation(Path(tmp),owner("a")); a=s.allocate(Bundle((32000,),"c","d"))
    class Poison(dict):
      def __getitem__(self,k): raise AssertionError("value accessed")
    for n in ("PRODUCTION_DATABASE_URL","BREAKGLASS_TOKEN","CARR_DB_JOBS_URL","CARR_DB_OWNER_URL","CARR_DB_BACKUP_URL","CARR_AUTHORITY_TOKEN","CARR_DEVICE_TOKEN"):
      try:s.write_fake_environment(a,Poison({n:"x"}))
      except IsolationRefusal:pass
      else:raise AssertionError(n)
    env=s.write_fake_environment(a,{"R09_FAKE_TOKEN":"fake"}); assert env.stat().st_mode&0o777==0o600 and (env.parent/".r09-exclude-from-archives").exists()
    try:s.teardown(a, "wrong")
    except IsolationRefusal:pass
    else:raise AssertionError("wrong generation")
    s.teardown(a); assert not env.exists()
def test_entrant_and_read_seam():
  with tempfile.TemporaryDirectory() as tmp:
    now=[1.0]; clock=lambda:now[0]; a=RuntimeIsolation(Path(tmp),owner("a"),clock=clock); b=RuntimeIsolation(Path(tmp),owner("b"),clock=clock)
    aa=a.allocate(Bundle((33000,),"ca","da")); bb=b.allocate(Bundle((33001,),"cb","db")); g=a.acquire_entrant(aa["run_id"],stale_after_seconds=0,owner_alive=lambda _:False); before=b.lock_posture()
    try:b.acquire_entrant(bb["run_id"],stale_after_seconds=0,owner_alive=lambda _:True)
    except IsolationRefusal:pass
    else:raise AssertionError("second entrant")
    before_files=tree_snapshot(Path(tmp))
    assert read_lock_posture(Path(tmp))==before
    assert tree_snapshot(Path(tmp))==before_files
    now[0]+=2; assert read_lock_posture(Path(tmp),lambda _:None)["state"]=="stale_uncertain"
    for liveness in (True,None):
      try:b.acquire_entrant(bb["run_id"],stale_after_seconds=1,owner_alive=lambda _,value=liveness:value)
      except IsolationRefusal:pass
      else:raise AssertionError(f"stale entrant accepted while liveness={liveness!r}")
    takeover=b.acquire_entrant(bb["run_id"],stale_after_seconds=1,owner_alive=lambda _:False)
    try:a.teardown(aa,g)
    except IsolationRefusal:pass
    else:raise AssertionError("old owner released")
    a.teardown(aa); b.teardown(bb,takeover)
def test_malformed_allocation_refuses():
  with tempfile.TemporaryDirectory() as tmp:
    s=RuntimeIsolation(Path(tmp),owner("malformed"))
    s.state_path.write_text('{"allocations":{"bad":{}},"entrant":null,"receipts":[]}',encoding="utf-8")
    try:s.allocate(Bundle((34000,),"cm","dm"))
    except IsolationRefusal:pass
    else:raise AssertionError("malformed allocation was accepted")
def main():
  assert "GIT_DIR" not in fixture_env({"GIT_DIR":"fixture-escape"})
  count=int(os.environ.get("R09_EVIDENCE_ITERATIONS","8")); receipt=run_race(count); test_secrets_and_lifecycle(); test_entrant_and_read_seam(); test_malformed_allocation_refuses(); print(json.dumps(receipt,sort_keys=True)); assert receipt["outcome"]=="pass"
if __name__=="__main__": main()
