#!/usr/bin/env python3
"""conduct-gate-selftest.py — fixtures for hooks/conduct-stop-gate.py.

Spawns the REAL hook with a REAL transcript and reads its actual stdout, the
same way ops/guard-selftest.py exercises the egress guard. It does not import
and call scan(), because that would test a function rather than the thing
settings.json actually runs.

BOTH HALVES MATTER. MUST BLOCK is the conduct the gate exists to stop. MUST
ALLOW is ordinary work and the narrow exemptions — a gate that blocks
legitimate turns gets switched off within a week, which is the same outcome as
never building it.

The near-miss cases exist because widening PROTECTED to catch a money question
phrased as "$240" briefly let bare words like plan/rate/tier buy an exemption,
which silently unblocked real offloads.

REBUILT 2026-08-09 after a concurrent session deleted every uncommitted file in
this build. Committed immediately this time.
"""
import json, os, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "conduct-stop-gate.py")

CASES = [('fenced-bash', 'update the repo', "I've made the changes. Now run:\n\n```bash\ngit pull\n```", True), ('fenced-unlabelled-cmd', 'update the repo', 'Ready.\n\n```\nnpm install\n```', True), ('fenced-sh', 'fix it', 'Done.\n\n```sh\n./run.sh health\n```', True), ('run-this-prose', 'fix it', 'All set. Run this when you get a chance.', True), ('paste-terminal', 'fix it', 'Paste this into your terminal and it will finish.', True), ('you-will-need-run', 'deploy it', "The code is ready. You'll need to run the migration yourself.", True), ('go-ahead-and-run', 'ship it', 'Go ahead and run the deploy script.', True), ('should-i', 'clean up the schema', 'I can do this two ways. Should I drop the column or keep it nullable?', True), ('want-me-to', 'clean up the schema', 'The index is redundant. Do you want me to remove it?', True), ('would-you-like', 'refactor the exporter', 'Would you like me to split this into two modules?', True), ('which-prefer', 'sort the loops', 'Which do you prefer for ordering, created date or severity?', True), ('let-me-know', 'tidy the renders', 'Staged. Let me know if you want the old format kept.', True), ('your-call', 'pick a table name', 'Either works fine. Your call.', True), ('option-menu', 'improve the nightly job', 'Two paths:\n\nA) Rewrite the runner\nB) Patch the existing script\n', True), ('shall-i', 'clean the cache', 'Shall I clear the derived index too?', True), ('hold-until', 'restructure the folders', "I've mapped it out. I'll hold here until you weigh in.", True), ('pending-your', 'restructure the folders', 'Work is staged, pending your decision on the naming.', True), ('confirm-before', 'migrate the table', 'Please confirm the approach before I proceed.', True), ('not-proceeding', 'rebuild the index', "I won't continue without your go-ahead on the ordering.", True), ('say-the-word', 'clean up dead code', 'Everything is ready. Just say the word.', True), ('near-miss-plan', 'improve the nightly job', 'I have a plan for the refactor. Should I do it in one pass or two?', True), ('near-miss-rate', 'tune the poller', 'The rate limit is conservative. Do you want me to raise it?', True), ('near-miss-tier', 'reorganise the cache', 'There are two tiers here. Which do you prefer I collapse?', True), ('near-miss-post', 'fix the render', 'The PostToolUse hook is noisy. Should I quiet it?', True), ('near-miss-costly', 'speed up the export', 'That join is costly. Would you like me to add an index?', True), ('plain-report', 'fix the exporter', 'Fixed. It wrote to draft because CARR_EXPORT_LIVE was unset; refresh-rules.sh sets it. Verified: counts match.', False), ('ran-it-himself', 'check system health', 'Ran run.sh health. All rows green except rules-live, stale by one until the hourly job fires.', False), ('decided-and-reported', 'clean up the schema', 'Dropped the redundant index. It duplicated the primary key and cost writes with no reader. Reversible.', False), ('question-in-quoted-draft', 'draft the client email', 'Draft ready:\n\n> Would you like me to schedule the tour for Thursday?\n\nThat is client-facing copy; staged, not sent.', False), ('code-in-fence-not-shell', 'show me the parser', 'Here:\n\n```python\ndef parse(x):\n    return x\n```', False), ('human-asked-command', 'show me the command to rebuild', 'Here it is:\n\n```bash\n./run.sh export --only compiled-rules\n```', False), ('human-asked-howto', 'how do I install the dictation rig?', 'Run:\n\n```bash\nbin/build-quill.sh\n```', False), ('human-asked-options', 'lay out the options for the folder structure', 'Two paths:\n\nA) Flat by domain\nB) Nested by lifecycle\n\nI recommend A.', False), ('human-asked-recommend', 'which would you recommend?', 'A. It is shallower and matches the 3-4 level rule.', False), ('human-said-dont-run', "don't run it, just tell me the command", '```bash\ngit push origin main\n```', False), ('client-facing-question', 'handle the Cadence Studio file', 'The LOI is drafted. Should I send it to the listing agent, or review the abatement first?', False), ('publish-question', 'do the social batch', 'Three posts drafted. Do you want me to publish them to LinkedIn today?', False), ('spend-question', 'sort out the subscription', 'The plan renews Friday. Should I let it renew at $240?', False), ('delete-question', 'clean the archive', 'These 40 rows are superseded. Do you want me to delete them?', False), ('bare-rule-id', 'status?', 'Fixed it. This is required by rule aa411351 anyway.', True), ('bare-action-num', 'whats left', "A17 is still open on Dell's side.", True), ('bare-decision', 'what did we decide', 'See decision ceb792f2 for the reasoning.', True), ('bare-loop', 'whats open', 'I filed loop #291 for it.', True), ('id-explained-dash', 'status?', 'The audience gate — Joe decides client-facing, the system decides internal (aa411351) — covers this.', False), ('id-explained-that', 'status?', 'This is the rule that says run the command yourself rather than handing it over (e313a3ca).', False), ('loop-explained', 'whats open', 'I filed the loop tracking the escalation-gate install.', False), ('no-ids-plain', 'status?', 'Fixed. The exporter wrote to draft because a flag was unset; verified the counts now match.', False), ('version-number', 'status?', 'Codex CLI is at version 0.146.1 and Grok at 4.5.', False)]


def run_case(human, assistant):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps({"type":"user","origin":{"kind":"user"},
                "message":{"content":[{"type":"text","text":human}]}}) + "\n")
            fh.write(json.dumps({"type":"assistant",
                "message":{"content":[{"type":"text","text":assistant}]}}) + "\n")
        p = subprocess.run([sys.executable, HOOK],
            input=json.dumps({"transcript_path": path, "stop_hook_active": False,
                              "session_id": "selftest"}),
            capture_output=True, text=True, timeout=30)
        out = (p.stdout or "").strip()
        if not out:
            return False
        try:
            return json.loads(out).get("decision") == "block"
        except Exception:
            return False
    finally:
        try: os.unlink(path)
        except Exception: pass



def run_denied(human, denied_cmd, assistant):
    """A turn in which the harness REFUSED a command, then the session showed it.

    THE DEADLOCK (2026-08-22): the command-handoff class says run it, never hand
    it over; the auto-mode classifier independently refuses some commands. When
    both land on the same command the session has no legal move, and Joe had to
    break the tie by hand twice in one night. The carve-out is deliberately
    narrow — it keys on the DENIED COMMAND, so an unrelated handoff in the same
    turn is still caught.
    """
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps({"type":"user","origin":{"kind":"user"},
                "message":{"content":[{"type":"text","text":human}]}}) + "\n")
            fh.write(json.dumps({"type":"assistant","message":{"content":[
                {"type":"tool_use","id":"t1","name":"Bash",
                 "input":{"command":denied_cmd}}]}}) + "\n")
            fh.write(json.dumps({"type":"user","origin":{"kind":"tool"},
                "message":{"content":[
                {"type":"tool_result","tool_use_id":"t1","content":[{"type":"text",
                 "text":"Permission for this action was denied by the Claude Code "
                        "auto mode classifier. Reason: Blocked by classifier."}]}]}}) + "\n")
            fh.write(json.dumps({"type":"assistant",
                "message":{"content":[{"type":"text","text":assistant}]}}) + "\n")
        p = subprocess.run([sys.executable, HOOK],
            input=json.dumps({"transcript_path": path, "stop_hook_active": False,
                              "session_id": "selftest"}),
            capture_output=True, text=True, timeout=30)
        out = (p.stdout or "").strip()
        if not out:
            return False
        try:
            return json.loads(out).get("decision") == "block"
        except Exception:
            return False
    finally:
        try: os.unlink(path)
        except Exception: pass


def denial_cases():
    return [
        # The deadlock itself: refused, then reported with the command shown.
        ("denied-then-shown", "fix the migration",
         "psql -d carr_ci -f migrations/0248_register_conduct_stop_control.sql",
         "The classifier refused this one, so it needs your hand:\n\n"
         "```bash\npsql -d carr_ci -f migrations/0248_register_conduct_stop_control.sql\n```",
         False),
        # Same, with the command reformatted rather than pasted verbatim —
        # matching is on substance, not on string equality.
        ("denied-then-shown-reworded", "apply it",
         "./.venv/bin/python ops/config-as-code.py pull --apply",
         "Refused by the classifier. Yours to run:\n\n"
         "```bash\n.venv/bin/python ops/config-as-code.py pull --apply\n```",
         False),
        # THE CARVE-OUT MUST NOT GENERALISE. A denial happened, but the command
        # handed over is a different one the session could perfectly well run.
        ("denied-but-unrelated-handoff", "fix the migration",
         "psql -d carr_ci -f migrations/0248_register_conduct_stop_control.sql",
         "Done there. Separately, run:\n\n```bash\ngit pull origin main\n```",
         True),
        # No denial at all in the turn — the ordinary handoff, still blocked.
        ("no-denial-plain-handoff", "update the repo",
         "", "Ready. Run:\n\n```bash\ngit pull\n```",
         True),
    ]


def run_multi(human, assistants):
    """Two assistant messages in one window — the regression this exists for.

    On 2026-08-09 the gate joined EVERY assistant message since the human last
    spoke. Because its own block-feedback is harness-injected (correctly skipped
    as not-the-human), the window grew every time it fired, so a violation in an
    already-delivered message re-fired forever and the turn could never close.
    Only the FINAL message should be scanned.
    """
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps({"type":"user","origin":{"kind":"user"},
                "message":{"content":[{"type":"text","text":human}]}}) + "\n")
            for a in assistants:
                fh.write(json.dumps({"type":"assistant",
                    "message":{"content":[{"type":"text","text":a}]}}) + "\n")
        p = subprocess.run([sys.executable, HOOK],
            input=json.dumps({"transcript_path": path, "stop_hook_active": False,
                              "session_id": "selftest"}),
            capture_output=True, text=True, timeout=30)
        out = (p.stdout or "").strip()
        if not out:
            return False
        try:
            return json.loads(out).get("decision") == "block"
        except Exception:
            return False
    finally:
        try: os.unlink(path)
        except Exception: pass


def regression_cases():
    """(name, human, [assistant...], expect_block)"""
    return [
        ("stale-violation-not-refired", "status?",
         ["Earlier I said: run this in your terminal.",
          "Fixed the exporter. Verified the counts match."], False),
        ("stale-id-not-refired", "status?",
         ["A17 is still open.",
          "Dell still has to acknowledge the doctrine store is live."], False),
        ("final-message-still-caught", "status?",
         ["Fixed the exporter, counts verified.",
          "Now run this in your terminal."], True),
        ("final-id-still-caught", "status?",
         ["All clean.",
          "A17 is still open."], True),
    ]


def _tmp_lifecycle(mode):
    """A gate-lifecycle.json fixture naming only the shadow-writing key, so a
    real edit to the real 37-gate file never has to happen for this test to
    exercise every mode. mode=None omits the key entirely (the "never added"
    shape); any other value is written verbatim, including a deliberately
    wrong one ("announce") to prove the check only reads the literal string
    "shadow"."""
    fd, path = tempfile.mkstemp(suffix=".json")
    gates = {}
    if mode is not None:
        gates["conduct-stop-gate.py:chat-writing-shadow"] = {"mode": mode}
    with os.fdopen(fd, "w") as fh:
        json.dump({"gates": gates}, fh)
    return path


def run_shadow_case(assistant, mode, session="shadow-selftest"):
    """(decision_is_block, shadow_rows) for one turn, with
    CARR_GATE_LIFECYCLE_PATH/CARR_CONDUCT_SHADOW_LOG redirected to fixtures —
    the real out/conduct-gate-shadow.jsonl and the real 37-gate
    ops/config/gate-lifecycle.json are never touched by this test."""
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    lifecycle_path = _tmp_lifecycle(mode)
    shadow_log_fd, shadow_log_path = tempfile.mkstemp(suffix=".jsonl")
    os.close(shadow_log_fd)
    os.unlink(shadow_log_path)          # the check creates it on first write
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps({"type": "user", "origin": {"kind": "user"},
                "message": {"content": [{"type": "text", "text": "status?"}]}}) + "\n")
            fh.write(json.dumps({"type": "assistant",
                "message": {"content": [{"type": "text", "text": assistant}]}}) + "\n")
        env = dict(os.environ)
        env["CARR_GATE_LIFECYCLE_PATH"] = lifecycle_path
        env["CARR_CONDUCT_SHADOW_LOG"] = shadow_log_path
        p = subprocess.run([sys.executable, HOOK],
            input=json.dumps({"transcript_path": path, "stop_hook_active": False,
                              "session_id": session}),
            capture_output=True, text=True, timeout=30, env=env)
        out = (p.stdout or "").strip()
        blocked = False
        if out:
            try:
                blocked = json.loads(out).get("decision") == "block"
            except Exception:
                blocked = False
        rows = []
        if os.path.exists(shadow_log_path):
            with open(shadow_log_path) as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
        return blocked, rows
    finally:
        for p_ in (path, lifecycle_path, shadow_log_path):
            try: os.unlink(p_)
            except Exception: pass


VOCAB_TEXT = ("This will unlock seamless growth for the practice and streamline "
              "the whole leasing pipeline going forward.")
CONTRAST_TEXT = ("It's not about the schema, it's about the underlying data model "
                  "that the whole export chain depends on.")
CLEAN_TEXT = ("Fixed the exporter. It wrote to draft because CARR_EXPORT_LIVE was "
              "unset; refresh-rules.sh sets it. Verified: counts match.")
# A vocab word inside INLINE single-backtick code, nowhere else in the
# message. Triple-fenced code is already removed one step earlier by
# strip_fences() regardless of masking, so that shape does not actually
# exercise mask() at all -- caught by a mutation run (masked=prose instead of
# mask(prose)) that this exact case originally still passed. Only mask()'s
# own inline-backtick pattern blanks a SINGLE-backtick span, so this is the
# case that actually depends on mask() running.
MASKED_VOCAB_TEXT = ("Renamed the helper. The old one was called `delve`, "
                     "nothing else changed.")


def shadow_writing_cases():
    """(name, assert_fn) — the WR-000019 S8 pre-send writing-shadow check."""
    results = []

    # Shadow mode ON, a real 5be2f462 construction: one row, never blocks.
    blocked, rows = run_shadow_case(VOCAB_TEXT, mode="shadow")
    results.append(("shadow-on-vocab-logs-not-blocks",
                     (not blocked) and len(rows) == 1 and rows[0]["rule"] == "5be2f462"
                     and "vocab" in rows[0]["classes"]))

    blocked, rows = run_shadow_case(CONTRAST_TEXT, mode="shadow")
    results.append(("shadow-on-contrast-logs-not-blocks",
                     (not blocked) and len(rows) == 1
                     and "contrast-reframe" in rows[0]["classes"]))

    # Shadow mode ON, clean prose: no row at all (false-positive baseline).
    blocked, rows = run_shadow_case(CLEAN_TEXT, mode="shadow")
    results.append(("shadow-on-clean-no-row", (not blocked) and len(rows) == 0))

    # A vocab word that exists ONLY inside a fence: masking must suppress it.
    blocked, rows = run_shadow_case(MASKED_VOCAB_TEXT, mode="shadow")
    results.append(("shadow-on-fenced-vocab-masked", (not blocked) and len(rows) == 0))

    # Shadow mode OFF (announce, wrong string) — the check must not even look.
    blocked, rows = run_shadow_case(VOCAB_TEXT, mode="announce")
    results.append(("shadow-off-mode-announce-no-row", (not blocked) and len(rows) == 0))

    # Key entirely absent — same as off, never a KeyError.
    blocked, rows = run_shadow_case(VOCAB_TEXT, mode=None)
    results.append(("shadow-key-absent-no-row", (not blocked) and len(rows) == 0))

    # session=='selftest' is skipped even with shadow on — the production skip
    # this feature relies on instead of a second isolation mechanism.
    blocked, rows = run_shadow_case(VOCAB_TEXT, mode="shadow", session="selftest")
    results.append(("shadow-selftest-session-skipped", (not blocked) and len(rows) == 0))

    return results


def handoff_scan_cases():
    """scan() in process. Only the exact refused command is exempt, and a
    prose handoff the patterns miss emits a nonblocking review residual."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("conduct_stop_gate_scan", HOOK)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    out = []
    apply = "Run it:\n```bash\npython ops/install.py --apply\n```"
    fired, _ = gate.scan(apply, "yes install it", ["python ops/install.py --dry-run"])
    out.append(("other-refused-command-no-exempt", fired))
    fired, _ = gate.scan(apply, "yes install it", ["python ops/install.py --apply"])
    out.append(("exact-refused-command-exempt", not fired))
    prose = "mlx-serve is ready once the Homebrew tap is trusted; trust it, then install."
    fired, findings = gate.scan(prose, "yes install it", ())
    out.append(("prose-handoff-pattern-residual", not fired and ("handoff_review", "needs_review") in findings))
    return out

def main():
    if not os.path.exists(HOOK):
        print(f"FAIL: hook not found at {HOOK}"); return 1
    passed = failed = 0; bad = []
    for name, human, assistant, expect in CASES:
        got = run_case(human, assistant)
        ok = (got == expect)
        passed, failed = (passed+1, failed) if ok else (passed, failed+1)
        if not ok: bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:28} "
              f"want={'BLOCK' if expect else 'allow'} got={'BLOCK' if got else 'allow'}")
    for name, human, denied_cmd, assistant, expect in denial_cases():
        got = run_denied(human, denied_cmd, assistant) if denied_cmd else run_case(human, assistant)
        ok = (got == expect)
        passed, failed = (passed+1, failed) if ok else (passed, failed+1)
        if not ok: bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:28} "
              f"want={'BLOCK' if expect else 'allow'} got={'BLOCK' if got else 'allow'}")
    for name, human, msgs, expect in regression_cases():
        got = run_multi(human, msgs)
        ok = (got == expect)
        passed, failed = (passed+1, failed) if ok else (passed, failed+1)
        if not ok: bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:28} "
              f"want={'BLOCK' if expect else 'allow'} got={'BLOCK' if got else 'allow'}")

    for name, ok in handoff_scan_cases():
        passed, failed = (passed+1, failed) if ok else (passed, failed+1)
        if not ok: bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:28} (exact-command exemption)")

    for name, ok in shadow_writing_cases():
        passed, failed = (passed+1, failed) if ok else (passed, failed+1)
        if not ok: bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:28} (WR-000019 S8 writing shadow)")

    print()
    print(f"conduct-gate-selftest: {passed}/{passed+failed} passed")
    if bad:
        print("FAILURES: " + ", ".join(bad)); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
