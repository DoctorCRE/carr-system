#!/usr/bin/env python3
"""drift-assertion-gate.py — the drift check, moved to the door where the claim
actually reaches a human.

WHY THIS EXISTS, and the frequency is the whole argument. "A CURRENT artifact
read accurately, the DECISION behind it left unread" is the most frequent
failure class this system has recorded, running since 2026-08-04, with the
majority caught by Joe rather than by a session. When one was filed on
2026-08-14 the record layer answered with the point this file acts on: "A repeat
class is a design problem, not a lapse: say so rather than filing the next one
quietly."

THE COUNT IS NOT WRITTEN HERE, deliberately, and that is a correction rather
than an omission. This file carried "NINE times" in its docstring and again in
the message it prints. By 2026-08-15 the ledger read eleven, so the gate whose
entire job is catching a stale artifact quoted as present state was itself
quoting a stale artifact as present state, in the sentence meant to persuade.
Rule b01edd26 bans exactly this: no hardcoded count a later edit can falsify.
A Stop hook cannot ask the record layer for the live figure — it must stay fast
and work offline — so the honest move is to state the shape and point at the
live source. `standing-context` returns the ledger with current counts.

drift-claim-gate.py already guards this class and it WORKS — it fired twice on
2026-08-14 and both flagged claims turned out to be wrong. Its limitation is
positional, not logical: it sits on PreToolUse for record-defect and add-loop, so
it speaks when a session FILES A RECORD. That is not when the damage happens. On
2026-08-14 the session told Joe the wrong thing twice in chat before it filed
anything, and the filing is what finally tripped the gate. On other occasions in
this class nothing was ever filed at all, so nothing ever fired.

The expensive failure is the ASSERTION — telling a partner that something is
broken when it was chosen, which is how six of the nine were discovered, by him.
So this is the same policy on the Stop door, where the reply is about to reach
him and can still be revised.

ONE JUDGEMENT, TWO DOORS. It imports drift-claim-gate's DRIFT detector and its
decision search rather than restating them, the same discipline bash-write-gate
and write-effect-check follow. A second copy of that regex would drift from the
first the week either one changed, and the selftest asserts the two are the same
pattern rather than trusting that they stay so.

WHY IT BLOCKS HERE WHEN THE WRITE DOOR ONLY INFORMS. A record can be corrected
after the fact; a wrong claim in front of Joe costs his attention and, six times
out of nine, his correction. Stop is also the one door where blocking is cheap:
it reopens the turn, which is exactly what happened all day on 2026-08-14 to good
effect.

IT FIRES ONCE PER CLAIM, and that is the most important line in this file. A Stop
hook that blocks the same reply forever is a session that cannot end. The first
block hands over the matching rulings; if the same claim comes back, the session
has read them and the call is now its own.

THE IDENTITY OF "THE SAME CLAIM" IS THE RULINGS, NOT THE WORDING (2026-08-23).
It used to be a sha256 of the exact prose, and that failed live during the
gates-audit council's own sitting: the chair sent a reply, was held here, read
the rulings, sent a narrower reply — and was held a SECOND time, because one
word had changed ("regression") and a text hash cannot tell a restatement from a
new finding. It then had to compose a third reply engineered not to match the
detector, which is a gate teaching evasion rather than reading. The identity is
now the SET OF RULINGS the claim matched, through hooks/stop_latch.py: those are
what the block hands over, so a session that has been handed them once has been
handed them. Different rulings are a different finding and still block. This is
Joe's 2026-08-15 ruling in its Stop form — remember what was already said rather
than widening or narrowing the matcher.

THIS GATE KEEPS ITS REOPEN. Joe's 2026-08-23 Stop-gate rationing cut eleven
reopeners to three, and this is one of them, with the other two being core
conduct and completion-evidence. It earns it on the record the docstring opens
with: this is the most frequent failure class the system has, most of it caught
by Joe rather than by a session, and the next message after this block is the
RESULT OF WORK — rulings read — rather than a restatement.

IT IS SILENT WHEN NO RULING MATCHES — drift-claim-gate's own rule, inherited
deliberately: "a drift claim with no matching ruling is probably a real finding."
Most reports of breakage are true. A gate that fires on all of them is one
somebody turns off, and then it is not a gate.

FAILS OPEN ON EVERYTHING ELSE. No transcript, no decision log, an unreadable
file, an internal error: none may strand a turn.
"""

import importlib.util
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Telemetry routing first (PR #554): the meter decides which stream this
# process's log line belongs in, and a missing meter must not change a verdict.
try:
    import hook_meter
    _GUARD_LOG = hook_meter.guard_log_path(REPO)
except Exception:
    _GUARD_LOG = os.path.join(REPO, "out", "hook-guard.log")

# The latch ledger moved into hooks/stop_latch.py, which reads its directory
# from the environment AT IMPORT. This gate's own long-standing override has to
# be honoured before that import or every fixture would write into the real
# out/stop-latch — and out/ is a symlink back to the canonical checkout from
# every worktree on this Mac, so a fixture run would silence the live gate.
# CARR_STOP_LATCH_STATE still wins if a caller sets it explicitly.
if os.environ.get("CARR_DRIFT_ASSERTION_STATE") and not os.environ.get("CARR_STOP_LATCH_STATE"):
    os.environ["CARR_STOP_LATCH_STATE"] = os.environ["CARR_DRIFT_ASSERTION_STATE"]

from stop_latch import claim_identity, latched, record_fire  # noqa: E402
LOG = _GUARD_LOG
STATE = os.environ.get("CARR_DRIFT_ASSERTION_STATE") or os.path.join(
    REPO, "out", "drift-assertion")

_policy = None
_chat = None


def log(line):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(f"drift-assertion-gate {line}\n")
    except Exception:
        pass


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(os.path.dirname(__file__), filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def policy():
    """drift-claim-gate, the WRITE door's module — imported, never copied.

    Its explicitly NONCANONICAL fixture path is redirected only in tests.  The
    normal policy reads the canonical decision record and ignores ambient vault
    configuration.
    """
    global _policy
    if _policy is None:
        _policy = _load("carr_drift_claim_policy", "drift-claim-gate.py")
        override = os.environ.get("CARR_NONCANONICAL_DECISIONS_PATH")
        if override:
            _policy.NONCANONICAL_DECISIONS_PATH = "CARR_NONCANONICAL_DECISIONS_PATH"
    return _policy


def chat():
    """chat-lint-gate, for its transcript reader. Same reason: one reader."""
    global _chat
    if _chat is None:
        _chat = _load("carr_chat_lint_reader", "chat-lint-gate.py")
    return _chat


def final_assistant_text(path):
    reader = chat()
    text = ""
    for record in reader.read_tail(path):
        candidate = reader.text_of(record, ("assistant",))
        if candidate and candidate.strip():
            text = candidate.strip()
    return text


def already_raised(session, hits):
    """True if THIS SET OF RULINGS has already been put in front of the session.

    The identity is the rulings, not the sentence that surfaced them — see the
    docstring. Keying on prose let one changed word re-open a finding the
    session had already read and acted on, twice in one sitting.

    Marks it raised as it reads, so the caller speaks once and the next reply
    carrying the same rulings passes. Fails open through stop_latch: if the
    ledger cannot be read or written, this returns False and the gate speaks,
    which is the safe direction for a check whose whole value is speaking.
    """
    identity = claim_identity("drift-assertion-gate", "governed-drift-claim",
                              [line for _, line in hits])
    if latched(session, identity):
        return True
    record_fire(session, identity)
    return False


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        # Logged, never silent (2026-09-24). This exit was bare, and when
        # run-record-gate handed this gate a drained stdin it allowed every
        # Stop with no trace; drift-claim-gate's ALLOW(parse-error) line is how
        # the same fault was found there.
        log(f"ALLOW(parse-error) {exc}")
        sys.exit(0)
    try:
        if (payload.get("hook_event_name") or "Stop") != "Stop":
            sys.exit(0)
        if payload.get("stop_hook_active"):
            sys.exit(0)
        path = payload.get("transcript_path")
        if not path or not os.path.exists(path):
            sys.exit(0)

        text = final_assistant_text(path)
        if len(text) < 60:
            sys.exit(0)

        module = policy()
        prose = chat().strip_fences(text)
        if not module.DRIFT.search(prose):
            sys.exit(0)
        hits = module.search_decisions(module.salient_tokens(prose))
        if not hits:
            sys.exit(0)                     # no ruling: probably a real finding
        if already_raised(payload.get("session_id"), hits):
            sys.exit(0)                     # said once; the call is the session's

        body = "\n".join(f"  · [{tag}] {line[:300]}" for tag, line in hits)
        log(f"BLOCK hits={len(hits)}")
        print(
            "DRIFT ASSERTION — you are about to tell Joe that a present state is "
            "WRONG, and the decision log has something on this subject.\n\n"
            "This is the most frequent failure class on record here, running since "
            "2026-08-04, most of them caught by him rather than by a session, and it "
            "always has the same shape: a current artifact read accurately, the "
            "decision behind it left unread. `standing-context` returns the live "
            "count if you want it. "
            "The write-door version of this check only fires when a record gets "
            "filed — by then the claim has usually already reached him in chat, "
            "which is what this door is for.\n\nMatching rulings, newest first:\n\n"
            + body +
            "\n\nRead them before this reply goes out. If one explains the state "
            "you are calling broken, the state was CHOSEN and the finding is either "
            "nothing or a stale prompt to correct instead. If none apply, say so "
            "and send it — a drift claim with no governing ruling is usually real. "
            "This will not stop you twice over the same rulings, however you word "
            "the reply, so answer the rulings rather than rewriting around them.",
            file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        log(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
