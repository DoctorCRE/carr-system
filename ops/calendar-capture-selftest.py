#!/usr/bin/env python3
"""calendar-capture-selftest.py — prove the unattended calendar capture refuses
rather than reporting an empty answer.

THE PROPERTY UNDER TEST is one sentence: a DENIED read and an empty calendar must
never look the same. That distinction is the whole reason this job exists in the
shape it does, and it is not theoretical — on 2026-08-14 a verb answered emptily
instead of refusing, a session read the empty answer as truth, and concluded a
settled council ruling did not exist. The same confusion in calendar capture would
silently stop touches reaching the deal record while every run reported success.

The bundle is stubbed by putting a fake `open` first on PATH, so these cases run
with no calendar, no permission prompt and no GUI — which is what lets them run in
CI on a machine that has none of those.

    .venv/bin/python ops/calendar-capture-selftest.py
"""
import os
import json
import importlib.util
import atexit
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "bin" / "calendar-eventkit-capture.sh"

failures: list[str] = []
fixture_roots: list[Path] = []


def cleanup_fixtures() -> None:
    for root in fixture_roots:
        shutil.rmtree(root, ignore_errors=True)


atexit.register(cleanup_fixtures)


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        failures.append(name)


def fixture(app_exists=True, appends="", *, dump_json="", matcher_json=""):
    """A throwaway CARR_REPO with a stub `open` that appends `appends` to the log."""
    root = Path(tempfile.mkdtemp(prefix="calcap-"))
    fixture_roots.append(root)
    (root / "out").mkdir()
    (root / "bin").mkdir()
    (root / "tools").mkdir()
    if app_exists:
        (root / "tools" / "CARR Calendar Access.app").mkdir()
    # The stub stands in for the bundle: it writes whatever this case needs into
    # the access log, exactly as the real bundle does, and never touches a calendar.
    stub = root / "stubbin"
    stub.mkdir()
    open_script = (
        "#!/bin/sh\n"
        'for target do :; done\n'
        'mkdir -p "$target"\n'
        f"cat >> \"$target/calendar-access.log\" <<'EOF'\n{appends}\nEOF\n"
    )
    if dump_json:
        open_script += f"cat > \"$target/calendar-attendees.json\" <<'EOF'\n{dump_json}\nEOF\n"
    open_script += "exit 0\n"
    (stub / "open").write_text(open_script)
    (stub / "open").chmod(0o755)
    if matcher_json:
        (root / "tools" / "calendar-touch-matcher.py").write_text(
            "#!/usr/bin/env python3\n"
            f"print({matcher_json!r})\n")
    return root, stub


def run(root, stub, *args, timeout=90, extra_env=None, input_text=None):
    writer = root / "run.sh"
    if writer.exists() and "catch-me-up" not in writer.read_text():
        original = root / "writer-original"
        shutil.copy2(writer, original)
        writer.write_text("#!/usr/bin/env python3\nimport json,os,sys\n"
            "if sys.argv[2]=='catch-me-up':\n"
            " print(json.dumps({'calendar_history':[]}))\n"
            "elif sys.argv[2]=='add-room-turn': print('{\"ok\":true,\"seq\":123}')\n"
            "else: os.execv('./writer-original',['./writer-original',*sys.argv[1:]])\n")
        writer.chmod(0o755)
    env = dict(os.environ)
    env["CARR_REPO"] = str(root)
    env["CARR_CALENDAR_CAPTURE_WAIT_SECONDS"] = "2"
    env["PATH"] = f"{stub}:{env['PATH']}"
    env.update(extra_env or {})
    return subprocess.run(["sh", str(SCRIPT), *args], env=env,
                          input=input_text, capture_output=True, text=True, timeout=timeout)


print("calendar capture — refusal beats an empty answer")

# 1. THE CENTRAL CASE. Access denied must be a hard failure, and must NOT be
#    reported as a successful run that happened to find nothing.
root, stub = fixture(appends="RESULT: Calendars access DENIED.\nexit=3")
p = run(root, stub)
out = p.stdout + p.stderr
check("DENIED exits non-zero", p.returncode != 0, f"exit={p.returncode}")
check("DENIED exits with its OWN code (3), distinct from a generic failure",
      p.returncode == 3, f"exit={p.returncode}")
check("DENIED says permission, not emptiness", "DENIED" in out)
check("DENIED never claims zero touches",
      "no exact matches" not in out and "0 touches" not in out,
      "a permission answer was dressed up as an empty result")
check("DENIED tells the reader how to grant it",
      "Privacy" in out and "Calendars" in out)

# 2. A stale SUCCESS from an earlier run must not be mistaken for this one's.
#    A naive tail of the log would read the old success and pass.
root, stub = fixture(appends="RESULT: Calendars access DENIED.\nexit=3")
(root / "out" / "calendar-access.log").write_text(
    "--- 2026-08-13T19:13:04Z CARR Calendar Access (dump) ---\n"
    "events scanned: 936; carrying attendees: 386\n"
    "exit=0\n")
p = run(root, stub)
check("a previous run's success does not mask this run's denial",
      p.returncode == 3, f"exit={p.returncode} — the marker scoping failed")

# 3. A missing bundle is its own diagnosis, not a permission error.
root, stub = fixture(app_exists=False, appends="exit=0")
p = run(root, stub)
out = p.stdout + p.stderr
check("a missing bundle fails and names the bundle",
      p.returncode != 0 and "bundle is missing" in out, f"exit={p.returncode}")
check("a missing bundle explains WHY it matters",
      "cannot prompt" in out)

# 4. A read that never finishes must fail rather than hang forever or pass.
root, stub = fixture(appends="--- started, no exit line ever written ---")
p = run(root, stub, timeout=120)
check("a read that never completes fails", p.returncode != 0, f"exit={p.returncode}")
check("an unfinished read says so", "did not finish" in (p.stdout + p.stderr))

# 5. The Control Plane shadow stores stdout in an immutable receipt.  Its
# aggregate-only mode must isolate scratch files and never print attendee data.
sensitive = {
    "counts": {"emails": 3, "exact": 1, "domain": 1, "unknown": 1,
               "internal": 0, "upcoming": 0},
    "exact": [{"ref": "L-PRIVATE", "email": "person@example.com",
               "last_seen": "2026-08-16", "events": []}],
    "domain": [{"email": "domain@example.com", "org": "Private Org"}],
    "unknown": [{"email": "unknown@example.com", "last_seen": "2026-08-16"}],
}
root, stub = fixture(
    appends="events scanned: 12; carrying attendees: 3\nexit=0",
    dump_json="{}", matcher_json=json.dumps(sensitive))
isolated = root / "isolated-calendar-shadow"
p = run(root, stub, "--dry-run", "--receipt-safe", "--days", "7",
        extra_env={"CARR_CALENDAR_OUTPUT_ROOT": str(isolated)})
out = p.stdout + p.stderr
check("receipt-safe EventKit shadow succeeds with a finite aggregate marker",
      p.returncode == 0
      and "calendar-capture: source=eventkit mode=shadow scanned=12 exact=1 domain=1 unknown=1 writes=0 failed=0" in out,
      f"exit={p.returncode} output={out[-300:]}")
check("receipt-safe EventKit shadow prints no attendee or record identity",
      not any(value in out for value in (
          "L-PRIVATE", "person@example.com", "domain@example.com",
          "unknown@example.com", "Private Org")))
check("receipt-safe EventKit shadow confines scratch evidence to its output root",
      bool(list((isolated / "calendar-runs").glob("*/calendar-access.log")))
      and bool(list((isolated / "calendar-runs").glob("*/calendar-attendees.json")))
      and (isolated / "calendar-touch-proposals.json").is_file()
      and not (root / "out" / "calendar-access.log").exists())

# 6. Canary must stop before the normal unmatched-attendee intake gate.  The
# fixture deliberately has no intake helper; a successful canary therefore
# proves it wrote only the dedicated receipt seam, not a live activity/intake.
root, stub = fixture(appends="events scanned: 12; carrying attendees: 3\nexit=0",
                     dump_json="{}", matcher_json=json.dumps(sensitive))
(root / "tools" / "calendar-canary-result.py").write_text(
    "#!/usr/bin/env python3\nprint('calendar-capture: canary-result {\"contact_count\":1,\"domain_count\":1,\"exact_count\":1,\"snapshot_digest\":\"8057599fd071214d206d766b28876a7ef467c6e86a44ecdb6b19bbbe785ccfac\",\"source_snapshot_id\":\"00000000-0000-0000-0000-000000000000\",\"unknown_count\":1}')\n")
p = run(root, stub, "--canary", "--days", "7", extra_env={
    "CARR_CONTROL_PLANE_MODE": "canary"}, input_text='{"source_snapshot_id":"00000000-0000-0000-0000-000000000000","snapshot_digest":"8057599fd071214d206d766b28876a7ef467c6e86a44ecdb6b19bbbe785ccfac","contact_count":1,"snapshot_text":"[{\\"email\\":\\"fixture@example.com\\",\\"name\\":\\"Fixture\\",\\"org\\":null,\\"ref\\":\\"C-1\\"}]"}')
check("canary bypasses normal intake and emits only its strict aggregate",
      p.returncode == 0 and 'calendar-capture: canary-result' in p.stdout)

# 7. One unresolved external attendee cannot suppress an independently proven
# exact match or refuse its capture. Only the exact match is
# eligible for the canonical activity call.  All identities here are synthetic.
mixed = {
    "counts": {"emails": 2, "exact": 1, "domain": 0, "unknown": 1,
               "internal": 0, "upcoming": 0},
    "exact": [{"ref": "C-TEST", "email": "known@example.test",
               "last_seen": "2026-09-25", "events": [{"day": "2026-09-25",
               "title": "Synthetic meeting", "event_id": "synthetic-one", "start_at": "2026-09-25T12:00:00+00:00"}]}],
    "domain": [],
    "unknown": [{"email": "new@example.test", "last_seen": "2026-09-25"}],
}
root, stub = fixture(appends="events scanned: 2; carrying attendees: 2\nexit=0",
                     dump_json="{}", matcher_json=json.dumps(mixed))
shutil.copy2(REPO / "tools" / "calendar-intake-gate.py",
             root / "tools" / "calendar-intake-gate.py")
(root / "run.sh").write_text(
    "#!/bin/sh\nprintf '%s\\n' \"$2\" >> out/canonical-calls.txt\n"
    "printf '{\"ok\": true}\\n'\n")
(root / "run.sh").chmod(0o755)
p = run(root, stub)
calls = (root / "out" / "canonical-calls.txt")
check("pending unmatched intake does not refuse exact capture", p.returncode == 0,
      f"exit={p.returncode}")
check("unresolved intake preserves the independent exact touch",
      calls.is_file() and calls.read_text().splitlines() == ["log-activity"],
      f"calls={calls.read_text() if calls.exists() else 'none'}")
check("live capture output is aggregate-only",
      not any(value in p.stdout + p.stderr for value in (
          "new@example.test", "known@example.test", "C-TEST", "Synthetic meeting")))
(root / "run.sh").write_text(
    "#!/bin/sh\nprintf 'refused known@example.test C-TEST\n'\nexit 1\n")
(root / "run.sh").chmod(0o755)
p = run(root, stub)
check("failed exact write takes precedence without leaking call output",
      p.returncode == 1 and not any(value in p.stdout + p.stderr for value in (
          "new@example.test", "known@example.test", "C-TEST", "Synthetic meeting")))

# Multiple synthetic meetings must each reach client, lead and vendor records.
# Use the real matcher and workbook reader, rather than precomputed proposals.
import datetime
import openpyxl
root, stub = fixture(appends="events scanned: 9; carrying attendees: 9\nexit=0",
                     dump_json=json.dumps({"schema": "calendar-events/v2", "events": [
                         {"event_id": f"synthetic-{i}", "start_at": (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1, hours=i)).isoformat(),
                          "title": f"Synthetic meeting {i}", "emails": [email]}
                         for i, email in enumerate([
                             "client@clinic.example.test", "lead@practice.example.test",
                             "vendor@service.example.test", "client@clinic.example.test",
                             "client@clinic.example.test", "client@clinic.example.test",
                             "client@clinic.example.test", "client@clinic.example.test",
                             "new@unknown.example.test"])
                     ]}))
shutil.copy2(REPO / "tools/calendar-touch-matcher.py", root / "tools/calendar-touch-matcher.py")
shutil.copy2(REPO / "tools/calendar-intake-gate.py", root / "tools/calendar-intake-gate.py")
(root / ".venv").symlink_to(sys.prefix, target_is_directory=True)
(root / "exporters").mkdir()
(root / "exporters/__init__.py").write_text("")
(root / "exporters/common.py").write_text(f"EXPORT_HOME = {str(root / 'exports')!r}\n")
for rel, sheet, headers, row in [
    ("DNA/Clients/client-roster.xlsx", "Clients", ["Client ID", "Name", "Practice / Entity", "Email"],
     ["C-TEST", "Synthetic Client", "Synthetic Clinic", "client@clinic.example.test"]),
    ("DNA/Leads/lead-registry.xlsx", "Registry", ["Lead ID", "Contact Name", "Practice", "Email"],
     ["L-TEST", "Synthetic Lead", "Synthetic Practice", "lead@practice.example.test"]),
    ("DNA/Network/vendors.xlsx", "Vendors", ["ID", "Name", "Company", "Email"],
     ["V-TEST", "Synthetic Vendor", "Synthetic Service", "vendor@service.example.test"]),
]:
    path = root / "exports" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(headers)
    ws.append(row)
    wb.save(path)
(root / "run.sh").write_text(
    "#!/usr/bin/env python3\nimport json,sys\n"
    "with open('out/activity-args.jsonl','a') as f: f.write(sys.argv[3]+'\\n')\n"
    "print('{\"ok\":true}')\n")
(root / "run.sh").chmod(0o755)
p = run(root, stub)
arg_path = root / "out/activity-args.jsonl"
activities = [json.loads(line) for line in arg_path.read_text().splitlines()] if arg_path.exists() else []
check("every synthetic meeting reaches its matched client, lead or vendor despite an unknown",
      p.returncode == 0 and len(activities) == 8
      and sorted(a["ref"] for a in activities) == ["C-TEST"] * 6 + ["L-TEST", "V-TEST"],
      f"exit={p.returncode}, touches={len(activities)}")
check("unmatched list retains the unknown without assigning a touch",
      json.loads((root / "out/calendar-touch-proposals.json").read_text())["counts"]["unknown"] == 1)
check("distinct same-day meetings have distinct retry keys",
      len({a["idempotency_key"] for a in activities}) == 8)
p = run(root, stub)
repeated = [json.loads(line) for line in arg_path.read_text().splitlines()] if arg_path.exists() else []
check("repeat capture keeps identical event keys and activity payloads",
      len(repeated) == 16 and repeated[:8] == repeated[8:])
p = run(root, stub, "--dry-run", "--receipt-safe")
check("dry-run reports would-write count without a canonical call or identity output",
      p.returncode == 0 and "would_write=8" in p.stdout
      and len(arg_path.read_text().splitlines()) == 16
      and not any(x in p.stdout + p.stderr for x in ("@", "C-TEST", "L-TEST", "V-TEST", "Synthetic meeting")))

# Execute the actual nightly calendar block with a fake step wrapper; no other
# nightly step, scheduler, database or credential is run.
nightly = (REPO / "bin/nightly.sh").read_text()
block = nightly[nightly.index("# Added 2026-08-06 (loop #180)"):nightly.index("# MAIL, loop #169")]
shutil.copy2(SCRIPT, root / "bin/calendar-eventkit-capture.sh")
(root / "bin/calendar-eventkit-capture.sh").chmod(0o755)
(root / "bin/archive-calendar.sh").write_text("#!/bin/sh\nexit 0\n")
(root / "bin/archive-calendar.sh").chmod(0o755)
env = {**os.environ, "CARR_REPO": str(root), "PATH": str(stub) + os.pathsep + os.environ.get("PATH", ""),
       "CARR_CALENDAR_CAPTURE_WAIT_SECONDS": "1"}
p = subprocess.run(["/bin/zsh", "-c", 'step() { shift; "$@"; }\n' + block],
                   cwd=root, env=env, capture_output=True, text=True, timeout=15)
check("nightly calendar step executes the capture and continues with unmatched pending",
      p.returncode == 0 and "source=eventkit mode=live" in p.stdout
      and len(arg_path.read_text().splitlines()) == 24)

# Invalid intake evidence still fails, even when unknowns are deferred.
(root / "out/calendar-intake-evidence.json").write_text("not-json")
p = run(root, stub)
check("malformed intake ledger still refuses capture completion", p.returncode == 65)

# 8. Matcher diagnostics can contain attendee data. The launcher and Control
# Plane persist command output, so only fixed failure classes may leave this job.
root, stub = fixture(appends="events scanned: 2; carrying attendees: 2\nexit=0",
                     dump_json="{}")
(root / "tools" / "calendar-touch-matcher.py").write_text(
    "import sys\nprint('matcher failed for synthetic@example.test', file=sys.stderr)\nsys.exit(5)\n")
p = run(root, stub)
check("matcher failure output is aggregate-only",
      p.returncode == 1 and "synthetic@example.test" not in p.stdout + p.stderr)
(root / "tools" / "calendar-touch-matcher.py").write_text(
    "import sys\nprint('operation not permitted for synthetic@example.test', file=sys.stderr)\nsys.exit(5)\n")
p = run(root, stub)
check("matcher permission failure keeps safe diagnosis without identity",
      p.returncode == 4 and "FULL DISK ACCESS" in p.stderr
      and "synthetic@example.test" not in p.stdout + p.stderr)

# 9. Only successful process completion and a top-level JSON boolean true can
# acknowledge an activity write. Error payloads stay out of persisted logs.
response_cases = [
    ("nested success cannot override top-level refusal",
     '{"ok":false,"nested":{"ok":true}}', 0, False),
    ("nonzero helper status cannot acknowledge a write",
     '{"ok":true}', 1, False),
    ("invalid JSON cannot acknowledge a write",
     'not-json "ok":true', 0, False),
    ("array response cannot acknowledge a write",
     '[{"ok":true}]', 0, False),
    ("numeric truth cannot acknowledge a write",
     '{"ok":1}', 0, False),
    ("missing success cannot acknowledge a write",
     '{"activity_id":"synthetic-activity"}', 0, False),
    ("JSON whitespace preserves valid success",
     '{"ok" : true, "activity_id":"synthetic-activity"}', 0, True),
]
exact_only = {**mixed, "unknown": [],
              "counts": {"emails": 1, "exact": 1, "domain": 0, "unknown": 0,
                         "internal": 0, "upcoming": 0}}
for name, response, status, succeeds in response_cases:
    root, stub = fixture(appends="events scanned: 1; carrying attendees: 1\nexit=0",
                         dump_json="{}", matcher_json=json.dumps(exact_only))
    shutil.copy2(REPO / "tools" / "calendar-intake-gate.py",
                 root / "tools" / "calendar-intake-gate.py")
    (root / "run.sh").write_text(
        "#!/usr/bin/env python3\nimport sys\n"
        f"print({response!r})\n"
        "print('known@example.test C-TEST Synthetic meeting', file=sys.stderr)\n"
        f"sys.exit({status})\n")
    (root / "run.sh").chmod(0o755)
    p = run(root, stub)
    output = p.stdout + p.stderr
    marker = "writes=1 failed=0" if succeeds else "writes=0 failed=1"
    check(name, p.returncode == (0 if succeeds else 1) and marker in output
          and not any(value in output for value in (
              "known@example.test", "C-TEST", "Synthetic meeting", response)))

# Regression 6: generic failure cannot authorize a stale dump.
root, stub = fixture(appends="exit=1", matcher_json=json.dumps(exact_only))
(root / "out/calendar-attendees.json").write_text("{}")
(root / "run.sh").write_text("#!/bin/sh\necho '{\"ok\":true}'\n")
(root / "run.sh").chmod(0o755)
p = run(root, stub)
check("failed reader cannot write from a stale dump", p.returncode == 1 and "logged exact touch" not in p.stdout)

# Regression 5: a second capture must not complete the paused first reader.
root, stub = fixture(matcher_json=json.dumps(exact_only))
reader_started = root / "out/reader-started"
reader_release = root / "out/reader-release"
(stub / "open").write_text(
    "#!/usr/bin/env python3\nimport os,sys,time\nfrom pathlib import Path\n"
    "if os.environ.get('CALCAP_TEST_PAUSE_READER') == '1':\n"
    f" Path({str(reader_started)!r}).touch()\n"
    " deadline = time.monotonic() + 10\n"
    f" while not Path({str(reader_release)!r}).exists():\n"
    "  if time.monotonic() >= deadline: sys.exit(1)\n"
    "  time.sleep(0.01)\n"
    "else:\n"
    " target = Path(sys.argv[-1])\n"
    " (target / 'calendar-attendees.json').write_text('{}')\n"
    " with (target / 'calendar-access.log').open('a') as log: log.write('exit=0\\n')\n")
env = dict(os.environ, CARR_REPO=str(root), PATH=f"{stub}:{os.environ['PATH']}",
           CARR_CALENDAR_CAPTURE_WAIT_SECONDS="3", CALCAP_TEST_PAUSE_READER="1")
# Model a slow process start so a wall-clock guess cannot establish ordering.
a = subprocess.Popen([
    sys.executable, "-c",
    "import os,sys,time; time.sleep(1); os.execvp('sh',['sh',*sys.argv[1:]])",
    str(SCRIPT), "--dry-run",
], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
try:
    # The bundle launch follows lock acquisition. Hold it there so scheduling
    # cannot let the first capture finish before the competing capture starts.
    deadline = time.monotonic() + 10
    while not reader_started.exists() and a.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert reader_started.exists(), "first capture never reached the paused reader"
    b = run(root, stub, extra_env={"CALCAP_TEST_PAUSE_READER": "0"})
finally:
    reader_release.touch()
    aout, aerr = a.communicate(timeout=10)
check("concurrent dry/live capture cannot acknowledge another read",
      a.returncode == 1 and "read did not finish" in aerr
      and b.returncode == 75 and "another capture is still reading" in b.stderr
      and "source=eventkit" not in aout,
      f"first={a.returncode}, second={b.returncode}; {aerr.strip()}; {b.stderr.strip()}")

# A timed-out reader completes during the next invocation. Its private exit/dump
# must never promote the next reader, which has not completed at all.
root, stub = fixture(matcher_json=json.dumps(exact_only))
(stub / "open").write_text("#!/usr/bin/env python3\nimport subprocess,sys\n"
    "subprocess.Popen([sys.executable,'-c',\"import pathlib,time,sys; time.sleep(1.5); p=pathlib.Path(sys.argv[1]); (p/'calendar-attendees.json').write_text('{}'); (p/'calendar-access.log').write_text('exit=0\\\\n')\",sys.argv[-1]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n")
p = run(root, stub, extra_env={"CARR_CALENDAR_CAPTURE_WAIT_SECONDS":"1"})
(stub / "open").write_text("#!/bin/sh\nexit 0\n")
next_run = run(root, stub)
check("late completion after timeout cannot authorize the next read", p.returncode == 1 and next_run.returncode == 1 and "logged exact touch" not in next_run.stdout)

# Regression 1/8: canonical legacy replay and title edits never create a second activity.
root, stub = fixture(appends="exit=0", dump_json="{}", matcher_json=json.dumps(exact_only))
shutil.copy2(REPO / "tools/calendar-intake-gate.py", root / "tools/calendar-intake-gate.py")
(root / "run.sh").write_text("#!/usr/bin/env python3\n" +
    "import json,sys,pathlib\np=pathlib.Path('out/store.json')\ns=json.loads(p.read_text()) if p.exists() else {}\na=json.loads(sys.argv[3])\n" +
    "if sys.argv[2]=='catch-me-up':\n print(json.dumps({'calendar_history':[{'key':k,'activity_id':v['id'],'summary':v['summary']} for k,v in s.items()]}))\n" +
    "else:\n k=a['idempotency_key']\n s.setdefault(k,dict(a,id=str(len(s)+1)))\n p.write_text(json.dumps(s))\n print('{\"ok\":true}')\n")
(root / "run.sh").chmod(0o755)
# Execute the prior writer's attendee/day algorithm against the same persistent
# fake store, then upgrade. This is the base loop (first event per exact email),
# rather than two invocations of the new occurrence writer or a seeded store.
for e in json.loads(json.dumps(exact_only))["exact"]:
    ev = e["events"][0]
    payload = {"idempotency_key":f"calcap-{e['email']}-{e['last_seen']}",
               "ref":e["ref"], "kind":"meeting", "occurred_at":ev["day"],
               "summary":f"Meeting: {ev['title']}"[:180]}
    old_run = subprocess.run(["./run.sh","call","log-activity",json.dumps(payload)],
                             cwd=root, capture_output=True, text=True)
    check("prior attendee/day writer captures the baseline activity", old_run.returncode == 0)
p = run(root, stub)
check("upgrade preserves a legacy activity without duplication", p.returncode == 0 and len(json.loads((root / "out/store.json").read_text())) == 1)
rescheduled = json.loads(json.dumps(exact_only))
rescheduled["exact"][0]["last_seen"] = "2026-09-26"
rescheduled["exact"][0]["events"][0].update(day="2026-09-26", start_at="2026-09-26T12:00:00+00:00", title="Edited legacy title")
(root / "tools/calendar-touch-matcher.py").write_text(f"print({json.dumps(rescheduled)!r})\n")
p = run(root, stub)
check("date and title edits preserve a reconciled legacy occurrence", p.returncode == 0 and len(json.loads((root / "out/store.json").read_text())) == 1)
(root / "tools/calendar-touch-matcher.py").write_text(f"print({json.dumps(exact_only)!r})\n")
(root / "out/store.json").write_text("{}")
p = run(root, stub)
edited = json.loads(json.dumps(exact_only))
edited["exact"][0]["events"][0]["title"] = "Edited title"
(root / "tools/calendar-touch-matcher.py").write_text(f"print({json.dumps(edited)!r})\n")
p = run(root, stub)
check("title edits preserve the same captured occurrence", p.returncode == 0 and len(json.loads((root / "out/store.json").read_text())) == 1)

# Regression 3: malformed evidence fails the actual nightly step, not EX_CONFIG skip.
nightly_step = nightly[nightly.index('step() {'):nightly.index('\ntombstone() {')]
(root / "out/calendar-intake-evidence.json").write_text("not-json")
shutil.copy2(SCRIPT, root / "bin/calendar-eventkit-capture.sh")
(root / "bin/calendar-eventkit-capture.sh").chmod(0o755)
harness = """rc_total=0; seam_blocked=0; LOG=out/nightly-test.log
say() { print -r -- "$@"; }
record_run() { print -r -- "state=$2"; }
carr_step_timeout_prefix() { CARR_STEP_TIMEOUT_ARGV=(); }
carr_step_timeout_for() { print 10; }
carr_routine_exec() { "$@"; }
""" + nightly_step + "\nstep calendar ./bin/calendar-eventkit-capture.sh\nprint -r -- \"rc_total=$rc_total\"\n"
p = subprocess.run(["/bin/zsh", "-c", harness], cwd=root,
    env=dict(os.environ, CARR_REPO=str(root), PATH=f"{stub}:{os.environ['PATH']}"),
    capture_output=True, text=True, timeout=15)
check("damaged evidence is a nightly failure", "rc_total=1" in p.stdout and "state=failed" in p.stdout)

# R1: private reader output must also reach the existing triage consumer through
# a completed snapshot, without letting a failed reader replace the last one.
triage_dump = {"schema": "calendar-events/v2", "events": [{
    "event_id": "synthetic-triage-occurrence", "title": "Synthetic triage meeting",
    "start_at": "2026-10-01T17:00:00+00:00", "emails": ["triage@example.test"]}]}
root, stub = fixture(appends="exit=0", dump_json=json.dumps(triage_dump),
                     matcher_json=json.dumps(exact_only))
published = root / "out/calendar-attendees.json"
published.write_text('{"legacy|2026-09-01": ["old@example.test"]}')
p = run(root, stub, "--dry-run")
triage_spec = importlib.util.spec_from_file_location("triage_capture_reader", REPO / "tools/calendar-triage-plan.py")
assert triage_spec is not None and triage_spec.loader is not None
triage_reader = importlib.util.module_from_spec(triage_spec)
triage_spec.loader.exec_module(triage_reader)
setattr(triage_reader, "ATTENDEES", str(published))
addresses = triage_reader.emails_in({"summary": "Synthetic triage meeting",
    "starts_at": "2026-10-01T12:00:00-05:00"}, triage_reader.load_local_attendees())
check("successful capture publishes a usable private triage snapshot",
      p.returncode == 0 and addresses == ["triage@example.test"]
      and published.stat().st_mode & 0o777 == 0o600)
before_failure = published.read_bytes()
(stub / "open").write_text('#!/bin/sh\nfor target do :; done\necho exit=1 > "$target/calendar-access.log"\n')
p = run(root, stub, "--dry-run")
check("failed reader preserves the completed triage snapshot",
      p.returncode != 0 and published.read_bytes() == before_failure)

print(f"\n{'OK all checks passed' if not failures else f'FAIL {len(failures)}: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
