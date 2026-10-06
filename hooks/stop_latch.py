#!/usr/bin/env python3
"""stop_latch.py — one Stop intervention per claim-set, keyed on WHAT was
claimed rather than on how it was worded.

WHY THIS EXISTS. The 2026-08-23 gates-audit council read one real working
session's gate ledger and both chairs landed on the same defect: the
completion-evidence gate fired a SECOND time on a summary message whose claims
already carried receipts one message earlier. Grok named it "no latch on
claim-set"; Sol named it "receipt reuse and finding identity". The same sitting
demonstrated the defect one layer down — hooks/drift-assertion-gate.py holds a
"speak once" memory, but keys it on a sha256 of the EXACT prose, so a reply that
changed one word ("regression") minted a fresh identity for an identical
finding and the chair was held twice for the same reading.

THE PRECEDENT THIS IS BUILT ON, and it is not a general preference for leniency.
Joe, 2026-08-15, third of four rulings on how to build an enforcement gate:
"WHEN A REFUSAL CAN BE ROUTED AROUND, REMEMBER WHAT WAS REFUSED RATHER THAN
WIDENING THE BAN." That ruling was about a vault-write gate bypassable through a
scratch file, and the fix was a sequential memory rather than a broader matcher.
This is the same move pointed the other way: the gate remembers what it already
said, so a restatement is not a new finding. The first ruling in that same
record — a gate that punishes the honest interim state gets deleted — is why the
duplicate had to go rather than being tolerated: a session that verified its
work, reported it, and then summarised it is in the honest state, and a gate
that charges it a second turn for the summary is the shape Joe deletes.

WHAT IT IS NOT. This is not a mute button. Three separations are deliberate and
each has a fixture:

  · A DIFFERENT CLAIM-SET IS A DIFFERENT FINDING. Add a changed file to the
    claim and the identity changes and the gate speaks. Narrowing a matcher must
    never become silencing it, which is the failure mode Joe's 2026-08-10
    layered-enforcement ruling was protecting against from the other direction.
  · A DIFFERENT REASON CLASS IS A DIFFERENT FINDING. "no fresh verification"
    and "delivery claim names no recipient" are two different things to be told
    about the same files.
  · A DIFFERENT HOOK IS A DIFFERENT FINDING. Two gates reaching the same claims
    for different reasons both get to speak once.

An EMPTY claim-set yields no identity at all (None), and a None identity never
latches in either direction. A gate that cannot say what it is latching on must
not latch — otherwise one nameless finding would silence every later nameless
finding in the session.

FAILS OPEN, and that is the property that decides whether this is safe to
install. Unreadable state, unwritable state, corrupt JSON, a missing session id:
every one of them means NOT LATCHED, so the gate speaks. A latch that failed
closed would let one bad directory permission silence the entire Stop cluster on
this machine, which is enormously worse than the duplicate fire it exists to
stop. ops/stop_latch-selftest.py asserts both failure paths explicitly.

STATE IS PER SESSION, under out/stop-latch/<session>.json, and that is not
incidental either: out/ is a symlink back to the canonical checkout from every
worktree on this Mac, so state keyed on a shared name is state two sessions
share. That exact shape produced a live CI flake in the chat-lint and conduct
fixtures on 2026-08-23 (a fixed "selftest" id, three concurrent ops/ci.sh runs,
failures in both directions), and a Stop latch sharing a ledger across sessions
would silence a real gate in one session because a different session had already
heard it. CARR_STOP_LATCH_STATE relocates the directory for fixtures.

THE SECOND HALF OF THIS MODULE IS THE ANNOUNCE REGISTER, and it is here rather
than in five copies for the reason rule a8c55a47 gives: the 2026-08-23 gates
audit demoted five Stop reopeners (map-architecture, context-handoff,
stale-claim, loose-work, unread-artifact) from BLOCK to ANNOUNCE, and a demotion
is only as good as the emit shape it lands on. Five hand-written copies of that
shape is five chances for one of them to be silently wrong — a gate that neither
blocks nor announces has not been demoted, it has been deleted, which is exactly
what the council's safe-removal procedure forbids doing by accident. One
function, one fixture, five callers.

Used by: hooks/completion-evidence-gate.py and hooks/drift-assertion-gate.py and
hooks/unread-artifact-gate.py (latch), and the five demoted Stop gates
(announce). The completion gate is where the duplicate fire actually happened,
and it latches per LAYER: the clause layer on (consumer, clause text) with files
deliberately excluded, the floor on the changed paths plus write-verb names, and
dual_block not at all — a close that calls landed work unbuilt is worth refusing
every time it is uttered.

Fixtures: ops/stop_latch-selftest.py
"""

import hashlib
import json
import os
import re
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.environ.get("CARR_STOP_LATCH_STATE") or os.path.join(REPO, "out", "stop-latch")

# A session ledger older than this is a session that ended. Swept on write so
# the directory does not grow without bound; never swept on read, because a read
# must stay cheap and must never have a side effect that could fail.
MAX_AGE_SECONDS = 7 * 24 * 3600

# The whole point of the identity: two spellings of one claim collapse. Case,
# surrounding whitespace and repeats are noise; order is noise. Anything else
# about the token is signal and is preserved verbatim.
_SPACE = re.compile(r"\s+")

# A session id reaches the filesystem, so it is sanitised to a leaf name. Not a
# hash: a readable filename is worth a great deal when someone is trying to
# work out which session a stale ledger belonged to.
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _normalise(token):
    return _SPACE.sub(" ", str(token or "")).strip().lower()


def claim_identity(hook, reason_class, tokens):
    """The stable id of a finding: (hook, reason class, the set of claims).

    Returns None when the claim-set is empty, which callers must treat as
    "cannot latch" rather than as an id. Two calls agree whenever the same hook
    reports the same reason about the same set of things, no matter how the
    surrounding prose was written, which is exactly what a restatement is.
    """
    try:
        claims = sorted({_normalise(t) for t in (tokens or []) if _normalise(t)})
        if not claims:
            return None
        blob = "\n".join([_normalise(hook), _normalise(reason_class), *claims])
        return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:20]
    except Exception:
        return None


def ledger_path(session):
    leaf = _UNSAFE.sub("_", str(session or "unknown"))[:80] or "unknown"
    return os.path.join(STATE, f"{leaf}.json")


def _read(session):
    try:
        with open(ledger_path(session)) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        # Missing, unreadable or corrupt all mean the same thing: nothing is
        # remembered, so nothing is latched, so the gate speaks. A corrupt file
        # is overwritten by the next write rather than poisoning the session.
        return {}


def _write(session, data):
    try:
        os.makedirs(STATE, exist_ok=True)
        path = ledger_path(session)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
        _sweep()
    except Exception:
        pass                    # unwritable state means unlatched, never wedged


def _sweep():
    """Drop ledgers whose sessions ended long ago. Never raises."""
    try:
        cutoff = time.time() - MAX_AGE_SECONDS
        for name in os.listdir(STATE):
            full = os.path.join(STATE, name)
            try:
                if os.path.isfile(full) and os.path.getmtime(full) < cutoff:
                    os.unlink(full)
            except OSError:
                continue
    except Exception:
        pass


def latched(session, identity):
    """True when this finding has already been raised or already answered.

    Both halves are one question on purpose. A finding the gate already spoke
    once and a finding whose claims already carry receipts are, from the
    session's side, the same thing: it has been told, and telling it again buys
    a turn nobody reads.
    """
    if not identity:
        return False
    data = _read(session)
    return (identity in (data.get("fired") or {})
            or identity in (data.get("satisfied") or {}))


def record_fire(session, identity):
    """Remember that this finding was raised. Call it BEFORE emitting."""
    if not identity:
        return
    data = _read(session)
    fired = data.setdefault("fired", {})
    fired[identity] = int(time.time())
    _write(session, data)


def record_satisfied(session, identity):
    """Remember that this claim-set came with its receipts.

    This is the half that stops the duplicate: it is called on the path where
    the gate DECIDES NOT TO FIRE because the evidence was there. A later
    restatement of the same claims is then latched even though, read on its own,
    it would qualify.
    """
    if not identity:
        return
    data = _read(session)
    satisfied = data.setdefault("satisfied", {})
    satisfied[identity] = int(time.time())
    _write(session, data)


def forget(session):
    """Drop one session's ledger. For fixtures and for a deliberate reset."""
    try:
        os.unlink(ledger_path(session))
    except Exception:
        pass


# ── the announce register ───────────────────────────────────────────────────
# A Stop hook has exactly two registers on this surface and the difference is
# the whole subject of the 2026-08-23 rationing:
#
#   REOPEN — {"decision": "block"} on stdout, or exit 2 with the text on stderr.
#            Costs a whole extra assistant message. Reserved, as of that audit,
#            for core conduct, completion-evidence and drift-assertion.
#   ANNOUNCE — the message rides into the model's context without forcing
#            another turn. Costs a few lines, once. This is the register the
#            five demoted gates now speak in.
#
# The shape below is the one hooks/ledger-sweep.py has used since it was built,
# copied here rather than invented so a demotion cannot land on an emit nobody
# has seen work.

def announce(message, event="Stop"):
    """Say it without reopening the turn. Returns 0, for `return announce(...)`.

    THE CHANNEL IS VERIFIED, NOT ASSUMED (rule 97326357 — a claim about a
    surface becomes doctrine only after a live test FROM that surface). This
    mattered more than usual: five gates were demoted onto this emit, and if it
    were inert the demotion would have been a DELETION of five gates dressed as
    a demotion, which the council's own removal procedure forbids. Checked
    2026-08-23 against real session transcripts under ~/.claude/projects, where
    hooks/ledger-sweep.py has used this shape since it was built. A Stop hook's
    additionalContext lands three ways in the record:

        {"type": "attachment", "attachment": {"type": "hook_success",
         "hookName": "Stop", "stdout": "{\"hookSpecificOutput\": ..."}}
        {"type": "attachment", "attachment": {"type": "hook_additional_context",
         "content": ["LEDGER SWEEP — ..."]}}
        {"type": "system", "subtype": "stop_hook_summary",
         "hookAdditionalContext": ["LEDGER SWEEP — ..."]}

    The middle one is the delivery: the text is attached to the conversation,
    not merely logged. Re-run that check against a real transcript before
    trusting this on any new Claude Code build.

    Never raises: a gate that has already decided to speak must not fail while
    speaking. A write that cannot reach stdout leaves the finding in the gate's
    own audit log, which is where the telemetry rollup reads it from anyway.
    """
    try:
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": event,
                "additionalContext": message,
            }
        }) + "\n")
        sys.stdout.flush()
    except Exception:
        pass
    return 0
