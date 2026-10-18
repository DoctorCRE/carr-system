#!/usr/bin/env python3
"""ops/pr-actor-selftest.py — the acceptance test for ops/pr_actor.py.

WHAT THE ACTOR IS FOR. ops/pr-hygiene-check.py notices a stranded pull request;
this clears it. Joe's direction on 2026-08-15: "we are actively moving away from
AI running the system and more into code running the system", which is his own
2026-08-13 rule (never spend a model on anything already expressible as a tested
predicate) restated because a session proposed a model-driven scheduled routine
for work that is finitely specifiable.

TWO TIERS, the house pattern.

TIER 1 (always runs, no network, no credential): the DECISION function. Given a
pull request's shape it returns one of close-superseded / rebase / resolve-
generated / report-only, and nothing else. Pure, so this is where the real
coverage lives.

TIER 2 (always runs, but builds throwaway git repositories in a temp dir): the
git MECHANICS, exercised against real repositories rather than mocks — a branch
merely behind main, a branch whose only conflict is a declared generated file,
and a branch with a genuine source conflict that must be refused. No network:
these are local clones, so `push` is a push to a local bare remote.

WHAT IS DELIBERATELY NOT TESTED HERE: the GitHub API calls. They are a thin
shell over `gh`, and asserting on a mocked `gh` would prove only that the mock
was written to match the caller. The actor never closes or pushes without
--execute, and the plan it prints under --dry-run is the reviewable artifact.

RUN IT:
    python3 ops/pr-actor-selftest.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

# pr_actor.py is importable by name (no hyphen), so this is a plain import
# rather than an importlib.util dance. That matters: a module loaded by
# spec_from_file_location is absent from sys.modules, and @dataclass resolves
# its annotations through sys.modules, so the dance fails at import time on
# Python 3.14. The straightforward import has no such hazard.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pr_actor as actor  # noqa: E402

# The one scrubber (ops/git_env.py). git hands every hook a GIT_DIR pointing
# at the repository that invoked it, and on 2026-08-14 that leaked a fixture
# commit onto live main — TIER 2 below drives real bare-remote/clone pairs and
# must not be captured by an inherited GIT_DIR.
from git_env import fixture_env  # noqa: E402

TEST_POLICY = dict(actor.DEFAULT_POLICY, generated_files=["ops/config/fixture-generated.json"])

FAILED: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
        FAILED.append(label)


def git(repo: str, *args: str, check_rc: bool = True) -> str:
    p = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                       env=fixture_env())
    if check_rc and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {repo}:\n{p.stderr}")
    return p.stdout.strip()


# ════════════════════════════════════════════════════════════════════════════
# TIER 1 — the decision function
# ════════════════════════════════════════════════════════════════════════════
print("TIER 1 — the decision, which must never be a judgment call")


def decide(**kw):
    base = dict(number=1, is_draft=False, commits_in_main=False,
                behind=True, conflicts=[], checks="SUCCESS")
    base.update(kw)
    return actor.decide(actor.PrShape(**base), TEST_POLICY)


check("a pull request whose commits are already in main is SUPERSEDED",
      decide(commits_in_main=True).action == "close-superseded")
check("...and that wins even when it also conflicts, because closing it ends the conflict",
      decide(commits_in_main=True, conflicts=["src/a.py"]).action == "close-superseded")

check("a branch merely behind main with no conflicts is a REBASE",
      decide(behind=True, conflicts=[]).action == "rebase")

check("a conflict confined to a declared generated file is RESOLVABLE",
      decide(conflicts=["ops/config/fixture-generated.json"]).action == "resolve-generated")


check("a genuine source conflict is REPORT-ONLY, never guessed at",
      decide(conflicts=["mcp-server/src/tools.js"]).action == "report-only")
check("a MIXED conflict — one generated file and one source file — is report-only",
      decide(conflicts=["ops/config/fixture-generated.json",
                        "mcp-server/src/tools.js"]).action == "report-only",
      "a partial mechanical resolution is the dangerous case; refuse the whole thing")

check("a red pull request is never auto-rebased — a red test may be a real bug",
      decide(checks="FAILURE", conflicts=[]).action == "report-only")
check("a draft is never acted on",
      decide(is_draft=True, commits_in_main=True).action == "report-only")
check("a pull request that is green, current and unconflicted needs NOTHING",
      decide(behind=False, conflicts=[], checks="SUCCESS").action == "none")

check("every decision carries a reason in plain words",
      all(decide(**kw).reason for kw in (
          {"commits_in_main": True}, {"conflicts": ["ops/config/fixture-generated.json"]},
          {"conflicts": ["src/x.py"]}, {"is_draft": True}, {"checks": "FAILURE"})))

# ════════════════════════════════════════════════════════════════════════════
# TIER 2 — the git mechanics, against real repositories
# ════════════════════════════════════════════════════════════════════════════
print("\nTIER 2 — the git mechanics, on throwaway repositories (no network)")

TMP = tempfile.mkdtemp(prefix="pr-actor-selftest-")


def new_repo(name: str) -> tuple[str, str]:
    """A bare 'remote' plus a working clone, with one commit on main."""
    bare = os.path.join(TMP, f"{name}.git")
    work = os.path.join(TMP, name)
    subprocess.run(["git", "init", "--bare", "-b", "main", bare],
                   capture_output=True, check=True, env=fixture_env())
    subprocess.run(["git", "clone", bare, work], capture_output=True, check=True,
                   env=fixture_env())
    git(work, "config", "user.email", "selftest@example.invalid")
    git(work, "config", "user.name", "selftest")
    with open(os.path.join(work, "README.md"), "w") as f:
        f.write("base\n")
    os.makedirs(os.path.join(work, "ops", "config"), exist_ok=True)
    with open(os.path.join(work, "ops", "config", "fixture-generated.json"), "w") as f:
        f.write('{"hashes": {"a.py": "AAA"}}\n')
    git(work, "add", "README.md", "ops/config/fixture-generated.json")
    git(work, "commit", "-m", "base")
    git(work, "push", "origin", "main")
    return bare, work


try:
    # ── a branch merely behind main rebases cleanly and pushes ──────────────
    bare, work = new_repo("behind")
    git(work, "checkout", "-b", "feature")
    with open(os.path.join(work, "feature.txt"), "w") as f:
        f.write("mine\n")
    git(work, "add", "feature.txt")
    git(work, "commit", "-m", "my feature")
    git(work, "push", "origin", "feature")
    git(work, "checkout", "main")
    with open(os.path.join(work, "other.txt"), "w") as f:
        f.write("someone else\n")
    git(work, "add", "other.txt")
    git(work, "commit", "-m", "main moved")
    git(work, "push", "origin", "main")

    res = actor.rebase_branch(work, "feature", "main", policy=TEST_POLICY)
    check("a branch merely behind main rebases cleanly", res.ok, res.detail)
    check("...and the rebased branch carries BOTH the new main commit and its own",
          "main moved" in git(work, "log", "--oneline", "feature") and
          "my feature" in git(work, "log", "--oneline", "feature"))

    # ── a conflict confined to a declared generated file is resolved ────────
    bare2, work2 = new_repo("gen")
    git(work2, "checkout", "-b", "gate-change")
    with open(os.path.join(work2, "ops", "config", "fixture-generated.json"), "w") as f:
        f.write('{"hashes": {"a.py": "BRANCH"}}\n')
    git(work2, "add", "ops/config/fixture-generated.json")
    git(work2, "commit", "-m", "branch blessed the gate")
    git(work2, "checkout", "main")
    with open(os.path.join(work2, "ops", "config", "fixture-generated.json"), "w") as f:
        f.write('{"hashes": {"a.py": "MAIN"}}\n')
    git(work2, "add", "ops/config/fixture-generated.json")
    git(work2, "commit", "-m", "main blessed the gate")

    res2 = actor.rebase_branch(work2, "gate-change", "main", policy=TEST_POLICY)
    check("a conflict confined to a declared generated file resolves", res2.ok, res2.detail)
    with open(os.path.join(work2, "ops", "config", "fixture-generated.json")) as f:
        body = f.read()
    check("...by taking MAIN's copy, never the branch's stale one",
          "MAIN" in body and "BRANCH" not in body,
          f"resolved file reads: {body!r} — taking the branch's copy is the "
          f"lost-update bug that left main shipping a stale gate hash on 2026-08-14")

    # ── a genuine source conflict is refused, and leaves no mess ────────────
    bare3, work3 = new_repo("srcconflict")
    git(work3, "checkout", "-b", "theirs")
    with open(os.path.join(work3, "README.md"), "w") as f:
        f.write("branch version\n")
    git(work3, "add", "README.md")
    git(work3, "commit", "-m", "branch edited the readme")
    git(work3, "checkout", "main")
    with open(os.path.join(work3, "README.md"), "w") as f:
        f.write("main version\n")
    git(work3, "add", "README.md")
    git(work3, "commit", "-m", "main edited the readme")

    res3 = actor.rebase_branch(work3, "theirs", "main", policy=TEST_POLICY)
    check("a genuine source conflict is REFUSED, not guessed at", not res3.ok, res3.detail)
    check("...and the refusal names the conflicting file",
          "README.md" in res3.detail, res3.detail)
    check("...and the repository is left with no rebase in progress",
          not os.path.exists(os.path.join(work3, ".git", "rebase-merge")) and
          not os.path.exists(os.path.join(work3, ".git", "rebase-apply")),
          "a half-finished rebase left behind would strand the next writer")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print()
if FAILED:
    print(f"FAILED {len(FAILED)} check(s):")
    for _label in FAILED:
        print(f"  - {_label}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
