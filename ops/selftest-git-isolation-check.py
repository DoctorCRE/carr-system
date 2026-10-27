#!/usr/bin/env python3
"""selftest-git-isolation-check.py — every git-fixture selftest scrubs the
inherited git environment, and builds its fixture outside the checkout.

WHY THIS EXISTS — the bug it pins destroyed local main on 2026-08-14.

Git exports GIT_DIR (and usually GIT_INDEX_FILE) into every hook it runs, and
those variables OVERRIDE cwd for any child `git` process. ops/githooks/pre-push
ran ops/ci.sh directly, so CI inherited them, and every selftest that builds a
throwaway repository with tempfile.mkdtemp and calls git with cwd=<temp> was
silently operating on the LIVE repository instead. Local main ended up six
commits ahead of origin on a tree of THREE files, five of the commits named
"seed", with all 818 paths staged as additions — so any commit from any session
would have swept the whole repo under one message.

TWO FIXES ALREADY LANDED, AND NEITHER IS THIS ONE. ops/githooks/pre-push strips
the variables before invoking CI (the outer layer), and ops/git_env.py exists so
a selftest can scrub for itself (the inner layer). ops/git-env-selftest.py proves
git_env.py actually confines git, and ops/hook-env-isolation-selftest.py proves
the hook still strips.

WHAT NOTHING CHECKED IS WHETHER THE INNER LAYER IS ACTUALLY WIRED UP. Measured
2026-08-23 for council recommendation 1: of 27 selftests that build a git
fixture, 4 used git_env, 7 hand-rolled their own scrub list (two mutually
inconsistent copies, both missing GIT_CEILING_DIRECTORIES and
GIT_WORK_TREE_OVERRIDE), and 16 scrubbed NOTHING AT ALL. The hook fix covers the
push path and only the push path; another git hook, a merge driver,
`git rebase -x`, or a session that simply has GIT_DIR exported all reach the same
selftests without passing through it. So the 2026-08-14 class of bug was still
reproducible in 16 files, and every one of them was green.

That is the shape this repo keeps rediscovering: a control that exists, is
correct, and is not connected to the thing it protects. Prose in a docstring
saying "runs in a temp dir" does not bind — disposability is worthless once
GIT_DIR is exported, because the variable outranks cwd.

WHAT IS ENFORCED, and both halves are cheap and static:

  1. A selftest that CREATES a git repository must import ops/git_env.py. Not
     "must scrub" — must use the ONE scrubber, because rule a8c55a47 says a
     manual path and an automated path doing the same job must be the same code,
     and the seven hand-rolled copies are the evidence for why: each was correct
     enough to pass its own tests and none of them covered the full list.
  2. A fixture directory must not be created INSIDE the checkout. A
     `TemporaryDirectory(dir=ROOT)` puts fixture git state in a child of the live
     tree, where a stray `git add` can reach it.

EXEMPTIONS ARE ANNOUNCED, NEVER SILENT — the same discipline
ops/config/ci-check-scope.json applies to the gates class. Each one prints its
reason on every run, so the coverage this check delivers is visible in its own
output rather than buried where nobody opens it.

WHY STATIC AND NOT A LIVE POISONED-ENVIRONMENT RUN. The live version is
strictly better evidence and was measured before being rejected: re-running the
27 git-fixture selftests under a poisoned GIT_DIR costs 275 seconds. The
per-job billing round-up sits at 300 seconds (round-1 council finding, same
day), so it would roughly double the gates class to prove a property this
check establishes structurally. The live proof still exists where it is cheap —
ops/git-env-selftest.py drives real git against a real victim repository with a
hostile GIT_DIR and asserts the victim is untouched. This check makes sure
everyone is actually calling the thing that file proves.

Exit 0 = every git-fixture selftest is wired to the scrubber. Exit 1 = at least
one is not, and it is named.

    .venv/bin/python ops/selftest-git-isolation-check.py
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The marker for "this file builds a git repository": it runs `git init`. Every
# fixture in this repo starts that way, and a file that only READS git state
# (ops/ci-selftest.py's `git ls-files`) is not what this check is about.
CREATES_REPO = re.compile(r"""["']init["']|\bgit\s+init\b""")
INVOKES_GIT = re.compile(r"""["']git["']""")
USES_SCRUBBER = re.compile(r"\bfrom\s+git_env\s+import\b|\bimport\s+git_env\b")
# A fixture rooted inside the checkout: tempfile.*(dir=ROOT/REPO/...) where the
# directory argument names this repository rather than the system temp area.
FIXTURE_INSIDE_REPO = re.compile(
    r"\b(?:mkdtemp|TemporaryDirectory)\s*\([^)]*\bdir\s*=\s*(ROOT|REPO)\b")

EXEMPT = {
    "hook-env-isolation-selftest.py":
        "DEMONSTRATES the hijack rather than avoiding it: it points a fabricated "
        "GIT_DIR at its own throwaway repo A and asserts a cwd=B call is "
        "captured. Importing the scrubber would delete the thing it proves. It "
        "never names a real tree — both repos are mkdtemp fixtures.",
}


def sources():
    """The executed gate inventory: exactly what ops/ci.sh hands the gates class.

    This used to be its own ops/*-selftest.py + tools/test-*.py walk, which
    missed tools/test_*.py, tools/*-selftest.py, room-bridge and doc-drift
    suites that CI runs (WR-000545). Asking ci.sh removes the second list.
    """
    listed = subprocess.run(["bash", os.path.join(ROOT, "ops", "ci.sh"), "--gate-programs"],
                            cwd=ROOT, capture_output=True, text=True, timeout=60)
    if listed.returncode != 0 or not listed.stdout.strip():
        raise SystemExit("FAIL  selftest-git-isolation: ops/ci.sh --gate-programs returned "
                         f"no inventory (exit {listed.returncode})")
    for rel in listed.stdout.split():
        if rel.endswith(".py"):
            yield rel, os.path.join(ROOT, rel)


def main():
    unscrubbed, inside_repo, checked, exempted = [], [], 0, []

    for rel, path in sources():
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                src = fh.read()
        except OSError:
            continue
        if not (INVOKES_GIT.search(src) and CREATES_REPO.search(src)):
            continue

        base = os.path.basename(path)
        if base in EXEMPT:
            exempted.append((rel, EXEMPT[base]))
            continue

        checked += 1
        if not USES_SCRUBBER.search(src):
            unscrubbed.append(rel)
        if FIXTURE_INSIDE_REPO.search(src):
            inside_repo.append(rel)

    for rel, why in exempted:
        print(f"  not checked  {rel}\n               {why}")

    if not unscrubbed and not inside_repo:
        print(f"ok  selftest-git-isolation: {checked} git-fixture selftest(s) "
              f"import ops/git_env.py and build outside the checkout")
        return 0

    print()
    if unscrubbed:
        print("FAIL  these selftests create a git repository without importing "
              "ops/git_env.py:")
        for rel in unscrubbed:
            print(f"        {rel}")
    if inside_repo:
        print("FAIL  these selftests build their fixture INSIDE the checkout "
              "(dir=ROOT/REPO):")
        for rel in inside_repo:
            print(f"        {rel}")
    print()
    print("  WHY IT MATTERS: git exports GIT_DIR and GIT_INDEX_FILE into every")
    print("  hook, and they OVERRIDE cwd for any child git process. A fixture")
    print("  built with cwd=<tempdir> and no scrub lands in whatever repository")
    print("  GIT_DIR names — which on 2026-08-14 was live main.")
    print()
    print("  THE FIX, three lines:")
    print("      sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))")
    print("      from git_env import fixture_env")
    print("      ENV = fixture_env()      # pass env=ENV to every git subprocess")
    print()
    print("  fixture_env() for THROWAWAY repos (it also refuses to inherit a real")
    print("  git identity); scrubbed_env() for real commits to a known repo.")
    print("  Proof that it confines git: ops/git-env-selftest.py.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
