#!/usr/bin/env python3
"""inherited-from-main-selftest.py — the paired suite for ops/inherited-from-main.py.

THE ONE THING THIS MECHANISM MUST NEVER DO is tell a session "not your fault"
about a defect that IS their fault. Everything else it can get wrong cheaply: a
refusal costs one full CI run, which is the state we were already in. So the
suite is built around seeding BOTH directions against the same fixture — a
broken base with an innocent branch, and a clean base with a guilty branch —
and the guilty case is the one that would have to be re-argued if anybody ever
loosens the check.

EVERY CASE RUNS AGAINST A REAL GIT REPOSITORY built in a temp directory, never
against carr-system itself. A structural test that greps the source for the
right-looking strings would pass just as happily on a version that answered
backwards, and this file's whole reason for existing is the answer.

Exit 0 all cases pass · 1 a case failed.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HELPER = os.path.join(REPO, "ops", "inherited-from-main.py")
INHERITED, NOT_INHERITED, CANNOT_TELL = 0, 1, 2

failures: list[str] = []

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402

# fixture_env, not a list of our own: it drops every variable git consults before
# the working directory AND points global config at /dev/null, which is what
# stops a fixture inheriting Joe's identity — and, on this Mac, stops a fixture
# commit running carr-system's own 50 gates through the absolute core.hooksPath.


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        failures.append(name)


def git(repo, *args, must=False):
    p = subprocess.run(["git", "-C", repo, *args],
                       capture_output=True, text=True, env=fixture_env())
    if must and p.returncode != 0:
        raise SystemExit(f"fixture setup failed: git {' '.join(args)}\n{p.stderr}")
    return p


def write(repo, rel, text, executable=False):
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path) or repo, exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    if executable:
        os.chmod(path, 0o755)


# A check that PASSES or FAILS according to a file it does not live in. That
# separation is the point: it lets a branch break the check without the diff
# ever naming the check, which is the case the pre-filter cannot see and only
# the merge-base re-run can answer.
CHECK_PY = """\
import sys
verdict = open("subject.txt").read().strip()
if verdict == "good":
    sys.exit(0)
if verdict == "notconfigured":
    print("no dependency here")
    sys.exit(78)
if verdict == "slow":
    import time
    time.sleep(60)
print("subject.txt says " + verdict)
sys.exit(1)
"""


def make_repo(tmp, base_subject="good"):
    """A repo whose main tip is a commit with subject.txt == base_subject.

    A PLAIN REPO, not a bare origin plus a clone. The helper falls back from
    origin/main to a local main, so the remote adds nothing to any case here
    except a second git operation per fixture — and this suite is collected by
    ops/ci.sh's gates class, the slowest class in the suite and the one the
    2026-08-23 council is trying to make cheaper. Ten clones cost real seconds
    on every push forever to test nothing extra.
    """
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main", must=True)
    git(repo, "config", "user.email", "t@example.invalid", must=True)
    git(repo, "config", "user.name", "t", must=True)
    write(repo, "check.py", CHECK_PY)
    write(repo, "subject.txt", base_subject + "\n")
    write(repo, "unrelated.txt", "seed\n")
    git(repo, "add", "-A", must=True)
    git(repo, "commit", "-q", "-m", "seed", must=True)
    return repo


def branch(repo, name, edits):
    git(repo, "checkout", "-q", "-b", name, must=True)
    for rel, text in edits.items():
        write(repo, rel, text)
    git(repo, "add", "-A", must=True)
    git(repo, "commit", "-q", "-m", f"work on {name}", must=True)


def ask(repo, *extra, timeout="180", env=None, check_path="check.py"):
    """Run the helper the way ops/ci.sh runs it: from inside the branch tree.

    check_path is the repo-relative check, defaulting to the one make_repo
    seeds; the collection cases below put a second copy in a subdirectory the
    fixture's own ci.sh globs do not reach.
    """
    e = fixture_env()
    e.pop("CARR_CI_NO_INHERIT_CHECK", None)
    if env:
        e.update(env)
    p = subprocess.run(
        [sys.executable, HELPER, "--check", check_path, "--timeout", timeout,
         *extra, "--", sys.executable, check_path],
        cwd=repo, capture_output=True, text=True, env=e, timeout=300)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def commit_ci_sh(repo, loops, extra_files=None):
    """Give the fixture an ops/ci.sh whose collection globs are `loops`.

    Only the `for t in ...; do` shape matters — the helper reads ci.sh's
    collection globs out of its source and never executes it.
    """
    body = "".join(f"for t in {loop}; do :; done\n" for loop in loops)
    write(repo, "ops/ci.sh", "#!/usr/bin/env bash\n" + body)
    for rel, text in (extra_files or {}).items():
        write(repo, rel, text)
    git(repo, "add", "-A", must=True)
    git(repo, "commit", "-q", "-m", "ci.sh and its collected checks", must=True)


# ---------------------------------------------------------------- the two seeds
def test_broken_base_innocent_branch_is_inherited():
    """Cluster A, 2026-08-22: main is broken, the branch never touched it."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, base_subject="bad")          # main is already red
        branch(repo, "victim", {"unrelated.txt": "a tour packet\n"})
        rc, out = ask(repo)
        check("a break present at the merge base is reported INHERITED",
              rc == INHERITED, f"exit {rc}\n{out}")
        check("the message is the one the council specified",
              "INHERITED FROM MAIN — do not diagnose this branch" in out, out)
        check("the message names the check that is broken on main",
              "the break is check.py on main" in out, out)
        check("the message NAMES THE MOVE rather than only the diagnosis",
              "THE MOVE:" in out and "nothing on this branch" in out, out)


def test_clean_base_guilty_branch_is_not_inherited():
    """The case that must never misfire: the branch really did break it.

    The branch does NOT touch check.py — it changes the file check.py reads. So
    the diff pre-filter cannot save us here and the verdict has to come from the
    merge-base re-run. If this case ever answers INHERITED, a session is being
    told to stop looking at its own defect.
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, base_subject="good")          # main is green
        branch(repo, "guilty", {"subject.txt": "bad\n"})    # the branch breaks it
        rc, out = ask(repo)
        check("a break the branch introduced is NOT called inherited",
              rc == NOT_INHERITED, f"exit {rc}\n{out}")
        check("the branch is told plainly that it owns the failure",
              "NOT inherited" in out and "this branch introduced the failure" in out, out)
        check("no attribution banner leaks into the guilty case",
              "INHERITED FROM MAIN" not in out, out)


# ---------------------------------------------------------- every refusal path
def test_a_touched_check_is_left_to_the_branch():
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, base_subject="bad")
        # Even with a broken base, editing the check itself hands it back.
        branch(repo, "edits-the-check", {"check.py": CHECK_PY + "# touched\n"})
        rc, out = ask(repo)
        check("editing the failing check itself is never attributed to main",
              rc == CANNOT_TELL, f"exit {rc}\n{out}")
        check("the refusal says which file put it out of scope",
              "the change touches check.py" in out, out)


def test_a_check_absent_from_the_merge_base_is_not_inherited():
    """python exits 2 on a missing file; that must never read as a break there.

    Reached with an UNTRACKED check — present in the tree, absent from the diff,
    absent from the merge base. When the branch commits the new check instead,
    the diff pre-filter answers first and this guard never runs; that ordering is
    correct and is asserted separately below. The guard exists for the case the
    pre-filter cannot see, and without it `python missing.py` exiting 2 would be
    read as "it fails at the merge base too" — the exact misattribution this
    whole mechanism must not make.
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, base_subject="good")
        branch(repo, "adds-a-check", {"unrelated.txt": "x\n"})
        write(repo, "newcheck.py", "import sys; sys.exit(1)\n")   # never committed
        e = fixture_env()
        p = subprocess.run([sys.executable, HELPER, "--check", "newcheck.py",
                            "--", sys.executable, "newcheck.py"],
                           cwd=repo, capture_output=True, text=True, env=e, timeout=300)
        out = (p.stdout or "") + (p.stderr or "")
        check("a check absent from the merge base is not called inherited",
              p.returncode == CANNOT_TELL, f"exit {p.returncode}\n{out}")
        check("the refusal says it does not exist at the merge base",
              "does not exist at the merge base" in out, out)


def test_committing_a_new_check_is_answered_by_the_pre_filter_first():
    """The cheap answer must come first — the guard above is the backstop."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, base_subject="good")
        branch(repo, "adds-a-check", {"newcheck.py": "import sys; sys.exit(1)\n"})
        e = fixture_env()
        p = subprocess.run([sys.executable, HELPER, "--check", "newcheck.py",
                            "--", sys.executable, "newcheck.py"],
                           cwd=repo, capture_output=True, text=True, env=e, timeout=300)
        out = (p.stdout or "") + (p.stderr or "")
        check("a committed new check is handed back without materialising anything",
              p.returncode == CANNOT_TELL and "the change touches newcheck.py" in out,
              f"exit {p.returncode}\n{out}")


def test_exit_78_at_the_base_is_not_a_break():
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, base_subject="notconfigured")
        branch(repo, "innocent", {"unrelated.txt": "x\n"})
        rc, out = ask(repo)
        check("a check that DECLINED at the merge base is not called a break there",
              rc == CANNOT_TELL, f"exit {rc}\n{out}")
        check("the refusal names exit 78 rather than inventing a verdict",
              "exit 78" in out, out)


def test_an_unmaterialised_runtime_prerequisite_is_not_a_break():
    """A detached merge-base tree does not inherit ignored installed tools.

    The caller's checkout has the runtime, so the check is reproducible there.
    The helper's fresh worktree does not. Treating that environment difference
    as a failing base would falsely exonerate an unrelated branch.
    """
    runtime_check = """\
import pathlib
import subprocess

root = pathlib.Path(__file__).resolve().parent
subprocess.run([str(root / "runtime" / "tool")], check=True)
"""
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, base_subject="good")
        write(repo, "runtime-check.py", runtime_check)
        git(repo, "add", "runtime-check.py", must=True)
        git(repo, "commit", "-q", "-m", "add runtime-dependent check", must=True)
        branch(repo, "innocent", {"unrelated.txt": "x\n"})
        write(repo, "runtime/tool", "#!/bin/sh\nexit 0\n", executable=True)

        live = subprocess.run([sys.executable, "runtime-check.py"], cwd=repo,
                              capture_output=True, text=True, env=fixture_env())
        check("the runtime-dependent check is configured in the caller checkout",
              live.returncode == 0, live.stderr)

        e = fixture_env()
        p = subprocess.run(
            [sys.executable, HELPER, "--check", "runtime-check.py", "--",
             sys.executable, "runtime-check.py"],
            cwd=repo, capture_output=True, text=True, env=e, timeout=300)
        out = (p.stdout or "") + (p.stderr or "")
        check("a runtime absent only from the detached replay resolves to cannot-tell",
              p.returncode == CANNOT_TELL, f"exit {p.returncode}\n{out}")
        check("the refusal names the unmaterialised runtime prerequisite",
              "runtime prerequisite is absent from the merge-base worktree" in out, out)


def test_the_replay_reuses_the_callers_installed_dependencies():
    """Requirement: mcp-server/node_modules and .venv are symlinked into the
    detached merge-base tree before a check runs there.

    `git worktree add` materialises tracked source only — never an ignored,
    installed directory such as node_modules or .venv (see .gitignore). A
    check whose only problem is that IT runs in a fresh tree must not read as
    a break on main; this proves the mechanism reuses what the caller
    checkout already installed rather than merely detecting its absence.
    """
    dependency_check = """\
import os
import sys
root = os.path.dirname(os.path.abspath(__file__))
marker = os.path.join(root, "mcp-server", "node_modules", "MARKER")
if os.path.isfile(marker):
    sys.exit(0)
sys.stderr.write("dependency missing: " + marker + "\\n")
sys.exit(1)
"""
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, base_subject="good")
        write(repo, "dependency-check.py", dependency_check)
        git(repo, "add", "dependency-check.py", must=True)
        git(repo, "commit", "-q", "-m", "add dependency-dependent check", must=True)
        branch(repo, "innocent", {"unrelated.txt": "x\n"})
        # An installed, ignored dependency directory present ONLY in the
        # caller checkout — never committed, exactly like a real npm install.
        os.makedirs(os.path.join(repo, "mcp-server", "node_modules"), exist_ok=True)
        write(repo, "mcp-server/node_modules/MARKER", "installed\n")

        rc, out = ask(repo, check_path="dependency-check.py")
        check("the base replay finds the caller's node_modules via the symlink",
              rc == NOT_INHERITED, f"exit {rc}\n{out}")
        check("no attribution banner and no 'dependency missing' trace leak through",
              "INHERITED FROM MAIN" not in out and "dependency missing" not in out, out)


def test_missing_node_modules_replay_is_attribution_unavailable():
    """defect 71c7c3f2 (PR #1195, hosted run 35979979350, 2026-09-24): the
    merge-base re-run of ci-selftest.py's "ci.yml parses" check failed only
    because the detached tree had no mcp-server/node_modules — a bare
    `node -e "require('js-yaml')..."` MODULE_NOT_FOUND, reported INHERITED
    FROM MAIN even though main's own gate had passed on its own PR.

    This seeds a check whose caller-side install directory is ALSO absent (so
    link_install_dirs() has nothing to symlink) and whose failure text names
    the missing package rather than a tree-rooted path — the shape
    missing_replay_prerequisite() cannot see, because Node's own
    MODULE_NOT_FOUND message never spells the missing module as a path. The
    verdict must be "attribution unavailable", never INHERITED and never a
    silent NOT_INHERITED that would blame the branch for an environment gap.
    """
    node_style_check = """\
import sys
sys.stderr.write(
    "node:internal/modules/cjs/loader:1050\\n"
    "Error: Cannot find module 'js-yaml'\\n"
    "Require stack:\\n"
    "- /tmp/carr-mergebase-xyz/base/mcp-server/[eval]\\n"
    "    at Module._resolveFilename (node:internal/modules/cjs/loader:1047:15) {\\n"
    "  code: 'MODULE_NOT_FOUND',\\n"
    "  requireStack: [ '/tmp/carr-mergebase-xyz/base/mcp-server/[eval]' ]\\n"
    "}\\n"
)
sys.exit(1)
"""
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, base_subject="good")
        write(repo, "node-check.py", node_style_check)
        git(repo, "add", "node-check.py", must=True)
        git(repo, "commit", "-q", "-m", "add node-dependent check", must=True)
        branch(repo, "innocent", {"unrelated.txt": "x\n"})
        # Deliberately no mcp-server/node_modules anywhere — the caller
        # checkout has nothing to symlink, exactly like a from-scratch worktree.

        rc, out = ask(repo, check_path="node-check.py")
        check("a MODULE_NOT_FOUND replay is never read as an inherited break",
              rc == CANNOT_TELL, f"exit {rc}\n{out}")
        check("no INHERITED FROM MAIN banner is printed",
              "INHERITED FROM MAIN" not in out, out)
        check("the refusal names the environment-class signature",
              "environment-class signature" in out and "MODULE_NOT_FOUND" in out, out)


def test_missing_venv_replay_is_attribution_unavailable():
    """The same environment-class guard for a Python virtualenv, not Node's."""
    venv_style_check = """\
import sys
sys.stderr.write(
    "/bin/sh: /tmp/carr-mergebase-xyz/base/.venv/bin/python: No such file or directory\\n"
)
sys.exit(1)
"""
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, base_subject="good")
        write(repo, "venv-check.py", venv_style_check)
        git(repo, "add", "venv-check.py", must=True)
        git(repo, "commit", "-q", "-m", "add venv-dependent check", must=True)
        branch(repo, "innocent", {"unrelated.txt": "x\n"})
        # No .venv anywhere in the caller checkout either.

        rc, out = ask(repo, check_path="venv-check.py")
        check("a missing-venv replay is never read as an inherited break",
              rc == CANNOT_TELL, f"exit {rc}\n{out}")
        check("no INHERITED FROM MAIN banner is printed",
              "INHERITED FROM MAIN" not in out, out)
        check("the refusal names the missing virtualenv",
              "virtualenv" in out, out)


def test_a_check_this_branch_newly_collected_is_not_inherited():
    """main never RAN it, so its failure at the merge base is not main's break.

    2026-09-10, and this is the case that cost two full diagnoses. A branch
    widened ops/ci.sh's collection globs to reach tools/<subdir>/test_*.py.
    One of the suites that started running failed on the hosted runner, the
    re-run at the merge base failed there too — truthfully, the suite really
    does exit 1 in that environment — and the branch was told "INHERITED FROM
    MAIN, wait for main to go green" about a file main's own globs had never
    collected once. Main was green. The canary had nothing to name. No freeze
    was going to lift, and the branch could not merge.

    The check does NOT sit in the diff, so the pre-filter cannot see it, and it
    DOES exist at the merge base, so the added-by-this-branch guard cannot
    either. Only the collection globs tell the two situations apart.
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, base_subject="bad")            # the check is red
        commit_ci_sh(repo, ["check-*.py"], {"sub/check.py": CHECK_PY})
        # The branch widens collection and touches nothing else. sub/check.py is
        # unchanged and present at the merge base; what changed is that anything
        # runs it.
        branch(repo, "widens-the-collector", {"unrelated.txt": "x\n"})
        commit_ci_sh(repo, ["check-*.py sub/check*.py"])
        rc, out = ask(repo, check_path="sub/check.py")
        check("a check only this branch collects is never attributed to main",
              rc == CANNOT_TELL, f"exit {rc}\n{out}")
        check("the refusal says the branch is what makes the check run",
              "this branch is what makes sub/check.py run" in out, out)
        check("no attribution banner leaks into the newly-collected case",
              "INHERITED FROM MAIN" not in out, out)


def test_a_check_the_merge_base_already_collected_stays_inherited():
    """The guard above must not become a blanket off-switch for ci.sh branches.

    Two shapes it must leave exactly as they were: a check BOTH sides collect
    (ci.sh changed for some unrelated reason), and a check NEITHER side collects
    by glob — which is every check ci.sh invokes by name, hooks/gate-integrity.py
    and the inventory checks among them.
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, base_subject="bad")
        commit_ci_sh(repo, ["check*.py"])                    # the base collects it
        branch(repo, "edits-ci-sh", {"unrelated.txt": "x\n"})
        commit_ci_sh(repo, ["check*.py", "extra-*.py"])      # widened elsewhere
        rc, out = ask(repo)
        check("a check the merge base already collected keeps its verdict",
              rc == INHERITED, f"exit {rc}\n{out}")

    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, base_subject="bad")
        commit_ci_sh(repo, ["ops/*-selftest.py"])            # collects neither side
        branch(repo, "victim", {"unrelated.txt": "x\n"})
        rc, out = ask(repo)
        check("a check ci.sh invokes by name rather than by glob keeps its verdict",
              rc == INHERITED, f"exit {rc}\n{out}")


def test_a_slow_base_run_times_out_into_cannot_tell():
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, base_subject="slow")
        branch(repo, "innocent", {"unrelated.txt": "x\n"})
        rc, out = ask(repo, timeout="2")
        check("a merge-base run that will not finish resolves to cannot-tell",
              rc == CANNOT_TELL, f"exit {rc}\n{out}")
        check("the refusal names the timeout", "did not finish within 2s" in out, out)


def test_on_main_itself_there_is_nothing_to_attribute():
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, base_subject="bad")
        rc, out = ask(repo)                                  # still on main
        check("a run whose HEAD is the merge base attributes nothing",
              rc == CANNOT_TELL, f"exit {rc}\n{out}")
        check("it says why: this run IS the base",
              "this run IS the base" in out, out)


def test_no_base_ref_refuses():
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, base_subject="bad")
        branch(repo, "orphaned", {"unrelated.txt": "x\n"})
        git(repo, "branch", "-m", "main", "trunk", must=True)  # nothing named main, no remote
        rc, out = ask(repo)
        check("with no main to compare against it refuses rather than guesses",
              rc == CANNOT_TELL, f"exit {rc}\n{out}")
        check("the refusal names the missing base",
              "no origin/main or main" in out, out)


def test_the_kill_switch_disables_it_entirely():
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, base_subject="bad")
        branch(repo, "victim", {"unrelated.txt": "x\n"})
        rc, out = ask(repo, env={"CARR_CI_NO_INHERIT_CHECK": "1"})
        check("CARR_CI_NO_INHERIT_CHECK=1 switches the short-circuit off",
              rc == CANNOT_TELL, f"exit {rc}\n{out}")
        check("and says so rather than silently doing nothing",
              "CARR_CI_NO_INHERIT_CHECK=1" in out, out)


def test_it_leaves_no_worktree_behind():
    """A leaked worktree registration blocks the next session's push on this Mac."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, base_subject="bad")
        branch(repo, "victim", {"unrelated.txt": "x\n"})
        rc, _ = ask(repo)
        listed = git(repo, "worktree", "list").stdout.strip().splitlines()
        check("the merge-base tree is removed even on the inherited path",
              rc == INHERITED and len(listed) == 1, f"exit {rc}, worktrees: {listed}")


def main():
    for fn in (test_broken_base_innocent_branch_is_inherited,
               test_clean_base_guilty_branch_is_not_inherited,
               test_a_touched_check_is_left_to_the_branch,
               test_a_check_absent_from_the_merge_base_is_not_inherited,
               test_committing_a_new_check_is_answered_by_the_pre_filter_first,
               test_exit_78_at_the_base_is_not_a_break,
               test_an_unmaterialised_runtime_prerequisite_is_not_a_break,
               test_the_replay_reuses_the_callers_installed_dependencies,
               test_missing_node_modules_replay_is_attribution_unavailable,
               test_missing_venv_replay_is_attribution_unavailable,
               test_a_check_this_branch_newly_collected_is_not_inherited,
               test_a_check_the_merge_base_already_collected_stays_inherited,
               test_a_slow_base_run_times_out_into_cannot_tell,
               test_on_main_itself_there_is_nothing_to_attribute,
               test_no_base_ref_refuses,
               test_the_kill_switch_disables_it_entirely,
               test_it_leaves_no_worktree_behind):
        print(f"\n{fn.__name__}")
        try:
            fn()
        except Exception as exc:  # a crashing case is a failing case, never a skip
            check(f"{fn.__name__} raised", False, repr(exc))
    print(f"\ninherited-from-main-selftest: {len(failures)} failure(s)")
    for f in failures:
        print(f"  - {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
