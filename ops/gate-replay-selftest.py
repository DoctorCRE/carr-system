#!/usr/bin/env python3
"""gate-replay-selftest.py — proves ops/gate-replay.py cannot be satisfied
without actually running the gates, and that its leak guard catches every miss
the 2026-09-24 Opus review found.

WHAT IS PROVEN, and each is adversarial rather than a happy path:

  LEAK GUARD. One planted leak per miss the review listed (lowercase street
  address, "1.2M", "450k/yr", USD, "<Name> Orthodontics", "Dr Smith", person
  names, a name used as a JSON key, sk_live_, ghp_, AKIA, a database URL with a
  password) plus the originals. Each must be caught both by the pattern module
  and by the CI scan of a planted fixture file, including a leak hidden behind
  JSON escaping. Clean text and the replay session id must pass, and every
  committed fixture must scan clean.

  EXTRACTOR. A planted leak drops the WHOLE record (no field of it survives),
  session ids are rewritten, out-of-checkout edits and conversation-quoting
  advisories are never extracted.

  MANIFEST. A new hook file with no manifest entry, a wiring that config has
  and the manifest lacks (or the reverse), a changed matcher, and a wiring
  whose matcher selects no fixture all fail.

  REPLAY, on a miniature repository with the REAL hooks/hook-meter-run.py:
    * a behaviour change with no text change is caught; a comment-only change
      (even one that names the fixture directory) changes nothing, so a comment
      can neither satisfy nor fool the check;
    * every register maps to the right verdict (allow, announce, deny, ask,
      reopen), read from the wrapper's own telemetry line;
    * a crash, a timeout, and a write outside out/ each fail;
    * the clock is pinned and the network is refused inside the gate;
    * helper evidence comes from modules the gate process actually executed, both
      through sys.path imports and through spec_from_file_location;
    * the run never asks git about any ref beyond its own sandbox, so a runner
      checkout with no local `main` cannot turn it into a no-op.
"""
from __future__ import annotations

import importlib.util
import json
import os
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))
sys.path.append(str(REPO / "lib"))
from selftest_harness import Checker  # noqa: E402
from ops import business_data_patterns as bdp  # noqa: E402
from ops import git_env  # noqa: E402


def load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


GR = load("gate_replay_under_test", REPO / "ops" / "gate-replay.py")
EX = load("extract_real_replay_under_test", REPO / "tools" / "extract-real-replay.py")
CHECK = Checker()
check = CHECK.check


# Host scheduling is not a fixture input. Suspend an interpreter between clock
# reads, then prove replay deadlines don't consume the host's pause. Stdlib
# subprocess waits must still expire on real time even when the shim is active.
clock_env = git_env.fixture_env()
clock_env.update(PYTHONPATH=str(GR.SHIM_DIR), CARR_GATE_REPLAY_EPOCH="1800000000")
clock_probe = '''
import os, signal, subprocess, sys, time
before = time.monotonic()
print("ready", flush=True)
os.kill(os.getpid(), signal.SIGSTOP)
print(time.monotonic() - before, flush=True)
before = time.monotonic()
time.sleep(0.02)
print(time.monotonic() - before, flush=True)
try:
    subprocess.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.1)
except subprocess.TimeoutExpired:
    print("real timeout", flush=True)
'''
with subprocess.Popen([sys.executable, "-c", clock_probe], env=clock_env,
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as probe:
    assert probe.stdout is not None
    try:
        assert probe.stdout.readline().strip() == "ready"
        _, status = os.waitpid(probe.pid, os.WUNTRACED)
        assert os.WIFSTOPPED(status)
        time.sleep(0.1)
    finally:
        os.kill(probe.pid, signal.SIGCONT)
    output, error = probe.communicate(timeout=10)
    lines = output.splitlines()
    check("host suspension cannot exhaust a replay deadline",
          probe.returncode == 0 and float(lines[0]) < 0.01, output + error)
    check("explicit sleep advances the replay deadline",
          float(lines[1]) >= 0.02, output + error)
    check("stdlib subprocess timeouts retain real time", lines[2:] == ["real timeout"], output + error)


# Waiters imported after startup must use real deadlines, even though gate code
# reads logical monotonic time. Exercise timeout failures and completed results.
waiter_probe = """
import concurrent.futures as futures
import queue
import time

def expires(label, call, exception):
    started = time.perf_counter()
    try:
        call()
    except exception:
        elapsed = time.perf_counter() - started
        assert 0.03 <= elapsed < 0.5, (label, elapsed)
    else:
        raise AssertionError(label + " did not time out")

q = queue.Queue()
expires("queue get", lambda: q.get(timeout=0.05), queue.Empty)
q = queue.Queue(maxsize=1)
q.put("first")
expires("queue put", lambda: q.put("second", timeout=0.05), queue.Full)
f = futures.Future()
expires("future result", lambda: f.result(timeout=0.05), futures.TimeoutError)
expires("as_completed", lambda: next(futures.as_completed([f], timeout=0.05)), futures.TimeoutError)
started = time.perf_counter()
done, pending = futures.wait([f], timeout=0.05)
assert not done and pending == {f}
assert 0.03 <= time.perf_counter() - started < 0.5
f.set_result("done")
assert f.result(timeout=0.05) == "done"
assert list(futures.as_completed([f], timeout=0.05)) == [f]
print("real waiter deadlines")
"""
waiter = subprocess.run([sys.executable, "-c", waiter_probe], env=clock_env,
                        capture_output=True, text=True, timeout=10)
check("stdlib queue and future waits retain real deadlines",
      waiter.returncode == 0 and waiter.stdout.strip() == "real waiter deadlines",
      waiter.stdout + waiter.stderr)


# ---------------------------------------------------------------- leak guard

PLANTED = {
    "lowercase street address": "mailed the packet to 1204 oak st yesterday",
    "title-case street address": "site visit at 88 Airport Blvd",
    "suite number": "move into Suite 210 next month",
    "money shorthand M": "valued at 1.2M",
    "money shorthand k per year": "rent comes to 450k/yr",
    "USD code": "invoice for USD 4,000",
    "usd after amount": "invoice for 4,000 usd",
    "dollar amount": "quoted at $18.50 per foot",
    "dollars per SF": "asking $/SF is high",
    "practice specialty name": "Smith Orthodontics wants two operatories",
    "honorific without period": "Dr Smith called back",
    "honorific all caps": "DR SMITH called back",
    "honorific with period lowercase": "spoke with dr. smith",
    "person name": "Sarah Jones signed it",
    "person name lowercase": "ask sarah jones about it",
    # The credential shapes are assembled at run time so this file never holds
    # one literally: the repo's own secret scan would (rightly) refuse it.
    "stripe live key": "export KEY=" + "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc",
    "github token": "token " + "gh" + "p_" + "16C7e42F292c6912E7710c838347Ae178B4a",
    "aws access key id": "AK" + "IA" + "IOSFODNN7EXAMPLE in the env",
    "database url with password": "psql postgres://app:" + "hunter2secret" + "@10.0.0.5:5432/carr",
    "bearer token": "curl -H 'Authorization: Bearer notarealtoken0123456789abcdef'",
    "email": "send it to someone@example.org",
    "phone": "call 251-555-0142",
    "foreign uuid": "row 123e4567-e89b-42d3-a456-426614174000",
    "client ref": "see L-1042 for the lease",
    "lease term": "the NNN charges went up",
    "square footage": "2,400 sq ft on the second floor",
    "hostname": "fetch api.example.com now",
    "file in Downloads": "open ~/Downloads/Acme Tower Report.pdf",
    "machine address": "ssh -o BatchMode=yes someone@100.81.2.46 hostname",
    "file in the Drive vault": "ls '{{HOME}}/My Drive/CARR AI/Clients'",
}

for label, text in PLANTED.items():
    check(f"pattern module catches: {label}", bdp.find_matches(text), repr(text))

check("a person name used as a JSON KEY is caught",
      any(where.endswith("<key>") for _, where in bdp.scan_value({"Sarah Jones": {"rent": 1}})))
check("a leak hidden behind JSON escaping (\\n before 'Dr Smith') is caught",
      bool(bdp.scan_value(json.loads('{"note": "first line\\nDr Smith second line"}'))))
CLEAN = ["git status --porcelain", "python3 ops/ci.sh --only gates", "sleep 5m; timeout 10m make",
         "sed -n 1,80p hooks/bash-write-gate.py", "echo 100 ms latency", "Show working tree status",
         "curl -s http://127.0.0.1:8787/health", "python3.14 -m pip install x==1.2.3",
         f"session {bdp.REPLAY_SESSION_ID}"]
for text in CLEAN:
    check(f"clean text passes: {text[:40]}", not bdp.find_matches(text), bdp.find_matches(text))

with tempfile.TemporaryDirectory(prefix="gate-replay-leak-") as tmp:
    planted_file = Path(tmp) / "planted.jsonl"
    for label, text in PLANTED.items():
        planted_file.write_text(json.dumps({"id": "x", "tool_name": "Bash",
                                            "tool_input": {"command": text}}) + "\n")
        check(f"CI scan of a fixture file catches: {label}", GR.leak_scan([planted_file]))
    planted_file.write_text(json.dumps({"id": "x", "tool_input": {"command": "git status"}}) + "\n")
    check("CI scan passes a clean fixture file", GR.leak_scan([planted_file]) == [])
    tsv = Path(tmp) / "verdict-snapshot.tsv"
    tsv.write_text("# header\nbash-write-gate.py\tPreToolUse\tbash:x\tdeny\tDr Smith refused\n")
    check("CI scan reads the TSV snapshot too", GR.leak_scan([tsv]))

committed = GR.leak_scan(GR.leak_scan_targets(manifest_data=GR.load_manifest()))
check("every committed fixture, the manifest and the snapshot scan clean", committed == [], committed[:5])

# THE LOCAL ROSTER. A plain name matches no regex; only a roster knows it. The
# name below is invented for this test and is on no roster. The real roster
# candidates are hidden throughout, so no result depends on this machine.
PLANTED_NAME = "Quillon Barstow"
NAME_SHAPES = {
    "as written": "met Quillon Barstow at the site",
    "lower case": "met quillon barstow at the site",
    "run together in camel case": "see QuillonBarstow.md for notes",
    "hyphenated in a path": "{{REPO}}/notes/quillon-barstow/intake.txt",
    "underscored": "rename Quillon_Barstow_v2",
    "as an edit's old_string": "PAIRS = [('Quillon Barstow', 'Alder Finch')]",
}
real_roster = bdp.roster
saved_roster_env = os.environ.get(bdp.ROSTER_ENV)
with tempfile.TemporaryDirectory(prefix="gate-replay-roster-") as tmp:
    roster_file = Path(tmp) / "roster.txt"
    roster_file.write_text("# test roster\n" + PLANTED_NAME + "\nAl\n")
    try:
        os.environ[bdp.ROSTER_ENV] = str(roster_file)
        check("$CARR_CLIENT_ROSTER is the first roster candidate",
              bdp.roster_candidates()[0] == roster_file, bdp.roster_candidates()[:1])
        local = bdp.load_roster([roster_file])
        check("a local roster file is read", local is not None)
        assert local is not None
        for label, text in NAME_SHAPES.items():
            check(f"the local roster catches a planted name {label}", local.hits(text), text)
        check("one word of a two-word name alone is not a hit", not local.hits("the quillon file"))
        check("a name shorter than the minimum is ignored", not local.hits("Al said so"))
        check("describe() gives a count, never a name",
              "Quillon" not in local.describe() and "1 names" in local.describe(), local.describe())

        bdp.roster = lambda: local  # type: ignore[assignment]
        check("find_matches reports a roster name with no other pattern firing",
              bdp.find_matches("met Quillon Barstow") == ["roster_name"],
              bdp.find_matches("met Quillon Barstow"))
        comment = Path(tmp) / "fixture.jsonl"
        comment.write_text("# note: Quillon Barstow\n" + json.dumps({"id": "x"}) + "\n")
        check("CI scan reads # comment lines", any("roster_name" in f for f in GR.leak_scan([comment])),
              GR.leak_scan([comment]))
        odd = Path(tmp) / "notes.md"
        odd.write_text("intake for QuillonBarstow\n")
        check("CI scan reads a fixture file of any extension",
              any("roster_name" in f for f in GR.leak_scan([odd])), GR.leak_scan([odd]))
        fixture_dir = Path(tmp) / "fx"
        (fixture_dir / "deep").mkdir(parents=True)
        (fixture_dir / "deep" / "extra.yaml").write_text("x: 1\n")
        targets = GR.leak_scan_targets(fixture_dir, Path(tmp) / "manifest.json",
                                       {"fixture_sets": {"s": {"file": "../elsewhere.jsonl"}}})
        check("scan targets include every file under the fixture dir and every manifest-named file",
              fixture_dir / "deep" / "extra.yaml" in targets
              and fixture_dir / "../elsewhere.jsonl" in targets, targets)

        # No roster on this machine: the shape patterns are the whole check.
        check("with no roster file anywhere, none is loaded",
              bdp.load_roster([Path(tmp) / "absent.txt"]) is None)
        bdp.roster = lambda: None  # type: ignore[assignment]
        check("with no roster, a bare name is not flagged", GR.leak_scan([odd]) == [], GR.leak_scan([odd]))
        email = Path(tmp) / "mail.txt"
        email.write_text("write to someone@example-clinic.com\n")
        check("with no roster, the shape patterns still run", GR.leak_scan([email]) != [])
    finally:
        bdp.roster = real_roster  # type: ignore[assignment]
        if saved_roster_env is None:
            os.environ.pop(bdp.ROSTER_ENV, None)
        else:
            os.environ[bdp.ROSTER_ENV] = saved_roster_env


# ---------------------------------------------------------------- extractor

SESSION = "7d0f3a52-1c7e-4e0b-9a55-2f3b8c1d4e6f"


def transcript_record(**body: Any) -> Dict[str, Any]:
    return {"sessionId": SESSION, "uuid": "11111111-2222-4333-8444-555555555555", **body}


def bash_use(command: str) -> Dict[str, Any]:
    return transcript_record(type="assistant", message={"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": command}}]})


with tempfile.TemporaryDirectory(prefix="gate-replay-extract-") as tmp:
    project = Path(tmp) / "-Users-booko-carr-system"
    project.mkdir()
    rows = [
        bash_use("git status --porcelain"),
        bash_use("echo mailed to 1204 oak st && git log -1"),
        bash_use(f"cat /Users/booko/carr-system/.claude/worktrees/wt-a/out/{SESSION}.log"),
        transcript_record(type="assistant", message={"role": "assistant", "content": [
            {"type": "tool_use", "id": "t2", "name": "Edit",
             "input": {"file_path": "/Users/booko/carr-system/hooks/x.py", "old_string": "a", "new_string": "b"}},
            {"type": "tool_use", "id": "t3", "name": "Write",
             "input": {"file_path": "/Users/booko/.claude/memory/note.md", "content": "a private note"}},
            {"type": "tool_use", "id": "t4", "name": "Write",
             "input": {"file_path": "/Users/booko/carr-system/_scratch_notes.py", "content": "untracked scratch"}},
            {"type": "tool_use", "id": "t5", "name": "Read",
             "input": {"file_path": "/Users/booko/Downloads/Some Client Report.pdf"}}]}),
        transcript_record(type="attachment", attachment={
            "type": "hook_additional_context", "hookEvent": "Stop", "hookName": "Stop",
            "content": ["LEDGER SWEEP — his words: \"move the lease\""]}),
        transcript_record(type="attachment", attachment={
            "type": "hook_additional_context", "hookEvent": "UserPromptSubmit",
            "hookName": "UserPromptSubmit",
            "content": [json.dumps({"schema": "rule-jev-message-delivery/v2", "session_id": SESSION,
                                    "build_receipt": {"schema": "jev-build-turn-receipt/v1",
                                                      "advisory": {"required_actions": [
                                                          {"facet": "semantic_creation"}]}}})]}),
    ]
    (project / f"{SESSION}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    out = EX.extract(tmp, tracked=({"hooks/x.py"}, {"", "hooks"}))
    blob = json.dumps(out)
    commands = [r["tool_input"]["command"] for r in out["bash-commands.jsonl"]]
    check("extractor keeps a clean real command", "git status --porcelain" in commands)
    check("extractor drops the WHOLE record carrying a planted leak (no field survives)",
          "oak st" not in blob and "git log -1" not in blob)
    check("extractor rewrites the checkout root and worktree paths to {{REPO}}",
          any(c.startswith("cat {{REPO}}/out/") for c in commands), commands)
    check("extractor rewrites a real session id to the replay session id",
          SESSION not in blob and bdp.REPLAY_SESSION_ID in blob)
    edits = [r["tool_input"]["file_path"] for r in out["file-edits.jsonl"]]
    check("extractor keeps an in-checkout edit", edits == ["{{REPO}}/hooks/x.py"], edits)
    check("extractor never extracts an edit outside the checkout", "private note" not in blob)
    check("extractor never extracts an edit to an untracked file in the checkout",
          "untracked scratch" not in blob)
    check("extractor never extracts a read of a file outside the checkout", "Client Report" not in blob)
    check("extractor never extracts a conversation-quoting advisory", "LEDGER SWEEP" not in blob)
    advisories = out["hook-advisories.jsonl"]
    check("extractor keeps a nested build_receipt advisory and marks it",
          len(advisories) == 1 and advisories[0]["has_build_receipt"] is True, advisories)

# NAME-BEARING EDITS: the 2026-09-24 leak was the client-name scrub's own edits.
check("scrub-style rename: only capitalised words swapped",
      EX.scrub_style_rename("owner = 'Quillon Barstow'", "owner = 'Alder Finch'"))
check("a code edit that changes structure is not a scrub",
      not EX.scrub_style_rename("MAX = 3000", "MAX = 4000  # raised"))
check("a lower-case identifier rename is not a scrub",
      not EX.scrub_style_rename("value = count", "value = total"))
with tempfile.TemporaryDirectory(prefix="gate-replay-extract2-") as tmp:
    project = Path(tmp) / "-Users-booko-carr-system"
    project.mkdir()
    uses = [
        ("t1", "Edit", {"file_path": "/Users/booko/carr-system/exporters/targets.py",
                        "old_string": "x = 1", "new_string": "x = 2"}),
        ("t2", "Edit", {"file_path": "/Users/booko/carr-system/hooks/x.py",
                        "old_string": "DOSSIER_FILES = {}", "new_string": "DOSSIER_FILES = load()"}),
        ("t3", "Edit", {"file_path": "/Users/booko/carr-system/hooks/x.py",
                        "old_string": "name: Quillon Barstow", "new_string": "name: Alder Finch"}),
        ("t4", "MultiEdit", {"file_path": "/Users/booko/carr-system/hooks/x.py", "edits": [
            {"old_string": "a = 1", "new_string": "a = 2"},
            {"old_string": "see Quillon", "new_string": "see Alder"}]}),
        ("t5", "Edit", {"file_path": "/Users/booko/carr-system/hooks/x.py",
                        "old_string": "timeout = 30", "new_string": "timeout = 45"}),
    ]
    row = transcript_record(type="assistant", message={"role": "assistant", "content": [
        {"type": "tool_use", "id": uid, "name": name, "input": body} for uid, name, body in uses]})
    (project / f"{SESSION}.jsonl").write_text(json.dumps(row) + "\n")
    out = EX.extract(tmp, tracked=({"hooks/x.py", "exporters/targets.py"}, {"", "hooks", "exporters"}))
    kept = [r["tool_input"] for r in out["file-edits.jsonl"]]
    blob = json.dumps(kept)
    check("extractor drops any edit to exporters/targets.py", "targets.py" not in blob, kept)
    check("extractor drops any edit that mentions DOSSIER_FILES", "DOSSIER_FILES" not in blob, kept)
    check("extractor drops a scrub-style rename, in an Edit and inside a MultiEdit",
          "Quillon" not in blob and "Alder" not in blob, kept)
    check("extractor keeps an ordinary edit", len(kept) == 1 and kept[0].get("new_string") == "timeout = 45",
          kept)


# A descheduled replay process must not spend its inspection budget before
# reaching Jev. Inject host elapsed time without waiting, then exercise the
# real supervisor's deadline branch through the logical replay clock shim.
budget_probe = r'''
import importlib.util, os, runpy, sys, time
from pathlib import Path
repo = Path(sys.argv[1])
host_elapsed = [100.0]
cpu_elapsed = [0.0]
time.monotonic = lambda: host_elapsed[0]
time.process_time = lambda: cpu_elapsed[0]
time.process_time_ns = lambda: int(cpu_elapsed[0] * 1e9)
os.environ["CARR_GATE_REPLAY_EPOCH"] = "1750000000"
runpy.run_path(str(repo / "ops/gate_replay_shim/sitecustomize.py"))
spec = importlib.util.spec_from_file_location("supervisor", repo / "hooks/jev-supervisor.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)
run = hook.Run(receipt_path=os.devnull)
called = []
def inspect_stop_boundary():
    called.append(True)
    return []
host_elapsed[0] += 20.0
run.do(inspect_stop_boundary)
assert called, run.results
assert not run.results, run.results
for _ in range(int(hook.BUDGET_SECONDS / 0.001) + 1):
    time.monotonic()
run.do(inspect_stop_boundary)
assert len(called) == 1, called
assert run.results[0]["detail"]["advice"].startswith(
    "inspect_stop_boundary unavailable (time_budget_exhausted)"), run.results
'''
budget_result = subprocess.run([sys.executable, "-c", budget_probe, str(REPO)],
                       capture_output=True, text=True, timeout=10,
                       env=git_env.fixture_env())
check("host scheduling delays cannot exhaust the replayed Stop inspection budget",
      budget_result.returncode == 0, budget_result.stderr)

# ---------------------------------------------------------------- manifest coverage

REAL_MANIFEST = GR.load_manifest()
REAL_FIXTURES = GR.load_fixtures(REAL_MANIFEST)
REAL_WIRED = GR.config_wirings()
check("the committed manifest passes its own coverage check",
      GR.check_manifest(REAL_MANIFEST, REAL_FIXTURES, REPO / "hooks", REAL_WIRED) == [])

with tempfile.TemporaryDirectory(prefix="gate-replay-hooks-") as tmp:
    hooks = Path(tmp) / "hooks"
    shutil.copytree(REPO / "hooks", hooks)
    (hooks / "brand-new-gate.py").write_text("import sys\nsys.exit(0)\n")
    errors = GR.check_manifest(REAL_MANIFEST, REAL_FIXTURES, hooks, REAL_WIRED)
    check("a hook file missing from the manifest fails, by name",
          any("brand-new-gate.py" in e for e in errors), errors)

manifest = json.loads(json.dumps(REAL_MANIFEST))
manifest["hooks"]["bash-write-gate.py"]["wirings"] = []
errors = GR.check_manifest(manifest, REAL_FIXTURES, REPO / "hooks", REAL_WIRED)
check("a wiring config has but the manifest lacks fails",
      any("bash-write-gate.py" in e and "manifest has no such wiring" in e for e in errors), errors)

wired = dict(REAL_WIRED)
wired[("bash-write-gate.py", "PreToolUse", "Bash|Write", "")] = "ops/config/hooks.json"
del wired[("bash-write-gate.py", "PreToolUse", "Bash", "")]
errors = GR.check_manifest(REAL_MANIFEST, REAL_FIXTURES, REPO / "hooks", wired)
check("a matcher changed in config but not in the manifest fails",
      any("Bash|Write" in e for e in errors) and any("not wired" in e for e in errors), errors)

manifest = json.loads(json.dumps(REAL_MANIFEST))
manifest["hooks"]["executor-tier-gate.py"]["wirings"][0]["fixtures"] = ["bash"]
wired = dict(REAL_WIRED)
errors = GR.check_manifest(manifest, REAL_FIXTURES, REPO / "hooks", wired)
check("a wiring whose matcher selects no fixture record fails",
      any("executor-tier-gate.py" in e and "selects" in e for e in errors), errors)

manifest = json.loads(json.dumps(REAL_MANIFEST))
wiring = manifest["hooks"]["bash-write-gate.py"]["wirings"][0]
wiring["no_replay"] = "too hard"
wiring["fixtures"] = []
errors = GR.check_manifest(manifest, REAL_FIXTURES, REPO / "hooks", REAL_WIRED)
check("no_replay on any event but SessionStart fails",
      any("bash-write-gate.py" in e and "only on SessionStart" in e for e in errors), errors)

manifest = json.loads(json.dumps(REAL_MANIFEST))
manifest["hooks"]["session-brief.py"]["wirings"][0]["wired_in"] = "vault-settings"
errors = GR.check_manifest(manifest, REAL_FIXTURES, REPO / "hooks", REAL_WIRED)
check("wired_in other than hooks.json or project-settings fails",
      any("session-brief.py" in e and "wired_in must be" in e for e in errors), errors)

wired = {k: v for k, v in REAL_WIRED.items() if k[0] != "session-brief.py"}
errors = GR.check_manifest(REAL_MANIFEST, REAL_FIXTURES, REPO / "hooks", wired)
check("a no_replay SessionStart wiring still has to be wired in tracked config",
      any("session-brief.py" in e and "not wired" in e for e in errors), errors)

tmp_root = tempfile.gettempdir()
old_workdir = os.environ.get("CARR_GATE_REPLAY_WORKDIR")
try:
    for label, candidate in (("the platform temp dir", os.path.join(tmp_root, "replay")),
                             ("/tmp", "/tmp/replay"), ("/private/tmp", "/private/tmp/replay")):
        os.environ["CARR_GATE_REPLAY_WORKDIR"] = candidate
        try:
            GR.replay_workdir()
            refused = False
        except GR.WorkdirRefused:
            refused = True
        check(f"a replay workdir under {label} is refused", refused)
    os.environ["CARR_GATE_REPLAY_WORKDIR"] = str(Path.home() / ".cache" / "carr-gate-replay")
    check("a replay workdir under the user cache is accepted",
          GR.replay_workdir() == Path.home() / ".cache" / "carr-gate-replay")
finally:
    if old_workdir is None:
        os.environ.pop("CARR_GATE_REPLAY_WORKDIR", None)
    else:
        os.environ["CARR_GATE_REPLAY_WORKDIR"] = old_workdir


# ---------------------------------------------------------------- replay on a miniature repo

GATE_SOURCE = r'''
import json, os, sys, time, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mini_helper  # opened through sys.path
payload = json.load(sys.stdin)
command = (payload.get("tool_input") or {}).get("command", "")
{behaviour}
'''
BEHAVIOUR_DENY_RM = '''
if command.startswith("rm "):
    print("MINI GATE refused a delete", file=sys.stderr)
    sys.exit(2)
if command.startswith("ask "):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": "MINI GATE asks"}}))
    sys.exit(0)
if command.startswith("note "):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "MINI GATE notes " + datetime.datetime.now().strftime("%A %Y")}}))
    sys.exit(0)
if command.startswith("net "):
    import socket
    try:
        host, port = command.split()[1].split(":")
        socket.create_connection((host, int(port)), timeout=2)
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "MINI GATE reached the network"}}))
    except OSError:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "MINI GATE network refused"}}))
    sys.exit(0)
if command.startswith("crash "):
    raise RuntimeError("mini gate fell over")
if command.startswith("hang "):
    time.sleep(30)
if command.startswith("failopen "):
    with open(os.environ["CARR_HOOK_GUARD_LOG"], "a") as fh:
        fh.write("mini-gate ALLOW(internal-error) KeyError: 'x'\\n")
    sys.exit(0)
if command.startswith("outside "):
    with open(os.environ["CARR_HOOK_GUARD_LOG"], "a") as fh:
        fh.write("mini-gate ALLOW(outside-repo) /elsewhere\\n")
    sys.exit(0)
if command.startswith("seeded "):
    if os.path.exists(os.path.join(os.environ["HOME"], "state", "peers.json")):
        print("MINI GATE saw the seeded state", file=sys.stderr)
        sys.exit(2)
    sys.exit(0)
if command.startswith("scribble "):
    open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scribbled.txt"), "w").write("x")
sys.exit(0)
'''
STOP_GATE = r'''
import json, sys, importlib.util, os
spec = importlib.util.spec_from_file_location("spec_helper", os.path.join(os.path.dirname(os.path.abspath(__file__)), "spec_helper.py"))
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
payload = json.load(sys.stdin)
lines = [json.loads(l) for l in open(payload["transcript_path"])]
text = json.dumps(lines)
if "claim it is live" in text:
    print(json.dumps({"decision": "block", "reason": "MINI STOP refused an unproven claim"}))
sys.exit(0)
'''

# A real listener on loopback, so "refused" can only mean the shim refused it:
# without the shim this connection succeeds.
LISTENER = socket.socket()
LISTENER.bind(("127.0.0.1", 0))
LISTENER.listen(8)
NET = f"net 127.0.0.1:{LISTENER.getsockname()[1]}"
COMMANDS = ["git status", "rm -rf build", "ask first", "note today", NET]


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, env=git_env.fixture_env(), check=True, capture_output=True)


def mini_repo(root: Path, behaviour: str, extra: str = "") -> Path:
    repo = root / "src"
    if repo.exists():
        shutil.rmtree(repo)
    (repo / "hooks").mkdir(parents=True)
    (repo / "lib").mkdir()
    for name in ("hook-meter-run.py", "hook_meter.py"):
        shutil.copy2(REPO / "hooks" / name, repo / "hooks" / name)
    (repo / "hooks" / "mini_helper.py").write_text("VALUE = 1\n")
    (repo / "hooks" / "spec_helper.py").write_text("VALUE = 2\n")
    (repo / "hooks" / "mini-gate.py").write_text(extra + GATE_SOURCE.replace("{behaviour}", behaviour))
    (repo / "hooks" / "mini-stop.py").write_text(STOP_GATE)
    (repo / ".gitignore").write_text("out/\n.venv/\n")
    git(repo, "init", "-q", "-b", "work")
    git(repo, "add", "-A")
    # Untracked, so the sandbox must never see it (it copies the index only).
    (repo / "hooks" / "untracked_scratch.py").write_text("SCRATCH = 1\n")
    return repo


def mini_manifest(fixture_dir: Path) -> Dict[str, Any]:
    return {
        "schema": "gate-replay-manifest/v1",
        "pinned_utc": "2026-09-23T15:00:00Z",
        "fixture_sets": {
            "bash": {"file": "bash.jsonl", "origin": "real", "kind": "tool"},
            "advisories": {"file": "adv.jsonl", "origin": "real", "kind": "turn"},
        },
        "turn_prompt": "Make the change.",
        "stop_turn_shapes": {"claims": {"steps": [], "final": "Done, and I claim it is live."}},
        "hooks": {
            "mini-gate.py": {"role": "gate", "wirings": [{"event": "PreToolUse", "matcher": "Bash", "fixtures": ["bash"]}]},
            "mini-stop.py": {"role": "gate", "wirings": [{"event": "Stop", "matcher": "", "fixtures": ["advisories"]}]},
            "mini_helper.py": {"role": "helper"},
            "spec_helper.py": {"role": "helper"},
            "hook-meter-run.py": {"role": "wrapper"},
            "hook_meter.py": {"role": "helper"},
        },
        "lib_helpers": [],
    }


def write_fixtures(fixture_dir: Path, commands: List[str]) -> None:
    fixture_dir.mkdir(parents=True, exist_ok=True)
    with open(fixture_dir / "bash.jsonl", "w") as handle:
        for index, command in enumerate(commands):
            handle.write(json.dumps({"id": f"{index:012x}", "tool_name": "Bash",
                                     "tool_input": {"command": command}}) + "\n")
    with open(fixture_dir / "adv.jsonl", "w") as handle:
        handle.write(json.dumps({"id": "0000000000aa", "hookEvent": "UserPromptSubmit",
                                 "hookName": "UserPromptSubmit",
                                 "content": ["{\"schema\": \"jev-build-turn-receipt/v1\"}"],
                                 "has_build_receipt": True}) + "\n")


def run(repo: Path, fixture_dir: Path, manifest: Dict[str, Any]) -> Any:
    fixtures = GR.load_fixtures(manifest, fixture_dir)
    return GR.replay(manifest, fixtures, jobs=2, source=repo, progress=False)


def verdicts(report: Any) -> Dict[str, Any]:
    return {r.inv.record["tool_input"]["command"] if "tool_input" in r.inv.record else r.inv.gate: r
            for r in report.results}


work = Path(tempfile.mkdtemp(prefix="gate-replay-selftest-"))
# The sandboxes themselves must not live under a temp prefix (the runner
# refuses one), so they go under the user cache; the mini repos stay in temp.
RUNS = Path.home() / ".cache" / "carr-gate-replay-selftest" / f"runs-{os.getpid()}"
os.environ["CARR_GATE_REPLAY_WORKDIR"] = str(RUNS)
calls: List[List[str]] = []
real_run = GR.subprocess.run


def spying_run(*args: Any, **kwargs: Any) -> Any:
    argv = args[0] if args else kwargs.get("args")
    if isinstance(argv, list) and argv and argv[0] == "git":
        calls.append([str(a) for a in argv])
    return real_run(*args, **kwargs)


try:
    GR.subprocess.run = spying_run
    fixtures_dir = work / "fixtures"
    write_fixtures(fixtures_dir, COMMANDS)
    manifest = mini_manifest(fixtures_dir)
    repo = mini_repo(work, BEHAVIOUR_DENY_RM)
    base = run(repo, fixtures_dir, manifest)
    got = verdicts(base)
    check("allow: a clean command", got["git status"].verdict == "allow", got["git status"].verdict)
    check("deny: exit 2 on PreToolUse", got["rm -rf build"].verdict == "deny"
          and got["rm -rf build"].reason == "MINI GATE refused a delete", got["rm -rf build"].row)
    check("ask: permissionDecision ask", got["ask first"].verdict == "ask", got["ask first"].row)
    check("announce: additionalContext without a decision", got["note today"].verdict == "announce")
    check("the clock inside the gate is the pinned Wednesday",
          got["note today"].reason == "MINI GATE notes Wednesday #", got["note today"].reason)
    check("the network is refused inside the gate",
          got[NET].reason == "MINI GATE network refused", got[NET].reason)
    check("reopen: a Stop gate's decision block on a real advisory turn",
          got["mini-stop.py"].verdict == "reopen", got["mini-stop.py"].row)
    check("helper evidence: a sys.path import and a spec_from_file_location load are both seen",
          GR.helper_errors(manifest, base.results) == [], GR.helper_errors(manifest, base.results))
    check("no gate mutated the sandbox on a clean run", base.mutated == [], base.mutated)
    snapshot = GR.snapshot_rows(base.results)
    check("the snapshot lists each non-allow verdict and one counts row per gate and event",
          sum(1 for r in snapshot if r[2] == GR.COUNTS) == 2
          and sum(1 for r in snapshot if r[2] != GR.COUNTS) == 5, snapshot)

    again = run(repo, fixtures_dir, manifest)
    check("two runs produce the identical snapshot", GR.snapshot_rows(again.results) == snapshot)

    comment_only = mini_repo(work, BEHAVIOUR_DENY_RM,
                             extra="# covered by ops/fixtures/real-replay/bash-commands.jsonl replay\n"
                                   "# load_fixture('ops/fixtures/real-replay') selftest real-replay\n")
    commented = run(comment_only, fixtures_dir, manifest)
    check("a comment naming the fixtures changes no verdict (text cannot satisfy or break it)",
          GR.diff_rows(snapshot, GR.snapshot_rows(commented.results)) == [])

    loosened = mini_repo(work, BEHAVIOUR_DENY_RM.replace('command.startswith("rm ")', 'command.startswith("rmdir ")'))
    changed = GR.diff_rows(snapshot, GR.snapshot_rows(run(loosened, fixtures_dir, manifest).results))
    check("a behaviour change with no new selftest or comment is caught as a verdict diff",
          len(changed) == 2 and any("rm -rf" not in c and "deny=1" in c for c in changed)
          and any("deny -> allow" in c for c in changed), changed)

    edited_snapshot = [tuple(r[:3]) + ("allow", "-") if r[3] == "deny" else r for r in snapshot]
    check("editing the snapshot alone, without the gate change, is caught",
          GR.diff_rows(edited_snapshot, snapshot) != [])

    helper_manifest = json.loads(json.dumps(manifest))
    helper_manifest["hooks"]["never_used.py"] = {"role": "helper"}
    helper_manifest["lib_helpers"] = ["lib/not_opened.py"]
    herrors = GR.helper_errors(helper_manifest, base.results)
    check("a listed helper nothing executed fails", any("never_used.py" in e for e in herrors), herrors)
    check("a listed lib helper nothing executed fails", any("lib/not_opened.py" in e for e in herrors), herrors)

    repo = mini_repo(work, BEHAVIOUR_DENY_RM)  # the loosened variant above replaced it
    check("the sandbox copies only tracked files: an untracked file in the source is left out",
          "hooks/untracked_scratch.py" not in GR.tracked_files(repo)
          and "hooks/mini-gate.py" in GR.tracked_files(repo))

    write_fixtures(fixtures_dir, ["failopen now", "outside now"])
    got = verdicts(run(repo, fixtures_dir, manifest))
    check("a gate that logs ALLOW(internal-error) and exits 0 is a fail-open crash, not an allow",
          got["failopen now"].verdict == "error" and got["failopen now"].reason == "fail-open",
          got["failopen now"].row)
    check("an ordinary ALLOW(outside-repo) decision is still an allow",
          got["outside now"].verdict == "allow", got["outside now"].row)

    # Scenarios: each names the verdict it expects, whatever the snapshot says.
    write_fixtures(fixtures_dir, ["git status"])
    scen_manifest = json.loads(json.dumps(manifest))
    scen_manifest["fixture_sets"]["scenarios"] = {"file": "scen.jsonl", "origin": "synthetic", "kind": "scenario"}
    scen_manifest["hooks"]["mini-gate.py"]["wirings"][0]["fixtures"] = ["bash", "scenarios"]
    scen_rows = [
        {"id": "5c00000000a1", "gate": "mini-gate.py", "event": "PreToolUse", "matcher": "Bash",
         "expect": "deny", "why": "delete", "tool_name": "Bash", "tool_input": {"command": "rm -rf x"}},
        {"id": "5c00000000a2", "gate": "mini-gate.py", "event": "PreToolUse", "matcher": "Bash",
         "expect": "deny", "why": "wrongly expected", "tool_name": "Bash", "tool_input": {"command": "git log"}},
        {"id": "5c00000000a3", "gate": "other-gate.py", "event": "PreToolUse", "matcher": "Bash",
         "expect": "deny", "why": "another gate's record", "tool_name": "Bash", "tool_input": {"command": "rm x"}},
    ]
    (fixtures_dir / "scen.jsonl").write_text("".join(json.dumps(r) + "\n" for r in scen_rows))
    scen = run(repo, fixtures_dir, scen_manifest)
    berrors = GR.behaviour_errors(scen_manifest, scen.results)
    check("a scenario that reaches its expected verdict passes",
          not any("5c00000000a1" in e for e in berrors), berrors)
    check("a scenario whose verdict differs from its expect fails CI",
          any(e.startswith("SCENARIO") and "5c00000000a2" in e for e in berrors), berrors)
    check("a scenario record is replayed only through the gate it names",
          not any("5c00000000a3" in r.inv.fixture_key for r in scen.results))

    seed_rows = [
        {"id": "5c00000000b1", "gate": "mini-gate.py", "event": "PreToolUse", "matcher": "Bash",
         "expect": "deny", "why": "seeded", "home_files": {"state/peers.json": {"peers": 2}},
         "tool_name": "Bash", "tool_input": {"command": "seeded now"}},
        {"id": "5c00000000b2", "gate": "mini-gate.py", "event": "PreToolUse", "matcher": "Bash",
         "expect": "allow", "why": "no seed, and the last invocation's seed must be gone",
         "tool_name": "Bash", "tool_input": {"command": "seeded again"}},
    ]
    (fixtures_dir / "scen.jsonl").write_text("".join(json.dumps(r) + "\n" for r in seed_rows))
    seeded = run(repo, fixtures_dir, scen_manifest)
    serrors = [e for e in GR.behaviour_errors(scen_manifest, seeded.results) if e.startswith("SCENARIO")]
    check("a scenario's home_files reach the gate, and never leak into the next invocation",
          serrors == [], serrors)
    check("the vendor-host placeholder is filled in at run time, never stored",
          GR.substitute("https://{{BANNED_VENDOR_HOST}}/x", "/r", "/h") == "https://costar" + ".com/x")

    allow_manifest = json.loads(json.dumps(manifest))
    allow_run = run(repo, fixtures_dir, allow_manifest)
    aerrors = GR.behaviour_errors(allow_manifest, allow_run.results)
    check("a wiring whose every verdict is allow, with no allow_only reason, fails",
          any("mini-gate.py" in e and "every replayed verdict is allow" in e for e in aerrors), aerrors)
    allow_manifest["hooks"]["mini-gate.py"]["wirings"][0]["allow_only"] = "records only"
    check("the same wiring with an allow_only reason passes",
          not any("mini-gate.py" in e for e in GR.behaviour_errors(allow_manifest, allow_run.results)))
    scen_manifest["hooks"]["mini-gate.py"]["wirings"][0]["allow_only"] = "claims it never denies"
    check("an allow_only wiring that does deny fails",
          any("declared allow_only" in e for e in GR.behaviour_errors(scen_manifest, scen.results)))

    write_fixtures(fixtures_dir, ["crash now", "hang now", "scribble now"])
    old_timeout = GR.INVOCATION_TIMEOUT
    GR.INVOCATION_TIMEOUT = 5.0
    try:
        bad = run(repo, fixtures_dir, manifest)
    finally:
        GR.INVOCATION_TIMEOUT = old_timeout
    got = verdicts(bad)
    check("a gate that crashes on a fixture is an error verdict",
          got["crash now"].verdict == "error" and "RuntimeError" in got["crash now"].detail,
          got["crash now"].detail)
    check("a gate that hangs is an error verdict", got["hang now"].verdict == "error"
          and got["hang now"].reason == "timeout", got["hang now"].row)
    check("a gate that writes outside out/ is reported", any("scribbled.txt" in m for m in bad.mutated),
          bad.mutated)

    refs = [c for c in calls if any(word in c for word in
                                    ("diff", "merge-base", "rev-parse", "fetch", "log", "show", "main", "origin/main"))]
    allowed = {"ls-files", "init", "add", "commit", "status"}
    check("the replay asks git nothing about any ref: only ls-files, init, add, commit, status",
          refs == [] and all(c[1] in allowed for c in calls if len(c) > 1),
          sorted({c[1] for c in calls if len(c) > 1}))
finally:
    GR.subprocess.run = real_run
    LISTENER.close()
    shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(RUNS, ignore_errors=True)

sys.exit(CHECK.summary())
