#!/usr/bin/env python3
"""grammar.py — the assignment bridge's one deterministic decision.

Joe's ruled design test: CODE DECIDES, MODELS ONLY AT JUDGMENT BOUNDARIES. A
room turn either matches this grammar exactly or it does not; nothing here
ever asks a model to interpret intent. Two forms, both one line:

    @<seat> assign <ref> <one-line title>
    @<seat> claim <ref>

LEGACY SCOPE. The canonical queue now enters through ``@queue`` and Hermes;
these old ``@<seat> assign|claim`` commands are retained only so their senders
receive an explicit deprecation receipt. They never create a local JSON
assignment log and cannot infer a safe queue capability or task body.

AUTHORIZATION. Only a turn whose `seat` is "human" may trigger a legacy
assignment deprecation receipt.
A model seat (claude, codex, grok, sol, hermes) can echo or paraphrase a
command in the room, and this bridge must not treat that as instruction —
only a person typing it counts. Model seats enqueue their permitted work
through the separately closed ``@queue`` grammar.

THREE OUTCOMES, so a near-miss is never silently ignored:
  "not_a_command"  — body does not look like an attempt at this grammar at
                      all; do nothing, no receipt (an ordinary chat turn that
                      happens to start with '@' is not spammed).
  "malformed"       — body opens with "@<word> assign|claim" but fails the
                      strict grammar (missing ref, missing title, extra
                      lines, bad ref shape). Rejected, with a receipt saying
                      why — the sender typed a command and deserves to know
                      it did not parse.
  "unauthorized"    — the grammar matched perfectly but the sending seat is
                      not authorized. Rejected, with a receipt naming the
                      seat.
  "ok"              — matched and authorized; apply_assignment() proceeds.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

AUTHORIZED_SEATS = frozenset({"human"})

_SEAT = r"[a-z][a-z0-9-]{0,31}"
_REF = r"[A-Za-z][A-Za-z0-9_-]{0,40}"

_LOOSE_TRIGGER = re.compile(rf"^@\S+\s+(assign|claim)\b", re.IGNORECASE)
_ASSIGN_RE = re.compile(rf"^@(?P<seat>{_SEAT})\s+assign\s+(?P<ref>{_REF})\s+(?P<title>\S.{{0,199}})$")
_CLAIM_RE = re.compile(rf"^@(?P<seat>{_SEAT})\s+claim\s+(?P<ref>{_REF})$")

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def classify(turn: dict) -> tuple[str, dict | None, str | None]:
    """(outcome, parsed, reason). parsed is {"verb","target_seat","ref","title"}."""
    body = str(turn.get("body") or "")
    line = body.strip()
    if "\n" in body.strip("\n"):
        if _LOOSE_TRIGGER.match(line.splitlines()[0] if line else ""):
            return "malformed", None, "a command must be exactly one line; this turn has more than one"
        return "not_a_command", None, None
    if not _LOOSE_TRIGGER.match(line):
        return "not_a_command", None, None

    m = _ASSIGN_RE.match(line)
    if m:
        parsed = {"verb": "assign", "target_seat": m.group("seat"),
                  "ref": m.group("ref"), "title": m.group("title").strip()}
    else:
        m = _CLAIM_RE.match(line)
        if m:
            parsed = {"verb": "claim", "target_seat": m.group("seat"),
                      "ref": m.group("ref"), "title": None}
        else:
            return "malformed", None, (
                "looked like '@<seat> assign|claim ...' but did not match the strict "
                "grammar — expected '@<seat> assign <ref> <one-line title>' or "
                "'@<seat> claim <ref>'"
            )

    sender_seat = str(turn.get("seat") or "")
    if sender_seat not in AUTHORIZED_SEATS:
        return "unauthorized", parsed, (
            f"seat {sender_seat!r} is not authorized to trigger an assignment "
            f"(authorized: {sorted(AUTHORIZED_SEATS)})"
        )
    return "ok", parsed, None


def apply_assignment(turn: dict, parsed: dict, *, add_room_turn) -> dict:
    """Deprecate the local assignment-log pseudo-queue without replacing it.

    The canonical path is now ``@queue enqueue ... cap=read``.  This legacy
    grammar cannot safely infer a queue title/body/capability, so it does not
    translate automatically and, critically, writes no second task ledger.
    """
    record = {
        "assignment_deprecated": {
            "seat": parsed["target_seat"],
            "verb": parsed["verb"],
            "ref": parsed["ref"],
            "title": parsed["title"],
            "by": turn.get("sponsor"),
            "at_seq": turn.get("seq"),
            "source_msg_id": turn.get("msg_id"),
            "deprecated_at": _now(),
        },
        "status": "deprecated_use_queue",
        "note": "Use @queue enqueue target=<alias> cap=read :: <title>. No local assignment log was written.",
    }
    receipt = add_room_turn(
        body=json.dumps(record, separators=(",", ":")),
        seat="hermes",
        kind="receipt",
    )
    return {"record": record, "receipt": receipt}


def reject(turn: dict, outcome: str, reason: str, *, add_room_turn) -> dict:
    """Post the 'why nothing happened' receipt for malformed/unauthorized
    attempts. Never called for not_a_command — that is not an attempt."""
    body = json.dumps({
        "assignment_rejected": {
            "outcome": outcome,
            "reason": reason,
            "source_seat": turn.get("seat"),
            "source_seq": turn.get("seq"),
            "source_msg_id": turn.get("msg_id"),
        },
    }, separators=(",", ":"))
    return add_room_turn(body=body, seat="hermes", kind="receipt")
