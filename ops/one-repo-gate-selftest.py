#!/usr/bin/env python3
"""
one-repo-gate-selftest.py — acceptance test for hooks/one-repo-gate.py,
written before the gate (rule e65efc68: write the test before the thing, and
especially before a gate).

WHY THE GATE EXISTS. CARR's CLAUDE.md rev 10 says the code lives in ONE repo,
jbookout/carr-system, and a session that cannot reach it must STOP and say so
rather than improvise a home. That is prose, so it binds only the sessions that
read it and obey it — which is exactly how it failed. A cloud session that could
not see carr-system filed an entire system audit plus two specs into an empty
scaffold repo and hand-wrote a DECISIONS.md duplicating what the add-loop verb
already does. Nothing refused it, and nobody found it by looking.

THE MECHANISM THIS ROW ORIGINALLY NAMED DOES NOT WORK, and that is worth keeping
here so nobody re-proposes it. The DirectoryAdded event is real, but it is a
POST-ACTION event: the directory is already registered when it fires, and every
non-zero exit goes to the debug log only. A hook there is an audit line, not a
control.

WHAT DOES WORK is the door the failure actually came through. The shape is not
"a directory got registered", it is "engineering work got WRITTEN somewhere that
is not the repo" — a PreToolUse Write gate, the same door record-home-gate.py and
gate-edit-gate.py already sit on, which does deny.

WHAT THIS MUST GET RIGHT, and most of it is about what it must NOT do:

  1. WORKTREES ARE THE REPO. run.sh worktree is the house default for any session
     that will commit (rule 4a53ff82), so a worktree lives at
     ~/carr-system/.claude/worktrees/<name> and its .git is a FILE pointing back
     at the canonical .git. A gate that compared directory prefixes naively would
     deny every worktree session's first Python file and be turned off the same
     day. Case 4 is the one that matters most.

  2. CREATION, NOT EDITING. The failure shape is new work landing in a foreign
     home. Patching a file that already exists in some other checkout is ordinary
     and legitimate, and denying it would be the over-parsing the loop warned
     breeds the habit of disabling the gate.

  3. NOT-A-REPO IS NOT THIS GATE'S BUSINESS. Scratchpads, /tmp and the vault are
     owned by other controls or by nothing. A gate that fires everywhere is a
     gate people learn to ignore — lint-gate.py's doctrine, and it applies here.

  4. FAIL OPEN ON ERROR, closed only on a real match. A gate that wedges a
     session costs more than the marginal safety of failing closed.

KNOWN LIMIT, stated rather than discovered later: this covers Write/Edit/MultiEdit
only. A `python3 -c` heredoc from Bash writes the same file and is not seen —
the identical hole loop #287 documents in record-home-gate.py, whose fix is a
design question about parsing arbitrary shell for write targets. Naming it here
so this gate is never mistaken for complete coverage.

Hermetic: every case builds throwaway git repos and never touches the real one.
"""

import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GATE = os.path.join(REPO, "hooks", "one-repo-gate.py")

# The one scrubber (ops/git_env.py), not a local copy of the removed-not-
# blanked list this file used to hand-roll: git hands every hook a GIT_DIR
# pointing at the repository that invoked it, and on 2026-08-14 that leaked a
# fixture commit onto live main. A partial hand-rolled list (this one was
# missing GIT_ALTERNATE_OBJECT_DIRECTORIES, GIT_CEILING_DIRECTORIES and
# GIT_WORK_TREE_OVERRIDE) is exactly the drift rule a8c55a47 warns about.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "lib"))
from selftest_harness import Checker  # noqa: E402

CHECKER = Checker()
failures = CHECKER.failures
check = CHECKER.check


def git(repo, *args):
    """git in a fixture, scrubbed via fixture_env() — REMOVED, not blanked.

    Same lesson loose-work-gate-selftest.py paid for: GIT_DIR="" is not an unset
    GIT_DIR, it is a path that does not exist, and every call dies with `fatal:
    not a git repository: ''`. The fixture then never exists, and the cases that
    assert ALLOW all pass against nothing. fixture_env() pops the location vars
    rather than setting them empty, so this stays correct.
    """
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                       env=fixture_env())
    if p.returncode != 0 and args and args[0] in ("init", "add", "commit", "config",
                                                  "worktree"):
        raise SystemExit(f"fixture setup failed: git {' '.join(args)}\n{p.stderr}")
    return p


def make_repo(path):
    os.makedirs(path, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.invalid")
    git(path, "config", "user.name", "t")
    with open(os.path.join(path, "seed.txt"), "w") as fh:
        fh.write("seed\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "seed")
    return path


def run(file_path, home_repo, tool="Write", cwd=None, raw_payload=None, escape=False):
    """Drive the gate. Returns (exit_code, stderr).

    CARR_ONE_REPO_ROOT points the gate at the fixture that stands in for
    ~/carr-system, the same override idiom loose-work-gate.py uses, so no case
    here depends on the real checkout being present or absent.
    """
    payload = raw_payload if raw_payload is not None else json.dumps({
        "tool_name": tool,
        "tool_input": {"file_path": file_path, "content": "x = 1\n"},
        "cwd": cwd or os.path.dirname(file_path),
    })
    # The gate itself shells out to git to resolve the write target's repo, so
    # this invocation needs the same scrub as the fixture git() calls above —
    # an inherited GIT_DIR must not redirect what the gate thinks it is
    # judging either.
    env = dict(fixture_env(), CARR_ONE_REPO_ROOT=home_repo)
    env.pop("CARR_ALLOW_FOREIGN_REPO", None)
    if escape:
        env["CARR_ALLOW_FOREIGN_REPO"] = "1"
    p = subprocess.run([sys.executable, GATE], input=payload, capture_output=True,
                       text=True, env=env)
    return p.returncode, p.stderr


def main():
    print("one-repo gate")
    tmp = tempfile.mkdtemp(prefix="one-repo-gate-selftest-")
    try:
        home = make_repo(os.path.join(tmp, "carr-system"))
        foreign = make_repo(os.path.join(tmp, "some-other-repo"))
        loose = os.path.join(tmp, "not-a-repo")
        os.makedirs(loose, exist_ok=True)

        # A real worktree of the home repo, made the way run.sh worktree makes
        # one: .git is a FILE pointing back at the canonical repo.
        wt = os.path.join(home, ".claude", "worktrees", "featurebranch")
        git(home, "worktree", "add", "-q", "-b", "featurebranch", wt)
        check("fixture: the worktree's .git is a file, not a directory",
              os.path.isfile(os.path.join(wt, ".git")),
              "the worktree case is not exercising the shape it exists for")

        # 1. THE INCIDENT ITSELF: new code into a foreign checkout.
        rc, err = run(os.path.join(foreign, "audit.py"), home)
        check("new .py in a foreign repo is DENIED", rc == 2, f"exit {rc}")
        check("the refusal names the one repo", "carr-system" in err, err[:160])
        check("the refusal names the stop-and-say-so rule",
              "stop" in err.lower(), err[:160])

        # 2. Only code-shaped files. Markdown has its own gate and its own rules.
        rc, _ = run(os.path.join(foreign, "NOTES.md"), home)
        check("new .md in a foreign repo is allowed (not this gate's lane)", rc == 0,
              f"exit {rc}")

        # 3. The home repo is always fine.
        rc, _ = run(os.path.join(home, "tools", "thing.py"), home)
        check("new .py in carr-system is allowed", rc == 0, f"exit {rc}")

        # 4. THE CASE THAT WOULD GET THIS GATE SWITCHED OFF. A worktree IS the
        #    repo; run.sh worktree is the default for any session that commits.
        rc, err = run(os.path.join(wt, "tools", "thing.py"), home)
        check("new .py in a carr-system WORKTREE is allowed", rc == 0,
              f"exit {rc}: {err[:160]}")

        # 5. Outside any git tree — scratchpads, /tmp, the vault.
        rc, _ = run(os.path.join(loose, "scratch.py"), home)
        check("new .py outside any git tree is allowed", rc == 0, f"exit {rc}")

        # 6. Creation is the failure shape; patching what exists is ordinary.
        existing = os.path.join(foreign, "already.py")
        with open(existing, "w") as fh:
            fh.write("# pre-existing\n")
        rc, _ = run(existing, home, tool="Edit")
        check("editing an EXISTING file in a foreign repo is allowed", rc == 0,
              f"exit {rc}")

        # 7. Every code extension the rule names, so none is quietly unguarded.
        for ext in (".py", ".js", ".mjs", ".ts", ".sql", ".sh"):
            rc, _ = run(os.path.join(foreign, f"new{ext}"), home)
            check(f"new {ext} in a foreign repo is DENIED", rc == 2, f"exit {rc}")

        # 8. A relative path is resolved against cwd, or the gate is trivially
        #    walked around by not typing an absolute one.
        rc, _ = run("subdir/spec.py", home, cwd=foreign)
        check("a RELATIVE path resolves against cwd and is DENIED", rc == 2,
              f"exit {rc}")

        # 9/10/11. Fail open, and stay out of other tools' way.
        rc, _ = run("", home, raw_payload="{not json")
        check("a malformed payload fails OPEN", rc == 0, f"exit {rc}")
        rc, _ = run(os.path.join(foreign, "x.py"), home, tool="Read")
        check("a non-write tool is ignored", rc == 0, f"exit {rc}")
        rc, _ = run("", home, raw_payload=json.dumps(
            {"tool_name": "Write", "tool_input": {}}))
        check("a write with no file_path fails OPEN", rc == 0, f"exit {rc}")

        # 12. THE ESCAPE. This gate installs at USER level and therefore sees Life
        #     AI and every ordinary side project, all of which may legitimately
        #     hold a .py file. A control that fires on legitimate work with no way
        #     through is the one the originating loop named — it breeds the habit
        #     of disabling the gate, and a gate somebody switched off protects
        #     nothing. The refusal has to SAY so, or nobody finds the way through.
        rc, err = run(os.path.join(foreign, "sideproject.py"), home)
        check("the refusal names the escape variable",
              "CARR_ALLOW_FOREIGN_REPO" in err, err[:200])
        rc, _ = run(os.path.join(foreign, "sideproject.py"), home, escape=True)
        check("the escape hatch allows the write", rc == 0, f"exit {rc}")

        # 14. THE AUTHORIZED SIBLING HOMES (loop #610). Decision
        #     1ceee300-7627-426f-b729-ab339d6984fc superseded the single-code-home
        #     rule on 2026-09-14 and AGENTS.md now names three: carr-system,
        #     doctorcre-app and software-factory. Until this case existed the gate
        #     refused every NEW file in the other two, and the V5-UX slices worked
        #     around it by authoring outside the clone and copying files in —
        #     which is the gate teaching sessions to route around it, the exact
        #     failure its own docstring warns about.
        #
        #     IDENTITY IS THE ORIGIN REMOTE, not the directory name, because a
        #     clone can sit anywhere and be called anything. Both URL spellings
        #     are tested: a directory named doctorcre-app pointing at somebody
        #     else's remote must still be refused.
        for name, url in (
            ("ssh", "git@github.com:jbookout/doctorcre-app.git"),
            ("https", "https://github.com/jbookout/software-factory"),
        ):
            sibling = make_repo(os.path.join(tmp, f"authorized-{name}"))
            git(sibling, "remote", "add", "origin", url)
            rc, err = run(os.path.join(sibling, "app", "page.ts"), home)
            check(f"new code in an authorized home ({name} remote) is allowed",
                  rc == 0, f"exit {rc}: {err[:200]}")

        # …and the identity test has to be able to FAIL, or it is not a test.
        impostor = make_repo(os.path.join(tmp, "doctorcre-app"))
        git(impostor, "remote", "add", "origin",
            "git@github.com:someone-else/doctorcre-app.git")
        rc, _ = run(os.path.join(impostor, "app", "page.ts"), home)
        check("a look-alike clone on a foreign remote is still DENIED", rc == 2,
              f"exit {rc}")

        unrelated = make_repo(os.path.join(tmp, "life-ai"))
        git(unrelated, "remote", "add", "origin",
            "git@github.com:jbookout/life-ai.git")
        rc, _ = run(os.path.join(unrelated, "thing.py"), home)
        check("another repo of Joe's own is still DENIED", rc == 2, f"exit {rc}")

        rc, _ = run(os.path.join(foreign, "no-remote.py"), home)
        check("a repo with no origin remote at all is still DENIED", rc == 2,
              f"exit {rc}")

        # 13. A nested repo INSIDE the home tree is still foreign. This is the
        #     rev-10 shape on a laptop: a scaffold repo cloned somewhere handy.
        nested = make_repo(os.path.join(home, "vendor-checkout"))
        rc, _ = run(os.path.join(nested, "setup.py"), home)
        check("a nested foreign repo inside the home tree is still DENIED",
              rc == 2, f"exit {rc}")
    finally:
        subprocess.run(["rm", "-rf", tmp])

    return CHECKER.summary()


if __name__ == "__main__":
    raise SystemExit(main())
