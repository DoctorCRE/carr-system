#!/usr/bin/env python3
"""
migrate-prod-support.py — the receipt writer and escalation orchestrator for
bin/migrate-prod.sh's prevention wiring (WR-000048 activation, regenerated
design at out/wr48-activation/design-rehearse/prevention-design.md).

migrate-prod.sh is zsh; the two pieces of this design that need real JSON
handling and a durable fsynced write are done here instead of inline shell,
per that design's own §2 note that a "small python helper is acceptable if
shelling to python3 with an argv payload" (no heredocs, no inline SQL/scripts
in the .sh file — scripts live in files, per repo convention). Everything
that actually reaches the record layer still goes through the ONE sanctioned
Bash door named in CLAUDE.md and the design's §3: `<run_door> call <verb>
'<json>'` — this file only decides WHAT to call and reads back WHAT landed;
it never opens a database connection itself.

Two subcommands, both invoked from migrate-prod.sh's trap functions:

  write-receipt --reason-class C --detail D [--migration M] [--host H] --out-dir DIR
      Writes out/migrate-prod-refusal-receipt.<ts>.json, fsynced, BEFORE
      either record-layer call is attempted (design §2). Prints the path.
      Never raises: a failure to write is printed loud to stderr and this
      exits 1, but no Python traceback ever reaches the operator.

  escalate --receipt-path P --reason-class C --detail D [--migration M] --run-door DOOR
      Design §3's retry-then-escalate route for BOTH record-layer calls
      (open-incident, add-room-turn), each followed by an INDEPENDENT READBACK
      through the corresponding read verb (get-incident, read-room) — never
      trusting a write call's own response, per review finding 9 (design
      §3, "the fix"). Updates the receipt in place with the re-read refs.
      Never raises, and its own exit code carries no meaning migrate-prod.sh
      acts on — the refusal's real exit code is never touched by whether
      escalation itself succeeded (design §6: this is pure notification,
      never a second production write path).
"""
import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from secret_redaction import redact_text, sensitive_env_values

SCHEMA = "migrate-prod-refusal-receipt.v1"
SERVICE = "migrate-prod"
ENVIRONMENT = "production"
ROOM = "partner-line"
DETAIL_MAX = 2000


# ── fsync discipline, matching docs/frontier-finding/breakglass_run.py's
#    _fsync_file_and_dir / write_receipt exactly (design §2 cites that file by
#    name) — tmp-file write, flush + fsync the file, os.replace into place,
#    then fsync the file again AND its parent directory, so a crash right
#    after this call still leaves either the old receipt or a complete new
#    one on disk, never a half-written one. ─────────────────────────────────
def _fsync_file_and_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _write_json_fsynced(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(document, fh, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_file_and_dir(path)


def _read_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ── write-receipt ────────────────────────────────────────────────────────
def cmd_write_receipt(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"migrate-prod-refusal-receipt.{ts}.json"
    # COLLISION GUARD, not in the design's literal sketch: the sketch's
    # filename has one-second resolution, and a signal handler firing twice
    # in the same wall-clock second (or this file's own scenario harness,
    # which fires several refusals back to back) would otherwise let the
    # second os.replace silently overwrite the first receipt. Only engages
    # when a same-second collision is actually observed, so the ordinary
    # one-refusal-at-a-time path keeps the exact documented filename shape.
    if path.exists():
        path = out_dir / f"migrate-prod-refusal-receipt.{ts}.{os.getpid()}.json"

    doc = {
        "schema": SCHEMA,
        "reason_class": args.reason_class,
        "detail": (args.detail or "")[:DETAIL_MAX],
        "migration_name": args.migration or "",
        "host": args.host or "",
        "occurred_at": ts,
        "escalation": {"incident_call": "pending", "model_room_call": "pending"},
    }
    try:
        _write_json_fsynced(path, doc)
    except OSError as exc:
        # NEVER RAISES past this point, and NEVER SWALLOWED: a receipt that
        # cannot be written is the floor named in design §5 ("the design's
        # own last line of defense is exhausted at that point") and must be
        # shouted, not silently absorbed by the caller's `|| true`.
        print(f"COULD NOT WRITE THE LOCAL REFUSAL RECEIPT to {path}: {exc}", file=sys.stderr)
        print("out/ may be full, unwritable, or missing — check by hand.", file=sys.stderr)
        return 1
    print(str(path))
    return 0


# ── escalate ──────────────────────────────────────────────────────────────
def _call_verb(run_door: str, verb: str, payload: dict) -> tuple[int, str, str]:
    """One invocation of the sanctioned Bash door: `<run_door> call <verb>
    '<json>'` — design §3.1/§3.2's exact shape. Returns (rc, stdout, stderr).
    A door that cannot even be spawned is reported as rc=1 with the
    exception text as stderr, never an unhandled traceback."""
    try:
        proc = subprocess.run(
            [run_door, "call", verb, json.dumps(payload)],
            capture_output=True, text=True, timeout=90,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def _open_incident_and_readback(run_door: str, operation: str, reason_class: str,
                                 detail: str, fingerprint: str) -> tuple[str, bool]:
    payload = {
        "idempotency_key": str(uuid.uuid4()),
        "service": SERVICE,
        "environment": ENVIRONMENT,
        "operation": operation,
        "failure_class": reason_class,
        "observed": detail[:DETAIL_MAX],
        "severity": "SEV-2",
        "owner": "joe",
        "next_action": "review the refusal, correct the frontier or the environment, "
                       "and re-run bin/migrate-prod.sh",
    }
    # ONE RETRY, SAME IDEMPOTENCY KEY (design §3, "safe by construction" —
    # open-incident is idempotency_key-gated, so a retry after a response was
    # merely LOST, rather than never sent, cannot mint a second incident).
    rc = out = err = None
    for _attempt in (1, 2):
        rc, out, err = _call_verb(run_door, "open-incident", payload)
        if rc == 0:
            break
    if rc != 0 or out is None:
        return "NONE", False

    try:
        ref = json.loads(out)["ref"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return "NONE", False

    # INDEPENDENT READBACK — a fresh get-incident call, not a parse of the
    # write's own response (design §3, review finding 9). Only a match on
    # the re-read record counts as landed.
    rc2, out2, _err2 = _call_verb(run_door, "get-incident", {"ref": ref})
    if rc2 != 0:
        return "NONE", False
    try:
        incident = json.loads(out2)["incident"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return "NONE", False

    # incident.fingerprint is i.signature, which incidentSignature()
    # (mcp-server/src/trace.js) builds as a plain
    # "service|environment|operation|failure_class" string — recomputing and
    # comparing it, plus environment directly, is a genuine re-read match
    # rather than trusting anything the write call itself said.
    if incident.get("fingerprint") == fingerprint and incident.get("environment") == ENVIRONMENT:
        return ref, True
    return "NONE", False


def _add_room_turn_and_readback(run_door: str, body: str) -> bool:
    payload = {
        "idempotency_key": str(uuid.uuid4()),
        "body": body,
        "seat": "claude",
        "room": ROOM,
        "kind": "system",
    }
    rc = out = err = None
    for _attempt in (1, 2):
        rc, out, err = _call_verb(run_door, "add-room-turn", payload)
        if rc == 0:
            break
    if rc != 0 or out is None:
        return False

    try:
        raw_seq = json.loads(out)["seq"]
        if isinstance(raw_seq, bool):
            return False
        seq = int(raw_seq)
        if seq < 1:
            return False
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, OverflowError):
        return False

    rc2, out2, _err2 = _call_verb(run_door, "read-room", {"room": ROOM, "after_seq": seq - 1})
    if rc2 != 0:
        return False
    try:
        turns = json.loads(out2)["turns"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return False

    def exact_turn(turn: object) -> bool:
        if not isinstance(turn, dict):
            return False
        turn_seq = turn.get("seq")
        if isinstance(turn_seq, bool) or not isinstance(turn_seq, (str, int)):
            return False
        try:
            return int(turn_seq) == seq and turn.get("body") == body
        except (TypeError, ValueError, OverflowError):
            return False

    return any(exact_turn(turn) for turn in turns)


def _update_receipt_escalation(receipt_path: Path, field: str, value: str) -> None:
    try:
        doc = _read_json(receipt_path)
    except (OSError, json.JSONDecodeError):
        # The receipt this function is asked to update does not exist or is
        # unreadable — still never swallowed, but there is nothing left to
        # update, so this is reported and skipped rather than raised.
        print(f"could not update {receipt_path} (escalation.{field}) — the file is "
              f"missing or unreadable; the local receipt is now stale.", file=sys.stderr)
        return
    doc.setdefault("escalation", {})[field] = value
    try:
        _write_json_fsynced(receipt_path, doc)
    except OSError as exc:
        print(f"could not rewrite {receipt_path} with escalation.{field}={value!r}: {exc}",
              file=sys.stderr)


def _loud_fallback(verb: str, receipt_path: Path) -> None:
    noun = "AN INCIDENT" if verb == "open-incident" else "A MODEL ROOM EVENT"
    print("", file=sys.stderr)
    print(f"COULD NOT OPEN {noun} for this refusal — the record layer was unreachable twice.",
          file=sys.stderr)
    print(f"A local receipt was written and fsynced: {receipt_path}", file=sys.stderr)
    print(f"Escalate this BY HAND: read that file and call {verb} once the record layer "
          f"is reachable.", file=sys.stderr)


def cmd_escalate(args: argparse.Namespace) -> int:
    receipt_path = Path(args.receipt_path)
    reason_class = args.reason_class or "wrapper_terminated"
    detail = args.detail or "migrate-prod.sh exited with no named cause"
    operation = args.migration or reason_class
    fingerprint = f"{SERVICE}|{ENVIRONMENT}|{operation}|{reason_class}"

    try:
        incident_ref, incident_landed = _open_incident_and_readback(
            args.run_door, operation, reason_class, detail, fingerprint)
    except Exception as exc:  # noqa: BLE001 — escalation must NEVER raise past this point
        incident_ref, incident_landed = "NONE", False
        print(f"open-incident escalation raised unexpectedly: {exc}", file=sys.stderr)

    _update_receipt_escalation(
        receipt_path, "incident_call",
        incident_ref if incident_landed else f"failed, see {receipt_path}")
    if not incident_landed:
        _loud_fallback("open-incident", receipt_path)

    room_body = (f"migrate-prod refused: {reason_class} — {detail}. "
                 f"Incident: {incident_ref if incident_landed else 'NONE'}.")
    try:
        room_landed = _add_room_turn_and_readback(args.run_door, room_body)
    except Exception as exc:  # noqa: BLE001 — same "never raise" floor as above
        room_landed = False
        print(f"add-room-turn escalation raised unexpectedly: {exc}", file=sys.stderr)

    _update_receipt_escalation(
        receipt_path, "model_room_call",
        "ok" if room_landed else f"failed, see {receipt_path}")
    if not room_landed:
        _loud_fallback("add-room-turn", receipt_path)

    # This exit code is deliberately never checked by migrate-prod.sh (called
    # with `|| true`): escalation succeeding or failing must never change the
    # refusal's own exit code (design §6 — no new production write path, and
    # escalation is notification, not a gate).
    return 0 if (incident_landed and room_landed) else 1


def cmd_neon_dsn(args: argparse.Namespace) -> int:
    """Keep raw provider stderr in memory; emit only redacted diagnostics."""
    try:
        result = subprocess.run(['neonctl', 'connection-string', 'production',
            '--project-id', args.project_id, '--role-name', 'neondb_owner'],
            capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"neonctl unavailable ({type(exc).__name__})", file=sys.stderr)
        return 1
    if result.stderr:
        safe = redact_text(result.stderr, known_secrets=sensitive_env_values(os.environ))
        print(safe[:DETAIL_MAX].replace('\n', ' · '), file=sys.stderr)
    if result.returncode == 0:
        print(result.stdout.strip())
    return result.returncode if result.returncode > 0 else (1 if result.returncode < 0 else 0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    neon = sub.add_parser("neon-dsn")
    neon.add_argument("--project-id", required=True)
    neon.set_defaults(func=cmd_neon_dsn)

    wr = sub.add_parser("write-receipt")
    wr.add_argument("--reason-class", required=True)
    wr.add_argument("--detail", required=True)
    wr.add_argument("--migration", default="")
    wr.add_argument("--host", default="")
    wr.add_argument("--out-dir", required=True)
    wr.set_defaults(func=cmd_write_receipt)

    es = sub.add_parser("escalate")
    es.add_argument("--receipt-path", required=True)
    es.add_argument("--reason-class", required=True)
    es.add_argument("--detail", required=True)
    es.add_argument("--migration", default="")
    es.add_argument("--run-door", required=True)
    es.set_defaults(func=cmd_escalate)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
