#!/usr/bin/env python3
"""chat-lint-gate-selftest.py — fixtures for hooks/chat-lint-gate.py, written
before the hook (rule e65efc68, enforcing rules 5be2f462 and 3a9dbafd).

TWO RULES, ONE MOMENT. Both bind the assistant's own outgoing chat text, and
the only place that text exists before Joe reads it is the Stop hook:

  5be2f462  the contrast-reframe and banned-vocab HARD regexes that already
            guard every prospect-visible surface (tools/writing-lint.py,
            enforced by lint-gate.py on file writes) finally reach the surface
            they were actually taught about: internal dialogue to Joe.
  3a9dbafd  never make a partner decode an ID. A bare 8-hex rule id or a bare
            'loop #250' with no plain words around it forces Joe to go look up
            what the thing is; the id must ride WITH its gloss.

SCOPE IS DELIBERATELY NARROW. Only the HARD ids the audit row names (vocab,
contrast-reframe, contrast-reframe-split) point at chat. contrast-compressed
stays off this surface: 'X, not Y' is REVIEW severity precisely because a
genuine correction of fact takes that shape, and honest technical chat is
full of genuine corrections. The em-dash ban stays prospect-only — chat and
this repo's own house style use em dashes on purpose. A lint that flags
half of every honest report gets muted in a day, and then it catches nothing.

FALSE-POSITIVE GUARDS THE CASES BELOW PIN DOWN: fenced code is exempt (git
output is full of hex), all-digit tokens are exempt (20260814 is a date, not
an id), and an id WITH plain words in its sentence is a gloss, not a bare id.

Spawns the REAL hook with REAL Stop payloads + transcript fixtures.

THE CONTRACT CHANGED 2026-08-16 and these fixtures pin the new one: a wording
fault no longer BLOCKS the turn, because the offending text has already reached
Joe and a block only buys a restatement he pays for twice (rule 1d50a3bb). The
gate now parks a note that hooks/chat-lint-carryover.py injects before the next
reply. CAUGHT = a note was parked; exit 2 is now itself a regression.
"""
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "chat-lint-gate.py")
# The audience split's once-per-session ledger. Pinned to a scratch directory so
# fixtures never write into the live out/stop-latch, which every worktree on this
# Mac shares through a symlink.
LATCH_STATE = tempfile.mkdtemp(prefix="chat-lint-latch-")

# (name, assistant_text, expect_caught)
CASES = [
    ("vocab-banned", "This will seamlessly unlock a transformative workflow "
                     "for the whole pipeline.", True),
    ("contrast-reframe", "It's not about speed, it's about correctness of the "
                         "render path.", True),
    ("contrast-split", "This isn't a bug. It's a feature of the exporter that "
                       "was chosen on purpose.", True),
    # bare-id moved to the conduct stop gate 2026-08-22 (loop 504, item three):
    # both gates flagged it under two class names, so one bare identifier blocked
    # the turn twice. This gate must now stay SILENT on it, and the conduct
    # selftest carries the case. Verified before the move: conduct flags this
    # exact string as hex_alone on both ids.
    ("bare-id-list", "Done today:\n- c0b38d80\n- 24e10ee8\nBoth landed.", False),
    # Same move as bare-id-list above. Verified before flipping: the conduct
    # detector flags this exact string as loop_num on both references.
    ("bare-loop-ref", "Close loop #250 and loop #251.", False),
    ("glossed-id", "Rule c0b38d80 (re-bless the baseline in the same commit "
                   "as any gate change) is now enforced by the pre-commit "
                   "hook, so the next unblessed gate change cannot commit.",
     False),
    ("glossed-loop", "Loop #250 (the living-orb panel visual spec) still "
                     "waits on the origination conversation nobody recovered.",
     False),
    ("hex-in-fence", "All green now:\n```\nc0b38d80 gates: re-bless landed\n"
                     "24e10ee8 commit-msg check\n```\nEvery selftest passes.",
     False),
    ("date-not-id", "The audit file 20260814 rows were classified today and "
                    "the queue is ranked by leverage.", False),
    ("plain-chat", "The rebase finished cleanly and every selftest passes. "
                   "The branch is ready to push once the checkout reconciles.",
     False),
    ("unnamed-deal-question", "Can you review the draft lease before lunch?", True),
    ("named-deal-question", "Can you review the Riverwalk draft lease before lunch?", False),
    ("unnamed-loi-question", "Did the LOI arrive from the landlord?", True),
    ("street-named-deal-question", "Did the 123 Main LOI arrive from the landlord?", False),
    ("multi-clause-task", "Please review the lease and approve the redlines today.", True),
    ("numbered-but-unmarked", "Please do these:\n1. Review the lease\n2. Approve the redlines", True),
    ("numbered-all-required", "All required:\n1. Review the lease\n2. Approve the redlines", False),
    ("self-narrated-progress", "I reviewed the lease and approved the redlines today.", False),
    ("confidentiality-access-boundary", "Hermes may not read CARR doctrine because it is confidential.", True),
    ("private-share-boundary", "The provider must not share CARR material because it is private.", True),
    ("confidentiality-correction", "Confidentiality is not the boundary; Hermes cannot hold a live credential.", False),
    ("real-credential-boundary", "Hermes cannot hold a live CARR credential or execute autonomously.", False),
    ("quoted-historical-boundary", "Historical rule text: “The model cannot read CARR records because they are confidential.”", False),
    ("blockquote-historical-boundary", "> The provider may not share CARR material because it is sensitive.", False),
]

passed = 0
bad: list[str] = []


# A FRESH SESSION ID PER CASE, and per process, for two different reasons that
# both land here.
#
#   ACROSS RUNS: out/ is a symlink back to the canonical checkout from every
#   worktree on this Mac, so a fixed "selftest" id meant every concurrent
#   ops/ci.sh run read and wrote the SAME carry file.
#
#   ACROSS CASES: as of 2026-08-23 this gate consolidates reporting-prose
#   findings into ONE note per session (the audience split — see the gate's
#   main()). With a shared id, case two onwards would be legitimately silent
#   and every one of them would read as a miss. Order-dependent fixtures are
#   worse than no fixtures, because the failure looks like the gate.
_SESSION_N = itertools.count()


def next_session():
    return f"selftest-{os.getpid()}-{next(_SESSION_N)}"


def carry_path(session):
    return os.path.join(REPO, "out", "chat-lint-carry", f"{session}.txt")


def run_stop(assistant_text, event="Stop", stop_active=False, session=None):
    session = session or next_session()
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    # A PRIVATE REPO ROOT PER RUN, not a per-pid session id. The hook parks its
    # note at <root>/out/chat-lint-carry/selftest.txt; with the real repo root
    # that fixed name is shared by every concurrent ci.sh run, and two runs
    # cross-wire — one deletes the note the other is about to read (the
    # 2026-08-23 load-flake class, four sessions deep on this one bug). Pointing
    # CARR_REPO_ROOT at a throwaway tree gives each process its own out/ dir.
    # hooks/chat-lint-gate.py and chat-lint-carryover.py both honour it, the
    # same contract close-before-open-gate.py already established.
    root = tempfile.mkdtemp(prefix="chat-lint-selftest.")
    os.makedirs(os.path.join(root, "out", "chat-lint-carry"), exist_ok=True)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps({"type": "user", "origin": {"kind": "user"},
                "message": {"content": [{"type": "text", "text": "status?"}]}}) + "\n")
            fh.write(json.dumps({"type": "assistant",
                "message": {"content": [{"type": "text", "text": assistant_text}]}}) + "\n")
        payload = {"hook_event_name": event, "transcript_path": path,
                   "session_id": session, "stop_hook_active": stop_active}
        # The gate no longer BLOCKS on a wording fault (rule 1d50a3bb: the bad
        # text has already reached Joe, so a block only buys a restatement he
        # pays for twice). It parks a note that hooks/chat-lint-carryover.py
        # injects before the next reply. So "caught" now means a note was
        # written, and exit 2 would itself be a regression.
        # The private repo root per run (HEAD, the load-flake fix) carries the
        # carry file; the fresh session id per case (rationing branch) keeps
        # cases from sharing one consolidated note. Both compose here.
        carry = os.path.join(root, "out", "chat-lint-carry", f"{session}.txt")
        env = dict(os.environ, CARR_REPO_ROOT=root)
        p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=30, env=env)
        if p.returncode == 2:
            return False, "REGRESSION: gate blocked instead of carrying"
        note = ""
        if os.path.exists(carry):
            with open(carry) as fh:
                note = fh.read()
        return bool(note.strip()), note
    finally:
        # BOTH cleanups on EVERY path, including the exit-2 regression return
        # above, which used to leave the temp tree behind for whoever came next.
        shutil.rmtree(root, ignore_errors=True)
        try:
            os.unlink(path)
        except Exception:
            pass


def main():
    global passed
    if not os.path.exists(HOOK):
        print(f"FAIL: hook not found at {HOOK}")
        return 1
    for name, text, expect in CASES:
        got, out = run_stop(text)
        ok = got == expect
        if ok:
            passed += 1
        else:
            bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:20} "
              f"want={'CAUGHT' if expect else 'clean'} got={'CAUGHT' if got else 'clean'}")

    # ── THE AUDIENCE SPLIT (2026-08-23) ────────────────────────────────────
    # The gates-audit council measured this lint firing on EIGHT messages in one
    # shipped session, several of them reading ordinary reporting prose as
    # "multi-clause tasks for Joe" when nothing was asked of him. Joe's
    # 2026-08-10 layered-enforcement ruling settled that blanket keyword gates
    # lose, and names its own reopen condition — measured false positives. These
    # are that condition, frozen as fixtures.
    #
    # THE RULES ARE NOT NARROWED, THE MATCHER IS. Rule 38b15dc6 binds
    # "multi-clause instructions TO A PARTNER"; prose instructing nobody was
    # never inside it.
    REPORTING = [
        ("report-mentions-you",
         "You'll see the count in the log now. The exporter checks the manifest "
         "and writes the render, so the nightly run no longer needs the flag."),
        ("report-two-verbs",
         "The job will read the queue and update each row in one pass. Your "
         "earlier concern about the ordering is handled by the index."),
        ("report-narrating-plan",
         "I will review the diff and then run the suite. Once that is green I "
         "will open the pull request and add the label."),
    ]
    for name, text in REPORTING:
        got, note = run_stop(text)
        ok = not got
        passed += ok
        if not ok:
            bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:28} "
              f"want=clean got={'CAUGHT' if got else 'clean'}"
              f"{'' if ok else ' :: ' + note[:200]}")

    # ...and the real shape is untouched. This is a genuine two-clause
    # instruction and must still be flagged.
    got, note = run_stop(
        "Please approve the migration and then send the LOI back to the "
        "landlord once you have read it.")
    ok = got and "multi-clause" in note
    passed += ok
    if not ok:
        bad.append("real-multi-clause-ask-still-caught")
    print(f"  {'ok  ' if ok else 'FAIL'} {'real-multi-clause-ask-still-caught':28} "
          f"want=CAUGHT got={'CAUGHT' if got else 'clean'}")

    # ── ONCE PER SESSION for reporting prose ───────────────────────────────
    # Each fire costs the next reply a block of context. A finding that is true
    # but standing does not need repeating every turn; a NEW ask does.
    shared = next_session()
    banned = CASES[0][1]                       # a wording fault, no ask in it
    first, _ = run_stop(banned, session=shared)
    passed += first
    if not first:
        bad.append("report-summary-delivered-once")
    print(f"  {'ok  ' if first else 'FAIL'} {'report-summary-first-fire':28} "
          f"want=CAUGHT got={'CAUGHT' if first else 'clean'}")

    second, note2 = run_stop(banned, session=shared)
    ok = not second
    passed += ok
    if not ok:
        bad.append("report-summary-repeats")
    print(f"  {'ok  ' if ok else 'FAIL'} {'report-summary-not-repeated':28} "
          f"want=clean got={'CAUGHT' if second else 'clean'}"
          f"{'' if ok else ' :: ' + note2[:160]}")

    # ...but a PARTNER-DIRECTED ask in the same session is never consolidated
    # away. This is the half that keeps the narrowing from becoming a mute.
    third, note3 = run_stop(
        "Please approve the migration and then send the LOI back to the "
        "landlord once you have read it.", session=shared)
    ok = third and "multi-clause" in note3
    passed += ok
    if not ok:
        bad.append("partner-ask-still-flagged-after-summary")
    print(f"  {'ok  ' if ok else 'FAIL'} {'partner-ask-after-summary':28} "
          f"want=CAUGHT got={'CAUGHT' if third else 'clean'}")

    got, _ = run_stop(CASES[0][1], stop_active=True)
    if not got:
        passed += 1
    else:
        bad.append("stop-active-loops")
    print(f"  {'ok  ' if not got else 'FAIL'} {'stop-active-never-loops':20} "
          f"want=clean got={'CAUGHT' if got else 'clean'}")

    got, out = run_stop(CASES[0][1])
    if "writing" in out.lower() or "vocab" in out.lower():
        passed += 1
        print("  ok   the carried note names what it caught")
    else:
        bad.append("carry-text")
        print(f"  FAIL the carried note names what it caught — {out[:100]}")

    print(f"\nchat-lint-gate-selftest: {passed}/{passed + len(bad)} passed")
    if bad:
        print("FAILURES: " + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
