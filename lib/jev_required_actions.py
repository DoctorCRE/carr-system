"""Dispatch ownership only; build advisory and required-action enforcement are retired.

The TypeSafe client still binds receipts to the human who dispatched a call.
This module never reads advice or imposes per-turn model obligations.
"""

import hashlib
import json
import re

SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S | re.I)


CONTINUATION_PREFIXES = (
    "Stop hook feedback:",
    "<task-notification>",
    "[SYSTEM NOTIFICATION",
    "[MESSAGE FROM NON-USER SOURCE",
    "Another Claude session sent a message",
    "<cross-session-message",
    "[Cross-session delivery",
    "This session is being continued from a previous conversation",
)


CONTINUATION_ORIGIN_KINDS = ("task-notification", "peer")


SYNTHETIC_USER_PREFIXES = (
    "The following is the Codex agent history",
    "<environment_context>",
    "<app-context>",
)


def _record_message(rec):
    if not isinstance(rec, dict):
        return None, None
    msg = rec.get("message")
    if isinstance(msg, dict):
        return msg, msg.get("role")
    payload = rec.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "message":
        return payload, payload.get("role")
    return None, None


def _first_text_block(msg):
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") in (
                    "text", "input_text", "output_text"):
                value = block.get("text")
                if isinstance(value, str):
                    return value
    return None


def is_real_user_turn(rec):
    """A candidate human boundary: user/human text, excluding the Codex
    history/environment preamble. latest_user_turn_index also excludes
    synthetic continuations, since Stop feedback has the same role and
    text shape as a human prompt. Native prompt IDs alone do not decide it.
    """
    msg, role = _record_message(rec)
    if role not in ("user", "human") or not isinstance(msg, dict):
        return False
    first = _first_text_block(msg)
    if not isinstance(first, str):
        return False
    return not first.lstrip().startswith(SYNTHETIC_USER_PREFIXES)


def is_synthetic_continuation(rec):
    """A record that CONTINUES the current turn rather than starting a new
    one, even though it is role "user" with real, non-empty text: an
    automated Stop-hook reopen ("Stop hook feedback: ..."), a scheduled task
    notification, a cross-session message wrapper, or a message that is
    nothing but system-reminder wrapper text once every
    <system-reminder>...</system-reminder> block is stripped out.

    Found 2026-09-24 (Opus re-replay over real transcripts, PR #1224): a real
    Stop-hook-feedback reopen record is `{"type": "user", "promptId": <SAME
    id as the turn it reopened>, "message": {"role": "user", "content":
    "Stop hook feedback:\\n..."}}` — same shape as a genuine prompt, so a
    plain role check treated it as a fresh turn boundary and the advisory
    fell out of the window on the very next Stop.
    """
    msg, role = _record_message(rec)
    if role not in ("user", "human") or not isinstance(msg, dict):
        return False
    first = _first_text_block(msg)
    if not isinstance(first, str):
        return False
    stripped = first.lstrip()
    if stripped.startswith(CONTINUATION_PREFIXES):
        return True
    # Claude's own origin stamp, when present, is authoritative: a genuine
    # prompt is kind "human"; a background task's notification is
    # "task-notification" and a cross-session message is "peer" (both seen
    # directly in a real session, round 3).
    origin = rec.get("origin") if isinstance(rec, dict) else None
    if isinstance(origin, dict) and origin.get("kind") in CONTINUATION_ORIGIN_KINDS:
        return True
    if isinstance(rec, dict) and rec.get("isCompactSummary"):
        return True
    without_reminders = SYSTEM_REMINDER_RE.sub("", first).strip()
    return bool(first.strip()) and not without_reminders


def latest_user_turn_index(recs):
    """Latest genuine human prompt; -1 for a machine-only transcript."""
    # A promptId is metadata, not a boundary: a client can reuse it, and a
    # notification can mint one. Only a genuine human prompt starts a turn.
    return max((i for i, rec in enumerate(recs or ())
                if is_real_user_turn(rec) and not is_synthetic_continuation(rec)), default=-1)


class HumanTurnScope:
    """Dispatch-time identity of the latest genuine human transcript boundary."""

    def __init__(self, recs, session_id=None):
        recs = tuple(recs or ())
        boundary = latest_user_turn_index(recs)
        human = recs[boundary] if boundary >= 0 else None
        binding = {"session": session_id, "record_index": boundary,
                   "record_id": (human.get("uuid") or human.get("id")) if human else None,
                   "prompt_id": human.get("promptId") if human else None,
                   "timestamp": human.get("timestamp") if human else None}
        self.identity = ("human-turn:v1:" + hashlib.sha256(json.dumps(
            binding, sort_keys=True).encode()).hexdigest()) if human else None



def human_turn_scope(recs, session_id=None):
    return HumanTurnScope(recs, session_id)
