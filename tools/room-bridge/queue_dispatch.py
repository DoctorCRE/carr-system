#!/usr/bin/env python3
"""Canonical controller for Hermes queue tasks addressed to named desks.

Hermes remains the only task-state authority.  This module reads ready cards,
claims one atomically, delivers it through the existing named-desk wire, and
applies only the task's admitted terminal transition.  It never calls a model
directly, never falls back to another target, and never republishes raw model
output into the partner room. It does expose a bounded typed completion payload
so the bridge can wake the originating room's dispatcher without a human relay.

The claim uses Hermes' canonical 900-second lease.  Hermes
``release_stale_claims`` restores an expired, workerless run to its retry
phase on a dispatcher tick.  This is deliberately the recovery authority: a
second local retry ledger would create a competing task state machine.

Queue metadata is an execution-routing boundary, not a Job Passport
ExecutionEnvelope.  The controller therefore does not fabricate Work Request,
authority, state, or AttemptReceipt records from the small queue header.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable


META_PREFIX = "[CARR_QUEUE_META "
RESULT_PREFIX = "CARR_QUEUE_RESULT "
META_FIELDS = {"v", "target", "cap", "source_seq", "source_msg_id", "finish"}
# kanban_adapter stamps server-derived provenance as an optional field; tasks without it keep working.
META_OPTIONAL = {"origin"}
ORIGIN_VALUE = re.compile(r"[a-z][a-z-]{0,31}:[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
# The desk-facing line carrying that provenance; always the prompt's third line (see _prompt), never body text.
TRUST_PREFIX = "[Model Room trust"
RESULT_FIELDS = {"v", "task_id", "outcome", "summary"}
RECORD_WRITE_EVIDENCE_FIELDS = {"mcp_verb", "record_id", "readback_verb", "readback_record_id"}
TERMINAL_STATES = {"done", "review", "blocked", "archived"}
MCP_VERB = re.compile(r"^[a-z][a-z0-9-]{0,79}$")
QUEUE_TRANSIENT_PREFIX = "queue_transient:"
RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 300
RETRYABLE_DISPATCH_STATUSES = {
    "quota_exhausted": "provider_quota",
    "timed_out": "provider_unavailable",
    "failed": "provider_unavailable",
}
DESK_UNAVAILABLE_WAIT_S = 60.0
MAX_REPLY_CHARS = 4000
REPLY_TRUNCATION_POINTER = "~/.config/carr/hermes-dispatch-results.jsonl"


class QueueDispatchError(ValueError):
    pass


class RecordWriteEvidenceMissing(QueueDispatchError):
    pass


def _now(now: str | None = None) -> datetime:
    value = datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _retry_at(*, attempt: int, now: str | None) -> str:
    delay = min(RETRY_BASE_SECONDS * (2 ** max(0, attempt - 1)), RETRY_MAX_SECONDS)
    return (_now(now) + timedelta(seconds=delay)).isoformat(timespec="seconds")


def _dispatch_failure_code(value: object) -> str:
    if isinstance(value, str) and value in RETRYABLE_DISPATCH_STATUSES:
        return RETRYABLE_DISPATCH_STATUSES[value]
    return "provider_unavailable"


def validate_execution_catalog(catalog: dict) -> dict:
    targets = catalog.get("targets") if isinstance(catalog, dict) else None
    if catalog.get("v") != 1 or not isinstance(targets, dict):
        raise QueueDispatchError("execution catalog must be version 1")
    for alias, target in targets.items():
        if not isinstance(alias, str) or not isinstance(target, dict):
            raise QueueDispatchError("execution catalog target is invalid")
        adapter = target.get("adapter")
        assignee = target.get("assignee")
        if adapter not in {"desk", "hermes", "manual"} or not isinstance(assignee, str):
            raise QueueDispatchError(f"target {alias!r} has no supported adapter")
        if adapter == "desk":
            if not assignee.startswith("desk:") or not isinstance(target.get("desk"), str):
                raise QueueDispatchError(f"desk target {alias!r} is not named explicitly")
        elif assignee.startswith("desk:"):
            raise QueueDispatchError(f"non-desk target {alias!r} cannot use a desk assignee")
    return catalog


def _decode_exact(raw: str, fields: set[str], label: str) -> dict:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise QueueDispatchError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict) or set(value) != fields:
        raise QueueDispatchError(f"{label} fields are invalid")
    return value


def _decode_meta(raw: str) -> dict:
    """The six required metadata fields, plus the optional server-stamped `origin` when present and well-formed."""
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise QueueDispatchError("queue task metadata is not valid JSON") from exc
    if not isinstance(value, dict) or not META_FIELDS <= set(value) <= META_FIELDS | META_OPTIONAL:
        raise QueueDispatchError("queue task metadata fields are invalid")
    if "origin" in value and not (isinstance(value["origin"], str) and ORIGIN_VALUE.fullmatch(value["origin"])):
        raise QueueDispatchError("queue task metadata fields are invalid")
    return value


def parse_queue_task(task: dict, target_alias: str, target: dict) -> dict:
    task_id = task.get("id")
    body = task.get("body")
    if not isinstance(task_id, str) or not task_id.startswith("t_") or not isinstance(body, str):
        raise QueueDispatchError("queue task identity or body is invalid")
    first, separator, instructions = body.partition("\n")
    if not separator or not first.startswith(META_PREFIX) or not first.endswith("]"):
        raise QueueDispatchError("queue task metadata is absent")
    meta = _decode_meta(first[len(META_PREFIX):-1])
    if (meta["v"] != 1 or meta["target"] != target_alias or
            meta["cap"] not in target.get("capabilities", [])):
        raise QueueDispatchError("queue task metadata does not match its target")
    if meta["finish"] not in {"done", "review"}:
        raise QueueDispatchError("queue task finish state is invalid")
    if task.get("assignee") != target.get("assignee") or target.get("adapter") != "desk":
        raise QueueDispatchError("queue task assignee does not match its named desk")
    if not isinstance(meta["source_msg_id"], str) or not meta["source_msg_id"]:
        raise QueueDispatchError("queue task source identity is invalid")
    if (not isinstance(meta["source_seq"], int) or isinstance(meta["source_seq"], bool)
            or meta["source_seq"] < 0):
        raise QueueDispatchError("queue task source sequence is invalid")
    if not isinstance(instructions, str) or not instructions.strip():
        raise QueueDispatchError("queue task instructions are empty")
    title = task.get("title")
    if not isinstance(title, str) or not title.strip():
        raise QueueDispatchError("queue task title is invalid")
    return {"task_id": task_id, "title": title.strip(), "instructions": instructions.strip(), "meta": meta}


def parse_terminal_result(raw: str, task_id: str, cap: str = "read") -> dict:
    if not isinstance(raw, str):
        raise QueueDispatchError("terminal result is absent")
    lines = [line.strip() for line in raw.rstrip().splitlines() if line.strip()]
    if not lines or not lines[-1].startswith(RESULT_PREFIX):
        raise QueueDispatchError("terminal result line is absent")
    try:
        value = json.loads(lines[-1][len(RESULT_PREFIX):])
    except json.JSONDecodeError as exc:
        raise QueueDispatchError("terminal result is not valid JSON") from exc
    if not isinstance(value, dict) or not RESULT_FIELDS <= set(value):
        raise QueueDispatchError("terminal result fields are invalid")
    # Real sessions add their own detail fields (pr_url, verbs, gaps,
    # room_seq, ...) next to the required ones: t_24b0a0c6 and t_a4765f1b did
    # their work and were blocked as result_protocol_error only for that.
    # Unknown fields are therefore dropped, never validated or forwarded; the
    # protocol fields themselves stay exact.  ``code`` is a protocol field and
    # belongs only to a blocked outcome.
    known = RESULT_FIELDS | {"code"}
    if cap == "record-write":
        known |= RECORD_WRITE_EVIDENCE_FIELDS
    value = {key: item for key, item in value.items() if key in known}
    if "code" in value and value.get("outcome") != "blocked":
        raise QueueDispatchError("terminal result fields are invalid")
    if cap == "record-write" and value.get("outcome") == "success":
        if not RECORD_WRITE_EVIDENCE_FIELDS <= set(value):
            raise RecordWriteEvidenceMissing("record-write evidence is absent")
    else:
        value = {key: item for key, item in value.items() if key not in RECORD_WRITE_EVIDENCE_FIELDS}
    if value["v"] != 1 or value["task_id"] != task_id:
        raise QueueDispatchError("terminal result belongs to another task")
    if value["outcome"] not in {"success", "blocked"}:
        raise QueueDispatchError("terminal result outcome is invalid")
    summary = value["summary"]
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 500 or "\n" in summary:
        raise QueueDispatchError("terminal result summary is invalid")
    value["summary"] = summary.strip()
    if value["outcome"] == "blocked" and "code" in value:
        if value["code"] != "capability_escalation_required":
            raise QueueDispatchError("terminal result block code is invalid")
    if cap == "record-write" and value["outcome"] == "success":
        for field in RECORD_WRITE_EVIDENCE_FIELDS:
            evidence = value.get(field)
            if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 200 or "\n" in evidence:
                raise RecordWriteEvidenceMissing("record-write evidence is invalid")
        if not MCP_VERB.fullmatch(value["mcp_verb"]) or not MCP_VERB.fullmatch(value["readback_verb"]):
            raise RecordWriteEvidenceMissing("record-write MCP verb evidence is invalid")
        if value["record_id"] != value["readback_record_id"]:
            raise RecordWriteEvidenceMissing("record-write read-back identifies another record")
    return value


def _bounded_reply(raw_result: str) -> str:
    """The desk's own prose: its trailing CARR_QUEUE_RESULT protocol line stripped,
    the rest truncated at MAX_REPLY_CHARS. Not redaction — nothing here scans for or
    removes secrets/PII from the model's own text; it is the desk's reply verbatim,
    just with the protocol line removed and a length bound applied.

    Used for synchronous desks without MCP tools (Flash and Grok). They cannot
    post their own answers into the room while doing the task; this callback
    carries those answers.
    """
    if not isinstance(raw_result, str):
        return "(empty reply)"
    lines = raw_result.rstrip().splitlines()
    if lines and lines[-1].strip().startswith(RESULT_PREFIX):
        lines = lines[:-1]
    text = "\n".join(lines).strip()
    if not text:
        return "(empty reply)"
    if len(text) > MAX_REPLY_CHARS:
        omitted = len(text) - MAX_REPLY_CHARS
        text = (
            text[:MAX_REPLY_CHARS]
            + f"\n... [truncated {omitted} chars; full reply in {REPLY_TRUNCATION_POINTER}]"
        )
    return text


def _task_status(payload: object) -> str | None:
    if isinstance(payload, dict):
        task = payload.get("task")
        if isinstance(task, dict) and isinstance(task.get("status"), str):
            return task["status"]
        if isinstance(payload.get("status"), str):
            return payload["status"]
    return None


class QueueDeskExecutor:
    def __init__(self, *, catalog: dict, adapter):
        self.catalog = validate_execution_catalog(catalog)
        self.adapter = adapter
        self.last_ready_task_ids: set[str] = set()
        self.last_ready_scan_complete = False

    def _target(self, alias: str) -> dict | None:
        target = self.catalog["targets"].get(alias)
        return target if isinstance(target, dict) else None

    @staticmethod
    def _prompt(parsed: dict) -> str:
        task_id = parsed["task_id"]
        source = (
            f"[Model Room source seq {parsed['meta']['source_seq']} "
            f"msg_id {parsed['meta']['source_msg_id']}]\n"
        )
        origin = parsed["meta"].get("origin")
        if isinstance(origin, str) and ORIGIN_VALUE.fullmatch(origin):
            # Server provenance from the adapter-built metadata line: always the prompt's third line, directly after
            # the source line and before the blank line that starts the body, so posted text can never stand here.
            source += f"{TRUST_PREFIX} origin {origin} cap {parsed['meta']['cap']}]\n"
        evidence = ""
        if parsed["meta"]["cap"] == "record-write":
            evidence = (
                " For record-write success, also include bounded mcp_verb, record_id, readback_verb, "
                "and readback_record_id fields in that JSON; no evidence means Review or Blocked, never Done."
            )
        return (
            f"[Hermes queue {task_id}] {parsed['title']}\n{source}\n{parsed['instructions']}\n\n"
            "Your final non-empty line must be exactly one JSON object prefixed with "
            f"CARR_QUEUE_RESULT and must bind task_id={task_id}. Allowed outcomes: success, blocked. "
            "The JSON object must include the exact field \"v\":1. "
            "Keep summary to one redacted sentence of at most 500 characters. If broader authority is needed, "
            "return outcome=blocked with code=capability_escalation_required." + evidence + "\n"
            "Exact shape: CARR_QUEUE_RESULT "
            + json.dumps({"v": 1, "task_id": task_id, "outcome": "success",
                          "summary": "<one sentence>"}, separators=(",", ":"))
            + "\nThe queue reads only these fields; put PR URLs, verbs, gaps and other detail in the summary "
            "or in your reply above that line, not in extra JSON fields."
        )

    def _retry_or_block(self, task_id: str, code: str, *, now: str | None,
                        before_block: Callable[[str], None] | None = None) -> dict:
        """Use only Hermes evidence for the finite recovery bound.

        ``before_block`` runs immediately before any permanent block, and never
        before a retry. The synchronous flash-local path passes its FINAL room
        completion post here, so a retry posts nothing (the retried attempt owns
        the one completion key) and a post failure raises before Hermes is
        marked blocked, leaving the claim for Hermes' own stale-claim recovery.
        """
        def block(reason: str) -> None:
            if before_block is not None:
                before_block(reason)
            self.adapter.block(task_id, reason, kind="transient")

        try:
            attempts, limit = self.adapter.retry_attempt(task_id, QUEUE_TRANSIENT_PREFIX)
            if (not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0
                    or not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
                raise QueueDispatchError("canonical retry evidence is invalid")
        except Exception:
            block("queue_unavailable")
            return {"outcome": "blocked", "task_id": task_id, "code": "queue_unavailable"}
        if attempts + 1 >= limit:
            block(code)
            return {"outcome": "blocked", "task_id": task_id, "code": code}
        try:
            self.adapter.reclaim(task_id, f"{QUEUE_TRANSIENT_PREFIX}{code}")
        except Exception:
            block("queue_unavailable")
            return {"outcome": "blocked", "task_id": task_id, "code": "queue_unavailable"}
        return {"outcome": "retry_scheduled", "task_id": task_id, "code": code,
                "retry_at": _retry_at(attempt=attempts + 1, now=now)}

    def start(self, target_alias: str, *, dispatch_call: Callable[[str], dict],
              desk_busy: bool = False, retry_at: dict[str, str] | None = None,
              now: str | None = None, desk_live: bool = True,
              unavailable_since: dict[str, str] | str | None = None,
              unavailable_wait_s: float = DESK_UNAVAILABLE_WAIT_S,
              include_reply: bool = False, retry_protocol_errors: bool = False,
              post_completion=None, completion_dir: Path | None = None) -> dict:
        target = self._target(target_alias)
        self.last_ready_task_ids = set()
        self.last_ready_scan_complete = False
        if target is None or target.get("adapter") != "desk" or not target.get("enabled"):
            return {"outcome": "not_desk_target", "target": target_alias}
        if desk_busy:
            return {"outcome": "desk_busy", "target": target_alias}

        candidates = []
        for row in self.adapter.ready_for(target["assignee"]):
            if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"].startswith("t_"):
                self.last_ready_task_ids.add(row["id"])
            try:
                parsed = parse_queue_task(row, target_alias, target)
            except QueueDispatchError:
                continue
            candidates.append((int(row.get("created_at") or 0), parsed["task_id"], parsed))
        self.last_ready_scan_complete = True
        if not candidates:
            return {"outcome": "idle", "target": target_alias}
        current_time = _now(now)
        ready_candidates = []
        delayed_candidates = []
        for candidate in candidates:
            scheduled_at = (retry_at or {}).get(candidate[1])
            if scheduled_at is None:
                ready_candidates.append(candidate)
                continue
            if not isinstance(scheduled_at, str):
                self.adapter.block(candidate[1], "queue_unavailable", kind="transient")
                return {"outcome": "blocked", "task_id": candidate[1], "code": "queue_unavailable"}
            try:
                due = _now(scheduled_at)
            except (TypeError, ValueError):
                self.adapter.block(candidate[1], "queue_unavailable", kind="transient")
                return {"outcome": "blocked", "task_id": candidate[1], "code": "queue_unavailable"}
            if due > current_time:
                delayed_candidates.append((due, candidate, scheduled_at))
            else:
                ready_candidates.append(candidate)
        if not ready_candidates:
            due, candidate, scheduled_at = min(delayed_candidates)
            return {"outcome": "retry_wait", "task_id": candidate[1], "target": target_alias,
                    "retry_at": scheduled_at}
        _created, task_id, parsed = min(ready_candidates)
        receipt = self._read_final(completion_dir, parsed)

        # A socket-backed desk that is known dead gets a timing-only grace
        # window.  Crucially this happens before claim, so no Hermes attempt,
        # dispatch, retry, or reassignment is manufactured while waiting.
        if desk_live is False and receipt is None:
            task_unavailable_since = (
                unavailable_since.get(task_id) if isinstance(unavailable_since, dict)
                else unavailable_since
            )
            if not isinstance(unavailable_wait_s, (int, float)) or isinstance(unavailable_wait_s, bool) \
                    or unavailable_wait_s <= 0 or unavailable_wait_s > 3600:
                self.adapter.block(task_id, "queue_unavailable", kind="transient")
                return {"outcome": "blocked", "task_id": task_id, "code": "queue_unavailable"}
            if task_unavailable_since is None:
                return {"outcome": "desk_unavailable_wait", "task_id": task_id,
                        "target": target_alias, "unavailable_since": _now(now).isoformat()}
            try:
                first_dead = _now(task_unavailable_since)
            except (TypeError, ValueError, OverflowError):
                self.adapter.block(task_id, "queue_unavailable", kind="transient")
                return {"outcome": "blocked", "task_id": task_id, "code": "queue_unavailable"}
            elapsed = (_now(now) - first_dead).total_seconds()
            if elapsed < 0:
                self.adapter.block(task_id, "queue_unavailable", kind="transient")
                return {"outcome": "blocked", "task_id": task_id, "code": "queue_unavailable"}
            if elapsed < unavailable_wait_s:
                return {"outcome": "desk_unavailable_wait", "task_id": task_id,
                        "target": target_alias, "unavailable_since": task_unavailable_since}
            self.adapter.block(task_id, "desk_unavailable", kind="transient")
            return {"outcome": "blocked", "task_id": task_id, "code": "desk_unavailable"}

        # The canonical claim is the race boundary. No dispatch happens first.
        try:
            self.adapter.claim(task_id)
        except Exception:
            # Another controller may have won the atomic claim, or Hermes may
            # have become unavailable. Either way, dispatching would be wrong.
            return {"outcome": "claim_not_acquired", "task_id": task_id, "target": target_alias}
        if receipt is not None:
            if post_completion is None:
                raise QueueDispatchError("saved completion requires its publication route")
            return self._apply_final(receipt, post_completion)
        try:
            row = dispatch_call(self._prompt(parsed))
        except Exception as exc:  # dispatch details may contain provider output; never persist them here
            error_code = getattr(exc, "code", None)
            if error_code == "queue_unavailable":
                code = "queue_unavailable"
            elif isinstance(error_code, str):
                code = "desk_unavailable"
            else:
                code = "provider_unavailable"
            return self._retry_or_block(task_id, code, now=now)

        status = row.get("status") if isinstance(row, dict) else None
        if status == "delivered":
            session_id = row.get("session_id")
            transport = row.get("transport")
            return {
                "outcome": "pending", "task_id": task_id, "target": target_alias,
                "pending": {
                    "origin_kind": "queue", "kanban_task_id": task_id,
                    "target": target_alias, "finish": parsed["meta"]["finish"],
                    "cap": parsed["meta"]["cap"],
                    "source_seq": parsed["meta"]["source_seq"],
                    "source_msg_id": parsed["meta"]["source_msg_id"],
                    "dispatch_msg_id": row.get("msg_id"),
                    "injected_at": row.get("dispatched_at"),
                    **({"session_id": session_id} if isinstance(session_id, str) else {}),
                    **({"transport": transport} if isinstance(transport, str) else {}),
                },
            }
        if status != "completed":
            # A no-answer reply is a distinct, honestly-labeled failure, not a
            # generic provider outage: flash_wire is the only dispatch_call
            # that ever sets detail="no_answer" (an empty Flash reply), so
            # this stays desk-agnostic and only ever fires for that case. The
            # route's `then` desk in ops/config/model-routes.v1.json is NOT
            # dispatched to here — see finish_pending's retry_protocol_errors
            # for why a real cross-desk hand-off is out of this module's
            # bounded scope today; this still only retries then blocks, under
            # its own diagnosable code instead of a misleading one.
            detail = row.get("detail") if isinstance(row, dict) else None
            code = "no_answer" if detail == "no_answer" else _dispatch_failure_code(status)
            return self._retry_or_block(task_id, code, now=now)
        raw_result = row.get("result")
        pending = {"kanban_task_id": task_id, "target": target_alias, "finish": parsed["meta"]["finish"],
                   "cap": parsed["meta"]["cap"], "source_seq": parsed["meta"]["source_seq"],
                   "source_msg_id": parsed["meta"]["source_msg_id"],
                   "dispatch_msg_id": row.get("msg_id")}
        if include_reply and isinstance(row.get("provider_metadata"), dict):
            # The wire validates provider identity. Keep only its declared
            # receipt fields; arbitrary provider diagnostics never enter the room.
            pending["provider_metadata"] = {
                key: row["provider_metadata"][key] for key in (
                    "requested_model", "actual_model", "effort", "request_id",
                    "session_id", "model_calls", "cost_usd", "stop_reason")
                if key in row["provider_metadata"]
            }
        clean_result = raw_result if isinstance(raw_result, str) else ""
        if post_completion is not None:
            return self.finish_pending_posted(
                pending, clean_result, post_completion=post_completion,
                include_reply=include_reply, retry_protocol_errors=retry_protocol_errors, now=now,
                completion_dir=completion_dir,
            )
        return self.finish_pending(
            pending, clean_result,
            include_reply=include_reply, retry_protocol_errors=retry_protocol_errors, now=now,
        )

    @staticmethod
    def _final_path(completion_dir: Path, task_id: str) -> Path:
        return Path(completion_dir) / (hashlib.sha256(task_id.encode()).hexdigest() + ".json")

    def _read_final(self, completion_dir: Path | None, parsed: dict) -> dict | None:
        if completion_dir is None:
            return None
        try:
            receipt = json.loads(self._final_path(completion_dir, parsed["task_id"]).read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise QueueDispatchError("saved completion is unreadable") from exc
        expected = {"kanban_task_id": parsed["task_id"], "target": parsed["meta"]["target"],
                    **{key: parsed["meta"][key] for key in
                       ("finish", "cap", "source_seq", "source_msg_id")}}
        completion = receipt.get("completion") if isinstance(receipt, dict) else None
        callback = completion.get("queue_completion") if isinstance(completion, dict) else None
        if (not isinstance(receipt, dict) or receipt.get("v") != 1
                or receipt.get("binding") != expected
                or receipt.get("method") not in {"complete", "request_review", "block"}
                or not isinstance(receipt.get("summary"), str)
                or not isinstance(receipt.get("metadata"), dict)
                or not isinstance(receipt.get("outcome"), str)
                or receipt.get("block_kind") not in {None, "transient", "needs_input"}
                or not isinstance(callback, dict)
                or any(callback.get(key) != parsed["meta"][key] for key in
                       ("target", "source_seq", "source_msg_id"))
                or callback.get("task_id") != parsed["task_id"]):
            raise QueueDispatchError("saved completion binding is invalid")
        return receipt

    def _save_final(self, completion_dir: Path | None, receipt: dict) -> None:
        if completion_dir is None:
            return
        path = self._final_path(completion_dir, receipt["binding"]["kanban_task_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write ahead of publication, including an uncertain post response.
        # Preserve JSON key order: the room compares the serialized body.
        temp = path.with_suffix(".tmp")
        with temp.open("w") as stream:
            stream.write(json.dumps(receipt, separators=(",", ":")))
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _apply_final(self, receipt: dict, post_completion) -> dict:
        task_id = receipt["binding"]["kanban_task_id"]
        result = {"outcome": receipt["outcome"], "task_id": task_id,
                  "completion": receipt["completion"]}
        if receipt["block_kind"] == "transient":
            result["code"] = receipt["summary"]
        if _task_status(self.adapter.show(task_id)) in TERMINAL_STATES:
            return {**result, "outcome": "already_terminal"}
        if post_completion is not None:
            post_completion(receipt["completion"])
        if receipt["method"] == "block":
            self.adapter.block(task_id, receipt["summary"], kind=receipt["block_kind"])
        else:
            getattr(self.adapter, receipt["method"])(task_id, receipt["summary"], receipt["metadata"])
        return result

    @staticmethod
    def completion_payload(pending: dict, raw_result: str, *, include_reply: bool = False) -> dict:
        """Return the bounded callback contract.

        Never return model prose — EXCEPT when ``include_reply`` is set by the
        synchronous Flash/Grok desk paths (see start()/finish_pending()). A
        codex-session or claude-session desk has its own MCP tools and posts its own reply into the room
        as part of doing the task, so the "never return model prose" rule holds for it
        unchanged; Flash/Grok have no tools, so their replies would otherwise be lost.
        ``include_reply`` is the one bounded exception carrying it back — its
        protocol result line stripped and truncated (_bounded_reply), not redacted.
        """
        task_id = pending.get("kanban_task_id")
        if not isinstance(task_id, str) or not task_id.startswith("t_"):
            raise QueueDispatchError("pending queue task identity is invalid")
        cap = str(pending.get("cap") or "read")
        try:
            terminal = parse_terminal_result(raw_result, task_id, cap)
        except RecordWriteEvidenceMissing:
            terminal = {
                "outcome": "blocked", "summary": "record_write_evidence_missing",
                "code": "record_write_evidence_missing",
            }
        except QueueDispatchError:
            terminal = {
                "outcome": "blocked", "summary": "result_protocol_error",
                "code": "result_protocol_error",
            }
        callback = {
            "v": 1,
            "task_id": task_id,
            "target": pending.get("target"),
            "outcome": terminal["outcome"],
            "summary": terminal["summary"],
            "source_seq": pending.get("source_seq"),
            "source_msg_id": pending.get("source_msg_id"),
            "dispatcher_instruction": (
                "Continue the originating workflow autonomously within its existing authority. "
                "Consume this result and take the next permitted coordination step; do not merely acknowledge."
            ),
        }
        if isinstance(terminal.get("code"), str):
            callback["code"] = terminal["code"]
        if cap == "record-write" and terminal["outcome"] == "success":
            callback["record_write"] = {
                field: terminal[field] for field in RECORD_WRITE_EVIDENCE_FIELDS
            }
        if include_reply:
            callback["reply"] = _bounded_reply(raw_result)
            if isinstance(pending.get("dispatch_msg_id"), str):
                callback["dispatch_msg_id"] = pending["dispatch_msg_id"]
            if isinstance(pending.get("provider_metadata"), dict):
                callback["provider_metadata"] = pending["provider_metadata"]
        return {"queue_completion": callback}

    def finish_pending(self, pending: dict, raw_result: str, *, include_reply: bool = False,
                       retry_protocol_errors: bool = False, now: str | None = None) -> dict:
        return self._finish(
            pending, raw_result, include_reply=include_reply,
            retry_protocol_errors=retry_protocol_errors, now=now, post_completion=None,
        )

    def finish_pending_posted(self, pending: dict, raw_result: str, *, post_completion,
                              include_reply: bool = False, retry_protocol_errors: bool = False,
                              now: str | None = None, completion_dir: Path | None = None) -> dict:
        """Like finish_pending, but posts the room completion callback BEFORE any Hermes
        terminal transition, never after.

        Synchronous desks without MCP tools (start()) need this. The bridge supplies
        completion_dir to persist the exact final callback and terminal operation
        before publication. Once Hermes releases a stale claim, start() claims it
        again and replays that receipt without dispatching another provider call.
        This is publication evidence, not a second task-state or retry authority.

        Posting first instead means a failed post (post_completion raises, and the
        exception propagates to the caller UNCHANGED) leaves the Hermes claim in
        place — never marked done/blocked/review — so the task stays "running" until
        Hermes' own release_stale_claims puts it back in the retry phase on its own
        schedule. That is the SAME recovery authority every other transient dispatch
        failure in this module already relies on (see the module docstring), not a
        second local retry ledger.

        Only a FINAL completion is ever posted: after the result line parses, or
        when the task is being permanently blocked (a non-retryable failure, or a
        retryable protocol error whose Hermes retry budget is spent). A protocol
        error that schedules a retry posts nothing. The room callback has exactly
        one idempotency key per task (queue-completion:<task_id>) and the server
        rejects that key with a different body (key_reuse), so posting an interim
        "blocked" before a retry would both show the room a false result and make
        the retried attempt's real completion unpostable forever (PR #1254 round
        2 review, finding 1). A crash between a successful post and the Hermes
        transition re-posts the identical final body, which the server replays.
        """
        return self._finish(
            pending, raw_result, include_reply=include_reply,
            retry_protocol_errors=retry_protocol_errors, now=now, post_completion=post_completion,
            completion_dir=completion_dir,
        )

    def _finish(self, pending: dict, raw_result: str, *, include_reply: bool,
               retry_protocol_errors: bool, now: str | None, post_completion,
               completion_dir: Path | None = None) -> dict:
        task_id = pending.get("kanban_task_id")
        if not isinstance(task_id, str) or not task_id.startswith("t_"):
            raise QueueDispatchError("pending queue task identity is invalid")
        completion = self.completion_payload(pending, raw_result, include_reply=include_reply)

        def final_receipt(method: str, summary: str, outcome: str,
                          metadata: dict | None = None, kind: str | None = None) -> dict:
            receipt = {"v": 1, "binding": {key: pending.get(key) for key in
                       ("kanban_task_id", "target", "finish", "cap", "source_seq", "source_msg_id")},
                       "completion": completion, "method": method, "summary": summary,
                       "metadata": metadata or {}, "block_kind": kind, "outcome": outcome}
            if post_completion is not None:
                self._save_final(completion_dir, receipt)
            return receipt

        def finish(method: str, summary: str, outcome: str,
                   metadata: dict | None = None, kind: str | None = None) -> dict:
            return self._apply_final(final_receipt(method, summary, outcome, metadata, kind), post_completion)

        def result(outcome: str) -> dict:
            return {"outcome": outcome, "task_id": task_id, "completion": completion}

        current = _task_status(self.adapter.show(task_id))
        if current in TERMINAL_STATES:
            return result("already_terminal")
        try:
            terminal = parse_terminal_result(raw_result, task_id, str(pending.get("cap") or "read"))
        except RecordWriteEvidenceMissing:
            metadata = {"queue_protocol": "carr-queue-result.v1", "target": pending.get("target"),
                        "outcome": "unverified", "verification": "record_write_evidence_missing"}
            if pending.get("finish") == "review":
                return finish("request_review", "record_write_evidence_missing", "review", metadata)
            return finish("block", "record_write_evidence_missing", "record_write_evidence_missing",
                          kind="needs_input")
        except QueueDispatchError:
            # A desk with no MCP tools of its own (flash-local) is far more likely
            # to fumble the exact trailing-line protocol than a codex-session or
            # claude-session desk, which write it themselves as one more tool
            # call. retry_protocol_errors (set only for flash-local, see start())
            # gives it the same bounded retry-then-block every other transient
            # failure gets here, instead of a permanent block on the first miss.
            # A scheduled retry posts NOTHING to the room; only the exhausted,
            # permanent block posts the final completion (see
            # finish_pending_posted). This is NOT the cross-desk hand-off to the
            # route's `then` desk that flash_wire.py's docstring and
            # ops/config/model-routes.v1.json describe: Hermes'
            # CARR_QUEUE_META.target is fixed in the task body at creation and
            # validated against the claiming desk's own alias (parse_queue_task),
            # so handing a task to a different desk would need a new, linked task
            # rather than a reassignment of this one — tracked as loop #649.
            if retry_protocol_errors:
                def before_block(reason: str) -> None:
                    receipt = final_receipt("block", reason, "blocked", kind="transient")
                    if post_completion is not None:
                        post_completion(receipt["completion"])
                return self._retry_or_block(
                    task_id, "result_protocol_error", now=now, before_block=before_block)
            return finish("block", "result_protocol_error", "result_protocol_error")

        summary = terminal["summary"]
        metadata = {
            "queue_protocol": "carr-queue-result.v1",
            "target": pending.get("target"),
            "outcome": terminal["outcome"],
        }
        if pending.get("cap") == "record-write":
            metadata["record_write"] = {key: terminal[key] for key in RECORD_WRITE_EVIDENCE_FIELDS}
        if terminal["outcome"] == "blocked":
            return finish("block", terminal.get("code") or summary, "blocked", kind="needs_input")
        if pending.get("finish") == "review":
            return finish("request_review", summary, "review", metadata)
        if pending.get("finish") != "done":
            return finish("block", "result_protocol_error", "result_protocol_error")
        return finish("complete", summary, "done", metadata)

    def fail_pending(self, pending: dict, reason: str, *, now: str | None = None) -> dict:
        task_id = pending.get("kanban_task_id")
        if not isinstance(task_id, str) or not task_id.startswith("t_"):
            raise QueueDispatchError("pending queue task identity is invalid")
        current = _task_status(self.adapter.show(task_id))
        if current in TERMINAL_STATES:
            return {"outcome": "already_terminal", "task_id": task_id}
        safe_reason = "desk_result_timeout" if reason == "desk_result_timeout" else "provider_unavailable"
        return self._retry_or_block(task_id, safe_reason, now=now)
