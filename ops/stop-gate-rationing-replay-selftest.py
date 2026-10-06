#!/usr/bin/env python3
"""stop-gate-rationing-replay-selftest.py — replay the 2026-08-23 session shape
against the live gates, and score it.

WHY THIS FILE EXISTS RATHER THAN A PARAGRAPH IN A PULL REQUEST. Joe's acceptance
criterion for the Stop-gate rationing was not "the tests pass" — it was a
scoreboard on one specific, already-measured session:

    the five correct catches still catch
    the eight lint fires on reporting prose become zero
    the duplicate completion fire becomes zero
    reopens per shipped session drop by most
    nothing new can reopen without an admission card

That is a claim about the whole stack at once, and every individual gate's own
selftest can be green while it is false. So the claim is compiled here, and it
keeps running: the 48-hour test the council set is a bar this has to still clear
tomorrow, not a sentence somebody wrote today.

THE LEDGER BEING REPLAYED is in the council brief
(out/council/gates-audit-20260823/brief.md), where one real working session that
ran five councils and shipped three pull requests had every gate intervention it
experienced classified by hand: 5 CATCH, ~9 NOISE, 2 DEADEND. Grok's chair named
the target shape 5 : <=2 : 0.

WHAT IT DOES NOT COVER, said plainly so nobody reads a clean run as more than it
is:

  · THE TWO DEAD ENDS were harness-level — the Claude Code permission classifier
    denying a receipted incident close and then denying report-problem, the verb
    that exists so a session can file a block. No repo gate can fix or test
    that; the council's M1 assigns it to the classifier allowlist. It is out of
    scope here and stays at 2 until that lands.
  · THE DUPLICATE COMPLETION FIRE is now wired and is asserted here against the
    LIVE gate, not against the primitive. It was handed to busy-williamson-41dbc1,
    who built the widened gate (fix A), then handed the wiring back with the
    reason: hooks/stop_latch.py exists only on this branch, so an import from
    theirs would raise at module load — before main()'s try/except, so it would
    not even fail open — and two branches adding the same file collide in the
    gate baseline. Their branch is merged here and the latch sits on top of it.
    ops/completion-evidence-gate-selftest.py owns the detailed cases; this file
    asserts only that the duplicate is gone end to end.
  · NOISE IS COUNTED AS REOPENS, not as findings. Context guidance and the four
    gates that remain demoted still make their findings and still write audit
    rows. What is being scored is what the session was CHARGED, because that is
    what the rationing changed.

    .venv/bin/python ops/stop-gate-rationing-replay-selftest.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
CANONICAL = os.path.expanduser("~/carr-system")

failures: list[str] = []
notes: list[str] = []
passed = 0


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name}{' :: ' + detail if detail else ''}")


def fire(hook, payload, env=None, repo=REPO):
    """Run a real hook and classify what it COST, not merely what it said.

    REOPEN  the turn is charged another assistant message: exit 2, or a
            {"decision": "block"} on stdout.
    ANNOUNCE the finding rides into context for free.
    DENY    a PreToolUse refusal (its own register; costs no extra turn).
    SILENT  nothing.

    Exit codes are read from the process, never through a pipe: a shell pipeline
    reports the LAST command's status, and a gate's exit code disappearing into
    `| head` is how a refusal gets read as an allow.
    """
    path = hook if os.path.isabs(hook) else os.path.join(repo, "hooks", hook)
    if not os.path.exists(path):
        return "MISSING", ""
    p = subprocess.run([PY, path], input=json.dumps(payload), capture_output=True,
                       text=True, timeout=60, env={**os.environ, **(env or {})})
    out, err = p.stdout or "", p.stderr or ""
    if p.returncode == 2:
        return "REOPEN", err or out
    try:
        body = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
    except (ValueError, IndexError):
        body = {}
    if body.get("decision") == "block":
        return "REOPEN", body.get("reason", "")
    hso = body.get("hookSpecificOutput") or {}
    if hso.get("permissionDecision") == "deny":
        return "DENY", hso.get("permissionDecisionReason", "")
    if hso.get("additionalContext"):
        return "ANNOUNCE", hso["additionalContext"]
    if out.strip() and "ANNOUNCED" in out:
        return "ANNOUNCE", out
    return "SILENT", out + err


def transcript(tmp, name, assistant, human="what's left?", tools=()):
    path = os.path.join(tmp, name)
    with open(path, "w") as fh:
        fh.write(json.dumps({"type": "user", "origin": {"kind": "user"},
                             "message": {"content": [{"type": "text",
                                                      "text": human}]}}) + "\n")
        for tool, ti in tools:
            fh.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": tool, "input": ti}]}}) + "\n")
        fh.write(json.dumps({"type": "assistant",
                             "message": {"content": [{"type": "text",
                                                      "text": assistant}]}}) + "\n")
    return path


# ── the five correct catches, from the brief's own descriptions ─────────────

def replay_catches(tmp):
    print("\n  ── the five correct catches must still catch")

    # 1. "canonical-edit gate refused a tracked-file edit in the shared checkout".
    # Driven against the CANONICAL copy on purpose: the gate resolves its repo
    # from its own location, so a worktree copy sees worktree paths as the
    # remedy and correctly allows them. Absent canonical checkout is reported,
    # never silently passed.
    hook = os.path.join(CANONICAL, "hooks", "canonical-edit-gate.py")
    if not os.path.exists(hook):
        notes.append("canonical-edit: no ~/carr-system checkout on this machine, "
                     "so its catch could not be replayed here")
        print("  NOTE canonical-edit not replayable — no canonical checkout")
    else:
        verdict, text = fire(hook, {
            "tool_name": "Edit", "session_id": "selftest",
            "tool_input": {"file_path": os.path.join(CANONICAL, "pipelines/lead-promote.py"),
                           "new_string": "x"}})
        check("canonical-edit still refuses a tracked edit in the shared tree",
              verdict in ("REOPEN", "DENY") and "worktree" in text.lower(),
              f"{verdict}: {text[:120]}")

    # 2. "executor-tier gate refused an Agent spawn with no model named".
    # Since #1228 (ruling 5ec806a4) the catch is no longer "always deny": a
    # no-model spawn is DENIED when Jev cannot pick a tier at or above the
    # acting threshold, and ALLOWED WITH JEV'S TIER FILLED IN when it can. Either
    # way the spawn never runs on an unnamed, inherited tier, which is what the
    # 2026-08-23 catch was for. Jev is pinned through the gate's own
    # CARR_EXECUTOR_TIER_JEV_STUB seam, so this asserts the same thing whether
    # or not the live judge is reachable, and makes no network call.
    spawn = {"description": "sweep the logs", "prompt": "count the DENY lines"}
    gate = os.path.join(REPO, "hooks", "executor-tier-gate.py")
    base = {k: v for k, v in os.environ.items()
            if k not in ("CARR_EXECUTOR_TIER_JEV_STUB", "CARR_HOOK_FIXTURE")}

    def tier_gate(stub):
        p = subprocess.run([PY, gate], capture_output=True, text=True, timeout=60,
                           input=json.dumps({"tool_name": "Agent", "session_id": "selftest",
                                             "tool_input": spawn}),
                           env={**base, "CARR_EXECUTOR_TIER_JEV_STUB": stub})
        try:
            hso = json.loads(p.stdout.strip().splitlines()[-1]).get("hookSpecificOutput", {})
        except (ValueError, IndexError):
            hso = {}
        return p.returncode, hso

    for stub, why in (("none", "Jev unavailable"),
                      ("sonnet:0.30", "Jev below the acting threshold")):
        rc, hso = tier_gate(stub)
        check(f"executor-tier still refuses an Agent spawn naming no model ({why})",
              rc == 0 and hso.get("permissionDecision") == "deny"
              and "updatedInput" not in hso
              and "model" in (hso.get("permissionDecisionReason") or "").lower(),
              f"rc={rc} {json.dumps(hso)[:160]}")
    rc, hso = tier_gate("haiku:0.90")
    updated = hso.get("updatedInput") or {}
    check("executor-tier names the tier itself when Jev is confident (allow, model filled in)",
          rc == 0 and hso.get("permissionDecision") == "allow"
          and updated.get("model") == "haiku"
          and all(updated.get(k) == v for k, v in spawn.items()),
          f"rc={rc} {json.dumps(hso)[:160]}")

    # 3a. conduct: "a shell command handed to Joe instead of run".
    verdict, _ = fire("conduct-stop-gate.py", {
        "session_id": "selftest", "stop_hook_active": False,
        "transcript_path": transcript(tmp, "handoff.jsonl",
            "All set. Run this when you get a chance:\n\n```bash\n./run.sh health\n```",
            human="fix the health check")})
    check("conduct still reopens on a command handed over instead of run",
          verdict == "REOPEN", verdict)

    # 3b. conduct: "a turn parked on him with no question".
    verdict, _ = fire("conduct-stop-gate.py", {
        "session_id": "selftest", "stop_hook_active": False,
        "transcript_path": transcript(tmp, "parked.jsonl",
            "I have mapped it out. I'll hold here until you weigh in.",
            human="restructure the folders")})
    check("conduct still reopens on a turn parked with no question",
          verdict == "REOPEN", verdict)

    # 3c. conduct: "a bare id with no plain-language gloss".
    verdict, _ = fire("conduct-stop-gate.py", {
        "session_id": "selftest", "stop_hook_active": False,
        "transcript_path": transcript(tmp, "bareid.jsonl",
            "Fixed it. This is required by rule aa411351 anyway.", human="status?")})
    check("conduct still reopens on a bare id with no gloss", verdict == "REOPEN", verdict)

    # 4. drift-assertion: "held a 'this state is wrong' claim until the decision
    # log was read". The latch identity changed under it today, so this is the
    # one catch of the five that had to be re-proven rather than assumed.
    decisions = os.path.join(tmp, "decision-history.md")
    with open(decisions, "w") as fh:
        fh.write("# Decision history\n\n- `2026-08-13` — The quokka-indexer lane was "
                 "DELIBERATELY disabled by Joe after the overnight run cost more than "
                 "it returned; leaving it off is the chosen state.\n")
    claim = ("The quokka-indexer lane is no longer running. It was supposed to fire "
             "nightly and the schedule has silently reverted, so the index is stale "
             "and nothing has been re-pointed.")
    state = os.path.join(tmp, "drift-state")
    verdict, text = fire("drift-assertion-gate.py", {
        "session_id": "replay-drift", "stop_hook_active": False,
        "transcript_path": transcript(tmp, "drift.jsonl", claim)},
        env={"CARR_NONCANONICAL_DECISIONS_PATH": decisions,
             "CARR_DRIFT_ASSERTION_STATE": state})
    check("drift-assertion still reopens on a governed drift claim",
          verdict == "REOPEN" and "quokka-indexer" in text, f"{verdict}: {text[:120]}")

    # 5. completion-evidence: "caught a delivery claim naming no recipient".
    # cwd IS REQUIRED HERE. Without it the gate falls back to sniffing the
    # transcript for CARR path markers, and a scratch fixture has none — the
    # gate then correctly reads the turn as somebody else's project and stays
    # silent. Left out, this case would go green as "no fire" for the wrong
    # reason, which is the shape of false pass this whole file exists to avoid.
    # The LATCH DIR is per-run too (b2a17494's own lesson): a fixed "selftest"
    # id writes out/stop-latch/selftest.json in the REAL out/, so a second run
    # of this suite read its own earlier fire as latched and went silent.
    verdict, text = fire("completion-evidence-gate.py", {
        "session_id": "selftest", "stop_hook_active": False, "cwd": REPO,
        "transcript_path": transcript(tmp, "delivery.jsonl",
            "Done — the packet has been delivered and the summary sent.",
            human="get the handoff out",
            tools=[("Write", {"file_path": os.path.join(REPO, "a.py"), "content": "x"}),
                   ("Write", {"file_path": os.path.join(REPO, "b.py"), "content": "x"})])},
        env={"CARR_STOP_LATCH_STATE": os.path.join(tmp, "completion-latch")})
    check("completion-evidence still reopens on a delivery claim with no recipient",
          verdict == "REOPEN" and "recipient" in text.lower(), f"{verdict}: {text[:160]}")


# ── the nine noise fires ────────────────────────────────────────────────────

REPORTING_PROSE = [
    "You'll see the count in the log now. The exporter checks the manifest and "
    "writes the render, so the nightly run no longer needs the flag.",
    "The job will read the queue and update each row in one pass. Your earlier "
    "concern about the ordering is handled by the index now.",
    "I will review the diff and then run the suite. Once that is green I will "
    "open the pull request and add the label.",
    "Committed hooks/one-repo-gate.py and ops/one-repo-gate-selftest.py, pushed, "
    "and the pull request is open with automerge armed.",
    "Changed: exporters/targets.py, hooks/md_manifest.py, ops/config/gate-baseline.json. "
    "47 cases green, and you can read the timings in the class summary.",
    "The council transcripts are written. Both chairs returned, the manifest names "
    "what each one was asked to run, and you have them under out/council.",
    "Ran the health check. All rows green except rules-live, which is stale by one "
    "until the hourly job fires, so nothing needs doing there.",
    "Dropped the redundant index. It duplicated the primary key and cost writes with "
    "no reader, and it is reversible if the plan regresses.",
]


def replay_noise(tmp):
    print("\n  ── the eight lint fires on reporting prose must cost zero reopens")
    latch = os.path.join(tmp, "lint-latch")
    carried, reopened = 0, 0
    for i, text in enumerate(REPORTING_PROSE):
        session = f"replay-lint-{i}"          # each its own session: the worst case
        carry = os.path.join(REPO, "out", "chat-lint-carry", f"{session}.txt")
        try:
            os.unlink(carry)
        except OSError:
            pass
        verdict, _ = fire("chat-lint-gate.py", {
            "hook_event_name": "Stop", "session_id": session, "stop_hook_active": False,
            "transcript_path": transcript(tmp, f"lint{i}.jsonl", text)},
            env={"CARR_STOP_LATCH_STATE": latch})
        if verdict == "REOPEN":
            reopened += 1
        if os.path.exists(carry):
            carried += 1
            os.unlink(carry)
    check("chat-lint reopens on reporting prose: 0", reopened == 0, f"{reopened} reopens")
    check("...and it no longer flags the misread shapes at all", carried == 0,
          f"{carried} of {len(REPORTING_PROSE)} still flagged")

    # ONE SESSION, EIGHT MESSAGES — the actual shape of the day being replayed.
    # Even where a finding is TRUE, the session hears it once.
    banned = ("This will seamlessly unlock a transformative workflow and "
              "effortlessly streamline the whole pipeline.")
    session = "replay-lint-oneday"
    delivered = 0
    for i in range(8):
        carry = os.path.join(REPO, "out", "chat-lint-carry", f"{session}.txt")
        try:
            os.unlink(carry)
        except OSError:
            pass
        fire("chat-lint-gate.py", {
            "hook_event_name": "Stop", "session_id": session, "stop_hook_active": False,
            "transcript_path": transcript(tmp, f"same{i}.jsonl", banned)},
            env={"CARR_STOP_LATCH_STATE": latch})
        if os.path.exists(carry):
            delivered += 1
            os.unlink(carry)
    check("a true wording finding is delivered once per session, not eight times",
          delivered == 1, f"delivered {delivered} times")


def replay_demotions(tmp):
    print("\n  ── context guidance and four demoted gates announce")

    # loose-work: this session edited a tracked file and left it.
    repo = os.path.join(tmp, "loose")
    os.makedirs(repo)
    env0 = fixture_env()   # the scrubber, not a hand-rolled GIT_ filter
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@e.invalid"],
                 ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", repo, *args], capture_output=True, env=env0)
    target = os.path.join(repo, "tracked.txt")
    with open(target, "w") as fh:
        fh.write("seed\n")
    subprocess.run(["git", "-C", repo, "add", "-A"], capture_output=True, env=env0)
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "seed"], capture_output=True,
                   env=env0)
    with open(target, "w") as fh:
        fh.write("left behind\n")
    verdict, text = fire("loose-work-gate.py", {
        "session_id": "replay", "cwd": repo,
        "transcript_path": transcript(tmp, "loose.jsonl", "Done.",
                                      tools=[("Write", {"file_path": target, "content": "x"})])},
        env={"CARR_LOOSE_WORK_REPO": repo})
    check("loose-work announces the file and does not reopen",
          verdict == "ANNOUNCE" and "tracked.txt" in text, f"{verdict}: {text[:100]}")

    # context-handoff: the 70% band.
    usage = os.path.join(tmp, "ctx.jsonl")
    with open(usage, "w") as fh:
        fh.write(json.dumps({"type": "assistant", "message": {"usage": {
            "input_tokens": 100, "cache_read_input_tokens": 699_900}}}) + "\n")
    verdict, text = fire("context-handoff-gate.py",
                         {"hook_event_name": "Stop",
                          "session_id": "replay-ctx",
                          "prompt_id": "prompt-replay-ctx",
                          "transcript_path": usage},
                         env={"CARR_CONTEXT_WINDOW": "1000000",
                              "CARR_CONTEXT_STATE": os.path.join(tmp, "ctx-state.json"),
                              "CARR_CONTEXT_HOOK_EVENT": "Stop",
                              "CARR_CONTEXT_AUDIT": "off"})
    check("context-handoff announces at the hard line without blocking Stop",
          verdict == "ANNOUNCE" and "context headroom notice" in text,
          f"{verdict}: {text[:160]}")

    # unread-artifact: a behavioural claim about a file only ever grepped.
    verdict, text = fire("unread-artifact-gate.py", {
        "hook_event_name": "Stop", "session_id": "replay-unread", "stop_hook_active": False,
        "transcript_path": transcript(tmp, "unread.jsonl",
            "The digest is produced upstream: `tools/health-check.py` writes the radar "
            "digest every Monday, so re-pointing it is the cheapest job to move.",
            tools=[("Bash", {"command": "grep -rn radar-digest tools/ | head"})])},
        env={"CARR_STOP_LATCH_STATE": os.path.join(tmp, "unread-latch"),
             "CARR_UNREAD_ARTIFACT_STATE": os.path.join(tmp, "unread-latch")})
    check("unread-artifact announces the unread file and does not reopen",
          verdict == "ANNOUNCE" and "health-check.py" in text, f"{verdict}: {text[:100]}")

    # map-architecture: governed map work that never called the verb.
    verdict, text = fire("map-architecture-gate.py", {
        "session_id": "replay-map", "stop_hook_active": False, "cwd": REPO,
        "transcript_path": transcript(tmp, "map.jsonl", "Here is the plan.",
                                      human="Build an interactive tour map.")})
    check("map-architecture announces the missing verb call and does not reopen",
          verdict == "ANNOUNCE" and "map-architecture" in text, f"{verdict}: {text[:100]}")



def replay_latch(tmp):
    print("\n  ── the duplicate fire, and the card")
    # THE DUPLICATE, AGAINST THE LIVE GATE. The ledger's own case: a turn
    # mutates and claims completion (fires), and the next message restates the
    # same claims in different words (must not fire).
    latch_state = os.path.join(tmp, "completion-latch")
    work = [{"type": "user", "message": {"role": "user", "content": "reconcile the deal"}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "mcp__carr__update-deal", "input": {}}]}}]

    def completion_stop(final, session):
        path = os.path.join(tmp, f"completion-{abs(hash(final + session)) % 10 ** 8}.jsonl")
        with open(path, "w") as fh:
            for rec in work:
                fh.write(json.dumps(rec) + "\n")
            fh.write(json.dumps({"type": "assistant",
                                 "message": {"role": "assistant", "content": final}}) + "\n")
        verdict, _ = fire("completion-evidence-gate.py",
                          {"session_id": session, "stop_hook_active": False,
                           "cwd": REPO, "transcript_path": path},
                          env={"CARR_STOP_LATCH_STATE": latch_state})
        return verdict

    check("the completion gate fires once on an unverified claim",
          completion_stop("Done.", "replay-completion") == "REOPEN")
    check("...and the duplicate fire on the same claims is gone",
          completion_stop("That is complete now — the reconciliation is finished.",
                          "replay-completion") == "SILENT")
    check("...while another session still hears the first fire",
          completion_stop("Done.", "replay-completion-other") == "REOPEN")

    # Nothing new can reopen without a card.
    cards = os.path.join(tmp, "gate-admission.json")
    with open(cards, "w") as fh:
        json.dump({"cards": {}}, fh)
    body = "\n".join(['"""new"""', "import json, sys", "", "",
                      "def main():", "    payload = json.load(sys.stdin)",
                      '    print(json.dumps({"decision": "block", "reason": "no"}))',
                      "    return 0", "", "",
                      "if __name__ == '__main__':", "    sys.exit(main())"])
    verdict, text = fire("gate-edit-gate.py", {
        "tool_name": "Write", "session_id": "selftest",
        "tool_input": {"file_path": os.path.join(REPO, "hooks", "zzz-replay-new-gate.py"),
                       "content": body}},
        env={"CARR_GATE_ADMISSION_CARDS": cards})
    check("a new blocking gate with no admission card is refused",
          verdict == "REOPEN" and "GATE ADMISSION" in text, f"{verdict}: {text[:100]}")

    body_announce = body.replace(
        'print(json.dumps({"decision": "block", "reason": "no"}))',
        'print(json.dumps({"hookSpecificOutput": {"hookEventName": "Stop", '
        '"additionalContext": "noticed"}}))')
    verdict, _ = fire("gate-edit-gate.py", {
        "tool_name": "Write", "session_id": "selftest",
        "tool_input": {"file_path": os.path.join(REPO, "hooks", "zzz-replay-new-gate.py"),
                       "content": body_announce}},
        env={"CARR_GATE_ADMISSION_CARDS": cards})
    check("...and the same matcher, written to announce, ships with no card",
          verdict == "ANNOUNCE", verdict)


def main():
    print("stop-gate rationing — replaying the 2026-08-23 session shape")
    with tempfile.TemporaryDirectory(prefix="rationing-replay-") as tmp:
        replay_catches(tmp)
        replay_noise(tmp)
        replay_demotions(tmp)
        replay_latch(tmp)

    print()
    for n in notes:
        print(f"  NOT COVERED HERE: {n}")
    print("  NOT COVERED HERE: the 2 harness dead ends (the permission classifier "
          "denying report-problem) are not a repo gate and stay at 2 until the "
          "council's classifier allowlist lands")
    print()
    if failures:
        print(f"rationing replay FAILED {len(failures)}/{passed + len(failures)}")
        for f in failures:
            print(f"  · {f}")
        return 1
    print(f"rationing replay ok ({passed} checks) — "
          "5 catches kept, 0 reopens charged for reporting prose, "
          "0 context reopens, 0 from four still-demoted gates, "
          "no new blocker without a card")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
