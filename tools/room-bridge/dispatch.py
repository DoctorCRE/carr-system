#!/usr/bin/env python3
"""One task, one desk, no model in the middle.

Hermes runs this. It reasons about WHICH seat a task belongs in; this file
does not reason at all, which is the point — a router that needs its own model
to route costs the tokens the routing was supposed to save.

    dispatch.py send claude-desk "reconcile the loop board"
    dispatch.py send codex-desk "rename this variable across the package" --family luna --effort low
    dispatch.py send codex-desk "repair the build" --family sol --effort high --fresh --checkout new:repair-build
    dispatch.py send codex-desk "Own PR: #1667\nWrites: tools/room-bridge/*.py\nrepair dispatch" --effort high
    dispatch.py desks
    dispatch.py register claude-desk --socket /tmp/cc-socks/claude-desk.sock
    dispatch.py register codex-desk --family sol --effort high --cwd ~/carr-system
    dispatch.py where            # how to find THIS session's socket

Codex desks preserve their thread context. The registry names a default family,
never a model version. Each send resolves Sol or Luna from the current local
Codex catalog, prints the executor slug and effort, and records them in its row.

EVERY DISPATCH LEAVES A LINE in the results file, NDJSON, one object per
dispatch. That file is how Hermes learns what happened without holding a
connection open — a claude-session answers in its own window on its own time,
so the line records that the turn was DELIVERED, not what the session decided.
A headless Codex run is synchronous, so its line carries the actual result.
"""

from __future__ import annotations

from contextlib import nullcontext
import argparse
import errno
import json
import os
import re
import shlex
import signal
import select
import subprocess
import sys
import time
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import codex_models  # noqa: E402
import codex_checkout  # noqa: E402
from ops.git_env import scrubbed_env  # noqa: E402
import write_ownership  # noqa: E402
import desks  # noqa: E402
from desks import DeskError, Registry  # noqa: E402
import claude_wire as inject_mod  # noqa: E402  — the Idea 78 wire, see the module
import claude_desktop_wire  # noqa: E402 — background supervisor + supported /desktop
import claude_remote_wire
import codex_wire  # noqa: E402  — Codex worked out this protocol, see the module
import codex_ipc  # noqa: E402  — a thread Codex Desktop holds open, see the module
import grok_wire  # noqa: E402 — authenticated public retrieval, provider metadata checked
import flash_wire  # noqa: E402  — the local Flash model as a desk, see the module
import verb_io  # noqa: E402 — the ONE path to the record layer; see that module

TOOLS_ROOT = HERE.parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))
import credential_env  # noqa: E402 — shared long-lived-token loader
import flashlib
import seat_gate  # noqa: E402 — offline host / failed seat refusal at send time

DEFAULT_RESULTS = Path(
    os.environ.get(
        "CARR_HERMES_RESULTS",
        Path.home() / ".config" / "carr" / "hermes-dispatch-results.jsonl",
    )
)

CODEX_TIMEOUT_S = float(os.environ.get("CARR_HERMES_CODEX_TIMEOUT", "5400"))

# Codex prints this on STDOUT and still exits 0, so the exit code lies.
QUOTA_HINT = re.compile(r"hit your usage limit", re.I)
RETRY_AT = re.compile(r"try again at ([0-9:]+ ?[AP]M)", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _record(results_path: Path, row: dict) -> None:
    write_ownership.record(results_path, row)


def _to_claude(entry: dict, task: str, msg_id: str) -> dict:
    """Deliver one peer turn to a live labeled session."""
    desks.dispatched_permission_mode(entry.get("permission_mode"))
    payload = {
        "type": "user",
        "message": {"role": "user", "content": desks.desk_prompt(task)},
        "origin": {"kind": "peer", "from": f"hermes:{entry['name']}", "msg_id": msg_id},
    }
    conn = inject_mod.inject_keepalive(entry["socket"], payload)
    try:
        # `delivered` is deliberately narrow. The socket binds before the
        # session finishes its first-run prompts, so a turn sent to a desk
        # still sitting on "use my browser?" is accepted and then queued
        # behind a modal nobody is watching. Clear a new desk's prompts once
        # before dispatching to it; `dispatch.py where` says so too.
        return {"status": "delivered",
                "detail": "the desk's socket accepted the turn; it answers in "
                          "its own window, and this file does not carry that back"}
    finally:
        conn.close()


def _to_claude_desktop(entry: dict, task: str) -> dict:
    """Create one durable background session; later bridge cycles observe it."""
    try:
        return claude_desktop_wire.launch_background(entry, task)
    except claude_desktop_wire.ClaudeDesktopError as exc:
        return {"status": "failed", "detail": exc.code, "error": str(exc)}


def _codex_events(stdout: str) -> list[dict]:
    """Codex prints one JSON object per line with --json. Ignore the rest."""
    out = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _codex_timeout_activity(output, thread, env):
    """Describe a bounded rollout tail without copying private session content."""
    if isinstance(output, bytes):
        output = output.decode('utf-8', errors='replace')
    events = _codex_events(output or '')
    started = next((e for e in events if e.get('type') == 'thread.started'), {})
    thread_id = started.get('thread_id') or thread
    activity = {'source': 'executor_output', 'last_event_type':
                events[-1].get('type') if events else 'unavailable', 'hook_failures': 0}
    if not isinstance(thread_id, str) or not re.fullmatch(r'[0-9a-fA-F-]{36}', thread_id):
        return thread_id, activity
    home = Path((env or os.environ).get('CODEX_HOME') or Path.home() / '.codex')
    try:
        path = next((home / 'sessions').glob(f'*/*/*/rollout-*-{thread_id}.jsonl'), None)
        if path is None:
            return thread_id, activity
        with path.open('rb') as rollout:
            rollout.seek(0, os.SEEK_END)
            offset = max(0, rollout.tell() - 2 * 1024 * 1024)
            rollout.seek(offset)
            if offset:
                rollout.readline()
            tail = rollout.read(2 * 1024 * 1024).decode('utf-8', errors='replace')
    except OSError:
        return thread_id, activity
    rows = [row for row in _codex_events(tail) if isinstance(row.get('payload'), dict)]
    if not rows:
        return thread_id, activity
    last = rows[-1]
    payload = last.get('payload') or {}
    activity = {'source': 'rollout', 'last_event_type': '.'.join(filter(None, (
        last.get('type'), payload.get('type')))), 'hook_failures': 0,
        'tail_truncated': offset > 0}
    for row in rows:
        message = row.get('payload') or {}
        if message.get('type') != 'message' or message.get('role') != 'user':
            continue
        content = message.get('content')
        if not isinstance(content, list):
            continue
        texts = [part['text'] for part in content
                 if isinstance(part, dict) and isinstance(part.get('text'), str)]
        if any(text.lstrip().startswith('<hook_prompt ') and
               re.search(r"\[errno \d+\]|can't open file|\b(?:failed|error)\b", text, re.I)
               for text in texts):
            activity['hook_failures'] += 1
    return thread_id, activity


def _run_codex_process(argv, env, timeout, on_executor, stream_output, **options):
    """Track a dedicated process group, including children surviving a CLI exit."""
    proc = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, **options,
                            start_new_session=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT if stream_output else subprocess.PIPE,
                            text=True, pass_fds=(() if write_ownership.CHECKOUT_FD is None
                                                else (write_ownership.CHECKOUT_FD,)))
    identity = {**write_ownership.process_owner(), 'pid': proc.pid, 'pgid': proc.pid,
                'start_time': write_ownership.process_start(proc.pid), 'kind': 'process_group'}
    chunks = []
    reader = None
    try:
        if on_executor:
            on_executor(identity)
        if stream_output:
            def relay():
                with proc.stdout:
                    for line in proc.stdout:
                        chunks.append(line)
                        print(line, end='', flush=True)
            reader = threading.Thread(target=relay, daemon=True)
            reader.start()
            proc.wait(timeout=timeout)
            reader.join(timeout=timeout)
            if reader.is_alive():
                raise subprocess.TimeoutExpired(argv, timeout)
            stdout, stderr = ''.join(chunks), ''
        else:
            stdout, stderr = proc.communicate(timeout=timeout)
    except BaseException as exc:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            # An unkillable group retains the claim; do not hang on its pipes.
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        if isinstance(exc, subprocess.TimeoutExpired):
            if reader is not None:
                reader.join(timeout=2)
                exc.output = ''.join(chunks)
            exc.termination_confirmed = write_ownership.process_terminated(identity, group=True)
        raise
    finally:
        if reader is None or not reader.is_alive():
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()
    result = subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
    result.termination_confirmed = write_ownership.process_terminated(identity, group=True)
    return result


def _to_codex(
    entry: dict,
    task: str,
    env: dict | None,
    fresh: bool = False,
    config_overrides: tuple[str, ...] = (),
    live_desktop: bool = False,
    stream_output: bool = False,
    timeout_s: float | None = None,
    on_executor=None,
    claim_id: str | None = None,
) -> dict:
    """Send one task to a standing Codex thread, resuming it when there is one.

    The thread is what makes this an equal seat rather than a shot: Codex
    keeps its own context, so a desk that started a new thread every task
    would throw away everything it had been told. `codex exec resume <id>`
    carries it, and --json reports the thread id in its first event.

    timeout_s lets a caller with its own authority window (the Engineering
    controller's 930 s lease) pin a shorter limit than the router default.
    """
    limit_s = CODEX_TIMEOUT_S if timeout_s is None else float(timeout_s)
    task = desks.desk_prompt(task)
    thread = None if fresh else entry.get("thread_id")
    # A THREAD CODEX DESKTOP HOLDS OPEN CANNOT BE RESUMED FROM HERE. Found live
    # 2026-09-27: the orchestrator's Desktop thread refused `codex exec resume`
    # with "thread ... already has an active writer", so a turn addressed to it
    # never arrived. When the Desktop router names an owner, the turn is
    # started inside that owner instead; the session answers in its own window,
    # the same contract as a live Claude desk. No owner (Desktop closed, or the
    # thread not open there) keeps the durable resume path below.
    #
    # OPT-IN, and its own status. Only the conversational bridge asks for this
    # (bridge.deliver passes live_desktop=True). A caller that waits for a result
    # in the desk log, like the queue executor, would read a plain "delivered" as
    # "wait", time out, and dispatch again, starting a fresh turn in the same
    # Desktop thread on every retry (PR #1345 review). "delivered_live" says the
    # answer arrives in the session's own window and nowhere a caller can wait on.
    if live_desktop and thread and codex_ipc.thread_owner(thread) is not None:
        marker = f'Room write owner: {claim_id}' if claim_id else None
        if marker:
            task += '\n\n' + marker
        if on_executor:
            on_executor({'kind': 'codex_desktop', 'thread_id': thread, 'marker': marker})
        live = codex_ipc.start_turn(thread, task, approval_policy="never",
                                    model=entry["model"], effort=entry["effort"])
        if live.get("status") != "not_live":
            status = "delivered_live" if live.get("status") == "delivered" else live.get("status")
            return {"resumed": True, **live, "status": status, "thread_id": thread,
                    "termination_confirmed": False}
        if on_executor:
            on_executor({'kind': 'no_launch', 'thread_id': thread, 'marker': marker,
                         'reason': 'desktop_not_live'})
    with tempfile.TemporaryDirectory(prefix="hermes-codex-") as tmp:
        last = Path(tmp) / "last-message.txt"
        argv = ["codex", "exec"]
        if thread:
            argv.append("resume")
        argv += [
            # Without this, Codex runs its enabled hooks only if their trust is
            # already persisted — so a dispatched run silently skips hooks that
            # a hand-run session would fire, and the two seats stop behaving the
            # same way. bin/council-lib.sh and pipelines/run_codex_review.py
            # carry it for the same reason, and ops/codex-hook-smoke-selftest.py
            # fails any new call site that leaves it out. This was a new site
            # that left it out.
            "--dangerously-bypass-hook-trust",
            "--json",
            "-m", entry["model"],
            # Verified from CLI help on 2026-08-24: `codex exec --help` and
            # `codex exec resume --help` both list `-c <config>`, so this
            # delegation-specific effort is passed on both fresh and resumed paths.
            "-c", f"model_reasoning_effort={entry['effort']}",
            "-o", str(last),
        ]
        # Callers may carry a reviewed, desk-specific Codex posture without
        # teaching the shared registry or CLI how to widen every desk. The
        # Engineering adapter is the only production caller and supplies an
        # exact constant tuple; ordinary dispatches keep this empty and stay
        # offline. -c is valid on both fresh and resumed exec paths.
        for override in config_overrides:
            if not isinstance(override, str) or not override.strip():
                raise DeskError(
                    "bad_codex_config",
                    "Codex config overrides must be non-empty strings")
            argv += ["-c", override]
        # Bind both starts and resumes, after overrides, so an old thread or
        # caller config cannot restore human approval cards.
        argv += ["-c", 'approval_policy="never"']
        # `codex exec resume` does not accept -C/-s/--add-dir at all — a
        # resumed session already carries the cwd, sandbox and extra dirs it
        # was FIRST started with, and passing them again is a hard CLI parse
        # error ("unexpected argument '-C' found"), not a no-op override.
        # Caught live 2026-08-22 dispatching a second task to an already-
        # resumed room-bridge desk: the fresh-thread path (below) had always
        # been exercised by the unit suite's stand-in `codex` script, which
        # does not validate real argument parsing, so this never surfaced
        # until a genuine second call hit the real binary.
        if not thread:
            if entry.get('checkout_workspace'):
                argv.append('--skip-git-repo-check')
            argv += ["-C", entry.get("cwd") or str(Path.cwd())]
            if entry.get("sandbox"):
                argv += ["-s", entry["sandbox"]]
            for extra in entry.get("add_dirs") or []:
                argv += ["--add-dir", extra]
        if thread:
            argv.append(thread)
        argv.append(task)
        process_options = ({'cwd': entry['checkout_workspace']}
                           if not thread and entry.get('checkout_workspace') else {})

        try:
            # stdin=DEVNULL is load-bearing: `codex exec` reads stdin when it
            # is not a terminal and appends it to the prompt, so an inherited
            # pipe makes the run hang or swallow whatever the caller was fed.
            # It is the same reason every command in CLAUDE.md carries
            # `</dev/null`.
            if on_executor or stream_output:
                proc = _run_codex_process(argv, env or os.environ.copy(), limit_s,
                                          on_executor, stream_output, **process_options)
            else:
                proc = subprocess.run(
                    argv, env=env or os.environ.copy(), capture_output=True,
                    text=True, timeout=limit_s, stdin=subprocess.DEVNULL,
                    **process_options,
                )
        except FileNotFoundError:
            return {"status": "failed", "detail": "codex is not on PATH", 'termination_confirmed': True}
        except subprocess.TimeoutExpired as exc:
            thread_id, activity = _codex_timeout_activity(exc.output, thread, env)
            detail = (f"no answer in {limit_s:.0f}s; last event {activity['last_event_type']}; "
                      f"hook failures in {activity['source'].replace('_', ' ')} tail: "
                      f"{activity['hook_failures']}")
            return {"status": "timed_out", "detail": detail,
                    'thread_id': thread_id, 'resumed': bool(thread), 'last_activity': activity,
                    **({'termination_confirmed': exc.termination_confirmed}
                       if hasattr(exc, 'termination_confirmed') else {})}

        events = _codex_events(proc.stdout or "")
        started = next((e for e in events if e.get("type") == "thread.started"), None)
        thread_id = (started or {}).get("thread_id") or thread
        failure = next(
            (e for e in events if e.get("type") in ("error", "turn.failed")), None
        )
        result = last.read_text(encoding="utf-8").strip() if last.exists() else ""

        base = {"thread_id": thread_id, "resumed": bool(thread),
                **({'termination_confirmed': proc.termination_confirmed}
                   if hasattr(proc, 'termination_confirmed') else {})}

        # A seat that is out of credit is not a broken seat, and a router needs
        # to tell those apart: the first means send this task somewhere else
        # now, the second means stop sending anything here.
        if failure:
            msg = failure.get("message") or (failure.get("error") or {}).get("message", "")
            if QUOTA_HINT.search(msg):
                at = RETRY_AT.search(msg)
                return {**base, "status": "quota_exhausted", "detail": msg.strip(),
                        "retry_after": at.group(1) if at else None, "result": result}
            return {**base, "status": "failed", "detail": msg.strip()[-500:], "result": result}

        # Belt for the same signal arriving as prose. Codex prints the limit
        # BOTH as a --json event and as a plain line, and the plain line is
        # what a future version might keep if the event shape changes. Only
        # Codex's own plain lines count: a JSON event carries command output,
        # so a job that read a file holding this phrase (dispatch.py does) is
        # not out of credit, and a turn that finished with an answer never is.
        plain = [line for line in (proc.stdout or "").splitlines()
                 if not line.lstrip().startswith("{")]
        blob = "\n".join([proc.stderr or "", *plain])
        finished = proc.returncode == 0 and bool(result) and any(
            e.get("type") == "turn.completed" for e in events)
        if not finished and QUOTA_HINT.search(blob):
            at = RETRY_AT.search(blob)
            return {**base, "status": "quota_exhausted",
                    "detail": (QUOTA_HINT.search(blob) and
                               blob[blob.lower().index("hit your usage limit"):][:200].strip()),
                    "retry_after": at.group(1) if at else None, "result": result}

        if proc.returncode != 0 or not result:
            return {**base, "status": "failed",
                    "detail": (proc.stderr or proc.stdout or "").strip()[-500:],
                    "result": result}
        return {**base, "status": "completed", "result": result}


def _execute(request: dict, *, on_executor=None) -> dict:
    """Invoke one adapter; owned jobs reach this only inside the launch gate."""
    entry, task, msg_id = request['entry'], request['task'], request['msg_id']
    name = entry.get('name')
    env = request.get('env')
    fresh = request.get('fresh', False)
    cwd = request.get('cwd')
    retrieval = request.get('retrieval', False)
    live_desktop = request.get('live_desktop', False)
    network_access = entry.get('network_access', False)
    codex_timeout_s = request.get('codex_timeout_s')
    config_overrides = tuple(request.get('config_overrides', ()))
    stream_options: dict = {'stream_output': True} if request.get('stream_output') else {}
    def executor_started(identity):
        on_executor({**identity, **({'socket': entry['socket']}
                                    if identity.get('kind') in ('codex_turn', 'no_launch') and entry.get('socket') else {})})
    executor_options: dict = {'on_executor': executor_started} if on_executor else {}
    codex_options: dict = {**executor_options, 'claim_id': request.get('claim_id')} if on_executor else {}
    if entry["kind"] == "claude-session":
        if name == "flash":
            with flashlib.activity_scope():
                outcome = _to_claude(entry, task, msg_id)
        else:
            outcome = _to_claude(entry, task, msg_id)
    elif entry["kind"] == "claude-desktop":
        outcome = _to_claude_desktop(entry, task)
    elif entry["kind"] == "claude-remote":
        outcome = claude_remote_wire.run_task(entry, task, msg_id)
    elif entry["kind"] == "grok-cli":
        outcome = grok_wire.run_task(entry, task, **({"retrieval": True} if retrieval else {}))
    elif entry["kind"] == "flash-local":
        outcome = flash_wire.run_task(task)
    elif entry["kind"] == "codex-live":
        outcome = codex_wire.run_turn(
            entry["socket"], task,
            thread_id=None if fresh else entry.get("thread_id"),
            cwd=entry.get("cwd"), model=entry.get("model"), effort=entry["effort"],
            network_access=network_access,
            deadline_s=codex_timeout_s,
            **executor_options,
        )
    elif cwd:
        outcome = _to_codex(
            {**entry, "cwd": cwd}, task, env, fresh=True, config_overrides=config_overrides,
            timeout_s=codex_timeout_s, **stream_options,
            **codex_options,
        )
    else:
        outcome = _to_codex(
            entry, task, env, fresh=fresh, config_overrides=config_overrides,
            live_desktop=live_desktop, timeout_s=codex_timeout_s,
            **stream_options,
            **codex_options,
        )
    return outcome


def dispatch(
    name: str,
    task: str,
    registry: Registry | None = None,
    results_path: Path | None = None,
    env: dict | None = None,
    fresh: bool = False,
    config_overrides: tuple[str, ...] = (),
    cwd: str | None = None,
    live_desktop: bool = False,
    stream_output: bool = False,
    retrieval: bool = False,
    codex_timeout_s: float | None = None,
    family: str | None = None,
    effort: str | None = None,
    writes: list[str] | None = None,
    checkout: str | None = None,
) -> dict:
    """Send one task to one desk. Raises DeskError when the desk is not usable.

    `live_desktop` (codex-session desks only) lets a thread Codex Desktop holds
    open take the turn in its own window, returning status "delivered_live".
    Only a caller that expects no result back may set it; see _to_codex.

    `cwd` (codex-session desks only) runs this one task in that directory on a FRESH
    thread and leaves the desk's standing thread untouched: flash-run's escalation gives
    the Sol fixer desk a throwaway copy per task (2026-09-24)."""
    registry = registry or Registry()
    results_path = Path(results_path or DEFAULT_RESULTS)
    if name == "flash" and registry.entries().get(name, {}).get("kind") == "claude-session":
        try:
            flashlib.ensure_desk(lambda: desks.is_live(registry.entries()[name].get("socket", "")))
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            raise DeskError("desk_not_live", str(exc)) from exc
    entry = registry.resolve(name)
    network_access = entry.get('network_access', False)
    if network_access and not fresh:
        raise DeskError('network_access_requires_fresh_thread',
                        'network_access desks require --fresh for every dispatched turn')
    if checkout is not None and entry['kind'] not in codex_models.CODEX_KINDS:
        raise DeskError('unsupported_checkout', '--checkout requires a Codex desk')
    if entry["kind"] in codex_models.CODEX_KINDS:
        entry = {**entry, "family": codex_models.family_default(family or entry.get("family"), entry.get("model")),
                 "effort": desks._normalize_effort(entry["kind"], effort or entry.get("effort"))}
        entry["model"] = codex_models.resolve_model(entry["family"], env)
    elif family is not None or effort is not None:
        raise DeskError("unsupported_model_override", "job family and effort overrides require a Codex desk")
    if retrieval and entry["kind"] != "grok-cli":
        raise DeskError("unsupported_retrieval", "explicit source retrieval requires a Grok desk")
    if stream_output and entry["kind"] not in ("codex-session", "codex-exec"):
        raise DeskError("unsupported_stream", "stream output requires a headless Codex desk")
    original_task = task
    # The background wire validates the original task before adding its own
    # instruction. Prepending here would turn a blank task into valid work.
    if entry["kind"] != "claude-desktop":
        task = desks.desk_prompt(task)
    msg_id = str(uuid.uuid4())
    if entry["kind"] in ("claude-desktop", "claude-remote", "codex-session", "codex-live", "flash-local", "grok-cli"):
        if not entry.get("model") or not str(entry.get("model")).strip():
            raise DeskError(
                "unnamed_model_or_effort",
                "dispatch refused: a delegation names its specific model and reasoning "
                "effort (cheapest qualified, stated explicitly). Register a model or Codex family "
                "and a reasoning effort, or pass --family and --effort for this job.",
            )
        if not entry.get("effort") or not str(entry.get("effort")).strip():
            raise DeskError(
                "unnamed_model_or_effort",
                "dispatch refused: a delegation names its specific model and reasoning "
                "effort (cheapest qualified, stated explicitly). Register a model or Codex family "
                "and a reasoning effort, or pass --family and --effort for this job.",
            )

    base = {"msg_id": msg_id, "desk": name, "kind": entry["kind"],
            "task": original_task, "dispatched_at": _now(),
            **({key: entry[key] for key in ("family", "model", "effort")}
               if entry["kind"] in codex_models.CODEX_KINDS else {})}
    declared_writes = write_ownership.declaration(original_task, writes)
    if declared_writes and entry['kind'] not in codex_models.CODEX_KINDS:
        raise DeskError('unsupported_owned_adapter', 'writing dispatch requires a Codex adapter')

    if entry["kind"] in codex_models.CODEX_KINDS:
        print(f"executor: {entry['model']} / {entry['effort']} (family {entry['family']}, desk {name})",
              file=sys.stderr, flush=True)

    if checkout is not None:
        prepared = codex_checkout.prepare(checkout, cwd or entry.get('cwd') or str(Path.cwd()), env)
        env = scrubbed_env(env)
        base.update(prepared)
        task = codex_checkout.instruction(prepared) + task
        if fresh or cwd or not entry.get('thread_id'):
            entry = {**entry, **prepared, 'cwd': prepared['checkout_workspace']}
            if cwd:
                cwd = prepared['checkout_workspace']
    request = {'entry': entry, 'task': task, 'msg_id': msg_id, 'env': env,
               'fresh': fresh, 'cwd': cwd, 'retrieval': retrieval,
               'live_desktop': live_desktop, 'codex_timeout_s': codex_timeout_s,
               'config_overrides': config_overrides, 'stream_output': stream_output,
               'claim_id': msg_id if declared_writes else None}
    workspace = base.get('checkout_path') or cwd or entry.get('cwd') or str(Path.cwd())
    with write_ownership.checkout_writer(workspace) if declared_writes else nullcontext() as bind:
        outcome = _execute(request, on_executor=bind)
    if outcome.get('thread_id') and not network_access and (entry['kind'] == 'codex-live'
            or entry['kind'] in codex_models.CODEX_KINDS and not cwd):
        registry.remember_thread(name, outcome['thread_id'])

    row = {
        **base,
        **outcome,
        'status': outcome['status'],
        **({'ephemeral_thread': True, 'thread_resumable': False} if network_access else {}),
    }
    _record(results_path, row)
    return row


# WR-000119 — THE DESK'S OWN ACKNOWLEDGEMENT, AND ONLY ITS OWN.
#
# The stage is a MODULE CONSTANT and not a parameter. A desk observes exactly
# one thing first-hand: that a turn landed in a window, at a byte offset it can
# name. Whether the session then TOOK THE TURN UP is the session's own fact and
# it writes that itself, from inside its own turn -- so there is deliberately no
# way to reach `acknowledged` through this function, and a caller that wanted to
# send it would have to write a second one.
#
# The evidence is the desk name and the injection offset, both measured, never a
# guess that "it probably arrived".
DESK_ACK_STAGE = "received"


def acknowledge_received(dispatch_ref: str, *, desk: str, log_offset: int,
                          injected_at: str | None = None,
                          call_verb=verb_io._run_verb) -> dict:
    """Append this desk's `received` acknowledgement for one dispatch.

    ``verb_io._run_verb`` is reused rather than a second subprocess path being
    invented here: two paths to the record layer would be two places for the
    identity derivation to drift, and verb_io.py is outside this Work Request's
    authorized paths, so no public wrapper could be added to it.
    """
    if not dispatch_ref:
        raise DeskError("dispatch_ref_missing",
                        "an acknowledgement names the dispatch it acknowledges")
    evidence = f"desk {desk} log offset {int(log_offset)}"
    if injected_at:
        evidence = f"{evidence} injected at {injected_at}"
    return call_verb("acknowledge-dispatch", {
        "dispatch_ref": dispatch_ref,
        "stage": DESK_ACK_STAGE,
        "evidence": evidence,
    })


# ---------------------------------------------------------------------------
# putting a desk on the line
# ---------------------------------------------------------------------------

DESK_STATE = Path(
    os.environ.get("CARR_HERMES_DESK_STATE", Path.home() / ".config" / "carr" / "desks")
)
SOCK_DIR = Path(os.environ.get("CARR_HERMES_SOCK_DIR", "/tmp/cc-socks"))
BIND_TIMEOUT_S = float(os.environ.get("CARR_HERMES_BIND_TIMEOUT", "90"))


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True
    return True


def _read_pid(pid_file: Path) -> int | None:
    try:
        return int(pid_file.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _send_seed(fifo: Path, seed: str, pid: int, deadline: float) -> None:
    """Bound both FIFO open and backpressure by the startup deadline."""
    payload = (json.dumps({"type": "user", "message": {
        "role": "user", "content": desks.desk_prompt(seed)}}) + "\n").encode()
    fd = None
    try:
        while time.monotonic() < deadline:
            if not _alive(pid):
                raise DeskError("desk_failed_to_start", "the desk exited while accepting its seed")
            if fd is None:
                try:
                    fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
                except OSError as exc:
                    if exc.errno != errno.ENXIO:
                        raise
                    time.sleep(min(0.05, max(0, deadline - time.monotonic())))
                    continue
            try:
                written = os.write(fd, payload)
            except BlockingIOError:
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
                continue
            payload = payload[written:]
            if not payload:
                return
        raise DeskError("desk_failed_to_start", "the desk did not accept its seed within the startup deadline")
    except OSError as exc:
        raise DeskError("desk_failed_to_start", "the desk seed input became unavailable") from exc
    finally:
        if fd is not None:
            os.close(fd)


def desk_start(
    name: str,
    registry: Registry | None = None,
    state_dir: Path | None = None,
    sock_dir: Path | None = None,
    env: dict | None = None,
    seed: str | None = None,
    token_path: "Path | str | None" = None,
) -> dict:
    """Start a Claude session that STAYS, and register it under `name`.

    TWO THINGS KEEP A DESK STANDING, and leaving out either one produces a
    session that answers once and dies:

      * streaming input, so the session waits for more turns instead of
        printing an answer and exiting;
      * a stdin that never reaches end-of-file. The session is handed a FIFO
        it opens read-write and holds both ends of, which is what makes it
        un-EOF-able — a pipe from the launcher would close the moment this
        command returned, and the desk would go down with it.

    The first version of the live test in this package passed exactly once,
    by delivering its turn into the closing window of a session already on its
    way out. That is the failure this function exists to make impossible.
    """
    registry = registry or Registry()
    state_dir = Path(state_dir or DESK_STATE)
    sock_dir = Path(sock_dir or SOCK_DIR)

    if not desks.NAME_OK.match(name or ""):
        raise DeskError("bad_name", f"{name!r} is not a desk name")
    sock = sock_dir / f"{name}.sock"
    # a desk named 12345 would bind 12345.sock, which is indistinguishable
    # from an ordinary session's pid socket and refused everywhere else
    desks.refuse_pid_socket(str(sock))

    state_dir.mkdir(parents=True, exist_ok=True)
    sock_dir.mkdir(parents=True, exist_ok=True)
    pid_file = state_dir / f"{name}.pid"
    log = state_dir / f"{name}.log"
    fifo = state_dir / f"{name}.stdin"

    running = _read_pid(pid_file)
    if running and _alive(running) and desks.is_live(str(sock)):
        registry.register(name, "claude-session", socket=str(sock))
        return {"name": name, "socket": str(sock), "pid": running, "log": str(log),
                "already_running": True}

    for stale in (sock, fifo):
        try:
            os.unlink(stale)
        except FileNotFoundError:
            pass
    os.mkfifo(fifo, 0o600)

    # `exec 3<>fifo` opens BOTH ends in the session itself, so nothing outside
    # it has to stay alive to keep stdin open.
    shell = (
        f"exec 3<>{shlex.quote(str(fifo))}; "
        f"exec claude --messaging-socket-path {shlex.quote(str(sock))} "
        f"--permission-mode dontAsk "
        f"-p --input-format stream-json --output-format stream-json --verbose "
        f"<&3 >>{shlex.quote(str(log))} 2>&1"
    )
    # This is the ONE unattended launch of `claude -p` a room-bridge poll cycle
    # can make (see bridge.py's own header: launchd fires the cycle, no human
    # is present). The child gets the long-lived login merged into ITS OWN
    # env only — os.environ itself is never touched — so a keychain entry
    # that has expired between launchd wakes does not take this desk down.
    # Absent-safe: with no token configured, this is exactly the env the
    # caller already passed (or a plain copy of the current environment).
    child_env, warning = credential_env.claude_child_env(env or None, path=token_path)
    if warning:
        print(f"desk_start {name}: {warning}", flush=True)
    proc = subprocess.Popen(
        ["/bin/sh", "-c", shell],
        env=child_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,          # outlives the shell that started it
    )
    pid_file.write_text(f"{proc.pid}\n")

    deadline = time.monotonic() + BIND_TIMEOUT_S
    while time.monotonic() < deadline:
        if desks.is_live(str(sock)):
            break
        if not _alive(proc.pid):
            tail = log.read_text(errors="replace")[-800:] if log.exists() else ""
            raise DeskError("desk_failed_to_start",
                            f"the session exited before binding {sock}. Log tail:\n{tail}")
        time.sleep(0.2)
    else:
        raise DeskError("desk_failed_to_start",
                        f"nothing bound {sock} within {BIND_TIMEOUT_S:.0f}s")

    if proc.poll() is not None or not _alive(proc.pid):
        raise DeskError("desk_failed_to_start", "the session exited after binding its socket")
    # An unseeded desk has no pending turn. The instruction rides on its
    # first dispatched task, avoiding an unrelated bootstrap result racing
    # with bridge.deliver's first task log offset.
    if seed:
        _send_seed(fifo, seed, proc.pid, deadline)
    registry.register(name, "claude-session", socket=str(sock))

    return {"name": name, "socket": str(sock), "pid": proc.pid, "log": str(log),
            "already_running": False}


def desk_stop(name: str, state_dir: Path | None = None,
              sock_dir: Path | None = None) -> dict:
    """Take a desk down. Safe to run on a desk that is already down."""
    state_dir = Path(state_dir or DESK_STATE)
    sock_dir = Path(sock_dir or SOCK_DIR)
    pid_file = state_dir / f"{name}.pid"
    pid = _read_pid(pid_file)
    stopped = False
    if pid and _alive(pid):
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        stopped = True
    for gone in (sock_dir / f"{name}.sock", state_dir / f"{name}.stdin", pid_file):
        try:
            os.unlink(gone)
        except FileNotFoundError:
            pass
    return {"name": name, "stopped": stopped, "pid": pid}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_where() -> int:
    sock = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET")
    print("THIS session's socket:", sock or "<unset — not a messaging-enabled session>")
    if sock and desks.PID_SOCKET.match(os.path.basename(sock)):
        print()
        print("That is a pid socket, so it cannot be registered as a desk —")
        print("it names a process that happened to start, and it changes every")
        print("time. A desk is a session started on purpose, under a name:")
        print()
        print("    tmux new-session -d -s carr-desk -c ~/carr-system \\")
        print("      \"claude --messaging-socket-path /tmp/cc-socks/claude-desk.sock\"")
        print()
        print("Then, once per live window:")
        print()
        print("    dispatch.py register claude-desk --socket /tmp/cc-socks/claude-desk.sock")
    print()
    print("CLEAR THE DESK'S FIRST-RUN PROMPTS BEFORE DISPATCHING ANYTHING.")
    print("A brand-new session opens on questions that wait for a keypress —")
    print("whether to use the Chrome browser, whether to trust the folder. The")
    print("socket is already bound and accepting while those are up, so a task")
    print("dispatched then reports `delivered` and sits behind the prompt doing")
    print("nothing. Attach once (tmux attach -t carr-desk), answer them, detach.")
    print("`delivered` means the desk's socket took the turn. It never means the")
    print("desk has acted on it: a live session answers in its own window, on its")
    print("own time, and its answer is not carried back through this file.")
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--registry", default=None, help="desk registry file")
    p.add_argument("--results", default=None, help="NDJSON results file")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("register", help="name a desk")
    r.add_argument("name")
    r.add_argument("--kind", default=None, choices=list(desks.KINDS))
    r.add_argument("--socket", default=None)
    r.add_argument("--model", default=None)
    r.add_argument("--family", choices=codex_models.FAMILIES)
    r.add_argument("--effort", default=None, choices=[*desks.EFFORT_CHOICES, "max"])
    r.add_argument("--cwd", default=None)
    r.add_argument("--host", default=None, help="SSH destination for a claude-remote desk")
    r.add_argument("--timeout", type=float, default=900, help="remote execution deadline in seconds")
    r.add_argument("--sandbox", default=None,
                   choices=["read-only", "workspace-write", "danger-full-access"],
                   help="Codex sandbox for this desk; omit to leave Codex's default")
    r.add_argument("--network-access", action="store_true",
                   help="opt in to read-only network access for fresh codex-live turns only")
    r.add_argument("--add-dir", dest="add_dirs", action="append", default=None,
                   help="a directory this desk may write outside its workspace (repeatable)")

    f = sub.add_parser("forget", help="drop a desk")
    f.add_argument("name")

    sub.add_parser("desks", help="list registered desks and whether they are live")

    st = sub.add_parser("start", help="put a Claude desk on the line and register it")
    st.add_argument("name")
    st.add_argument("--seed", default=None,
                    help="an opening instruction to hand the desk once it is up")

    sp = sub.add_parser("stop", help="take a desk down")
    sp.add_argument("name")
    sub.add_parser("where", help="print how to find and name THIS session's socket")
    s = sub.add_parser("send", help="dispatch one task to one desk")
    s.add_argument("name")
    s.add_argument("task")
    s.add_argument("--family", choices=codex_models.FAMILIES)
    s.add_argument("--effort", choices=desks.EFFORT_CHOICES)
    s.add_argument("--writes", action="append", help="repository-relative write glob (repeatable)")
    s.add_argument('--checkout', metavar='BRANCH|new:NAME',
                   help='clone origin before launch into a retained /private/tmp job folder; '
                        'BRANCH uses that branch, new:NAME branches from origin default HEAD. '
                        'Fresh jobs start in its parent workspace with the nested checkout named '
                        'in the task; resumed desks keep cwd and receive the checkout path. '
                        'Uses the canonical noreply author without changing sandbox permissions')
    s.add_argument("--fresh", action="store_true",
                   help="start a new Codex thread instead of resuming the desk's")
    s.add_argument("--stream-output", action="store_true",
                   help="tee headless Codex events into the caller's registered job log")
    s.add_argument("--retrieve", action="store_true",
                   help="require public source text evidence from a Grok desk")

    a = p.parse_args(argv)
    reg = Registry(a.registry) if a.registry else Registry()
    results = Path(a.results) if a.results else DEFAULT_RESULTS

    try:
        if a.cmd == "where":
            return _cmd_where()

        if a.cmd == "start":
            out = desk_start(a.name, registry=reg, seed=a.seed)
            state = "is already on the line" if out["already_running"] else "is on the line"
            print(f"{a.name} {state} (pid {out['pid']})")
            print(f"  socket  {out['socket']}")
            print(f"  log     {out['log']}")
            print(f'  send    dispatch.py send {a.name} "<task>"')
            print(f"  stop    dispatch.py stop {a.name}")
            return 0

        if a.cmd == "stop":
            out = desk_stop(a.name)
            print(f"{a.name}: {'stopped' if out['stopped'] else 'was not running'}")
            return 0

        if a.cmd == "register":
            kind = a.kind or ("claude-session" if a.socket else "codex-exec")
            entry = reg.register(a.name, kind, socket=a.socket, model=a.model, cwd=a.cwd,
                                 effort=a.effort, family=a.family,
                                 sandbox=getattr(a, "sandbox", None),
                                 network_access=a.network_access,
                                 add_dirs=getattr(a, "add_dirs", None), host=a.host, timeout_s=a.timeout)
            print(json.dumps({a.name: entry}, indent=2))
            return 0

        if a.cmd == "forget":
            reg.forget(a.name)
            print(f"forgot {a.name}")
            return 0

        if a.cmd == "desks":
            rows = reg.entries()
            if not rows:
                print("no desks registered — see `dispatch.py where`")
                return 0
            for name, e in sorted(rows.items()):
                kind = e.get("kind", "?")
                thread = e.get("thread_id")
                where = "new thread on first task" if not thread else f"thread {thread}"
                if kind in codex_models.CODEX_KINDS:
                    family = e.get("family")
                    model = codex_models.resolve_model(family) if family else "job family required"
                    live = f"live={desks.is_live(e.get('socket', ''))}" if kind == "codex-live" else where
                    print(f"{name:20} {kind:15} family={family} model={model} effort={e.get('effort')} [{live}]")
                elif kind == "claude-session":
                    live = "live" if desks.is_live(e.get("socket", "")) else "not live"
                    print(f"{name:20} {kind:15} {e.get('socket')}  [{live}]")
                else:
                    print(f"{name:20} {kind:15} {e.get('model')}  in {e.get('cwd')}  [{where}]")
            return 0

        task = sys.stdin.read() if a.task == "-" else a.task
        if not task.strip():
            raise DeskError("empty_task", "dispatch requires a non-empty task")
        # An offline host or a seat whose last probe failed is refused here,
        # with the reason, before any claim or launch (tools/room-bridge/seat_gate.py).
        desk_entry = reg.entries().get(a.name)
        if isinstance(desk_entry, dict):
            blocked = seat_gate.check({**desk_entry, "name": a.name})
            if blocked:
                raise DeskError(*blocked)
        row = dispatch(a.name, task, registry=reg, results_path=results,
                       fresh=getattr(a, "fresh", False), stream_output=a.stream_output,
                       retrieval=a.retrieve, family=a.family, effort=a.effort, writes=a.writes,
                       checkout=a.checkout)
        print(json.dumps(row, indent=2))
        return 0 if row["status"] in ("delivered", "completed") else 1

    except DeskError as e:
        print(f"refused ({e.code}): {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
