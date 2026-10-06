#!/usr/bin/env python3
"""Closed grammar for the partner room's canonical Hermes queue ingress.

This module interprets no prose and runs no command.  Every trimmed turn that
starts with ``@queue`` is either a validated command or an explicit refusal;
that lets bridge.py consume attempts before a local desk can see them.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


CAPABILITIES = frozenset({
    "read", "repo-write", "record-write", "merge-approve", "production",
    "external-send", "destructive", "credential",
})
HUMAN_ONLY = frozenset({"merge-approve", "production", "external-send", "destructive", "credential"})
TRUSTED_BROWSER_ACTORS = frozenset({"joe", "dell"})
AUTO_TARGET = "auto"  # the Model Room picks the target (QueueService.route_auto)
STATUSES = frozenset({"triage", "todo", "ready", "scheduled", "running", "review", "blocked", "done"})
PRIORITIES = {f"P{n}": 4 - n for n in range(5)}
SLUG = re.compile(r"^[a-z][a-z0-9-]{0,79}$")
TASK_ID = re.compile(r"^t_[A-Za-z0-9_-]{4,128}$")
DURATION = re.compile(r"^([1-9][0-9]*)([mh])$")


@dataclass(frozen=True)
class ParseResult:
    kind: str
    value: dict | None = None
    code: str | None = None
    reason: str | None = None
    hint: str | None = None


def _reject(code: str, reason: str, hint: str = "Use @queue targets") -> ParseResult:
    return ParseResult("rejected", code=code, reason=reason, hint=hint)


def _fields(tokens: list[str], allowed: set[str]) -> tuple[dict | None, ParseResult | None]:
    fields: dict[str, str] = {}
    for token in tokens:
        if token.count("=") != 1:
            return None, _reject("field_malformed", f"field {token!r} must be name=value")
        name, value = token.split("=", 1)
        if not name or not value:
            return None, _reject("field_malformed", f"field {token!r} must have a name and value")
        if name not in allowed:
            return None, _reject("field_unknown", f"field {name!r} is not accepted by @queue")
        if name in fields:
            return None, _reject("field_duplicate", f"field {name!r} was supplied more than once")
        fields[name] = value
    return fields, None


def _duration(value: str) -> bool:
    matched = DURATION.fullmatch(value)
    if not matched:
        return False
    amount, unit = matched.groups()
    seconds = int(amount) * (60 if unit == "m" else 3600)
    return 60 <= seconds <= 8 * 3600


def _target(fields: dict, catalog: dict) -> tuple[dict | None, ParseResult | None]:
    target = fields.get("target", "")
    entry = catalog.get("targets", {}).get(target)
    if not isinstance(entry, dict):
        return None, _reject("target_unknown", f"target {target!r} is not enabled")
    if not entry.get("enabled"):
        return None, _reject("target_disabled", str(entry.get("unavailable_reason") or f"target {target!r} is disabled"))
    return entry, None


def _origin(turn: dict) -> tuple[str | None, ParseResult | None]:
    """Classify authority from server-owned room provenance, never `seat`."""
    channel = turn.get("origin_channel")
    actor = turn.get("origin_actor")
    if channel == "mcp" and isinstance(actor, str) and actor:
        return "model", None
    if channel == "browser-human" and actor in TRUSTED_BROWSER_ACTORS:
        return "browser-human", None
    return None, _reject("origin_untrusted", "queue enqueue requires server-derived MCP or authenticated browser-human provenance")


def _parse_enqueue(turn: dict, head: str, body: str, catalog: dict) -> ParseResult:
    if head.count("::") != 1:
        return _reject("enqueue_malformed", "enqueue requires exactly one '::' before its title")
    before, title = (piece.strip() for piece in head.split("::", 1))
    prefix = "@queue enqueue"
    if not before.startswith(prefix) or (len(before) > len(prefix) and not before[len(prefix)].isspace()):
        return _reject("command_unknown", "expected '@queue enqueue', '@queue status', or '@queue targets'")
    title_len = len(title)
    if not 1 <= title_len <= 200:
        return _reject("title_invalid", "title must be 1 to 200 characters")
    if len(body) > 8000:
        return _reject("body_invalid", "body must be at most 8,000 characters")
    fields, rejected = _fields(before[len(prefix):].split(), {"target", "cap", "priority", "runtime", "key", "after", "finish"})
    if rejected:
        return rejected
    assert fields is not None
    missing = [name for name in ("cap",) if name not in fields]
    if missing:
        return _reject("field_required", f"required field(s) missing: {', '.join(missing)}")
    # No target named: the Model Room routes it (Joe 2026-09-26, agreed with the Orchestrator session). A named
    # target is never rewritten, so a pinned dispatch stays exactly where its sender put it.
    fields.setdefault("target", AUTO_TARGET)
    # target=auto: the Model Room routes the task (ops/jev_model_route.py via QueueService) after parsing, so the
    # grammar checks everything except the target here and the service checks the target it picks.
    auto = fields["target"] == AUTO_TARGET
    entry: dict | None = {"capabilities": list(CAPABILITIES - HUMAN_ONLY)} if auto else None
    if not auto:
        entry, rejected = _target(fields, catalog)
        if rejected:
            return rejected
    assert entry is not None
    cap = fields["cap"]
    if cap not in CAPABILITIES:
        return _reject("capability_unsupported", f"capability {cap!r} is not supported")
    origin, rejected = _origin(turn)
    if rejected:
        return rejected
    assert origin is not None
    if cap in HUMAN_ONLY:
        if auto:
            return _reject("capability_human_lane_required", f"capability {cap!r} may only be queued to the Joe manual lane")
        if origin != "browser-human":
            return _reject("capability_human_only", f"capability {cap!r} is human-only")
        if fields["target"] != "joe" or entry.get("adapter") != "manual":
            return _reject("capability_human_lane_required", f"capability {cap!r} may only be queued to the Joe manual lane")
    if cap not in entry.get("capabilities", []):
        return _reject("capability_target_refused", f"target {fields['target']!r} does not accept cap={cap}")
    priority = fields.get("priority", "P2")
    if priority not in PRIORITIES:
        return _reject("priority_invalid", "priority must be P0 through P4")
    runtime = fields.get("runtime", "8h")
    if not _duration(runtime):
        return _reject("runtime_invalid", "runtime must be between 1m and 8h")
    key = fields.get("key")
    if key is not None and not SLUG.fullmatch(key):
        return _reject("key_invalid", "key must be a lowercase slug")
    after = fields.get("after")
    if after is not None and not TASK_ID.fullmatch(after):
        return _reject("after_invalid", "after must be one Hermes task id")
    finish = fields.get("finish", "done" if cap == "read" else "review")
    if finish not in {"done", "review"}:
        return _reject("finish_invalid", "finish must be done or review")
    msg_id = str(turn.get("msg_id") or "")
    if not msg_id:
        return _reject("source_invalid", "room turn has no message id")
    room = str(turn.get("room") or "partner-line")
    idempotency = f"room:{room}:{key or msg_id}"
    return ParseResult("enqueue", {
        "target": fields["target"], "cap": cap, "priority": PRIORITIES[priority],
        "priority_label": priority, "runtime": runtime, "key": key, "after": after,
        "finish": finish, "title": title, "body": body, "idempotency_key": idempotency,
        "manual": bool(entry.get("adapter") == "manual" and cap in HUMAN_ONLY),
        # server-owned provenance, already validated present by _origin; carried so routing can gate a Flash code
        # run on a trusted origin (kanban_adapter.route_auto), never on `seat`.
        "origin_channel": turn.get("origin_channel"), "origin_actor": turn.get("origin_actor"),
    })


def _parse_status(head: str, body: str, catalog: dict) -> ParseResult:
    if body:
        return _reject("status_malformed", "status commands cannot have a body")
    prefix = "@queue status"
    fields, rejected = _fields(head[len(prefix):].split(), {"id", "state", "target"})
    if rejected:
        return rejected
    assert fields is not None
    if ("id" in fields) == ("state" in fields):
        return _reject("status_malformed", "status requires exactly one of id= or state=")
    if "id" in fields:
        if "target" in fields or not TASK_ID.fullmatch(fields["id"]):
            return _reject("status_malformed", "id status requires one Hermes task id and no target")
    else:
        if fields["state"] not in STATUSES:
            return _reject("state_invalid", "state is not a canonical Hermes status")
        if "target" in fields and fields["target"] not in catalog.get("targets", {}):
            return _reject("target_unknown", f"target {fields['target']!r} is not enabled")
    return ParseResult("status", fields)


def parse(turn: dict, catalog: dict) -> ParseResult:
    """Classify one room turn.  ``not_command`` is the only routable result."""
    raw = str(turn.get("body") or "")
    trimmed = raw.strip()
    if not trimmed.startswith("@queue"):
        return ParseResult("not_command")
    lines = trimmed.splitlines()
    head = lines[0].strip()
    body = "\n".join(lines[1:])
    if head == "@queue targets":
        if body:
            return _reject("targets_malformed", "@queue targets cannot have a body")
        _trusted, rejected = _origin(turn)
        if rejected:
            return rejected
        return ParseResult("targets")
    if head.startswith("@queue enqueue"):
        return _parse_enqueue(turn, head, body, catalog)
    if head.startswith("@queue status"):
        return _parse_status(head, body, catalog)
    return _reject("command_unknown", "expected '@queue enqueue', '@queue status', or '@queue targets'")
