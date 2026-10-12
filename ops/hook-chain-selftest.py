#!/usr/bin/env python3
"""hook-chain-selftest.py — a chain of gates in ONE interpreter decides exactly
what the same gates decided as separate processes (WR-000259).

THE PROPERTY. lib/hook_execution.py run_chain runs every gate of one event and
matcher in a single interpreter and merges their results for the harness. If
the merge dropped a deny, reordered the gates, leaked one gate's environment
into the next, or let a hung gate hold the others hostage, enforcement would
change for every Bash call at once. So each case here fires the SAME gates both
ways — one process each, the way the harness ran them before, and one chain —
and compares what the harness acts on:

  blocked?   any member exit 2, or a JSON deny on exit 0
  model text the denying members' stderr (and JSON reasons) when blocked,
             otherwise the additionalContext texts, in declared order
  decision   the most restrictive permissionDecision (deny > ask > allow)

and, per gate, the telemetry row the scorecard reads (hook, outcome, exit,
register, deny_class, deny_headline, error_tail). A single deny or a single
speaking gate must come through BYTE-identical.

Three sections:
  1. synthetic gates that pin each merge rule (deny, JSON deny, context merge,
     allow+context, crash, timeout, env isolation, declared order, all members
     run after a deny);
  2. the REAL wired PreToolUse Bash chain, rendered from
     ops/config/gate-declarations.json, against real and scenario payloads from
     ops/fixtures/real-replay (benign commands, bash-write denies, GitHub burst
     denies, the draft-export announce, and combinations of them);
  3. MUTANTS: the runner is copied with a deliberate defect — a dropped deny,
     reversed member order, no environment restore, no per-member timeout — and
     section 1 must FAIL against each. A mutant that survives fails this suite,
     so the cases above cannot quietly stop meaning anything.
Plus the admission rules in lib/gate_declarations.py (hazard scan, events,
contiguity, minimum size) on synthetic declarations.

Everything runs in a throwaway island checkout (hooks/ and lib/ copied, its own
git repo, its own HOME), so no gate state lands in a real out/ directory.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from lib import gate_declarations  # noqa: E402

FIXTURES = os.path.join(REPO, "ops", "fixtures", "real-replay")
JOIN = "\n\n"
SEVERITY = {"allow": 1, "ask": 2, "deny": 3}
ROW_FIELDS = ("hook", "event", "tool", "tool_use_id", "outcome", "exit", "register",
              "reopen", "deny_class", "deny_headline", "error_tail")

failures = []
QUIET = [False]          # the mutant pass counts failures without printing them


def check(name, cond, detail=""):
    if cond:
        if not QUIET[0]:
            print(f"  ok   {name}")
    else:
        if not QUIET[0]:
            print(f"  FAIL {name} {str(detail)[:600]}")
        failures.append(name)


# ── fixture gates ────────────────────────────────────────────────────────────
GATES = {
    "allow.py": "import sys, json\njson.load(sys.stdin)\n",
    "deny_x.py": ("import sys\nsys.stdin.read()\n"
                  "sys.stderr.write('DENY-CLASS: fixture.x\\nBLOCKED by fixture X: reason one\\n')\n"
                  "sys.exit(2)\n"),
    "deny_y.py": ("import sys\nsys.stdin.read()\n"
                  "print('BLOCKED by fixture Y', file=sys.stderr)\nraise SystemExit(2)\n"),
    "deny_json.py": ("import sys, json\njson.load(sys.stdin)\n"
                     "print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',"
                     " 'permissionDecision': 'deny', 'permissionDecisionReason': 'json says no'}}))\n"),
    "context_a.py": ("import sys, json\njson.load(sys.stdin)\n"
                     "print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',"
                     " 'additionalContext': 'A-context: read me'}}))\n"),
    "context_b.py": ("import sys, json\njson.load(sys.stdin)\n"
                     "print(json.dumps({'systemMessage': 'B says hi', 'hookSpecificOutput': "
                     "{'hookEventName': 'PreToolUse', 'additionalContext': 'B-context'}}))\n"),
    "allow_json.py": ("import sys, json\njson.load(sys.stdin)\n"
                      "print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',"
                      " 'permissionDecision': 'allow', 'additionalContext': 'allowed, note this'}}))\n"),
    "crash.py": "import sys\nsys.stdin.read()\nraise RuntimeError('fixture crash')\n",
    "slow.py": "import sys, time\nsys.stdin.read()\ntime.sleep(6)\nprint('too late')\n",
    "leak.py": ("import os, sys\nsys.stdin.read()\nos.environ['CHAIN_LEAK'] = '1'\n"
                "sys.path.insert(0, '/chain-leak')\n"),
    "probe.py": ("import os, sys\nsys.stdin.read()\n"
                 "if os.environ.get('CHAIN_LEAK') or '/chain-leak' in sys.path:\n"
                 "    sys.stderr.write('LEAKED state from an earlier gate\\n'); sys.exit(2)\n"),
    "order_a.py": ("import os, sys\nsys.stdin.read()\n"
                   "open(os.environ['CHAIN_ORDER_LOG'], 'a').write('a\\n')\n"),
    "order_b.py": ("import os, sys\nsys.stdin.read()\n"
                   "open(os.environ['CHAIN_ORDER_LOG'], 'a').write('b\\n')\n"),
}

# (name, [(timeout, gate)], solo comparable?)  timeouts are the chain's per-member seconds
SYNTHETIC = [
    ("single deny is byte-identical", [(10, "allow.py"), (10, "deny_x.py"), (10, "allow.py")]),
    ("single context is byte-identical", [(10, "allow.py"), (10, "context_a.py")]),
    ("two denies both reach the model", [(10, "deny_x.py"), (10, "context_a.py"), (10, "deny_y.py")]),
    ("JSON deny alone blocks", [(10, "context_a.py"), (10, "deny_json.py")]),
    ("exit-2 deny plus JSON deny", [(10, "deny_json.py"), (10, "deny_y.py")]),
    ("contexts merge in declared order", [(10, "context_a.py"), (10, "allow.py"), (10, "context_b.py")]),
    ("allow decision keeps context", [(10, "allow_json.py"), (10, "context_b.py")]),
    ("crash beside context is non-blocking", [(10, "crash.py"), (10, "context_a.py")]),
    ("crash alone is a non-blocking error", [(10, "allow.py"), (10, "crash.py")]),
    ("crash beside deny still denies", [(10, "crash.py"), (10, "deny_x.py")]),
    ("all silent", [(10, "allow.py"), (10, "allow.py")]),
    ("environment does not leak forward", [(10, "leak.py"), (10, "probe.py")]),
]


def write_gates(directory):
    os.makedirs(directory, exist_ok=True)
    for name, body in GATES.items():
        with open(os.path.join(directory, name), "w", encoding="utf-8") as fh:
            fh.write(body)


def build_island(tmp, mutate=None):
    """A minimal checkout: hooks/ + lib/, its own git repo, fixture gates."""
    island = os.path.join(tmp, "island")
    shutil.copytree(os.path.join(REPO, "hooks"), os.path.join(island, "hooks"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(os.path.join(REPO, "lib"), os.path.join(island, "lib"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    if mutate:
        path = os.path.join(island, "lib", "hook_execution.py")
        text = open(path, encoding="utf-8").read()
        old, new = mutate
        if old not in text:
            raise AssertionError(f"mutant target missing from hook_execution.py: {old!r}")
        open(path, "w", encoding="utf-8").write(text.replace(old, new, 1))
    os.makedirs(os.path.join(island, "out"), exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=island, check=True,
                   env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    write_gates(os.path.join(island, "fixture-gates"))
    return island


def env_for(tmp, telemetry):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CARR_", "CLAUDE_")) and k != "PYTHONPATH"}
    env.update({
        "HOME": os.path.join(tmp, "home"),
        "CARR_HOOK_FIXTURE": "1",
        "CARR_HOOK_TELEMETRY": telemetry,
        "CARR_HOOK_GUARD_LOG": os.path.join(tmp, "guard.log"),
        "CARR_SETTINGS_GATE_OFFLINE": "1",
        "CHAIN_ORDER_LOG": os.path.join(tmp, "order.log"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    })
    os.makedirs(env["HOME"], exist_ok=True)
    return env


def payload(island, tool_input, tool_use_id):
    return json.dumps({
        "session_id": "hook-chain-selftest", "transcript_path": "/nonexistent/chain.jsonl",
        "cwd": island, "permission_mode": "default", "hook_event_name": "PreToolUse",
        "tool_name": "Bash", "tool_input": tool_input, "tool_use_id": tool_use_id,
    })


def fire(island, argv, data, env, timeout=60):
    started = time.monotonic()
    try:
        proc = subprocess.run([sys.executable, os.path.join(island, "hooks", "hook-meter-run.py")]
                              + argv, input=data, capture_output=True, text=True, env=env,
                              cwd=island, timeout=timeout)
        result = (proc.returncode, proc.stdout, proc.stderr)
    except subprocess.TimeoutExpired:
        result = (None, "", "")          # the harness killed it: no verdict, fails open
    return result, time.monotonic() - started


def rows(path, tool_use_id):
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return []
    out = [json.loads(line) for line in lines if line.strip()]
    return [r for r in out if r.get("tool_use_id") == tool_use_id]


def _json(text):
    try:
        value = json.loads((text or "").strip())
        return value if isinstance(value, dict) else None
    except ValueError:
        return None


def harness_view(results):
    """What the harness acts on, given one or more hook results (code, out, err)."""
    denies, contexts, messages, decision = [], [], [], None
    for code, out, err in results:
        if code == 2:
            denies.append(err if err.endswith("\n") else err + "\n")
            continue
        if code != 0:
            continue
        data = _json(out) or {}
        specific = data.get("hookSpecificOutput") or {}
        pd = specific.get("permissionDecision")
        if pd == "deny" or data.get("decision") in ("block", "deny"):
            reason = specific.get("permissionDecisionReason") or data.get("reason") or "denied"
            denies.append(reason if reason.endswith("\n") else reason + "\n")
            continue
        if SEVERITY.get(pd, 0) > SEVERITY.get(decision, 0):
            decision = pd
        if specific.get("additionalContext"):
            contexts.append(specific["additionalContext"])
        if data.get("systemMessage"):
            messages.append(data["systemMessage"])
    if denies:
        return {"blocked": True, "model": "".join(denies)}
    return {"blocked": False, "decision": decision, "context": JOIN.join(contexts),
            "system": JOIN.join(messages)}


def comparable(row):
    return {k: row.get(k) for k in ROW_FIELDS}


def compare(label, island, members, tool_input, env_tmp, tag):
    """Fire members solo and as one chain; return (solo results, chain result, rows)."""
    solo_tel = os.path.join(env_tmp, f"solo-{tag}.jsonl")
    chain_tel = os.path.join(env_tmp, f"chain-{tag}.jsonl")
    solo_env, chain_env = env_for(env_tmp, solo_tel), env_for(env_tmp, chain_tel)
    solo_id, chain_id = f"toolu_solo_{tag}", f"toolu_chain_{tag}"
    out_dir = os.path.join(island, "out")
    snapshot = os.path.join(env_tmp, f"out-snapshot-{tag}")
    shutil.copytree(out_dir, snapshot)
    solo = []
    for seconds, gate in members:
        result, _ = fire(island, [gate], payload(island, tool_input, solo_id), solo_env,
                         timeout=seconds)
        solo.append(result)
    # Gates keep state under out/ (burst counters, write snapshots); the chain
    # must see the state the solo run saw, not the state it left behind.
    shutil.rmtree(out_dir)
    shutil.copytree(snapshot, out_dir)
    tokens = [f"{seconds}:{gate}" for seconds, gate in members]
    chained, wall = fire(island, ["--chain"] + tokens, payload(island, tool_input, chain_id),
                         chain_env, timeout=sum(s for s, _ in members) + 5)
    return solo, chained, rows(solo_tel, solo_id), rows(chain_tel, chain_id), wall


def assert_parity(label, solo, chained, solo_rows, chain_rows, strict_rows=True):
    live = [r for r in solo if r[0] is not None]
    check(f"{label}: harness view matches", harness_view(live) == harness_view([chained]),
          f"solo={harness_view(live)} chain={harness_view([chained])}")
    denies = [r for r in live if r[0] == 2]
    json_denies = [r for r in live if r[0] == 0 and harness_view([r])["blocked"]]
    speakers = [r for r in live if r[0] == 0 and r[1].strip()]
    if len(denies) == 1 and not json_denies:
        check(f"{label}: the one deny's stderr is byte-identical",
              chained[0] == 2 and chained[2] == denies[0][2], repr(chained))
    if not denies and len(speakers) == 1:
        check(f"{label}: the one speaker's stdout is byte-identical",
              chained[0] == 0 and chained[1] == speakers[0][1], repr(chained))
    if strict_rows:
        check(f"{label}: one telemetry row per gate, same fields and values",
              [comparable(r) | {"tool_use_id": None} for r in solo_rows]
              == [comparable(r) | {"tool_use_id": None} for r in chain_rows],
              f"\n solo={[comparable(r) for r in solo_rows]}\n chain={[comparable(r) for r in chain_rows]}")
        check(f"{label}: chain rows share one process and carry their position",
              len({r.get("pid") for r in chain_rows}) <= 1
              and [r.get("chain_pos") for r in chain_rows] == list(range(len(chain_rows)))
              and all(set(s) <= set(c) for s, c in zip(solo_rows, chain_rows)),
              chain_rows)


# ── 1. synthetic merge rules ─────────────────────────────────────────────────
def synthetic(tmp, mutate=None, quiet=False):
    """Run section 1; return the number of failed checks (for the mutant pass)."""
    global failures
    saved, failures = failures, []
    QUIET[0] = quiet
    island = build_island(tmp, mutate)
    g = os.path.join(island, "fixture-gates")
    for index, (label, members) in enumerate(SYNTHETIC):
        members = [(t, os.path.join(g, name)) for t, name in members]
        solo, chained, srows, crows, _ = compare(label, island, members,
                                                 {"command": "echo hi"}, tmp, f"s{index}")
        assert_parity(label, solo, chained, srows, crows)

    # Declared order, and every member runs even after a deny.
    order_log = os.path.join(tmp, "order.log")
    for members, want, label in (
            ([(10, "order_a.py"), (10, "order_b.py")], "a\nb\n", "members run in declared order"),
            ([(10, "order_b.py"), (10, "order_a.py")], "b\na\n", "order follows the declaration, not the name"),
            ([(10, "deny_x.py"), (10, "order_a.py")], "a\n", "a member after a deny still runs")):
        if os.path.exists(order_log):
            os.remove(order_log)
        tel = os.path.join(tmp, "order.jsonl")
        fire(island, ["--chain"] + [f"{t}:{os.path.join(g, n)}" for t, n in members],
             payload(island, {"command": "echo hi"}, "toolu_order"), env_for(tmp, tel))
        got = open(order_log).read() if os.path.exists(order_log) else ""
        check(label, got == want, repr(got))

    # A member that overruns its own timeout fails open the way a killed
    # process does, and does not take the deny beside it down with it.
    tel = os.path.join(tmp, "timeout.jsonl")
    (code, out, err), wall = fire(
        island, ["--chain", f"1:{os.path.join(g, 'slow.py')}", f"10:{os.path.join(g, 'deny_y.py')}"],
        payload(island, {"command": "echo hi"}, "toolu_timeout"), env_for(tmp, tel), timeout=20)
    trows = rows(tel, "toolu_timeout")
    check("a hung member is cancelled at ITS timeout, not the chain's", wall < 4.5, f"{wall:.1f}s")
    check("the deny after a hung member still denies", code == 2 and "fixture Y" in err
          and "too late" not in out, (code, out, err))
    check("the hung member's row says it timed out and errored",
          len(trows) == 2 and trows[0]["outcome"] == "error" and trows[0]["exit"] == 124
          and "timeout" in (trows[0].get("error_tail") or ""), trows)
    (code, out, err), wall = fire(
        island, ["--chain", f"1:{os.path.join(g, 'slow.py')}", f"10:{os.path.join(g, 'allow.py')}"],
        payload(island, {"command": "echo hi"}, "toolu_timeout2"), env_for(tmp, tel), timeout=20)
    check("a hung member alone fails open (non-blocking exit)", code == 1 and wall < 4.5,
          (code, err, wall))

    # Malformed chain arguments are a non-blocking error, never a deny.
    (code, _, err), _ = fire(island, ["--chain", "nonsense"], payload(island, {}, "toolu_bad"),
                             env_for(tmp, os.path.join(tmp, "bad.jsonl")))
    check("a malformed chain is a non-blocking error", code == 1 and "SECONDS:PATH" in err, err)
    failed, failures = failures, saved
    QUIET[0] = False
    if not quiet:
        failures.extend(failed)
    return failed


# ── 2. the real wired Bash chain ─────────────────────────────────────────────
def wired_bash_chain():
    groups = gate_declarations.render_hooks()["PreToolUse"]
    hits = [h["command"] for grp in groups if grp.get("matcher") == "Bash"
            for h in grp["hooks"] if " --chain " in h["command"]]
    check("PreToolUse Bash is wired as exactly one chain", len(hits) == 1, hits)
    if not hits:
        return []
    tokens = hits[0].split(" --chain ", 1)[1].split()
    return [(int(t.split(":", 1)[0]), t.split(":", 1)[1]) for t in tokens]


def scenario(record_id, gate):
    for line in open(os.path.join(FIXTURES, "gate-scenarios.jsonl"), encoding="utf-8"):
        record = json.loads(line)
        if record["id"] == record_id and record["gate"] == gate:
            return record
    raise KeyError(record_id)


def real_chain(tmp):
    members = wired_bash_chain()
    if not members:
        return
    island = build_island(tmp)
    members = [(t, path.replace("{{REPO}}", island)) for t, path in members]
    declared = [os.path.basename(p) for _, p in members]
    decl = json.load(open(os.path.join(REPO, "ops/config/gate-declarations.json")))["hooks"]
    want = sorted(
        ((ex["order"], name) for name, entry in decl.items() for w in entry.get("wirings", [])
         for ex in w.get("executions", []) if ex.get("chain") == "pretool-bash"),
    )
    check("the chain runs the declared members in declared order",
          declared == [name for _, name in want], (declared, want))

    seed = scenario("a1000000002b", "draft-export-gate.py")["out_files"]
    for rel, content in seed.items():
        target = os.path.join(island, "out", rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        open(target, "w", encoding="utf-8").write(content)

    def cmd(record_id, gate):
        return scenario(record_id, gate)["tool_input"]["command"].replace("{{REPO}}", island)

    real = [json.loads(line)["tool_input"]["command"]
            for line in open(os.path.join(FIXTURES, "bash-commands.jsonl"), encoding="utf-8")][:4]
    cases = [(f"real command {i}", c) for i, c in enumerate(real)] + [
        ("bash-write deny (redirect)", cmd("5c0000000003", "bash-write-gate.py")),
        ("bash-write deny (tee)", cmd("5c0000000004", "bash-write-gate.py")),
        ("burst deny (xargs)", cmd("a10000000b01", "github-burst-guard.py")),
        ("burst deny (for loop)", cmd("a10000000b02", "github-burst-guard.py")),
        ("draft-export announce", cmd("a1000000002b", "draft-export-gate.py")),
        ("two gates deny at once", cmd("a10000000b01", "github-burst-guard.py")
         + " > out/delegation-gate-state.json"),
        ("announce beside a deny", cmd("a1000000002b", "draft-export-gate.py")
         + " && " + cmd("a10000000b02", "github-burst-guard.py")),
        ("two announcements merge", cmd("a1000000002b", "draft-export-gate.py")
         + " && cat out/jev-calls.jsonl > out/jev-calls.jsonl"),
    ]
    # Every case starts from the same seeded out/: draft-export-gate announces
    # once per session and the burst guard counts calls, so a case must not
    # inherit the state an earlier case left behind.
    pristine = os.path.join(tmp, "out-pristine")
    shutil.copytree(os.path.join(island, "out"), pristine)
    seen = {"blocked": 0, "context": 0, "multi": 0}
    for index, (label, command) in enumerate(cases):
        shutil.rmtree(os.path.join(island, "out"))
        shutil.copytree(pristine, os.path.join(island, "out"))
        solo, chained, srows, crows, _ = compare(
            label, island, members, {"command": command, "description": "chain parity"},
            tmp, f"r{index}")
        assert_parity(label, solo, chained, srows, crows)
        view = harness_view([r for r in solo if r[0] is not None])
        seen["blocked"] += view["blocked"]
        seen["context"] += bool(view.get("context"))
        seen["multi"] += sum(1 for r in solo if r[0] == 2) > 1 or (
            not view["blocked"] and sum(1 for r in solo if r[0] == 0 and r[1].strip()) > 1)
    # The scenarios must actually reach the paths they are named for, or the
    # parity above is parity between two allows.
    check("real cases reached deny, announce and multi-gate paths",
          seen["blocked"] >= 5 and seen["context"] >= 1 and seen["multi"] >= 2, seen)


# ── 3. mutants ───────────────────────────────────────────────────────────────
MUTANTS = [
    ("a dropped deny",
     ("                parts.append(m.err)\n",
      "                parts.append(m.err if not parts else '')\n")),
    ("every deny dropped",
     ("    if denies:\n        parts = []", "    if False:\n        parts = []")),
    ("reversed member order",
     ("    for timeout, target in specs:", "    for timeout, target in reversed(specs):")),
    ("no environment restore",
     ("        if os.environ != saved_env:", "        if False:")),
    ("no per-member timeout",
     ("signal.setitimer(signal.ITIMER_REAL, timeout)", "signal.setitimer(signal.ITIMER_REAL, 0)")),
    ("contexts merged out of order",
     ('specific["additionalContext"] = CHAIN_JOIN.join(contexts)',
      'specific["additionalContext"] = CHAIN_JOIN.join(reversed(contexts))')),
]


def mutants(tmp):
    for index, (label, mutation) in enumerate(MUTANTS):
        sub = os.path.join(tmp, f"mutant-{index}")
        os.makedirs(sub)
        failed = synthetic(sub, mutate=mutation, quiet=True)
        check(f"mutant killed: {label}", bool(failed), "every check passed against the mutant")
        if failed:
            print(f"         (caught by: {failed[0]})")


# ── admission (lib/gate_declarations.py) ─────────────────────────────────────
def admission(tmp):
    hazards = gate_declarations.chain_hazards
    check("hazard scan flags os._exit, signals, threads, chdir, bare except",
          all(hazards(src) for src in ("os._exit(0)", "signal.alarm(3)", "import threading",
                                        "os.chdir('/')", "try:\n  x()\nexcept:\n  pass",
                                        "except BaseException:", "import atexit")))
    check("hazard scan passes an ordinary gate", not hazards(GATES["deny_x.py"]))
    for name in ("settings-change-gate.py", "bash-write-gate.py", "write-effect-check.py",
                 "draft-export-gate.py", "github-burst-guard.py", "staging-observation-tracker.py"):
        path = os.path.join(REPO, "hooks", name)
        if os.path.exists(path):
            check(f"wired member {name} is admissible", not hazards(open(path).read()))

    real = json.load(open(os.path.join(REPO, "ops/config/gate-declarations.json")))

    def variant(edit):
        data = json.loads(json.dumps(real))
        edit(data)
        path = os.path.join(tmp, "decl.json")
        json.dump(data, open(path, "w"))
        return gate_declarations.validate(path, repo=REPO)

    def executions(data, name):
        return [ex for w in data["hooks"][name].get("wirings", [])
                for ex in w.get("executions", []) if ex.get("chain")]

    check("the real declarations validate", gate_declarations.validate(repo=REPO) == [],
          gate_declarations.validate(repo=REPO))

    def lone(data):
        for name, entry in data["hooks"].items():
            for ex in executions(data, name):
                if ex["order"] != 0:
                    ex.pop("chain")
    check("a one-member chain is refused",
          any("at least two" in e for e in variant(lone)))

    def gap(data):
        executions(data, "bash-write-gate.py")[0].pop("chain")
    check("a chain with a gap in its orders is refused",
          any("consecutive" in e for e in variant(gap)))

    def wrapped(data):
        ex = executions(data, "bash-write-gate.py")[0]
        ex["command"] = "CARR_X=1 " + ex["command"]
    check("a member with an env prefix is refused",
          any("plain solo command" in e for e in variant(wrapped)))

    rendered = gate_declarations.chain_command([
        {"command": gate_declarations.SOLO_PREFIX + "{{REPO}}/hooks/a.py", "timeout": 7},
        {"command": gate_declarations.SOLO_PREFIX + "{{REPO}}/hooks/b.py"}])
    check("chain_command renders T:PATH tokens and the summed budget",
          rendered["command"].endswith("--chain 7:{{REPO}}/hooks/a.py 60:{{REPO}}/hooks/b.py")
          and rendered["timeout"] == 7 + 60 + gate_declarations.CHAIN_SLACK_SECONDS, rendered)


def main():
    print("hook-chain-selftest")
    with tempfile.TemporaryDirectory(prefix="hook-chain-") as tmp:
        for name, fn in (("admission", admission), ("synthetic", synthetic),
                         ("real", real_chain), ("mutants", mutants)):
            sub = os.path.join(tmp, name)
            os.makedirs(sub)
            print(f"-- {name}")
            fn(sub)
    if failures:
        print(f"hook-chain-selftest: {len(failures)} FAILED")
        return 1
    print("hook-chain-selftest: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
