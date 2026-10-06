#!/usr/bin/env python3
"""test-worktree-plumb.py — the regression set for `bin/worktree.sh --plumb`
(added 2026-08-14 alongside hooks/worktree-self-plumb.py).

WHY THIS FILE EXISTS. `run.sh worktree <name>` links .venv, out, and
mcp-server/node_modules back to the canonical tree at CREATE time — but only
for worktrees IT creates. A worktree opened through any other door (Claude
Code's native isolation doing a bare `git worktree add` under
.claude/worktrees/, Codex, or a human typing `git worktree add` by hand) gets
none of that: no mcp-server/node_modules, tooling that hard-codes ./.venv/
fails, and the live failure this closes is a session falling back to running
from the shared CANONICAL checkout instead — the exact multi-writer collision
`run.sh worktree` exists to prevent.

`--plumb [path]` is the SAME linking step (bin/worktree.sh's `link()`,
unmodified — rule a8c55a47: a manual path and an automated path doing the
same job must be the same code), made callable against a worktree the script
did not create, so hooks/worktree-self-plumb.py can call it automatically at
SessionStart.

Builds a SCRATCH git repo in a tempdir — NEVER carr-system itself — exactly
the way tools/test-worktree-sweep.py does, including the bin/ symlink trick
that makes `$0`-derived REPO/CANON resolve inside the scratch tree so every
git call this test drives lands in the scratch repo, never in carr-system.

Five cases:
  1. --plumb on a BARE worktree (raw `git worktree add`, bypassing this
     script's own create path entirely) adds exactly the three links.
  2. Already-plumbed: running --plumb again is a no-op (same three links,
     unchanged targets) and still exits 0.
  3. Canonical tree: --plumb refuses, exit non-zero, nothing written.
  4. Unregistered path: --plumb refuses the same way --remove does for a
     path this repo's `git worktree list` does not know about.
  5. A TRACKED real .venv (not a symlink) is left alone by --plumb, same
     guard link() already applies at create time — proven here against the
     --plumb call path specifically, not just the create path.

Run: .venv/bin/python tools/test-worktree-plumb.py   # exit 0 = all pass
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

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


def check(label, ok, detail=""):
    results.append((label, ok, detail))
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if (not ok and detail) else ""))


def run(cmd, cwd, **kw):
    kw.setdefault("env", fixture_env())
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60, **kw)


def git(args, cwd):
    p = run(["git"] + args, cwd)
    assert p.returncode == 0, f"git {args} failed in {cwd}:\n{p.stdout}\n{p.stderr}"
    return p.stdout


class Scratch:
    """One throwaway git repo + a bare 'origin' + a worktree.sh symlink that
    resolves bin/worktree.sh's own REPO/CANON to THIS tree, never carr-system.
    Also fakes the three canonical dirs (.venv, out, mcp-server/node_modules)
    as real directories with a marker file each, standing in for what the
    real canonical tree carries — --plumb links point AT these."""

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
        # mcp-server/ itself IS tracked (source files); only node_modules
        # under it is gitignored. A worktree checkout therefore always has
        # the mcp-server/ directory but never node_modules — this dummy
        # tracked file reproduces that shape so `mcp-server/node_modules`
        # has a parent directory to link into, same as the real repo.
        os.makedirs(os.path.join(self.repo, "mcp-server"))
        with open(os.path.join(self.repo, "mcp-server", "index.js"), "w") as f:
            f.write("// scratch stand-in for tracked mcp-server source\n")
        lock = {
            "name": "fixture",
            "version": "1.0.0",
            "lockfileVersion": 3,
            "requires": True,
            "packages": {
                "": {
                    "name": "fixture",
                    "version": "1.0.0",
                    "dependencies": {"fixture-pkg": "1.0.0"},
                },
                "node_modules/fixture-pkg": {
                    "version": "1.0.0",
                    "resolved": "https://registry.example/fixture-pkg-1.0.0.tgz",
                    "integrity": "sha512-fixture-1.0.0",
                },
            },
        }
        with open(os.path.join(self.repo, "mcp-server", "package.json"), "w") as f:
            json.dump({
                "name": "fixture",
                "version": "1.0.0",
                "dependencies": {"fixture-pkg": "1.0.0"},
            }, f)
        with open(os.path.join(self.repo, "mcp-server", "package-lock.json"), "w") as f:
            json.dump(lock, f)
        # A Python lockfile, because the .venv half of --plumb now FAILS CLOSED
        # without one: a runtime that cannot be proven against a lock is not
        # exposed. Deliberately pin-free — an empty requirement set is satisfied
        # by the empty `--without-pip` venv built below, which keeps this fixture
        # about PLUMBING rather than about package resolution.
        with open(os.path.join(self.repo, "requirements.lock"), "w") as f:
            f.write("# fixture lock: no pinned packages\n")
        git(["add", "README.md", "mcp-server/index.js", "mcp-server/package.json",
             "mcp-server/package-lock.json", "requirements.lock"], self.repo)
        git(["commit", "-q", "-m", "init"], self.repo)
        git(["remote", "add", "origin", self.origin], self.repo)
        git(["push", "-q", "-u", "origin", "main"], self.repo)

        os.makedirs(os.path.join(self.repo, "bin"))
        os.symlink(WORKTREE_SH, os.path.join(self.repo, "bin", "worktree.sh"))

        # The canonical .venv must be a REAL venv now: --plumb validates the
        # interpreter's installed distribution set against requirements.lock
        # before exposing it, so a directory with a marker file in it is no
        # longer a runtime this script will link.
        subprocess.run([sys.executable, "-m", "venv", "--without-pip",
                        os.path.join(self.repo, ".venv")],
                       check=True, capture_output=True)

        for rel in ("out", os.path.join("mcp-server", "node_modules")):
            d = os.path.join(self.repo, rel)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "marker.txt"), "w") as f:
                f.write(rel + "\n")
        installed_lock = dict(lock)
        installed_lock["packages"] = dict(lock["packages"])
        installed_lock["packages"].pop("")
        with open(os.path.join(self.repo, "mcp-server", "node_modules", ".package-lock.json"), "w") as f:
            json.dump(installed_lock, f)

    def worktree_sh(self, *args):
        return run(["zsh", os.path.join(self.repo, "bin", "worktree.sh"), *args], self.repo)

    def worktree_list(self):
        out = git(["worktree", "list", "--porcelain"], self.repo)
        return {ln.split(" ", 1)[1] for ln in out.splitlines() if ln.startswith("worktree ")}

    def make_bare_worktree(self, name):
        """A worktree created by a door OTHER than this script — raw
        `git worktree add`, no call into worktree.sh's create path at all, so
        it starts with none of the three links. This is the exact shape of
        Claude Code's native isolation and of Codex/hand-typed worktrees."""
        wt = os.path.join(self.tmp, name)
        git(["worktree", "add", "-b", name, wt, "main"], self.repo)
        return wt

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def with_scratch(fn):
    # realpath: macOS /tmp is itself a symlink to /private/..., and git
    # registers a worktree's REAL path in `worktree list --porcelain` — same
    # reasoning as tools/test-worktree-sweep.py.
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="wt-plumb-test-"))
    s = Scratch(tmp)
    try:
        fn(s)
    finally:
        s.cleanup()


LINKS = (".venv", "out", os.path.join("mcp-server", "node_modules"))


def assert_all_linked(wt, repo):
    for rel in LINKS:
        p = os.path.join(wt, rel)
        ok = os.path.islink(p) and os.path.realpath(p) == os.path.realpath(os.path.join(repo, rel))
        check(f"{rel} is a symlink into the canonical tree", ok, p)


# ── 1. --plumb on a bare worktree adds exactly the three links ──────────
def test_plumb_bare_worktree():
    print("\n[1] --plumb on a bare (non-script-created) worktree adds exactly the three links")

    def body(s):
        wt = s.make_bare_worktree("wt-bare")
        for rel in LINKS:
            check(f"starts with no {rel}", not os.path.exists(os.path.join(wt, rel)))

        p = s.worktree_sh("--plumb", wt)
        check("--plumb exits 0", p.returncode == 0, p.stdout + p.stderr)
        assert_all_linked(wt, s.repo)
        # exactly three "ok" lines — nothing extra, nothing skipped
        ok_lines = [ln for ln in p.stdout.splitlines() if ln.strip().startswith("ok")]
        check("prints exactly one line per link made", len(ok_lines) == 3, p.stdout)

    with_scratch(body)


# ── 2. already-plumbed -> no-op, still exit 0 ────────────────────────────
def test_plumb_idempotent():
    print("\n[2] already-plumbed -> no-op, still exit 0")

    def body(s):
        wt = s.make_bare_worktree("wt-idempotent")
        p1 = s.worktree_sh("--plumb", wt)
        check("first --plumb exits 0", p1.returncode == 0, p1.stdout + p1.stderr)
        targets_before = {rel: os.path.realpath(os.path.join(wt, rel)) for rel in LINKS}

        p2 = s.worktree_sh("--plumb", wt)
        check("second --plumb exits 0", p2.returncode == 0, p2.stdout + p2.stderr)
        assert_all_linked(wt, s.repo)
        targets_after = {rel: os.path.realpath(os.path.join(wt, rel)) for rel in LINKS}
        check("link targets unchanged by the repeat", targets_before == targets_after,
              f"{targets_before} vs {targets_after}")

    with_scratch(body)


# ── 3. canonical tree -> refused ─────────────────────────────────────────
def test_plumb_refuses_canonical():
    print("\n[3] --plumb on the canonical tree itself -> refused")

    def body(s):
        p = s.worktree_sh("--plumb", s.repo)
        check("--plumb on canonical exits non-zero", p.returncode != 0, p.stdout + p.stderr)
        check("says there is nothing to plumb",
              "nothing to plumb" in (p.stdout + p.stderr), p.stdout + p.stderr)
        for rel in LINKS:
            real_dir_untouched = os.path.isdir(os.path.join(s.repo, rel)) and not os.path.islink(
                os.path.join(s.repo, rel))
            check(f"canonical {rel} is still a real directory, not a link", real_dir_untouched)

    with_scratch(body)


# ── 4. unregistered path -> refused ──────────────────────────────────────
def test_plumb_refuses_unregistered_path():
    print("\n[4] --plumb on a path this repo does not recognize as a worktree -> refused")

    def body(s):
        stray = os.path.join(s.tmp, "not-a-worktree")
        os.makedirs(stray)
        p = s.worktree_sh("--plumb", stray)
        check("--plumb on an unregistered path exits non-zero", p.returncode != 0, p.stdout + p.stderr)
        check("says it is not a registered worktree",
              "not a registered worktree" in (p.stdout + p.stderr), p.stdout + p.stderr)
        for rel in LINKS:
            check(f"no {rel} link created in the stray directory",
                  not os.path.exists(os.path.join(stray, rel)))

    with_scratch(body)


# ── 5. a TRACKED real .venv is left alone by --plumb ─────────────────────
def test_plumb_leaves_tracked_real_dir_alone():
    print("\n[5] a tracked real .venv (not a symlink) is left alone by --plumb")

    def body(s):
        wt = s.make_bare_worktree("wt-tracked-venv")
        # Make .venv a REAL, TRACKED directory in this worktree — the same
        # shape branch dealroom-chrome-tidy hit on 2026-08-13 against the
        # create path. --plumb must apply the identical guard link() already
        # has: existing + not-a-symlink -> left alone, never deleted.
        venv_dir = os.path.join(wt, ".venv")
        os.makedirs(venv_dir, exist_ok=True)
        with open(os.path.join(venv_dir, "real.txt"), "w") as f:
            f.write("this is a real tracked file, not scratch plumbing\n")
        git(["add", ".venv/real.txt"], wt)
        git(["commit", "-q", "-m", "tracked real .venv"], wt)

        p = s.worktree_sh("--plumb", wt)
        # A worktree-local .venv is the runtime --plumb JUDGES, rather than
        # something it merely declines to overwrite. This one has no interpreter,
        # so it cannot be proven against the lock — and --plumb must REPORT that
        # by exiting nonzero. Expecting success here was itself a hole: a caller
        # reading the exit status was told the plumbing had succeeded while the
        # runtime in the tree was untrusted.
        check("--plumb exits NONZERO when the worktree-local .venv is untrusted",
              p.returncode != 0, f"rc={p.returncode}\n{p.stdout}{p.stderr}")
        check("--plumb reports the worktree-local .venv left in place",
              "left in place" in p.stdout, p.stdout)
        check(".venv is still a real directory, not a symlink",
              os.path.isdir(venv_dir) and not os.path.islink(venv_dir))
        check("the tracked file survives untouched",
              os.path.isfile(os.path.join(venv_dir, "real.txt")))
        # the other two targets, never made real/tracked, still get linked
        for rel in ("out", os.path.join("mcp-server", "node_modules")):
            p_rel = os.path.join(wt, rel)
            ok = os.path.islink(p_rel) and os.path.realpath(p_rel) == os.path.realpath(
                os.path.join(s.repo, rel))
            check(f"{rel} still gets linked normally", ok, p_rel)

    with_scratch(body)


test_plumb_bare_worktree()
test_plumb_idempotent()
test_plumb_refuses_canonical()
test_plumb_refuses_unregistered_path()
test_plumb_leaves_tracked_real_dir_alone()

fails = [(label, detail) for label, ok, detail in results if not ok]
total = len(results)
if fails:
    print(f"\nFAIL — {len(fails)} of {total} checks failed\n")
    for label, detail in fails:
        print(f"  {label}" + (f"  — {detail}" if detail else ""))
    sys.exit(1)
print(f"\nok — {total}/{total} worktree-plumb checks pass")
sys.exit(0)
