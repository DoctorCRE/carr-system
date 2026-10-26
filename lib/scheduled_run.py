"""lib/scheduled_run.py — the ONE implementation of "what happened in a Claude
Code scheduled-task session", shared by the Stop hook and the manual CLI (rule
a8c55a47: a manual path and an automated path that do the same job must be the
same code).

WHY THIS EXISTS. Program 3's gate line: "Job failure becomes a durable failed
run, incident, or Work Request. It cannot exist only in stdout or a local log."
The ~17 Claude Code scheduled tasks on this Mac are PROMPTS, not scripts — they
have no exit code, so nothing wrote a row to ops.run when one of them failed.
This module reads a scheduled-task session's own transcript (the harness's
record of what actually happened) and turns it into the same shape of ops.run
call bin/nightly.sh's record_run() already makes for the launchd chain.

HOW IT KNOWS WHICH TASK. Proven pattern, copied from hooks/model-floor-gate.py
(measured against a real scheduled-run transcript on 2026-08-13): a scheduled
run's FIRST transcript record is a queue-operation whose `content` field opens
with `<scheduled-task name="..." file="...">`. Reading that attribute is immune
to whatever the session goes on to talk about; a plain substring search over the
transcript is not (an interactive session merely discussing a run's name would
false-positive).

HOW IT KNOWS THE OUTCOME. Deterministic signals only (rule 5e89c211: never spend
a cognition token on a decision already expressible as code) — this module does
NOT ask a model to judge whether the run "went well":
  - a PreToolUse gate DENY anywhere in the transcript (guard-unattended,
    model-floor-gate, escalation-gate, ...) means the run was actively stopped
    from doing its job -> failed, failure_class=gate_denied.
  - the run's last tool call before Stop came back is_error=true with nothing
    after it -> failed, failure_class=tool_error.
  - otherwise -> succeeded.
This intentionally does NOT try to parse task-specific semantic success (did
loop-drain close 3 loops, did restore-rehearse print PASS) — that would require
per-task parsing rules that drift from the prompts. It catches the category the
doctrine line is actually worried about: a run that stopped existing anywhere
but stdout.

SERVICE KEY = TASK NAME. ops/config/services.json registers one service per
scheduled task, keyed identically to the task's own directory name (taskId), so
no separate mapping table exists to fall out of sync. An unregistered task name
(one of the three currently-disabled tasks, or a brand new one nobody has added
yet) makes ops-record.py refuse with its own "no service registered" message
(exit 78) — logged, never fatal, and it names the fix.

nightly-record-layer IS a registered service already (the launchd chain's own
key). Its OUTER Claude Code session (the one that launches ./bin/nightly.sh) is
recorded under the SAME service but a DISTINCT run_key (RUN_KEY, below) so it
never collides with the chain's own per-step rows (nightly.cadence-engine,
nightly.exports, ...).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO, "hooks"))
try:                                    # telemetry only — never load-bearing
    import hook_meter
    LOG = hook_meter.guard_log_path(REPO)
except Exception:                       # a missing meter must not change a verdict
    LOG = os.path.join(REPO, "out", "hook-guard.log")
PYTHON = os.path.join(REPO, ".venv", "bin", "python")
OPS_RECORD = os.path.join(REPO, "tools", "ops-record.py")

# The run_key every scheduled-task session is recorded under. Fixed and single
# because a scheduled-task session is one unit of work, unlike bin/nightly.sh's
# multi-step chain which threads several keys under one correlation id.
RUN_KEY = "scheduled-session"

# How many lines at the head of the transcript to scan for the launch marker.
# Three, not the whole file — same reasoning as model-floor-gate.py: the marker
# is always on line 0, and scanning further risks matching a LATER, unrelated
# interactive turn that merely talks about a run by name.
HEAD_LINES = 3

# Cap on how much of the transcript we scan for outcome signals. A scheduled
# session that finishes its own turn and yields (the normal case, and the ONLY
# case this module records — see idempotency in the hook) is small; sessions
# observed in practice run tens to a few hundred KB by their first Stop. 8MB is
# generous headroom without risking a multi-hour continued-interactive session
# (observed on 2026-08-13/14, a scheduled run whose transcript kept growing for
# nine more hours of unrelated human-driven work after its own turn ended) from
# making every Stop of that session re-scan a multi-megabyte file.
SCAN_BYTES_CAP = 8_000_000

LAUNCH_MARKER = re.compile(r'<scheduled-task\s+name="([^"]+)"')
SOCIAL_BATCH_MARKER = re.compile(
    r"SOCIAL_BATCH_RESULT state=(succeeded|failed) week_start=(\d{4}-\d{2}-\d{2}) "
    r"queue_count=(\d+|unknown) complete=(true|false) failure_class=(none|no_output|zero_drafts|zero_duration)"
)


def social_batch_result(records: list[dict], started_at: str | None) -> dict:
    """Read the social task's machine-readable queue observation, or fail closed."""
    marker = None
    for record in records:
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                value = block.get("content")
                if isinstance(value, str):
                    for line in value.splitlines():
                        if line.startswith("SOCIAL_BATCH_RESULT "):
                            marker = SOCIAL_BATCH_MARKER.fullmatch(line)
    if marker:
        state, week_start, count, complete, failure = marker.groups()
        try:
            week = date.fromisoformat(week_start)
        except ValueError:
            week = None
        count_value = None if count == "unknown" else int(count)
        valid_failure = (failure in {"no_output", "zero_duration"} and count == "unknown"
                         and complete == "false" and week is not None
                         and week.isoformat() == week_start and week.weekday() == 0)
        if (complete == "true" and count_value is not None and week is not None
                and week.isoformat() == week_start and week.weekday() == 0
                and ((count_value == 0 and state == "failed" and failure == "zero_drafts")
                     or (count_value > 0 and state == "succeeded" and failure == "none"))):
            return {"week_start": week_start, "queue_count": count_value,
                    "queue_complete": True, "failure": None if count_value else "zero_drafts"}
        if valid_failure and state == "failed":
            return {"week_start": week_start, "queue_count": None,
                    "queue_complete": False, "failure": failure}

    try:
        launched = datetime.fromisoformat((started_at or "").replace("Z", "+00:00"))
    except ValueError:
        launched = datetime.now(timezone.utc)
    local_day = launched.astimezone(ZoneInfo("America/Chicago")).date()
    days = (7 - local_day.weekday()) % 7 or 7
    week_start = (local_day + timedelta(days=days)).isoformat()
    return {"week_start": week_start, "queue_count": None,
            "queue_complete": False, "failure": "no_output"}


def log(msg: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with open(LOG, "a") as fh:
            fh.write(f"{ts} scheduled-run-record {msg.rstrip()}\n")
    except Exception:
        pass


def quick_launched_task(path: str, n: int = HEAD_LINES) -> str | None:
    """Cheap task-identity check: read only the first `n` lines of the
    transcript file directly, without parsing the rest.

    THIS IS THE CALL EVERY STOP EVENT PAYS, including every ordinary
    interactive session's every turn. A full load_records() scan (capped at
    SCAN_BYTES_CAP) is fine ONCE a session is confirmed to be a scheduled-task
    launch, but paying it on every Stop of a long, ordinary, non-scheduled
    session would mean re-reading megabytes of transcript per turn for no
    reason — this reads at most a few hundred bytes.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= n:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if not isinstance(rec, dict):
                    continue
                content = rec.get("content")
                if not isinstance(content, str):
                    continue
                found = LAUNCH_MARKER.search(content)
                if found:
                    return found.group(1).strip()
    except OSError:
        pass
    return None


def load_records(path: str, cap: int = SCAN_BYTES_CAP) -> list[dict]:
    """Parse a transcript JSONL file into a list of dicts, capped by byte size.

    A truncated read still yields every record from the START of the file
    (queue-operation, launch marker, the run's own turn) because we stop
    consuming AFTER the cap rather than seeking into the middle — the run's own
    work is always at the front, and anything later is either padding or a
    continuation this module deliberately never looks at (see the hook's
    per-session cache).
    """
    records: list[dict] = []
    read = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                read += len(line)
                line = line.strip()
                if not line:
                    if read > cap:
                        break
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if isinstance(rec, dict):
                    records.append(rec)
                if read > cap:
                    break
    except OSError:
        pass
    return records


def launched_task(records: list[dict], n: int = HEAD_LINES) -> str | None:
    """The scheduled task this session was LAUNCHED as, or None.

    Reads the launch attribute out of the opening queue-operation record's
    `content` field and nothing else — naming a run in conversation is not
    being one.
    """
    for rec in records[:n]:
        content = rec.get("content")
        if not isinstance(content, str):
            continue
        found = LAUNCH_MARKER.search(content)
        if found:
            return found.group(1).strip()
    return None


def launch_timestamp(records: list[dict], n: int = HEAD_LINES) -> str | None:
    """The ISO timestamp on the launch queue-operation record, if present."""
    for rec in records[:n]:
        if rec.get("type") == "queue-operation" and isinstance(rec.get("content"), str):
            ts = rec.get("timestamp")
            if isinstance(ts, str):
                return ts
    return None


# Matches BOTH how a deny can appear on disk: as genuine nested JSON
# (`"permissionDecision": "deny"`) and — the form actually observed in real
# transcripts, verified against a live session on 2026-08-14 — as a JSON
# STRING inside a string (a hook's stdout is captured as
# `"stdout": "{\"hookSpecificOutput\": ...}\n"`), where the quotes are
# backslash-escaped. re-serializing a parsed record with json.dumps() does not
# recover the first form's raw text and DOUBLE-escapes the second, so this
# scans the untouched raw file text instead of anything re-encoded.
# The reason capture deliberately does NOT try to consume escaped characters
# inside the value (no `\\.` alternative): this text may itself be escaped one
# level deep (a hook's JSON captured as a string inside another JSON string),
# so a backslash here is ambiguous between "an escaped char in the reason" and
# "the escaped terminating quote one level up". Stopping at the first quote or
# backslash gives a clean (if occasionally truncated) reason instead of
# occasionally over-consuming into the surrounding JSON — acceptable for a
# best-effort, human-readable `detail` line capped at 300 chars regardless.
DENY_MARKER = re.compile(r'\\?"permissionDecision\\?"\s*:\s*\\?"deny\\?"')
DENY_REASON = re.compile(r'\\?"permissionDecisionReason\\?"\s*:\s*\\?"([^"\\]{0,200})')


def load_raw(path: str, cap: int = SCAN_BYTES_CAP) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(cap)
    except OSError:
        return ""


def detect_gate_denial(raw_text: str) -> tuple[bool, str]:
    """A PreToolUse gate DENY anywhere in the transcript's raw text. These are
    emitted as a JSON permissionDecision by every gate in hooks/
    (guard-unattended.py, model-floor-gate.py, escalation-gate.py, ...), so a
    plain regex over the untouched file text is exact rather than a semantic
    guess — see DENY_MARKER's comment for why raw text, not a re-serialized
    record."""
    if DENY_MARKER.search(raw_text):
        reason = DENY_REASON.search(raw_text)
        return True, (reason.group(1) if reason else "a PreToolUse gate denied a call")
    return False, ""


def detect_trailing_tool_error(records: list[dict]) -> tuple[bool, str]:
    """True if the LAST tool_result in the transcript is an error with nothing
    recorded after it — the run's last action failed and it never got a chance
    to recover or was not given one before Stop fired."""
    last_error_reason = ""
    saw_anything_after_error = False
    found_error = False
    for rec in records:
        msg = rec.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            if found_error:
                saw_anything_after_error = True
            continue
        hit = False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                if block.get("is_error") is True:
                    found_error = True
                    saw_anything_after_error = False
                    hit = True
                    inner = block.get("content")
                    if isinstance(inner, str):
                        last_error_reason = inner[:200]
                    elif isinstance(inner, list):
                        for b in inner:
                            if isinstance(b, dict) and b.get("type") == "text":
                                last_error_reason = str(b.get("text", ""))[:200]
                                break
                elif found_error:
                    saw_anything_after_error = True
        if not hit and found_error:
            saw_anything_after_error = True
    return (found_error and not saw_anything_after_error), last_error_reason


def compute_outcome(records: list[dict], raw_text: str = "") -> tuple[str, str | None, str]:
    """(state, failure_class, detail) — deterministic, see module docstring."""
    denied, reason = detect_gate_denial(raw_text)
    if denied:
        return "failed", "gate_denied", f"a PreToolUse gate denied a call: {reason}"[:300]
    task = launched_task(records)
    if task == "social-batch-weekly":
        started_at = launch_timestamp(records)
        result = social_batch_result(records, started_at)
        if result["failure"]:
            failure_class = "social_batch_" + result["failure"]
            return "failed", failure_class, f"social-batch-weekly failed: {result['failure'].replace('_', ' ')}"
        trailing_error, err_reason = detect_trailing_tool_error(records)
        if trailing_error:
            detail = "session ended on an unrecovered tool error"
            if err_reason:
                detail += f": {err_reason}"
            return "failed", "tool_error", detail[:300]
        return "succeeded", None, f"social batch verified {result['queue_count']} posts for week starting {result['week_start']}"
    trailing_error, err_reason = detect_trailing_tool_error(records)
    if trailing_error:
        detail = "session ended on an unrecovered tool error"
        if err_reason:
            detail += f": {err_reason}"
        return "failed", "tool_error", detail[:300]
    return "succeeded", None, "scheduled session reached Stop with no deny or trailing tool error"


def build_run_args(task: str, state: str, failure_class: str | None,
                    started_at: str | None, ended_at: str | None,
                    detail: str, correlation: str | None,
                    source_ref: str) -> list[str]:
    """The exact argv tail for `tools/ops-record.py run`, so the hook and the
    manual CLI construct an identical call."""
    key = RUN_KEY
    args = [
        "run",
        "--service", task,
        "--key", key,
        "--state", state,
        "--source-kind", "collector",
        "--source-ref", source_ref,
        "--detail", detail,
    ]
    if failure_class:
        args += ["--failure-class", failure_class]
    if started_at:
        args += ["--started-at", started_at]
    if ended_at:
        args += ["--ended-at", ended_at]
    if correlation:
        args += ["--correlation", correlation]
    return args


def run_ops_record(argv: list[str]) -> tuple[int, str, str]:
    """Invoke tools/ops-record.py with argv, tolerating a missing DB credential
    or missing psycopg install without ever raising — the caller (hook or CLI)
    must never have the task run corrupted by a recording failure. Returns
    (returncode, stdout, stderr)."""
    python = PYTHON if os.path.exists(PYTHON) else sys.executable
    try:
        proc = subprocess.run(
            [python, OPS_RECORD, *argv],
            capture_output=True, text=True, timeout=30,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as exc:  # noqa: BLE001 — never let recording crash the caller
        return 1, "", f"scheduled-run-record: could not invoke ops-record.py: {exc}"


def record_from_transcript(transcript_path: str, correlation: str | None = None,
                            source_ref: str = "hooks/scheduled-run-record.py") -> dict:
    """The one entry point both the hook and the manual CLI call.

    Returns a dict describing what happened (for logging/printing), and never
    raises — a recording failure must fail LOUD in its own output, never
    corrupt or block the scheduled task's own run.
    """
    task = quick_launched_task(transcript_path)
    if not task:
        return {"recorded": False, "reason": "not a scheduled-task session (no launch marker)"}

    records = load_records(transcript_path)
    raw_text = load_raw(transcript_path)
    state, failure_class, detail = compute_outcome(records, raw_text)
    started_at = launch_timestamp(records)
    ended_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if task == "social-batch-weekly":
        observation = social_batch_result(records, started_at)
        try:
            sys.path.insert(0, os.path.dirname(__file__))
            import job_watchdog
            config = job_watchdog.load_config(os.path.join(REPO, "ops/config/job-watchdog.json"))
            root = Path(os.environ.get("CARR_REPO_ROOT") or REPO).resolve()
            job_watchdog.report_social_batch(root, config, **{
                **observation, "failure": None if observation["queue_complete"] else observation["failure"],
            })
        except Exception as exc:  # noqa: BLE001 — evidence must not block the stop hook
            log(f"FAIL social watchdog report: {exc}")

    argv = build_run_args(task, state, failure_class, started_at, ended_at,
                           detail, correlation, source_ref)
    rc, out, err = run_ops_record(argv)
    result = {
        "recorded": rc == 0,
        "task": task,
        "state": state,
        "failure_class": failure_class,
        "detail": detail,
        "exit_code": rc,
        "stdout": out.strip(),
        "stderr": err.strip(),
    }
    if rc == 0:
        log(f"OK task={task} state={state} failure_class={failure_class}")
    elif rc == 78:
        log(f"SKIP(not-configured) task={task} — {err.strip()[:300]}")
    else:
        log(f"FAIL(exit={rc}) task={task} state={state} — {err.strip()[:300]}")
    return result
