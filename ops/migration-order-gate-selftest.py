#!/usr/bin/env python3
"""Exercise free migration slots, reverse merge order, and collision refusals.

Real Git fixtures cover stale branches and merge-group bases. Unreadable refs
and incomplete events must fail closed before database work.
"""
import importlib.util
import json
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


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(failures=failures, counters={"passed": "pass"}, ns=globals())


# ── the pure decision ──────────────────────────────────────────────────────
print("decision")
base = {"migrations/0785_a.sql", "migrations/0786_b.sql", "migrations/0787_c.sql",
        "migrations/README.md"}

v = mog.violations(base, base, base | {"migrations/0788_new.sql"})
check("an addition above the base maximum passes", v == [], repr(v))

v = mog.violations(base, base, base | {"migrations/0783_deal_timeline_lease_read.sql"})
check("a free number below the base maximum passes",
      v == [], repr(v))

v = mog.violations(base, base, base | {"migrations/0787_other.sql"})
check("an addition that reuses the base maximum slot is refused",
      v == ["migrations/0787_other.sql"], repr(v))

renamed = (base - {"migrations/0787_c.sql"}) | {"migrations/0787_c.sql"}
v = mog.violations(base, base, renamed | {"migrations/0790_x.sql", "migrations/0791_y.sql"})
check("several additions all above the maximum pass", v == [], repr(v))

v = mog.violations(base, base, base | {"migrations/0786a_interstitial.sql"})
check("a lettered addition that reuses an occupied slot is refused",
      v == ["migrations/0786a_interstitial.sql"], repr(v))

v = mog.violations(base | {"migrations/0790a_x.sql"}, base | {"migrations/0790a_x.sql"},
                   base | {"migrations/0790a_x.sql", "migrations/0790_y.sql"})
check("a lettered base migration owns its numeric slot",
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
check("a stale branch keeps its unoccupied number below the base maximum",
      v == [], repr(v))




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


def run_gate(repo: pathlib.Path, *args: str,
             event: dict | None = None) -> subprocess.CompletedProcess:
    gate_env = dict(env)
    for key in ("GITHUB_BASE_REF", "GITHUB_EVENT_NAME", "GITHUB_EVENT_PATH"):
        gate_env.pop(key, None)
    with tempfile.TemporaryDirectory() as tmp:
        if event is not None:
            path = pathlib.Path(tmp) / "event.json"
            path.write_text(json.dumps(event["payload"]))
            gate_env.update(GITHUB_EVENT_NAME=event["name"], GITHUB_EVENT_PATH=str(path))
        return subprocess.run([sys.executable, str(GATE), "--repo", str(repo), *args],
                              env=gate_env, capture_output=True, text=True)


with tempfile.TemporaryDirectory() as tmp:
    repo = pathlib.Path(tmp) / "r"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    add(repo, "migrations/0785_a.sql", "a")
    add(repo, "migrations/0786_b.sql", "b")

    git(repo, "checkout", "-q", "-b", "feature")
    add(repo, "migrations/0787_feature.sql", "feature")
    r = run_gate(repo, "--base", "main", "--head", "feature")
    check("a branch ahead of main passes", r.returncode == 0, r.stdout + r.stderr)

    git(repo, "checkout", "-q", "main")
    add(repo, "migrations/0788_other.sql", "other")
    add(repo, "migrations/0789_other.sql", "other2")

    r = run_gate(repo, "--base", "main", "--head", "feature")
    check("the same branch, now behind main, passes", r.returncode == 0, r.stdout + r.stderr)
    # The update-branch path: main merged INTO the branch, exactly what the
    # merge queue's update-branch call and the pull_request merge ref build.
    git(repo, "checkout", "-q", "feature")
    git(repo, "merge", "-q", "--no-edit", "main")
    r = run_gate(repo, "--base", "main", "--head", "HEAD")
    check("after update-branch the unoccupied number still passes", r.returncode == 0,
          r.stdout + r.stderr)
    r = run_gate(repo, "--base", "main")
    check("--head defaults to HEAD", r.returncode == 0, r.stdout + r.stderr)

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
    check("the stale branch keeps its free number under main's maximum",
          r.returncode == 0, r.stdout + r.stderr)
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
    check("a merge_group run takes the group's base_sha, not origin/main",
          mog.default_base({"GITHUB_EVENT_NAME": "merge_group"},
                           {"merge_group": {"base_sha": "abc123"}}) == "abc123")


# The lower migration enters the queue after a higher-numbered group. The
# origin/main ref still lags both groups.
print("merge-queue replay (#2560/#2503)")
with tempfile.TemporaryDirectory() as tmp:
    repo = pathlib.Path(tmp) / "queue"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    for name in ("1015_team_memory_store", "1016_list_client_portals", "1017_work_request_queue"):
        add(repo, f"migrations/{name}.sql", name)
    git(repo, "update-ref", "refs/remotes/origin/main", "main")

    git(repo, "checkout", "-q", "-b", "pr-2503", "main")
    for name in ("1020_email_deal_matching", "1022_email_send_result_time",
                 "1023_email_thread_filing_events"):
        add(repo, f"migrations/{name}.sql", name)
    git(repo, "checkout", "-q", "-b", "pr-2560", "main")
    add(repo, "migrations/1018_work_request_job_start.sql", "#2560")

    # The queue: group 1 = main + #2503, group 2 = group 1 + #2560.
    git(repo, "checkout", "-q", "--detach", "main")
    git(repo, "merge", "-q", "--no-ff", "--no-edit", "pr-2503")
    group1 = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "merge", "-q", "--no-ff", "--no-edit", "pr-2560")
    group2 = git(repo, "rev-parse", "HEAD").strip()

    def queue_event(base_sha: str, head_sha: str) -> dict:
        return {"name": "merge_group", "payload": {"merge_group": {
            "base_sha": base_sha, "head_sha": head_sha, "base_ref": "refs/heads/main"}}}

    r = run_gate(repo, event=queue_event(group1, group2))
    out = r.stdout + r.stderr
    check("merge group: lower number merged after higher number passes", r.returncode == 0, out)
    r = run_gate(repo, event=queue_event(git(repo, "rev-parse", "main").strip(), group1))
    check("merge group: #2503's own group (1020..1023 over 1017) passes",
          r.returncode == 0, r.stdout + r.stderr)

    git(repo, "checkout", "-q", "-b", "collision", "main")
    add(repo, "migrations/1020_conflict.sql", "collides with group one")
    r = run_gate(repo, "--head", "collision", event=queue_event(group1, group2))
    check("merge group: a slot held by an earlier group refuses",
          r.returncode == 1 and "1020_conflict.sql" in r.stderr, r.stdout + r.stderr)

    r = run_gate(repo, event={"name": "merge_group", "payload": {}})
    check("merge group: an event without base_sha exits 2, never 0",
          r.returncode == 2, r.stdout + r.stderr)


# ── the wiring: a gate nothing runs is decoration ──────────────────────────
print("wiring")
spec = importlib.util.spec_from_file_location("ci_gates", REPO / "ops/ci-gates.py")
assert spec is not None and spec.loader is not None
gates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gates)
check("gates inventory runs migration-order-gate", "migration-order-gate" in gates.INVARIANTS)
ci_yml = (REPO / ".github" / "workflows" / "ci.yml").read_text()
pr_types = ci_yml.split("pull_request:", 1)[1].split("\n", 3)[1] if "pull_request:" in ci_yml else ""
check("CI re-runs on synchronize (the update-branch re-check path)",
      "synchronize" in pr_types, pr_types)
check("CI runs on merge_group (the merge-queue re-check path)",
      re.search(r"^  merge_group:", ci_yml, re.MULTILINE) is not None)
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
