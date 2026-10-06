#!/usr/bin/env python3
"""Dedicated, sponsor-bound Joe calendar-prebrief scheduler/runtime.

This is intentionally separate from the generic control-plane runner.  It
knows one owner, one workflow version, one mode, one installed EventKit app,
and one jobs credential.  It is disabled unless an explicit local profile says
so *and* the database activation gate accepts the current allowlist.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, NoReturn
from urllib.parse import unquote, urlsplit

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = "calendar-prebrief-projection-joe-daily"
APP_NAME = "CARR Calendar Access.app"
EX_CONFIG = 78
LEASE_SECONDS = 300
CHILD_REFUSAL_CLASS = "calendar_prebrief_child_refusal"
PRE_CHILD_LEASE_CLASS = "calendar_prebrief_pre_child_lease_refusal"
POST_CHILD_FAILURE_CLASS = "calendar_prebrief_post_child_refusal"


class Refusal(RuntimeError):
    pass


class RecordedJobFailure(RuntimeError):
    """A lease-owning runtime recorded an exact phase-specific failure."""

    def __init__(self, failure_class: str, state: str):
        super().__init__(state)
        self.failure_class = failure_class
        self.state = state


class FailureReceiptRejected(RuntimeError):
    """The original phase failure survived, but its fail_job write did not."""

    def __init__(self, failure_class: str, recording_error: Exception):
        super().__init__("failure receipt rejected")
        self.failure_class = failure_class
        self.recording_error = recording_error


def _dsn_login(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not parsed.password or not parsed.path.strip("/") or parsed.fragment:
            return ""
        return unquote(parsed.username or "")
    except ValueError:
        return ""


def _secure_file(path: Path, label: str, mode: int = 0o600) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise Refusal(f"{label} is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or stat.S_IMODE(info.st_mode) != mode:
        raise Refusal(f"{label} must be a {mode:04o} regular non-symlink")


def _absolute(value: str, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise Refusal(f"{label} must be absolute")
    return path


def load_profile(path: Path, *, home: Path | None = None) -> dict[str, str]:
    """Parse literal KEY=VALUE, never execute a local credential file."""
    expected = {
        "CARR_CALENDAR_PREBRIEF_ENABLED", "CARR_DB_JOBS_URL",
        "CARR_CALENDAR_PREBRIEF_CHILD_PROFILE", "CARR_CALENDAR_PREBRIEF_COLLECTOR_PUBLIC_KEY",
        "CARR_CALENDAR_PREBRIEF_ALLOWLIST", "CARR_CALENDAR_PREBRIEF_COLLECTOR_PRIVATE_KEY",
        "CARR_CALENDAR_PREBRIEF_COLLECTOR_VERSION", "CARR_CALENDAR_PREBRIEF_EVENTKIT_APP",
    }
    _secure_file(path, "runtime profile")
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or key not in expected or key in values or "$(" in value or "`" in value:
            raise Refusal("runtime profile has unknown, duplicate, or executable content")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    if set(values) != expected or values["CARR_CALENDAR_PREBRIEF_ENABLED"] not in {"false", "true"}:
        raise Refusal("runtime profile is incomplete or has invalid enablement")
    if _dsn_login(values["CARR_DB_JOBS_URL"]) != "carr_jobs":
        raise Refusal("runtime profile lacks exact carr_jobs identity")
    if not values["CARR_CALENDAR_PREBRIEF_COLLECTOR_VERSION"]:
        raise Refusal("runtime profile collector version is empty")
    root = home or Path.home()
    app = _absolute(values["CARR_CALENDAR_PREBRIEF_EVENTKIT_APP"], "EventKit application")
    if app != root / "Applications" / APP_NAME:
        raise Refusal("EventKit application must be the fixed per-user installed bundle")
    for key in ("CARR_CALENDAR_PREBRIEF_CHILD_PROFILE", "CARR_CALENDAR_PREBRIEF_COLLECTOR_PUBLIC_KEY", "CARR_CALENDAR_PREBRIEF_ALLOWLIST", "CARR_CALENDAR_PREBRIEF_COLLECTOR_PRIVATE_KEY"):
        _absolute(values[key], key)
    return values


def verify_app(path: Path) -> None:
    launcher = path / "Contents/MacOS/carr-calendar-access"
    info = path / "Contents/Info.plist"
    try:
        launcher_info = launcher.lstat()
        plist = info.read_text(encoding="utf-8")
    except OSError as exc:
        raise Refusal("installed EventKit application is incomplete") from exc
    if not stat.S_ISREG(launcher_info.st_mode) or stat.S_ISLNK(launcher_info.st_mode) or not os.access(launcher, os.X_OK):
        raise Refusal("installed EventKit application executable is unsafe")
    if "us.carr.calendar-access" not in plist or "NSCalendarsFullAccessUsageDescription" not in plist:
        raise Refusal("installed EventKit application identity is invalid")


def _connector():
    try:
        import psycopg
    except ImportError as exc:
        raise Refusal("psycopg is required for runtime execution") from exc
    return psycopg.connect


def _jobs_call(dsn: str, query: str, args: tuple[Any, ...] = ()) -> Any:
    connect = _connector()
    with connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select session_user,current_user")
        if tuple(cur.fetchone() or ()) != ("carr_jobs", "carr_jobs"):
            raise Refusal("jobs database session identity mismatch")
        cur.execute(query, args)
        row = cur.fetchone()
        conn.commit()
    return row[0] if row else None


def schedule(dsn: str) -> dict[str, Any] | None:
    value = _jobs_call(dsn, "select row_to_json(job) from ops.schedule_calendar_prebrief_joe_live_job() job")
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"job_id", "scheduled_for"}:
        raise Refusal("Joe scheduler returned malformed job")
    return value


def claim(dsn: str) -> dict[str, str] | None:
    value = _jobs_call(dsn, "select row_to_json(job) from ops.claim_calendar_prebrief_joe_live_job(%s,%s) job", ("calendar-prebrief-joe-runtime", LEASE_SECONDS))
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"job_id", "lease", "scheduled_for"} or any(not isinstance(value[key], str) or not value[key] for key in value):
        raise Refusal("Joe claim returned malformed lease")
    return value


def heartbeat(dsn: str, claim_value: Mapping[str, str]) -> None:
    value = _jobs_call(dsn, "select ops.heartbeat_job(%s::uuid,%s::uuid,%s)", (claim_value["job_id"], claim_value["lease"], LEASE_SECONDS))
    if value is not True:
        raise Refusal("Joe calendar prebrief lease is no longer live")


def fail_job(dsn: str, claim_value: Mapping[str, str], failure_class: str, detail: str) -> str:
    value = _jobs_call(
        dsn,
        "select ops.fail_job(%s::uuid,%s::uuid,%s,%s)",
        (claim_value["job_id"], claim_value["lease"], failure_class, detail),
    )
    if value not in {"retry_wait", "dead_lettered"}:
        raise Refusal("Joe calendar prebrief failure receipt did not read back exactly")
    return value


def record_failure(dsn: str, claim_value: Mapping[str, str], failure_class: str, detail: str, original: Exception) -> NoReturn:
    try:
        state = fail_job(dsn, claim_value, failure_class, detail)
    except Exception as recording_error:
        # Preserve the phase failure as __cause__.  The fail_job rejection is
        # attached separately so neither event overwrites the other.
        raise FailureReceiptRejected(failure_class, recording_error) from original
    raise RecordedJobFailure(failure_class, state) from original


def complete(dsn: str, claim_value: Mapping[str, str], result: Mapping[str, Any]) -> dict[str, Any]:
    if set(result) != {"sponsor", "mode", "attestation_id", "receipt_id", "unknown_attendees"} or result.get("sponsor") != "joe" or result.get("mode") != "live":
        raise Refusal("Joe child returned malformed completion")
    value = _jobs_call(dsn, "select row_to_json(receipt) from ops.complete_calendar_prebrief_joe_live_job(%s,%s,%s::uuid,%s::uuid) receipt", (claim_value["job_id"], claim_value["lease"], result["attestation_id"], result["receipt_id"]))
    required = {"job_id", "attempt", "state", "attestation_id", "receipt_id", "allowlist_revision_id", "allowlist_digest", "scheduled_for"}
    if not isinstance(value, dict) or set(value) != required or value.get("job_id") != claim_value["job_id"] or value.get("state") != "succeeded" or value.get("attestation_id") != result["attestation_id"] or value.get("receipt_id") != result["receipt_id"]:
        raise Refusal("Joe completion receipt did not read back exactly")
    return value


def _coordinator():
    spec = importlib.util.spec_from_file_location("calendar_prebrief_coordinator", REPO / "tools/calendar-prebrief-coordinator.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_tick(profile: Mapping[str, str], profile_path: Path) -> dict[str, Any]:
    if profile["CARR_CALENDAR_PREBRIEF_ENABLED"] != "true":
        raise Refusal("Joe calendar prebrief remains explicitly disabled")
    # This must happen before even attempting to enqueue.  A LaunchAgent with
    # an accidentally replaced profile is not a valid scheduler just because a
    # previously parsed mapping happened to be in memory.
    _secure_file(profile_path, "runtime profile")
    verify_app(Path(profile["CARR_CALENDAR_PREBRIEF_EVENTKIT_APP"]))
    scheduled = schedule(profile["CARR_DB_JOBS_URL"])
    coordinator = _coordinator()
    env = {"PATH": os.environ.get("PATH", ""), "CARR_DB_JOBS_URL": profile["CARR_DB_JOBS_URL"],
           "CARR_CALENDAR_PREBRIEF_CLAIM_COMMAND": f"{sys.executable} {Path(__file__).resolve()} --profile {profile_path} claim",
           **{key: profile[key] for key in ("CARR_CALENDAR_PREBRIEF_CHILD_PROFILE", "CARR_CALENDAR_PREBRIEF_COLLECTOR_PUBLIC_KEY", "CARR_CALENDAR_PREBRIEF_EVENTKIT_APP", "CARR_CALENDAR_PREBRIEF_ALLOWLIST", "CARR_CALENDAR_PREBRIEF_COLLECTOR_PRIVATE_KEY", "CARR_CALENDAR_PREBRIEF_COLLECTOR_VERSION")}}
    claimed: dict[str, str] | None = None

    def protect_lease(value: dict[str, str]) -> None:
        nonlocal claimed
        claimed = dict(value)
        try:
            heartbeat(profile["CARR_DB_JOBS_URL"], claimed)
        except Exception as exc:
            record_failure(
                profile["CARR_DB_JOBS_URL"], claimed, PRE_CHILD_LEASE_CLASS,
                "lease protection failed before sponsor child execution", exc,
            )

    try:
        got = coordinator.parent_execute(sponsor="joe", mode="live", claim_command=env["CARR_CALENDAR_PREBRIEF_CLAIM_COMMAND"], child_profile=Path(env["CARR_CALENDAR_PREBRIEF_CHILD_PROFILE"]), public_key=Path(env["CARR_CALENDAR_PREBRIEF_COLLECTOR_PUBLIC_KEY"]), environ=env, include_claim=True, after_claim=protect_lease)
    except (RecordedJobFailure, FailureReceiptRejected):
        raise
    except Exception as exc:
        if claimed is None:
            raise
        record_failure(
            profile["CARR_DB_JOBS_URL"], claimed, CHILD_REFUSAL_CLASS,
            "sponsor child refused its DB-issued calendar capture contract", exc,
        )
    if got == {"status": "empty"}:
        return {"scheduled": int(scheduled is not None), "claimed": 0}
    try:
        heartbeat(profile["CARR_DB_JOBS_URL"], got["claim"])
        receipt = complete(profile["CARR_DB_JOBS_URL"], got["claim"], got["result"])
    except Exception as exc:
        record_failure(
            profile["CARR_DB_JOBS_URL"], got["claim"], POST_CHILD_FAILURE_CLASS,
            "lease protection or completion failed after sponsor child execution", exc,
        )
    unknown = got["result"]["unknown_attendees"]
    write_last_run(receipt, unknown)
    return {"scheduled": int(scheduled is not None), "claimed": 1, "completion": receipt,
            "unknown_attendees": unknown}


LAST_RUN = REPO / "out" / "calendar-prebrief-joe-last-run.json"


def write_last_run(receipt: Mapping[str, Any], unknown: Mapping[str, Any], path: Path | None = None) -> None:
    """Addressless local summary of the latest completed run, read by health.

    The skipped-attendee count and opaque keys (migration 0735) are not in the
    database receipt; this is where the standing check finds them. Failure to
    write is reported, never allowed to undo a completed, receipted run.
    """
    body = {"scheduled_for": receipt.get("scheduled_for"), "job_id": receipt.get("job_id"),
            "receipt_id": receipt.get("receipt_id"), "unknown_attendees": dict(unknown)}
    path = path or LAST_RUN
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name("." + path.name + ".tmp")
        temporary.write_text(json.dumps(body, sort_keys=True, default=str) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        print("calendar prebrief Joe runtime: WARN last-run summary not written", file=sys.stderr)
        # Never leave an older run's summary standing in for this one.
        try:
            path.unlink()
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight")
    sub.add_parser("claim")
    sub.add_parser("tick")
    args = parser.parse_args()
    try:
        profile = load_profile(args.profile)
        if args.command == "preflight":
            verify_app(Path(profile["CARR_CALENDAR_PREBRIEF_EVENTKIT_APP"]))
            output: dict[str, Any] = {"sponsor": "joe", "workflow": WORKFLOW, "enabled": profile["CARR_CALENDAR_PREBRIEF_ENABLED"] == "true", "ready": False}
        elif args.command == "claim":
            got = claim(profile["CARR_DB_JOBS_URL"])
            if got is None:
                print(json.dumps({"status": "empty"}, sort_keys=True))
                return 0
            print(json.dumps(got, sort_keys=True))
            return 0
        else:
            output = run_tick(profile, args.profile)
        print(json.dumps(output, sort_keys=True))
        return 0
    except RecordedJobFailure as exc:
        print(json.dumps({"status": "failed", "failure_class": exc.failure_class, "state": exc.state}, sort_keys=True))
        return 1
    except FailureReceiptRejected as exc:
        print(json.dumps({"status": "failed", "failure_class": exc.failure_class, "state": "failure_receipt_rejected"}, sort_keys=True))
        return 1
    except Refusal as exc:
        print(f"calendar prebrief Joe runtime: REFUSE {exc}", file=sys.stderr)
        return EX_CONFIG


if __name__ == "__main__":
    raise SystemExit(main())
