#!/usr/bin/env python3
"""corpus-render-gate-selftest.py — proves the corpus-render write-block actually
denies, by spawning the REAL hooks and reading their exit codes.

Same contract as ops/guard-selftest.py: no imports of the gate's internals, no
mocking. A gate is verified by running it, because the failure this whole family
of checks exists to catch is a gate that is installed, looks healthy, and does
nothing (2026-08-08, when a plugin install silently deleted the hooks block and
five gates were off for a day).

WHAT IT PROVES. Both doors of the block Joe ordered on 2026-08-10:
  * the Write/Edit door in hooks/record-home-gate.py
  * the Bash door in hooks/guard-unattended.py
deny writes onto corpus renders under BOTH Drive spellings, allow the repo path
that is the correct place to make the same edit, allow reads, and allow
corpus-sync.py, which is the sanctioned writer of every one of these files.

THE ALLOW CASES CARRY THE WEIGHT. Any gate can deny everything. A block that
also stops the correct edit is a block someone turns off within a week, and off
it protects nothing.

Exit 0 when every case behaves; 1 otherwise, naming each failure.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(REPO, "hooks")
sys.path.insert(0, HOOKS)
from corpus_renders import render_map, roots, command_spellings  # noqa: E402

DENY, ALLOW = 2, 0
CASES: list[tuple[str, str, dict, int]] = []   # (label, hook, payload, expected_exit)


def run(hook, payload, home=None):
    p = subprocess.run(["/usr/bin/env", "python3", os.path.join(HOOKS, hook)],
                       input=payload if isinstance(payload, str) else json.dumps(payload), capture_output=True, text=True,
                       env={**os.environ, "HOME": home} if home else None)
    return p.returncode, (p.stderr or "").strip()


def edit(path):
    return {"tool_name": "Write",
            "tool_input": {"file_path": path, "content": "x"}}


def bash(cmd):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


def build():
    rt = roots()
    rm = render_map()
    if not rm:
        print("FAIL: the deny set is EMPTY — corpus/manifest.json unreadable, so the "
              "gate is failing open on every write. This is the whole finding.")
        sys.exit(1)

    drive_md = os.path.join(rt["drive"], ".claude", "agents", "council-skeptic.md")
    home_md = os.path.join(rt["home"], ".claude", "skills", "handoff", "SKILL.md")
    vault_md = os.path.join(rt["vault"], "DNA", "Deal Management",
                            "deal-analysis-toolkit.md")
    repo_src = os.path.join(REPO, "corpus", "_drive", ".claude", "agents",
                            "council-skeptic.md")

    # The unresolved CloudStorage spelling of the same file — the one that
    # realpath collapses, and the one a Bash deny set of realpaths would miss.
    raw_drive = next((s for s, r in command_spellings().items()
                      if r == os.path.realpath(drive_md) and s != os.path.realpath(drive_md)),
                     drive_md)

    # --- Write/Edit door -----------------------------------------------------
    CASES.append(("Write onto a Drive agent render", "record-home-gate.py",
                  edit(drive_md), DENY))
    CASES.append(("Write onto a ~/.claude skill render", "record-home-gate.py",
                  edit(home_md), DENY))
    CASES.append(("Write onto a vault doctrine render", "record-home-gate.py",
                  edit(vault_md), DENY))
    CASES.append(("Write via the CloudStorage spelling", "record-home-gate.py",
                  edit(raw_drive), DENY))
    CASES.append(("ALLOW: the repo original, which is the correct edit",
                  "record-home-gate.py", edit(repo_src), ALLOW))
    CASES.append(("ALLOW: an unrelated scratch file", "record-home-gate.py",
                  edit("/tmp/claude-501/scratch-note.md"), ALLOW))

    # --- Bash door -----------------------------------------------------------
    CASES.append(("Bash append onto a Drive render", "guard-unattended.py",
                  bash(f'echo hi >> "{drive_md}"'), DENY))
    CASES.append(("Bash append, CloudStorage spelling", "guard-unattended.py",
                  bash(f'echo hi >> "{raw_drive}"'), DENY))
    CASES.append(("Bash tee onto a render", "guard-unattended.py",
                  bash(f'echo hi | tee "{home_md}"'), DENY))
    CASES.append(("Bash sed -i on a render", "guard-unattended.py",
                  bash(f"sed -i '' 's/a/b/' \"{drive_md}\""), DENY))
    CASES.append(("Bash cp onto a render", "guard-unattended.py",
                  bash(f'cp /tmp/x.md "{drive_md}"'), DENY))
    CASES.append(("Bash python open(w) on a render", "guard-unattended.py",
                  bash(f"python3 -c \"open('{drive_md}','w').write('x')\""), DENY))
    CASES.append(("ALLOW: reading a render", "guard-unattended.py",
                  bash(f'cat "{drive_md}"'), ALLOW))
    CASES.append(("ALLOW: grep across the agents dir", "guard-unattended.py",
                  bash(f'grep -rn council "{os.path.dirname(drive_md)}"'), ALLOW))
    CASES.append(("ALLOW: corpus-sync --push, the sanctioned writer",
                  "guard-unattended.py",
                  bash("cd ~/carr-system && python3 tools/corpus-sync.py --push"), ALLOW))
    CASES.append(("ALLOW: writing the repo original from the shell",
                  "guard-unattended.py", bash(f'echo hi >> "{repo_src}"'), ALLOW))


def check_cases(cases, home=None):
    fails = []
    for label, hook, payload, want in cases:
        code, err = run(hook, payload, home)
        if code != want:
            verb = "DENIED" if code == DENY else f"allowed (exit {code})"
            fails.append(f"  {label}: expected {'DENY' if want == DENY else 'ALLOW'}, "
                         f"got {verb}. {err[:160]}")
    return fails


def main():
    build()
    fails = check_cases(CASES)
    with tempfile.TemporaryDirectory(prefix="corpus-render-home-") as tmp:
        canonical_home = Path(tmp).resolve() / "home"
        canonical_home.mkdir()
        linked_home = Path(tmp) / "linked-home"
        linked_home.symlink_to(canonical_home, target_is_directory=True)
        cases = []
        for spelling, home in (("symlinked", linked_home), ("canonical", canonical_home)):
            target = home / ".claude/skills/handoff/SKILL.md"
            for operation, command, want in (
                ("tee", f'echo hi | tee "{target}"', DENY),
                ("redirect", f'echo hi > "{target}"', DENY),
                ("cp", f'cp /tmp/x.md "{target}"', DENY),
                ("read", f'cat "{target}"', ALLOW),
            ):
                cases.append((f"Bash {operation}, {spelling} HOME", "guard-unattended.py",
                              bash(command), want))
        suffix = "/.claude/skills/handoff/SKILL.md"
        spellings = {
            "HOME": '"$HOME' + suffix + '"',
            "braced HOME": '"${HOME}' + suffix + '"',
            "tilde": '~' + suffix,
            "relative real": 'home' + suffix,
            "relative symlink": 'linked-home' + suffix,
            "dotdot": str(linked_home) + '/../linked-home' + suffix,
            "double slash": str(linked_home) + '//' + suffix.lstrip('/'),
            "dot segment": str(linked_home) + '/.' + suffix,
            "internal dotdot": str(linked_home) + '/.claude/skills/../skills/handoff/SKILL.md',
        }
        for spelling, operand in spellings.items():
            for operation, command in (
                ("tee", f'echo hi | tee {operand}'),
                ("redirect", f'echo hi > {operand}'),
                ("append", f'echo hi >> {operand}'),
                ("cp", f'cp /tmp/x.md {operand}'),
                ("mv", f'mv /tmp/x.md {operand}'),
                ("heredoc", f'cat > {operand} <<EOF\nprobe\nEOF'),
                ("python", "python3 -c \"open('" + operand.strip('"') + "','w').write('x')\""),
            ):
                payload = bash(command)
                payload["cwd"] = tmp
                cases.append((f"Bash {operation}, {spelling}", "guard-unattended.py", payload, DENY))
            payload = bash(f'cat {operand}')
            payload['cwd'] = tmp
            cases.append((f"ALLOW read, {spelling}", "guard-unattended.py", payload, ALLOW))
        for label, payload in (
            ("malformed JSON", "{"),
            ("empty input", ""),
            ("numeric command", {"tool_name": "Bash", "tool_input": {"command": 42}}),
            ("missing command", {"tool_name": "Bash", "tool_input": {}}),
            ("array event", []),
        ):
            cases.append((label, "guard-unattended.py", payload, DENY))
        fails.extend(check_cases(cases, str(linked_home)))
    total = len(CASES) + len(cases)
    print(f"corpus-render gate selftest — {total - len(fails)}/{total} passed "
          f"· deny set {len(render_map())} render(s)")
    if fails:
        print("FAILED:")
        for f in fails:
            print(f)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
