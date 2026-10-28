#!/usr/bin/env python3
"""test-worktree-sweep.py — the regression set for bin/worktree.sh's --sweep
reap gate and its CREATE PATH FRESHNESS fix (both added 2026-08-14).

WHY THIS FILE EXISTS. Nine concurrent sessions on one machine surfaced two
lifecycle gaps in `./run.sh worktree`:

  STALE BASE: a new worktree branches from LOCAL main, which nothing keeps
  fresh — on a busy machine it lags origin/main, so a fresh worktree's diff
  is against the wrong base and a session misdiagnoses what actually changed.

  NEVER REAPED: nothing ever removes a worktree whose work already landed.
  22 existed live when this was written; 10 had branches already merged into
  origin/main.

Builds a SCRATCH git repo in a tempdir — NEVER carr-system itself — with a
second bare repo standing in for `origin`, so the sweep's three-condition
reap guard (merged into origin/main, clean, idle 48h+) is proven without any
risk to a worktree a real session might be using right now.

bin/worktree.sh resolves its own repo as `$(dirname "$0")/..`, so the trick
that makes it testable against a scratch tree is a real `<scratch>/bin/`
directory holding a SYMLINK to the actual script: invoked from there, `$0`
(and therefore REPO and CANON) resolve inside the scratch repo, so every git
call the script makes lands in the scratch repo and its scratch origin —
never in carr-system. This runs the REAL script, unmodified, not a mock of it.

Nine cases:
  1. merged + clean + idle 48h+          -> reaped
  2. merged + clean but touched recently -> kept, reported active
  3. merged + dirty                      -> kept, reported
  4. unmerged                            -> kept, reported with ahead/behind
  5. worktree directory deleted by hand  -> pruned (gone from `worktree list`)
  6. --dry-run                           -> reports the reap, removes nothing
  7. create path freshness: new worktree branches from origin/main (fetched),
     not stale local main; --from still overrides
  8. cherry predicate (2026-08-14): a branch whose commit was landed on
     origin/main from a SEPARATE clone (never merged/rebased/pushed from the
     branch itself) is patch-equivalent but NOT an ancestor — the shape a
     squash or rebase merge leaves behind. Clean + idle -> still reaped,
     via `git cherry origin/main <branch>` rather than ancestry.
  9. static check: the removal/sweep code path (REMOVAL PLUMBING through the
     end of the --sweep case) contains no --force, -f, or -D token — a reap
     that runs unattended must never carry a flag that overrides the dirty
     refusal.

Run: .venv/bin/python tools/test-worktree-sweep.py   # exit 0 = all pass
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WORKTREE_SH = os.path.join(REPO, "bin", "worktree.sh")

# The one scrubber (ops/git_env.py) — lives in ops/, not tools/, so ops/ goes
# on sys.path instead of this file's own directory. GIT_DIR is exported by
# every git hook and overrides cwd for any child git process; bin/worktree.sh
# itself shells out to git repeatedly, so an inherited GIT_DIR would retarget
# its calls exactly as it did the fixture commits that landed on live main on
# 2026-08-14.
sys.path.insert(0, os.path.join(REPO, "ops"))
from git_env import fixture_env  # noqa: E402

results: list[tuple[str, bool, str]] = []


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(
    detail="  — {detail}",
    record=lambda label, ok, detail: results.append((label, ok, detail)),
)


def run(cmd, cwd, **kw):
    kw.setdefault("env", fixture_env())
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60, **kw)


def git(args, cwd):
    p = run(["git"] + args, cwd)
    assert p.returncode == 0, f"git {args} failed in {cwd}:\n{p.stdout}\n{p.stderr}"
    return p.stdout


class Scratch:
    """One throwaway git repo + a bare 'origin' + a worktree.sh symlink that
    resolves bin/worktree.sh's own REPO/CANON to THIS tree, never carr-system."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.origin = os.path.join(tmp, "origin.git")
        self.repo = os.path.join(tmp, "repo")
        os.makedirs(self.repo)
        git(["init", "--bare", "-q", "-b", "main", self.origin], tmp)
        git(["init", "-q", "-b", "main", self.repo], tmp)
        git(["config", "user.email", "t@example.com"], self.repo)
        git(["config", "user.name", "t"], self.repo)
        with open(os.path.join(self.repo, "README.md"), "w") as f:
            f.write("scratch\n")
        git(["add", "README.md"], self.repo)
        git(["commit", "-q", "-m", "init"], self.repo)
        git(["remote", "add", "origin", self.origin], self.repo)
        git(["push", "-q", "-u", "origin", "main"], self.repo)

        os.makedirs(os.path.join(self.repo, "bin"))
        os.symlink(WORKTREE_SH, os.path.join(self.repo, "bin", "worktree.sh"))

    def worktree_sh(self, *args):
        return run(["zsh", os.path.join(self.repo, "bin", "worktree.sh"), *args], self.repo)

    def worktree_list(self):
        out = git(["worktree", "list", "--porcelain"], self.repo)
        return {ln.split(" ", 1)[1] for ln in out.splitlines() if ln.startswith("worktree ")}

    def make_branch_worktree(self, name, *, merged=True, commit=True):
        """Create a worktree on a new branch via the script itself (this
        also exercises the create path), optionally add one commit and push
        it to origin so the branch counts as merged."""
        p = self.worktree_sh(name)
        assert p.returncode == 0, f"worktree create failed:\n{p.stdout}\n{p.stderr}"
        wt = os.path.join(self.repo, ".claude", "worktrees", name)
        if commit:
            with open(os.path.join(wt, f"{name}.txt"), "w") as f:
                f.write(name + "\n")
            git(["add", f"{name}.txt"], wt)
            git(["commit", "-q", "-m", f"work on {name}"], wt)
            if merged:
                git(["push", "-q", "origin", f"{name}:main"], wt)
                git(["fetch", "-q", "origin"], self.repo)
        return wt

    def age_files(self, wt, hours_ago):
        """Backdate every real file's mtime (skips symlinks — the dangling
        .venv/out plumbing symlinks a scratch CANON produces have no target
        to stat)."""
        cutoff = time.time() - hours_ago * 3600
        for dirpath, dirnames, filenames in os.walk(wt, followlinks=False):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for f in filenames:
                p = os.path.join(dirpath, f)
                if os.path.islink(p):
                    continue
                try:
                    os.utime(p, (cutoff, cutoff))
                except OSError:
                    pass

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def with_scratch(fn):
    """Run fn(scratch) in its own tempdir, always cleaned up."""
    # realpath: on macOS /tmp (and /var/folders, where mkdtemp actually
    # lands) is itself a symlink to /private/..., and git registers a
    # worktree's REAL path in `worktree list --porcelain`. Comparing against
    # the un-resolved tmpdir path would spuriously "fail" every membership
    # check below regardless of anything bin/worktree.sh does.
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="wt-sweep-test-"))
    s = Scratch(tmp)
    try:
        fn(s)
    finally:
        s.cleanup()


# ── 1. merged + clean + idle 48h+  ->  reaped ────────────────────────────
def test_reap_merged_clean_idle():
    print("\n[1] merged + clean + idle 48h+ -> reaped")

    def body(s):
        wt = s.make_branch_worktree("wt-reap-me")
        s.age_files(wt, hours_ago=72)
        p = s.worktree_sh("--sweep")
        check("sweep exits 0", p.returncode == 0, p.stdout + p.stderr)
        check("directory removed from disk", not os.path.exists(wt))
        check("gone from git worktree list", wt not in s.worktree_list())
        check("summary reports 1 reaped",
              "1 reaped" in p.stdout, p.stdout)

    with_scratch(body)


# ── 2. merged + clean + fresh mtime  ->  kept, reported active ──────────
def test_keep_merged_clean_fresh():
    print("\n[2] merged + clean but touched recently -> kept, reported active")

    def body(s):
        wt = s.make_branch_worktree("wt-fresh")
        # no aging: files keep their just-now mtime
        p = s.worktree_sh("--sweep")
        check("sweep exits 0", p.returncode == 0, p.stdout + p.stderr)
        check("directory still present", os.path.exists(wt))
        check("still registered", wt in s.worktree_list())
        check("reported active in the report", "active" in p.stdout, p.stdout)
        check("summary reports 0 reaped", "0 reaped" in p.stdout, p.stdout)

    with_scratch(body)


# ── 3. merged + dirty  ->  kept, reported ────────────────────────────────
def test_keep_merged_dirty():
    print("\n[3] merged + dirty -> kept, reported")

    def body(s):
        wt = s.make_branch_worktree("wt-dirty")
        # uncommitted change, never staged/committed
        with open(os.path.join(wt, "scratch-note.txt"), "w") as f:
            f.write("uncommitted\n")
        p = s.worktree_sh("--sweep")
        check("sweep exits 0", p.returncode == 0, p.stdout + p.stderr)
        check("directory still present", os.path.exists(wt))
        check("still registered", wt in s.worktree_list())
        check("reported dirty in the report", "dirty" in p.stdout, p.stdout)
        check("dirty count is nonzero in the report",
              "dirty=0" not in p.stdout, p.stdout)

    with_scratch(body)


# ── 4. unmerged  ->  kept, reported with ahead/behind ────────────────────
def test_keep_unmerged():
    print("\n[4] unmerged -> kept, reported with ahead/behind")

    def body(s):
        wt = s.make_branch_worktree("wt-unmerged", merged=False)
        s.age_files(wt, hours_ago=72)  # unmerged must outrank the idle guard
        p = s.worktree_sh("--sweep")
        check("sweep exits 0", p.returncode == 0, p.stdout + p.stderr)
        check("directory still present", os.path.exists(wt))
        check("still registered", wt in s.worktree_list())
        check("reported unmerged in the report", "unmerged" in p.stdout, p.stdout)

    with_scratch(body)


# ── 5. worktree directory deleted from disk  ->  pruned ─────────────────
def test_prune_deleted_directory():
    print("\n[5] worktree directory deleted from disk -> pruned")

    def body(s):
        wt = s.make_branch_worktree("wt-gone")
        check("worktree registered before deletion", wt in s.worktree_list())
        shutil.rmtree(wt)
        p = s.worktree_sh("--sweep")
        check("sweep exits 0", p.returncode == 0, p.stdout + p.stderr)
        check("prune cleared the registration", wt not in s.worktree_list())

    with_scratch(body)


# ── 6. --dry-run  ->  reports the reap, removes nothing ─────────────────
def test_dry_run_removes_nothing():
    print("\n[6] --dry-run reports the reap, removes nothing")

    def body(s):
        wt = s.make_branch_worktree("wt-dryrun")
        s.age_files(wt, hours_ago=72)
        p = s.worktree_sh("--sweep", "--dry-run")
        check("sweep exits 0", p.returncode == 0, p.stdout + p.stderr)
        check("reports 'would remove'", "would remove" in p.stdout, p.stdout)
        check("directory NOT removed", os.path.exists(wt))
        check("still registered", wt in s.worktree_list())
        check("summary reports 1 reaped even though nothing was removed",
              "1 reaped" in p.stdout, p.stdout)
        # And a second, REAL sweep afterward must still be able to reap it —
        # proving --dry-run truly touched nothing that would block a real one.
        p2 = s.worktree_sh("--sweep")
        check("a real sweep after the dry-run still reaps it",
              not os.path.exists(wt), p2.stdout + p2.stderr)

    with_scratch(body)


# ── 7. create path freshness ─────────────────────────────────────────────
def test_create_path_freshness():
    print("\n[7] create path: new worktree branches from origin/main, not stale local main")

    def body(s):
        # Advance origin/main from a SECOND clone, without ever fetching it
        # into s.repo — s.repo's local main is now demonstrably stale.
        second = os.path.join(s.tmp, "second-clone")
        git(["clone", "-q", s.origin, second], s.tmp)
        git(["config", "user.email", "t@example.com"], second)
        git(["config", "user.name", "t"], second)
        with open(os.path.join(second, "marker.txt"), "w") as f:
            f.write("origin moved on\n")
        git(["add", "marker.txt"], second)
        git(["commit", "-q", "-m", "advance origin main"], second)
        git(["push", "-q", "origin", "main"], second)

        check("local main is provably stale (no marker yet)",
              not os.path.exists(os.path.join(s.repo, "marker.txt")))

        # Default base: must fetch and branch from origin/main -> has the marker.
        p = s.worktree_sh("wt-default-base")
        check("create (default base) exits 0", p.returncode == 0, p.stdout + p.stderr)
        default_wt = os.path.join(s.repo, ".claude", "worktrees", "wt-default-base")
        check("branched from origin/main: marker.txt present",
              os.path.exists(os.path.join(default_wt, "marker.txt")))
        check("says it branched from origin/main", "from origin/main" in p.stdout, p.stdout)

        # --from override must still win outright: explicit stale local main.
        p2 = s.worktree_sh("wt-explicit-base", "--from", "main")
        check("create (--from main) exits 0", p2.returncode == 0, p2.stdout + p2.stderr)
        explicit_wt = os.path.join(s.repo, ".claude", "worktrees", "wt-explicit-base")
        check("--from main is honored: no marker.txt",
              not os.path.exists(os.path.join(explicit_wt, "marker.txt")))
        check("says it branched from main", "branched wt-explicit-base from main" in p2.stdout, p2.stdout)

    with_scratch(body)


# ── 8. cherry predicate: patch-equivalent, not an ancestor -> reaped ────
def test_reap_cherry_picked_patch_equivalent():
    print("\n[8] cherry-picked onto origin/main (patch-equivalent, not ancestor) "
          "+ clean + idle -> reaped via the cherry predicate")

    def body(s):
        # A normal branch commit, never pushed/merged from the branch itself.
        wt = s.make_branch_worktree("wt-cherry", merged=False)

        # Land the IDENTICAL patch on origin/main from a separate clone, as
        # its own fresh commit. Same content -> same patch-id; different
        # parent/message/author -> a different sha and NOT an ancestor of
        # the branch's commit or vice versa. This is what a squash or
        # rebase merge looks like from the original branch's point of view.
        lander = os.path.join(s.tmp, "wt-cherry-lander")
        git(["clone", "-q", s.origin, lander], s.tmp)
        git(["config", "user.email", "t@example.com"], lander)
        git(["config", "user.name", "t"], lander)
        shutil.copy(os.path.join(wt, "wt-cherry.txt"),
                    os.path.join(lander, "wt-cherry.txt"))
        git(["add", "wt-cherry.txt"], lander)
        git(["commit", "-q", "-m", "land wt-cherry (squashed elsewhere)"], lander)
        git(["push", "-q", "origin", "main"], lander)

        # Sanity check on the test fixture itself, not on worktree.sh: prove
        # this really does exercise the NEW predicate and not the old
        # ancestry one.
        git(["fetch", "-q", "origin"], s.repo)
        p_ancestor = run(["git", "merge-base", "--is-ancestor",
                           "wt-cherry", "origin/main"], s.repo)
        check("fixture sanity: branch tip is NOT an ancestor of origin/main",
              p_ancestor.returncode != 0)

        s.age_files(wt, hours_ago=72)
        p = s.worktree_sh("--sweep")
        check("sweep exits 0", p.returncode == 0, p.stdout + p.stderr)
        check("directory removed from disk", not os.path.exists(wt))
        check("gone from git worktree list", wt not in s.worktree_list())
        check("summary reports 1 reaped", "1 reaped" in p.stdout, p.stdout)
        check("reap line credits the cherry predicate, not ancestry",
              "cherry-detected" in p.stdout, p.stdout)

    with_scratch(body)


# ── 9. static check: no force flag in the removal/sweep code path ───────
def test_removal_code_has_no_force_flags():
    print("\n[9] static check: the sweep/remove code path carries no "
          "--force, -f, or -D token")

    with open(WORKTREE_SH) as f:
        text = f.read()

    start_marker = "# REMOVAL PLUMBING — shared by --remove and --sweep"
    start_idx = text.index(start_marker)
    # The scope is everything from the removal plumbing through the end of
    # the --sweep case body — the code an automated reap actually runs.
    # The top-level `case "$1" in ... esac` closes right after --sweep, and
    # the create-path logic (branch/base handling) begins after it, so the
    # closing `esac` is the natural right edge of "the removal/sweep code".
    esac_idx = text.index("\nesac", start_idx)
    region = text[start_idx:esac_idx]

    tokens = region.split()
    forbidden = {"-f", "-D", "--force"}
    hits = sorted(set(t for t in tokens if t in forbidden))
    check("no --force/-f/-D token appears in the removal/sweep code",
          not hits, f"found: {hits}")


test_reap_merged_clean_idle()
test_keep_merged_clean_fresh()
test_keep_merged_dirty()
test_keep_unmerged()
test_prune_deleted_directory()
test_dry_run_removes_nothing()
test_create_path_freshness()
test_reap_cherry_picked_patch_equivalent()
test_removal_code_has_no_force_flags()

fails = [(label, detail) for label, ok, detail in results if not ok]
total = len(results)
if fails:
    print(f"\nFAIL — {len(fails)} of {total} checks failed\n")
    for label, detail in fails:
        print(f"  {label}" + (f"  — {detail}" if detail else ""))
    sys.exit(1)
print(f"\nok — {total}/{total} worktree-sweep checks pass")
sys.exit(0)
