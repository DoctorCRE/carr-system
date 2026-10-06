#!/usr/bin/env python3
"""
drift-assertion-gate-selftest.py — acceptance test for hooks/drift-assertion-gate.py,
written before the gate (rule e65efc68).

WHY IT EXISTS, and the frequency is the argument. "A current artifact read
accurately, the decision behind it left unread" is the most frequent failure
class on record here, running since 2026-08-04, most caught by Joe rather than
by a session. The record layer says so itself on every filing: "A repeat class
is a design problem, not a lapse." The integer is deliberately absent — it was
"NINE" here while the ledger read twelve, and a suite testing the gate against
stale figures should not carry one. `standing-context` returns the live count.

drift-claim-gate.py already guards this, and it works — it fired twice on
2026-08-14 and both times the claim it flagged was wrong. But it sits on
PreToolUse for record-defect and add-loop, so it fires when a session FILES a
record. On 2026-08-14 the session had already told Joe the wrong thing twice in
chat before it filed anything, and on other occasions nothing was ever filed at
all. The gate guards the write; the damage happens at the ASSERTION.

So this is the same policy on the door where the claim actually reaches a human:
Stop. It imports drift-claim-gate's detector and its decision search rather than
restating them — one judgement, two doors, the same discipline bash-write-gate
and write-effect-check follow. A second copy of that regex would drift from the
first the week either changed.

WHAT IT MUST GET RIGHT, and it is mostly about not wedging a session:

  1. FIRE ONCE PER CLAIM. A Stop hook that blocks the same reply forever is a
     session that cannot end. The first block hands over the rulings; if the same
     claim comes back, the session has read them and made its call, and the gate
     stands down. This is the single most important case here.
  2. SILENCE WHEN NO RULING MATCHES. drift-claim-gate's own rule, inherited
     deliberately: "a drift claim with no matching ruling is probably a real
     finding." Most reports of breakage are true, and a gate that fires on all of
     them is one somebody turns off.
  3. NEVER ON ORDINARY PROSE. The detector is narrow by construction — it is
     about a present state being WRONG rather than CHOSEN, not about the word
     "broken" appearing.
  4. FAIL OPEN ON EVERYTHING. No transcript, no decision log, an unreadable
     file: none may strand a turn.

Hermetic: a throwaway transcript and a throwaway decision log per case.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GATE = os.path.join(REPO, "hooks", "drift-assertion-gate.py")

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "lib"))
from selftest_harness import Checker  # noqa: E402

CHECKER = Checker()
failures = CHECKER.failures
check = CHECKER.check


def transcript(tmp, assistant_text, name="t.jsonl"):
    path = os.path.join(tmp, name)
    with open(path, "w") as fh:
        fh.write(json.dumps({"type": "user", "message": {"content": "go"}}) + "\n")
        fh.write(json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": assistant_text}]},
        }) + "\n")
    return path


def run(tmp, path, decisions, state, event="Stop", stop_active=False, session="s1"):
    payload = json.dumps({"session_id": session, "transcript_path": path,
                          "hook_event_name": event,
                          "stop_hook_active": stop_active})
    env = {**os.environ, "CARR_NONCANONICAL_DECISIONS_PATH": decisions,
           "CARR_DRIFT_ASSERTION_STATE": state}
    p = subprocess.run([sys.executable, GATE], input=payload, capture_output=True,
                       text=True, env=env)
    return p.returncode, p.stderr


def main():
    print("drift assertion gate")
    tmp = tempfile.mkdtemp(prefix="drift-assertion-selftest-")
    try:
        # A decision log holding one ruling about a made-up subsystem, so the
        # match is deterministic and owes nothing to the real vault.
        decisions = os.path.join(tmp, "decision-history.md")
        with open(decisions, "w") as fh:
            fh.write(
                "# Decision history\n\n"
                "- `2026-08-13` — The quokka-indexer lane was DELIBERATELY disabled by "
                "Joe after the overnight run cost more than it returned; leaving it off "
                "is the chosen state and must not be read as drift.\n")
        state = os.path.join(tmp, "state")

        # 1. THE FAILURE THIS EXISTS FOR: a drift-shaped claim about something the
        #    decision log has already ruled on, stated to the partner in chat.
        claim = ("The quokka-indexer lane is no longer running. It was supposed to "
                 "fire nightly and the schedule has silently reverted, so the index "
                 "is stale and nothing has been re-pointed. I think this regressed "
                 "when the overnight lane changed.")
        path = transcript(tmp, claim)
        rc, err = run(tmp, path, decisions, state)
        check("a drift assertion with a matching ruling BLOCKS", rc == 2, f"exit {rc}")
        check("the block quotes the governing ruling",
              "quokka-indexer" in err and "DELIBERATELY" in err, err[:200])
        check("the block says the claim is about to reach the partner",
              "chat" in err.lower() or "partner" in err.lower() or "joe" in err.lower(),
              err[:200])

        # 2. FIRE ONCE. The same claim a second time must pass — the session has
        #    been handed the ruling and the call is now its own. A Stop hook that
        #    blocks forever is a session that cannot end.
        rc2, _ = run(tmp, path, decisions, state)
        check("the SAME claim a second time is allowed through", rc2 == 0,
              f"exit {rc2} — this gate would wedge the session")

        # 2b. THE COUNCIL'S OWN DOUBLE FIRE, as a fixture (2026-08-23). The
        # latch used to be a sha256 of the exact prose, so a REWORDED reply
        # about the same subject minted a fresh identity and the same rulings
        # were pushed at the session a second time. It happened live to a
        # council chair, which then had to compose a third reply engineered not
        # to match the detector — a gate teaching evasion instead of reading.
        # The identity is now the set of rulings, so this must be silent.
        reworded = ("The quokka-indexer lane is not firing on its schedule. That "
                    "looks like a regression in the overnight lane rather than "
                    "anything anyone chose, and the index has gone stale as a "
                    "result with no re-pointing behind it.")
        rc2b, err2b = run(tmp, transcript(tmp, reworded, "t2b.jsonl"), decisions, state)
        check("a REWORDED claim over the SAME rulings is silent", rc2b == 0,
              f"exit {rc2b}: {err2b[:160]}")

        # ...and narrowing must not have become muting. A claim that matches a
        # DIFFERENT ruling is a different finding and still blocks.
        two_rulings = os.path.join(tmp, "two-decisions.md")
        with open(two_rulings, "w") as fh:
            fh.write(
                "# Decision history\n\n"
                "- `2026-08-13` — The quokka-indexer lane was DELIBERATELY disabled by "
                "Joe after the overnight run cost more than it returned; leaving it off "
                "is the chosen state and must not be read as drift.\n"
                "- `2026-08-16` — The wombat-render surface was DELIBERATELY frozen at "
                "version two; the newer renderer is not a rollout anyone is waiting on.\n")
        other = ("The wombat-render surface is out of date: it sits on version two "
                 "and the newer renderer never shipped, so every page it produces "
                 "is stale and nothing has re-pointed it.")
        rc2c, err2c = run(tmp, transcript(tmp, other, "t2c.jsonl"), two_rulings, state)
        check("a claim over DIFFERENT rulings still blocks", rc2c == 2,
              f"exit {rc2c}: the latch became a mute")
        check("...and it quotes the ruling that actually applies",
              "wombat-render" in err2c, err2c[:200])

        # ...and one session's latch does not silence another's gate. out/ is a
        # symlink back to the canonical checkout from every worktree here, so a
        # ledger keyed on anything shared would do exactly that.
        rc2d, _ = run(tmp, path, decisions, state, session="a-different-session")
        check("a second session still hears the finding", rc2d == 2,
              f"exit {rc2d} — the latch leaked across sessions")

        # 3. Silence when the decision log has nothing on the subject.
        empty = os.path.join(tmp, "empty-decisions.md")
        with open(empty, "w") as fh:
            fh.write("# Decision history\n\nnothing relevant here\n")
        path3 = transcript(tmp, claim, "t3.jsonl")
        rc3, err3 = run(tmp, path3, empty, os.path.join(tmp, "state3"))
        check("a drift claim with NO matching ruling is allowed and silent",
              rc3 == 0 and err3.strip() == "", f"exit {rc3}: {err3[:120]}")

        # 4. Ordinary reporting is not a drift assertion.
        for label, text in [
            ("a plain test failure", "The build failed: three tests are red in the "
             "parser suite and I am fixing the off-by-one now."),
            ("a plain status report", "Merged and installed. The selftest passes "
             "39 of 39 and the machine reports no config drift."),
        ]:
            p4 = transcript(tmp, text, f"t-{abs(hash(label))}.jsonl")
            rc4, err4 = run(tmp, p4, decisions, os.path.join(tmp, "s4"))
            check(f"allowed: {label}", rc4 == 0 and err4.strip() == "",
                  f"exit {rc4}: {err4[:120]}")

        # 5. Never wedge. Every one of these fails OPEN.
        rc5, _ = run(tmp, path, decisions, os.path.join(tmp, "s5"), stop_active=True)
        check("stop_hook_active is respected", rc5 == 0, f"exit {rc5}")
        rc6, _ = run(tmp, os.path.join(tmp, "missing.jsonl"), decisions,
                     os.path.join(tmp, "s6"))
        check("a missing transcript fails OPEN", rc6 == 0, f"exit {rc6}")
        rc7, _ = run(tmp, path, os.path.join(tmp, "no-such-log.md"),
                     os.path.join(tmp, "s7"))
        check("a missing decision log fails OPEN", rc7 == 0, f"exit {rc7}")
        rc8, _ = run(tmp, path, decisions, os.path.join(tmp, "s8"), event="PreToolUse")
        check("a non-Stop event is ignored", rc8 == 0, f"exit {rc8}")
        p9 = subprocess.run([sys.executable, GATE], input="{not json",
                            capture_output=True, text=True)
        check("a malformed payload fails OPEN", p9.returncode == 0,
              f"exit {p9.returncode}")

        # 6. ONE POLICY, TWO DOORS — asserted, not assumed. A second copy of the
        #    detector would drift from the write-door gate the week either changed.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "dag", GATE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        spec2 = importlib.util.spec_from_file_location(
            "dcg", os.path.join(REPO, "hooks", "drift-claim-gate.py"))
        write_door = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(write_door)
        check("the Stop door uses the WRITE door's own detector, not a copy",
              module.policy().DRIFT.pattern == write_door.DRIFT.pattern,
              "the two doors would drift apart")
    finally:
        subprocess.run(["rm", "-rf", tmp])

    return CHECKER.summary()


if __name__ == "__main__":
    raise SystemExit(main())
