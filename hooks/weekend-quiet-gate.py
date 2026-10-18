#!/usr/bin/env python3
"""weekend-quiet-gate.py — Saturday and Sunday are not workdays (rule 236ca227).

THE RULE: weekends are off for both partners. Joe and Dell do not work them,
and the system does not go looking for them on one.

THE HOLE THIS CLOSES, from the 2026-08-14 enforceability audit and verified
still open the moment before this was written. The session brief STATES the
policy at boot, and nothing denied a weekend ping. The record layer has no send
verb at all — that half is structurally absent, which the audit records — but a
session reaches the partners through tools the record layer never sees: a push
notification, and the mail connector that drafts and sends to Joe's own
address. No hook matcher covered either, and no code anywhere looked at the day
of the week.

WHY THIS ONE IS ENFORCEABLE WHERE MOST OF ITS NEIGHBOURS ARE NOT. Most of the
audit's partial rows are advisory because their binding condition needs
judgment. "Is it Saturday" is a predicate (rule 5e89c211: never spend a
cognition token on a decision a predicate can make).

THE EXCEPTION IS THE LOAD-BEARING PART, and it is the rule's own: "human elects
to work weekend". A partner typing into a session on a Sunday IS that election
— he is at the keyboard, he opened the session, and refusing to answer him
would be absurd. So this reads his keystrokes; a session cannot grant itself
the exemption by asserting he is around.

SCOPE IS DELIBERATELY NARROW: partner-facing SENDS only. Reads, record writes,
loop filing, peer messages between sessions, and every other tool are untouched
whatever the day. The weekend rule is about not pestering two people on their
days off, not about stopping the system from working — the nightly chain, the
scheduled jobs and the record layer all run through the weekend as before, and
should.

FAILS OPEN on every error, including a clock it cannot read: a gate that cannot
tell the day must not guess, and a wedged session costs more than one push
notification arriving on a Saturday.

Fixtures: ops/weekend-quiet-gate-selftest.py.
"""

import json
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:                                    # telemetry only — never load-bearing
    import hook_meter
    DEBUG = hook_meter.guard_log_path(REPO)
except Exception:                       # a missing meter must not change a verdict
    DEBUG = os.path.join(REPO, "out", "hook-guard.log")

# Both partners work the Florida Panhandle and South Alabama, which is Central.
# Same zone the rest of the repo uses.
ZONE = ZoneInfo("America/Chicago")

# The tools that actually reach a partner. Matched on the tool NAME, so a new
# mail connector with a different server id is still caught by the verb half.
SEND_TOOLS = re.compile(
    r"^PushNotification$"
    r"|^mcp__[a-z0-9-]+__(send_message|create_draft|update_draft)$",
    re.I)


from turn_origin import partner_typed_this  # noqa: E402


def dlog(msg):
    try:
        os.makedirs(os.path.dirname(DEBUG), exist_ok=True)
        with open(DEBUG, "a") as fh:
            fh.write(f"weekend-quiet {msg}\n")
    except Exception:
        pass


def now():
    """The clock, injectable so the fixtures are date-stable. Returns None when
    it cannot be read, which the caller treats as fail-open."""
    override = os.environ.get("CARR_NOW")
    if override:
        try:
            return datetime.fromisoformat(override).astimezone(ZONE)
        except ValueError:
            return None
    return datetime.now(ZONE)


def text_of(rec):
    """The record's human-visible text, or empty when it carries none."""
    msg = rec.get("message") or rec
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                          if isinstance(b, dict) and b.get("type") == "text")
    return ""


def partner_is_here(transcript_path):
    """True when a partner has typed in this session.

    His own keystrokes are the election the rule names, and they are the one
    signal a session cannot fabricate for itself. Harness-injected turns are
    NOT his words.

    THE ORIGIN ALLOWLIST WAS GUESSED, AND THE GUESS WAS WRONG (defect 16291f00,
    2026-08-22). It accepted kinds "user" and "keyboard". The harness has never
    written either: a typed turn carries origin {"kind": "human"}. So every real
    keystroke was rejected as non-keyboard, the carve-out could not fire once,
    and the gate refused a notification on a Saturday Joe had personally
    authorised two turns earlier. The selftest passed throughout because its own
    fixture wrote {"kind": "user"} — the code's assumption, checked against
    itself. Measured across sixteen real transcripts, the accepted set matched
    zero turns out of 3,271 candidate records.

    SO THE VALUES BELOW ARE OBSERVED, NOT ASSUMED, and they stay an allowlist
    rather than becoming a denylist of injected kinds. A denylist fails OPEN:
    an origin kind nobody has seen yet would count as the partner speaking, and
    a session could self-authorise its own weekend send off a notification —
    exactly the 2026-08-05 mistake. An unknown kind is refused AND LOGGED, so
    the next drift is visible in out/hook-guard.log rather than silent for a
    week. That logging is the actual repair; the missing value is just the bug.
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return False
    try:
        with open(transcript_path, errors="replace") as fh:
            lines = fh.readlines()[-400:]
    except Exception:
        return False
    for line in lines:
        try:
            rec = json.loads(line.strip())
        except Exception:
            continue
        if rec.get("type") not in ("user", "human"):
            continue
        if rec.get("isMeta") or rec.get("isCompactSummary"):
            continue
        # The shared detector decides. It screens harness commentary, injected
        # content shapes, cross-session relays and any origin kind that is not
        # exactly "human" — and it defaults to NOT the partner, so a shape
        # nobody has met yet keeps the weekend quiet instead of ending it.
        if not partner_typed_this(rec, text_of(rec)):
            continue
        return True
    return False


REASON = (
    "WEEKEND QUIET — refused. It is {day}, and weekends are off for both "
    "partners (rule 236ca227). Joe and Dell do not work them, and neither of "
    "them has typed in this session, so there is nobody here who chose to.\n\n"
    "Nothing is lost by waiting: file it and it will be in front of him on "
    "Monday. add-loop for work that must survive, log-decision for a settled "
    "call, record-finding for something learned. All of them render into the "
    "briefs he reads at the start of the week.\n\n"
    "This gate covers partner-facing SENDS only. Reads, record writes, loop "
    "filing and the scheduled jobs all run through the weekend exactly as "
    "before. If a partner starts typing, this stops applying for the rest of "
    "the session — that is his election to work, and it is his to make."
)


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.hook_runtime import decision, run


def _parse_error(exc):
    dlog(f"ALLOW(parse-error) {exc}")
    return 0


def _decision_error(exc):
    dlog(f"ALLOW(internal-error) {exc}")
    return 0


@decision(on_error=_decision_error)
def decide(payload):
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    if not SEND_TOOLS.match(tool or ""):
        sys.exit(0)

    clock = now()
    if clock is None:
        dlog("ALLOW(unreadable-clock)")
        sys.exit(0)
    if clock.weekday() < 5:                               # Mon-Fri
        sys.exit(0)

    if partner_is_here(payload.get("transcript_path")):
        dlog(f"ALLOW(partner-present) {tool}")
        sys.exit(0)

    day = clock.strftime("%A")
    dlog(f"DENY {tool} on {day}")
    print(REASON.format(day=day), file=sys.stderr)
    sys.exit(2)


def main():
    sys.exit(run(decide, parse_error=_parse_error))


if __name__ == "__main__":
    sys.exit(main())
