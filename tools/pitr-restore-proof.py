#!/usr/bin/env python3
"""pitr-restore-proof.py — PROVE how recent a point production can be restored
to (V5-F08, the record-layer RPO cell). Run through bin/pitr-restore-proof.sh.

WHY A PROOF AND NOT A READ. The database provider's API reports the project's
history retention, but no field anywhere names a "latest restorable point", and
"point-in-time restore is enabled" is a setting, not a measurement. So this
measures it, with a positive probe AND a negative control:

  1. write a POSITIVE probe row (ops.pitr_probe via ops.write_pitr_probe: the
     nonce and the write instant are made by the server, migration 0597);
  2. wait until the DATABASE clock is >= its write instant + 61 s, and take
     T = that clock floored to whole seconds (the provider's point and the
     probe's write instant share one clock, so the machine's clock is never
     consulted);
  3. wait until the database clock is >= T + 6 s and write a NEGATIVE probe;
  4. pass the neon-disposable-branch metering admission, record the branch name
     to a local state file, then ask the provider API for a branch whose
     parent_id is the production branch and parent_timestamp is T, expiring in
     one hour;
  5. read the branch back: parent_id, parent_timestamp == T exactly, and a
     resolved parent_lsn;
  6. through a READ-ONLY session on the branch: the positive probe must be
     present with the same id, nonce and write instant, the negative probe must
     be ABSENT, and the core tables must hold rows;
  7. re-read both probe rows from production (read-only) and the history
     retention from the provider;
  8. delete the branch by id, and confirm its life from the provider's
     OPERATIONS LOG — a finished create_branch and a finished delete_timeline
     on that branch id, no older than the proof's start — rather than from a
     404, which an invented branch id also returns.

ONE PROCESS, NO FILE IN BETWEEN (review H1). Every fact the evaluator decides
on is read by THIS process in this run, the branch-side ones while the branch
still exists: the parent point from the provider, the probes and core rows on
the branch itself. There is no `verify` mode that reads an earlier run's file:
nothing that decides the verdict comes from a caller. The evidence block is
printed on stdout, stamped with the binding (lib/recovery_evidence.py) that
mcp-server/bin/recovery-matrix-evaluate.mjs `rpo -` requires; progress lines go
to stderr. out/pitr-restore-proof.json is a copy for the operator's eyes only.

WHAT IS AUTHORITY (review K3): only this stdout PIPED into the evaluator, as
bin/pitr-restore-proof.sh does. An evaluator verdict on the saved file is not
an RPO result: nothing proves the file is what this process printed.

CLOCK (review K4): T and the probe instants are the DATABASE's clock; the
binding stamp is this machine's. Before binding, the proof compares the two
and refuses a skew over lib/recovery_evidence.MAX_CLOCK_SKEW_SECONDS.

WHAT IS WRITTEN. Production business data is never written. The only writes
are the two probe rows, through the one function migration 0597 made for them,
on the owner connection; ops.pitr_probe is append-only. Every other production
session is read-only (default_transaction_read_only=on).

NO SECRET LEAVES THIS PROCESS. The provider key is read from the environment
(NEON_API_KEY, loaded by the wrapper) and sent only as an Authorization header.
Connection strings come from the provider API into memory and go straight to
the driver: never printed, never on an argument list, never in a file.

NOTHING OUTLIVES THE PROVIDER'S OWN CLEANUP. Every branch this creates carries
a provider-side expiry one hour out, so a branch leaks for at most an hour
whatever happens to this process. On top of that: the branch name is recorded
before the create call, an interrupted create (any BaseException, Ctrl-C
included) is found by that name and deleted, SIGINT/SIGTERM/SIGHUP are BLOCKED
from the create call until the new id is in the teardown list (review H4), a
delete the provider answers 423 (an operation still running) is retried with
backoff, teardown runs with SIGINT, SIGTERM and SIGHUP ignored, and the next
run sweeps whatever the state file
still names, by id or by name. (Review G3 removed the old stand-in-parent
rehearsal mode: its parent could not carry an expiry, because the provider
refuses children of an expiring branch.)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from lib.recovery_evidence import CORE_TABLES, bind, check_clock_skew  # noqa: E402
API = "https://console.neon.tech/api/v2"
PROJECT_ID = "steep-field-48688294"
DATABASE = "neondb"
OWNER_ROLE = "neondb_owner"
BRANCH_PREFIX = "pitr-proof-"
STALE_AFTER = timedelta(hours=1)
LIFETIME_MINUTES = 60
PROBE_MARGIN_SECONDS = 60          # V5_PITR_PROBE_MARGIN_SECONDS, plus one second of slack below
NEGATIVE_MARGIN_SECONDS = 5        # V5_PITR_NEGATIVE_MARGIN_SECONDS, plus one second of slack below
OUT = REPO / "out" / "pitr-restore-proof.json"
STATE = REPO / "out" / "pitr-proof-branches.json"
DELETE_RETRIES = 6                 # a 423 (the branch has an operation running) is retried with backoff
OPERATIONS_PAGE = 100
OPERATIONS_MAX_PAGES = 20
READ_ONLY = "-c default_transaction_read_only=on"
US_FORMAT = """to_char(written_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')"""


class ProofError(RuntimeError):
    pass


def say(message: str) -> None:
    """Progress for the operator. stdout carries only the evidence block."""
    print(message, file=sys.stderr, flush=True)


# ── the provider API (the key is a header, never an argument) ────────────────

def api(method: str, path: str, body: dict | None = None, query: dict | None = None) -> tuple[int, Any]:
    key = os.environ.get("NEON_API_KEY", "")
    if not key:
        raise ProofError("NEON_API_KEY is not loaded; run through bin/pitr-restore-proof.sh")
    url = API + path + ("?" + urllib.parse.urlencode(query) if query else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {key}", "Accept": "application/json", "Content-Type": "application/json",
        "User-Agent": "carr-pitr-restore-proof"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        return exc.code, payload


def api_ok(method: str, path: str, body: dict | None = None, query: dict | None = None) -> Any:
    status, payload = api(method, path, body, query)
    if status >= 300:
        raise ProofError(f"provider {method} {path} answered {status}: {str(payload.get('message', ''))[:200]}")
    return payload


def branches() -> list[dict]:
    return list(api_ok("GET", f"/projects/{PROJECT_ID}/branches").get("branches", []))


def default_branch_id() -> str:
    found = [b["id"] for b in branches() if b.get("default")]
    if len(found) != 1:
        raise ProofError(f"expected one default branch, found {len(found)}")
    return str(found[0])


def retention() -> tuple[int, str]:
    project = api_ok("GET", f"/projects/{PROJECT_ID}").get("project", {})
    value = project.get("history_retention_seconds")
    if not isinstance(value, int):
        raise ProofError("the provider returned no history_retention_seconds")
    return value, iso_now()


def connection_uri(branch_id: str) -> str:
    payload = api_ok("GET", f"/projects/{PROJECT_ID}/connection_uri",
                     query={"branch_id": branch_id, "database_name": DATABASE, "role_name": OWNER_ROLE})
    uri = payload.get("uri")
    if not isinstance(uri, str) or not uri:
        raise ProofError(f"no connection URI for branch {branch_id}")
    return uri


def host_of(uri: str) -> str:
    return urllib.parse.urlsplit(uri).hostname or ""


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── branch lifecycle: state file, sweep, delete-and-confirm ──────────────────

def load_state() -> list[dict]:
    try:
        return list(json.loads(STATE.read_text()))
    except (OSError, json.JSONDecodeError):
        return []


def save_state(rows: list[dict]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(rows, indent=2, sort_keys=True))


def delete_and_confirm(branch_id: str, never: set[str]) -> bool:
    """Delete one branch by id and require the provider to answer 404 for it afterwards.

    A 423 means the branch still has an operation running (a create that has
    not finished, typically); it is retried with backoff rather than given up.
    """
    if branch_id in never:
        raise ProofError(f"refusing to delete protected branch {branch_id}")
    for attempt in range(DELETE_RETRIES):
        status, _ = api("DELETE", f"/projects/{PROJECT_ID}/branches/{branch_id}")
        if status != 423:
            break
        time.sleep(min(30, 2 ** attempt))
    if status >= 300 and status != 404:
        return False
    for _ in range(30):
        status, _ = api("GET", f"/projects/{PROJECT_ID}/branches/{branch_id}")
        if status == 404:
            save_state([r for r in load_state() if r.get("id") != branch_id])
            return True
        time.sleep(2)
    return False


def sweep(never: set[str]) -> None:
    """Delete proof branches a crashed earlier run left: named in the state file, or prefixed and over an hour old."""
    now = datetime.now(timezone.utc)
    state = load_state()
    recorded = {r.get("id") for r in state if r.get("id")}
    # A row with a name but no id is a create that was interrupted before its
    # answer arrived; the branch may exist all the same, so it is found by name.
    recorded_names = {r.get("name") for r in state if r.get("name") and not r.get("id")}
    for b in branches():
        if b.get("default") or b.get("protected") or b["id"] in never:
            continue
        created = datetime.fromisoformat(str(b.get("created_at", "")).replace("Z", "+00:00"))
        stale = str(b.get("name", "")).startswith(BRANCH_PREFIX) and now - created > STALE_AFTER
        if b["id"] in recorded or b.get("name") in recorded_names or stale:
            ok = delete_and_confirm(b["id"], never)
            say(f"  sweep: {'deleted' if ok else 'COULD NOT DELETE'} leftover proof branch {b['id']} ({b.get('name')})")
            if not ok:
                raise ProofError(f"leftover proof branch {b['id']} could not be deleted; delete it by hand")
    live = branches()
    live_ids, live_names = {b["id"] for b in live}, {b.get("name") for b in live}
    save_state([r for r in load_state()
                if r.get("id") in live_ids or (not r.get("id") and r.get("name") in live_names)])


def admit_metered_branch() -> None:
    """The neon-disposable-branch metering admission, BEFORE any branch create below."""
    active = sum(1 for b in branches() if not b.get("default"))
    got = subprocess.run([sys.executable, str(REPO / "ops" / "platform-metering-gate.py"),
                          "--gate", "neon-disposable-branch", "--requested-lifetime-minutes", str(LIFETIME_MINUTES),
                          "--active-nondefault-branches", str(active), "--cleanup-registered"],
                         capture_output=True, text=True, check=False)
    if got.returncode:
        raise ProofError(f"neon-disposable-branch metering admission refused: {got.stderr.strip()[-200:]}")


INTERRUPTS = tuple(s for s in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None),
                                getattr(signal, "SIGHUP", None)) if s is not None)


class interrupts_blocked:
    """Hold SIGINT/SIGTERM/SIGHUP pending (not lost) for the duration; they are
    delivered, and their handlers run, the moment the block ends."""

    def __enter__(self):
        self.previous = signal.pthread_sigmask(signal.SIG_BLOCK, set(INTERRUPTS))
        return self

    def __exit__(self, *exc):
        signal.pthread_sigmask(signal.SIG_SETMASK, self.previous)
        return False


def create_branch(name: str, parent_id: str, parent_timestamp: str, never: set[str], created: list[str]) -> dict:
    """Create one disposable, EXPIRING branch through the API and put its id in `created`.

    Admission first; the name is recorded before the call. Review H4: from the
    POST until the new id is in `created` (the list the caller's teardown
    walks), interrupts are BLOCKED, so no Ctrl-C, SIGTERM or hang-up can land
    in the window where the branch exists but nothing would delete it.
    """
    admit_metered_branch()
    save_state([*load_state(), {"name": name, "id": None, "requested_at": iso_now()}])
    spec: dict[str, Any] = {
        "parent_id": parent_id, "name": name, "parent_timestamp": parent_timestamp,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=LIFETIME_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with interrupts_blocked():
        try:
            status, payload = api("POST", f"/projects/{PROJECT_ID}/branches",
                                  {"branch": spec, "endpoints": [{"type": "read_write"}]})
        except BaseException as exc:  # any failure: the call may have landed, so find it by name
            try:
                found = [b for b in branches() if b.get("name") == name]
            except Exception:  # noqa: BLE001 — provider unreachable; the state row keeps the name for the sweep
                found = []
            for b in found:
                ok = delete_and_confirm(b["id"], never)
                say(f"  create interrupted; branch {b['id']} named {name}: "
                    f"{'deleted' if ok else 'COULD NOT DELETE (it expires within the hour)'}")
            if isinstance(exc, Exception):
                raise ProofError(f"branch create failed: {type(exc).__name__}") from exc
            raise
        if status >= 300:
            raise ProofError(f"branch create answered {status}: {str(payload.get('message', ''))[:200]}")
        branch = payload.get("branch", {})
        branch_id = str(branch.get("id", ""))
        if not branch_id or branch_id in never:
            raise ProofError("branch create returned no id, or a protected branch's id")
        created.append(branch_id)
        save_state([*[r for r in load_state() if r.get("name") != name],
                    {"name": name, "id": branch_id, "requested_at": iso_now()}])
    return branch


def read_back_ready(branch_id: str, attempts: int = 60) -> dict:
    """The branch as the provider reports it once created: it fills in the
    resolved parent point (parent_timestamp beside parent_lsn) only after the
    create operation finishes, so an immediate read shows neither."""
    back: dict = {}
    for _ in range(attempts):
        back = api_ok("GET", f"/projects/{PROJECT_ID}/branches/{branch_id}").get("branch", {})
        if back.get("current_state") == "ready" and back.get("parent_timestamp") and back.get("parent_lsn"):
            return back
        time.sleep(2)
    return back


class teardown_signals_deferred:
    """Ignore SIGINT, SIGTERM and SIGHUP while cleanup runs, so a second Ctrl-C,
    a kill or a closed terminal cannot cut a delete short; restore the handlers after."""

    SIGNALS = INTERRUPTS

    def __enter__(self):
        self.saved = {s: signal.getsignal(s) for s in self.SIGNALS}
        for s in self.SIGNALS:
            signal.signal(s, signal.SIG_IGN)
        return self

    def __exit__(self, *exc):
        for s, handler in self.saved.items():
            signal.signal(s, handler)
        return False


def operations_since(started_at: str, branch_id: str) -> list[dict]:
    """The provider's operations log for one branch, newest first, back to the proof's start."""
    since = epoch_of(started_at)
    found: list[dict] = []
    cursor: str | None = None
    for _ in range(OPERATIONS_MAX_PAGES):
        query: dict[str, Any] = {"limit": OPERATIONS_PAGE}
        if cursor:
            query["cursor"] = cursor
        page = api_ok("GET", f"/projects/{PROJECT_ID}/operations", query=query)
        ops = page.get("operations") or []
        found += [op for op in ops if op.get("branch_id") == branch_id]
        cursor = (page.get("pagination") or {}).get("cursor")
        oldest = min((epoch_of(str(op["created_at"])) for op in ops if op.get("created_at")), default=None)
        if not ops or not cursor or (oldest is not None and oldest < since):
            return found
    raise ProofError("the provider operations log did not reach back to the proof's start")


def confirmed_operation(ops: list[dict], action: str, started_at: str) -> str | None:
    """The id of the ONE finished `action` operation at or after the proof's start, else None."""
    since = epoch_of(started_at)
    hits = [op for op in ops if op.get("action") == action and op.get("status") == "finished"
            and op.get("created_at") and epoch_of(str(op["created_at"])) >= since]
    return str(hits[0]["id"]) if len(hits) == 1 and hits[0].get("id") else None


# ── the database side (the driver takes the DSN; nothing prints it) ──────────

def connect(uri: str, *, read_only: bool, attempts: int = 40):
    import psycopg  # the repo venv's driver

    last: Exception | None = None
    for _ in range(attempts):
        try:
            return psycopg.connect(uri, autocommit=True, options=READ_ONLY if read_only else None, connect_timeout=15)
        except psycopg.OperationalError as exc:  # a fresh branch's compute takes a moment to start
            last = exc
            time.sleep(3)
    raise ProofError(f"could not connect: {type(last).__name__}")


def db_now(conn) -> float:
    return float(conn.execute("select extract(epoch from clock_timestamp())").fetchone()[0])


def wait_db_clock(conn, target_epoch: float) -> float:
    while True:
        now = db_now(conn)
        if now >= target_epoch:
            return now
        time.sleep(min(5.0, max(0.2, target_epoch - now)))


def write_probe(conn, role: str) -> dict:
    row = conn.execute(f"select id::text, nonce, {US_FORMAT} from ops.write_pitr_probe(%s)", (role,)).fetchone()
    return {"id": row[0], "nonce": row[1], "written_at": row[2]}


def read_probes(conn, ids: list[str]) -> dict[str, dict]:
    rows = conn.execute(f"select id::text, nonce, {US_FORMAT} from ops.pitr_probe where id = any(%s::uuid[])", (ids,)).fetchall()
    return {r[0]: {"id": r[0], "nonce": r[1], "written_at": r[2]} for r in rows}


def epoch_of(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def iso_seconds(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_exact(raw: str) -> str:
    """The provider's timestamp in UTC, losing nothing: a fractional second is kept, never floored away."""
    if not raw:
        raise ProofError("the provider returned no parent_timestamp for the branch")
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ" if dt.microsecond else "%Y-%m-%dT%H:%M:%SZ")


# ── prove ─────────────────────────────────────────────────────────────────────

def prove(now: datetime | None = None) -> dict:
    """The whole proof in one process; returns the bound evidence block (review H1)."""
    proof_started_at = iso_now()
    production_id = default_branch_id()
    never = {production_id}
    say(f"  ok    production branch {production_id}")
    sweep(never)
    created: list[str] = []
    parent_id = production_id
    try:
        parent_uri = connection_uri(parent_id)
        with connect(parent_uri, read_only=False) as writer:
            positive = write_probe(writer, "positive")
            say(f"  ok    positive probe {positive['id']} written {positive['written_at']}")
            reached = wait_db_clock(writer, epoch_of(positive["written_at"]) + PROBE_MARGIN_SECONDS + 1)
            t_epoch = math.floor(reached)
            t_iso = iso_seconds(t_epoch)
            wait_db_clock(writer, t_epoch + NEGATIVE_MARGIN_SECONDS + 1)
            negative = write_probe(writer, "negative")
            say(f"  ok    T = {t_iso}; negative probe {negative['id']} written {negative['written_at']}")
        name = f"{BRANCH_PREFIX}{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
        branch_id = create_branch(name, parent_id, t_iso, never, created)["id"]
        # The parent point as the PROVIDER reports it for this branch, read now, while it exists.
        back = read_back_ready(branch_id)
        say(f"  ok    branch {branch_id} read back: parent {back.get('parent_id')} at {back.get('parent_timestamp')}, lsn {back.get('parent_lsn')}")
        branch_uri = connection_uri(branch_id)
        if host_of(branch_uri) == host_of(parent_uri):
            raise ProofError("the proof branch resolves to its parent's host; refusing")
        # The probes and core rows ON THE BRANCH ITSELF, read now, while it exists.
        with connect(branch_uri, read_only=True) as reader:
            seen = read_probes(reader, [positive["id"], negative["id"]])
            core = {t: int(reader.execute(f"select count(*) from {t}").fetchone()[0]) for t in CORE_TABLES}
        say(f"  probe: positive on branch {seen.get(positive['id']) == positive}; negative absent {negative['id'] not in seen}")
        # Production re-read (read-only, a fresh session): the rows the probes claim to be.
        with connect(connection_uri(production_id), read_only=True) as conn:
            rows = read_probes(conn, [positive["id"], negative["id"]])
            server_now = datetime.fromtimestamp(db_now(conn), timezone.utc)
        try:
            check_clock_skew(datetime.now(timezone.utc), server_now, "the production server")
        except ValueError as exc:
            raise ProofError(str(exc)) from None
        readback = {"positive": rows.get(positive["id"]), "negative": rows.get(negative["id"]), "read_at": iso_now()}
        retention_seconds, retention_read_at = retention()
    finally:
        with teardown_signals_deferred():
            for created_id in reversed(created):
                ok = delete_and_confirm(created_id, never)
                say(f"  teardown: branch {created_id} {'deleted (provider answers 404)' if ok else 'COULD NOT BE DELETED — it expires within the hour; delete it by hand'}")
    operations = confirm_lifecycle(branch_id, proof_started_at)
    facts = {
        "source": "pitr_branch_proof",
        "proof_target_kind": "disposable_branch",
        "project_id": PROJECT_ID,
        "production_branch_id": production_id,
        "branch_id": branch_id,
        "requested_parent_timestamp": t_iso,
        "branch_parent_id": str(back.get("parent_id", "")),
        "branch_parent_timestamp": utc_exact(str(back.get("parent_timestamp", ""))),
        "branch_parent_lsn": str(back.get("parent_lsn") or ""),
        "positive_probe": positive,
        "negative_probe": negative,
        "positive_on_branch": seen.get(positive["id"]),
        "negative_present_on_branch": negative["id"] in seen,
        "branch_core_table_rows": core,
        "branch_operations": operations,
        "history_retention_seconds": retention_seconds,
        "retention_read_at": retention_read_at,
        "production_readback": readback,
    }
    return bind(facts, "record_layer_rpo", now)


def confirm_lifecycle(branch_id: str, started_at: str, attempts: int = 15) -> dict[str, str | None]:
    """Create and delete as the provider LOGGED them; a delete also needs the branch gone now.

    The delete operation can take a moment to finish, so the log is polled.
    """
    result: dict[str, str | None] = {"create": None, "delete": None}
    for _ in range(attempts):
        ops = operations_since(started_at, branch_id)
        status, _ = api("GET", f"/projects/{PROJECT_ID}/branches/{branch_id}")
        result = {
            "create": confirmed_operation(ops, "create_branch", started_at),
            "delete": confirmed_operation(ops, "delete_timeline", started_at) if status == 404 else None,
        }
        if result["create"] and result["delete"]:
            return result
        time.sleep(2)
    return result


def _interrupt(signum, _frame) -> None:
    raise SystemExit(129 if signum == getattr(signal, "SIGHUP", None) else 130)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # One mode, no file input: there is nothing a caller can hand this (review H1).
    p.add_argument("mode", choices=("prove",))
    p.parse_args(argv)
    for sig in INTERRUPTS:
        signal.signal(sig, _interrupt)
    try:
        evidence = prove()
        text = json.dumps(evidence, sort_keys=True)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text)  # the operator's copy; nothing reads it back
        print(text, flush=True)
        return 0
    except (ProofError, OSError, KeyError) as exc:
        print(f"PITR PROOF FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
