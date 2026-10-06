"""Local spool for Claude continuity receipts the store did not confirm.

The spool holds only receipts whose delivery is unknown (the store was
unreachable or did not answer in time).  A receipt the store refused can never
apply, so it goes straight to the archive with the store's reason.  The drain
re-sends spooled lifecycle receipts in order under their original idempotency
keys; it never sends a checkpoint or anything carrying a pending external
effect.  Nothing here deletes a receipt: every one that leaves the spool is
appended to the archive with its outcome.
"""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import pathlib
import secrets
import shlex
import stat
import subprocess
import tempfile
import time
import uuid
import re
from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
from lib import claude_continuity_config as continuity_config
from lib.health_incident import (confirmed_reply, loop_record as _loop_record,
                                 read_state as _state, save_state as _save_state,
                                 incident_lock as _loop_lock, sync_directory)
from lib.secret_redaction import redact_text, sensitive_env_values

REPO = pathlib.Path(__file__).resolve().parents[1]
CALL_TIMEOUT_SECONDS = 7.0
MAX_SPOOL_BYTES = 1_000_000
MAX_SPOOL_FILES = 100
MAX_DRAIN_ATTEMPTS = 5
WARN_COUNT = 10
WARN_AGE_HOURS = 36
DRAINABLE_VERB = "claude-record-event"
RECORD_EVENT_FIELDS = frozenset({
    "runtime", "session_id", "transcript_path_digest", "project_affinity", "cwd",
    "parent_session_id", "native_agent_id", "model_id", "idempotency_key", "event_type",
    "cursor", "transcript_digest", "observed_at", "telemetry", "checkpoint_version",
})
ACTION = ("on breach: open/update one dedup loop · owner orchestrator · "
          "remediation read the failure kinds in the spool and the refusal reasons in the archive, "
          "then fix the refused or unreachable write · verify the next drain leaves fewer than "
          f"{WARN_COUNT} unsent and none older than {WARN_AGE_HOURS}h, with no unresolved archive failures · "
          "auto-clear when under threshold and every failed delivery is store-confirmed")


@dataclass(frozen=True)
class Delivered:
    response: dict


@dataclass(frozen=True)
class Failed:
    failure: dict


@dataclass(frozen=True)
class HealthResult:
    line: str
    status: int


TERMINAL_ERRORS = frozenset({
    "key_reuse", "claude_continuity_binding_conflict", "claude_event_key_conflict",
    "claude_continuity_digest_invalid", "claude_continuity_field_invalid",
    "claude_continuity_json_invalid", "claude_continuity_payload_too_large",
    "claude_event_checkpoint_version_invalid", "claude_event_type_invalid",
    "claude_runtime_required", "claude_session_id_invalid", "invalid_arguments",
})


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def spool_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get(
        "CARR_CLAUDE_CONTINUITY_SPOOL_DIR",
        str(pathlib.Path.home() / ".config/carr/claude-continuity-spool"))).expanduser()


def archive_path() -> pathlib.Path:
    default = spool_dir().with_name(spool_dir().name + "-archive.jsonl")
    return pathlib.Path(os.environ.get("CARR_CLAUDE_CONTINUITY_SPOOL_ARCHIVE", str(default))).expanduser()


def _attempts_path() -> pathlib.Path:
    return spool_dir().with_suffix(".drain.json")


def spool_key() -> bytes:
    path = spool_dir().with_suffix(".key")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        data = path.read_bytes()
        if len(data) != 32 or stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise ValueError("invalid spool key")
        return data
    except FileNotFoundError:
        data = secrets.token_bytes(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        return data


def _signature(body: dict) -> str:
    unsigned = {key: value for key, value in body.items() if key != "hmac_sha256"}
    return hmac.new(spool_key(), canonical(unsigned), hashlib.sha256).hexdigest()


def invoke(verb: str, args: dict) -> Delivered | Failed:
    """Return a confirmed response or a failure with a retry classification.

    Only proven terminal argument, key, and binding errors are refused.
    Unknown server exceptions retain redacted diagnostics for a later retry.
    """
    raw = os.environ.get("CARR_CLAUDE_CONTINUITY_CALL")
    try:
        argv = shlex.split(raw) if raw else [str(REPO / "run.sh"), "call"]
    except ValueError:
        return Failed({"kind": "transport", "exit": None})
    argv.extend((verb, json.dumps(args, separators=(",", ":"), ensure_ascii=False)))
    env = {**os.environ, "CARR_MCP_CLIENT_PROFILE": "claude-continuity"}
    try:
        proc = subprocess.run(argv, cwd=REPO, env=env, capture_output=True,
                              text=True, timeout=CALL_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        return Failed({"kind": "timeout"})
    except OSError:
        return Failed({"kind": "transport", "exit": None})
    if proc.returncode:
        marker = proc.stderr.find("TOOL ERROR ")
        if marker >= 0:
            try:
                payload, _ = json.JSONDecoder().raw_decode(proc.stderr[marker + len("TOOL ERROR "):])
                error = payload.get("error") if isinstance(payload, dict) else None
                if isinstance(error, str) and error:
                    detail = redact_text(json.dumps(payload), known_secrets=sensitive_env_values(env))[:500]
                    return Failed({"kind": "refused" if error in TERMINAL_ERRORS else "server",
                                   "error": redact_text(error, known_secrets=sensitive_env_values(env))[:200],
                                   "detail": detail})
            except ValueError:
                pass
        return Failed({"kind": "transport", "exit": proc.returncode})
    try:
        response = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return Failed({"kind": "transport", "exit": 0})
    if isinstance(response, dict) and response.get("ok") is True:
        return Delivered(response)
    return Failed({"kind": "transport", "exit": 0})


def archive(receipt: dict, outcome: str, reason: str) -> None:
    path = archive_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    row = {"schema_version": 1, "outcome": outcome, "reason": reason,
           "archived_at": datetime.now(timezone.utc).isoformat(), "receipt": receipt}
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        offset = os.lseek(fd, 0, os.SEEK_END)
        encoded = canonical(row) + b"\n"
        try:
            written = 0
            while written < len(encoded):
                count = os.write(fd, encoded[written:])
                if count <= 0:
                    raise OSError("archive append made no progress")
                written += count
            os.fsync(fd)
            sync_directory(path.parent)
        except BaseException:
            os.ftruncate(fd, offset)
            raise
    finally:
        os.close(fd)


def _read(path: pathlib.Path) -> dict | None:
    try:
        body = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def _move_to_archive(path: pathlib.Path, outcome: str, reason: str) -> None:
    receipt = _read(path)
    if receipt is None or outcome == "not_replayable":
        quarantine = spool_dir().with_name(spool_dir().name + "-quarantine")
        quarantine.mkdir(parents=True, exist_ok=True, mode=0o700)
        archive(receipt if receipt is not None else {"unreadable_file": path.name}, outcome, reason)
        os.replace(path, quarantine / path.name)
        sync_directory(quarantine)
    else:
        archive(receipt, outcome, reason)
        path.unlink(missing_ok=True)
    sync_directory(path.parent)


def spooled() -> list[pathlib.Path]:
    """Spool files oldest first; the name starts with the spool time in ns."""
    return sorted(spool_dir().glob("*.json"))


def spool_receipt(verb: str, args: dict, failure: dict, *,
                  max_files: int = MAX_SPOOL_FILES, max_bytes: int = MAX_SPOOL_BYTES) -> None:
    with _spool_lock():
        directory = spool_dir()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        body = {"schema_version": 1, "verb": verb, "args": args, "failure": failure,
                "spooled_at": datetime.now(timezone.utc).isoformat()}
        body["hmac_sha256"] = _signature(body)
        encoded = canonical(body) + b"\n"
        fd, temp_name = tempfile.mkstemp(prefix=".receipt-", dir=directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, directory / f"{time.time_ns()}-{secrets.token_hex(4)}.json")
            sync_directory(directory)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            pathlib.Path(temp_name).unlink(missing_ok=True)

        files = []
        total = 0
        for path in spooled():
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                continue
            files.append((path, size))
            total += size
        while files and (len(files) > max_files or total > max_bytes):
            oldest, size = files.pop(0)
            try:
                _move_to_archive(oldest, "overflow", "spool_cap_reached")
            except FileNotFoundError:
                pass
            total -= size


def _not_replayable(body: dict | None) -> str | None:
    if body is None:
        return "unreadable"
    signature = body.get("hmac_sha256")
    if (not isinstance(signature, str) or re.fullmatch(r"[0-9a-f]{64}", signature) is None
            or not hmac.compare_digest(signature, _signature(body))):
        return "signature_invalid"
    if body.get("verb") != DRAINABLE_VERB:
        return str(body.get("verb"))[:200]
    args = body.get("args")
    if not isinstance(args, dict) or not args.get("idempotency_key") or set(args) - RECORD_EVENT_FIELDS:
        return "not_a_lifecycle_receipt"
    return None


@contextmanager
def _spool_lock(*, blocking=True, suffix=".spool.lock"):
    lock = spool_dir().with_suffix(suffix)
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _attempts() -> dict:
    try:
        value = json.loads(_attempts_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_attempts(attempts: dict) -> None:
    _save_state(_attempts_path(), attempts)


def drain(call=None) -> dict:
    """Re-send spooled lifecycle receipts in order; stop at the first unknown outcome."""
    call = call or invoke
    result = {"delivered": 0, "already_recorded": 0, "archived": 0, "transient": 0, "locked": False}
    mode_path = pathlib.Path(os.environ.get("CARR_CLAUDE_CONTINUITY_MODE_FILE",
                         str(pathlib.Path.home() / ".config/carr/claude-continuity-mode.json")))
    try:
        mode = continuity_config.read_mode(mode_path, continuity_config.load(REPO))
    except (OSError, ValueError, RuntimeError):
        mode = None
    if mode not in {"checkpoint", "inject"}:
        result["inactive"] = True
        return result
    with _spool_lock(blocking=False, suffix=".drain.lock") as held:
        if not held:
            result["locked"] = True
            return result
        attempts = _attempts()
        try:
            for path in spooled():
                with _spool_lock():
                    if not path.exists():
                        continue
                    body = _read(path)
                    reason = _not_replayable(body)
                    if reason:
                        _move_to_archive(path, "not_replayable", reason)
                        result["archived"] += 1
                        continue
                if body is None:
                    raise ValueError("validated receipt missing")
                reply = call(body["verb"], body["args"])
                with _spool_lock():
                    if isinstance(reply, Delivered):
                        outcome = "already_recorded" if reply.response.get("replayed") is True else "delivered"
                        archive(body, outcome, "store_confirmed")
                        path.unlink(missing_ok=True)
                        sync_directory(path.parent)
                        attempts.pop(path.name, None)
                        result[outcome] += 1
                    elif not path.exists():
                        attempts.pop(path.name, None)
                    elif reply.failure["kind"] == "refused":
                        _move_to_archive(path, "refused", reply.failure["error"])
                        attempts.pop(path.name, None)
                        result["archived"] += 1
                    else:
                        tried = attempts.get(path.name, 0) + 1
                        if tried >= MAX_DRAIN_ATTEMPTS:
                            _move_to_archive(path, "retry_budget_exhausted", reply.failure["kind"])
                            attempts.pop(path.name, None)
                            result["archived"] += 1
                        else:
                            attempts[path.name] = tried
                        result["transient"] += 1
                        break
        finally:
            live = {path.name for path in spooled()}
            _save_attempts({name: count for name, count in attempts.items() if name in live})
    return result


def measure(now: datetime) -> tuple[int, float | None]:
    """Unsent count and the oldest receipt's age in hours, from the spool itself."""
    files = spooled()
    if not files:
        return 0, None
    oldest_ns = int(files[0].name.split("-", 1)[0])
    return len(files), (now.timestamp() - oldest_ns / 1e9) / 3600


def loop_state_path() -> pathlib.Path:
    return spool_dir().with_suffix(".loop.json")


def run_verb(name: str, payload: dict) -> dict:
    """Loop verbs run as the ordinary local actor, not the continuity token."""
    from lib.record_call import call_verb
    return confirmed_reply(name, call_verb(name, payload, timeout=35))


def unresolved_failures() -> int:
    failed: set[str] = set()
    delivered: set[str] = set()
    try:
        with archive_path().open(encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_SH)
            for number, line in enumerate(handle):
                try:
                    row = json.loads(line)
                    receipt = row["receipt"]
                    args = receipt.get("args", {})
                    key = (hashlib.sha256(canonical({"verb": receipt.get("verb"), "args": args})).hexdigest()
                           if args.get("idempotency_key") else
                           receipt.get("unreadable_file") or f"archive-line-{number}")
                    if row["outcome"] in {"delivered", "already_recorded"}:
                        delivered.add(key)
                    else:
                        failed.add(key)
                except (ValueError, KeyError, TypeError, AttributeError):
                    failed.add(f"archive-line-{number}")
    except FileNotFoundError:
        pass
    return len(failed - delivered)


def _complete_pending(state_path, state, run_verb):
    pending = state.get("pending")
    if not pending:
        return
    answer = run_verb(pending["verb"], pending["payload"])
    if answer.get("ok") is not True or answer.get("error"):
        raise RuntimeError("incident action did not confirm the write")
    if pending["verb"] == "add-loop":
        if not isinstance(answer.get("loop_id"), str) or not answer["loop_id"]:
            raise RuntimeError("add-loop returned no loop_id")
        state["loop_id"] = answer["loop_id"]
        state.pop("pending")
    else:
        state.clear()
    _save_state(state_path, state)


def health_row(*, run_verb, state_path: pathlib.Path, now: datetime, drained: dict | None = None) -> HealthResult:
    """Report queued and unresolved archived deliveries and transact one incident."""
    line = f"UNAVAILABLE claude continuity spool · {ACTION}"
    try:
        with _loop_lock(state_path):
            with _spool_lock():
                count, age = measure(now)
                failures = unresolved_failures()
            breach = failures > 0 or count >= WARN_COUNT or (age is not None and age > WARN_AGE_HOURS)
            oldest = "none" if age is None else f"oldest {age:.1f}h"
            ran = ""
            if drained is not None:
                ran = (f" · this run delivered {drained['delivered'] + drained['already_recorded']}, "
                       f"archived {drained['archived']}")
            line = (f"{'WARN' if breach else 'OK'} claude continuity spool — {count} unsent, {oldest} "
                    f"(warn at {WARN_COUNT} or {WARN_AGE_HOURS}h), {failures} unresolved archived{ran} · {ACTION}")
            state = _state(state_path)
            _complete_pending(state_path, state, run_verb)
            if breach and not state.get("loop_id"):
                generation = str(uuid.uuid4())
                state = {"generation": generation, "pending": {"verb": "add-loop", "payload": {
                    "idempotency_key": generation,
                    "kind": "open_loop", "domain": "system", "owner": "claude", "marker": "none",
                    "body": (f"Claude continuity receipts are not reaching the record layer: {count} unsent, "
                             f"{oldest}, {failures} unresolved archived. Read the spool and archive failures, "
                             "fix the refused or unreachable write, then run "
                             "`tools/health-check.py --section claude-continuity-spool`. "
                             f"Verify fewer than {WARN_COUNT} unsent, none older than {WARN_AGE_HOURS}h, "
                             "and no unresolved archived failures."),
                    "blocker": "capability",
                    "blocker_detail": "the Worker refusing or not answering claude-record-event from the continuity token",
                }}}
                _save_state(state_path, state)
                _complete_pending(state_path, state, run_verb)
            elif not breach and state.get("loop_id"):
                state["pending"] = {"verb": "close-loop", "payload": {
                    "idempotency_key": str(uuid.uuid4()), "loop_id": state["loop_id"],
                    "resolution": "done", "base_version": _loop_record(run_verb, state["loop_id"])["version"],
                    "outcome": f"Auto-cleared: {count} unsent, {oldest}, no unresolved archived failures.",
                }}
                _save_state(state_path, state)
                _complete_pending(state_path, state, run_verb)
            return HealthResult(line, int(breach))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return HealthResult(line + f" · loop action FAILED ({type(exc).__name__})", 1)
