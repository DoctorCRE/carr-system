"""machine_envelope.py — is a prompt ENTIRELY a machine envelope, and of what shape.

Used by the two Jev callers that treat machine prompts differently from partner
messages: ops/jev_build_advisory.py skips build advice on them, and
ops/jev_rule_select.py reuses rule verdicts across envelopes of one shape.

WHOLE-PROMPT, NOT PREFIX (2026-09-25 review of the first cut). A prefix test
skipped build advice on, and pooled rule verdicts for, a notification block
followed by a human instruction, a hand-typed wrapper followed by "ship it to
prod now", "Stop hook feedback: actually, rewrite auth module", and a
hand-typed system-reminder block. So a prompt counts as a machine envelope only
when, after removing every COMPLETE, well-formed envelope block, nothing at
all is left:

  * a <task-notification> block with a <task-id> and a <summary> or <event>,
    closed by </task-notification> — every real one in this repository's
    transcripts (1,094 sampled) strips to nothing;
  * a <cross-session-message ...> block, closed, optionally preceded by the
    exact harness line "Another Claude session sent a message:";
  * <system-reminder> blocks, but only ALONGSIDE one of the two above — a
    prompt that is nothing but reminders is not an envelope, because a reminder
    is trivially typed by hand.

Single-line forms ("Stop hook feedback:", "This session is being continued")
always carry free text after the marker, so they are never envelopes here and
are judged like any other message. Anything left over is partner text.

A LIBRARY: no entrypoint construct (see ops/typesafe_client.py).
"""

import re

_TASK_BLOCK = re.compile(
    r"<task-notification>(?:(?!<task-notification>).)*?</task-notification>", re.S)
_TASK_ID = re.compile(r"<task-id>\s*[^<\s]+\s*</task-id>")
_TASK_SUMMARY = re.compile(r"<(summary|event)>.*?</\1>", re.S)
_CROSS_BLOCK = re.compile(
    r"(?:^|\n)[ \t]*(?:Another Claude session sent a message:[ \t]*\n)?[ \t]*"
    r"<cross-session-message\b[^>]*>(?:(?!<cross-session-message\b).)*?"
    r"</cross-session-message>", re.S)
_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
_STATUS = re.compile(r"<status>\s*(\w+)\s*</status>")
_KIND = re.compile(r"<summary>\s*(\w+)")


def _blocks(text):
    """(remaining text, [shape, ...]) after removing complete envelope blocks."""
    shapes = []

    def task(match):
        block = match.group(0)
        if not (_TASK_ID.search(block) and _TASK_SUMMARY.search(block)):
            return block  # malformed: leave it, so it counts as free text
        kind = _KIND.search(block)
        status = _STATUS.search(block)
        shapes.append("task-notification|{}|{}".format(
            kind.group(1).lower() if kind else "event",
            status.group(1).lower() if status else "event"))
        return ""

    def cross(match):
        shapes.append("cross-session-message")
        return ""

    remaining = _TASK_BLOCK.sub(task, text)
    remaining = _CROSS_BLOCK.sub(cross, remaining)
    if shapes:
        remaining = _REMINDER.sub("", remaining)
    return remaining, shapes


def envelope_shapes(text):
    """The envelope shapes when `text` is ENTIRELY machine envelopes, else None."""
    if not isinstance(text, str):
        return None
    remaining, shapes = _blocks(text)
    if not shapes or remaining.strip():
        return None
    return sorted(shapes)


def is_machine_envelope(text):
    return envelope_shapes(text) is not None
