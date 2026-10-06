#!/usr/bin/env python3
"""Behavioral boundary checks for normal runtime Drive retirement.

Each normal path is run with a poisoned CARR_VAULT.  Success means it either
uses the repository/record contract or ignores that ambient value; none may
discover or open the poisoned tree.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
POISON = "/definitely-not-a-carr-source"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    failures: list[str] = []
    checks = 0

    def check(name: str, value: bool) -> None:
        nonlocal checks
        checks += 1
        print(f"  {'ok  ' if value else 'FAIL'} {name}")
        if not value:
            failures.append(name)

    env = {**os.environ, "CARR_VAULT": POISON}
    sync = subprocess.run([str(REPO / "bin" / "sync-settings.sh")], text=True,
                          capture_output=True, env=env)
    check("settings normal mode ignores poisoned ambient root",
          sync.returncode == 0 and "repository config is canonical" in sync.stdout
          and POISON not in sync.stdout + sync.stderr)

    control = load("drive_runtime_control", REPO / "tools" / "control-plane.py")
    captured: list[dict[str, str]] = []
    original_run = control.subprocess.run
    original_env = os.environ.copy()
    os.environ["CARR_VAULT"] = POISON
    try:
        def fake_run(_argv, **kwargs):
            captured.append(kwargs["env"])
            return subprocess.CompletedProcess([], 0, "", "")
        control.subprocess.run = fake_run
        control._execute_deterministic(
            {"execution": {"entrypoint": "bin/nightly.sh", "args": [], "shadow_args": []}},
            {}, 30, "shadow")
    finally:
        control.subprocess.run = original_run
        os.environ.clear()
        os.environ.update(original_env)
    check("deterministic children exclude poisoned ambient root",
          bool(captured) and "CARR_VAULT" not in captured[0])

    # EXECUTOR-TIER vs A POISONED SYNCED DEFINITION (rewritten 2026-09-24).
    # This used to assert "deny" as a proxy for "the gate did not read the
    # ambient definition". Since #1228 (ruling 5ec806a4) a no-model spawn is
    # ALLOWED with Jev's tier filled in whenever Jev is confident, so the proxy
    # failed on any machine where Jev was reachable. The property itself is
    # tested now: a poisoned root that really contains a synced definition
    # pinning a model, at every place a synced definition used to be read from
    # (under CARR_VAULT and under HOME's Drive mirror), and the gate's decision
    # and model must never come from it. Jev is pinned through the gate's own
    # CARR_EXECUTOR_TIER_JEV_STUB seam so both branches are exercised whether or
    # not the live judge is reachable, and no network call is made.
    poisoned_model = "fable"   # deliberately not the tier the stub picks
    poisoned_def = f"---\nname: poisoned-agent\nmodel: {poisoned_model}\n---\nPoisoned.\n"
    with tempfile.TemporaryDirectory(prefix="poisoned-agent-root-") as ptmp:
        proot = Path(ptmp)
        vault, home = proot / "vault", proot / "home"
        poison_dirs = [vault / ".claude" / "agents",
                       vault / "CARR AI" / ".claude" / "agents",
                       home / "My Drive" / ".claude" / "agents",
                       home / "My Drive" / "CARR AI" / ".claude" / "agents",
                       home / ".claude" / "agents"]
        for d in poison_dirs:
            d.mkdir(parents=True, exist_ok=True)
            (d / "poisoned-agent.md").write_text(poisoned_def)
        poison_marks = (str(vault), str(home / "My Drive"), str(home / ".claude"),
                        "poisoned-agent.md")
        payload = json.dumps({"tool_name": "Agent", "tool_input": {
            "subagent_type": "poisoned-agent", "description": "d", "prompt": "p"}})
        base_env = {k: v for k, v in os.environ.items()
                    if k not in ("CARR_EXECUTOR_TIER_JEV_STUB", "CARR_HOOK_FIXTURE")}
        base_env.update({"CARR_VAULT": str(vault), "HOME": str(home)})

        def run_gate(stub: str, hook: Path = REPO / "hooks" / "executor-tier-gate.py"):
            proc = subprocess.run([sys.executable, str(hook)], input=payload, text=True,
                                  capture_output=True,
                                  env={**base_env, "CARR_EXECUTOR_TIER_JEV_STUB": stub})
            try:
                hso = json.loads(proc.stdout.strip().splitlines()[-1]).get("hookSpecificOutput", {})
            except (ValueError, IndexError):
                hso = {}
            return proc, hso

        def clean(proc) -> bool:
            text = proc.stdout + proc.stderr
            return not any(mark in text for mark in poison_marks)

        # Positive control: the SAME definition, placed where the gate is meant
        # to read (a repository's claude-tree/agents), does pin the tier. Without
        # this, a malformed poison would make the two checks below pass vacuously.
        control_repo = proot / "control-repo"
        (control_repo / "hooks").mkdir(parents=True)
        (control_repo / "claude-tree" / "agents").mkdir(parents=True)
        shutil.copy2(REPO / "hooks" / "executor-tier-gate.py",
                     control_repo / "hooks" / "executor-tier-gate.py")
        (control_repo / "claude-tree" / "agents" / "poisoned-agent.md").write_text(poisoned_def)
        control, control_hso = run_gate("none", control_repo / "hooks" / "executor-tier-gate.py")
        check("executor fixture: the poisoned definition is a real pin where the gate does read",
              control.returncode == 0 and not control.stdout.strip() and not control_hso)

        # Jev unavailable: the poisoned pin would have allowed silently; the
        # gate must deny as for any undefined type.
        unavailable, un_hso = run_gate("none")
        check("executor ignores a poisoned synced agent definition (Jev unavailable: deny)",
              unavailable.returncode == 0 and un_hso.get("permissionDecision") == "deny"
              and "updatedInput" not in un_hso and clean(unavailable))

        # Jev confident: the model comes from Jev, never from the poisoned pin.
        confident, co_hso = run_gate("sonnet:0.95")
        co_model = (co_hso.get("updatedInput") or {}).get("model")
        check("executor ignores a poisoned synced agent definition (Jev confident: Jev's tier)",
              confident.returncode == 0 and co_hso.get("permissionDecision") == "allow"
              and co_model == "sonnet" and co_model != poisoned_model and clean(confident))

    sources = load("drive_runtime_sources", REPO / "lib" / "record_sources.py")
    check("record identities require a declared root, not an ambient mount",
          sources._strip_source_root("/repo/DNA/example.md", "/repo") == "DNA/example.md"
          and sources._strip_source_root(POISON + "/DNA/example.md", "/repo")
          == POISON + "/DNA/example.md")

    policy_text = (REPO / "hooks" / "drift-claim-gate.py").read_text()
    check("drift policy has no ambient Drive-root reader", "CARR_VAULT" not in policy_text)

    # The installed command deliberately routes to the repository interpreter.
    # The fixture below invokes the launcher from /usr/bin/python3 and returns a
    # nonempty canonical v_decision_entry row; no direct-gate monkeypatches are
    # accepted as evidence for this boundary.
    hooks_config = (REPO / "ops" / "config" / "hooks.json").read_text()
    # Both drift entries now run through hooks/hook-meter-run.py (2026-08-23,
    # hook telemetry). What this check protects is unchanged and is NOT the
    # wrapper: it is that the bootstrap is the FIXED, absolute /usr/bin/python3
    # rather than a PATH-resolved `env python3`, because PATH is attacker-
    # influenced and this pair of gates is what records drift. The wrapper
    # inherits that interpreter and runs the gate in it, so the property holds;
    # only the literal moved. The `env python3` clause is asserted against BOTH
    # spellings so the wrapper cannot become a way to reintroduce it.
    check("installed drift hooks use fixed system bootstrap and repository interpreter",
          hooks_config.count("/usr/bin/python3 {{REPO}}/hooks/hook-meter-run.py "
                             "{{REPO}}/hooks/run-record-gate.py drift-") == 2
          and "/usr/bin/env python3 {{REPO}}/hooks/run-record-gate.py" not in hooks_config
          and ("/usr/bin/env python3 {{REPO}}/hooks/hook-meter-run.py "
               "{{REPO}}/hooks/run-record-gate.py") not in hooks_config)

    # PRIVATE TMP, NOT THE REPO ROOT (2026-08-23 load-flake sweep): was dir=REPO.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for directory in (root / "hooks", root / "lib", root / ".venv" / "bin"):
            directory.mkdir(parents=True, exist_ok=True)
        # stop_latch.py joined this list 2026-08-23, and the reason is worth a
        # line: this fixture mirrors hooks/ BY HAND, so a gate that gains an
        # import gains it here too or the launcher crashes and the check reads
        # as a bootstrap failure. drift-assertion-gate.py now latches on the
        # RULINGS it matched rather than on a hash of the prose (Joe's
        # Stop-gate rationing), and that latch lives in the shared module.
        for name in ("run-record-gate.py", "drift-claim-gate.py",
                     "drift-assertion-gate.py", "stop_latch.py"):
            shutil.copy2(REPO / "hooks" / name, root / "hooks" / name)
        (root / "hooks" / "chat-lint-gate.py").write_text('''
import json
def read_tail(path):
    with open(path) as fh: return [json.loads(line) for line in fh if line.strip()]
def text_of(record, _roles):
    content = record.get("message", {}).get("content", [])
    return "\\n".join(item.get("text", "") for item in content if isinstance(item, dict))
def strip_fences(text): return text
''')
        (root / "lib" / "__init__.py").write_text("")
        (root / "lib" / "record_sources.py").write_text('''
import os
if os.environ.get("PYTHONPATH"):
    raise RuntimeError("launcher failed to scrub PYTHONPATH")
if os.environ.get("FIXTURE_FIXED_INTERPRETER") != "1":
    raise RuntimeError("launcher did not select the fixed interpreter")
class Cursor:
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, *_): pass
    def fetchall(self):
        return [("2026-08-20", "The quokka-indexer lane stays disabled", "Joe approved it", "the cost exceeded value", "chosen state")]
class Connection:
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def cursor(self): return Cursor()
def _connect(): return Connection()
''')
        interpreter = root / ".venv" / "bin" / "python"
        # Any `-c` invocation is a readiness probe. The launcher must no longer
        # make one, so the fixture records every probe and the check below
        # asserts the marker was never written.
        probe_marker = root / "probe-ran"
        interpreter.write_text(f'''#!/bin/sh
if [ "$1" = -c ]; then echo probe >> "{probe_marker}"; exit 0; fi
export FIXTURE_FIXED_INTERPRETER=1
exec /usr/bin/python3 "$@"
''')
        interpreter.chmod(0o755)
        record_env = {**env, "PYTHONPATH": str(root / "poisoned-pythonpath"), "HOME": str(root)}
        claim = ("The quokka-indexer lane is no longer running. It was supposed to fire nightly "
                 "and the schedule silently reverted, so the index is stale and nothing has been re-pointed.")
        write = subprocess.run(["/usr/bin/python3", str(root / "hooks" / "run-record-gate.py"), "drift-claim-gate.py"],
                               input=json.dumps({"tool_name": "mcp__x__record-defect",
                                                 "tool_input": {"body": claim}}),
                               text=True, capture_output=True, env=record_env)
        transcript = root / "transcript.jsonl"
        transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": claim}]}}) + "\n")
        assertion = subprocess.run(["/usr/bin/python3", str(root / "hooks" / "run-record-gate.py"), "drift-assertion-gate.py"],
                                   input=json.dumps({"hook_event_name": "Stop", "transcript_path": str(transcript)}),
                                   text=True, capture_output=True,
                                   env={**record_env, "CARR_DRIFT_ASSERTION_STATE": str(root / "state")})
        unknown = subprocess.run(["/usr/bin/python3", str(root / "hooks" / "run-record-gate.py"), "unknown.py"],
                                 text=True, capture_output=True, env=record_env)
        malformed = subprocess.run(["/usr/bin/python3", str(root / "hooks" / "run-record-gate.py"),
                                    "drift-claim-gate.py", "extra"], text=True, capture_output=True, env=record_env)
        missing_path = root / ".venv" / "bin" / "python-missing"
        interpreter.rename(missing_path)
        try:
            missing_interpreter = subprocess.run(["/usr/bin/python3", str(root / "hooks" / "run-record-gate.py"), "drift-claim-gate.py"],
                                                 text=True, capture_output=True, env=record_env)
        finally:
            missing_path.rename(interpreter)
        probe_ran = probe_marker.exists()
    check("system bootstrap reaches fixed interpreter, scrubs PYTHONPATH, and blocks on canonical context",
          write.returncode == 0 and "quokka-indexer" in write.stdout
          and assertion.returncode == 2 and "quokka-indexer" in assertion.stderr)

    # WHY THIS CHECK CHANGED SHAPE (2026-08-23). It used to require the launcher
    # to exit nonzero on a psycopg readiness probe that failed or timed out. The
    # probe had a 3s budget and cost a second interpreter cold start; measured
    # 4.5-8.4s on this Mac at load average ~330, so under load it timed out
    # EVERY time and the launcher returned 2 — a blocking Stop hook with empty
    # stderr. The drift-assertion check therefore never ran on a busy machine,
    # and the session could neither comply nor diagnose. The probe was never the
    # enforcement: both gates already catch a failed record read and log
    # ALLOW(internal-error). So the probe is gone, and the two cases that only
    # existed to drive it are replaced by the two properties that actually
    # matter — no probe is made at all, and no exit is ever silent.
    check("launcher makes no readiness probe (the 3s cliff cannot come back)",
          not probe_ran)
    check("launcher bails loudly, and never blocking, on argv and interpreter faults",
          all(item.returncode == 1 and "bootstrap-fault" in item.stderr
              for item in (unknown, malformed, missing_interpreter)))

    if failures:
        print(f"FAIL {len(failures)}: {', '.join(failures)}")
        return 1
    print(f"drive runtime hooks selftest: {checks}/{checks} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
