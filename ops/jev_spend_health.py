"""Local Jev spend estimate and its single deduplicated response loop.

This is a library for the canonical health surface. It reads the canonical
usage receipt log, never vendor credentials or prompt text.
"""

import fcntl
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import record_call  # noqa: E402


def _root():
    source_root = Path(__file__).resolve().parent.parent
    try:
        git_dir = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=source_root, capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip()
        return Path(git_dir).parent if git_dir else source_root
    except (OSError, subprocess.SubprocessError):
        return source_root


ROOT = _root()
USAGE_LOG = ROOT / "out" / "jev-calls.jsonl"
FACTORY_USAGE_LOG = Path.home() / ".local" / "state" / "software-factory" / "jev-calls.jsonl"
LOOP_STATE = ROOT / "out" / "jev-spend-loop.json"
CONFIG = Path(__file__).resolve().parent / "config" / "jev-cost-guard.v1.json"
ACTION = ("on breach: open/update one dedup loop · owner orchestrator · "
          "remediation find caller in jev usage log · verify next UTC-day estimate "
          "below threshold · auto-clear when below threshold")


def _run_verb(name, payload):
    result = record_call.call_verb(name, payload, timeout=35)
    if result.kind not in (record_call.OK, record_call.REFUSED):
        raise RuntimeError(result.describe())
    answer = result.reply
    if not result.ok or not isinstance(answer, dict) or (name != "read-loop" and answer.get("ok") is not True):
        raise RuntimeError(f"{name} did not confirm the write")
    return answer


def _loop_version(run_verb, loop_id):
    current = run_verb("read-loop", {"loop_id": loop_id})
    current = current.get("loop", current)
    if current.get("loop_id") != loop_id or type(current.get("version")) is not int:
        raise RuntimeError("read-loop returned no matching version")
    return current["version"]


def _state(path):
    try:
        result = json.loads(Path(path).read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


@contextmanager
def _loop_lock(path):
    lock = Path(str(path) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _id(day, action, amount):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"carr-jev-spend:{day}:{action}:{amount:.2f}"))


def _today_usage(log_path, day):
    calls = tokens = unknown = 0
    with Path(log_path).open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
                if row.get("cache_hit") is True:
                    continue
                stamp = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))
                if stamp.astimezone(timezone.utc).date().isoformat() != day:
                    continue
                usage = row.get("usage") or {}
                value = usage.get("input_tokens")
                if type(value) is not int or value < 0:
                    status = row.get("http_status")
                    if row.get("ok") is True or (type(status) is int and 200 <= status < 300):
                        unknown += 1
                    continue
                calls += 1
                tokens += value
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
    return calls, tokens, unknown


def read_worker_usage(day):
    """Read the Worker's server-timestamped receipts through its read verb."""
    row = _run_verb("read-jev-call-receipt-integrity", {}).get("daily_usage")
    if not isinstance(row, dict) or row.get("utc_day") != day or any(
            type(row.get(key)) is not int or row[key] < 0
            for key in ("calls", "input_tokens", "unknown")):
        raise RuntimeError("Worker daily Jev usage is unavailable or stale")
    return row


def nightly_exit_status(line):
    """Missing token counts are informational; fail unreadable sources/writes."""
    return 0 if line.startswith(("OK jev spend", "WARN jev spend", "UNKNOWN jev spend")) and not any(
        marker in line for marker in
        ("usage unavailable", "loop action FAILED")
    ) else 1


def check_spend(log_path=USAGE_LOG, config_path=CONFIG, state_path=LOOP_STATE,
                run_verb=_run_verb, *, now=None, extra_logs=(), worker_usage=None):
    """Return one health row, with the bound action in the row itself."""
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    threshold = float(config["daily_warning_usd"])
    price = float(config["price_usd_per_million_input_tokens"])
    day = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()
    if not Path(log_path).is_file() and not any(Path(extra).is_file() for extra in extra_logs) and worker_usage is None:
        return f"UNAVAILABLE jev spend — local usage log absent · {ACTION}"
    calls = tokens = unknown = 0
    try:
        for path in (log_path, *extra_logs):
            if Path(path).is_file():
                measured = _today_usage(path, day)
                calls += measured[0]
                tokens += measured[1]
                unknown += measured[2]
    except (OSError, UnicodeError):
        return f"UNAVAILABLE jev spend — local usage unavailable · {ACTION}"
    abandoned = 0
    abandon_after = 3600
    worker_unavailable = False
    if worker_usage is not None:
        try:
            measured = worker_usage(day)
            if not isinstance(measured, dict) or any(type(measured.get(key)) is not int or measured[key] < 0
                                                     for key in ("calls", "input_tokens", "unknown")):
                raise ValueError("invalid Worker Jev usage")
            calls += measured["calls"]
            tokens += measured["input_tokens"]
            unknown += measured["unknown"]
            abandoned = measured.get("abandoned_attempts", 0)
            abandon_after = measured.get("abandon_after_seconds", 3600)
            if type(abandoned) is not int or abandoned < 0 or type(abandon_after) is not int or abandon_after <= 0:
                raise ValueError("invalid Worker abandoned attempt usage")
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            worker_unavailable = True
    amount = tokens * price / 1_000_000
    missing = f"{unknown} call or attempt{'s' if unknown != 1 else ''} missing usage"
    attempts = f" · {abandoned} abandoned attempts (older than {abandon_after}s; excluded from missing usage)"
    if (unknown or worker_unavailable) and amount <= threshold:
        return (f"UNKNOWN jev spend — {missing}"
                f"; Worker usage {'unavailable' if worker_unavailable else 'read'} "
                f"({calls} measured calls; warning retained until usage is measurable) "
                f"· {ACTION}{attempts}")
    status = "WARN" if amount > threshold else "OK"
    line = (f"{status} jev spend — ${amount:.3f} estimated / UTC day "
            f"({calls} calls, {tokens} input tokens; threshold ${threshold:.2f}) · {ACTION}{attempts}")
    if unknown:
        line += f" · at least this amount; {missing}"
    if worker_unavailable:
        line += " · at least this amount; Worker usage unavailable"
    body = (f"Jev estimated recorded spend is ${amount:.3f} on {day} UTC, above "
            f"${threshold:.2f}/day. Find caller in jev usage log at "
            "out/jev-calls.jsonl, in software-factory's jev-calls.jsonl, "
            "or in Worker Jev receipts; inspect prompt hashes for duplicate "
            "calls and verify the next UTC-day estimate below threshold.")
    try:
        with _loop_lock(state_path):
            state = _state(state_path)
            if amount > threshold:
                if not state.get("loop_id"):
                    answer = run_verb("add-loop", {
                        "idempotency_key": _id(day, "add", amount),
                        # CARR's orchestrator queue is named "claude" in the
                        # loop owner contract; the health row names the role.
                        "kind": "open_loop", "domain": "system", "owner": "claude",
                        "body": body, "marker": "none", "blocker": "capability",
                        "blocker_detail": "TypeSafe callers outside this machine's local usage ledger",
                    })
                    if not answer.get("loop_id"):
                        raise RuntimeError("add-loop returned no loop_id")
                    state = {"loop_id": answer["loop_id"], "reported": amount, "day": day}
                    _save_state(state_path, state)
                elif day != state.get("day") or amount - float(state.get("reported", 0)) >= 0.10:
                    run_verb("update-loop", {
                        "idempotency_key": _id(day, "update", amount),
                        "loop_id": state["loop_id"],
                        "base_version": _loop_version(run_verb, state["loop_id"]),
                        "body": body,
                    })
                    state.update(reported=amount, day=day)
                    _save_state(state_path, state)
            elif state.get("loop_id") and not worker_unavailable and not unknown:
                run_verb("close-loop", {
                    "idempotency_key": _id(day, "clear", amount),
                    "loop_id": state["loop_id"], "resolution": "done",
                    "base_version": _loop_version(run_verb, state["loop_id"]),
                    "outcome": (f"Auto-cleared: estimated Jev spend on {day} UTC "
                                f"is ${amount:.3f}, below ${threshold:.2f}/day."),
                })
                _save_state(state_path, {})
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        line += f" · loop action FAILED ({type(exc).__name__})"
    return line
