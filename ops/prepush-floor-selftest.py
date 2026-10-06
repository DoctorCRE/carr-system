#!/usr/bin/env python3
"""prepush-floor-selftest.py — the acceptance test for what ops/githooks/pre-push
runs, and for the plumbing that carries the answer back.

WHY THIS FILE EXISTS AT ALL. On 2026-08-23 the gates council moved the full
ten-class ops/ci.sh off the push path and left a fast local floor behind. Doing
that required three changes to the hook whose failure modes are silent:

  1. It now selects classes (`--only pushfloor,secret`). Select the wrong ones
     and the push path checks less than anybody thinks, with no symptom.
  2. It now derives CARR_CI_RANGE from the refs on stdin -- which the owner
     check above it already consumed, so stdin had to be captured and replayed.
     Get that wrong and either the owner check stops seeing main, or the range
     is empty and the secret scan silently widens (safe) or misreads (not).
  3. It now TEEs the run so the failure text can be attributed. A shell pipeline
     reports the status of its LAST command, and tee always succeeds, so the
     obvious spelling of this makes EVERY RED PUSH GREEN. That is the single
     worst outcome available in this file and it leaves no trace.

None of those is caught by "the checks pass". They are caught by asserting what
the hook invoked and what it did with the answer, which is what this does.

HOW. The unit under test is the HOOK, not ops/ci.sh, so the fixture installs a
STUB ci.sh that records its arguments and environment and exits with whatever
status the case wants. A real suite here would test ci.sh twice and this file's
actual subject not at all.

The fixture is built outside the checkout with ops/git_env.fixture_env(), per
ops/selftest-git-isolation-check.py -- and pointedly so, because this file is
run by ops/ci.sh which is run by this very hook.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ops"))
from git_env import fixture_env                                # noqa: E402

HOOK = ROOT / "ops" / "githooks" / "pre-push"

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


def git(repo, *args, env=None, check_rc=False):
    e = dict(env or fixture_env())
    p = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, env=e)
    if check_rc and p.returncode != 0:
        raise SystemExit(f"fixture setup failed: git {' '.join(args)}\n{p.stderr}")
    return p


STUB = """#!/bin/sh
# Stub ci.sh: record how the hook called us, then exit as the case demands.
{
  echo "args:$*"
  echo "range:${CARR_CI_RANGE-<unset>}"
} >> "$CI_CALL_LOG"
echo "  FAIL  pushfloor   seeded/failure.py is red"
exit "${STUB_CI_RC:-0}"
"""


def build(tmp):
    env = fixture_env()
    bare = tmp / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)],
                   env=env, check=True)
    repo = tmp / "work"
    subprocess.run(["git", "clone", "-q", str(bare), str(repo)], env=env,
                   check=True)
    git(repo, "config", "user.email", "fixture@example.invalid", check_rc=True)
    git(repo, "config", "user.name", "Fixture", check_rc=True)

    hooks = repo / "githooks"
    hooks.mkdir()
    shutil.copy(HOOK, hooks / "pre-push")
    os.chmod(hooks / "pre-push", 0o755)
    git(repo, "config", "core.hooksPath", "githooks", check_rc=True)

    ops = repo / "ops"
    ops.mkdir()
    (ops / "ci.sh").write_text(STUB)
    os.chmod(ops / "ci.sh", 0o755)
    (repo / "seed.txt").write_text("seed\n")
    git(repo, "add", "-A", check_rc=True)
    git(repo, "commit", "-qm", "seed", check_rc=True)
    # The seed push goes to main, which the owner check correctly refuses for a
    # fixture identity -- the hatch is the point of the hatch. It also proves,
    # before any assertion runs, that the owner check still reads its refs.
    seed_env = dict(env)
    seed_env.update(CARR_ALLOW_MAIN_PUSH="1", STUB_CI_RC="0",
                    CI_CALL_LOG="/dev/null")
    git(repo, "push", "-q", "origin", "main", env=seed_env, check_rc=True)
    return repo


def push(repo, branch, rc="0", log=None, extra=None):
    env = dict(fixture_env())
    env["STUB_CI_RC"] = rc
    env["CI_CALL_LOG"] = str(log) if log else "/dev/null"
    if extra:
        env.update(extra)
    return git(repo, "push", "origin", f"HEAD:refs/heads/{branch}", env=env)


def main():
    with tempfile.TemporaryDirectory(prefix="prepush-floor-") as td:
        tmp = pathlib.Path(td).resolve()
        repo = build(tmp)
        log = tmp / "calls.log"

        # 1. A GREEN floor lets the push through, and calls the fast subset.
        (repo / "a.txt").write_text("a\n")
        git(repo, "add", "a.txt", check_rc=True)
        git(repo, "commit", "-qm", "first", check_rc=True)
        r = push(repo, "feature-a", rc="0", log=log)
        check("a green floor allows the push", r.returncode == 0, r.stderr)
        calls = log.read_text() if log.exists() else ""
        check("the hook selected the fast subset, not the whole suite",
              "args:--only pushfloor secret" in calls, calls)
        # THE SEPARATOR IS LOAD-BEARING, so it is pinned rather than left to the
        # substring above to imply. ci.sh has only understood a COMMA list since
        # the push-floor change that taught --only to split them; before that
        # --only was a plain assignment and a comma list arrived as one token,
        # refused as "no such class: pushfloor,secret,freshness". This hook is
        # always canonical's (core.hooksPath is one shared path for every
        # worktree) while ci.sh comes from the tree being pushed, so a worktree
        # branched before that change runs today's hook against that day's
        # ci.sh. Observed 2026-08-26: every push from such a worktree refused,
        # naming a class that does not exist. Spaces parse correctly on BOTH
        # versions. A future edit that "tidies" these back to commas re-breaks
        # every older worktree on the machine, silently, so it fails here first.
        check("the class list is space-separated, parseable by every ci.sh version",
              "--only pushfloor,secret" not in calls, calls)
        check("the hook did NOT run the full ten-class suite",
              "args:\n" not in calls, calls)

        # 2. THE ONE THAT MATTERS. A red floor must refuse, and the tee must not
        #    swallow the status.
        (repo / "b.txt").write_text("b\n")
        git(repo, "add", "b.txt", check_rc=True)
        git(repo, "commit", "-qm", "second", check_rc=True)
        r = push(repo, "feature-b", rc="1", log=log)
        check("a RED floor refuses the push (tee did not mask the status)",
              r.returncode != 0, f"exit {r.returncode}")
        check("the refusal explains the local/hosted split",
              "hosted" in (r.stdout + r.stderr).lower())
        pushed = git(repo, "ls-remote", "origin", "refs/heads/feature-b").stdout
        check("and nothing reached the remote", pushed.strip() == "", pushed)

        # 3. THE RANGE reaches ci.sh, and names the commits being pushed.
        log.write_text("")
        r = push(repo, "feature-b", rc="0", log=log)
        calls = log.read_text()
        check("the pushed range is exported to ci.sh",
              "range:" in calls and "<unset>" not in calls, calls)
        head = git(repo, "rev-parse", "HEAD").stdout.strip()
        check("the range ends at the commit being pushed",
              head[:12] in calls, calls)

        # 3b. THE CLASS LIST IS THE FLOOR THE COUNCIL RULED, and `freshness` is
        #     part of it. Asserted on the recorded argv rather than by reading
        #     the hook, so a change to the list has to change this line too.
        check("the floor runs the ruled classes",
              "args:--only pushfloor secret freshness" in calls, calls)
        check("the floor does NOT run the full suite",
              "args:--only\n" not in calls and "gates" not in calls, calls)

        # 3c. MIGRATION IS CONDITIONAL. The class needs a throwaway Postgres and
        #     SKIPs on a laptop, so running it on every push buys a skip line;
        #     running it when the diff touches migrations/ buys the check.
        (repo / "migrations").mkdir(exist_ok=True)
        (repo / "migrations" / "0001_probe.sql").write_text("-- probe\n")
        git(repo, "add", "migrations/0001_probe.sql", check_rc=True)
        git(repo, "commit", "-qm", "a migration", check_rc=True)
        log.write_text("")
        push(repo, "feature-migration", rc="0", log=log)
        check("a diff touching migrations/ adds the migration class",
              "migration" in log.read_text(), log.read_text())

        # The negative case must be an INCREMENTAL push of the same branch. A
        # fresh branch's range runs back to the merge base, so it still carries
        # the migration commit above — and adding the class there is correct,
        # because that push really does ship a migration. Pushing again after a
        # non-migration commit is the case that must not add it.
        (repo / "plain.txt").write_text("no migration here\n")
        git(repo, "add", "plain.txt", check_rc=True)
        git(repo, "commit", "-qm", "no migration", check_rc=True)
        log.write_text("")
        push(repo, "feature-migration", rc="0", log=log)
        check("a diff with no migration does NOT add the migration class",
              "migration" not in log.read_text(), log.read_text())

        # 4. A SECOND PUSH of the same branch uses remote..local, not the whole
        #    branch: the incremental case is the common one.
        (repo / "c.txt").write_text("c\n")
        git(repo, "add", "c.txt", check_rc=True)
        git(repo, "commit", "-qm", "third", check_rc=True)
        log.write_text("")
        push(repo, "feature-b", rc="0", log=log)
        calls = log.read_text()
        check("an incremental push scopes to remote..local",
              f"range:{head}.." in calls, calls)

        # 5. THE OWNER CHECK ABOVE STILL SEES ITS REFS. Capturing stdin for the
        #    range is exactly the change that could have blinded it.
        env = dict(fixture_env())
        env.update(STUB_CI_RC="0", CI_CALL_LOG="/dev/null",
                   GIT_AUTHOR_EMAIL="stranger@example.invalid")
        r = git(repo, "-c", "user.email=stranger@example.invalid",
                "push", "origin", "HEAD:refs/heads/main", env=env)
        check("a non-owner push to main is still refused",
              r.returncode != 0, f"exit {r.returncode}")
        check("the owner refusal is the one that fired",
              "PUSH TO main REFUSED" in (r.stdout + r.stderr))

        # 5b. THE NEW-BRANCH BASE IS THE MERGE BASE, NOT origin/main.
        #     `git diff origin/main..HEAD` is a TWO-DOT diff: it compares two
        #     trees, so every commit main gained while the branch was in flight
        #     reads as a change this push is making, in reverse. Measured on the
        #     real branch that introduced this floor: seventeen paths, five of
        #     them other sessions' work on main. The floor would then typecheck
        #     files the push does not touch and run another session's selftest as
        #     if it were ours.
        #
        #     The assertion is that the range's BASE is an ancestor of the commit
        #     being pushed. With the bug it is not, because main moved sideways.
        git(repo, "checkout", "-q", "main", check_rc=True)
        (repo / "main-moved.txt").write_text("main advanced meanwhile\n")
        git(repo, "add", "main-moved.txt", check_rc=True)
        git(repo, "commit", "-qm", "main moves on", check_rc=True)
        seed_env = dict(fixture_env())
        seed_env.update(CARR_ALLOW_MAIN_PUSH="1", STUB_CI_RC="0",
                        CI_CALL_LOG="/dev/null")
        git(repo, "push", "-q", "origin", "main", env=seed_env, check_rc=True)
        # The fixture pushes with HEAD:refs/heads/<name>, so no LOCAL branch of
        # that name exists; branch from the pushed commit itself.
        git(repo, "checkout", "-q", "-b", "feature-diverged",
            "origin/feature-b", check_rc=True)
        (repo / "mine.txt").write_text("only mine\n")
        git(repo, "add", "mine.txt", check_rc=True)
        git(repo, "commit", "-qm", "my own work", check_rc=True)
        log.write_text("")
        push(repo, "feature-diverged", rc="0", log=log)
        calls = log.read_text()
        base = ""
        for line in calls.splitlines():
            if line.startswith("range:") and ".." in line:
                base = line[len("range:"):].split("..")[0]
        tip = git(repo, "rev-parse", "HEAD").stdout.strip()
        ancestor = git(repo, "merge-base", "--is-ancestor", base, tip).returncode == 0 \
            if base else False
        check("the new-branch range starts at the merge base, not origin/main",
              ancestor, f"base={base!r} is not an ancestor of {tip[:12]}")

        # 6. The escape hatch now needs a reason (bypass audit item 10,
        # 2026-09-24). CARR_SKIP_CI=1 alone used to skip a red floor
        # unconditionally, with no trail of who used it or why -- exactly the
        # "quiet door" the audit named. It now requires a non-empty
        # CARR_SKIP_CI_REASON to be honoured at all.
        (repo / "d.txt").write_text("d\n")
        git(repo, "add", "d.txt", check_rc=True)
        git(repo, "commit", "-qm", "fourth", check_rc=True)
        log.write_text("")
        r = push(repo, "feature-d", rc="1", log=log,
                 extra={"CARR_SKIP_CI": "1"})
        check("CARR_SKIP_CI=1 with NO reason is refused, not honoured",
              r.returncode != 0, r.stderr)
        check("and the suite WAS invoked (the skip did not take)",
              log.read_text().strip() != "", log.read_text())
        check("the refusal names the missing CARR_SKIP_CI_REASON",
              "CARR_SKIP_CI_REASON" in r.stderr, r.stderr)

        # 6b. WITH a non-empty reason, the skip is honoured exactly as before
        # -- and now leaves a receipt.
        receipts = repo / "out" / "ci-skip-receipts.jsonl"
        if receipts.exists():
            receipts.unlink()
        (repo / "e.txt").write_text("e\n")
        git(repo, "add", "e.txt", check_rc=True)
        git(repo, "commit", "-qm", "fifth", check_rc=True)
        log.write_text("")
        r = push(repo, "feature-e", rc="1", log=log,
                 extra={"CARR_SKIP_CI": "1", "CARR_SKIP_CI_REASON": "selftest exercising the honoured skip"})
        check("CARR_SKIP_CI=1 WITH a reason still skips a red floor",
              r.returncode == 0, r.stderr)
        check("and the suite was genuinely not invoked",
              log.read_text().strip() == "", log.read_text())
        check("a receipt was appended to out/ci-skip-receipts.jsonl",
              receipts.exists() and "selftest exercising the honoured skip" in receipts.read_text(),
              receipts.read_text() if receipts.exists() else "(no file)")
        check("the failure text's skip-command line is gone",
              "CARR_SKIP_CI=1 git push" not in r.stderr, r.stderr)

    # 7. PORTABILITY, asserted statically because the runner that catches it is
    #    not the machine that runs this hook. `mktemp -t <prefix>` is BSD
    #    spelling: macOS appends a random suffix, GNU coreutils requires XXXXXX
    #    and errors without it. That form passed every check on Joe's Mac and
    #    refused every push on the ubuntu runner, green ones included, because
    #    an empty CI_LOG made `cat ""` return 1. The behavioural cases above
    #    cannot see it from macOS; this line can.
    # Comment lines are excluded, and that is not a detail: the prose above the
    # fix necessarily SPELLS the broken form in order to warn about it, so a
    # naive scan of the whole file flags the warning as the defect.
    offenders = [line.strip() for line in HOOK.read_text().splitlines()
                 if "mktemp" in line
                 and not line.lstrip().startswith("#")
                 and "XXXXXX" not in line]
    check("every mktemp template carries XXXXXX (GNU errors without it)",
          not offenders, offenders)

    print(f"\n{passed} passed, {len(failures)} failed")
    if failures:
        print("FAILED: " + ", ".join(failures))
        return 1
    print("PRE-PUSH FLOOR SELFTEST PASSED: the hook runs the fast subset, "
          "exports the pushed range, still refuses a red floor and a non-owner "
          "push to main, and honours its escape hatch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
