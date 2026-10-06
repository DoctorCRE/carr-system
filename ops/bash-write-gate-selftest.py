#!/usr/bin/env python3
"""
bash-write-gate-selftest.py — acceptance test for hooks/bash-write-gate.py,
written before the gate (rule e65efc68).

WHY THE GATE EXISTS, proven in one session on 2026-08-09 rather than theorised:
the linkedin-engagement-daily run wrote a vault markdown file with a `python3 -c`
command from Bash and it SUCCEEDED. Minutes later the identical change attempted
through Edit was BLOCKED with record-home-gate.py's full refusal text. Nothing
about the content differed. THE SAME CONTENT IS REFUSED OR ALLOWED PURELY BY
WHICH TOOL CARRIES IT, because every write gate matches Write|Edit|MultiEdit and
Bash is a door none of them watch.

That run had no intent to bypass anything. It reached for Bash because the
content contained URLs another guard had just refused, and discovered the write
gate only when Edit refused a follow-up correction. An unintentional bypass by a
compliant session is stronger evidence that a control is mis-scoped than a
deliberate one would be — and it sets the threat model this file is written
against. The adversary here is CARELESSNESS, not evasion.

THE DESIGN QUESTION THE LOOP LEFT OPEN, and the answer taken. Three options were
on the table: parse Bash for write targets, protect the paths at the filesystem
level, or accept Bash as trusted and move enforcement somewhere it can be
complete. The problem with the first as stated is real — under-parsing leaves the
hole, over-parsing blocks legitimate work and breeds the habit of switching the
gate off.

What makes it tractable is refusing to write a THIRD POLICY. Two gates already
hold the judgment about where a write may land: record-home-gate.py owns the
vault, one-repo-gate.py owns code outside the repo. A Bash gate that re-decided
either would be the two-homes disease one layer down, and the copies would drift.
So this gate decides NOTHING. It extracts candidate write TARGETS from a shell
command and hands each to the existing policies unchanged — one judgment, three
doors, which is rule a8c55a47 (a manual path and an automated path that do the
same job must be the same code) applied to tool doors rather than to scripts.

WHAT IT MUST GET RIGHT, and most of it is the false positives:

  1. QUOTES ARE NOT REDIRECTIONS. `git commit -m "a > b"` contains a `>` that is
     not a redirect. Tokenising properly instead of regexing raw text is the
     whole difference, and case 'quoted' below is the one that would otherwise
     make this gate fire on ordinary commits until somebody disabled it.
  2. 2>&1 AND /dev/null ARE NOT FILES. They appear in a large share of all
     commands ever run here.
  3. READS ARE NOT WRITES. `cat` and `grep` against a vault file must pass, and
     so must a `python3 -c` that only opens a file to read it.
  4. THE RESIDUAL IS NAMED, NOT HIDDEN. Extraction cannot be complete against
     arbitrary shell — an interpreter this file does not know, a path built by
     string concatenation at runtime, a script invoked by name. The gate's
     docstring says so plainly rather than letting the next reader assume
     coverage it does not have.

Hermetic: builds a throwaway vault, a throwaway foreign repo, and drives the gate
as a subprocess exactly as the harness does.
"""

import json
import os
import subprocess
import sys
import tempfile

# The one scrubber (ops/git_env.py), not a local copy. GIT_DIR is exported by
# every git hook and overrides cwd for any child git process — the
# 2026-08-14 incident where fixture commits landed on live main is the
# reason the hand-rolled 3-variable pop in run() and the 7-variable list in
# git() below are replaced with the single, complete list git_env.py keeps.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GATE = os.path.join(REPO, "hooks", "bash-write-gate.py")

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "lib"))
from selftest_harness import Checker  # noqa: E402

CHECKER = Checker()
failures = CHECKER.failures
check = CHECKER.check


def policy_vault():
    """Where record-home-gate.py believes the vault is — asked, never assumed."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "carr_record_home_probe", os.path.join(REPO, "hooks", "record-home-gate.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.VAULT


def run(command, vault, home_repo, cwd=None):
    """Drive the gate over one shell command. Returns (exit_code, stderr)."""
    rc, _out, err = run_full(command, vault, home_repo, cwd)
    return rc, err


def run_full(command, vault, home_repo, cwd=None):
    """Drive the gate over one shell command. Returns (exit_code, stdout, stderr)."""
    payload = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": cwd or home_repo,
    })
    env = dict(fixture_env(), CARR_VAULT=vault, CARR_ONE_REPO_ROOT=home_repo)
    env.pop("CARR_ALLOW_FOREIGN_REPO", None)
    p = subprocess.run([sys.executable, GATE], input=payload, capture_output=True,
                       text=True, env=env)
    return p.returncode, p.stdout, p.stderr


def git(repo, *args):
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                       env=fixture_env())
    if p.returncode != 0 and args and args[0] in ("init", "add", "commit", "config"):
        raise SystemExit(f"fixture setup failed: git {' '.join(args)}\n{p.stderr}")
    return p


def main():
    print("bash write gate")
    tmp = tempfile.mkdtemp(prefix="bash-write-gate-selftest-")
    try:
        # THE FIXTURE IS DERIVED FROM THE POLICY UNDER TEST, not invented beside
        # it. The first version of this file built a stand-in vault in a temp dir
        # and asserted the vault cases against it — and every one of them passed
        # as ALLOW, because record-home-gate.py resolves its own vault root and
        # has no reason to care what a test wishes it were. Eighteen checks were
        # measuring nothing.
        #
        # Asking the policy where its vault is fixes that and needs no directory
        # to exist: that policy is path-structural, so it refuses a vault .md
        # whether or not the file is on disk. This runs identically on a laptop
        # with Drive mounted and on a CI runner with no vault at all.
        vault = policy_vault()
        deals = os.path.join(vault, "DNA", "Network", "deals.md")

        home = os.path.join(tmp, "carr-system")
        os.makedirs(home, exist_ok=True)
        git(home, "init", "-q", "-b", "main")
        git(home, "config", "user.email", "t@example.invalid")
        git(home, "config", "user.name", "t")
        with open(os.path.join(home, "seed.txt"), "w") as fh:
            fh.write("seed\n")
        git(home, "add", "seed.txt")
        git(home, "commit", "-q", "-m", "seed")

        foreign = os.path.join(tmp, "scaffold-repo")
        os.makedirs(foreign, exist_ok=True)
        git(foreign, "init", "-q", "-b", "main")
        git(foreign, "config", "user.email", "t@example.invalid")
        git(foreign, "config", "user.name", "t")
        with open(os.path.join(foreign, "seed.txt"), "w") as fh:
            fh.write("seed\n")
        git(foreign, "add", "seed.txt")
        git(foreign, "commit", "-q", "-m", "seed")

        loose = os.path.join(tmp, "scratch")
        os.makedirs(loose, exist_ok=True)

        # ── THE SHAPES THAT MUST BE CAUGHT ──────────────────────────────────
        # Every one of these writes a vault markdown file, which record-home-gate
        # refuses through Write and, until now, allowed through Bash.
        for label, cmd in [
            ("append redirect", f'echo "x" >> "{deals}"'),
            ("clobber redirect", f'echo "x" > "{deals}"'),
            ("no-space redirect", f'echo "x" >"{deals}"'),
            ("forced clobber", f'echo "x" >| "{deals}"'),
            ("heredoc into a file", f'cat > "{deals}" <<EOF\nx\nEOF'),
            ("tee", f'echo "x" | tee "{deals}"'),
            ("tee -a", f'echo "x" | tee -a "{deals}"'),
            ("sed -i", f"sed -i '' 's/a/b/' \"{deals}\""),
            ("cp destination", f'cp /tmp/x.md "{deals}"'),
            ("mv destination", f'mv /tmp/x.md "{deals}"'),
            ("dd of=", f'dd if=/tmp/x.md of="{deals}"'),
            # THE ONE THAT ACTUALLY HAPPENED, 2026-08-09.
            ("python3 -c write", f'python3 -c \'open("{deals}","w").write("x")\''),
            ("python3 -c append", f'python3 -c \'open("{deals}","a").write("x")\''),
            ("python write_text", f'python3 -c \'from pathlib import Path; Path("{deals}").write_text("x")\''),
            ("node writeFileSync", f'node -e \'require("fs").writeFileSync("{deals}","x")\''),
            ("second command in a chain", f'cd /tmp && echo "x" > "{deals}"'),
        ]:
            rc, err = run(cmd, vault, home)
            check(f"DENIED: {label}", rc == 2, f"exit {rc}")
            if label == "append redirect":
                check("the refusal names the path it caught",
                      "deals.md" in err, err[:200])
                check("the refusal explains it came through Bash",
                      "bash" in err.lower(), err[:200])

        # Code into a foreign repo, the one-repo policy reached through Bash.
        rc, _ = run(f'echo "x" > {foreign}/audit.py', vault, home)
        check("DENIED: new .py into a foreign repo via redirect", rc == 2, f"exit {rc}")

        # ── THE FALSE POSITIVES THAT WOULD GET THIS GATE SWITCHED OFF ───────
        allowed = [
            ("a quoted > inside a commit message",
             'git commit -m "throughput a > b now"'),
            ("stderr to stdout", "make build 2>&1"),
            ("redirect to /dev/null", "noisy-command >/dev/null 2>&1"),
            ("reading a vault file", f'cat "{deals}"'),
            ("grepping a vault file", f'grep -n foo "{deals}"'),
            ("a python -c that only READS a vault file",
             f'python3 -c \'print(open("{deals}").read())\''),
            ("writing to a scratchpad", f'echo "x" > {loose}/notes.md'),
            ("writing inside the repo", f'echo "{{}}" > {home}/out/thing.json'),
            ("a .md into a foreign repo (not the one-repo lane)",
             f'echo "x" > {foreign}/NOTES.md'),
            ("no redirect at all", "ls -la"),
            # THE LIVE FALSE POSITIVE, and the case this suite was missing. Every
            # fixture path above is an absolute literal, so nothing here exercised
            # what a PreToolUse hook actually receives: unexpanded shell. The gate
            # went live, and within a minute `echo x > "$D/NOTES.md"` was REFUSED —
            # $D is not expanded before the hook runs, the literal text was joined
            # to cwd, and that session's cwd was the vault, so an ordinary write
            # into a scratch directory read as vault markdown. A false DENY blocks
            # real work every time; a false ALLOW only misses something.
            ("a target built from a shell variable", 'echo "x" > "$D/NOTES.md"'),
            ("a braced shell variable", 'echo "x" > "${OUT}/report.md"'),
            ("a command substitution", 'echo "x" > "$(mktemp -d)/notes.md"'),
            ("a glob target", 'echo "x" > out/*.json'),
            # THE SECOND LIVE FALSE POSITIVE, found the same hour as the first and
            # by the same route: a verification command of my own. The heredoc
            # scan paired two loose tests — "contains a path literal" and,
            # separately, "contains a write indicator" — so a script that READ one
            # file and WROTE another had the read path judged as a write target.
            # That fires on most scripts. A path must now sit inside the write
            # call itself.
            ("a heredoc script that reads one file and writes another",
             'python3 <<PY\n'
             'src = "{deals}"\n'
             'print(open(src).read())\n'
             'open("/tmp/out.txt","w").write("done")\n'
             'PY'),
            ("a path mentioned in a list beside an unrelated write",
             'python3 -c \'paths=["{deals}"]; open("/tmp/x.txt","w").write(str(paths))\''),
            ("a heredoc with no file target", "python3 <<'PY'\nprint(1)\nPY"),
        ]
        for label, cmd in allowed:
            rc, err = run(cmd.replace("{deals}", deals), vault, home)
            check(f"allowed: {label}", rc == 0, f"exit {rc}: {err[:160]}")

        # ── THE JEV RECEIPT LEDGER: WARNED, NEVER BLOCKED (PR #1224 round 3) ─
        # ops/typesafe_client.py's ask() is the only legitimate writer of
        # out/jev-calls.jsonl and writes it from inside Python, so any shell
        # command that writes the file is a forged receipt or a mistake. The
        # gate warns loudly (decision d47931da: detectable, not prevented).
        ledger = f"{home}/out/jev-calls.jsonl"
        for label, cmd in [
            ("append redirect onto the ledger", f"echo '{{\"ok\": true}}' >> {ledger}"),
            ("tee -a onto the ledger", f"echo x | tee -a {ledger}"),
            ("python append onto the ledger",
             f'python3 -c \'open("{ledger}","a").write("x")\''),
            ("inline write in a command naming the ledger by variable",
             'python3 -c \'p="out/jev-calls.jsonl"; open(p,"a").write("x")\''),
        ]:
            rc, out, _ = run_full(cmd, vault, home)
            check(f"WARNED (allowed): {label}",
                  rc == 0 and "JEV RECEIPT LEDGER WRITE DETECTED" in out, f"exit {rc}: {out[:160]}")
        for label, cmd in [
            ("tail of the ledger", f"tail -5 {ledger}"),
            ("grep of the ledger", f"grep -c ok {ledger} 2>&1"),
            ("a script that calls ask() without naming the ledger",
             "./.venv/bin/python ops/some_jev_caller.py > /tmp/answers.json"),
        ]:
            rc, out, _ = run_full(cmd, vault, home)
            check(f"not warned: {label}",
                  rc == 0 and "JEV RECEIPT LEDGER" not in out, f"exit {rc}: {out[:160]}")

        # ── FAIL OPEN ───────────────────────────────────────────────────────
        rc, _ = run("", vault, home)
        check("an empty command is allowed", rc == 0, f"exit {rc}")
        p = subprocess.run([sys.executable, GATE], input="{not json",
                           capture_output=True, text=True,
                           env={**os.environ, "CARR_VAULT": vault})
        check("a malformed payload fails OPEN", p.returncode == 0,
              f"exit {p.returncode}")
        p = subprocess.run([sys.executable, GATE],
                           input=json.dumps({"tool_name": "Read",
                                             "tool_input": {"command": f'echo x > "{deals}"'}}),
                           capture_output=True, text=True,
                           env={**os.environ, "CARR_VAULT": vault})
        check("a non-Bash tool is ignored", p.returncode == 0, f"exit {p.returncode}")
    finally:
        __import__("shutil").rmtree(tmp)

    return CHECKER.summary()



# Independently reproduced Dot cases share the offline behavioral fixtures.
import runpy as _dot_runpy
_dot_runpy.run_path(str(__import__("pathlib").Path(__file__).with_name("dot-review-selftest.py")))["run_regressions"](['test_b05', 'test_control_copy'])

if __name__ == "__main__":
    raise SystemExit(main())
