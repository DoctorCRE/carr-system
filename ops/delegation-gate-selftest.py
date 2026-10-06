#!/usr/bin/env python3
"""Executable regressions for hooks/delegation-gate.py.

REWRITTEN 2026-08-27 (WR-000019 slice S4) for the passive-observer redesign.
The gate used to deny a PreToolUse call outright once a session's broad-search
streak crossed a threshold (2 broad calls under an active delegation latch, 3
without one and no executor declared). It denied 35,322 times in 18 days and
that denial path is gone: hooks/delegation-gate.py never returns exit 2 and
never emits {"decision": "block"} anymore, from anywhere.

WHAT THIS FILE NOW PROVES, in three parts:

  1. NEVER BLOCKS. Every fixture shape that used to trip the tripwire (sticky
     latch, no-latch sweep, Codex's own spelling of both) still reaches exit 0
     with no block decision in its output. This is the regression guard
     against silently reintroducing denial.

  2. TELEMETRY IS RECORDED CORRECTLY. The same classification logic that used
     to decide "deny or allow" now decides "bump which counter in this
     session's bucket inside out/delegation-gate-state.json's new "telemetry"
     key" -- mechanical_calls (every mechanical call), broad_calls (every
     sweep-shaped call), broad_calls_while_latched, would_have_flagged (a call
     that reached the old deny threshold), and flag_classes (sticky_latch vs
     second_mechanical_call). Counters accumulate ACROSS repeated hook
     invocations for the same session, the way a real session's tool calls
     arrive one at a time over the life of the session -- this is the one
     structural difference from the old per-call-window classification, and
     the accumulation fixtures below call the hook multiple times in sequence
     to prove it.

  3. THE STOP SUMMARY IS RIGHT. At hook_event_name=="Stop", the gate reads
     back this session's bucket, appends EXACTLY ONE row to
     out/delegation-gate-ledger.jsonl (never one row per call), clears the
     bucket, and speaks an ANNOUNCE (hooks/stop_latch.announce -- additional
     context, never a block) only when would_have_flagged reached
     DELEGATION_GATE_MATERIAL_THRESHOLD. A session that never called a
     mechanical tool gets no ledger row at all; a session under the material
     threshold gets a ledger row but no announcement.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "delegation-gate.py")


def hook_env(extra=None, child_session=False):
    """Base env for a hook subprocess, with CLAUDE_CODE_CHILD_SESSION pinned.

    This selftest runner is itself frequently a leaf worker (spawned via the
    Agent tool), so os.environ here can carry CLAUDE_CODE_CHILD_SESSION=1 of
    its OWN. Every case below simulates a specific seat -- main or subagent --
    and must not silently inherit whichever seat happens to be running the
    test.
    """
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_CHILD_SESSION", None)
    if child_session:
        env["CLAUDE_CODE_CHILD_SESSION"] = "1"
    if extra:
        env.update(extra)
    return env


def load_hook():
    spec = importlib.util.spec_from_file_location("delegation_gate", HOOK)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


GATE = load_hook()


def user(text):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def assistant_text(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def tool(name):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "name": name, "input": {}}]}}


def tool_with_input(name, tool_input):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": name, "input": tool_input}
    ]}}


def broad():
    return tool_with_input("Grep", {"pattern": "x"})


def status_call():
    return tool_with_input("Bash", {"command": "git status"})


BROAD_TOOL = "Grep"
BROAD_INPUT = {"pattern": "y"}


def codex_user(text, include_generated_context=False):
    content = [{"type": "input_text", "text": text}]
    if include_generated_context:
        content.append({
            "type": "input_text",
            "text": "<environment_context> delegation latch documentation </environment_context>",
        })
    return {"type": "response_item", "payload": {
        "type": "message", "role": "user", "content": content,
    }}


def codex_history_wrapper(quoted):
    return {"type": "response_item", "payload": {
        "type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "The following is the Codex agent history added since your last approval"},
            {"type": "input_text", "text": quoted},
        ],
    }}


def codex_tool(name, broad_call=True):
    payload = {"type": "custom_tool_call", "name": name}
    if broad_call:
        payload["input"] = {"cmd": "grep -rn TODO ."}
    return {"type": "response_item", "payload": payload}


def write_transcript(recs):
    fd, path = tempfile.mkstemp(prefix="delegation-gate-selftest-", suffix=".jsonl")
    with os.fdopen(fd, "w") as fh:
        for rec in recs:
            fh.write(json.dumps(rec) + "\n")
    return path


def run_pretooluse(recs, session, state, cwd=REPO, subagent=False,
                    child_session=False, tool_name=BROAD_TOOL, tool_input=None,
                    hook_event_name=None, extra_payload=None):
    with tempfile.TemporaryDirectory(prefix="delegation-gate-") as td:
        base = os.path.join(td, "subagents") if subagent else td
        os.makedirs(base, exist_ok=True)
        transcript = os.path.join(base, "session.jsonl")
        with open(transcript, "w") as fh:
            for rec in recs:
                fh.write(json.dumps(rec) + "\n")
        payload = {
            "session_id": session,
            "cwd": cwd,
            "transcript_path": transcript,
            "tool_name": tool_name,
            "tool_input": tool_input if tool_input is not None else dict(BROAD_INPUT),
        }
        if hook_event_name:
            payload["hook_event_name"] = hook_event_name
        if extra_payload:
            payload.update(extra_payload)
        env = hook_env({"DELEGATION_GATE_STATE": state}, child_session=child_session)
        return subprocess.run(
            [sys.executable, HOOK], input=json.dumps(payload), text=True,
            capture_output=True, env=env,
        )


def run_stop(session, state, ledger, threshold=None):
    payload = {"hook_event_name": "Stop", "session_id": session, "cwd": REPO}
    env_extra = {"DELEGATION_GATE_STATE": state, "DELEGATION_GATE_LEDGER": ledger}
    if threshold is not None:
        env_extra["DELEGATION_GATE_MATERIAL_THRESHOLD"] = str(threshold)
    env = hook_env(env_extra)
    return subprocess.run(
        [sys.executable, HOOK], input=json.dumps(payload), text=True,
        capture_output=True, env=env,
    )


def parse_stdout_json(proc):
    try:
        return json.loads(proc.stdout) if proc.stdout else None
    except json.JSONDecodeError:
        return None


def never_blocks(proc) -> bool:
    if proc.returncode != 0:
        return False
    response = parse_stdout_json(proc)
    if isinstance(response, dict) and response.get("decision") == "block":
        return False
    return True


def read_state(state):
    try:
        with open(state) as fh:
            return json.load(fh)
    except Exception:
        return {}


def bucket_for(state, session):
    return read_state(state).get("telemetry", {}).get(session)


def read_ledger_rows(ledger, session=None):
    rows = []
    try:
        with open(ledger) as fh:
            for line in fh:
                row = json.loads(line)
                if session is None or row.get("session") == session:
                    rows.append(row)
    except FileNotFoundError:
        pass
    return rows


def main():
    oks = []

    def check(name, ok, detail=""):
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f": {detail}" if not ok and detail else ""))
        oks.append(ok)

    with tempfile.TemporaryDirectory(prefix="delegation-gate-state-") as state_dir:
        state = os.path.join(state_dir, "state.json")
        ledger = os.path.join(state_dir, "ledger.jsonl")

        # --- 1. NEVER BLOCKS -------------------------------------------------
        never_block_cases = [
            ("third broad call without executor",
             [user("audit this"), broad(), broad()], "ordinary-third"),
            ("explicit delegation, second broad call under active latch",
             [user("use the cheapest model that can do this correctly"), broad()], "sticky-basic"),
            ("delegation survives a later phase message",
             [user("delegate this to the cheapest qualified model"), tool("Agent"),
              user("I'm in Salesforce now"), broad()], "sticky-phase"),
            ("an unlisted executor label does not exempt a real sweep",
             [user("audit this"), broad(), broad(),
              assistant_text("executor: the vibes — because reasons")], "bogus-label"),
            ("full transcript keeps delegation beyond 500 records",
             [user("delegate this to the cheapest qualified model")] + [assistant_text("filler") for _ in range(501)]
             + [user("new data source is ready"), broad()], "long-transcript"),
        ]
        for name, recs, session in never_block_cases:
            proc = run_pretooluse(recs, session, state)
            check(f"never blocks: {name}", never_blocks(proc),
                  f"rc={proc.returncode} stdout={proc.stdout!r}")

        # Codex spelling, both the explicit-delegation and no-latch tripwire
        # shapes, must also never block.
        codex_never_block = [
            ("Codex explicit delegation",
             [codex_user("delegate this to the cheapest qualified model", True), codex_tool("exec")]),
            ("Codex no-latch tripwire shape",
             [codex_user("audit this", True), codex_tool("exec"), codex_tool("exec")]),
            ("Codex approval wrapper quoting an old delegation",
             [codex_user("audit this"), codex_history_wrapper("delegate this to a cheaper model"),
              codex_tool("exec"), codex_tool("exec")]),
        ]
        for i, (name, recs) in enumerate(codex_never_block):
            transcript = write_transcript(recs)
            payload = {
                "hook_event_name": "PreToolUse", "cwd": REPO, "session_id": f"codex-nb-{i}",
                "tool_name": "functions.exec", "tool_input": {"cmd": "grep -rn TODO ."},
                "transcript_path": transcript,
            }
            proc = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), text=True,
                                  capture_output=True,
                                  env=hook_env({"DELEGATION_GATE_STATE": state}))
            os.unlink(transcript)
            check(f"never blocks: {name}", never_blocks(proc), f"stdout={proc.stdout!r}")

        # Exempt shapes must ALSO never block AND must never even create a
        # telemetry bucket -- the exemption is total, matching the enforcing
        # version's reasoning (a leaf worker is not tracked; it IS the delegate).
        proc = run_pretooluse([user("do the assigned sweep"), broad(), broad()],
                               "subagent-exempt-nb", state, subagent=True)
        check("subagent transcript: never blocks", never_blocks(proc))
        check("subagent transcript: no telemetry bucket created",
              bucket_for(state, "subagent-exempt-nb") is None)

        proc = run_pretooluse([user("audit this"), broad(), broad()],
                               "non-carr-nb", state, cwd="/private/tmp")
        check("non-CARR session: never blocks", never_blocks(proc))
        check("non-CARR session: no telemetry bucket created",
              bucket_for(state, "non-carr-nb") is None)

        proc = run_pretooluse([user("delegate this to the cheapest qualified model"), broad(), broad()],
                               "child-env-nb", state, child_session=True)
        check("subagent env: never blocks", never_blocks(proc))
        check("subagent env: no telemetry bucket created",
              bucket_for(state, "child-env-nb") is None)

        # --- 2. TELEMETRY CLASSIFICATION --------------------------------------

        # 2a. Ordinary accumulation across three separate invocations of the
        # SAME session, mirroring three calls arriving over a session's life.
        acc_session = "accumulate-ordinary"
        proc = run_pretooluse([user("audit this")], acc_session, state)
        b = bucket_for(state, acc_session)
        check("accumulation call 1: bucket created, 1 mechanical/broad call, not flagged",
              bool(b) and b["mechanical_calls"] == 1 and b["broad_calls"] == 1
              and b["would_have_flagged"] == 0, b)

        proc = run_pretooluse([user("audit this"), broad()], acc_session, state)
        b = bucket_for(state, acc_session)
        check("accumulation call 2: counters accumulate, still below threshold",
              b["mechanical_calls"] == 2 and b["broad_calls"] == 2
              and b["would_have_flagged"] == 0, b)

        proc = run_pretooluse([user("audit this"), broad(), broad()], acc_session, state)
        b = bucket_for(state, acc_session)
        check("accumulation call 3: third broad call flags second_mechanical_call",
              b["mechanical_calls"] == 3 and b["broad_calls"] == 3
              and b["would_have_flagged"] == 1
              and b["flag_classes"].get("second_mechanical_call") == 1, b)
        check("accumulation call 3: never blocked even though flagged", never_blocks(proc))

        # 2b. Sticky-latch threshold is 2, not 3, and tags flag_class
        # sticky_latch plus broad_calls_while_latched.
        latch_session = "sticky-latch-telemetry"
        run_pretooluse([user("delegate this to the cheapest qualified model")], latch_session, state)
        proc = run_pretooluse([user("delegate this to the cheapest qualified model"), broad()],
                               latch_session, state)
        b = bucket_for(state, latch_session)
        check("sticky latch: second broad call under an active task flags at threshold 2",
              b["would_have_flagged"] == 1
              and b["flag_classes"].get("sticky_latch") == 1
              and b["broad_calls_while_latched"] >= 1
              and len(b["task_ids"]) == 1, b)
        check("sticky latch: never blocked even though flagged", never_blocks(proc))

        # 2c. An executor declaration suppresses the flag (still classified,
        # never counted as an under-delegation moment). One call, window
        # already at the threshold with the declaration present, so nothing
        # upstream of it has already tripped the flag.
        exec_session = "executor-declared-telemetry"
        proc = run_pretooluse(
            [user("audit this"), broad(), broad(),
             assistant_text("executor: main seat — because this is the verification step")],
            exec_session, state,
        )
        b = bucket_for(state, exec_session)
        check("executor declared: no flag recorded despite crossing the count threshold",
              b["would_have_flagged"] == 0, b)

        # 2d. A targeted Read of one file, git status/log/diff, and a single
        # DB query never count as broad, however many stack up -- the
        # 2026-08-14 fix must survive the redesign.
        never_broad_session = "never-broad-telemetry"
        run_pretooluse(
            [user("fix the gate"), status_call(), status_call()],
            never_broad_session, state, tool_name="Bash", tool_input={"command": "git status"},
        )
        proc = run_pretooluse(
            [user("fix the gate"), status_call(), status_call(),
             tool_with_input("Bash", {"command": "git log --oneline -5"})],
            never_broad_session, state, tool_name="Bash", tool_input={"command": "git diff HEAD"},
        )
        b = bucket_for(state, never_broad_session)
        check("git plumbing never counts as broad, however many stack up",
              b["mechanical_calls"] == 2 and b["broad_calls"] == 0
              and b["would_have_flagged"] == 0, b)

        read_session = "targeted-read-telemetry"
        for _ in range(3):
            run_pretooluse(
                [user("audit this"), tool_with_input("Read", {"file_path": "/tmp/dg-single.py"})],
                read_session, state, tool_name="Read", tool_input={"file_path": "/tmp/dg-single.py"},
            )
        b = bucket_for(state, read_session)
        check("a targeted Read of one named file is never broad, however many stack up",
              b["mechanical_calls"] == 3 and b["broad_calls"] == 0, b)

        db_session = "db-query-telemetry"
        run_pretooluse([user("verify the record")], db_session, state,
                        tool_name="mcp__carr__update-deal", tool_input={})
        b = bucket_for(state, db_session)
        check("a single database query is mechanical but never broad",
              b["mechanical_calls"] == 1 and b["broad_calls"] == 0, b)

        # 2e. A real sweep (three distinct files, or grep/find/rg/fd) still
        # gets flagged -- the gate must remain a genuine classifier, only the
        # consequence changed. Four distinct files across window + current
        # call (one.txt not broad -- first file seen; two.txt and two-b.txt
        # broad; three.txt, the CURRENT call, is a fifth-never-seen file so it
        # is broad too): total_broad reaches 3 on the current call alone.
        sweep_session = "real-sweep-telemetry"
        proc = run_pretooluse(
            [user("audit this"),
             tool_with_input("Read", {"file_path": "/tmp/one.txt"}),
             tool_with_input("Read", {"file_path": "/tmp/two.txt"}),
             tool_with_input("Read", {"file_path": "/tmp/two-b.txt"})],
            sweep_session, state, tool_name="Read", tool_input={"file_path": "/tmp/three.txt"},
        )
        b = bucket_for(state, sweep_session)
        check("three distinct-file reads still flag a real sweep",
              b["would_have_flagged"] == 1
              and b["flag_classes"].get("second_mechanical_call") == 1, b)
        check("real sweep is never blocked either", never_blocks(proc))

        # 2f. A Read of a file this turn already wrote is exempt, matching the
        # enforcing version's self-check carve-out.
        selfwrite_session = "self-write-read-telemetry"
        run_pretooluse(
            [user("build this"), tool_with_input("Write", {"file_path": "/tmp/dg-out.txt"})],
            selfwrite_session, state, tool_name="Write", tool_input={"file_path": "/tmp/dg-out.txt"},
        )
        proc = run_pretooluse(
            [user("build this"), tool_with_input("Write", {"file_path": "/tmp/dg-out.txt"}),
             tool_with_input("Read", {"file_path": "/tmp/dg-a.txt"}),
             tool_with_input("Read", {"file_path": "/tmp/dg-b.txt"})],
            selfwrite_session, state, tool_name="Read", tool_input={"file_path": "/tmp/dg-out.txt"},
        )
        b = bucket_for(state, selfwrite_session)
        check("a Read of a file this turn just wrote is exempt from ever counting as broad",
              b["broad_calls"] == 0, b)

        # 2g. An Agent spawn this turn is still counted as a mechanical call
        # but never as broad/flagged (a delegation already happened).
        agent_session = "agent-used-telemetry"
        proc = run_pretooluse(
            [user("audit this"), broad(), broad(), tool("Agent")],
            agent_session, state, tool_name="Read", tool_input={"file_path": "/tmp/whatever.txt"},
        )
        b = bucket_for(state, agent_session)
        check("a call after an Agent spawn this turn counts as mechanical, never as broad",
              b["mechanical_calls"] == 1 and b["broad_calls"] == 0
              and b["would_have_flagged"] == 0, b)
        check("agent-spawn turn: never blocked", never_blocks(proc))

        # 2h. Codex's own tool spelling accumulates telemetry the same way.
        codex_session = "codex-telemetry"
        for i in range(3):
            recs = [codex_user("audit this", True)] + [codex_tool("exec") for _ in range(i)]
            transcript = write_transcript(recs)
            payload = {
                "hook_event_name": "PreToolUse", "cwd": REPO, "session_id": codex_session,
                "tool_name": "functions.exec", "tool_input": {"cmd": "grep -rn TODO ."},
                "transcript_path": transcript,
            }
            proc = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), text=True,
                                  capture_output=True, env=hook_env({"DELEGATION_GATE_STATE": state}))
            os.unlink(transcript)
        b = bucket_for(state, codex_session)
        check("Codex functions.exec accumulates telemetry across three calls",
              b["mechanical_calls"] == 3 and b["broad_calls"] == 3
              and b["would_have_flagged"] == 1, b)

        # --- 3. STOP SUMMARY ---------------------------------------------------

        # 3a. A session under the material threshold gets a ledger row, no
        # announcement.
        under_session = "stop-under-threshold"
        run_pretooluse([user("audit this")], under_session, state)
        proc = run_stop(under_session, state, ledger, threshold=3)
        response = parse_stdout_json(proc)
        rows = read_ledger_rows(ledger, under_session)
        check("Stop under threshold: exits 0", proc.returncode == 0)
        check("Stop under threshold: exactly one ledger row",
              len(rows) == 1, rows)
        check("Stop under threshold: row not materially under-delegated",
              rows and rows[0]["materially_under_delegated"] is False, rows)
        check("Stop under threshold: no announcement emitted",
              response is None or "additionalContext" not in json.dumps(response), response)
        check("Stop clears the session's telemetry bucket",
              bucket_for(state, under_session) is None)

        # 3b. A session that reaches the material threshold gets an
        # ANNOUNCE-level Stop message, still never a block.
        over_session = "stop-over-threshold"
        for _ in range(3):
            run_pretooluse([user("audit this"), broad(), broad()], over_session, state)
        proc = run_stop(over_session, state, ledger, threshold=3)
        response = parse_stdout_json(proc)
        rows = read_ledger_rows(ledger, over_session)
        check("Stop over threshold: exits 0 (announce, never a block)", proc.returncode == 0)
        check("Stop over threshold: exactly one ledger row", len(rows) == 1, rows)
        check("Stop over threshold: row IS materially under-delegated",
              rows and rows[0]["materially_under_delegated"] is True, rows)
        check("Stop over threshold: row would_have_flagged == 3",
              rows and rows[0]["would_have_flagged"] == 3, rows)
        additional_context = (
            isinstance(response, dict)
            and response.get("hookSpecificOutput", {}).get("additionalContext", "")
        )
        check("Stop over threshold: announces via hookSpecificOutput.additionalContext",
              isinstance(additional_context, str) and "DELEGATION TELEMETRY" in additional_context,
              response)
        check("Stop over threshold: announcement mentions the report script",
              isinstance(additional_context, str) and "delegation-telemetry-report.py" in additional_context,
              response)
        check("Stop over threshold: never a block decision",
              not (isinstance(response, dict) and response.get("decision") == "block"), response)

        # 3c. Latch state is reported honestly: a session whose task is still
        # active at Stop time reports latch_active_at_end True.
        active_latch_session = "stop-active-latch"
        run_pretooluse([user("delegate this to the cheapest qualified model"), broad()],
                        active_latch_session, state)
        proc = run_stop(active_latch_session, state, ledger, threshold=99)
        rows = read_ledger_rows(ledger, active_latch_session)
        check("Stop reports an active latch honestly",
              rows and rows[0]["latch_active_at_end"] is True, rows)
        check("Stop over an active latch is still never a block", never_blocks(proc))

        # 3d. A session that never made a mechanical call gets no ledger row
        # at all -- no noise for a session with nothing to report.
        idle_session = "stop-idle-session"
        proc = run_stop(idle_session, state, ledger)
        check("Stop for an idle session exits 0", proc.returncode == 0)
        check("Stop for an idle session writes no ledger row",
              len(read_ledger_rows(ledger, idle_session)) == 0)

        # --- 4. EXECUTOR regex sanity (used only to suppress a telemetry flag
        # now, but a regression here would silently start flagging every
        # declared executor as under-delegated) ---------------------------
        for label in ("main seat", "top seat", "inline", "orchestrator", "T3",
                      "Fable", "Opus", "Terra peer"):
            sample = f"executor: {label} — because this is a test"
            check(f"EXECUTOR regex still matches label {label!r}",
                  bool(GATE.EXECUTOR.search(sample)), sample)

    print(f"\n{sum(oks)}/{len(oks)} delegation-gate cases passed")
    return 0 if all(oks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
