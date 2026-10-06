#!/usr/bin/env python3
"""Hermetic adversarial checks for DB-bound collector-envelope verification."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.machine_prerequisites import require_openssl_ed25519_or_exit

OPENSSL = require_openssl_ed25519_or_exit()
SCRIPT = ROOT / "tools/calendar-prebrief-coordinator.py"
spec = importlib.util.spec_from_file_location("coordinator", SCRIPT)
assert spec and spec.loader
coordinator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coordinator)
bad: list[str] = []


def check(name: str, value: bool) -> None:
    print(("  ok " if value else "  FAIL ") + name)
    if not value:
        bad.append(name)


def refuses(fn) -> bool:
    try:
        fn()
    except coordinator.Refusal:
        return True
    return False


def sign(private: Path, payload: bytes) -> bytes:
    # Linux OpenSSL requires seekable one-shot Ed25519 input. TemporaryFile is
    # unnamed/unlinked, so fixture payload bytes never gain a durable path.
    with tempfile.TemporaryFile() as raw_input:
        raw_input.write(payload)
        raw_input.flush()
        raw_input.seek(0)
        return subprocess.run(
            [OPENSSL, "pkeyutl", "-sign", "-inkey", str(private), "-rawin",
             "-in", f"/dev/fd/{raw_input.fileno()}"],
            capture_output=True, check=True, pass_fds=(raw_input.fileno(),),
        ).stdout


def contract() -> dict[str, object]:
    return {"challenge_id": "00000000-0000-4000-8000-000000000003", "sponsor": "joe",
            "job_id": "00000000-0000-4000-8000-000000000001", "attempt": 2,
            "lease_token": "00000000-0000-4000-8000-000000000002", "scheduled_for": "2026-08-20T06:30:00Z",
            "window_starts_at": "2026-08-13T06:30:00Z", "window_ends_at": "2026-10-04T06:30:00Z",
            "mode": "live", "destination": "live", "allowlist_revision_id": "00000000-0000-4000-8000-000000000004",
            "allowlist_digest": "d" * 64, "calendar_keys": ["a" * 64]}


with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    private, public = root / "private.pem", root / "public.pem"
    subprocess.run([OPENSSL, "genpkey", "-algorithm", "ED25519", "-out", str(private)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    private.chmod(0o600)
    subprocess.run([OPENSSL, "pkey", "-in", str(private), "-pubout", "-out", str(public)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    raw_payload = {"version": 1, "window": {"starts_at": "2026-08-13T06:30:00Z", "ends_at": "2026-10-04T06:30:00Z"}, "observed_calendars": [{"sponsor": "joe", "calendar_key": "a" * 64}], "events": [{"sponsor": "joe", "calendar_key": "a" * 64, "event_key": "b" * 64, "occurrence_key": "c" * 64, "starts_at": "2026-08-20T08:00:00Z", "ends_at": "2026-08-20T09:00:00Z", "title": "Meeting", "location": None, "attendee_emails": ["raw.attendee@example.test"]}]}
    envelope = contract() | {"raw_payload": raw_payload, "raw_payload_digest": hashlib.sha256(coordinator._canonical(raw_payload)).hexdigest(), "raw_payload_count": 1, "collector_version": "fixture-1", "key_fingerprint": hashlib.sha256(public.read_bytes()).hexdigest()}
    signature = sign(private, coordinator._canonical(envelope))
    envelope["signature"] = base64.b64encode(signature).decode("ascii")
    got_raw, evidence = coordinator.verify_envelope(envelope, public, contract())
    check("exact DB contract signed envelope verifies", got_raw == raw_payload and evidence["signature_sha256"] == hashlib.sha256(signature).hexdigest())
    if not hasattr(coordinator.os, "memfd_create"):
        def fixture_memfd(_name: str) -> int:
            with tempfile.TemporaryFile() as anonymous:
                return os.dup(anonymous.fileno())
        coordinator.os.memfd_create = fixture_memfd
        try:
            portable_raw, _ = coordinator.verify_envelope(envelope, public, contract())
        finally:
            delattr(coordinator.os, "memfd_create")
    else:
        portable_raw, _ = coordinator.verify_envelope(envelope, public, contract())
    check("anonymous seekable verification input is portable", portable_raw == raw_payload)
    for name, key, value in (("cross-job replay", "job_id", "00000000-0000-4000-8000-000000000010"), ("altered scheduled window", "window_starts_at", "2026-08-12T06:30:00Z"), ("altered destination", "destination", "calendar-prebrief-canary-joe"), ("altered allowlist revision", "allowlist_revision_id", "00000000-0000-4000-8000-000000000010"), ("altered challenge", "challenge_id", "00000000-0000-4000-8000-000000000010")):
        changed = dict(envelope)
        changed[key] = value
        check(name + " refuses before resolver", refuses(lambda changed=changed: coordinator.verify_envelope(changed, public, contract())))
    # The DB API consumes challenge and unique signature evidence atomically;
    # ensure the coordinator cannot omit either while calling that boundary.
    source = SCRIPT.read_text(encoding="utf-8")
    check("verified-envelope API carries DB challenge and signature digest", "contract[\"challenge_id\"]" in source and "evidence[\"signature_sha256\"]" in source)
    live = root / "live.env"
    live.write_text("\n".join(("CARR_DB_CALENDAR_PREBRIEF_ATTESTOR_JOE_URL=postgresql://carr_calendar_prebrief_attestor_joe:x@db/carr", "CARR_DB_CALENDAR_PREBRIEF_RESOLVER_JOE_URL=postgresql://carr_calendar_prebrief_resolver_joe:x@db/carr", "CARR_DB_CALENDAR_PREBRIEF_JOE_URL=postgresql://carr_calendar_prebrief_joe:x@db/carr")) + "\n")
    live.chmod(0o600)
    canary = root / "canary.env"
    canary.write_text("\n".join(("CARR_DB_CALENDAR_PREBRIEF_ATTESTOR_JOE_URL=postgresql://carr_calendar_prebrief_attestor_joe:x@db/carr", "CARR_DB_CALENDAR_PREBRIEF_RESOLVER_JOE_URL=postgresql://carr_calendar_prebrief_resolver_joe:x@db/carr", "CARR_DB_CALENDAR_PREBRIEF_CANARY_JOE_URL=postgresql://carr_calendar_prebrief_canary_joe:x@db/carr")) + "\n")
    canary.chmod(0o600)
    check("live and canary profiles accept only their own execution identity", coordinator._profile_file(live, "joe", "live")["CARR_DB_CALENDAR_PREBRIEF_JOE_URL"].startswith("postgresql://carr_calendar_prebrief_joe:") and coordinator._profile_file(canary, "joe", "canary")["CARR_DB_CALENDAR_PREBRIEF_CANARY_JOE_URL"].startswith("postgresql://carr_calendar_prebrief_canary_joe:"))
    check("a live profile cannot run canary and a canary profile cannot run live", refuses(lambda: coordinator._profile_file(live, "joe", "canary")) and refuses(lambda: coordinator._profile_file(canary, "joe", "live")))
    check("raw attendee material has no temporary-file or argv sink", all(token not in source for token in ("NamedTemporaryFile", "mkstemp", "--snapshot", "--email")))

    # Exercise the executable parent -> child -> collector path, not just its
    # individual helpers.  The fixtures record identity/environment *names*
    # only; no DSN values or raw attendee material leave this temp directory.
    e2e_log = root / "e2e.jsonl"
    fake = root / "fake"
    (fake / "psycopg" / "types").mkdir(parents=True)
    calendar_key = hashlib.sha256(b"calendar\0calendar-joe").hexdigest()
    live_job, canary_job = "00000000-0000-4000-8000-000000000011", "00000000-0000-4000-8000-000000000021"
    e2e_contracts = {
        live_job: {**contract(), "job_id": live_job, "lease_token": "00000000-0000-4000-8000-000000000012", "calendar_keys": [calendar_key]},
        canary_job: {**contract(), "job_id": canary_job, "lease_token": "00000000-0000-4000-8000-000000000022", "mode": "canary", "destination": "calendar-prebrief-canary-joe", "calendar_keys": [calendar_key]},
    }
    (fake / "psycopg" / "types" / "json.py").write_text("class Jsonb:\n def __init__(self,value): self.value=value\n", encoding="utf-8")
    (fake / "psycopg" / "__init__.py").write_text(
        "\n".join((
            "import json, os", "from urllib.parse import unquote, urlsplit",
            f"LOG={str(e2e_log)!r}", f"CONTRACTS={e2e_contracts!r}",
            "def note(kind,user=''):",
            " with open(LOG,'a',encoding='utf-8') as out: out.write(json.dumps({'kind':kind,'user':user,'db_env':sorted(k for k in os.environ if k.startswith('CARR_DB_') or k.startswith('PG'))})+'\\n')",
            "class Cursor:",
            " def __init__(self,user): self.user=user; self.query=''; self.args=()",
            " def execute(self,query,args=None): self.query=query; self.args=args or ()",
            " def fetchone(self):",
            "  if 'session_user,current_user' in self.query: return (self.user,self.user)",
            "  if 'issue_calendar_prebrief_capture_contract' in self.query:",
            "   note('contract',self.user); return (CONTRACTS[self.args[0]],)",
            "  if 'resolve_calendar_prebrief_email_ref' in self.query: note('resolver',self.user); return ('C-E2E-1',)",
            "  if 'record_calendar_prebrief_verified_envelope' in self.query: note('attestor',self.user); return ('00000000-0000-4000-8000-000000000031',)",
            "  if 'ingest_calendar_prebrief_projection' in self.query: note('live_ingest',self.user); return ('00000000-0000-4000-8000-000000000041',)",
            "  if 'ingest_calendar_prebrief_canary_projection' in self.query: note('canary_ingest',self.user); return ('00000000-0000-4000-8000-000000000051',)",
            "  raise RuntimeError('unexpected fixture query')",
            " def __enter__(self): return self", " def __exit__(self,*_): return False",
            "class Conn:",
            " def __init__(self,dsn): self.user=unquote(urlsplit(dsn).username or ''); note('connect',self.user)",
            " def cursor(self): return Cursor(self.user)", " def commit(self): pass",
            " def __enter__(self): return self", " def __exit__(self,*_): return False",
            "def connect(dsn): return Conn(dsn)", "",
        )), encoding="utf-8")
    (fake / "EventKit.py").write_text(
        "\n".join((
            "import json, os", f"LOG={str(e2e_log)!r}",
            "with open(LOG,'a',encoding='utf-8') as out: out.write(json.dumps({'kind':'collector_env','user':'','db_env':sorted(k for k in os.environ if k.startswith('CARR_DB_') or k.startswith('PG'))})+'\\n')",
            "class URL:\n def resourceSpecifier(self): return 'mailto:raw.e2e@example.test'",
            "class Attendee:\n def URL(self): return URL()",
            "class Calendar:\n def calendarIdentifier(self): return 'calendar-joe'",
            "class Event:\n def calendar(self): return Calendar()\n def eventIdentifier(self): return 'event-e2e'\n def startDate(self): from datetime import datetime,timezone; return datetime(2026,8,20,8,tzinfo=timezone.utc)\n def endDate(self): from datetime import datetime,timezone; return datetime(2026,8,20,9,tzinfo=timezone.utc)\n def title(self): return 'Meeting'\n def location(self): return None\n def attendees(self): return [Attendee()]\n def organizer(self): return None",
            "class Store:\n def requestFullAccessToEventsWithCompletion_(self,done): done(True,None)\n def calendarsForEntityType_(self,_): return [Calendar()]\n def predicateForEventsWithStartDate_endDate_calendars_(self,*args): return args\n def eventsMatchingPredicate_(self,_): return [Event()]",
            "class EKEventStore:\n @classmethod\n def alloc(cls): return cls()\n def init(self): return Store()", "",
        )), encoding="utf-8")
    (fake / "Foundation.py").write_text("class NSDate:\n @staticmethod\n def dateWithTimeIntervalSince1970_(value): return value\n", encoding="utf-8")
    allowlist = root / "e2e-allowlist.json"
    allowlist.write_text('{"version":1,"calendars":[{"identifier":"calendar-joe","sponsor":"joe"}]}', encoding="utf-8")
    allowlist.chmod(0o600)
    # The coordinator must reach EventKit through the responsible application
    # executable. This hermetic bundle-shaped shim preserves the app-routing
    # contract while the fake EventKit module keeps the test UI-free.
    app_launcher = root / "CARR Calendar Access.app" / "Contents" / "MacOS" / "carr-calendar-access"
    app_launcher.parent.mkdir(parents=True)
    app_launcher.write_text("#!/bin/sh\nexport CARR_CALENDAR_PREBRIEF_ALLOWLIST=\"$3\" CARR_CALENDAR_PREBRIEF_COLLECTOR_PRIVATE_KEY=\"$4\" CARR_CALENDAR_PREBRIEF_COLLECTOR_VERSION=\"$5\"\nexec " + repr(sys.executable) + " " + repr(str(ROOT / 'tools/calendar-prebrief-collector.py')) + " < \"$1\" > \"$2\"\n", encoding="utf-8")
    app_launcher.chmod(0o700)
    fake_open = root / "fake-open.py"
    fake_open.write_text("#!/usr/bin/env python3\nimport os,subprocess,sys\na=sys.argv[1:]\nassert a[0]=='-n' and a[2]=='--args' and a[3]=='collector' and len(a)==9\nraise SystemExit(subprocess.run([a[1]+'/Contents/MacOS/carr-calendar-access',*a[4:]],env=os.environ.copy()).returncode)\n", encoding="utf-8")
    fake_open.chmod(0o700)
    profile = root / "e2e-profile.env"
    claim = root / "claim.py"

    def dsn(user: str) -> str:
        return f"postgresql://{user}:fixture@db.example/carr"  # ci-secret-scan: allow — fixture

    def run_e2e(mode: str) -> subprocess.CompletedProcess[str]:
        job_id = live_job if mode == "live" else canary_job
        execution_key = "CARR_DB_CALENDAR_PREBRIEF_JOE_URL" if mode == "live" else "CARR_DB_CALENDAR_PREBRIEF_CANARY_JOE_URL"
        execution_user = "carr_calendar_prebrief_joe" if mode == "live" else "carr_calendar_prebrief_canary_joe"
        profile.write_text("\n".join((
            f"CARR_DB_CALENDAR_PREBRIEF_ATTESTOR_JOE_URL={dsn('carr_calendar_prebrief_attestor_joe')}",
            f"CARR_DB_CALENDAR_PREBRIEF_RESOLVER_JOE_URL={dsn('carr_calendar_prebrief_resolver_joe')}",
            f"{execution_key}={dsn(execution_user)}",
        )) + "\n", encoding="utf-8")
        profile.chmod(0o600)
        claim.write_text("import json,os\n" + f"open({str(e2e_log)!r},'a',encoding='utf-8').write(json.dumps({{'kind':'parent_claim_env','user':'','db_env':sorted(k for k in os.environ if k.startswith('CARR_DB_') or k.startswith('PG'))}})+'\\n')\n" + f"print(json.dumps({{'job_id':{job_id!r},'lease':{e2e_contracts[job_id]['lease_token']!r},'scheduled_for':'2026-08-20T06:30:00Z'}}))\n", encoding="utf-8")
        claim.chmod(0o700)
        environment = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(fake), "CARR_REPO": str(ROOT), "CARR_DB_JOBS_URL": dsn("carr_jobs"), "CARR_CALENDAR_PREBRIEF_CLAIM_COMMAND": f"{sys.executable} {claim}", "CARR_CALENDAR_PREBRIEF_CHILD_PROFILE": str(profile), "CARR_CALENDAR_PREBRIEF_COLLECTOR_PUBLIC_KEY": str(public), "CARR_CALENDAR_PREBRIEF_EVENTKIT_APP": str(app_launcher.parents[2]), "CARR_CALENDAR_PREBRIEF_ALLOWLIST": str(allowlist), "CARR_CALENDAR_PREBRIEF_COLLECTOR_PRIVATE_KEY": str(private), "CARR_CALENDAR_PREBRIEF_COLLECTOR_VERSION": "fixture-1", "CARR_CALENDAR_PREBRIEF_TEST_MODE":"1", "CARR_CALENDAR_PREBRIEF_TEST_OPEN_BIN":str(fake_open)}
        return subprocess.run([sys.executable, str(SCRIPT), "--sponsor", "joe", "--mode", mode], text=True, capture_output=True, env=environment, timeout=15, check=False)

    e2e_live, e2e_canary = run_e2e("live"), run_e2e("canary")
    if e2e_live.returncode or e2e_canary.returncode:
        raise RuntimeError(f"coordinator E2E fixture failed: live={e2e_live.stderr!r} canary={e2e_canary.stderr!r}")
    e2e_rows = [json.loads(line) for line in e2e_log.read_text(encoding="utf-8").splitlines()]
    check("executable parent-to-child live and canary paths return isolated receipts", e2e_live.returncode == 0 and e2e_canary.returncode == 0 and json.loads(e2e_live.stdout).get("mode") == "live" and json.loads(e2e_canary.stdout).get("mode") == "canary")
    check("parent-to-child result carries a valid skipped-attendee report (0735)", all(coordinator._valid_unknown_report(json.loads(run.stdout).get("unknown_attendees")) for run in (e2e_live, e2e_canary)))
    check("parent claim sees only the jobs credential", [row["db_env"] for row in e2e_rows if row["kind"] == "parent_claim_env"] == [["CARR_DB_JOBS_URL"], ["CARR_DB_JOBS_URL"]])
    check("child database calls receive no ambient DB credential", all(row["db_env"] == [] for row in e2e_rows if row["kind"] in {"connect", "contract", "resolver", "attestor", "live_ingest", "canary_ingest"}))
    check("collector receives no database credential", all(row["db_env"] == [] for row in e2e_rows if row["kind"] == "collector_env"))
    seen = [(row["kind"], row["user"]) for row in e2e_rows if row["kind"] in {"contract", "attestor", "live_ingest", "canary_ingest"}]
    check("E2E uses resolver, attestor, and distinct live/canary ingest identities", seen == [("contract", "carr_calendar_prebrief_resolver_joe"), ("attestor", "carr_calendar_prebrief_attestor_joe"), ("live_ingest", "carr_calendar_prebrief_joe"), ("contract", "carr_calendar_prebrief_resolver_joe"), ("attestor", "carr_calendar_prebrief_attestor_joe"), ("canary_ingest", "carr_calendar_prebrief_canary_joe")])
    # Empty is a deliberate protocol value, not missing connector output. It
    # must short-circuit before any EventKit or child credential process.
    claim.write_text("import json\nprint(json.dumps({'status':'empty'}))\n", encoding="utf-8")
    idle_env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(fake), "CARR_DB_JOBS_URL": dsn("carr_jobs"), "CARR_CALENDAR_PREBRIEF_CLAIM_COMMAND": f"{sys.executable} {claim}", "CARR_CALENDAR_PREBRIEF_CHILD_PROFILE": str(profile), "CARR_CALENDAR_PREBRIEF_COLLECTOR_PUBLIC_KEY": str(public)}
    idle = subprocess.run([sys.executable, str(SCRIPT), "--sponsor", "joe", "--mode", "live"], text=True, capture_output=True, env=idle_env, timeout=15, check=False)
    check("typed empty claim is an idle success while malformed silence is not", idle.returncode == 0 and json.loads(idle.stdout) == {"status": "empty"} and refuses(lambda: coordinator.parent_execute(sponsor="joe", mode="live", claim_command="/usr/bin/true", child_profile=profile, public_key=public, environ={"PATH": os.environ.get("PATH", ""), "CARR_DB_JOBS_URL": dsn("carr_jobs")})))

print("OK" if not bad else "FAIL " + ", ".join(bad))
raise SystemExit(bool(bad))
