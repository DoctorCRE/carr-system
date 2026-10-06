#!/usr/bin/env python3
"""Selftest for the hook-path contract in the tracked .claude/settings.json.

THE FAILURE THIS PINS (2026-09-27). Every hook command in the project settings
named "${HOME}/carr-system/hooks/<x>.py". A Claude Code cloud session clones
the repository somewhere else (it exposes the clone as $CLAUDE_PROJECT_DIR) and
runs with a HOME that holds no carr-system checkout. python3 then exits 2
("can't open file"), and a PreToolUse hook that exits 2 BLOCKS the call, so
every Bash, Read, Grep and Glob call in a cloud session was refused.

THE CONTRACT, per layout:

  Mac layout   (~/carr-system/hooks exists)
      every hook runs from the canonical ~/carr-system path, never from the
      project directory. A branch or worktree cannot swap in its own gate.

  cloud layout (~/carr-system/hooks absent)
      delegation-gate.py runs from $CLAUDE_PROJECT_DIR. It only observes and
      never denies, so the repository copy is safe to run.
      worktree-self-plumb.py and machine-converge.py do nothing: both act on
      machine state (worktree symlinks, the orphan reaper, a fast-forward of
      the canonical checkout, config-as-code install) that a container lacks.
      A missing script or an unset $CLAUDE_PROJECT_DIR allows the call.

Each command is run exactly as written in the tracked settings file, under
every POSIX shell present, against throwaway HOME and project directories.
Nothing here reads the real HOME.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SETTINGS = REPO / ".claude" / "settings.json"
CONTRACT = REPO / "ops" / "config" / "delegation-gate-hook.json"
SHELLS = [s for s in ("/bin/sh", "/bin/bash", "/bin/zsh") if os.access(s, os.X_OK)]
GATE = "delegation-gate.py"
MACHINE_HOOKS = ("worktree-self-plumb.py", "machine-converge.py")

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def wired_commands() -> list[tuple[str, str, str]]:
    """(event, script, command) for every hook in the tracked project settings."""
    hooks = json.loads(SETTINGS.read_text(encoding="utf-8"))["hooks"]
    out = []
    for event, groups in hooks.items():
        for group in groups:
            for hook in group.get("hooks", []):
                command = hook["command"]
                script = next((s for s in (GATE, *MACHINE_HOOKS) if s in command), "")
                out.append((event, script, command))
    return out


def clean_env(home: Path, project: Path | None) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE_", "DELEGATION_GATE_", "CARR_"))}
    env["HOME"] = str(home)
    if project is not None:
        env["CLAUDE_PROJECT_DIR"] = str(project)
    return env


def run(shell: str, command: str, env: dict, payload: dict, cwd: Path):
    return subprocess.run([shell, "-c", command], input=json.dumps(payload), env=env,
                          cwd=str(cwd), capture_output=True, text=True, timeout=60)


def payloads(project: Path, transcript: Path) -> dict[str, list[dict]]:
    base = {"session_id": "cloud-hook-paths-selftest", "cwd": str(project),
            "transcript_path": str(transcript)}
    return {
        "PreToolUse": [dict(base, hook_event_name="PreToolUse", tool_name=tool, tool_input=inp)
                       for tool, inp in (
                           ("Bash", {"command": "grep -rn foo ."}),
                           ("Read", {"file_path": str(project / "AGENTS.md")}),
                           ("Grep", {"pattern": "foo"}),
                           ("Glob", {"pattern": "**/*.py"}))],
        "Stop": [dict(base, hook_event_name="Stop")],
        "SessionStart": [dict(base, hook_event_name="SessionStart", source="startup")],
    }


def blocked(result) -> bool:
    text = result.stdout
    return (result.returncode == 2 or '"block"' in text or '"deny"' in text)


def cloud_project(tmp: Path) -> Path:
    """A copy of the repository files a cloud clone would carry for these hooks."""
    project = tmp / "repo"
    shutil.copytree(REPO / "hooks", project / "hooks",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (project / "ops").mkdir(parents=True)
    shutil.copy2(REPO / "ops" / "command_precheck.py", project / "ops" / "command_precheck.py")
    (project / ".claude").mkdir()
    shutil.copy2(SETTINGS, project / ".claude" / "settings.json")
    shutil.copy2(REPO / "AGENTS.md", project / "AGENTS.md")
    return project


def transcript_for(tmp: Path) -> Path:
    """A real transcript so delegation-gate walks its full classification path."""
    path = tmp / "transcript.jsonl"
    rows = [{"type": "user", "message": {"role": "user", "content": "delegate the sweep"}}]
    rows += [{"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Grep", "input": {"pattern": f"p{i}"}}]}} for i in range(3)]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def cloud_cases() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        home = tmp / "root"
        home.mkdir()
        project = cloud_project(tmp)
        transcript = transcript_for(tmp)
        env = clean_env(home, project)
        cases = payloads(project, transcript)
        for shell in SHELLS:
            for event, script, command in wired_commands():
                for payload in cases.get(event, []):
                    label = f"cloud/{os.path.basename(shell)}: {event} {script} {payload.get('tool_name', '')}".rstrip()
                    r = run(shell, command, env, payload, project)
                    check(f"{label} allows the call", r.returncode == 0 and not blocked(r),
                          f"rc={r.returncode} stderr={r.stderr.strip()[-200:]!r}")
                    if script in MACHINE_HOOKS:
                        check(f"{label} is a no-op", r.stdout == "" and r.stderr == "",
                              f"stdout={r.stdout[:120]!r} stderr={r.stderr[:120]!r}")
        # The machine hooks must not have written anything under the fake HOME:
        # no ~/.claude, no ~/.codex, no ~/.config/carr, no canonical checkout.
        check("cloud: HOME is untouched by every hook", sorted(os.listdir(home)) == [],
              str(sorted(os.listdir(home))))
        # delegation-gate still RAN from the project copy: its state ledger
        # landed under the project's out/, not under HOME.
        check("cloud: delegation-gate ran from $CLAUDE_PROJECT_DIR",
              (project / "out" / "delegation-gate-state.json").is_file(),
              str(sorted(p.name for p in project.iterdir())))

        # $CLAUDE_PROJECT_DIR unset, and set to a directory with no hooks at all.
        bare = tmp / "bare"
        bare.mkdir()
        for label, env2 in (("unset", clean_env(home, None)), ("no hooks", clean_env(home, bare))):
            for shell in SHELLS:
                for event, script, command in wired_commands():
                    payload = cases[event][0]
                    r = run(shell, command, env2, payload, bare)
                    check(f"cloud/{os.path.basename(shell)} project dir {label}: {event} {script} allows",
                          r.returncode == 0 and not blocked(r),
                          f"rc={r.returncode} stderr={r.stderr.strip()[-200:]!r}")


STUB = """#!/usr/bin/env python3
import sys
sys.stdin.read()
print("{marker}:{name}")
sys.exit({rc})
"""


def mac_cases() -> None:
    """The canonical path wins over $CLAUDE_PROJECT_DIR whenever it exists.

    The project copy of each script is a hostile stand-in that would BLOCK
    (exit 2). If any command ever preferred the project directory while the
    canonical checkout exists, a branch could swap in its own gate.
    """
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        home = tmp / "home"
        canonical = home / "carr-system" / "hooks"
        project = tmp / "worktree"
        (project / "hooks").mkdir(parents=True)
        canonical.mkdir(parents=True)
        for name in (GATE, *MACHINE_HOOKS):
            (canonical / name).write_text(STUB.format(marker="CANONICAL", name=name, rc=0))
            (project / "hooks" / name).write_text(STUB.format(marker="PROJECT", name=name, rc=2))
        env = clean_env(home, project)
        cases = payloads(project, tmp / "absent.jsonl")
        for shell in SHELLS:
            for event, script, command in wired_commands():
                payload = cases[event][0]
                r = run(shell, command, env, payload, project)
                label = f"mac/{os.path.basename(shell)}: {event} {script}"
                check(f"{label} runs the canonical copy",
                      r.returncode == 0 and f"CANONICAL:{script}" in r.stdout
                      and "PROJECT:" not in r.stdout,
                      f"rc={r.returncode} stdout={r.stdout.strip()!r}")

        # Unchanged Mac behaviour: a canonical checkout whose gate FILE is
        # missing is damage for gate-integrity to report, not absent machine
        # state, so the command must not quietly fall back to the project copy.
        (canonical / GATE).unlink()
        command = next(c for e, s, c in wired_commands() if s == GATE)
        r = run(SHELLS[0], command, env, cases["PreToolUse"][0], project)
        check("mac: a missing canonical gate never falls back to the project copy",
              "PROJECT:" not in r.stdout, f"stdout={r.stdout.strip()!r}")


def contract_cases() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    commands = {(e, s): c for e, s, c in wired_commands()}
    check("every wired hook names one of the three known scripts",
          all(s for (_e, s) in commands), str(list(commands)))
    pre, stop = commands.get(("PreToolUse", GATE)), commands.get(("Stop", GATE))
    check("PreToolUse and Stop run the identical delegation-gate command", pre == stop)
    check("the tracked contract names the settings' delegation command", contract["command"] == pre)
    check("the contract command falls back to $CLAUDE_PROJECT_DIR",
          "${CLAUDE_PROJECT_DIR" in contract["command"])


def main() -> int:
    if not SHELLS:
        print("no POSIX shell found")
        return 78
    contract_cases()
    cloud_cases()
    mac_cases()
    print(f"\n{'FAIL' if FAILURES else 'PASS'}: {len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
