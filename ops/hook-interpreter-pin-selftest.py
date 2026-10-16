#!/usr/bin/env python3
"""hook-interpreter-pin-selftest.py — every wired hook names the repo interpreter.

THE FAILURE THIS EXISTS BECAUSE OF (2026-09-23). Every hook command in
ops/config/hooks.json began `/usr/bin/env python3`, and the desktop app hands
its hooks a PATH with no Homebrew entry, so that resolved to Apple's
/usr/bin/python3 — Python 3.9. Two gates are written against the 3.14 venv:
hooks/rule-pack-preuse-reselection.py imports a typing name that exists from
3.10, and hooks/settings-change-gate.py evaluates a 3.10 union in a signature
at import. Both died at import on EVERY call: 823 and 1,283 error rows in one
day's telemetry across six sessions, and nobody saw it, because a crashing hook
fails open and the telemetry keeps one line of the traceback. The effect was
that Jev's semantic rule delivery and build advisory, merged 2026-09-20, never
reached a session that day — while a terminal-launched session, whose PATH does
carry Homebrew, ran the same hooks on 3.14 and saw nothing wrong.

THE CONTRACT NOW. A hook command names `{{REPO}}/.venv/bin/python` explicitly.
PATH never chooses the interpreter, which is the same rule
hooks/run-record-gate.py already applies to the record-backed gates. The venv
is the interpreter every other part of this repo already requires (run.sh,
nightly, migrate, health), Dell's migration builds and version-checks it, and
bin/worktree.sh links it into every worktree. A tree with no venv therefore
loses its hooks loudly — the harness prints the spawn failure — instead of
running them on whatever python3 happens to be first on PATH and failing
silently at import.

WHY NOT A RE-EXEC INSIDE hooks/hook-meter-run.py. That was built and proven
first, but every hooks/*.py file is a sealed script entrypoint in the SCAC
source inventory, and editing one owes a forward-only registry successor: a
generated registry file, a production migration and a disposable-Postgres
catalog measurement. The config is a gate-integrity CONTRACT (hashed, blessed)
but not an inventoried entrypoint, so pinning the interpreter there costs a
bless and one `config-as-code.py install --apply` per machine, and no seal.

TWO DELIBERATE EXCEPTIONS, both held by other suites and honoured here:
  * The two drift-recording hooks (run-record-gate.py driving drift-claim-gate
    and drift-assertion-gate) bootstrap from the FIXED, absolute
    /usr/bin/python3 and then exec the venv themselves; ops/drive-runtime-
    hooks-selftest.py asserts that literal because PATH is attacker-influenced
    and that pair is what records drift. A fixed absolute path satisfies the
    property this file protects, so it is allowed for exactly that launcher.
  * ops/config/codex-hooks.json: Codex is launched from a terminal whose PATH
    carries Homebrew. It is left as it is and is NOT covered here; moving it is
    a separate decision.

WHAT IS CHECKED, deterministically and without running a hook:
  1. every command in hooks.json that launches a CARR hook or ops script
     starts with `{{REPO}}/.venv/bin/python`, after an optional missing-file
     guard and leading VAR=value assignments, except the launcher above;
  2. no command in hooks.json still says `/usr/bin/env python3`;
  3. on this machine, if the venv interpreter exists it reports 3.10 or newer,
     so the pin points at something that can actually run the gates.
"""

import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = ("ops/config/hooks.json",)
PIN = "{{REPO}}/.venv/bin/python "
ASSIGNMENT = re.compile(r"^(?:[A-Z_][A-Z0-9_]*=\S*\s+)*")
FORBIDDEN = ("/usr/bin/env python3", "/usr/bin/env python ")

failures: list[str] = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        failures.append(name)


def commands(config):
    with open(os.path.join(REPO, config), encoding="utf-8") as fh:
        data = json.load(fh)
    hooks = data.get("hooks", data)
    out = []
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            for hook in group.get("hooks", []):
                if hook.get("type") == "command" and hook.get("command"):
                    out.append((event, hook["command"]))
    return out


def main():
    print("hook-interpreter-pin-selftest")
    total = 0
    for config in CONFIGS:
        rows = commands(config)
        total += len(rows)
        check(f"{config} has wired commands", bool(rows), "none found")
        unpinned = []
        forbidden = []
        for event, command in rows:
            invocation = command.rsplit("; fi; ", 1)[-1]
            body = invocation[ASSIGNMENT.match(invocation).end():]
            if "{{REPO}}/hooks/" in body or "{{REPO}}/ops/" in body:
                if not body.startswith(PIN):
                    unpinned.append(f"{event}: {command}")
            if any(token in command for token in FORBIDDEN):
                forbidden.append(f"{event}: {command}")
        check(f"{config}: every CARR hook command names the venv interpreter",
              not unpinned, "\n      " + "\n      ".join(unpinned[:6]))
        check(f"{config}: no other command lets PATH choose python",
              not forbidden, "\n      " + "\n      ".join(forbidden[:6]))
    venv = os.path.join(REPO, ".venv", "bin", "python")
    if os.access(venv, os.X_OK):
        probe = subprocess.run([venv, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
                               capture_output=True, text=True)
        parts = probe.stdout.split()
        ok = probe.returncode == 0 and len(parts) == 2 and tuple(map(int, parts)) >= (3, 10)
        check("the pinned interpreter is Python 3.10 or newer", ok,
              f"exit {probe.returncode} {probe.stdout.strip()!r} {probe.stderr[:120]}")
    else:
        print("  note no .venv/bin/python in this tree; the pin's target was not probed here")

    print()
    if failures:
        print(f"FAIL {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("OK all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
