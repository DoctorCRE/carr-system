#!/usr/bin/env python3
"""unfiled-bug-stop-gate-selftest.py — fixtures for hooks/unfiled-bug-stop-gate.py.

Spawns the REAL hook on a REAL transcript file and reads its stdout, the way
ops/conduct-gate-selftest.py drives the conduct gate: what settings.json runs
is what is tested, not a function inside it.

BOTH HALVES MATTER. MUST HOLD is the turn Joe objected to on 2026-10-12: a
defect the session saw, reported in prose, with nothing filed. MUST ALLOW is
ordinary work: a defect filed through any of the three capture verbs in any of
their call forms, a relay of somebody else's finding, a sentence that names
its own record, a hypothetical, the normal red-CI loop on a pull request. A
gate that holds legitimate turns is switched off within a week, which is the
same outcome as never building it.

The orchestrator cases quote the real 2026-10-12 sentences, so the headline
miss cannot quietly come back.

    .venv/bin/python ops/unfiled-bug-stop-gate-selftest.py
"""
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.environ.get("UNFILED_BUG_GATE_HOOK") or os.path.join(REPO, "hooks", "unfiled-bug-stop-gate.py")
sys.path.append(os.path.join(REPO, "lib"))
from selftest_harness import Checker, hook_env  # noqa: E402

CHECK = Checker()
TMP = tempfile.mkdtemp(prefix="unfiled-bug-gate-")
CARR_CWD = "/Users/booko/carr-system"
_ids = iter(range(1, 10_000))


# ------------------------------------------------------------- transcript DSL

def prompt(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def say(text, stop="end_turn"):
    return {"type": "assistant", "message": {
        "id": f"msg_{next(_ids)}", "stop_reason": stop,
        "content": [{"type": "text", "text": text}]}}


def call(name, args, result, is_error=False):
    """A tool call and its result: two records, as the harness writes them."""
    tid = f"toolu_{next(_ids)}"
    return [
        {"type": "assistant", "message": {
            "id": f"msg_{next(_ids)}", "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "id": tid, "name": name, "input": args}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": result, "is_error": is_error}]}},
    ]


def flat(*parts):
    out = []
    for part in parts:
        out.extend(part if isinstance(part, list) else [part])
    return out


def fire(records, *, stop_hook_active=False, cwd=CARR_CWD, session="selftest",
         raw_lines=(), env=None):
    path = os.path.join(TMP, f"t{next(_ids)}.jsonl")
    with open(path, "w") as handle:
        for line in raw_lines:
            handle.write(line + "\n")
        for rec in records:
            handle.write(json.dumps(rec) + "\n")
    payload = {"transcript_path": path, "stop_hook_active": stop_hook_active,
               "session_id": session, "cwd": cwd, "hook_event_name": "Stop"}
    proc = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30,
                          env=hook_env(TMP, **(env or {})))
    out = (proc.stdout or "").strip()
    reason = ""
    held = False
    for line in out.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("decision") == "block":
            held, reason = True, str(value.get("reason") or "")
    return held, reason, proc.returncode


def holds(name, records, **kw):
    held, reason, code = fire(records, **kw)
    CHECK.check(f"holds: {name}", held and code == 0, f"held={held} code={code}")
    return reason


def allows(name, records, **kw):
    held, reason, code = fire(records, **kw)
    CHECK.check(f"allows: {name}", not held and code == 0, f"held={held} code={code} {reason[:160]!r}")


# ---------------------------------------------------------------- the cases

BRITTLE = ("Pull request 1714 had a red check from a file-order change in the nightly "
           "preflight list. Fixed and pushed, mergeable again.")
MAGNET = "Pull request 1708 (the needle movers) had gone conflicting again after merges to main."
REARM = ("Recorded a gate defect: a seat's compaction wipes the parent session's completed "
         "rule boot and forces six re-fetches.")
OK_DEFECT = '{"ok": true, "defect_id": "1faab8d5-51c6-44ec-9ad6-204f87a51b56"}'
OK_WR = '{"ok": true, "human_ref": "WR-000266", "state": "captured"}'
OK_LOOP = '{"ok": true, "loop_id": "2da83eed", "number": "1294"}'
REFUSED = ('TOOL ERROR {"error": "value_not_in_declared_vocabulary", "verb": "record-defect", '
           '"field": "detected_by"}')
DEFECT_ARGS = {"idempotency_key": "6c1e0d1a-6a7f-4a39-9f0e-1c2b3d4e5f60", "defect_class": "brittle_test_pins_literal",
               "claimed": "the grok-session selftest proves the nightly preflight order",
               "actual": "it pins the literal preflight list, so any reorder turns it red",
               "detected_by": "self"}


def main():
    # 1. The headline: a defect seen and reported, nothing filed.
    reason = holds("observed brittle test, no receipt", [
        prompt("status?"),
        say("The grok-session selftest pins the literal preflight list, so every reorder "
            "of bin/nightly.sh turns it red.")])
    CHECK.check("hold message quotes the matched sentence",
                "pins the literal preflight list" in reason, reason[:200])
    CHECK.check("hold message names both receipts",
                "record-defect" in reason and "WR-000123" in reason and "report-problem" in reason,
                reason[:300])
    CHECK.check("hold message cites the rule it carries", "19ebbeb3" in reason, reason[:200])

    # 2. Joe's turn, verbatim shape: one filing, three defects, two unfiled.
    reason = holds("two unfiled defects beside one filing (2026-10-12 status report)", flat(
        prompt("status"),
        call("Bash", {"command": "./run.sh call record-defect \"$(python3 defect.py)\""}, OK_DEFECT),
        say(f"Delta since the last message:\n\n- {BRITTLE}\n- {MAGNET}\n- {REARM}")))
    CHECK.check("names the brittle check", "red check from a file-order change" in reason, reason[:400])
    CHECK.check("names the conflict magnet", "gone conflicting again" in reason, reason[:400])
    CHECK.check("does not name the filed re-arm", "rule boot" not in reason, reason[:400])

    # 3. A refused filing recorded nothing.
    holds("filing the verb refused is not a receipt", flat(
        prompt("go"),
        call("Bash", {"command": "./run.sh call record-defect \"$(python3 d.py)\" | tail -c 400"}, REFUSED),
        say("The rerun script keeps refusing with an error that names no field.")))

    # 4. A schema probe is not a filing.
    holds("empty-argument probe is not a receipt", flat(
        prompt("go"),
        call("Bash", {"command": "./run.sh call record-defect '{}' 2>&1 | tail -c 500"},
             'TOOL ERROR {"error": "missing_required"}'),
        say("The credential-health reader files a duplicate loop every day.")))

    # 5. A receipt from an EARLIER turn does not cover this turn's defect.
    holds("receipt in a previous turn does not count", flat(
        prompt("first"),
        call("mcp__carr__record-defect", DEFECT_ARGS, OK_DEFECT),
        say("Filed it."),
        prompt("next"),
        say("The SubagentHandback hook cannot deliver a seat's report outside auto mode.")))

    # 6. More classes from the rule's own list.
    holds("recurring merge conflict", [
        prompt("go"), say("The usage line of run.sh conflicted again on the third PR today.")])
    holds("gate fired wrongly", [
        prompt("go"), say("The question gate misread a rule-approval question as an internal decision.")])
    holds("verb threw an exception", [
        prompt("go"), say("Approving the two new rules threw a server-side exception.")])

    # 7. One unreadable line does not switch the gate off.
    holds("a garbage transcript line is skipped, not fatal",
          [prompt("go"), say("The calendar job reads a lead list, so it crashed on every run.")],
          raw_lines=["{not json", "[1, 2]"])

    # 8. A hold writes its catch row (not for the selftest session).
    log = os.path.join(TMP, "catch.jsonl")
    holds("a real session's hold is logged",
          [prompt("go"), say("The merge queue's progress-board write keeps failing.")],
          session="fixture-session", env={"CARR_UNFILED_BUG_LOG": log})
    try:
        with open(log) as handle:
            row = json.loads(handle.readline())
    except (OSError, ValueError):
        row = {}
    CHECK.check("catch row names hook, class and sentence",
                row.get("hook") == "unfiled-bug-stop-gate" and row.get("classes") == ["failing"]
                and "progress-board" in " ".join(row.get("sentences") or []), row)

    # ---- MUST ALLOW: each receipt form
    allows("record-defect over MCP (any server prefix)", flat(
        prompt("go"),
        call("mcp__b36e17b6-7e3b-4e65-b890-21f21d538440__record-defect", DEFECT_ARGS, OK_DEFECT),
        say("The grok-session selftest pins the literal preflight list, so every reorder turns it red.")))
    allows("report-problem over the run.sh Bash door", flat(
        prompt("go"),
        call("Bash", {"command": "cd /Users/booko/carr-system && ./run.sh call report-problem "
                                 "\"$(python3 -c 'print(1)')\""}, OK_WR),
        say("The SubagentHandback hook cannot deliver a seat's report outside auto mode.")))
    allows("add-loop over the claude.ai connector", flat(
        prompt("go"),
        call("mcp__claude_ai_CARR_Record_Layer__add-loop",
             {"idempotency_key": "x", "title": "credential-health duplicate loop"}, OK_LOOP),
        say("The credential-health reader files a duplicate loop every day.")))
    allows("record-defect through the call-verb passthrough", flat(
        prompt("go"),
        call("mcp__carr__call-verb", {"verb": "record-defect", "args": DEFECT_ARGS}, OK_DEFECT),
        say("The question gate misread a rule-approval question as an internal decision.")))
    allows("a seat dispatched with record-defect in its brief", flat(
        prompt("go"),
        call("Agent", {"description": "file it", "prompt": "Run record-defect for the re-arm, then fix it."},
             "Async agent launched successfully."),
        say("The rule-boot gate misread a seat's compaction as the parent's and re-armed it.")))

    # ---- MUST ALLOW: the sentence or its paragraph names the record
    allows("sentence names its Work Request", [
        prompt("go"), say("The run.sh usage line keeps conflicting again; tracked as WR-000266.")])
    allows("paragraph names the record a sentence later", [
        prompt("go"), say("Local test databases kept failing: killed runs leaked 30 of 32 shared-memory "
                          "slots. The permanent fix is tracked as Work Request WR-000230.")])
    allows("sentence says it is recorded as a defect", [
        prompt("go"), say("The grok-session test pins the literal list; recorded as defect ee21be2b.")])

    # ---- MUST ALLOW: not an observation by this session
    allows("relay of a seat's fix", [
        prompt("go"), say("PR 1715 also fixes a bug in the schema-snapshot tool that was mangling a "
                          "carriage return. Relayed to the orchestrator in model-room.")])
    allows("hypothetical", [
        prompt("go"), say("If the preflight list is reordered, the selftest would fail on every run.")])
    allows("negated", [
        prompt("go"), say("Hosted CI is green: nothing failed with an error and no regressions.")])
    allows("red CI on the PR under work is the build loop", [
        prompt("go"), say("PR 1708's gates job failed on the new head, so I am reading its log.")])
    allows("a fix already in hand", [
        prompt("go"), say("The flaky gh-spacing test is fixed in PR 1678 and merging now.")])
    allows("environment fault, not a defect", [
        prompt("go"), say("SSH to the MacBook over Tailscale failed again, so the probe waits.")])
    allows("defect words inside a code fence", [
        prompt("go"), say("Here is the log:\n\n```\nERROR: job crashed with a traceback\n```\n\nAll green now.")])
    allows("quoted gate text", [
        prompt("go"), say('The gate printed "the selftest was failing on every run" and I read it.')])
    allows("plain status report", [
        prompt("go"), say("PR 1717 is open and mergeable at head b93ab88d; hosted checks are pending.")])

    # ---- MUST ALLOW: structural exits
    allows("stop_hook_active lets the turn end",
           [prompt("go"), say("The merge queue's progress-board write keeps failing.")],
           stop_hook_active=True)
    allows("not a CARR session",
           [prompt("go"), say("The merge queue's progress-board write keeps failing.")],
           cwd="/Users/booko/other-project")
    allows("empty transcript", [])
    held, _reason, code = fire([], raw_lines=["{not json"])
    CHECK.check("allows: unreadable transcript fails open", not held and code == 0, f"held={held} code={code}")
    proc = subprocess.run([sys.executable, HOOK], input="not json", capture_output=True,
                          text=True, timeout=30, env=hook_env(TMP))
    CHECK.check("allows: malformed hook input fails open",
                proc.returncode == 0 and "block" not in proc.stdout, (proc.returncode, proc.stdout[:120]))

    return CHECK.summary()


if __name__ == "__main__":
    sys.exit(main())
