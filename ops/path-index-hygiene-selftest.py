#!/usr/bin/env python3
"""Hermetic fixtures for the 0e22e34a and 99e951b9 commit-time controls."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The one scrubber (ops/git_env.py). git hands every hook a GIT_DIR pointing
# at the repository that invoked it, and on 2026-08-14 that leaked a fixture
# commit onto live main — the throwaway repo built below must not be
# reachable through an inherited GIT_DIR.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402


def load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PATHS = load("path_hygiene", "ops/githooks/path-hygiene-check.py")
INDEX = load("index_upkeep", "ops/githooks/index-upkeep-check.py")


def check(name: str, value: bool, failures: list[str]):
    print(f"  {'ok' if value else 'FAIL'} {name}")
    if not value:
        failures.append(name)


def git(root: Path, *args: str, input: bytes | None = None):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                          env=fixture_env(), input=input)


def merge_fixture(label: str, incoming_names: list[list[bytes]], failures: list[str]):
    # Plumbing preserves arbitrary Git filename bytes even on filesystems
    # that cannot create them. Every fixture uses real objects and an index.
    with tempfile.TemporaryDirectory(prefix="path-hygiene-merge-") as tmp:
        root = Path(tmp)
        git(root, "init", "-q")
        git(root, "config", "user.email", "selftest@example.invalid")
        git(root, "config", "user.name", "Selftest")
        git(root, "config", "core.hooksPath", "/dev/null")
        git(root, "commit", "--allow-empty", "-qm", "base")
        base = git(root, "rev-parse", "HEAD").stdout.decode().strip()
        blob = git(root, "hash-object", "-w", "--stdin", input=b"historical\n").stdout.strip()
        parents = []
        for i, names in enumerate(incoming_names):
            tree = git(root, "mktree", "-z", input=b"".join(
                b"100644 blob " + blob + b"\t" + name + b"\0" for name in names
            )).stdout.decode().strip()
            commit = git(root, "commit-tree", tree, "-p", base,
                         input=b"incoming historical names\n").stdout.decode().strip()
            branch = f"incoming-{i}"
            git(root, "update-ref", f"refs/heads/{branch}", commit)
            parents.append(branch)
        git(root, "checkout", "-qb", "topic")
        git(root, "commit", "--allow-empty", "-qm", "topic")
        if any(b"\xff" in name for names in incoming_names for name in names):
            # Install the byte-valued tree directly, independent of the host
            # filesystem's filename restrictions.
            git(root, "read-tree", parents[0])
            merge_head = git(root, "rev-parse", "--git-path", "MERGE_HEAD").stdout.decode().strip()
            (root / merge_head).write_bytes(git(root, "rev-parse", parents[0]).stdout)
        else:
            git(root, "merge", "--no-commit", "--no-ff", *parents)

        def run_checker():
            return subprocess.run(
                [sys.executable, str(REPO / "ops/githooks/path-hygiene-check.py")],
                cwd=root, env=fixture_env(), capture_output=True, text=True)

        result = run_checker()
        check(f"{label}: inherited paths pass without unchecked fallback",
              result.returncode == 0 and not result.stderr, failures)
        added = "added-v5-file.cjs"
        (root / added).write_text("new during merge\n")
        git(root, "add", added)
        result = run_checker()
        check(f"{label}: new forbidden path refuses without unchecked fallback",
              result.returncode == 1 and added in result.stderr
              and "allowing unchecked" not in result.stderr, failures)
        check(f"{label}: inherited paths are absent from refusal",
              all(os.fsdecode(name) not in result.stderr
                  for names in incoming_names for name in names)
              and result.stderr.count("draft/final version filename is forbidden") == 1,
              failures)


def main() -> int:
    failures: list[str] = []
    check("four folder levels pass", not PATHS.violations(["a/b/c/d/file.py"]), failures)
    check("fifth folder level refuses", bool(PATHS.violations(["a/b/c/d/e/file.py"])), failures)
    check("final suffix refuses", bool(PATHS.violations(["ops/report_final.md"])), failures)
    check("version suffix refuses", bool(PATHS.violations(["ops/report-v2.json"])), failures)
    check("dot-versioned machine contract passes",
          not PATHS.violations(["ops/config/policy.v1.json"]), failures)
    check("ordinary descriptive filename passes", not PATHS.violations(["ops/report-2026.json"]), failures)

    check("declared vendor-tree depth passes",
          not PATHS.violations(["plugins/pstack/skills/why/references/sources/linear.md"]), failures)
    check("vendor-tree prefix does not exempt neighboring paths",
          bool(PATHS.violations(["plugins/pstack/skills-other/a/b/c/file.md"])), failures)
    check("vendor-tree exception preserves filename checks",
          bool(PATHS.violations(["plugins/pstack/skills/a/b/report_final.md"])), failures)
    check("vendor-tree exception refuses parent traversal",
          bool(PATHS.violations(["plugins/pstack/skills/../../a/b/c/file.md"])), failures)
    check("vendor-tree exception refuses whitespace aliases",
          bool(PATHS.violations(["plugins/pstack/skills/why/references/sources/linear.md "])), failures)
    check("vendor-tree exception refuses backslash aliases",
          bool(PATHS.violations(["plugins/pstack/skills\\why/references/sources/linear.md"])), failures)

    with tempfile.TemporaryDirectory(prefix="path-index-hygiene-") as tmp:
        root = Path(tmp)
        git(root, "init", "-q")
        git(root, "config", "user.email", "selftest@example.invalid")
        git(root, "config", "user.name", "Selftest")
        (root / "category").mkdir()
        (root / "category" / "INDEX.md").write_text("# Category\n")
        (root / "category" / "old.md").write_text("old\n")
        git(root, "add", ".")
        git(root, "commit", "-qm", "seed")

        (root / "category" / "new.md").write_text("new\n")
        git(root, "add", "category/new.md")
        old_cwd = os.getcwd()
        try:
            os.chdir(root)
            check("new categorized file requires index",
                  bool(INDEX.violations(INDEX.staged_added(), INDEX.staged_all())), failures)
        finally:
            os.chdir(old_cwd)

        (root / "category" / "INDEX.md").write_text("# Category\n- new\n")
        git(root, "add", "category/INDEX.md")
        try:
            os.chdir(root)
            check("same staged index satisfies category update",
                  not INDEX.violations(INDEX.staged_added(), INDEX.staged_all()), failures)
        finally:
            os.chdir(old_cwd)

        # THE M IN THE FILTER. 0e22e34a judges the SHAPE of a path, so only a
        # path new to the repository can violate it. A historical filename that
        # would be refused as an addition must stay editable — the case that
        # blocked the r7 amendment while staged_paths() still asked for ACMR.
        (root / "tool-v5-review.cjs").write_text("legacy\n")
        git(root, "add", "tool-v5-review.cjs")
        git(root, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "historical name")
        (root / "tool-v5-review.cjs").write_text("legacy, edited\n")
        git(root, "add", "tool-v5-review.cjs")
        try:
            os.chdir(root)
            check("editing a historical version-suffixed path passes",
                  not PATHS.violations(PATHS.staged_paths()), failures)
            (root / "added-v5-file.cjs").write_text("new\n")
            git(root, "add", "added-v5-file.cjs")
            check("adding a version-suffixed path still refuses",
                  bool(PATHS.violations(PATHS.staged_paths())), failures)
        finally:
            os.chdir(old_cwd)

    for label, name in [
        ("ASCII", "tool-v5-review.cjs"),
        ("non-ASCII", "café-v5-review.cjs"),
        ("tab", "tab\t-v5-review.cjs"),
        ("newline", "line\n-v5-review.cjs"),
        ("backslash", "back\\slash-v5-review.cjs"),
    ]:
        merge_fixture(label, [[name.encode()]], failures)
    merge_fixture("non-UTF-8", [[b"historical-\xff.txt"]], failures)
    merge_fixture("octopus", [[b"first-v5-review.cjs"], [b"second-v5-review.cjs"]], failures)

    print("path-index-hygiene-selftest: " +
          (f"failed: {', '.join(failures)}" if failures else "all passed"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
