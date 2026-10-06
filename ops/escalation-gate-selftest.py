#!/usr/bin/env python3
"""escalation-gate-selftest.py — fixtures for hooks/escalation-gate.py.

Spawns the REAL hook with a REAL AskUserQuestion payload and reads its exit
code. Exit 2 = denied, exit 0 = allowed.

THE ALLOW HALF IS THE IMPORTANT HALF. This gate sits in front of a tool a
working skill already depends on: network-debrief uses AskUserQuestion as a
tap-through for vendor verdicts and deal-stage changes, which is FACT CAPTURE
(only Joe was in the room), not a decision being offloaded. If any MUST-ALLOW
case flips to deny, the gate is breaking real work.

The boundary-change cases (weaken-gate, widen-allowlist, edit-settings) encode
the one place both council chairs overruled Joe's "internal is yours" framing:
changing the boundary itself is internal by subject and constitutional by
effect, so it must still reach him.

REBUILT 2026-08-09 after a concurrent session deleted every uncommitted file in
this build. Committed immediately this time.
"""
import json, os, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "escalation-gate.py")

CASES = [('schema-choice', 'clean up the database', 'Should the deleted_at column be nullable or use a sentinel date?', ['Nullable', 'Sentinel'], True), ('folder-structure', 'reorganise the vault', 'Which folder structure do you want for the exports?', ['Flat by domain', 'Nested by lifecycle'], True), ('naming', 'add the detector', 'What should I name the new view?', ['v_conduct_fires', 'v_gate_events'], True), ('refactor-scope', 'tidy the exporter', 'Do you want me to refactor the whole module or just the one function?', ['Whole module', 'Just the function'], True), ('job-schedule', 'set up the sweep', 'What time should the nightly job run?', ['2am', '4am'], True), ('sort-order', 'fix the render', 'Should loops sort by created date or by severity?', ['Created', 'Severity'], True), ('test-fixture', 'add coverage', 'Should the fixture live in ops/ or in tests/?', ['ops/', 'tests/'], True), ('hidden-in-options', 'improve things', 'Which approach do you prefer?', ['Rewrite the migration and drop the index', 'Patch the exporter script in place'], True), ('meeting-outcome', 'log my day', 'How did the meeting with Dr. Lindgren go?', ['Strong', 'Lukewarm', 'Dead'], False), ('vendor-verdict', 'log my day', 'Pursue or table this vendor?', ['Pursue', 'Table'], False), ('what-they-said', 'debrief me', 'What did the landlord say about the TI allowance?', ['Agreed', 'Pushed back', 'Did not come up'], False), ('delivery-grade', 'log the intro outcome', "What grade for the vendor's delivery on that intro?", ['A', 'B', 'C', 'F'], False), ('still-active', 'update the pipeline', 'Is Dr. Ashby still active, or has that gone cold?', ['Still active', 'Cold'], False), ('did-they-call', 'catch me up', 'Did they call you back this week?', ['Yes', 'No'], False), ('send-loi', 'handle the deal', 'Should I send the LOI to the listing agent today?', ['Send today', 'Hold'], False), ('publish-post', 'do the social batch', 'Do you want these published to LinkedIn?', ['Publish', 'Hold'], False), ('spend', 'sort the tooling', 'The plan renews at $240 Friday. Renew or cancel?', ['Renew', 'Cancel'], False), ('delete-records', 'clean the archive', 'Delete these 40 superseded rows permanently?', ['Delete', 'Keep'], False), ('client-tone', 'draft the follow-up', 'Which tone for the client email?', ['Warm', 'Direct'], False), ('weaken-gate', 'the gate is annoying', 'Should I disable the conduct hook so it stops blocking?', ['Disable', 'Keep'], False), ('widen-allowlist', 'curl is blocked', 'Do you want me to widen the egress allowlist to cover this host?', ['Widen', 'Leave it'], False), ('edit-settings', 'hooks are noisy', 'Should I edit settings.json to remove the lint hook?', ['Remove', 'Keep'], False), ('he-asked-options', 'lay out the options for the folder structure', 'Which folder structure do you want?', ['Flat', 'Nested'], False), ('he-asked-recommend', 'which would you recommend for the schema?', 'Nullable or sentinel?', ['Nullable', 'Sentinel'], False), ('he-said-ask-me', 'ask me before you pick the naming', 'What should I name the view?', ['v_a', 'v_b'], False)]


# Direct approvals required by Joe's 2026-10-04 rule. True means DENY.
APPROVAL_CASES = [
    ("approve-rule-restoration", "work the queue",
     "Do you approve restoring the source-study rule?", ["Approve", "Don't approve"], False),
    ("approve-retro-build", "work the queue",
     "Do you approve building all 16 retro fixes?", ["Approve", "Do not approve"], False),
    ("approve-proposed-rule", "work the queue",
     "Do you approve the proposed source-study rule?", ["Yes", "No"], False),
    ("approve-rule-retirement", "work the queue",
     "Do you not approve retiring the source-study rule?", ["Yes", "No"], False),
    ("approve-named-plan", "work the queue",
     "Will you approve the retro-repair plan?", ["Yes", "No"], False),
    ("approval-labelled-rule", "work the queue",
     "Restore the source-study rule?", ["Approve", "Don't approve"], False),
    ("approval-labelled-build", "work the queue",
     "Build all 16 retro fixes?", ["Approve", "Do not approve"], False),
    ("internal-approach", "work the queue",
     "Which approach should I take for the exporter refactor?", ["Rewrite", "Patch"], True),
    ("approve-internal-approach", "work the queue",
     "Do you approve using nullable columns in the schema?", ["Approve", "Don't approve"], True),
    ("approach-with-plan-suffix", "work the queue",
     "Do you approve using nullable columns in the schema plan?",
     ["Approve", "Do not approve"], True),
    ("schedule-with-plan-suffix", "work the queue",
     "When should I build the nightly job plan?", ["Approve", "Do not approve"], True),
    ("unnamed-build-approval", "work the queue",
     "Build it?", ["Approve", "Do not approve"], True),
    ("quoted-approach-with-plan", "work the queue",
     'Do you approve the "using nullable columns in the schema" plan?',
     ["Approve", "Do not approve"], True),
    ("implementation-with-plan-suffix", "work the queue",
     "Do you approve adding nullable columns schema plan?",
     ["Approve", "Do not approve"], True),
    ("insert-method-with-plan-suffix", "work the queue",
     "Do you approve inserting nullable columns schema plan?",
     ["Approve", "Do not approve"], True),
    ("insert-command-with-plan-suffix", "work the queue",
     "Do you approve insert nullable columns schema plan?",
     ["Approve", "Do not approve"], True),
    ("quoted-insert-method-with-plan", "work the queue",
     'Do you approve the "inserting nullable columns schema" plan?',
     ["Approve", "Do not approve"], True),
    ("insert-method-in-plan-scope", "work the queue",
     "Do you approve the schema-repair plan for inserting nullable columns?",
     ["Approve", "Do not approve"], True),
    ("configure-method-with-plan-suffix", "work the queue",
     "Do you approve configuring the schema plan?",
     ["Approve", "Do not approve"], True),
    ("edit-method-with-plan-suffix", "work the queue",
     "Do you approve editing the schema plan?",
     ["Approve", "Do not approve"], True),
    # Any -ing method word, listed or not, is a method rather than a name.
    *[(f"{verb}-method-with-plan-suffix", "work the queue",
       f"Do you approve {verb} {rest}?", ["Approve", "Do not approve"], True)
      for verb, rest in [
          ("altering", "nullable columns schema plan"),
          ("backfilling", "nullable columns schema plan"),
          ("truncating", "the log tables plan"),
          ("seeding", "fixture tables plan"),
          ("patching", "the exporter script plan"),
          ("rewriting", "the migration plan"),
          ("deploying", "the worker build"),
          ("disabling", "nightly job plan"),
          ("moving", "export folders plan")]],
    ("plan-scope-choice", "work the queue",
     "Do you approve the schema-repair plan for nullable or sentinel columns?",
     ["Approve", "Do not approve"], True),
    ("approve-quoted-plan", "work the queue",
     'Do you approve the "schema-repair" plan?', ["Approve", "Do not approve"], False),
    ("approve-single-quoted-plan", "work the queue",
     "Do you approve the 'schema-repair' plan?", ["Yes", "No"], False),
    ("approve-plan-with-scope", "work the queue",
     "Do you approve the schema-repair plan for the nightly job?",
     ["Approve", "Do not approve"], False),
    ("approve-quoted-plan-with-scope", "work the queue",
     'Do you approve the "schema-repair" plan for the nightly job?',
     ["Approve", "Do not approve"], False),
    ("approve-onboarding-plan", "work the queue",
     "Do you approve the onboarding plan?",
     ["Approve", "Do not approve"], False),
    ("approve-quoted-onboarding-plan", "work the queue",
     'Do you approve the "onboarding" plan?',
     ["Approve", "Do not approve"], False),
    ("approve-onboarding-plan-with-scope", "work the queue",
     "Do you approve the onboarding plan for the nightly job?",
     ["Approve", "Do not approve"], False),
    ("approve-restoration-as-written", "work the queue",
     "Do you approve restoring the source-study rule as written?",
     ["Approve", "Do not approve"], False),
    ("approve-quoted-restoration", "work the queue",
     'Do you approve restoring the "source-study" rule as written?',
     ["Approve", "Do not approve"], False),
    ("labels-dont-own-approach", "work the queue",
     "Which approach should I take for restoring the source-study rule?",
     ["Approve", "Don't approve"], True),
    ("short-approach-isnt-approval", "work the queue",
     "Which approach for the schema rule?", ["Approve", "Don't approve"], True),
    ("unnamed-rule-approval", "work the queue",
     "Do you approve the proposed rule for the schema?", ["Approve", "Don't approve"], True),
    ("approval-word-in-option", "work the queue",
     "Which approach for the exporter refactor?",
     ["Approve the retro-repair plan", "Patch the exporter"], True),
]

# ── the async spelling (rule e065aa82): add-loop calls that park a ruling ────
# (name, human_last, tool_name, tool_input, expect_deny)
LOOP_CASES = [
    ("loop-approval-stays-internal", "work the queue", "mcp__carr__add-loop",
     {"kind": "open_loop", "owner": "Joe", "marker": "decision",
      "body": "Do you approve restoring the source-study doctrine rule?"}, True),
    ("loop-internal-decision", "work the queue", "mcp__carr__add-loop",
     {"kind": "open_loop", "owner": "Joe", "marker": "decision",
      "body": "Should loops sort by created date or by severity in the render?"},
     True),
    ("loop-ruling-blocker", "work the queue", "mcp__carr__add-loop",
     {"kind": "open_loop", "owner": "Joe", "blocker": "ruling",
      "blocker_detail": "whether the exporter script should be refactored into modules",
      "body": "restructure the exporter"},
     True),
    ("loop-protected-decision", "work the queue", "mcp__carr__add-loop",
     {"kind": "open_loop", "owner": "Joe", "marker": "decision",
      "body": "Send the LOI to the listing agent at the revised price, or hold?"},
     False),
    ("loop-boundary-decision", "work the queue", "mcp__carr__add-loop",
     {"kind": "open_loop", "owner": "Joe", "marker": "decision",
      "body": "Widen the egress allowlist so the enrichment fetch can reach the vendor host?"},
     False),
    ("loop-backlog-internal", "work the queue", "mcp__carr__add-loop",
     {"kind": "open_loop", "owner": "Joe", "marker": "none",
      "blocker": "capability", "blocker_detail": "needs the deploy token",
      "body": "refactor the exporter into modules"},
     False),
    ("loop-he-asked", "ask me before you pick the naming", "mcp__carr__add-loop",
     {"kind": "open_loop", "owner": "Joe", "marker": "decision",
      "body": "What should the new view be named?"},
     False),
    ("record-defect-untouched", "work the queue", "mcp__carr__record-defect",
     {"claimed": "the nightly render never ran", "actual": "schema drift"},
     False),
]


def spawn(payload):
    p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=30)
    if p.returncode not in (0, 2):
        raise AssertionError(f"Unexpected hook exit {p.returncode}: {p.stderr}")
    return p.returncode == 2


def with_transcript(human, build_payload):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps({"type":"user","origin":{"kind":"user"},
                "message":{"content":[{"type":"text","text":human}]}}) + "\n")
        return spawn(build_payload(path))
    finally:
        try: os.unlink(path)
        except Exception: pass


def run_case(human, question, options, description=""):
    return with_transcript(human, lambda path: {
        "tool_name":"AskUserQuestion","transcript_path":path,
        "session_id":"selftest",
        "tool_input":{"questions":[{"question":question,"header":"Q",
            "multiSelect":False,
            "options":[{"label":o,"description":description} for o in options]}]}})


def run_loop_case(human, tool_name, tool_input):
    return with_transcript(human, lambda path: {
        "tool_name": tool_name, "transcript_path": path,
        "session_id": "selftest", "tool_input": tool_input})


def run_multi_case(human, items):
    """A MULTI-ITEM interview — the shape the 2026-08-23 council called the
    escalation gate's blind spot. items = [(question, options), ...]."""
    def build(path):
        questions = []
        for q, opts in items:
            questions.append({"question": q, "header": "Q", "multiSelect": False,
                              "options": [{"label": o, "description": ""}
                                          for o in opts]})
        return {"tool_name": "AskUserQuestion", "transcript_path": path,
                "session_id": "selftest", "tool_input": {"questions": questions}}
    return with_transcript(human, build)


# ── MULTI-ITEM interviews (the blind spot the 2026-08-23 gates-audit council
# named): classification must be per QUESTION, so one fact-capture item cannot
# exempt an internal item riding in the same call. ────────────────────────────
# (name, human_last, [(question, options), ...], expect_deny)
MULTI_CASES = [
    ("approval-cannot-exempt-internal", "work the queue",
     [("Do you approve restoring the source-study rule?", ["Approve", "Don't approve"]),
      ("Which approach should I take for the exporter refactor?", ["Rewrite", "Patch"])], True),
    ("mixed-fact-then-internal", "work the queue",
     [("How did the meeting with Dr. Lindgren go?", ["Strong", "Lukewarm"]),
      ("Should the deleted_at column be nullable?", ["Nullable", "Sentinel"])],
     True),
    ("mixed-internal-first", "work the queue",
     [("Which folder structure for the exports?", ["Flat", "Nested"]),
      ("Did they call you back this week?", ["Yes", "No"])],
     True),
    ("all-fact-capture", "log my day",
     [("How did the meeting go?", ["Strong", "Lukewarm"]),
      ("Pursue or table this vendor?", ["Pursue", "Table"])],
     False),
    ("single-item-still-denied", "work the queue",
     [("Should the nightly job run at 2am or 4am?", ["2am", "4am"])],
     True),
]


# ── ATTEMPT FIRST (Joe, 2026-09-23): a question may hand Joe a command only
# after the harness refused the session that command this turn. The real
# failure: `brew trust` put in front of Joe, then run by the session itself.
# (name, human_last, attempted command or None, denied?, question, options, expect_deny)
DENIAL = ("Permission for this action was denied by the Claude Code auto mode "
          "classifier. Reason: fixture.")
ATTEMPT_CASES = [
    ("approval-cannot-exempt-command", "work the queue", None, False,
     "Do you approve the retro-repair plan? Please run `python3 fix.py`.",
     ["Approve", "Don't approve"], True),
    ("untried-inline-command", "yes install it", None, False,
     "Can you run `brew trust --formula ddalcu/mlx-serve/mlx-serve` for me?",
     ["Done", "Skip"], True),
    ("untried-fenced-command", "yes install it", None, False,
     "Please run this:\n```bash\nbrew install mlx-serve\n```",
     ["Done", "Skip"], True),
    ("tried-and-refused", "yes install it",
     "brew trust --formula ddalcu/mlx-serve/mlx-serve", True,
     "The classifier refused `brew trust --formula ddalcu/mlx-serve/mlx-serve`. Will you run it?",
     ["Done", "Skip"], False),
    ("tried-and-succeeded", "yes install it",
     "brew trust --formula ddalcu/mlx-serve/mlx-serve", False,
     "Can you run `brew trust --formula ddalcu/mlx-serve/mlx-serve`?",
     ["Done", "Skip"], True),
    ("other-command-refused", "yes install it", "git push --force", True,
     "Can you run `brew install mlx-serve`?", ["Done", "Skip"], True),
    ("he-asked-how", "how do I install mlx-serve myself?", None, False,
     "Want the one-liner `brew install mlx-serve` or the app?", ["CLI", "App"], False),
]


def run_attempt_case(human, attempted, denied, question, options):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps({"type": "user", "origin": {"kind": "user"},
                "message": {"content": [{"type": "text", "text": human}]}}) + "\n")
            if attempted:
                fh.write(json.dumps({"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "id": "t1", "name": "Bash",
                     "input": {"command": attempted}}]}}) + "\n")
                fh.write(json.dumps({"type": "user", "message": {"content": [
                    {"type": "tool_result", "tool_use_id": "t1",
                     "content": DENIAL if denied else "ok"}]}}) + "\n")
        return spawn({"tool_name": "AskUserQuestion", "transcript_path": path,
                      "session_id": "selftest",
                      "tool_input": {"questions": [{"question": question, "header": "Q",
                          "multiSelect": False,
                          "options": [{"label": o, "description": ""} for o in options]}]}})
    finally:
        try: os.unlink(path)
        except Exception: pass


def main():
    if not os.path.exists(HOOK):
        print(f"FAIL: hook not found at {HOOK}"); return 1
    passed = failed = 0; bad = []
    approval_names = {case[0] for case in APPROVAL_CASES}
    for name, human, q, opts, expect in CASES + APPROVAL_CASES:
        # These interviews concern internal doctrine/jobs; that context in
        # option descriptions is what the old whole-item classifier refuses.
        description = ("The source-study doctrine and retro-repair jobs."
                       if name in approval_names else "")
        got = run_case(human, q, opts, description)
        ok = (got == expect)
        passed, failed = (passed+1, failed) if ok else (passed, failed+1)
        if not ok: bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:24} "
              f"want={'DENY ' if expect else 'allow'} got={'DENY' if got else 'allow'}")
    for name, human, tool_name, tool_input, expect in LOOP_CASES:
        got = run_loop_case(human, tool_name, tool_input)
        ok = (got == expect)
        passed, failed = (passed+1, failed) if ok else (passed, failed+1)
        if not ok: bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:24} "
              f"want={'DENY ' if expect else 'allow'} got={'DENY' if got else 'allow'}")
    for name, human, items, expect in MULTI_CASES:
        got = run_multi_case(human, items)
        ok = (got == expect)
        passed, failed = (passed+1, failed) if ok else (passed, failed+1)
        if not ok: bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:24} "
              f"want={'DENY ' if expect else 'allow'} got={'DENY' if got else 'allow'}")
    for name, human, attempted, denied, q, opts, expect in ATTEMPT_CASES:
        got = run_attempt_case(human, attempted, denied, q, opts)
        ok = (got == expect)
        passed, failed = (passed+1, failed) if ok else (passed, failed+1)
        if not ok: bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:24} "
              f"want={'DENY ' if expect else 'allow'} got={'DENY' if got else 'allow'}")
    print()
    print(f"escalation-gate-selftest: {passed}/{passed+failed} passed")
    if bad:
        print("FAILURES: " + ", ".join(bad)); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
