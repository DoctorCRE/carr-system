#!/usr/bin/env python3
"""
cmd-text-selftest.py — fixtures for hooks/cmd_text.py, the shared "tell a
command apart from the prose it carries" helper, written before its next
change (rule e65efc68).

WHY IT MATTERS THAT THIS FILE EXISTS AT ALL. cmd_text.py decides what two
gates are allowed to STOP looking at. Until now it had no direct fixtures —
only indirect coverage through each gate's own suite, which tests the gates'
verdicts rather than the boundary itself. A helper whose whole job is drawing
a safety boundary needs its own test, because both callers can keep passing
while the boundary moves underneath them.

THE CASE THAT DROVE THE CHANGE, 2026-08-14. The helper already treats a
heredoc body and a quoted `-m` message as data. It did not treat the other
flags that carry prose, so opening a pull request whose body DESCRIBED a
dangerous git command was refused as though it were running one:

    gh pr create --title "..." --body "...git stash drop <ref>..."

That is the same category error the module was written to end, arriving
through a flag nobody had listed. The workaround was to move the body into a
file — which is the outcome the module's own docstring warns about, because it
teaches whoever hits it that the gate is noise to route around.

THE RULE THIS FILE ENFORCES ON THE HELPER: it may only ever stop looking at
text the shell hands to a program as BYTES. Concretely, that means quoted
arguments to flags that carry prose. It must NOT stop looking at:
  · an unquoted flag argument (one shell word, cannot hide a command anyway,
    and matching it loosely would swallow real text after it);
  · anything inside `bash -c "..."`, `sh -c '...'` or a command substitution,
    which ARE executed;
  · a flag that merely starts with the same letters, like --body-file, whose
    argument is a path rather than prose.

Every ambiguity resolves toward scanning MORE, which is what the module means
by failing closed.

RUNNING IT. No database, no network, no production access:

    .venv/bin/python ops/cmd-text-selftest.py
"""

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODULE = REPO / "hooks" / "cmd_text.py"

passed = 0
failures: list[str] = []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def load():
    spec = importlib.util.spec_from_file_location("cmd_text", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = load()
strip = mod.strip_inert_text

# A command string is "carried" if a dangerous fragment SURVIVES stripping,
# meaning the gate will still scan and refuse it.
def survives(cmd, fragment):
    return fragment in strip(cmd)


print("\nhooks/cmd_text.py — a command is not the prose it carries")

print("\n  --- prose the shell never executes must become invisible ---")

# The 2026-08-14 case, verbatim in shape.
case = ('gh pr create --title "stash classifier fix" '
        '--body "the gate refused git stash drop stash@{0} on a dirty tree"')
check("a --body describing a command is not scanned",
      not survives(case, "git stash drop"), strip(case))
check("and its --title is not scanned either",
      "stash classifier fix" not in strip(case), strip(case))

for flag in ("--body", "--comment", "--notes", "--description", "--message"):
    cmd = f'gh issue comment 12 {flag} "never run git clean -fd here"'
    check(f"{flag} carrying prose is not scanned",
          not survives(cmd, "git clean -fd"), strip(cmd))

single = "gh pr create --body 'git add -A is refused by the writer gate'"
check("single-quoted prose is handled too",
      not survives(single, "git add -A"), strip(single))

# The behaviour that already worked, pinned so this change cannot lose it.
here = ('python3 - <<\'EOF\'\n'
        'print("git reset --hard origin/main")\n'
        'EOF')
check("a heredoc body stays inert", not survives(here, "git reset --hard"),
      strip(here))
msg = 'git commit -m "explain why git add -A is banned"'
check("a quoted -m message stays inert", not survives(msg, "git add -A"),
      strip(msg))

print("\n  --- and real commands must still be scanned ---")

check("a bare dangerous command survives",
      survives("git clean -fd", "git clean -fd"))
check("a real command AFTER a described one survives",
      survives('gh pr create --body "about git clean" && git clean -fd',
               "git clean -fd"),
      strip('gh pr create --body "about git clean" && git clean -fd'))
check("a real command BEFORE a described one survives",
      survives('git clean -fd && gh pr create --body "about it"',
               "git clean -fd"))

# These ARE executed, so quoting must not hide them.
for wrapper in ('bash -c "git clean -fd"', "sh -c 'git clean -fd'",
                'ssh host "git clean -fd"'):
    check(f"an executed quoted command survives: {wrapper[:18]}…",
          survives(wrapper, "git clean -fd"), strip(wrapper))

# A path argument is not prose, and the flag only shares a prefix.
bodyfile = "gh pr create --body-file /tmp/b.md && git clean -fd"
check("--body-file is not mistaken for --body",
      survives(bodyfile, "git clean -fd"), strip(bodyfile))

# Same reasoning the module already gives for bare -m words.
bare = "gh pr create --body dont-run-git-clean && git clean -fd"
check("an UNQUOTED flag argument does not swallow what follows",
      survives(bare, "git clean -fd"), strip(bare))

# An unterminated quote must strip nothing rather than guess.
broken = 'gh pr create --body "unterminated && git clean -fd'
check("an unterminated quote strips nothing",
      survives(broken, "git clean -fd"), strip(broken))

print(f"\n{passed} passed, {len(failures)} failed")
if failures:
    print(f"FAILED: {', '.join(failures)}")
    sys.exit(1)
print("CMD TEXT SELFTEST PASSED: prose the shell hands over as bytes is "
      "invisible, and everything the shell actually runs is still scanned.")

# Independently reproduced Dot cases share the offline behavioral fixtures.
import runpy as _dot_runpy
_dot_runpy.run_path(str(__import__("pathlib").Path(__file__).with_name("dot-review-selftest.py")))["run_regressions"](['test_b01', 'test_control_single'])
