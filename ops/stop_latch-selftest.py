#!/usr/bin/env python3
"""Fixtures for hooks/stop_latch.py — the claim-set latch, written before it.

WHAT THIS HAS TO PROVE. The 2026-08-23 gates-audit council named one defect in
today's labeled gate ledger with both chairs agreeing on it: the
completion-evidence gate fired a SECOND time on a summary whose claims already
carried receipts one message earlier. Grok called it "no latch on claim-set";
Sol called it "receipt reuse and finding identity". The same chair's own sitting
tripped hooks/drift-assertion-gate.py twice for the same reason one layer down —
that gate keys its "speak once" memory on the EXACT prose, so a single changed
word ("regression") minted a new identity for an identical finding.

So the acceptance shape is four properties, and every one of them is a case
below:

  1. IDENTITY IS THE CLAIM-SET, NOT THE WORDS. The same claims in reworded
     prose, in a different order, in different case, produce ONE identity. This
     is the property the drift gate's text hash does not have.
  2. A DIFFERENT CLAIM-SET IS A DIFFERENT FINDING. Narrowing must not become
     muting: add a claim and the gate speaks again.
  3. RECEIPTS LATCH IT SHUT. Once a claim-set is recorded satisfied, a later
     restatement never fires — even a restatement that would qualify on its own.
     That is Joe's 2026-08-15 ruling in its Stop-gate form: remember what was
     already answered rather than widening the matcher.
  4. SESSIONS DO NOT CROSS WIRES. out/ is a symlink back to the canonical
     checkout from every worktree on this Mac, so a latch keyed on a shared
     name would let one session silence another's gate. Two sessions, two
     ledgers.

And the property that outranks all four: FAIL OPEN. A latch that cannot read or
write its state must let the gate SPEAK, never silence it. An unwritable
directory silencing every Stop gate on the machine is a far worse failure than
one duplicate fire, so the unwritable case is asserted explicitly.

    .venv/bin/python ops/stop_latch-selftest.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(REPO, "hooks", "stop_latch.py")

failures: list[str] = []
passed = 0


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
    else:
        failures.append(f"{name}: {detail}" if detail else name)


def load(state_dir):
    """Import the module fresh with its state pinned to a scratch directory."""
    os.environ["CARR_STOP_LATCH_STATE"] = state_dir
    spec = importlib.util.spec_from_file_location("carr_stop_latch_fixture", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    with tempfile.TemporaryDirectory() as tmp:
        latch = load(tmp)

        # ---------------------------------------------------------- identity
        # 1. The same claim-set, reworded. This is the completion gate's second
        # fire and the drift gate's second block, both in one assertion.
        first = latch.claim_identity(
            "completion-evidence-gate", "terminal completion claim has no fresh verification",
            ["hooks/chat-lint-gate.py", "hooks/stale-claim-gate.py"])
        reworded = latch.claim_identity(
            "completion-evidence-gate", "terminal completion claim has no fresh verification",
            ["hooks/stale-claim-gate.py", "hooks/chat-lint-gate.py"])
        check("identity-order-independent", first == reworded,
              f"{first} != {reworded}")

        cased = latch.claim_identity(
            "completion-evidence-gate", "terminal completion claim has no fresh verification",
            ["HOOKS/Chat-Lint-Gate.py", " hooks/stale-claim-gate.py "])
        check("identity-normalises-case-and-space", first == cased, f"{first} != {cased}")

        duped = latch.claim_identity(
            "completion-evidence-gate", "terminal completion claim has no fresh verification",
            ["hooks/chat-lint-gate.py", "hooks/chat-lint-gate.py",
             "hooks/stale-claim-gate.py"])
        check("identity-ignores-repeats", first == duped, f"{first} != {duped}")

        # 2. A different claim-set, a different reason class, a different hook:
        # each is its own finding. Narrowing must not become muting.
        wider = latch.claim_identity(
            "completion-evidence-gate", "terminal completion claim has no fresh verification",
            ["hooks/chat-lint-gate.py", "hooks/stale-claim-gate.py", "hooks/loose-work-gate.py"])
        check("identity-new-claim-is-new-finding", first != wider)
        other_reason = latch.claim_identity(
            "completion-evidence-gate", "delivery claim names no recipient",
            ["hooks/chat-lint-gate.py", "hooks/stale-claim-gate.py"])
        check("identity-reason-class-separates", first != other_reason)
        other_hook = latch.claim_identity(
            "drift-assertion-gate", "terminal completion claim has no fresh verification",
            ["hooks/chat-lint-gate.py", "hooks/stale-claim-gate.py"])
        check("identity-hook-separates", first != other_hook)

        # An empty claim-set is not a claim-set. A gate that cannot name what it
        # is latching on must not latch at all, or one empty finding would
        # silence every later empty finding in the session.
        check("identity-empty-is-none", latch.claim_identity("x", "y", []) is None)
        check("identity-blank-tokens-are-none",
              latch.claim_identity("x", "y", ["", "   "]) is None)

        # ------------------------------------------------------------- firing
        check("fresh-identity-not-latched", not latch.latched("s1", first))
        latch.record_fire("s1", first)
        check("second-look-is-latched", latch.latched("s1", first))
        check("wider-claim-still-speaks", not latch.latched("s1", wider))
        check("other-reason-still-speaks", not latch.latched("s1", other_reason))

        # A None identity never latches anything, in either direction.
        latch.record_fire("s1", None)
        check("none-identity-never-latches", not latch.latched("s1", None))

        # ---------------------------------------------------------- receipts
        # THE DUPLICATE COMPLETION FIRE, as a fixture. Turn one: the gate sees
        # fresh verification and records the claim-set satisfied. Turn two: the
        # session summarises the same claims. Nothing fires.
        receipted = latch.claim_identity(
            "completion-evidence-gate", "terminal completion claim has no fresh verification",
            ["migrations/0286.sql", "mcp-server/src/incident.js"])
        check("receipted-claim-not-latched-before", not latch.latched("s2", receipted))
        latch.record_satisfied("s2", receipted)
        check("receipted-claim-latched-after", latch.latched("s2", receipted))
        # Reworded summary of the same receipted claims: still latched.
        restated = latch.claim_identity(
            "completion-evidence-gate", "terminal completion claim has no fresh verification",
            ["MCP-SERVER/src/incident.js", "migrations/0286.sql"])
        check("receipted-restatement-latched", latch.latched("s2", restated))
        # But work that touches a NEW file is a new claim-set and must fire.
        grew = latch.claim_identity(
            "completion-evidence-gate", "terminal completion claim has no fresh verification",
            ["migrations/0286.sql", "mcp-server/src/incident.js", "mcp-server/src/tools.js"])
        check("receipted-plus-new-work-speaks", not latch.latched("s2", grew))

        # --------------------------------------------------- session scoping
        check("other-session-unaffected", not latch.latched("s3", first))
        check("other-session-unaffected-receipts", not latch.latched("s3", receipted))
        latch.record_fire("s3", first)
        check("both-sessions-latched-independently",
              latch.latched("s1", first) and latch.latched("s3", first))

        # A session id with path characters must not escape its directory.
        nasty = "../../etc/passwd"
        landed = os.path.realpath(latch.ledger_path(nasty))
        latch.record_fire(nasty, first)
        check("session-id-cannot-escape",
              landed.startswith(os.path.realpath(tmp) + os.sep)
              and os.path.dirname(landed) == os.path.realpath(tmp),
              landed)

        # A missing session id still gets a ledger rather than raising.
        latch.record_fire(None, first)
        check("missing-session-id-tolerated", latch.latched(None, first))

    # ------------------------------------------------------------- fail open
    # An unwritable state directory must leave every gate SPEAKING. This is the
    # property that decides whether the latch is safe to install at all: if it
    # failed closed, one bad permission would silence the whole Stop cluster.
    with tempfile.TemporaryDirectory() as tmp:
        blocked = os.path.join(tmp, "nope")
        open(blocked, "w").close()          # a FILE where the directory must go
        latch = load(blocked)
        ident = latch.claim_identity("h", "r", ["a"])
        latch.record_fire("s", ident)
        check("unwritable-state-fails-open", not latch.latched("s", ident))
        latch.record_satisfied("s", ident)
        check("unwritable-receipts-fail-open", not latch.latched("s", ident))

    # Corrupt state is the same answer: speak, do not raise.
    with tempfile.TemporaryDirectory() as tmp:
        latch = load(tmp)
        ident = latch.claim_identity("h", "r", ["a"])
        latch.record_fire("s4", ident)
        with open(latch.ledger_path("s4"), "w") as fh:
            fh.write("{ this is not json")
        check("corrupt-ledger-fails-open", not latch.latched("s4", ident))
        # And a corrupt ledger must be recoverable rather than permanently poisoned.
        latch.record_fire("s4", ident)
        check("corrupt-ledger-heals", latch.latched("s4", ident))

    # ------------------------------------------------------- announce shape
    # The register the five demoted gates now speak in. A demotion that lands on
    # a wrong emit shape is not a demotion, it is a deletion — the exact thing
    # the council's safe-removal procedure forbids doing by accident — so the
    # shape is asserted here once rather than trusted five times.
    with tempfile.TemporaryDirectory() as tmp:
        latch = load(tmp)
        import contextlib
        import io
        import json as _json

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = latch.announce("LOOSE WORK GATE — three files never landed.")
        check("announce-returns-zero", rc == 0, repr(rc))
        try:
            emitted = _json.loads(buf.getvalue())
        except ValueError as exc:
            emitted = {}
            check("announce-emits-json", False, str(exc))
        else:
            check("announce-emits-json", True)
        hso = emitted.get("hookSpecificOutput") or {}
        check("announce-names-the-event", hso.get("hookEventName") == "Stop", str(hso))
        check("announce-carries-the-text",
              hso.get("additionalContext", "").startswith("LOOSE WORK GATE"), str(hso))
        # THE ONE THING THAT WOULD MAKE A DEMOTION A REOPEN. `decision` is the
        # key Claude Code reads to block; an announce must never carry it.
        check("announce-is-not-a-block", "decision" not in emitted, str(emitted))

        # It must survive a closed stdout rather than raising inside a gate that
        # has already decided to speak.
        with contextlib.redirect_stdout(None):
            try:
                rc = latch.announce("x")
                ok = rc == 0
            except Exception as exc:                        # noqa: BLE001
                ok = False
                rc = exc
        check("announce-survives-dead-stdout", ok, repr(rc))

    if failures:
        print(f"stop-latch selftest FAILED {len(failures)}/{passed + len(failures)}")
        for f in failures:
            print(f"  · {f}")
        return 1
    print(f"stop-latch selftest ok ({passed} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
