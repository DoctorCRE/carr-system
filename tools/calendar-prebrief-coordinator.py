#!/usr/bin/env python3
"""Run one DB-leased calendar prebrief through separate scoped processes.

The jobs parent sees only a lease.  The sponsor child gets a DB-issued capture
challenge using its resolver identity, pipes it to the fixed EventKit collector,
then verifies every signed field before resolving raw attendee addresses in RAM.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import unquote, urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.machine_prerequisites import openssl_executable  # noqa: E402

HEX64 = re.compile(r"^[0-9a-f]{64}$")
REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SPONSORS = {"joe", "dell"}
MAX_PIPE = 1_048_576


class Refusal(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _read_pipe(limit: int = MAX_PIPE) -> dict[str, Any]:
    mode = os.fstat(sys.stdin.fileno()).st_mode
    if sys.stdin.isatty() or stat.S_ISREG(mode):
        raise Refusal("coordinator input must be a process pipe")
    text = sys.stdin.read(limit + 1)
    if len(text) > limit:
        raise Refusal("coordinator input exceeds its bound")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Refusal("coordinator input is malformed") from exc
    if not isinstance(value, dict):
        raise Refusal("coordinator input must be an object")
    return value


def _claim(value: Mapping[str, Any]) -> dict[str, str]:
    if set(value) != {"job_id", "lease", "scheduled_for"} or any(not isinstance(value.get(key), str) or not value[key] for key in value):
        raise Refusal("jobs parent requires one exact leased claim")
    try:
        parsed = datetime.fromisoformat(value["scheduled_for"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise Refusal("leased claim scheduled_for must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise Refusal("leased claim scheduled_for must include timezone")
    return dict(value)


def _dsn_login(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not parsed.path.strip("/") or not parsed.password or parsed.fragment:
            return ""
        return unquote(parsed.username or "")
    except ValueError:
        return ""


def _profile_file(path: Path, sponsor: str, mode: str) -> dict[str, str]:
    suffix = sponsor.upper()
    ingest_key = f"CARR_DB_CALENDAR_PREBRIEF_{suffix}_URL" if mode == "live" else f"CARR_DB_CALENDAR_PREBRIEF_CANARY_{suffix}_URL"
    ingest_identity = f"carr_calendar_prebrief_{sponsor}" if mode == "live" else f"carr_calendar_prebrief_canary_{sponsor}"
    expected = {
        f"CARR_DB_CALENDAR_PREBRIEF_ATTESTOR_{suffix}_URL": f"carr_calendar_prebrief_attestor_{sponsor}",
        f"CARR_DB_CALENDAR_PREBRIEF_RESOLVER_{suffix}_URL": f"carr_calendar_prebrief_resolver_{sponsor}",
        ingest_key: ingest_identity,
    }
    try:
        info, lines = path.lstat(), path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise Refusal("sponsor credential profile is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise Refusal("sponsor credential profile must be a 0600 regular non-symlink")
    values: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in expected or key in values:
            raise Refusal("sponsor credential profile has unknown or duplicate content")
        value = value.strip().strip("'\"")
        if _dsn_login(value) != expected[key]:
            raise Refusal("sponsor credential profile has an unsafe identity")
        values[key] = value
    if set(values) != set(expected):
        raise Refusal("sponsor credential profile is incomplete or mixes run modes")
    return values


def _collector_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    source = Path(__file__).with_name("calendar-prebrief-collector.py")
    spec = importlib.util.spec_from_file_location("calendar_prebrief_collector", source)
    if not spec or not spec.loader:
        raise Refusal("collector contract verifier is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        return module.capture_contract(value)
    except module.Refusal as exc:
        raise Refusal("DB-issued capture contract is malformed") from exc


def verify_envelope(value: Mapping[str, Any], public_key: Path, contract: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    contract = _collector_contract(contract)
    required = set(contract) | {"raw_payload", "raw_payload_digest", "raw_payload_count", "collector_version", "key_fingerprint", "signature"}
    if set(value) != required:
        raise Refusal("collector envelope has an unsupported shape")
    for key in contract:
        if value.get(key) != contract[key]:
            raise Refusal("collector envelope does not match its DB-issued capture contract")
    raw = value.get("raw_payload")
    if not isinstance(raw, dict) or not isinstance(value.get("raw_payload_digest"), str) or not HEX64.fullmatch(value["raw_payload_digest"]) or type(value.get("raw_payload_count")) is not int or value["raw_payload_count"] < 0:
        raise Refusal("collector envelope raw capture evidence is malformed")
    if hashlib.sha256(_canonical(raw)).hexdigest() != value["raw_payload_digest"] or len(raw.get("events", [])) != value["raw_payload_count"]:
        raise Refusal("collector envelope raw capture evidence does not match its payload")
    fingerprint, version, signature_text = value.get("key_fingerprint"), value.get("collector_version"), value.get("signature")
    if not isinstance(fingerprint, str) or not HEX64.fullmatch(fingerprint) or not isinstance(version, str) or not VERSION.fullmatch(version) or not isinstance(signature_text, str):
        raise Refusal("collector envelope identity is malformed")
    try:
        info, key_bytes = public_key.lstat(), public_key.read_bytes()
        signature = base64.b64decode(signature_text, validate=True)
    except (OSError, ValueError) as exc:
        raise Refusal("collector public key or signature is invalid") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or not signature or len(signature) > 4096 or hashlib.sha256(key_bytes).hexdigest() != fingerprint:
        raise Refusal("collector public key or signature is invalid")
    read_fd, write_fd = os.pipe()
    payload_fd = signature_fd = -1
    try:
        signature_path = f"/dev/fd/{read_fd}"
        signed = {key: value[key] for key in value if key != "signature"}
        payload = _canonical(signed)
        input_path = "/dev/stdin"
        input_data: bytes | None = payload
        if hasattr(os, "memfd_create"):
            payload_fd = os.memfd_create("carr-calendar-envelope")
            signature_fd = os.memfd_create("carr-calendar-signature")
            remaining = memoryview(payload)
            while remaining:
                written = os.write(payload_fd, remaining)
                if written < 1:
                    raise Refusal("collector envelope could not be buffered")
                remaining = remaining[written:]
            os.lseek(payload_fd, 0, os.SEEK_SET)
            os.write(signature_fd, signature)
            os.lseek(signature_fd, 0, os.SEEK_SET)
            input_path, input_data = f"/dev/fd/{payload_fd}", None
            signature_path = f"/dev/fd/{signature_fd}"
        else:
            os.write(write_fd, signature)
        os.close(write_fd)
        verified = subprocess.run([openssl_executable(), "pkeyutl", "-verify", "-pubin", "-inkey", str(public_key), "-rawin", "-in", input_path, "-sigfile", signature_path], input=input_data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, pass_fds=tuple(item for item in (read_fd, payload_fd, signature_fd) if item >= 0), check=False)
    finally:
        try: os.close(read_fd)
        except OSError: pass
        try: os.close(write_fd)
        except OSError: pass
        for item in (payload_fd, signature_fd):
            if item >= 0:
                try: os.close(item)
                except OSError: pass
    if verified.returncode != 0:
        raise Refusal("collector envelope signature verification failed")
    return raw, {"collector_key_fingerprint": fingerprint, "signature_sha256": hashlib.sha256(signature).hexdigest(), "collector_version": version}


def attendee_key(email: str) -> str:
    """Opaque, stable id for an attendee the record does not know (never the address)."""
    return hashlib.sha256(("attendee\0" + email.strip().lower()).encode("utf-8")).hexdigest()


def _snapshot_from_raw(payload: Mapping[str, Any], sponsor: str, resolve, unknown: set[str] | None = None) -> dict[str, Any]:
    """Build the DB-bound snapshot; unresolved outside attendees go to ``unknown``.

    ``resolve`` returns a ref, None for an address no party row carries
    (skipped and counted, migration 0735), or raises for any known identity
    that is not exactly one live party with exactly one live ref (ambiguous,
    tombstoned or ref-less), which still refuses the snapshot.
    """
    if set(payload) != {"version", "window", "observed_calendars", "events"} or payload.get("version") != 1 or not isinstance(payload.get("events"), list):
        raise Refusal("collector payload has an unsupported shape")
    if not isinstance(payload.get("window"), dict) or set(payload["window"]) != {"starts_at", "ends_at"} or not isinstance(payload.get("observed_calendars"), list):
        raise Refusal("collector payload coverage is malformed")
    for calendar in payload["observed_calendars"]:
        if not isinstance(calendar, dict) or set(calendar) != {"sponsor", "calendar_key"} or calendar.get("sponsor") != sponsor or not isinstance(calendar.get("calendar_key"), str) or not HEX64.fullmatch(calendar["calendar_key"]):
            raise Refusal("collector payload crosses the selected sponsor boundary")
    events: list[dict[str, Any]] = []
    allowed = {"sponsor", "calendar_key", "event_key", "occurrence_key", "starts_at", "ends_at", "title", "location", "attendee_emails"}
    for event in payload["events"]:
        if not isinstance(event, dict) or set(event) != allowed or event.get("sponsor") != sponsor or not isinstance(event.get("attendee_emails"), list) or len(event["attendee_emails"]) > 16:
            raise Refusal("collector event has an unsupported shape")
        refs: set[str] = set()
        for email in event["attendee_emails"]:
            if not isinstance(email, str) or len(email) > 320 or email.count("@") != 1 or any(char.isspace() for char in email):
                raise Refusal("collector attendee address is malformed")
            if not email.lower().endswith("@carr.us"):
                ref = resolve(email)
                if ref is None:
                    if unknown is None:
                        raise Refusal("resolver found no contact and no unknown-attendee sink was given")
                    unknown.add(attendee_key(email))
                    continue
                if not isinstance(ref, str) or not REF.fullmatch(ref):
                    raise Refusal("resolver returned an invalid canonical ref")
                refs.add(ref)
        events.append({key: event[key] for key in allowed - {"attendee_emails"}} | {"participant_refs": sorted(refs)})
    return {"version": 1, "window": payload["window"], "observed_calendars": payload["observed_calendars"], "events": events}


def _connector():
    try:
        import psycopg
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise Refusal("psycopg is required for coordinator execution") from exc
    return psycopg.connect, Jsonb


def _call(dsn: str, identity: str, query: str, args: tuple[Any, ...]) -> Any:
    connect, Jsonb = _connector()
    with connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select session_user,current_user")
        if tuple(cur.fetchone() or ()) != (identity, identity):
            raise Refusal("scoped database session identity mismatch")
        cur.execute(query, tuple(Jsonb(value) if isinstance(value, (list, dict)) else value for value in args))
        row = cur.fetchone()
        if row is None:
            raise Refusal("scoped database call returned no receipt")
        conn.commit()
    return row[0]


def _capture(contract: Mapping[str, Any]) -> dict[str, Any]:
    # EventKit permission is attached to the responsible signed application,
    # not to a bare Python interpreter.  The runtime installs and validates the
    # bundle before this child is reached; do not silently fall back to direct
    # PyObjC, which would make a permission denial look like a usable runner.
    app = Path(os.environ.get("CARR_CALENDAR_PREBRIEF_EVENTKIT_APP", ""))
    launcher = app / "Contents/MacOS/carr-calendar-access"
    try:
        info = launcher.lstat()
    except OSError as exc:
        raise Refusal("responsible EventKit application is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or not os.access(launcher, os.X_OK):
        raise Refusal("responsible EventKit application is unsafe")
    allowlist = Path(os.environ.get("CARR_CALENDAR_PREBRIEF_ALLOWLIST", ""))
    private_key = Path(os.environ.get("CARR_CALENDAR_PREBRIEF_COLLECTOR_PRIVATE_KEY", ""))
    version = os.environ.get("CARR_CALENDAR_PREBRIEF_COLLECTOR_VERSION", "")
    if not version:
        raise Refusal("collector version is unavailable")
    # LaunchServices, rather than direct exec, makes this signed bundle the
    # responsible EventKit process. The request is contract-only; the bounded
    # raw envelope stays in a 0600 FIFO, never a filesystem payload.
    with tempfile.TemporaryDirectory(prefix="carr-prebrief-") as raw:
        root = Path(raw); request, response = root / "contract.fifo", root / "envelope.fifo"
        os.mkfifo(request, 0o600); os.mkfifo(response, 0o600)
        payload_box: list[bytes] = []; errors: list[Exception] = []
        def write_contract() -> None:
            try:
                with request.open("wb", buffering=0) as out: out.write(_canonical(contract))
            except Exception as exc: errors.append(exc)
        def read_envelope() -> None:
            try:
                with response.open("rb", buffering=0) as incoming:
                    value = incoming.read(MAX_PIPE + 1)
                payload_box.append(value)
            except Exception as exc: errors.append(exc)
        writer, reader = threading.Thread(target=write_contract), threading.Thread(target=read_envelope)
        writer.start(); reader.start()
        test_open = os.environ.get("CARR_CALENDAR_PREBRIEF_TEST_OPEN_BIN", "")
        if test_open and os.environ.get("CARR_CALENDAR_PREBRIEF_TEST_MODE") != "1":
            raise Refusal("production EventKit launcher cannot be overridden")
        opener = test_open if test_open else "/usr/bin/open"
        open_env={"PATH": os.environ.get("PATH", "")}
        if test_open:
            open_env["PYTHONPATH"]=os.environ.get("PYTHONPATH", "")
        result = subprocess.run([opener, "-n", str(app), "--args", "collector", str(request), str(response), str(allowlist), str(private_key), version], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=open_env, timeout=75, check=False)
        writer.join(5); reader.join(5)
        if writer.is_alive() or reader.is_alive() or errors or len(payload_box) != 1:
            raise Refusal("responsible EventKit application IPC did not finish")
        payload = payload_box[0]
    result_stdout = payload
    if result.returncode or len(result_stdout) > MAX_PIPE:
        raise Refusal("bound EventKit collector failed")
    try:
        value = json.loads(result_stdout)
    except json.JSONDecodeError as exc:
        raise Refusal("bound EventKit collector returned malformed envelope") from exc
    if not isinstance(value, dict):
        raise Refusal("bound EventKit collector returned malformed envelope")
    return value


def _issue_contract(resolver_dsn: str, sponsor: str, claim: Mapping[str, str]) -> dict[str, Any]:
    value = _call(resolver_dsn, f"carr_calendar_prebrief_resolver_{sponsor}", "select row_to_json(contract) from ops.issue_calendar_prebrief_capture_contract(%s,%s) contract", (claim["job_id"], claim["lease"]))
    if not isinstance(value, dict):
        raise Refusal("capture-contract DB call returned malformed result")
    return _collector_contract(value)


def child_execute(*, sponsor: str, mode: str, claim: Mapping[str, Any], profile: Path, public_key: Path) -> dict[str, Any]:
    if sponsor not in SPONSORS or mode not in {"live", "canary"}:
        raise Refusal("invalid sponsor or mode")
    if any(os.environ.get(key) for key in os.environ if key.startswith("CARR_DB_") or key.startswith("PG")):
        raise Refusal("sponsor child must receive credentials only through its profile")
    claim = _claim(claim)
    scoped = _profile_file(profile, sponsor, mode)
    resolver_dsn = scoped[f"CARR_DB_CALENDAR_PREBRIEF_RESOLVER_{sponsor.upper()}_URL"]
    contract = _issue_contract(resolver_dsn, sponsor, claim)
    if contract["scheduled_for"] != claim["scheduled_for"] or contract["sponsor"] != sponsor or contract["mode"] != mode:
        raise Refusal("DB-issued capture contract does not match the leased job")
    raw, evidence = verify_envelope(_capture(contract), public_key, contract)
    def resolve(email: str) -> str:
        return _call(resolver_dsn, f"carr_calendar_prebrief_resolver_{sponsor}", "select ops.resolve_calendar_prebrief_email_ref(%s)", (email,))
    unknown: set[str] = set()
    snapshot = _snapshot_from_raw(raw, sponsor, resolve, unknown)
    bridge_spec = importlib.util.spec_from_file_location("calendar_prebrief_ingest", Path(__file__).with_name("calendar-prebrief-ingest.py"))
    assert bridge_spec and bridge_spec.loader
    bridge = importlib.util.module_from_spec(bridge_spec)
    bridge_spec.loader.exec_module(bridge)
    observed, events = bridge.normalize_snapshot(snapshot, sponsor)
    if observed != contract["calendar_keys"] or snapshot["window"] != {"starts_at": contract["window_starts_at"], "ends_at": contract["window_ends_at"]}:
        raise Refusal("signed EventKit capture changed its exact DB contract coverage")
    attestor_dsn = scoped[f"CARR_DB_CALENDAR_PREBRIEF_ATTESTOR_{sponsor.upper()}_URL"]
    attestation = _call(attestor_dsn, f"carr_calendar_prebrief_attestor_{sponsor}", "select (ops.record_calendar_prebrief_verified_envelope(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)).id", (contract["job_id"], contract["lease_token"], contract["challenge_id"], contract["scheduled_for"], contract["window_starts_at"], contract["window_ends_at"], contract["allowlist_revision_id"], contract["allowlist_digest"], contract["calendar_keys"], observed, events, contract["destination"], evidence["collector_key_fingerprint"], evidence["signature_sha256"], evidence["collector_version"]))
    ingest_key = f"CARR_DB_CALENDAR_PREBRIEF_{sponsor.upper()}_URL" if mode == "live" else f"CARR_DB_CALENDAR_PREBRIEF_CANARY_{sponsor.upper()}_URL"
    ingest_identity = f"carr_calendar_prebrief_{sponsor}" if mode == "live" else f"carr_calendar_prebrief_canary_{sponsor}"
    ingest_dsn = scoped[ingest_key]
    if mode == "live":
        receipt = _call(ingest_dsn, ingest_identity, "select (ops.ingest_calendar_prebrief_projection(%s,%s,%s,%s)).id", (claim["job_id"], claim["lease"], observed, events))
    else:
        receipt = _call(ingest_dsn, ingest_identity, "select (ops.ingest_calendar_prebrief_canary_projection(%s,%s,%s,%s,%s)).id", (claim["job_id"], claim["lease"], contract["destination"], observed, events))
    return {"sponsor": sponsor, "mode": mode, "attestation_id": str(attestation), "receipt_id": str(receipt),
            "unknown_attendees": unknown_report(unknown)}


UNKNOWN_KEYS_CAP = 64


def unknown_report(keys: set[str]) -> dict[str, Any]:
    """Count plus pseudonymous ids of skipped attendees (confirmable by anyone
    holding the address, never reversible without it); bounded so stdout stays small."""
    ordered = sorted(keys)
    return {"count": len(ordered), "attendee_keys": ordered[:UNKNOWN_KEYS_CAP]}


def _valid_unknown_report(value: Any) -> bool:
    return (isinstance(value, dict) and set(value) == {"count", "attendee_keys"}
            and type(value["count"]) is int and value["count"] >= 0
            and isinstance(value["attendee_keys"], list)
            and len(value["attendee_keys"]) == min(value["count"], UNKNOWN_KEYS_CAP)
            and all(isinstance(k, str) and HEX64.fullmatch(k) for k in value["attendee_keys"]))


def parent_execute(*, sponsor: str, mode: str, claim_command: str, child_profile: Path, public_key: Path, environ: Mapping[str, str], include_claim: bool = False, after_claim: Callable[[dict[str, str]], None] | None = None) -> dict[str, Any]:
    if sponsor not in SPONSORS or mode not in {"live", "canary"} or any(environ.get(key) for key in environ if (key.startswith("CARR_DB_") and key != "CARR_DB_JOBS_URL") or key in {"DATABASE_URL", "CARR_DB_WRITER_URL", "CARR_DB_OWNER_URL"}):
        raise Refusal("jobs parent requires only its scoped jobs credential")
    jobs_dsn = environ.get("CARR_DB_JOBS_URL", "")
    if _dsn_login(jobs_dsn) != "carr_jobs":
        raise Refusal("jobs parent lacks exact carr_jobs credential")
    try:
        argv = shlex.split(claim_command, posix=True)
    except ValueError as exc:
        raise Refusal("claim connector command is malformed") from exc
    if not argv:
        raise Refusal("claim connector command is malformed")
    claim_run = subprocess.run(argv, input=b"", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env={"CARR_DB_JOBS_URL": jobs_dsn, "PATH": environ.get("PATH", "")}, timeout=30, check=False)
    if claim_run.returncode or len(claim_run.stdout) > 8192:
        raise Refusal("jobs claim connector failed")
    try:
        claimed_value = json.loads(claim_run.stdout)
    except (json.JSONDecodeError, TypeError, Refusal) as exc:
        raise Refusal("jobs claim connector returned malformed claim") from exc
    # An idle queue is a successful, typed result.  In particular, do not
    # treat a silent/empty connector stdout as idle: that would conceal a
    # broken lease boundary as a harmless no-op.
    if claimed_value == {"status": "empty"}:
        return {"status": "empty"}
    try:
        claim = _claim(claimed_value)
    except Refusal as exc:
        raise Refusal("jobs claim connector returned malformed claim") from exc
    # The runtime may renew the lease before the bounded child process starts.
    # The callback receives the same validated, jobs-only claim the parent uses;
    # it never crosses into the child environment or credential profile.
    if after_claim is not None:
        after_claim(claim)
    child_env = {key: environ[key] for key in ("PATH", "PYTHONPATH", "CARR_CALENDAR_PREBRIEF_EVENTKIT_APP", "CARR_CALENDAR_PREBRIEF_ALLOWLIST", "CARR_CALENDAR_PREBRIEF_COLLECTOR_PRIVATE_KEY", "CARR_CALENDAR_PREBRIEF_COLLECTOR_VERSION", "CARR_CALENDAR_PREBRIEF_TEST_MODE", "CARR_CALENDAR_PREBRIEF_TEST_OPEN_BIN") if environ.get(key)}
    child_env.update({"CARR_CALENDAR_PREBRIEF_CHILD_PROFILE": str(child_profile), "CARR_CALENDAR_PREBRIEF_COLLECTOR_PUBLIC_KEY": str(public_key)})
    child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", "--sponsor", sponsor, "--mode", mode], input=_canonical(claim), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=child_env, timeout=90, check=False)
    if child.returncode or len(child.stdout) > 8192:
        raise Refusal("sponsor child failed")
    try:
        result = json.loads(child.stdout)
    except json.JSONDecodeError as exc:
        raise Refusal("sponsor child returned malformed result") from exc
    if (not isinstance(result, dict)
            or set(result) != {"sponsor", "mode", "attestation_id", "receipt_id", "unknown_attendees"}
            or not _valid_unknown_report(result["unknown_attendees"])):
        raise Refusal("sponsor child returned malformed result")
    return {"claim": claim, "result": result} if include_claim else result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--sponsor", required=True, choices=sorted(SPONSORS))
    parser.add_argument("--mode", required=True, choices=("live", "canary"))
    args = parser.parse_args()
    try:
        if args.child:
            result = child_execute(sponsor=args.sponsor, mode=args.mode, claim=_read_pipe(), profile=Path(os.environ.get("CARR_CALENDAR_PREBRIEF_CHILD_PROFILE", "")), public_key=Path(os.environ.get("CARR_CALENDAR_PREBRIEF_COLLECTOR_PUBLIC_KEY", "")))
        else:
            result = parent_execute(sponsor=args.sponsor, mode=args.mode, claim_command=os.environ.get("CARR_CALENDAR_PREBRIEF_CLAIM_COMMAND", ""), child_profile=Path(os.environ.get("CARR_CALENDAR_PREBRIEF_CHILD_PROFILE", "")), public_key=Path(os.environ.get("CARR_CALENDAR_PREBRIEF_COLLECTOR_PUBLIC_KEY", "")), environ=os.environ)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Refusal, OSError, subprocess.SubprocessError):
        print("calendar prebrief coordinator: REFUSE", file=sys.stderr)
        return 78
    except Exception:
        print("calendar prebrief coordinator: FAIL", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
