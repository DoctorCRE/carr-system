#!/usr/bin/env python3
"""hook-meter-selftest.py — acceptance test for hooks/hook-meter-run.py + hook_meter.py.

WHAT IS BEING PROVEN, and it is one property above all others: WRAPPING A GATE
DOES NOT CHANGE WHAT IT DECIDES. Every wired gate in this system now runs inside
hooks/hook-meter-run.py. If that wrapper altered an exit code, swallowed a line
of stdout, or handed a gate an empty stdin, the whole enforcement stack would
change behaviour at once and the symptom would be a gate quietly allowing what
it used to refuse. So the first and largest section here fires REAL gates both
ways — bare, and wrapped — and demands byte-identical results.

THE SECOND PROPERTY: THE INSTRUMENT IS NEVER LOAD-BEARING. Proven the hard way
while this was being built. The first version had the gates do a bare
`import hook_meter`; ops/rule-shape-gate-selftest.py then failed because it
copies a gate into a temp directory, and the gate died at import with
ModuleNotFoundError instead of reaching its verdict. On a deny gate that is a
refusal silently not happening. Case 20 below deletes the meter and requires the
gate to still exit 2.

THE THIRD: the live / fixture / unclassified split is what makes the numbers
mean anything, so its routing is tested directly rather than assumed.

Fixture runs of this file mark themselves with CARR_HOOK_FIXTURE=1 and redirect
both streams to a temp directory, so running this selftest never writes a line
into the operational logs it exists to keep clean.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(REPO, "hooks")
RUNNER = os.path.join(HOOKS, "hook-meter-run.py")
METER = os.path.join(HOOKS, "hook_meter.py")

failures: list[str] = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        failures.append(name)


def payload(event="PreToolUse", tool="Bash", tool_input=None, **extra):
    # tool_use_id and prompt_id are the harness's own correlation keys — see
    # hooks/staging-observation-tracker.py, which has depended on tool_use_id
    # being identical across the Pre and Post call of one invocation since it
    # was written. They are in the default fixture rather than bolted onto one
    # case, so every check below runs against a realistic payload.
    body = {
        "session_id": "hook-meter-selftest",
        "transcript_path": "/tmp/hook-meter-selftest.jsonl",
        "cwd": REPO,
        "hook_event_name": event,
        "tool_name": tool,
        "tool_use_id": "toolu_selftest_01",
        "prompt_id": "prompt_selftest_01",
        "tool_input": tool_input if tool_input is not None else {"command": "echo hi"},
    }
    body.update(extra)
    return json.dumps(body)


def env_for(tmp, **extra):
    env = dict(os.environ)
    env["CARR_HOOK_FIXTURE"] = "1"
    env["CARR_HOOK_TELEMETRY"] = os.path.join(tmp, "telemetry.jsonl")
    env["CARR_HOOK_GUARD_LOG"] = os.path.join(tmp, "guard.log")
    env.update(extra)
    return env


def fire(args, data, env, cwd=None):
    proc = subprocess.run([sys.executable] + args, input=data, capture_output=True,
                          text=True, env=env, cwd=cwd or REPO)
    return proc.returncode, proc.stdout, proc.stderr


def records(tmp):
    path = os.path.join(tmp, "telemetry.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def write_gate(directory, name, body):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


# ── 1. decision equivalence against the real wired gates ────────────────────
# Chosen to cover both verdicts and several input shapes rather than to be
# exhaustive: a gate that denies, gates that allow, a malformed payload, an
# empty payload, and a Stop-event payload.
EQUIVALENCE = [
    ("guard-unattended.py", payload(tool_input={"command": "echo hi"})),
    ("guard-unattended.py", payload(tool_input={"command": "rm -rf /Users/booko/Documents/live"})),
    ("guard-unattended.py", payload(tool_input={"command": "git push --force origin main"})),
    ("guard-unattended.py", "not json at all"),
    ("guard-unattended.py", ""),
    ("bash-write-gate.py", payload(tool_input={"command": "ls -la"})),
    ("executor-tier-gate.py", payload(tool="Agent",
                                      tool_input={"subagent_type": "poisoned", "description": ""})),
    ("one-repo-gate.py", payload(tool="Write",
                                 tool_input={"file_path": "/tmp/x.py", "content": "hi"})),
    ("gate-edit-gate.py", payload(tool="Write",
                                  tool_input={"file_path": "/tmp/x.py", "content": "hi"})),
    ("record-home-gate.py", payload(tool="Write",
                                    tool_input={"file_path": "/tmp/x.md", "content": "hi"})),
    ("write-effect-check.py", payload(event="PostToolUse",
                                      tool_input={"command": "echo hi"},
                                      tool_response={"stdout": "hi"})),
    ("chat-lint-gate.py", payload(event="Stop", tool="", tool_input={})),
    ("completion-evidence-gate.py", payload(event="Stop", tool="", tool_input={})),
    ("unread-artifact-gate.py", payload(event="Stop", tool="", tool_input={})),
]


def equivalence_cases(tmp):
    seen_deny = False
    for gate, data in EQUIVALENCE:
        target = os.path.join(HOOKS, gate)
        if not os.path.exists(target):
            check(f"{gate} exists to be tested", False, "missing")
            continue
        env = env_for(tmp)
        bare_rc, bare_out, bare_err = fire([target], data, env)
        wrap_rc, wrap_out, wrap_err = fire([RUNNER, target], data, env)
        label = f"{gate} rc={bare_rc}"
        check(f"same exit code wrapped — {label}", bare_rc == wrap_rc,
              f"bare {bare_rc} wrapped {wrap_rc}")
        check(f"same stdout wrapped — {label}", bare_out == wrap_out,
              f"\n      bare={bare_out[:160]!r}\n      wrap={wrap_out[:160]!r}")
        if bare_rc == 0 or bare_rc == 2:
            # A crashing gate's traceback legitimately gains the wrapper's own
            # two frames at the top; every other path must match exactly.
            check(f"same stderr wrapped — {label}", bare_err == wrap_err,
                  f"\n      bare={bare_err[:160]!r}\n      wrap={wrap_err[:160]!r}")
        if bare_rc == 2:
            seen_deny = True
    check("the equivalence matrix actually exercised a DENY", seen_deny,
          "every case allowed — the test proves less than it claims")

    # The Claude lifecycle gate publishes a structured Stop announcement and persists a
    # per-task version. Give bare and wrapped runs independent empty state
    # roots so the wrapper comparison is byte-for-byte, not normalized after
    # the fact.
    transcript = os.path.join(tmp, "context-equivalence.jsonl")
    with open(transcript, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "type": "assistant",
            "message": {
                "model": "claude-opus-4-1",
                "usage": {
                    "input_tokens": 2,
                    "cache_creation_input_tokens": 98,
                    "cache_read_input_tokens": 599_800,
                    "output_tokens": 100,
                },
            },
        }) + "\n")
    data = payload(event="Stop", tool="", tool_input={},
                   transcript_path=transcript, session_id="context-equivalent")
    target = os.path.join(HOOKS, "context-handoff-gate.py")
    bare_env = env_for(
        tmp,
        CARR_SESSION_CONTEXT_STATE_DIR=os.path.join(tmp, "context-bare"),
        CARR_CONTEXT_AUDIT="off",
    )
    wrapped_env = env_for(
        tmp,
        CARR_SESSION_CONTEXT_STATE_DIR=os.path.join(tmp, "context-wrapped"),
        CARR_CONTEXT_AUDIT="off",
    )
    bare_rc, bare_out, bare_err = fire([target], data, bare_env)
    wrap_rc, wrap_out, wrap_err = fire([RUNNER, target], data, wrapped_env)
    check("context lifecycle wrapped exit is identical",
          bare_rc == wrap_rc, f"bare {bare_rc} wrapped {wrap_rc}")
    check("context lifecycle wrapped stdout is byte-identical",
          bare_out == wrap_out, f"bare={bare_out!r} wrapped={wrap_out!r}")
    check("context lifecycle wrapped stderr is byte-identical",
          bare_err == wrap_err, f"bare={bare_err!r} wrapped={wrap_err!r}")
    lifecycle_rows = [row for row in records(tmp)
                      if row.get("hook") == "context-handoff-gate.py"]
    check("context lifecycle telemetry records the announce-only outcome",
          lifecycle_rows
          and lifecycle_rows[-1].get("outcome") == "allow"
          and lifecycle_rows[-1].get("register") == "announce"
          and lifecycle_rows[-1].get("reopen") is False
          and lifecycle_rows[-1].get("deny_class") is None,
          lifecycle_rows[-1] if lifecycle_rows else "no row")

    malformed_env = env_for(
        tmp,
        CARR_SESSION_CONTEXT_STATE_DIR=os.path.join(tmp, "context-malformed"),
        CARR_CONTEXT_AUDIT="off",
        CARR_CONTEXT_HOOK_EVENT="Stop",
    )
    malformed_rc, malformed_out, malformed_err = fire(
        [RUNNER, target], "{bad", malformed_env)
    malformed_rows = [row for row in records(tmp)
                      if row.get("hook") == "context-handoff-gate.py"]
    malformed_row = malformed_rows[-1] if malformed_rows else {}
    check("malformed Stop telemetry uses trusted wired event",
          malformed_rc == 0 and malformed_out.strip()
          and not malformed_err.strip()
          and malformed_row.get("event") == "Stop"
          and malformed_row.get("outcome") == "allow"
          and malformed_row.get("register") == "announce"
          and malformed_row.get("reopen") is False
          and malformed_row.get("deny_class") is None,
          (malformed_rc, malformed_out, malformed_err, malformed_row))


# ── 2. synthetic gates: every outcome shape the classifier must name ────────
GATES = {
    "allow.py": "import sys\nsys.exit(0)\n",
    "deny.py": "import sys\nsys.stderr.write('REFUSED — the reason\\nmore detail\\n')\nsys.exit(2)\n",
    "crash.py": "raise RuntimeError('gate is broken')\n",
    "oddexit.py": "import sys\nsys.exit(9)\n",
    "stringexit.py": "import sys\nsys.exit('a message not a code')\n",
    "stopblock.py": ("import json, sys\n"
                     "print(json.dumps({'decision': 'block', 'reason': 'not done'}))\n"),
    "permdeny.py": ("import json, sys\n"
                    "print(json.dumps({'hookSpecificOutput': "
                    "{'hookEventName': 'PreToolUse', 'permissionDecision': 'deny'}}))\n"),
    "permask.py": ("import json, sys\n"
                   "print(json.dumps({'hookSpecificOutput': "
                   "{'hookEventName': 'PreToolUse', 'permissionDecision': 'ask'}}))\n"),
    "classdeny.py": ("import sys\n"
                     "sys.stderr.write('DENY-CLASS: vault-write\\nlong prose after\\n')\n"
                     "sys.exit(2)\n"),
    "readstdin.py": ("import sys\n"
                     "data = sys.stdin.read()\n"
                     "sys.exit(2 if 'rm -rf' in data else 0)\n"),
    "bufferstdin.py": ("import sys\n"
                       "raw = sys.stdin.buffer.read()\n"
                       "sys.exit(2 if b'rm -rf' in raw else 0)\n"),
    "jsonstdin.py": ("import json, sys\n"
                     "d = json.load(sys.stdin)\n"
                     "sys.exit(2 if d['tool_input']['command'].startswith('rm') else 0)\n"),
    "argvgate.py": ("import sys\n"
                    "print('arg=' + (sys.argv[1] if len(sys.argv) > 1 else 'none'))\n"),
    "namegate.py": ("import sys\n"
                    "print('name=' + __name__)\nprint('file=' + __file__)\n"),
    "chatty.py": "print('x' * 200000)\n",
    # The register a demoted Stop gate now speaks in: it returns context to the
    # model without blocking, so it costs nothing beyond the turn already paid
    # for. Five real Stop gates moved to this shape on 2026-08-23.
    "announcer.py": ("import json\n"
                     "print(json.dumps({'hookSpecificOutput': "
                     "{'hookEventName': 'Stop', "
                     "'additionalContext': 'a note for the model'}}))\n"),
}


def synthetic_cases(tmp):
    gates = os.path.join(tmp, "gates")
    os.makedirs(gates, exist_ok=True)
    for name, body in GATES.items():
        write_gate(gates, name, body)
    env = env_for(tmp)

    def run(name, data=None, event="PreToolUse", extra_args=()):
        return fire([RUNNER, os.path.join(gates, name)] + list(extra_args),
                    data if data is not None else payload(event=event), env)

    rc, out, err = run("allow.py")
    check("allow gate exits 0", rc == 0, f"exit {rc}")

    rc, out, err = run("deny.py")
    check("deny gate keeps exit 2", rc == 2, f"exit {rc}")
    check("deny gate's stderr reaches the harness untouched",
          err == "REFUSED — the reason\nmore detail\n", repr(err))

    rc, out, err = run("crash.py")
    check("a crashing gate still exits 1", rc == 1, f"exit {rc}")
    check("the gate's own exception text survives", "gate is broken" in err, err[-200:])

    rc, _, _ = run("oddexit.py")
    check("an unusual exit code is passed through unchanged", rc == 9, f"exit {rc}")

    rc, _, err = run("stringexit.py")
    check("sys.exit(str) exits 1 like bare python", rc == 1, f"exit {rc}")
    check("sys.exit(str) prints its message to stderr",
          "a message not a code" in err, repr(err))

    rc, out, _ = run("argvgate.py", extra_args=["drift-claim-gate.py"])
    check("a trailing argument reaches the gate as argv[1]",
          out.strip() == "arg=drift-claim-gate.py", repr(out))

    rc, out, _ = run("namegate.py")
    check("the gate runs as __main__", "name=__main__" in out, repr(out))
    check("the gate sees its own __file__", "namegate.py" in out, repr(out))

    deny_payload = payload(tool_input={"command": "rm -rf /somewhere"})
    for gate in ("readstdin.py", "bufferstdin.py", "jsonstdin.py"):
        rc, _, err = run(gate, data=deny_payload)
        check(f"{gate} receives the real payload on stdin", rc == 2, f"exit {rc} {err[:120]}")

    rc, out, _ = run("chatty.py")
    check("a gate larger than the capture cap still writes all of its stdout",
          len(out) >= 200000, f"got {len(out)} bytes")

    # The gates that publish a verdict in JSON rather than in an exit code. They
    # all exit 0, so the harness — and the meter — learn the decision only by
    # reading what they printed.
    for name in ("permdeny.py", "permask.py", "classdeny.py"):
        rc, out, _ = run(name)
        check(f"{name} keeps its own exit code", rc in (0, 2), f"exit {rc}")

    # outcome classification, read back off the telemetry
    rows = {r["hook"]: r for r in records(tmp)}
    check("allow.py is recorded as allow", rows.get("allow.py", {}).get("outcome") == "allow",
          rows.get("allow.py"))
    check("deny.py is recorded as deny", rows.get("deny.py", {}).get("outcome") == "deny",
          rows.get("deny.py"))
    check("crash.py is recorded as error", rows.get("crash.py", {}).get("outcome") == "error",
          rows.get("crash.py"))
    check("oddexit.py is recorded as error", rows.get("oddexit.py", {}).get("outcome") == "error",
          rows.get("oddexit.py"))
    check("a JSON permissionDecision:deny is recorded as deny",
          rows.get("permdeny.py", {}).get("outcome") == "deny", rows.get("permdeny.py"))
    check("a JSON permissionDecision:ask is recorded as ask, not deny",
          rows.get("permask.py", {}).get("outcome") == "ask", rows.get("permask.py"))
    check("a declared DENY-CLASS is captured",
          rows.get("classdeny.py", {}).get("deny_class") == "vault-write",
          rows.get("classdeny.py"))
    check("a refusal's first line is kept as the de-facto class",
          rows.get("deny.py", {}).get("deny_headline") == "REFUSED — the reason",
          rows.get("deny.py"))
    check("an allow carries no deny headline",
          rows.get("allow.py", {}).get("deny_headline") is None, rows.get("allow.py"))
    for name in ("allow.py", "deny.py"):
        rec = rows.get(name, {})
        check(f"{name} record carries a monotonic elapsed_ms",
              isinstance(rec.get("elapsed_ms"), (int, float)) and rec["elapsed_ms"] >= 0,
              rec.get("elapsed_ms"))
        check(f"{name} record carries the meter's own cost",
              isinstance(rec.get("meter_ms"), (int, float)), rec.get("meter_ms"))
        check(f"{name} record names the event and session",
              rec.get("event") == "PreToolUse" and rec.get("session") == "hook-meter-selftest",
              rec)
        # The correlation keys. Without tool_use_id the rollup can only guess
        # which firings belonged to one tool call, and a per-event budget built
        # on a guess is the false green this report exists to avoid.
        check(f"{name} record carries the harness's tool_use_id",
              rec.get("tool_use_id") == "toolu_selftest_01", rec.get("tool_use_id"))
        check(f"{name} record carries the prompt_id that identifies the turn",
              rec.get("prompt_id") == "prompt_selftest_01", rec.get("prompt_id"))


# ── 3. Stop reopens, which are the expensive firings ────────────────────────
def reopen_cases(tmp):
    gates = os.path.join(tmp, "gates")
    env = env_for(tmp, CARR_HOOK_TELEMETRY=os.path.join(tmp, "reopen.jsonl"))

    def rows():
        path = os.path.join(tmp, "reopen.jsonl")
        return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]

    fire([RUNNER, os.path.join(gates, "deny.py")], payload(event="Stop", tool=""), env)
    last = rows()[-1]
    check("a Stop gate that exits 2 is counted as a turn reopen", last.get("reopen") is True, last)

    fire([RUNNER, os.path.join(gates, "stopblock.py")], payload(event="Stop", tool=""), env)
    last = rows()[-1]
    check("a Stop gate that publishes decision:block is a reopen too",
          last.get("reopen") is True and last.get("outcome") == "deny", last)

    fire([RUNNER, os.path.join(gates, "allow.py")], payload(event="Stop", tool=""), env)
    last = rows()[-1]
    check("a Stop gate that allows is not a reopen", last.get("reopen") is False, last)

    fire([RUNNER, os.path.join(gates, "deny.py")], payload(event="PreToolUse"), env)
    last = rows()[-1]
    check("a PreToolUse deny is NOT a reopen — it costs no tokens",
          last.get("reopen") is False, last)

    # REGISTER: recorded from what the gate emitted, never re-derived from the
    # exit code downstream. The whole point is that a gate which announces looks
    # nothing like a gate which has gone quiet, even though both exit 0.
    fire([RUNNER, os.path.join(gates, "deny.py")], payload(event="Stop", tool=""), env)
    check("a blocking Stop gate is recorded in the reopen register",
          rows()[-1].get("register") == "reopen", rows()[-1])

    fire([RUNNER, os.path.join(gates, "announcer.py")], payload(event="Stop", tool=""), env)
    last = rows()[-1]
    check("an announcing Stop gate is recorded as an announcement",
          last.get("register") == "announce", last)
    check("and an announcement is NOT a reopen — it costs no extra turn",
          last.get("reopen") is False and last.get("outcome") == "allow", last)

    fire([RUNNER, os.path.join(gates, "allow.py")], payload(event="Stop", tool=""), env)
    check("a gate that emits nothing is recorded as silent, not as an announcement",
          rows()[-1].get("register") == "silent", rows()[-1])

    fire([RUNNER, os.path.join(gates, "crash.py")], payload(event="Stop", tool=""), env)
    check("a gate that fell over did not speak in any register",
          rows()[-1].get("register") == "error", rows()[-1])

    fire([RUNNER, os.path.join(gates, "permdeny.py")], payload(event="PreToolUse"), env)
    check("a PreToolUse refusal is a block, not a reopen",
          rows()[-1].get("register") == "block", rows()[-1])


# ── 4. the split that makes the numbers mean anything ───────────────────────
def routing_cases(tmp):
    sys.path.insert(0, HOOKS)
    import hook_meter

    saved = {k: os.environ.get(k) for k in
             ("CARR_HOOK_LIVE", "CARR_HOOK_FIXTURE", "PYTEST_CURRENT_TEST",
              "CARR_HOOK_GUARD_LOG", "CARR_HOOK_TELEMETRY")}
    try:
        for key in saved:
            os.environ.pop(key, None)
        check("an unmarked process is unclassified, not live",
              hook_meter.source() == hook_meter.UNCLASSIFIED, hook_meter.source())
        check("an unmarked gate's log goes to its own file, losing nothing",
              hook_meter.guard_log_path("/r") == "/r/out/hook-guard-unclassified.log",
              hook_meter.guard_log_path("/r"))

        os.environ["CARR_HOOK_LIVE"] = "1"
        check("the wrapper's marker makes a firing live",
              hook_meter.source() == hook_meter.LIVE, hook_meter.source())
        check("a live gate writes the operational log",
              hook_meter.guard_log_path("/r") == "/r/out/hook-guard.log",
              hook_meter.guard_log_path("/r"))

        os.environ["CARR_HOOK_FIXTURE"] = "1"
        check("a fixture marker BEATS the live marker",
              hook_meter.source() == hook_meter.FIXTURE, hook_meter.source())
        check("a fixture gate writes the fixture log",
              hook_meter.guard_log_path("/r") == "/r/out/fixtures/hook-guard-fixture.log",
              hook_meter.guard_log_path("/r"))
        check("mark_live cannot promote a fixture run to live",
              hook_meter.mark_live() == hook_meter.FIXTURE, hook_meter.mark_live())

        os.environ.pop("CARR_HOOK_FIXTURE")
        os.environ.pop("CARR_HOOK_LIVE")
        os.environ["PYTEST_CURRENT_TEST"] = "x"
        check("a pytest run counts as a fixture",
              hook_meter.source() == hook_meter.FIXTURE, hook_meter.source())
        os.environ.pop("PYTEST_CURRENT_TEST")

        os.environ["CARR_HOOK_GUARD_LOG"] = "/tmp/override.log"
        check("an explicit override wins over every stream rule",
              hook_meter.guard_log_path("/r") == "/tmp/override.log",
              hook_meter.guard_log_path("/r"))
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# ── 5. the property proven the hard way: never load-bearing ─────────────────
def fallback_cases(tmp):
    """A gate whose meter has been deleted must still reach its verdict."""
    island = os.path.join(tmp, "island")
    os.makedirs(os.path.join(island, "hooks"), exist_ok=True)
    needed = ["guard-unattended.py", "cmd_text.py", "refused_content.py",
              "md_manifest.py", "corpus_renders.py", "gate_paths.py",
              "hook-meter-run.py"]
    for name in needed:
        src = os.path.join(HOOKS, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(island, "hooks", name))
    check("the island fixture deliberately has NO hook_meter.py",
          not os.path.exists(os.path.join(island, "hooks", "hook_meter.py")))

    gate = os.path.join(island, "hooks", "guard-unattended.py")
    data = payload(tool_input={"command": "rm -rf /Users/booko/Documents/live"})
    env = env_for(tmp)

    rc, _, err = fire([gate], data, env, cwd=island)
    check("without the meter, the bare gate still DENIES", rc == 2,
          f"exit {rc} — {err[:200]}")

    runner = os.path.join(island, "hooks", "hook-meter-run.py")
    rc, _, err = fire([runner, gate], data, env, cwd=island)
    check("without the meter, the WRAPPED gate still DENIES", rc == 2,
          f"exit {rc} — {err[:200]}")
    check("and it does not crash trying to record", "ModuleNotFoundError" not in err,
          err[:200])


# ── 6. cost, concurrency, rotation ──────────────────────────────────────────
def cost_cases(tmp):
    gates = os.path.join(tmp, "gates")
    env = env_for(tmp, CARR_HOOK_TELEMETRY=os.path.join(tmp, "cost.jsonl"))
    for _ in range(12):
        fire([RUNNER, os.path.join(gates, "allow.py")], payload(), env)
    rows = [json.loads(line) for line in
            open(os.path.join(tmp, "cost.jsonl"), encoding="utf-8") if line.strip()]
    costs = sorted(r["meter_ms"] for r in rows if isinstance(r.get("meter_ms"), (int, float)))
    check("every firing recorded its own cost", len(costs) == 12, len(costs))

    # NO ABSOLUTE MILLISECOND THRESHOLD HERE, and that is a correction rather
    # than a convenience. This check first read "floor cost stays under 5ms" and
    # failed inside CI at 5.76ms on 2026-08-23 — not because the meter regressed
    # but because the gates class runs 231 selftests on a 16GB machine that was
    # already carrying three other CI runs at load average 110. A wall-clock
    # constant asserted on this host measures the host, and a check that goes red
    # on normal work is the check people learn to scroll past.
    #
    # So the assertion is a RATIO against a real gate timed in the same run,
    # under whatever load exists: the instrument must stay small next to the
    # thing it measures, and load moves both terms together. The actual ~2%
    # budget is not decided here at all — ops/hook-telemetry-rollup.py computes
    # meter share continuously from thousands of live firings, which is a far
    # better instrument than twelve samples on a busy laptop.
    real_env = env_for(tmp, CARR_HOOK_TELEMETRY=os.path.join(tmp, "real.jsonl"))
    gate = os.path.join(HOOKS, "guard-unattended.py")
    for _ in range(8):
        fire([RUNNER, gate], payload(), real_env)
    real = [json.loads(line) for line in
            open(os.path.join(tmp, "real.jsonl"), encoding="utf-8") if line.strip()]
    meter = sorted(r["meter_ms"] for r in real)
    gate_ms = sorted(r["elapsed_ms"] for r in real)
    mid = lambda v: v[len(v) // 2]           # noqa: E731 — median, no import
    check("the meter costs a small fraction of the gate it measures",
          real and mid(meter) <= 0.25 * mid(gate_ms),
          f"meter {mid(meter) if real else '—'}ms vs gate {mid(gate_ms) if real else '—'}ms")

    procs = []
    conc_env = env_for(tmp, CARR_HOOK_TELEMETRY=os.path.join(tmp, "conc.jsonl"))
    for _ in range(13):                 # the real number of hooks per Bash call
        procs.append(subprocess.Popen(
            [sys.executable, RUNNER, os.path.join(gates, "allow.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=conc_env, cwd=REPO))
    for proc in procs:
        proc.communicate(input=payload())
    lines = [line for line in open(os.path.join(tmp, "conc.jsonl"), encoding="utf-8")
             if line.strip()]
    check("13 concurrent firings write 13 lines", len(lines) == 13, len(lines))
    intact = all(json.loads(line).get("hook") == "allow.py" for line in lines)
    check("no concurrent write tore another one in half", intact)

    sys.path.insert(0, HOOKS)
    import hook_meter
    rot = os.path.join(tmp, "rot.jsonl")
    saved = hook_meter.ROTATE_BYTES
    try:
        hook_meter.ROTATE_BYTES = 400
        for i in range(20):
            hook_meter.emit(tmp, {"n": i, "pad": "y" * 50},
                            src=hook_meter.FIXTURE)
    finally:
        hook_meter.ROTATE_BYTES = saved
    stream = hook_meter.telemetry_path(tmp, hook_meter.FIXTURE)
    check("the stream rotates instead of growing without bound",
          os.path.exists(stream + ".1"), f"no {stream}.1")
    del rot


def main():
    print("hook-meter-selftest")
    tmp = tempfile.mkdtemp(prefix="hook-meter-selftest-")
    try:
        print("\n decision equivalence — real gates, bare vs wrapped")
        equivalence_cases(tmp)
        print("\n outcome classification — synthetic gates")
        synthetic_cases(tmp)
        print("\n turn reopens")
        reopen_cases(tmp)
        print("\n live / fixture / unclassified routing")
        routing_cases(tmp)
        print("\n the meter is never load-bearing")
        fallback_cases(tmp)
        print("\n cost, concurrency, rotation")
        cost_cases(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print(f"FAIL {len(failures)} check(s): {', '.join(failures[:8])}"
              + (" …" if len(failures) > 8 else ""))
        return 1
    print("OK all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
