#!/usr/bin/env python3
"""Fixtures for ops/migration-order-gate.py.

The gate refuses a change whose ADDED migration numbers are not strictly above
the highest migration number already on the base branch. These cases pin the
shapes that broke the worker release three times on 2026-10-02/03 (0757, the
0769/0770 pair, then 0783): a branch minted its number, main moved past it, and
the branch merged anyway. The staging candidate and production had already
applied the higher numbers, so the source ledger stopped being an exact prefix
of what was applied and staging-prepare refused every release after it.

They also pin the two ways a check like this gets switched off: firing on files
the branch did not add (a stale branch carrying main's own files), and passing
silently when it cannot read the base.

Run: .venv/bin/python ops/migration-order-gate-selftest.py
"""
import importlib.util
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
GATE = REPO / "ops" / "migration-order-gate.py"

# The one scrubber: git hands every hook a GIT_DIR pointing at the invoking
# repository, and a fixture commit must never be able to land there.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402

spec = importlib.util.spec_from_file_location("mog", GATE)
assert spec is not None and spec.loader is not None, f"cannot load {GATE}"
mog: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mog)

passed: int = 0
failures: list[str] = []


def check(name: str, cond: object, detail: str = "") -> None:
    global passed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


# ── the pure decision ──────────────────────────────────────────────────────
print("decision")
base = {"migrations/0785_a.sql", "migrations/0786_b.sql", "migrations/0787_c.sql",
        "migrations/README.md"}

v = mog.violations(base, base, base | {"migrations/0788_new.sql"})
check("an addition above the base maximum passes", v == [], repr(v))

v = mog.violations(base, base, base | {"migrations/0783_deal_timeline_lease_read.sql"})
check("the 2026-10-03 incident (0783 under 0787) is refused",
      v == ["migrations/0783_deal_timeline_lease_read.sql"], repr(v))

v = mog.violations(base, base, base | {"migrations/0787_other.sql"})
check("an addition EQUAL to the base maximum is refused (strictly greater)",
      v == ["migrations/0787_other.sql"], repr(v))

renamed = (base - {"migrations/0787_c.sql"}) | {"migrations/0787_c.sql"}
v = mog.violations(base, base, renamed | {"migrations/0790_x.sql", "migrations/0791_y.sql"})
check("several additions all above the maximum pass", v == [], repr(v))

v = mog.violations(base, base, base | {"migrations/0786a_interstitial.sql"})
check("a lettered interstitial slot below the maximum is refused",
      v == ["migrations/0786a_interstitial.sql"], repr(v))

v = mog.violations(base | {"migrations/0790a_x.sql"}, base | {"migrations/0790a_x.sql"},
                   base | {"migrations/0790a_x.sql", "migrations/0790_y.sql"})
check("a lettered slot on the base raises the maximum to its number",
      v == ["migrations/0790_y.sql"], repr(v))

v = mog.violations(base, base, base)
check("a change adding no migration passes", v == [], repr(v))

v = mog.violations(base, base, base | {"migrations/notes.md", "migrations/0100_x.txt"})
check("non-migration files under migrations/ are ignored", v == [], repr(v))

v = mog.violations(base, base, base - {"migrations/0786_b.sql"})
check("a deletion is not this gate's concern", v == [], repr(v))

dup = base | {"migrations/0169_one.sql", "migrations/0169_two.sql"}
v = mog.violations(dup, dup, dup | {"migrations/0800_z.sql"})
check("frozen historical duplicate prefixes on the base do not fire", v == [], repr(v))

v = mog.violations(set(), set(), {"migrations/0001_init.sql"})
check("an empty base admits any first migration", v == [], repr(v))

# A branch merely behind a main that renamed or deleted a migration after the
# fork still carries the old file. It never added it, so it must not count —
# the 2026-10-04 review replay (--base 5afd700b --head 179741a1) refused 0783.
fork = base | {"migrations/0783_deal_timeline_lease_read.sql"}
moved = base | {"migrations/0800_deal_timeline_lease_read.sql"}
v = mog.violations(moved, fork, fork)
check("main renaming a migration after the fork does not fire on a stale branch",
      v == [], repr(v))
v = mog.violations(base - {"migrations/0786_b.sql"}, base, base)
check("main deleting a migration after the fork does not fire on a stale branch",
      v == [], repr(v))
v = mog.violations(moved, fork, fork | {"migrations/0788_mine.sql"})
check("a stale branch's own addition is still judged against the base maximum",
      v == ["migrations/0788_mine.sql"], repr(v))

check("the reported maximum is the base maximum",
      mog.highest_number(base) == 787, repr(mog.highest_number(base)))


# ── real git trees, including the merge-ref shape a pull_request run builds ──
print("git fixtures")
env = fixture_env()
env.update({"GIT_AUTHOR_NAME": "selftest", "GIT_AUTHOR_EMAIL": "selftest@example.invalid",
            "GIT_COMMITTER_NAME": "selftest", "GIT_COMMITTER_EMAIL": "selftest@example.invalid"})


def git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True,
                          capture_output=True, text=True).stdout


def add(repo: pathlib.Path, name: str, message: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"-- {name}\n")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", message)


def run_gate(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    gate_env = dict(env)
    gate_env.pop("GITHUB_BASE_REF", None)
    return subprocess.run([sys.executable, str(GATE), "--repo", str(repo), *args],
                          env=gate_env, capture_output=True, text=True)


with tempfile.TemporaryDirectory() as tmp:
    repo = pathlib.Path(tmp) / "r"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    add(repo, "migrations/0785_a.sql", "a")
    add(repo, "migrations/0786_b.sql", "b")

    # A branch mints 0787 while main is at 0786 — correct at mint time.
    git(repo, "checkout", "-q", "-b", "feature")
    add(repo, "migrations/0787_feature.sql", "feature")
    r = run_gate(repo, "--base", "main", "--head", "feature")
    check("a branch ahead of main passes", r.returncode == 0, r.stdout + r.stderr)

    # Main moves past it: another PR lands 0788 and 0789.
    git(repo, "checkout", "-q", "main")
    add(repo, "migrations/0788_other.sql", "other")
    add(repo, "migrations/0789_other.sql", "other2")

    r = run_gate(repo, "--base", "main", "--head", "feature")
    check("the same branch, now behind main, fails", r.returncode == 1, r.stdout + r.stderr)
    check("the refusal names the offending file",
          "0787_feature.sql" in r.stdout + r.stderr, r.stdout + r.stderr)
    check("the refusal names the base maximum", "0789" in r.stdout + r.stderr, r.stdout + r.stderr)
    check("the refusal names the move (reserve-migration)",
          "reserve-migration" in r.stdout + r.stderr, r.stdout + r.stderr)

    # The update-branch path: main merged INTO the branch, exactly what the
    # merge queue's update-branch call and the pull_request merge ref build.
    git(repo, "checkout", "-q", "feature")
    git(repo, "merge", "-q", "--no-edit", "main")
    r = run_gate(repo, "--base", "main", "--head", "HEAD")
    check("after update-branch the stale number still fails", r.returncode == 1,
          r.stdout + r.stderr)
    r = run_gate(repo, "--base", "main")
    check("--head defaults to HEAD", r.returncode == 1, r.stdout + r.stderr)

    # The fix forward: renumber above the new maximum.
    git(repo, "mv", "migrations/0787_feature.sql", "migrations/0790_feature.sql")
    git(repo, "commit", "-q", "-m", "renumber")
    r = run_gate(repo, "--base", "main")
    check("the renumbered branch passes", r.returncode == 0, r.stdout + r.stderr)

    # A run on main itself (the scheduled run) adds nothing.
    git(repo, "checkout", "-q", "main")
    r = run_gate(repo, "--base", "main")
    check("a run on the base itself passes", r.returncode == 0, r.stdout + r.stderr)

    # A branch that forked, added nothing, and fell behind a main that renamed
    # one migration and deleted another. HEAD is the branch itself, not a merge
    # ref — the local `ops/ci.sh --only gates` and workflow_dispatch shape.
    git(repo, "checkout", "-q", "-b", "stale")
    git(repo, "checkout", "-q", "main")
    git(repo, "mv", "migrations/0788_other.sql", "migrations/0791_other.sql")
    git(repo, "commit", "-q", "-m", "renumber on main")
    r = run_gate(repo, "--base", "main", "--head", "stale")
    check("a stale branch behind a main-side rename passes", r.returncode == 0,
          r.stdout + r.stderr)
    git(repo, "rm", "-q", "migrations/0785_a.sql")
    git(repo, "commit", "-q", "-m", "delete on main")
    r = run_gate(repo, "--base", "main", "--head", "stale")
    check("a stale branch behind a main-side deletion passes", r.returncode == 0,
          r.stdout + r.stderr)
    git(repo, "checkout", "-q", "stale")
    add(repo, "migrations/0790_mine.sql", "stale branch mints under main's 0791")
    r = run_gate(repo, "--base", "main", "--head", "stale")
    check("the stale branch's own number under main's maximum still fails",
          r.returncode == 1 and "0790_mine.sql" in r.stderr
          and "0788_other.sql" not in r.stderr, r.stdout + r.stderr)
    git(repo, "checkout", "-q", "main")

    # A base that cannot be read is NOT a pass.
    r = run_gate(repo, "--base", "origin/does-not-exist")
    check("an unreadable base exits 2, never 0", r.returncode == 2, r.stdout + r.stderr)

    # GITHUB_BASE_REF selects origin/<base> in a pull_request run.
    check("GITHUB_BASE_REF picks origin/<ref>",
          mog.default_base({"GITHUB_BASE_REF": "release"}) == "origin/release")
    check("no GITHUB_BASE_REF defaults to origin/main",
          mog.default_base({}) == "origin/main")
    check("an empty GITHUB_BASE_REF defaults to origin/main",
          mog.default_base({"GITHUB_BASE_REF": ""}) == "origin/main")


# ── the wiring: a gate nothing runs is decoration ──────────────────────────
print("wiring")
ci_sh = (REPO / "ops" / "ci.sh").read_text()
inventory = ci_sh.split("for inv in ", 1)[1].split("; do", 1)[0] if "for inv in " in ci_sh else ""
check("ops/ci.sh's gates inventory loop runs migration-order-gate",
      "migration-order-gate" in inventory.split(), inventory[-200:])
ci_yml = (REPO / ".github" / "workflows" / "ci.yml").read_text()
pr_types = ci_yml.split("pull_request:", 1)[1].split("\n", 3)[1] if "pull_request:" in ci_yml else ""
check("CI re-runs on synchronize (the update-branch re-check path)",
      "synchronize" in pr_types, pr_types)
check("the gates class runs in a CI matrix group",
      re.search(r'-\s*"[^"\n]*\bgates\b[^"\n]*"', ci_yml) is not None)


# A forward number can still collide with another pending file in the same PR.
with tempfile.TemporaryDirectory() as tmp:
    repo = pathlib.Path(tmp) / "collision"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    add(repo, "migrations/0800_base.sql", "base")
    git(repo, "checkout", "-q", "-b", "feature")
    add(repo, "migrations/0801_first.sql", "first")
    add(repo, "migrations/0801_second.sql", "second")
    r = run_gate(repo, "--base", "main")
    check("a forward number collision refuses before any database work",
          r.returncode == 1 and "unregistered collision 0801" in r.stderr,
          r.stdout + r.stderr)

print(f"\n{passed} passed, {len(failures)} failed")
if failures:
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
