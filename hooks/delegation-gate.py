#!/usr/bin/env python3
"""Observe task-sticky delegation for CARR Claude Code sessions -- never deny.

REDESIGNED 2026-08-27 (WR-000019 slice S4). Across 18 days this gate's PreToolUse
denial produced 35,322 rows in out/delegation-gate.jsonl -- 19,796 sticky_latch,
14,137 second_mechanical_call, 1,389 executor_allowed_after_retry -- the single
largest friction source in the system. The GOAL it exists to serve is real and
unchanged: route mechanical work to the cheapest qualified seat when a
delegation latch is active. The MECHANISM -- denying the session's OWN tool
calls one at a time, with a retry loop to dodge a transcript-write race -- is
what retires here.

WHAT CHANGED. This file no longer returns exit 2 or {"decision": "block"} from
anywhere. Every PreToolUse call this gate used to deny is now silently
classified exactly as before (same is_broad/is_mechanical/sticky-task logic,
same "one lookup is free, the next one trips it" thresholds) and the
classification is recorded into a per-session counter bucket kept in the
existing state ledger (out/delegation-gate-state.json, now carrying a third
top-level key, "telemetry", alongside the unchanged "tasks"/"sessions" latch
bookkeeping). Nothing about the LATCH -- which session owns which delegated
task, when it resumes, when it releases -- changed; it is still exactly the
sticky_task() state machine this file has always kept, because "latch state"
is one of the three things a session's telemetry summary reports.

The retry-against-a-transcript-write-race machinery (RETRY_DELAYS,
executor_scan's re-read loop) existed ONLY to avoid denying a call whose
justifying text had not finished landing on disk. Now that nothing is denied,
a missed executor declaration costs nothing worse than one extra telemetry tick
in a session that will get a one-line Stop summary regardless -- so that
machinery is gone with the denial path it protected.

AT STOP, this hook does the other half of the redesign: it reads back this
session's accumulated telemetry bucket, appends EXACTLY ONE summary row to
out/delegation-gate-ledger.jsonl (never one row per call -- ops/delegation-
telemetry-report.py is the queryable per-session view over that ledger), and
speaks an ANNOUNCE-level message (hooks/stop_latch.announce -- the same
non-reopening register five other Stop gates were demoted onto on 2026-08-23)
only when the session materially under-delegated: DELEGATION_GATE_MATERIAL_
THRESHOLD (default 3) or more moments in the session where the old rule's
threshold was reached. A session that never reached that bar gets its ledger
row -- the report can still see it -- but no announcement; the point is that
the PATTERN stays visible in the transcript without ever blocking a call.

hooks/executor-tier-gate.py (which blocks a subagent spawn that names no model
or reasoning effort) is untouched by this slice and remains the enforcing
mechanical control in this space -- this file no longer enforces anything.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile


# WHICH COPY RUNS (2026-09-27, ops/config/delegation-gate-hook.json): the
# canonical ~/carr-system copy whenever that checkout exists, so on a Mac no
# worktree or branch can swap in its own gate. In a Claude Code cloud
# container, which has no canonical checkout, the settings command runs the
# copy under $CLAUDE_PROJECT_DIR instead. That is safe because this gate only
# observes: its latch and telemetry need nothing but repository files and a
# writable out/ beside them, and every path already fails open.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stop_latch import announce  # noqa: E402

VAULT_MARKERS = ("/CARR AI", "/My Drive/CARR AI")
STATE = os.environ.get(
    "DELEGATION_GATE_STATE", os.path.join(REPO, "out", "delegation-gate-state.json")
)
# The queryable ledger ops/delegation-telemetry-report.py reads. One row per
# SESSION, written at Stop -- never one row per call, which is what made the
# retired out/delegation-gate.jsonl grow to 35k rows in 18 days.
LEDGER = os.environ.get(
    "DELEGATION_GATE_LEDGER", os.path.join(REPO, "out", "delegation-gate-ledger.jsonl")
)
# How many "the old rule would have denied this call" moments in one session
# count as a materially under-delegated session worth an ANNOUNCE at Stop.
# One or two is a session that briefly forgot and self-corrected; three or more
# is a pattern the transcript should show plainly. Overridable for fixtures.
MATERIAL_THRESHOLD = int(os.environ.get("DELEGATION_GATE_MATERIAL_THRESHOLD", "3") or 3)

# Claude Code records these names directly. Codex uses functions.<name> in
# PreToolUse payloads and a custom_tool_call named <name> in its transcript.
MECHANICAL = {
    "Bash", "Read", "Grep", "Glob", "WebFetch", "WebSearch",
    "apply_patch", "functions.exec", "functions.apply_patch",
}
AGENT_TOOLS = {"Agent", "Task", "functions.Agent", "functions.spawn_agent"}

DELEGATE = re.compile(
    r"\b(delegat(?:e|ed|ing)|sub[- ]?agents?|lower[- ]?(?:cost|tier)?\s*models?|"
    r"cheaper\s+models?|cheapest\s+(?:qualified\s+)?model|"
    r"use\s+(?:a\s+)?(?:sonnet|haiku|terra|codex|grok))\b",
    re.I,
)
REVOKE = re.compile(
    r"\b(do not|don'?t|stop|no)\s+(?:use\s+)?(?:delegat\w*|sub[- ]?agents?|lower[- ]?models?)\b"
    r"|\bkeep (?:this|it) inline\b",
    re.I,
)
EXECUTOR = re.compile(
    r"(?im)^\s*(?:#|//)?\s*executor:\s*(?:T3(?:-inline)?|top(?:\s+seat)?|main(?:\s+seat)?|"
    r"orchestrator|Fable|Opus|inline|"
    r"Terra(?:\s+(?:peer|agent|specialist))?|peer(?:\s+Terra)?)\b[^\n]{0,180}"
    r"(?:because|—|--|:)\s*\S+"
)
# Kept only because the deny message used to assert every label appeared in it;
# the selftest still checks the regex accepts every one of these spellings.
EXECUTOR_LABELS = (
    "main seat, top seat, inline, orchestrator, T3, Fable, Opus, Terra peer"
)
RESUME_LINE = re.compile(
    r"(?im)^\s*delegation resume:\s*(dg-[0-9a-f]{16})\s*$"
)


def complete_line(task_id: str) -> re.Pattern[str]:
    """Exact completion marker for one immutable task, not a prose approximation."""
    return re.compile(
        rf"(?im)^\s*delegation complete:\s*{re.escape(task_id)}\s*$"
    )


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def in_carr_scope(cwd: str) -> bool:
    try:
        path = os.path.realpath(os.path.expanduser(cwd or ""))
    except Exception:
        return False
    if path == REPO or path.startswith(REPO + os.sep):
        return True
    return any(marker in path for marker in VAULT_MARKERS)


_LAST_DROPPED_LINES = 0


def records(path: str) -> list[dict]:
    """Read the whole transcript: a 500-record tail loses active task authority.

    A partially written trailing line is skipped rather than fatal, but the
    count is kept for diagnostics.
    """
    global _LAST_DROPPED_LINES
    out = []
    dropped = 0
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            try:
                out.append(json.loads(line))
            except Exception:
                dropped += 1
                continue
    _LAST_DROPPED_LINES = dropped
    return out


SYNTHETIC_CODEX_USER_PREFIXES = (
    "<recommended_plugins>", "# AGENTS.md instructions", "<environment_context>",
    "<app-context>", "<skills_instructions>", "<permissions instructions>",
    "<apps_instructions>", "<plugins_instructions>",
    "The following is the Codex agent history added since your last approval",
)


def message_for(rec: dict) -> dict:
    """Return a Claude message or Codex response-item payload."""
    payload = rec.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "message":
        return payload
    msg = rec.get("message")
    return msg if isinstance(msg, dict) else rec


def text_blocks(rec: dict, roles: tuple[str, ...]) -> str | None:
    """Read genuine partner/assistant text from Claude or Codex transcripts.

    Codex represents generated environment context and approval-review deltas
    as user messages. Those may quote an old "delegate" instruction, so they
    must not create a delegation latch.
    """
    msg = message_for(rec)
    role = msg.get("role") or rec.get("type")
    if role not in roles:
        return None
    if rec.get("isMeta") or rec.get("isCompactSummary"):
        return None
    content = msg.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        # A Codex approval/history wrapper is one synthetic user record even
        # when the quoted delta arrives in a later content block.  Skipping only
        # its prefix block would let an old quoted "delegate" recreate a live
        # task latch.
        eligible = [block.get("text", "") for block in content
                    if isinstance(block, dict)
                    and block.get("type") in {"text", "input_text", "output_text"}
                    and isinstance(block.get("text"), str)]
        if role in {"user", "human"} and eligible and eligible[0].lstrip().startswith(
            SYNTHETIC_CODEX_USER_PREFIXES
        ):
            return None
        chunks = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") not in {"text", "input_text", "output_text"}:
                continue
            value = block.get("text", "")
            if not isinstance(value, str):
                continue
            if role in {"user", "human"} and value.lstrip().startswith(
                SYNTHETIC_CODEX_USER_PREFIXES
            ):
                continue
            chunks.append(value)
        text = "\n".join(chunks)
    else:
        return None
    if not text or text.lstrip().startswith((
        "<system-reminder>", "<task-notification>", "[SYSTEM NOTIFICATION",
        "<local-command", "<command-name>", "Caveat:",
    )):
        return None
    return text


def tool_names(rec: dict) -> list[str]:
    return [name for name, _ in tool_calls(rec)]


def tool_calls(rec: dict) -> list[tuple[str, dict]]:
    """(name, input) pairs for one record -- tool_names' data plus the call
    input, which the sweep-vs-noise counting refinement needs to tell a real
    read sweep apart from a trivial command or a self-referential re-read."""
    payload = rec.get("payload")
    if isinstance(payload, dict):
        if payload.get("type") == "custom_tool_call":
            name = payload.get("name", "")
            inp = payload.get("input") or payload.get("arguments") or {}
            if not isinstance(inp, dict):
                inp = {}
            return [(f"functions.{name}", inp)] if isinstance(name, str) else []
        if payload.get("type") == "function_call":
            name, namespace = payload.get("name", ""), payload.get("namespace")
            inp = payload.get("arguments") or payload.get("input") or {}
            if not isinstance(inp, dict):
                inp = {}
            if isinstance(name, str):
                return [(f"{namespace}.{name}" if namespace else name, inp)]
            return []
    msg = rec.get("message") or rec
    content = msg.get("content")
    if not isinstance(content, list):
        return []
    out = []
    for b in content:
        if isinstance(b, dict) and b.get("type") == "tool_use":
            inp = b.get("input")
            out.append((b.get("name", ""), inp if isinstance(inp, dict) else {}))
    return out


def is_mechanical(tool: str) -> bool:
    """Classify repeatable CARR sweep work across both runtime spellings."""
    return tool in MECHANICAL or tool.startswith(("mcp__carr__", "mcp__carr_records__"))


WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "apply_patch", "functions.apply_patch"}


def written_path(tool: str, tool_input: dict) -> str | None:
    """The realpath a Write/Edit/apply_patch call targets, or None."""
    if tool not in WRITE_TOOLS:
        return None
    path = tool_input.get("file_path") or tool_input.get("path")
    if not isinstance(path, str) or not path:
        return None
    try:
        return os.path.realpath(path)
    except Exception:
        return path


def reads_own_recent_write(tool: str, tool_input: dict, written: set[str]) -> bool:
    """A Read of a path this same turn already wrote is a self-check, not a
    sweep across files the session doesn't yet know the contents of."""
    if tool != "Read":
        return False
    path = tool_input.get("file_path") or tool_input.get("path")
    if not isinstance(path, str) or not path:
        return False
    try:
        real = os.path.realpath(path)
    except Exception:
        real = path
    return real in written


BROAD_BASH = re.compile(
    r"\bgrep\b[^\n]*-\w*[rR]\w*(?:\s|$)"     # grep -r / -R (recursive)
    r"|\b(?:rg|ag|fd)\b"                      # ripgrep / silver searcher / fd
    r"|\bfind\s"                              # find over a path
    r"|\bls\s+-\w*R\w*\b",                    # ls -R
    re.I,
)
BASH_LIKE_TOOLS = {"Bash", "functions.exec"}


def is_broad(name: str, inp: dict, seen_read_files: set[str],
             written: set[str]) -> bool:
    """Is THIS call, on its own, sweep work -- independent of turn history?

    Mutates `seen_read_files` for Read calls so repeated calls in the same
    scan share one running notion of "distinct files seen so far this turn".
    Unchanged from the enforcing version of this gate: the classification
    still matters for telemetry even though nothing is denied on it anymore.
    """
    if name in ("Grep", "Glob", "WebSearch"):
        return True
    if name in BASH_LIKE_TOOLS:
        command = inp.get("command") or inp.get("cmd")
        if not isinstance(command, str):
            return False
        return bool(BROAD_BASH.search(command))
    if name == "Read":
        if reads_own_recent_write(name, inp, written):
            return False
        path = inp.get("file_path") or inp.get("path")
        if not isinstance(path, str) or not path:
            return False
        try:
            real = os.path.realpath(path)
        except Exception:
            real = path
        if real in seen_read_files:
            return False  # a re-read of a file already seen this turn
        first_file = not seen_read_files
        seen_read_files.add(real)
        return not first_file  # the FIRST distinct file this turn is targeted
    # DB queries (mcp__carr__/mcp__carr_records__), WebFetch (one URL),
    # apply_patch, and anything else: single-purpose, never broad.
    return False


def is_agent_tool(tool: str) -> bool:
    return tool in AGENT_TOOLS or tool.endswith(".spawn_agent")


def is_subagent_payload(payload: dict) -> bool:
    """Codex supplies agent_type for child tool calls; main calls omit it."""
    kind = str(payload.get("agent_type") or payload.get("agentType") or "").lower()
    return kind in {"subagent", "sub_agent", "child"}


def is_subagent_env() -> bool:
    """A leaf worker cannot delegate further -- it IS the delegate, and it is
    never tracked for delegation-compliance telemetry either, the same
    exemption this gate always gave it when it could still deny."""
    return os.environ.get("CLAUDE_CODE_CHILD_SESSION") == "1"


def is_carr_mcp_tool(tool: str) -> bool:
    """CARR record calls remain in scope even when a desktop task has no cwd."""
    return tool.startswith(("mcp__carr__", "mcp__carr_records__"))


def task_id_for(session_id: str, record_index: int, instruction: str) -> str:
    """Stable opaque id.  Neither the model nor later transcript edits choose it."""
    material = f"{session_id}\0{record_index}\0{instruction}".encode("utf-8")
    return "dg-" + hashlib.sha256(material).hexdigest()[:16]


def read_state_unlocked() -> dict:
    try:
        with open(STATE) as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            data.setdefault("version", 1)
            data.setdefault("tasks", {})
            data.setdefault("sessions", {})
            data.setdefault("telemetry", {})
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"version": 1, "tasks": {}, "sessions": {}, "telemetry": {}}


def write_state_unlocked(data: dict) -> None:
    """Atomic replacement while the adjacent advisory lock is held."""
    directory = os.path.dirname(STATE)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".delegation-gate-", dir=directory)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, STATE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked_state():
    """Serialize state transitions across parallel hooks and main sessions."""
    directory = os.path.dirname(STATE)
    os.makedirs(directory, exist_ok=True)
    lock_path = STATE + ".lock"
    with open(lock_path, "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        data = read_state_unlocked()
        try:
            yield data
        finally:
            write_state_unlocked(data)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def latest_human(recs: list[dict]) -> tuple[int | None, str]:
    for i in range(len(recs) - 1, -1, -1):
        text = text_blocks(recs[i], ("user", "human"))
        if text is not None:
            return i, text
    return None, ""


def latest_delegate(recs: list[dict]) -> tuple[int | None, str]:
    """Return a delegation not superseded by a later (or same-message) revoke.

    This matters when the durable state file is missing or recreated.  A later
    ordinary user turn must not resurrect an old delegation merely because the
    binding that would otherwise remember its revocation no longer exists.
    """
    found: tuple[int | None, str] = (None, "")
    last_revoke = -1
    for i, rec in enumerate(recs):
        human = text_blocks(rec, ("user", "human"))
        # Revocation wins even when the same message also contains "delegate".
        if human and REVOKE.search(human):
            last_revoke = i
        elif human and DELEGATE.search(human):
            found = (i, human)
    return found if found[0] is not None and found[0] > last_revoke else (None, "")


def has_exact_completion(recs: list[dict], task_id: str) -> bool:
    marker = complete_line(task_id)
    return any(
        (text := text_blocks(rec, ("assistant",))) and marker.search(text)
        for rec in recs
    )


def bind_task(data: dict, task_id: str, session_id: str) -> None:
    """A task has exactly one bound main session at a time."""
    task = data["tasks"][task_id]
    old_session = task.get("bound_session")
    if old_session:
        data["sessions"].pop(old_session, None)
    for known_session, known_task in list(data["sessions"].items()):
        if known_task == task_id:
            data["sessions"].pop(known_session, None)
    task["bound_session"] = session_id
    data["sessions"][session_id] = task_id


def release_task(data: dict, task_id: str, status: str) -> None:
    task = data["tasks"].get(task_id)
    if not task:
        return
    bound = task.get("bound_session")
    if bound:
        data["sessions"].pop(bound, None)
    task["bound_session"] = None
    task["status"] = status
    task["ended_at"] = now()


def sticky_task(session_id: str, recs: list[dict]) -> str | None:
    """Return this session's active task, creating/binding it only by valid rails.

    Unchanged from the enforcing version: this is the latch state a session's
    telemetry summary reports, not a decision that blocks anything by itself.
    """
    if not session_id:
        return None
    _, latest = latest_human(recs)
    with locked_state() as data:
        bound_id = data["sessions"].get(session_id)
        bound = data["tasks"].get(bound_id) if bound_id else None
        if bound and bound.get("bound_session") != session_id:
            data["sessions"].pop(session_id, None)
            bound_id, bound = None, None

        if bound and bound.get("status") == "active":
            if REVOKE.search(latest):
                # A revocation releases this session's task only, never another
                # session's task in the same ledger.
                release_task(data, bound_id, "revoked")
                return None
            if has_exact_completion(recs, bound_id):
                release_task(data, bound_id, "completed")
                return None
            return bound_id

        # A new session cannot inherit a task from a broad "delegate" phrase.
        # It may bind only by the exact visible resume marker and only to an
        # active task already known to this state ledger.
        resume = RESUME_LINE.search(latest)
        if resume:
            candidate = resume.group(1)
            task = data["tasks"].get(candidate)
            if task and task.get("status") == "active":
                bind_task(data, candidate, session_id)
                return candidate
            return None

        # Same-message revoke beats delegation and creates no replacement task.
        if REVOKE.search(latest):
            return None
        index, instruction = latest_delegate(recs)
        if index is None:
            return None
        task_id = task_id_for(session_id, index, instruction)
        task = data["tasks"].get(task_id)
        if not task:
            data["tasks"][task_id] = {
                "created_at": now(),
                "origin_session": session_id,
                "status": "active",
                "bound_session": None,
            }
        bind_task(data, task_id, session_id)
        return task_id


def declared_on_the_call(payload: dict) -> bool:
    """A declaration carried by the tool call itself is visible immediately,
    with no dependence on the transcript having flushed the assistant text
    block first -- still worth checking even though nothing is denied on its
    absence anymore, so telemetry does not misclassify a declared executor
    as an under-delegation moment."""
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(tool_input, dict):
        return False
    for key in ("description", "command"):
        value = tool_input.get(key)
        if isinstance(value, str) and EXECUTOR.search(value):
            return True
    return False


def _default_bucket() -> dict:
    return {
        "started_at": now(),
        "last_seen": now(),
        "cwd": None,
        "mechanical_calls": 0,
        "broad_calls": 0,
        "broad_calls_while_latched": 0,
        "would_have_flagged": 0,
        "flag_classes": {},
        "task_ids": [],
    }


def record_activity(session_id: str, cwd: str | None, task_id: str | None,
                     broad: bool, flagged: bool, flag_class: str | None) -> None:
    """One locked update per PreToolUse invocation that reaches classification.

    This is the whole of what replaces the old deny()/audit() call: instead of
    writing a per-call row to a log that grew to 35k rows in 18 days, it bumps
    a handful of counters in this session's bucket inside the existing state
    ledger. ops/delegation-telemetry-report.py and the Stop-time summary below
    are the only readers.
    """
    if not session_id:
        return
    with locked_state() as data:
        telemetry = data.setdefault("telemetry", {})
        bucket = telemetry.setdefault(session_id, _default_bucket())
        bucket["last_seen"] = now()
        if cwd:
            bucket["cwd"] = cwd
        bucket["mechanical_calls"] = bucket.get("mechanical_calls", 0) + 1
        if broad:
            bucket["broad_calls"] = bucket.get("broad_calls", 0) + 1
            if task_id:
                bucket["broad_calls_while_latched"] = (
                    bucket.get("broad_calls_while_latched", 0) + 1
                )
        if task_id:
            ids = set(bucket.get("task_ids") or [])
            ids.add(task_id)
            bucket["task_ids"] = sorted(ids)
        if flagged:
            bucket["would_have_flagged"] = bucket.get("would_have_flagged", 0) + 1
            if flag_class:
                classes = bucket.setdefault("flag_classes", {})
                classes[flag_class] = classes.get(flag_class, 0) + 1


def write_ledger_row(row: dict) -> None:
    try:
        os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
        with open(LEDGER, "a") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:
        pass


def stop_summary_message(row: dict) -> str:
    return (
        "DELEGATION TELEMETRY — this session's mechanical-call pattern reached "
        f"{row['would_have_flagged']} moment(s) where the retired denial rule "
        f"would have fired (threshold {MATERIAL_THRESHOLD}), out of "
        f"{row['mechanical_calls']} mechanical call(s) and {row['broad_calls']} "
        "broad-search call(s) this session"
        + (f", {row['broad_calls_while_latched']} of them while a delegation "
           "latch was active" if row['broad_calls_while_latched'] else "")
        + ". This no longer blocks anything — the gate only observes now — but "
        "the goal it was built to serve is still real: route mechanical sweep "
        "work to the cheapest qualified seat (a Terra peer counts) when a "
        "delegation latch is active, or a session that is plainly grinding "
        "through a codebase inline. See `./.venv/bin/python "
        "ops/delegation-telemetry-report.py` for the full per-session picture."
    )


def command_precheck(payload: dict) -> None:
    """Warn, before a shell command runs, when a fact on disk says it will fail.

    A SECOND CONCERN IN THIS FILE, DELIBERATELY AND CHEAPLY. The natural home is
    a hook of its own, but a file under hooks/ with a shebang is a new sealed
    ingress and admitting one costs a mutation-registry successor: a generated
    registry, a production migration, a fixture rebuilt from origin/main, and a
    disposable PostgreSQL round trip for the catalog digest. This gate already
    runs PreToolUse on every Bash call, already never denies, and already fails
    open, so hosting the dispatch here costs a re-digest of one row instead.
    Every decision lives in ops/command_precheck.py where it is tested; this is
    eleven lines of plumbing. If someone later wants the separation, lifting it
    out is a successor and a deletion, not a rewrite.

    IT CANNOT AFFECT THIS GATE. It runs before the delegation logic, catches
    everything, and returns None on any failure — no credential, no service, bad
    payload, missing library. The delegation path below is untouched either way.
    """
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "command_precheck",
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "ops", "command_precheck.py"))
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        note = module.advisory(payload)
        if note:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "additionalContext": note}}))
    except Exception:
        return


def handle_pretooluse(payload: dict) -> int:
    command_precheck(payload)
    if is_subagent_env():
        return 0

    tool = payload.get("tool_name") or payload.get("toolName") or ""
    if not is_mechanical(tool):
        return 0
    if not in_carr_scope(payload.get("cwd") or "") and not is_carr_mcp_tool(tool):
        return 0
    if is_subagent_payload(payload):
        return 0

    path = payload.get("transcript_path") or payload.get("transcriptPath") or ""
    if not path or not os.path.exists(path):
        return 0
    if f"{os.sep}subagents{os.sep}" in os.path.realpath(path):
        return 0

    recs = records(path)
    last_human_idx, last_human = latest_human(recs)
    if last_human_idx is None:
        return 0

    window = recs[last_human_idx + 1:]
    calls = [call for rec in window for call in tool_calls(rec)]
    used = [name for name, _ in calls]
    session_id = str(payload.get("session_id") or payload.get("sessionId") or "")
    cwd = payload.get("cwd") or ""

    # Persist/reconcile task authority first, exactly as the enforcing version
    # did -- the latch state is part of what telemetry reports, independent of
    # anything below ever being classified as under-delegated.
    task_id = sticky_task(session_id, recs)

    if any(is_agent_tool(name) for name in used):
        # A delegation already happened this turn; the current call is not a
        # sweep continuation. Still a real mechanical call worth counting.
        record_activity(session_id, cwd, task_id, broad=False, flagged=False,
                         flag_class=None)
        return 0

    written: set[str] = set()
    seen_read_files: set[str] = set()
    broad_count = 0
    for name, inp in calls:
        if is_broad(name, inp, seen_read_files, written):
            broad_count += 1
        target = written_path(name, inp)
        if target:
            written.add(target)

    current_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(current_input, dict):
        current_input = {}
    current_is_broad = is_broad(tool, current_input, seen_read_files, written)

    if not current_is_broad:
        record_activity(session_id, cwd, task_id, broad=False, flagged=False,
                         flag_class=None)
        return 0

    threshold = 2 if task_id else 3
    total_broad = broad_count + 1
    if total_broad < threshold:
        record_activity(session_id, cwd, task_id, broad=True, flagged=False,
                         flag_class=None)
        return 0

    if task_id:
        record_activity(session_id, cwd, task_id, broad=True, flagged=True,
                         flag_class="sticky_latch")
        return 0

    assistant_text = "\n".join(
        text for rec in window
        if (text := text_blocks(rec, ("assistant",)))
    )
    executor_declared = bool(EXECUTOR.search(assistant_text)) or declared_on_the_call(payload)
    if executor_declared:
        record_activity(session_id, cwd, task_id, broad=True, flagged=False,
                         flag_class=None)
        return 0

    record_activity(session_id, cwd, task_id, broad=True, flagged=True,
                     flag_class="second_mechanical_call")
    return 0


def handle_stop(payload: dict) -> int:
    session_id = str(payload.get("session_id") or payload.get("sessionId") or "")
    if not session_id:
        return 0

    with locked_state() as data:
        telemetry = data.setdefault("telemetry", {})
        bucket = telemetry.pop(session_id, None)
        bound_task_id = data["sessions"].get(session_id)
        task = data["tasks"].get(bound_task_id) if bound_task_id else None
        latch_active = bool(task and task.get("status") == "active"
                             and task.get("bound_session") == session_id)

    if not bucket:
        # This session never made a mechanical call the PreToolUse half
        # tracks -- nothing to summarise, and no ledger noise for it.
        return 0

    row = {
        "ts": now(),
        "session": session_id,
        "mechanical_calls": bucket.get("mechanical_calls", 0),
        "broad_calls": bucket.get("broad_calls", 0),
        "broad_calls_while_latched": bucket.get("broad_calls_while_latched", 0),
        "would_have_flagged": bucket.get("would_have_flagged", 0),
        "flag_classes": bucket.get("flag_classes", {}),
        "task_ids": bucket.get("task_ids", []),
        "latch_active_at_end": latch_active,
        "materially_under_delegated": bucket.get("would_have_flagged", 0) >= MATERIAL_THRESHOLD,
        "cwd": bucket.get("cwd"),
        "started_at": bucket.get("started_at"),
    }
    write_ledger_row(row)

    if row["materially_under_delegated"]:
        return announce(stop_summary_message(row), event="Stop")
    return 0


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    event = payload.get("hook_event_name") or payload.get("hookEventName")
    try:
        if event == "Stop":
            return handle_stop(payload)
        return handle_pretooluse(payload)
    except Exception:
        return 0  # conduct/cost gate fails open; it must never wedge the session


if __name__ == "__main__":
    sys.exit(main())
