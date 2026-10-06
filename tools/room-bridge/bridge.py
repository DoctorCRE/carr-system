#!/usr/bin/env python3
"""bridge.py — the partner room's live wire to local desks.

ONE POLL CYCLE, RUN PERIODICALLY BY LAUNCHD. This is the house pattern every
other unattended service in this repo already uses (a launchd calendar schedule + a short,
bounded invocation — see ops/config/services.json's capture-poll/partner-ping
entries and bin/run-scheduled.sh's own header) rather than a hand-rolled
always-on daemon: launchd IS the supervisor, each firing is one receipted
"run", and a desk's asynchronous reply is carried across firings in the state
file (state.py) rather than by holding a process open between polls. This is
what "supervised daemon loop" means here — the loop is the repeated firing,
supervised by launchd, not a long-lived process this file manages itself.

WHAT ONE CYCLE DOES, run_once() below, in order:
  1. read-room since the stored cursor (verb_io.read_room, through
     tools/call-verb.py — the deployed Worker, never a direct DB connection).
  2. For every new turn: queue it onto every LOCAL DESK that has a room_seat
     registered and whose seat did not author the turn (state.route_turn —
     echo suppression by msg_id + seat). Also run the assignment grammar
     (grammar.classify) on it, independently of desk routing.
  3. For every desk with a room_seat: if it has a turn in flight
     (state.get_pending), check whether it has answered yet — a claude-
     session desk answers asynchronously into its own log (dispatch.py's own
     words: "the desk answers in its own window, and this file does not carry
     that back"), so this scans the log for the next `type:"result"` line
     past the byte offset recorded before injection. A codex-session desk's
     dispatch is already synchronous (dispatch.dispatch() blocks and returns
     the answer directly), so it never has a pending state — deliver and
     reply-capture happen in the same call. Either way, a captured reply is
     posted back with add-room-turn under the DESK'S seat, with a fresh
     msg_id (never the msg_id it replied to — a fresh id is what makes the
     reply itself a new, dedup-able room turn other desks can be routed).
  4. If a desk has no turn in flight and something queued, deliver the oldest
     queued turn (dispatch.dispatch(), reused unmodified from the promoted
     spike — this file does not reimplement the wire).
  5. Stamp every seated desk's heartbeat (registry_ext.stamp_heartbeat) —
     "checked this cycle, found it live/dead" — regardless of whether it had
     traffic, so a desk that goes quiet is visible within one polling
     interval instead of only the next time work happens to route to it.
  6. At most once every HEARTBEAT_INTERVAL_S (five minutes, throttle persisted
     in the state file), post the roster itself INTO the room as a
     kind="receipt" turn — the Model Room observatory's single source for desk
     state, cursor position and cycle age. The panel reads only the wire, so
     anything it needs to know about local desks has to arrive as a turn.

CONSENT POSTURE (decision 351b9995) IS UNCHANGED BY THIS FILE. Every turn this
bridge injects lands through dispatch.py's claude_wire, exactly the socket
protocol a live `claude --messaging-socket-path` session already renders as a
visible, abortable cross-session message — nothing here adds a second,
silent channel, and nothing here can suppress the human's own Ctrl-C/abort in
that session. If an injected turn is aborted, no `type:"result"` line for it
ever appears, and PENDING_TIMEOUT_S below is what keeps the queue moving
instead of waiting on it forever.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import auth_control  # noqa: E402
import claude_desktop_wire  # noqa: E402
import desks  # noqa: E402
import dispatch  # noqa: E402
import execution_contract  # noqa: E402
import flash_wire  # noqa: E402
import grammar  # noqa: E402
import kanban_adapter  # noqa: E402
import queue_dispatch  # noqa: E402
import queue_grammar  # noqa: E402
import queue_projection  # noqa: E402
import registry_ext  # noqa: E402
import session_directory  # noqa: E402
import session_presence  # noqa: E402
import state as state_mod  # noqa: E402
import verb_io  # noqa: E402

DEFAULT_STATE = Path(
    os.environ.get("CARR_ROOM_BRIDGE_STATE",
                    Path.home() / ".config" / "carr" / "room-bridge-state.json")
)
DEFAULT_ROOM = os.environ.get("CARR_ROOM_BRIDGE_ROOM", "partner-line")
PENDING_TIMEOUT_S = float(os.environ.get("CARR_ROOM_BRIDGE_PENDING_TIMEOUT", "1800"))
READ_LIMIT = int(os.environ.get("CARR_ROOM_BRIDGE_READ_LIMIT", "50"))
_RECONCILIATION_CODES = {"queue_metadata_malformed", "queue_task_identity_invalid"}
ENGINEERING_DISPATCH = HERE.parents[1] / "bin" / "run-engineering-dispatch.sh"


def run_engineering_dispatch(*, command: Path = ENGINEERING_DISPATCH,
                             timeout_s: float = 1850.0) -> dict:
    """Run the fixed lease-bound controller after a normal bridge cycle.

    The bridge itself retains its no-direct-DB stance.  The child loads the
    narrow carr_jobs credential from the protected routine file, and the child
    in turn strips that credential before sending a fresh execution packet to
    Codex.  A job is never manufactured here: no admitted work means the
    controller returns the compact ``claimed: 0`` readback.
    """
    try:
        proc = subprocess.run([str(command)], capture_output=True, text=True,
                              timeout=timeout_s, env=os.environ.copy())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("engineering controller did not complete") from exc
    if proc.returncode != 0:
        # The controller deliberately emits only its typed error class.  Do
        # not relay a subprocess stderr blob into a room receipt or service
        # log, where a future dependency could accidentally expose context.
        raise RuntimeError("engineering controller refused or failed")
    try:
        value = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("engineering controller returned invalid readback") from exc
    if not isinstance(value, dict) or value.get("ok") is not True or not isinstance(value.get("claimed"), int):
        raise RuntimeError("engineering controller returned unsupported readback")
    readback = {"claimed": value["claimed"], "completed": value.get("completed", 0),
                "results": value.get("results", [])}
    if value.get("host") == "not_controller_host":
        # the launcher's explicit per-Mac acceptance (not-this-host marker): keep it visible
        readback["host"] = "not_controller_host"
    return readback


def _validated_reconciliation(value: object) -> kanban_adapter.ReconciliationResult | None:
    """Accept only bounded, redacted reconciliation facts at the bridge seam."""
    if not isinstance(value, dict) or set(value) != {"scanned", "blocked", "diagnostics"}:
        return None
    scanned = value["scanned"]
    blocked = value["blocked"]
    diagnostics = value["diagnostics"]
    if (not isinstance(scanned, int) or isinstance(scanned, bool) or scanned < 0
            or not isinstance(blocked, list) or not isinstance(diagnostics, list)
            or len(diagnostics) > kanban_adapter.RECONCILIATION_DIAGNOSTIC_LIMIT):
        return None
    if any(not isinstance(task_id, str) or not queue_grammar.TASK_ID.fullmatch(task_id)
           for task_id in blocked):
        return None
    for diagnostic in diagnostics:
        if (not isinstance(diagnostic, dict) or set(diagnostic) != {"code", "task_id"}
                or diagnostic["code"] not in _RECONCILIATION_CODES):
            return None
        task_id = diagnostic["task_id"]
        if task_id != "unknown" and (not isinstance(task_id, str)
                                      or not queue_grammar.TASK_ID.fullmatch(task_id)):
            return None
    return {"scanned": scanned, "blocked": list(blocked), "diagnostics": list(diagnostics)}
# THE OBSERVATORY HEARTBEAT (Joe's ruling 0892c539). The Model Room panel reads
# ONLY the room wire — no second API for desk state, no Worker reach into a
# local file — so the one fact the wire could not otherwise carry, "which desks
# exist and were they alive at this cycle", is published INTO the wire as a
# machine-readable receipt. Throttled, and the throttle is persisted in the
# state file rather than inferred from the room, because launchd fires this
# process far more often than five minutes and an unthrottled heartbeat would
# bury the conversation it is supposed to annotate.
HEARTBEAT_INTERVAL_S = float(os.environ.get("CARR_ROOM_BRIDGE_HEARTBEAT_INTERVAL", "300"))


def publish_job_passport_fact(kind: str, payload: dict, *, add_room_turn) -> dict:
    """Publish one controller-validated, redacted Job Passport fact to the wire.

    This is intentionally narrower than a dispatcher: it does not select a
    model, derive identity, create authority, or persist any transcript. The
    bridge is merely the existing server-attributed wire transport. The fixed
    Hermes seat says that a deterministic orchestration host relayed the fact;
    it does not turn Hermes into an authority source.
    """
    wire = execution_contract.job_passport_wire_receipt(kind, payload)
    result = add_room_turn(
        body=json.dumps(wire, separators=(",", ":")), seat="hermes", kind="receipt",
        msg_id=str(uuid.uuid4()),
    )
    return {"kind": kind, "attempt_id": wire["job_passport"]["payload"].get("attempt_id"),
            "result": result}


def rehearse_job_passport(envelope: dict, receipt: dict, events: list[dict], profile: dict, *, evaluation_kernel: dict | None = None, spatial_surface: dict | None = None, telemetry_measurements: list[dict] | None = None, add_room_turn) -> dict:
    """Exercise the full typed wire path using only synthetic, read-only facts.

    This is a rehearsal helper, never an autonomous runtime: caller-supplied
    material must already be a synthetic v1 envelope with server-derived
    read-only authority. It emits the exact envelope, progress, receipt, and
    deterministic Observatory projection shapes the Model Room consumes. An
    optional shared evaluation kernel is relayed as evidence, never promoted.
    """
    bound = execution_contract.validate_execution_envelope(envelope)
    if bound["request"]["data_class"] != "synthetic_only" or bound["server_binding"]["authority"]["read_only"] is not True:
        raise execution_contract.ContractError("Job Passport rehearsal is synthetic and read-only only")
    completed = execution_contract.validate_attempt_receipt(receipt, bound)
    projection = execution_contract.project_observatory_attempt(bound, completed, events, profile)
    published = [publish_job_passport_fact("execution_envelope", bound, add_room_turn=add_room_turn)]
    published.extend(publish_job_passport_fact("progress_event", event, add_room_turn=add_room_turn) for event in events)
    published.append(publish_job_passport_fact("attempt_receipt", completed, add_room_turn=add_room_turn))
    published.append(publish_job_passport_fact("observatory_projection", projection, add_room_turn=add_room_turn))
    if evaluation_kernel is not None:
        published.append(publish_job_passport_fact("evaluation_kernel", evaluation_kernel, add_room_turn=add_room_turn))
    if spatial_surface is not None:
        from spatial_surface import validate_spatial_surface
        published.append(publish_job_passport_fact("spatial_surface", validate_spatial_surface(spatial_surface, projection), add_room_turn=add_room_turn))
    from spatial_surface import measurements_from_attempt_receipt, validate_telemetry_measurement
    for measurement in telemetry_measurements if telemetry_measurements is not None else measurements_from_attempt_receipt(completed):
        validated = validate_telemetry_measurement(measurement)
        if validated["attribution"]["attempt_id"] != completed["attempt_id"]:
            raise execution_contract.ContractError("telemetry measurement does not bind rehearsal attempt")
        published.append(publish_job_passport_fact("telemetry_measurement", validated, add_room_turn=add_room_turn))
    return {"mode": "synthetic_read_only_rehearsal", "work_request_id": bound["work_request_id"],
            "attempt_id": completed["attempt_id"], "projection": projection, "published": published}


# WR-000119 — THE BRIDGE MINTS THE LINK, AND ONLY THE BRIDGE.
#
# At the moment the bridge appends the dispatch turn it holds, in its hand and
# from nowhere else, the three facts the link is made of: the msg_id of the turn
# it just wrote, the target session id it just dispatched to, and the work
# request that queued the task. Writing the link LATER, from a re-read of the
# turn, would mean recovering the session id out of prose again -- which is the
# substring match 0531 exists to retire.
#
# hermes-projector IS THE CREDENTIAL, not a courtesy. 0531 refuses any derived
# identity other than hermes-pilot inside the definer, and that selector is the
# one path by which the Worker derives hermes-pilot (see verb_io.project_room_queue).
# ``verb_io._run_verb`` is reused rather than a second subprocess path invented:
# verb_io.py is outside this Work Request's authorized paths, so no public
# wrapper could be added there, and two paths to the record layer would be two
# places for the identity derivation to drift.
def record_dispatch_link(*, turn_msg_id: str, session_id: str,
                          work_request_id: str | None = None,
                          dispatch_ref: str | None = None,
                          call_verb=verb_io._run_verb) -> dict:
    """Mint the explicit link between a room turn and the session it went to."""
    args = {
        "dispatch_ref": dispatch_ref or str(uuid.uuid4()),
        "turn_msg_id": turn_msg_id,
        "session_id": session_id,
    }
    if work_request_id:
        args["work_request_id"] = work_request_id
    return call_verb("record-dispatch-link", args,
                     client_profile="hermes-projector")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def bind_room_writer(writer, room: str):
    """Bind every bridge-authored turn to the room being polled.

    ``verb_io.add_room_turn`` retains the record layer's legacy partner-line
    default for callers that genuinely use that wire.  A bridge cycle may poll
    another named room, so relying on the writer's independent default would
    read one conversation and post its replies and receipts into another.
    Test doubles stay simple: run_once callers may still inject an already
    room-aware writer directly.
    """
    def write(*args, **kwargs):
        kwargs.setdefault("room", room)
        return writer(*args, **kwargs)

    return write


def conversational_desk_seats(entries: dict[str, dict]) -> dict[str, str]:
    """Room fan-out seats, excluding explicit queue-only transports."""
    return {
        name: entry["room_seat"] for name, entry in entries.items()
        if entry.get("room_seat") and entry.get("kind") != "claude-desktop"
    }


def mention_only_desks(entries: dict[str, dict]) -> frozenset[str]:
    """Desks registered with room_listen="mention": they hear people's turns but
    hear another desk only when @-mentioned (state.is_unaddressed_desk_turn)."""
    return frozenset(name for name, entry in entries.items()
                     if entry.get("room_listen") == "mention")


def _elapsed_seconds(iso_ts: str | None, *, now: str | None = None) -> float:
    if iso_ts is None:
        return 0.0
    try:
        t = datetime.fromisoformat(iso_ts)
    except (ValueError, TypeError):
        return 0.0
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    ref = datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return (ref - t).total_seconds()


def format_for_desk(turn: dict) -> str:
    """"[partner-room · claude] <body>" — mirrors partner-line/watch.py's
    format_body(), so a human watching a desk's own window can see who a
    routed turn is from without opening the room itself."""
    seat = turn.get("seat") or "?"
    body = turn.get("body") or ""
    return f"[partner-room · {seat}] {body}"


def scan_for_result(log_path: Path, offset: int) -> str | None:
    """The first `type:"result"` event's `result` field appearing after
    `offset` in a claude-session desk's own --output-format stream-json log —
    the final answer text for one completed turn. Returns None if nothing
    complete has landed yet; never raises on a partial trailing line (it just
    fails json.loads and is skipped, and gets re-read whole next cycle since
    `offset` is not advanced until a result is actually found)."""
    if not log_path.exists():
        return None
    with log_path.open("rb") as fh:
        fh.seek(offset)
        data = fh.read()
    if not data:
        return None
    for raw in data.decode("utf-8", errors="replace").split("\n"):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "result":
            return obj.get("result")
    return None


def probe_live(entry: dict) -> bool:
    kind = entry.get("kind")
    if kind == "claude-session" and entry.get("room_seat") == "flash":
        return True  # Demand dispatch starts this desk; probes must not load it.
    if kind in ("claude-session", "codex-live"):
        return desks.is_live(entry.get("socket", ""))
    if kind == "flash-local":
        return True  # A claimed task, rather than a bridge heartbeat, starts Flash.
    # claude-desktop and codex-session are durable rather than live
    # (dispatch.py's own framing) —
    # there is no process to probe between dispatches, so "live" here means
    # "usable", which delivery itself is what actually proves each cycle.
    return True


class QueueCompletionPostFailed(Exception):
    """A synchronous desk's FINAL completion post to the room failed.

    Raised only by run_once's post_sync_completion hook, wrapping the bare
    RuntimeError add_room_turn raises (verb_io.py's contract). It is a distinct
    type, not a RuntimeError, so run_once's per-desk handler can contain exactly
    this failure to its own desk while every OTHER RuntimeError — a
    conversational delivery or handle_pending post failure — still aborts the
    cycle before state is saved, so that turn is retried rather than recorded as
    consumed (PR #1254 round 2 review, finding 2).
    """


def _post_completion_payload(completion: dict, *, add_room_turn, seat: str) -> None:
    """Post one already-built queue completion payload into the room.

    The task_id is read back out of the payload itself (queue_completion.task_id)
    rather than taken as a separate argument, so a caller that has only the
    payload — like finish_pending_posted's post_completion hook, which runs
    before the caller ever gets a "terminal" dict back — can still post it.
    """
    task_id = (completion.get("queue_completion") or {}).get("task_id")
    if not isinstance(task_id, str) or not task_id.startswith("t_"):
        raise queue_dispatch.QueueDispatchError("queue completion callback identity is invalid")
    add_room_turn(
        body=json.dumps(completion, separators=(",", ":")),
        seat=seat,
        kind="turn",
        msg_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"carr:queue-completion:{task_id}")),
        idempotency_key=f"queue-completion:{task_id}",
    )


def _post_queue_completion(terminal: dict, *, add_room_turn, seat: str) -> None:
    """Post one queue task's bounded typed completion callback into the room.

    Used by the async pending path (handle_pending), which already has a full
    "terminal" dict ({"task_id": ..., "completion": ...}) in hand once
    finish_pending returns. Synchronous desks without MCP tools post through
    _post_completion_payload directly instead — see finish_pending_posted.
    """
    completion = terminal.get("completion")
    if not isinstance(completion, dict):
        raise queue_dispatch.QueueDispatchError("queue completion callback is absent")
    _post_completion_payload(completion, add_room_turn=add_room_turn, seat=seat)


def handle_pending(name: str, seat: str, state: dict, *, add_room_turn,
                    log_path: Path, pending_timeout_s: float,
                    scan=scan_for_result, now: str | None = None,
                    queue_executor: queue_dispatch.QueueDeskExecutor | None = None,
                    inspect_background=claude_desktop_wire.inspect_session,
                    read_background_result=claude_desktop_wire.read_final_text,
                    handoff_background=claude_desktop_wire.handoff_to_desktop) -> dict | None:
    pending = state_mod.get_pending(state, name)
    if pending is None:
        return None
    result_text = None
    if pending.get("transport") == "claude-desktop":
        session_id = pending.get("session_id")
        if not isinstance(session_id, str):
            observed = {"state": "failed", "found": False}
        else:
            try:
                observed = inspect_background(session_id)
            except claude_desktop_wire.ClaudeDesktopError:
                observed = {"state": "unknown", "found": False}
        observed_state = str(observed.get("state") or "unknown").lower()
        if observed_state in claude_desktop_wire.COMPLETED_STATES:
            result_text = read_background_result(session_id)
            if result_text is None:
                return {"desk": name, "outcome": "desktop_result_not_ready",
                        "session_id": session_id}
            # The session has already reported completion — result_text above
            # is its real answer, already in hand. A /desktop handoff failure
            # from here on (e.g. desktop_handoff_timeout) is a problem with
            # opening a window onto a finished session, never a reason to
            # treat the TASK as failed: doing so used to fall through to
            # queue_executor.fail_pending, which schedules a retry (a second
            # launch, duplicating the work — defect a2e7dcb5) or blocks a task
            # that in fact already succeeded. So a handoff failure here only
            # posts its own honest receipt and falls through to the ordinary
            # completion handling below, which finishes the task with the
            # result already read.
            try:
                handed = handoff_background(session_id)
            except claude_desktop_wire.ClaudeDesktopError as exc:
                add_room_turn(
                    body=json.dumps({"claude_desktop_handoff": {
                        "session_id": session_id, "status": "failed", "code": exc.code,
                    }}, separators=(",", ":")),
                    seat="hermes", kind="receipt", msg_id=str(uuid.uuid4()),
                )
            else:
                # "already_open": Claude Desktop already holds the session
                # (claude attach refuses it as running in another terminal),
                # which is the state the handoff exists to reach.
                status = handed.get("status") if isinstance(handed, dict) else None
                add_room_turn(
                    body=json.dumps({"claude_desktop_handoff": {
                        "session_id": session_id,
                        "status": status if status in {"opened", "already_open"} else "opened",
                    }}, separators=(",", ":")),
                    seat="hermes", kind="receipt", msg_id=str(uuid.uuid4()),
                )
        elif observed_state in (claude_desktop_wire.FAILED_STATES
                                | claude_desktop_wire.NEEDS_INPUT_STATES):
            if pending.get("origin_kind") == "queue" and queue_executor is not None:
                terminal = queue_executor.fail_pending(pending, "provider_unavailable", now=now)
                state_mod.clear_pending(state, name)
                return {"desk": name, "session_id": session_id, **terminal}
            state_mod.clear_pending(state, name)
            return {"desk": name, "outcome": f"desktop_{observed_state}",
                    "session_id": session_id}
    else:
        result_text = scan(log_path, pending["log_offset"])
    if result_text is not None:
        if pending.get("origin_kind") == "queue":
            if queue_executor is None:
                return {"desk": name, "outcome": "queue_executor_unavailable"}
            terminal = queue_executor.finish_pending(pending, result_text)
            _post_queue_completion(terminal, add_room_turn=add_room_turn, seat=seat)
            state_mod.clear_pending(state, name)
            return {"desk": name, **{key: value for key, value in terminal.items()
                                     if key != "completion"}}
        add_room_turn(body=result_text.strip() or "(empty reply)", seat=seat, kind="turn",
                      msg_id=str(uuid.uuid4()))
        state_mod.clear_pending(state, name)
        return {"desk": name, "outcome": "replied"}
    if _elapsed_seconds(pending.get("injected_at"), now=now) > pending_timeout_s:
        if pending.get("origin_kind") == "queue":
            if queue_executor is None:
                return {"desk": name, "outcome": "queue_executor_unavailable"}
            terminal = queue_executor.fail_pending(pending, "desk_result_timeout", now=now)
            if terminal.get("outcome") == "retry_scheduled":
                state_mod.set_queue_retry_at(state, terminal["task_id"], terminal["retry_at"])
            else:
                state_mod.clear_queue_retry_at(state, terminal.get("task_id"))
            state_mod.clear_pending(state, name)
            return {"desk": name, **terminal}
        add_room_turn(
            body=json.dumps({
                "desk": name, "timed_out_after_s": pending_timeout_s,
                "source_msg_id": pending.get("source_msg_id"),
            }, separators=(",", ":")),
            seat="hermes", kind="receipt", msg_id=str(uuid.uuid4()),
        )
        state_mod.clear_pending(state, name)
        return {"desk": name, "outcome": "timed_out"}
    return {"desk": name, "outcome": "still_pending"}


def deliver(name: str, entry: dict, seat: str, queued_turn: dict, *, state: dict,
            registry, results_path: Path, add_room_turn, dispatch_fn=dispatch.dispatch,
            desk_state_dir: Path = dispatch.DESK_STATE) -> dict:
    text = format_for_desk(queued_turn)
    kind = entry.get("kind")

    if kind == "claude-session":
        log_path = desk_state_dir / f"{name}.log"
        offset = log_path.stat().st_size if log_path.exists() else 0
        row = dispatch_fn(name, text, registry=registry, results_path=results_path)
        state_mod.set_pending(
            state, name, dispatch_msg_id=row["msg_id"], log_offset=offset,
            injected_at=row["dispatched_at"], source_msg_id=queued_turn["msg_id"],
            source_seq=queued_turn["seq"],
        )
        return {"desk": name, "outcome": "delivered_async"}

    if kind in ("codex-session", "codex-live", "flash-local", "grok-cli"):
        # Only a codex-session desk can sit on a thread Codex Desktop holds, and
        # this conversational path is the one caller that waits for nothing back,
        # so it alone opts into the Desktop route (see dispatch._to_codex).
        extra = {"live_desktop": True} if kind == "codex-session" else {}
        row = dispatch_fn(name, text, registry=registry, results_path=results_path, **extra)
        status = row.get("status")
        if status == "delivered_live":
            # The turn was started inside the Desktop window that owns the
            # thread (codex_ipc) and the session answers there, like a claude desk.
            return {"desk": name, "outcome": "delivered_live"}
        if status == "completed":
            if kind == "grok-cli":
                add_room_turn(body=json.dumps({"grok_execution": {
                    "desk": name, "source_msg_id": queued_turn["msg_id"],
                    "source_seq": queued_turn["seq"],
                    "dispatch_msg_id": row["msg_id"],
                    **row["provider_metadata"],
                }}, separators=(",", ":")), seat="hermes", kind="receipt",
                    msg_id=str(uuid.uuid4()))
            add_room_turn(body=(row.get("result") or "").strip() or "(empty reply)",
                          seat=seat, kind="turn", msg_id=str(uuid.uuid4()))
            return {"desk": name, "outcome": "replied_sync"}
        add_room_turn(
            body=json.dumps({"desk": name, "status": status,
                             "detail": row.get("detail"),
                             **({key: row[key] for key in ("next_route", "diagnostic_path") if key in row}
                                if kind == "grok-cli" else {})}, separators=(",", ":")),
            seat="hermes", kind="receipt", msg_id=str(uuid.uuid4()),
        )
        return {"desk": name, "outcome": f"failed:{status}"}

    return {"desk": name, "outcome": f"unsupported_kind:{kind}"}


def heartbeat_due(state: dict, *, now: str | None = None,
                  interval_s: float = HEARTBEAT_INTERVAL_S) -> bool:
    """True when this cycle should publish a heartbeat receipt. Never posted
    means due; otherwise due once `interval_s` has elapsed since the last one.
    A clock that has gone backwards (or an unparseable stamp) reads as 0
    elapsed and therefore NOT due — the throttle fails closed, so a bad
    timestamp cannot turn the heartbeat into a flood."""
    last = state_mod.get_heartbeat_at(state)
    if not last:
        return True
    return _elapsed_seconds(last, now=now) >= interval_s


def heartbeat_body(desk_entries: dict, cursor: int, cycle_at: str,
                   profiles: list | None = None, sessions: list | None = None) -> str:
    """The compact JSON the panel parses. `desks` carries every REGISTERED desk,
    seated or not: a desk with no room_seat is exactly the panel's dormant
    case, and omitting it would make an unwired desk indistinguishable from a
    desk that was never registered.

    `auth` is this cycle's own sign-in probe (spec section 17): true, false, or
    null when the vendor CLI could not answer. Null is NOT signed-out — see
    auth_control.probe_auth — and the panel renders it as unknown.

    `profiles` is the named-agent roster (loop 520): the NAME persists and the
    model behind it is staffing detail, and any feed window must contain
    current profile truth, so the roster is REPUBLISHED here rather than
    assumed from older receipts. None means the roster could not be read this
    cycle, and the key is then ABSENT — never an empty list, which would read
    as "no profiles exist". Each desk row also names the profile bound to it,
    when the local registry carries one."""
    rows = [
        {
            "name": name,
            "seat": entry.get("room_seat"),
            "live": bool(entry.get("last_live")),
            "last_seen": entry.get("last_seen"),
            "auth": entry.get("last_auth") if isinstance(entry.get("last_auth"), bool) else None,
            "profile": entry.get("profile") if isinstance(entry.get("profile"), str) else None,
            **({"model": entry.get("model"), "effort": entry.get("effort")}
               if entry.get("kind") == "grok-cli" else {}),
        }
        for name, entry in sorted(desk_entries.items())
    ]
    heartbeat: dict = {"desks": rows, "cursor": cursor, "cycle_at": cycle_at}
    if profiles is not None:
        heartbeat["profiles"] = profiles
    # `sessions` is the self-announced session roster (session_directory.py):
    # every session that posted itself to the room, with this cycle's liveness
    # and the address another session uses to reach it. Absent, never empty,
    # when the directory could not be read this cycle — same stance as profiles.
    if sessions is not None:
        heartbeat["sessions"] = sessions
        # The room caps a turn at 20,000 characters. A burst of live sessions
        # must not silence desk health altogether; publish the newest bounded
        # roster and say how many entries could not fit.
        if len(json.dumps({"heartbeat": heartbeat}, separators=(",", ":"))) > 20000:
            newest = sorted(sessions, key=lambda row: str(row.get("last_live_at") or ""), reverse=True)
            heartbeat["sessions_total"] = len(sessions)
            heartbeat["sessions_truncated"] = 0
            heartbeat["sessions"] = []
            for row in newest:
                heartbeat["sessions"].append(row)
                heartbeat["sessions_truncated"] = len(sessions) - len(heartbeat["sessions"])
                if len(json.dumps({"heartbeat": heartbeat}, separators=(",", ":"))) > 19000:
                    heartbeat["sessions"].pop()
                    heartbeat["sessions_truncated"] += 1
                    break
    return json.dumps({"heartbeat": heartbeat}, separators=(",", ":"))


def post_heartbeat(state: dict, desk_entries: dict, *, add_room_turn, cursor: int,
                   now: str | None = None,
                   interval_s: float = HEARTBEAT_INTERVAL_S,
                   profiles: list | None = None,
                   sessions: list | None = None) -> dict | None:
    """Publish the roster receipt if the throttle allows, and record that it
    went out. Returns None when throttled, so run_once's summary says honestly
    whether this cycle spoke."""
    if not heartbeat_due(state, now=now, interval_s=interval_s):
        return None
    stamp = now or _now()
    body = heartbeat_body(desk_entries, cursor, stamp, profiles=profiles, sessions=sessions)
    add_room_turn(body=body, seat="hermes", kind="receipt", msg_id=str(uuid.uuid4()))
    state_mod.set_heartbeat_at(state, stamp)
    return {"posted_at": stamp, "desks": len(desk_entries), "cursor": cursor}


def handle_control(turn: dict, control: dict, desk_entries: dict, state: dict, *,
                    add_room_turn, registry, now: str | None = None,
                    launch=auth_control.launch_login,
                    throttle_s: float = auth_control.LOGIN_THROTTLE_S) -> dict:
    """One control turn from the observatory's RECONNECT button, allowlisted
    and then executed or refused — never silently dropped, because a control
    nobody answers is indistinguishable from a bridge that is down.

    Every refusal posts its reason back onto the wire, which is also how the
    panel learns that a second click was throttled rather than lost."""
    outcome, reason = auth_control.classify_control(
        turn, control, registered=set(desk_entries), state=state, now=now, throttle_s=throttle_s)
    desk = str(control.get("desk") or "")
    if outcome != "ok":
        add_room_turn(
            body=json.dumps({"control_refused": {
                "action": control.get("action"), "desk": desk, "reason": reason,
                "source_seq": turn.get("seq"), "source_msg_id": turn.get("msg_id"),
            }}, separators=(",", ":")),
            seat="hermes", kind="receipt", msg_id=str(uuid.uuid4()),
        )
        return {"seq": turn.get("seq"), "outcome": "refused", "desk": desk, "reason": reason}

    stamp = now or _now()
    launched = launch(desk, desk_entries[desk])
    if not launched.get("launched"):
        add_room_turn(
            body=json.dumps({"control_refused": {
                "action": "login", "desk": desk, "reason": launched.get("reason"),
                "source_seq": turn.get("seq"),
            }}, separators=(",", ":")),
            seat="hermes", kind="receipt", msg_id=str(uuid.uuid4()),
        )
        return {"seq": turn.get("seq"), "outcome": "launch_failed", "desk": desk,
                "reason": launched.get("reason")}

    auth_control.note_login_launched(state, desk, stamp)
    add_room_turn(
        body=json.dumps({"control_executed": {
            "action": "login", "desk": desk, "at": stamp,
            "note": "the vendor's own sign-in flow was opened on this Mac; approval "
                    "happens in the browser and no credential passes through the bridge. "
                    "The desk process restarts automatically once the probe reads signed in.",
            "source_seq": turn.get("seq"),
        }}, separators=(",", ":")),
        seat="hermes", kind="receipt", msg_id=str(uuid.uuid4()),
    )
    return {"seq": turn.get("seq"), "outcome": "executed", "desk": desk}


def settle_restarts(desk_entries: dict, auth_by_desk: dict, state: dict, *,
                    add_room_turn, registry,
                    stop=dispatch.desk_stop, start=dispatch.desk_start) -> list[dict]:
    """A desk that was awaiting a login and now probes signed in gets its
    PROCESS restarted, because a running desk holds its token in memory
    (measured 2026-08-22). Restarting is what makes the reconnect button
    actually reconnect rather than merely open a browser tab."""
    settled: list[dict] = []
    for desk in list(auth_control.awaiting_login(state)):
        if desk not in desk_entries:
            auth_control.clear_awaiting_login(state, desk)
            continue
        if auth_by_desk.get(desk) is not True:
            continue
        result = auth_control.restart_desk(desk, registry=registry, stop=stop, start=start)
        auth_control.clear_awaiting_login(state, desk)
        add_room_turn(
            body=json.dumps({"desk_restarted": {
                "desk": desk, "after": "login",
                "restarted": bool(result.get("restarted")),
                "reason": result.get("reason"),
            }}, separators=(",", ":")),
            seat="hermes", kind="receipt", msg_id=str(uuid.uuid4()),
        )
        settled.append({"desk": desk, **{k: v for k, v in result.items() if k != "detail"}})
    return settled


def run_once(*, registry: desks.Registry | None = None, state_path: Path = DEFAULT_STATE,
             room: str = DEFAULT_ROOM, results_path: Path | None = None,
             pending_timeout_s: float = PENDING_TIMEOUT_S,
             read_room=verb_io.read_room, add_room_turn=verb_io.add_room_turn,
             dispatch_fn=dispatch.dispatch, desk_state_dir: Path = dispatch.DESK_STATE,
             probe_auth=auth_control.probe_auth, launch_login=auth_control.launch_login,
             desk_stop=dispatch.desk_stop, desk_start=dispatch.desk_start,
             read_profiles=verb_io.read_profiles,
             project_room_turn=verb_io.project_room_queue,
             queue_service: kanban_adapter.QueueService | None = None,
             queue_executor: queue_dispatch.QueueDeskExecutor | None = None,
             queue_projector=queue_projection.project_once,
             engineering_dispatcher=run_engineering_dispatch,
             now_fn=_now,
             session_directory_path: Path | None = None,
             session_probe=None, session_deliver=None, host: str | None = None,
             log=print) -> dict:
    registry = registry or desks.Registry()
    results_path = Path(results_path or dispatch.DEFAULT_RESULTS)
    state = state_mod.load_state(state_path)
    # The self-announced session directory lives beside the desk registry, so a
    # test's temporary registry never touches the real one.
    session_directory_path = Path(session_directory_path or
                                  registry.path.parent / "session-directory.json")

    desk_entries = registry.entries()
    # A claude-desktop desk is explicit-queue-only. Keeping its seat in the
    # registry makes heartbeat and result attribution visible, while excluding
    # it from conversational fan-out prevents ordinary room chatter from
    # creating model sessions or spending tokens.
    desk_seats = conversational_desk_seats(desk_entries)
    mention_only = mention_only_desks(desk_entries)

    # The target catalog is configuration for command ingress, not a reason to
    # stop the conversational bridge.  When it cannot be loaded, only an
    # attempted @queue command is refused; ordinary turns still route.
    queue_load_error = None
    if queue_service is None:
        try:
            queue_service = kanban_adapter.QueueService()
        except kanban_adapter.QueueError as exc:
            queue_load_error = exc

    if (queue_executor is None and queue_service is not None
            and hasattr(queue_service, "catalog")
            and hasattr(getattr(queue_service, "adapter", None), "ready_for")):
        queue_executor = queue_dispatch.QueueDeskExecutor(
            catalog=queue_service.catalog, adapter=queue_service.adapter)
    desk_queue_targets = {}
    if queue_executor is not None:
        desk_queue_targets = {
            target["desk"]: alias
            for alias, target in queue_executor.catalog["targets"].items()
            if target.get("enabled") and target.get("adapter") == "desk"
        }

    result = read_room(state["last_seq"], room=room)
    turns = [t for t in result.get("turns", []) if int(t.get("seq", 0)) > state["last_seq"]]

    routed: dict[str, list[str]] = {}
    assignments: list[dict] = []
    controls: list[dict] = []
    queue_events: list[dict] = []
    for t in turns:
        # Queue commands are a control plane, never conversational work.  This
        # check deliberately precedes route_turn: every @queue attempt is
        # consumed, including malformed and unauthorized requests.
        if queue_service is not None:
            queued = queue_service.handle(t, room=room)
        elif str(t.get("body") or "").strip().startswith("@queue"):
            queued = {
                "handled": True, "kind": "rejected", "receipt": {"queue_rejected": {
                    "source_seq": t.get("seq"), "source_msg_id": t.get("msg_id"),
                    "code": queue_load_error.code if queue_load_error else "queue_unavailable",
                    "reason": queue_load_error.reason if queue_load_error else "queue is unavailable",
                    "hint": "Try again after Hermes is available",
                }},
            }
        else:
            queued = {"handled": False}
        if queued.get("handled"):
            receipt = queued.get("receipt")
            if receipt:
                add_room_turn(body=json.dumps(receipt, separators=(",", ":")), seat="hermes",
                              kind="receipt", msg_id=str(uuid.uuid4()))
            routed[str(t.get("msg_id"))] = []
            queue_events.append({"seq": t.get("seq"), "kind": queued.get("kind")})
            continue
        routed[str(t.get("msg_id"))] = state_mod.route_turn(state, t, desk_seats, mention_only=mention_only)
        control = auth_control.parse_control(t)
        if control is not None:
            controls.append(handle_control(
                t, control, desk_entries, state, add_room_turn=add_room_turn,
                registry=registry, launch=launch_login))
            continue
        outcome, parsed, reason = grammar.classify(t)
        if outcome == "ok":
            assert parsed is not None  # guaranteed by classify() whenever outcome is "ok"
            res = grammar.apply_assignment(t, parsed, add_room_turn=add_room_turn)
            assignments.append({"seq": t.get("seq"), "outcome": outcome, **res})
        elif outcome in ("malformed", "unauthorized"):
            assert reason is not None  # guaranteed by classify() for these two outcomes
            grammar.reject(t, outcome, reason, add_room_turn=add_room_turn)
            assignments.append({"seq": t.get("seq"), "outcome": outcome, "reason": reason})
    state_mod.advance_seq(state, turns)

    # Quarantine cards for disabled catalog targets before any desk can claim
    # or dispatch a ready card.  This is one bounded canonical read/reconcile
    # per bridge cycle; Hermes remains the only state mutation authority.
    queue_reconciliation: kanban_adapter.ReconciliationResult = {
        "scanned": 0, "blocked": [], "diagnostics": []
    }
    errors: list[dict] = []
    queue_reconciliation_failed = False
    if queue_executor is not None and (
            queue_service is None or not hasattr(queue_service, "reconcile_disabled_targets")):
        queue_reconciliation_failed = True
        errors = [{"desk": "(queue-reconciler)", "error": "queue_reconciliation_failed",
                   "detail": "queue reconciliation unavailable"}]
    elif queue_service is not None:
        try:
            queue_reconciliation = queue_service.reconcile_disabled_targets()
            validated = _validated_reconciliation(queue_reconciliation)
            if validated is None:
                raise ValueError("invalid reconciliation result")
            queue_reconciliation = validated
        except Exception as exc:  # reconciliation failure must not enable fallback dispatch
            queue_reconciliation_failed = True
            queue_reconciliation = {"scanned": 0, "blocked": [], "diagnostics": []}
            errors = [{"desk": "(queue-reconciler)", "error": "queue_reconciliation_failed",
                       "detail": "queue reconciliation failed"}]

    # SIGN-IN STATE, probed once per cycle for EVERY registered desk — seated or
    # not, since an unseated desk can be signed out too and Joe's panel shows
    # it. Stamped onto the registry so the heartbeat below carries this cycle's
    # own answer rather than a stale one (spec section 17: no probe result older
    # than one cycle is shown as current).
    auth_by_desk: dict[str, bool | None] = {}
    for name, entry in desk_entries.items():
        auth_by_desk[name] = probe_auth(entry)
        registry_ext.stamp_auth(name, auth=auth_by_desk[name], path=registry.path)

    # Exactly one socket liveness answer per desk per cycle.  Queue admission
    # and the heartbeat below must consume this same fact; probing again after
    # dispatch can otherwise turn one cycle into two contradictory truths.
    live_by_desk = {name: probe_live(entry) for name, entry in desk_entries.items()}

    delivered: list[dict] = []
    # Global stale-timer pruning is safe only after reconciliation succeeded
    # and every configured enabled desk target was actually scanned.
    queue_scan_complete = queue_executor is not None and not queue_reconciliation_failed
    queue_ready_task_ids: set[str] = set()
    queue_scanned_desks: set[str] = set()
    required_queue_desks = set(desk_queue_targets)
    for name, entry in desk_entries.items():
        seat = entry.get("room_seat")
        # A queue-target desk (e.g. flash-local) is legitimately seatless: it is
        # deliberately excluded from conversational_desk_seats so ordinary room
        # chatter never reaches it (Joe 2026-09-24, and PR #1249's own docstring).
        # Before this fix, "no seat" and "no queue work either" were conflated —
        # `continue` skipped the ENTIRE per-desk body, including the executor's
        # start() dispatch call further down, so a seatless queue-target desk's
        # ready tasks were never claimed or dispatched at all (finding 1, PR
        # #1254 round 2 review). Only skip a desk that has neither.
        if not seat and name not in desk_queue_targets:
            continue
        # post_seat attributes a post when THIS desk has no room identity of its
        # own — the system seat used everywhere else in this file for a receipt
        # nobody in the room authored (e.g. desktop-handoff and timeout receipts
        # above). It is used only for queue-path posts below; the conversational
        # deliver() path is guarded by `seat` itself (see the assertion below),
        # since a seatless desk must never answer a conversational room turn.
        post_seat = seat or "hermes"
        try:
            pending_outcome = handle_pending(
                name, post_seat, state, add_room_turn=add_room_turn,
                log_path=desk_state_dir / f"{name}.log",
                pending_timeout_s=pending_timeout_s,
                now=now_fn(),
                queue_executor=queue_executor,
            )
            if pending_outcome:
                delivered.append(pending_outcome)
            if (name in desk_queue_targets and state_mod.get_pending(state, name) is not None):
                queue_scan_complete = False
            if state_mod.get_pending(state, name) is None:
                next_queued = state_mod.pop_next_queued(state, name)
                if next_queued is not None:
                    # conversational_desk_seats() never routes a room turn to a
                    # seatless desk, so this should be unreachable for one — but
                    # a seatless desk answering a human turn is exactly what must
                    # never happen, so a bug elsewhere fails loudly here instead
                    # of silently delivering it.
                    if not seat:
                        raise queue_dispatch.QueueDispatchError(
                            f"desk {name!r} popped a conversational turn without a room seat")
                    if name in desk_queue_targets:
                        queue_scan_complete = False
                    delivered.append(deliver(
                        name, entry, seat, next_queued, state=state, registry=registry,
                        results_path=results_path, add_room_turn=add_room_turn,
                        dispatch_fn=dispatch_fn, desk_state_dir=desk_state_dir,
                    ))
                elif (queue_executor is not None and name in desk_queue_targets
                      and not queue_reconciliation_failed):
                    log_path = desk_state_dir / f"{name}.log"
                    offset = log_path.stat().st_size if log_path.exists() else 0

                    def dispatch_queue(prompt: str) -> dict:
                        return dispatch_fn(
                            name, prompt, registry=registry, results_path=results_path)

                    # Flash and Grok have no MCP tools of their own. Their
                    # synchronous answers must reach the room through this
                    # callback before the queue task becomes terminal.
                    is_flash_local = entry.get("kind") == "flash-local"
                    publishes_sync_reply = entry.get("kind") in {"flash-local", "grok-cli"}

                    def post_sync_completion(completion: dict) -> None:
                        # Posted from INSIDE finish_pending_posted, only for a FINAL
                        # outcome and before Hermes is marked terminal — see that
                        # method's docstring for why a failed post must not lose the
                        # reply for good, and why a retry never posts. A post
                        # failure is re-raised as QueueCompletionPostFailed so the
                        # per-desk handler below contains exactly this failure to
                        # this desk, and nothing else.
                        try:
                            _post_completion_payload(
                                completion, add_room_turn=add_room_turn, seat=post_seat)
                        except RuntimeError as exc:
                            raise QueueCompletionPostFailed(str(exc)) from exc

                    queue_outcome = queue_executor.start(
                        desk_queue_targets[name], dispatch_call=dispatch_queue,
                        desk_busy=state_mod.has_queued(state, name),
                        retry_at=state["queue_retry_at"], now=now_fn(),
                        desk_live=live_by_desk.get(name, True),
                        unavailable_since=state.get("queue_unavailable_since", {}),
                        include_reply=publishes_sync_reply,
                        retry_protocol_errors=is_flash_local,
                        post_completion=post_sync_completion if publishes_sync_reply else None,
                        completion_dir=state_path.parent / (state_path.name + ".queue-completions"),
                    )
                    queue_scan_complete = queue_scan_complete and bool(
                        getattr(queue_executor, "last_ready_scan_complete", False))
                    if getattr(queue_executor, "last_ready_scan_complete", False):
                        queue_scanned_desks.add(name)
                    queue_ready_task_ids.update(
                        getattr(queue_executor, "last_ready_task_ids", set()))
                    if queue_outcome.get("outcome") == "retry_scheduled":
                        state_mod.set_queue_retry_at(
                            state, queue_outcome["task_id"], queue_outcome["retry_at"])
                    elif queue_outcome.get("outcome") not in {"retry_wait", "idle"}:
                        state_mod.clear_queue_retry_at(state, queue_outcome.get("task_id"))
                    if queue_outcome.get("outcome") == "desk_unavailable_wait":
                        state_mod.set_queue_unavailable_since(
                            state, str(queue_outcome["task_id"]),
                            str(queue_outcome["unavailable_since"]))
                    elif live_by_desk.get(name, True) or queue_outcome.get("outcome") == "idle":
                        state_mod.clear_queue_unavailable_since(
                            state, queue_outcome.get("task_id"))
                    elif queue_outcome.get("outcome") == "blocked":
                        state_mod.clear_queue_unavailable_since(
                            state, queue_outcome.get("task_id"))
                    pending = queue_outcome.get("pending")
                    if isinstance(pending, dict):
                        state_mod.set_pending(
                            state, name,
                            dispatch_msg_id=str(pending.get("dispatch_msg_id") or ""),
                            log_offset=offset,
                            injected_at=str(pending.get("injected_at") or _now()),
                            source_msg_id=str(pending.get("source_msg_id") or
                                              f"queue:{pending['kanban_task_id']}"),
                            source_seq=pending.get("source_seq"),
                            origin_kind="queue",
                            kanban_task_id=pending["kanban_task_id"],
                            target=pending["target"],
                            finish=pending["finish"],
                            cap=pending.get("cap"),
                            transport=pending.get("transport"),
                            session_id=pending.get("session_id"),
                        )
                        if pending.get("transport") == "claude-desktop":
                            # ONE msg_id, minted here and used twice: once as the
                            # turn's own id and once as what the link names. A
                            # second uuid would make the link point at a turn
                            # nobody could find.
                            dispatch_turn_msg_id = str(uuid.uuid4())
                            dispatch_ref = str(uuid.uuid4())
                            add_room_turn(
                                body=json.dumps({"claude_desktop_session": {
                                    "session_id": pending.get("session_id"),
                                    "status": "backgrounded",
                                    "task_id": pending.get("kanban_task_id"),
                                    "desktop_visibility": "after_completion",
                                    "dispatch_ref": dispatch_ref,
                                }}, separators=(",", ":")),
                                seat="hermes", kind="receipt",
                                msg_id=dispatch_turn_msg_id,
                            )
                            # A link that cannot be written is a visible error on
                            # this cycle, never a silent fall back to the body
                            # match the link exists to replace.
                            try:
                                record_dispatch_link(
                                    turn_msg_id=dispatch_turn_msg_id,
                                    session_id=str(pending.get("session_id") or ""),
                                    work_request_id=pending.get("work_request_id"),
                                    dispatch_ref=dispatch_ref,
                                )
                                # The DESK's own acknowledgement, written by the
                                # desk dispatcher and never by this file: the
                                # stage is fixed at `received` there, so the
                                # bridge cannot send `acknowledged` even by
                                # accident. `acknowledged` belongs to the session
                                # that acts, from inside its own turn.
                                dispatch.acknowledge_received(
                                    dispatch_ref, desk=name, log_offset=offset,
                                    injected_at=str(pending.get("injected_at") or ""),
                                )
                            except (RuntimeError, desks.DeskError) as exc:
                                errors.append({"desk": name,
                                               "error": "dispatch_link_failed",
                                               "detail": str(exc)[:500]})
                    if queue_outcome.get("outcome") != "idle":
                        delivered.append({"desk": name, **queue_outcome})
            registry_ext.stamp_heartbeat(name, live=live_by_desk.get(name, True), path=registry.path)
        except desks.DeskError as e:
            if name in required_queue_desks:
                queue_scan_complete = False
            code = e.code
            errors.append({"desk": name, "error": code, "detail": str(e)[:500]})
            registry_ext.stamp_heartbeat(name, live=False, path=registry.path)
        except (kanban_adapter.QueueError, queue_dispatch.QueueDispatchError) as e:
            queue_scan_complete = False
            code = getattr(e, "code", "queue_dispatch_failed")
            errors.append({"desk": name, "error": code, "detail": str(e)[:500]})
            # A queue outage says nothing about whether the named desk is live.
            registry_ext.stamp_heartbeat(name, live=live_by_desk.get(name, True), path=registry.path)
        except QueueCompletionPostFailed as e:
            # ONLY a synchronous final completion post (post_sync_completion,
            # above). Hermes was never marked terminal for it (finish_pending_posted
            # posts BEFORE that mutation), so the claim just ages out and Hermes'
            # own recovery returns the task to its retry phase. This must cost only
            # this desk's cycle, not the whole bridge run. Any other RuntimeError
            # (a conversational delivery, a handle_pending post) is deliberately
            # NOT caught here: it aborts the cycle before save_state, so the turn
            # it was carrying is retried rather than saved as consumed.
            if name in required_queue_desks:
                queue_scan_complete = False
            errors.append({"desk": name, "error": "queue_completion_post_failed", "detail": str(e)[:500]})
            registry_ext.stamp_heartbeat(name, live=live_by_desk.get(name, True), path=registry.path)

    # A complete ready-card scan across every relevant desk makes disappearance
    # or terminal transition observable. Never prune on a busy/failed scan.
    if (queue_executor is not None and required_queue_desks
            and queue_scan_complete
            and queue_scanned_desks == required_queue_desks):
        state_mod.prune_queue_unavailable_since(state, queue_ready_task_ids)

    # SELF-ANNOUNCED SESSIONS (requirement, 2026-09-27: every session posts itself to
    # the room so every other session can reach it). Read presence receipts off
    # this cycle's turns, probe every local session, expire the silent ones,
    # and carry each @-addressed turn into the session it names. Contained: a
    # failure here is a cycle error, never a reason to lose the desk work above.
    session_summary: dict = {"ingested": [], "expired": [], "delivered": []}
    session_rows = None
    try:
        directory = session_directory.load(session_directory_path)
        reserved = set(desk_entries) | {str(e.get("room_seat")) for e in desk_entries.values()
                                        if e.get("room_seat")} | {"hermes", "human", "queue"}
        cycle_host = host or session_presence.this_host()
        cycle_now = now_fn()
        session_summary["ingested"] = session_directory.ingest(
            directory, turns, host=cycle_host, now=cycle_now, reserved=reserved)
        session_summary["expired"] = session_directory.refresh(
            directory, now=cycle_now, host=cycle_host,
            probe=session_probe or session_directory.make_probe())
        session_summary["delivered"] = session_directory.route(
            directory, turns, state, deliver=session_deliver or session_directory.make_deliverer(),
            add_room_turn=add_room_turn)
        session_directory.save(session_directory_path, directory)
        session_rows = session_directory.roster(directory)
    except Exception as exc:  # noqa: BLE001 — contained to the session directory
        errors.append({"desk": "(sessions)", "error": "session_directory_failed",
                       "detail": str(exc)[:500]})

    restarts = settle_restarts(desk_entries, auth_by_desk, state,
                                add_room_turn=add_room_turn, registry=registry,
                                stop=desk_stop, start=desk_start)

    projection_events: list[dict] = []
    if queue_service is not None:
        projection_checked_at = now_fn()
        state["queue_projection_checked_at"] = projection_checked_at
        try:
            projection_events = queue_projector(
                state=state, add_room_turn=project_room_turn,
                target_catalog=queue_service.catalog.get("targets", {}),
                checked_at=projection_checked_at,
            )
            state["queue_projection_last_success_at"] = projection_checked_at
            state["queue_projection_error"] = None
        except Exception as exc:  # projection failure must be visible, never a live-looking board
            state["queue_projection_error"] = "queue_projection_failed"
            errors.append({"desk": "(queue-projector)", "error": "queue_projection_failed",
                           "detail": str(exc)[:500] or "queue projector failed"})

    # Read the registry back AFTER the heartbeat stamps above, so the roster the
    # observatory sees carries this cycle's own liveness rather than the values
    # this cycle started with.
    heartbeat = None
    try:
        # The named-agent roster (loop 520) rides the throttled heartbeat so
        # any feed window carries current profile truth. A roster read that
        # fails must cost the roster, never the heartbeat: profiles=None makes
        # the key honestly absent, and the failure is a reportable cycle error.
        profiles = None
        if heartbeat_due(state):
            try:
                profiles = read_profiles()
            except Exception as e:  # noqa: BLE001 — any fetch failure degrades the same way
                errors.append({"desk": "(profiles)", "error": "profile_roster_unread",
                               "detail": str(e)[:500]})
        heartbeat = post_heartbeat(
            state, registry_ext.all_desks(registry.path),
            add_room_turn=add_room_turn, cursor=state["last_seq"],
            profiles=profiles, sessions=session_rows,
        )
    except RuntimeError as e:
        # A heartbeat that cannot be posted is a reportable cycle error, never a
        # reason to lose the routing and delivery work this cycle already did.
        errors.append({"desk": "(heartbeat)", "error": "heartbeat_post_failed", "detail": str(e)})

    state_mod.save_state(state_path, state)

    # This is deliberately after the ordinary room work, state save, and
    # heartbeat: a bounded fresh Codex execution may run for minutes, but it
    # must never delay the room's own cursor/health truth.  The existing 60s
    # launchd wake is merely an opportunity to drain an already-admitted job;
    # it does not create a second schedule or queue.
    engineering = {"claimed": 0, "completed": 0, "results": []}
    # Keep ad-hoc/manual bridge invocations observational.  The reviewed
    # LaunchAgent explicitly opts in below; this makes the only live claim
    # opportunity visible in its installed configuration and prevents a test
    # or an operator's diagnostic one-shot from unexpectedly leasing work.
    if os.environ.get("CARR_ENGINEERING_DISPATCH_ENABLED") == "true":
        try:
            engineering = engineering_dispatcher()
        except Exception:
            errors.append({"desk": "(engineering-controller)", "error": "engineering_controller_failed",
                           "detail": "engineering controller failed"})

    summary = {
        "turns_read": len(turns), "last_seq": state["last_seq"], "routed": routed,
        "assignments": assignments, "delivered": delivered, "errors": errors,
        "heartbeat": heartbeat, "controls": controls, "restarts": restarts,
        "auth": auth_by_desk, "queue": queue_events,
        "queue_projection": projection_events,
        "queue_reconciliation": queue_reconciliation,
        "engineering": engineering,
        "sessions": session_summary,
    }
    log(f"room-bridge: {len(turns)} turn(s), {len(delivered)} desk action(s), "
        f"{len(assignments)} assignment event(s), {len(queue_events)} queue event(s), {len(controls)} control(s), "
        f"{len(restarts)} restart(s), {len(errors)} error(s), "
        f"heartbeat {'posted' if heartbeat else 'throttled'}; "
        f"cursor now {state['last_seq']}")
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--room", default=DEFAULT_ROOM)
    p.add_argument("--state", default=None)
    p.add_argument("--registry", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run-once", help="one poll cycle — what launchd invokes")
    a = p.parse_args(argv)

    reg = desks.Registry(a.registry) if a.registry else desks.Registry()
    state_path = Path(a.state) if a.state else DEFAULT_STATE

    if a.cmd == "run-once":
        try:
            summary = run_once(
                registry=reg, state_path=state_path, room=a.room,
                add_room_turn=bind_room_writer(verb_io.add_room_turn, a.room),
            )
        except RuntimeError as e:
            print(f"room-bridge: FAILED — {e}", file=sys.stderr)
            return 1
        if summary["errors"]:
            print(f"room-bridge: completed with {len(summary['errors'])} desk error(s)",
                  file=sys.stderr)
            for err in summary["errors"]:
                print(f"  {err['desk']}: {err['error']} — {err['detail']}", file=sys.stderr)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
