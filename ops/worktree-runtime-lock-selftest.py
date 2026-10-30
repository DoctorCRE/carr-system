#!/usr/bin/env python3
"""Plumbing never symlinks mcp-server/node_modules into a worktree (WR579.S1.J1)."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
ZSH = shutil.which("zsh")
assert ZSH, "zsh is required (hosted CI installs it before gate suites)"
FIXTURE_ENV = fixture_env()


def run(*args, cwd, check=True):
    return subprocess.run(
        args,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        env=FIXTURE_ENV,
    )


def lock(version):
    return {
        "name": "fixture",
        "version": "1.0.0",
        "lockfileVersion": 3,
        "requires": True,
        "packages": {
            "": {"name": "fixture", "version": "1.0.0", "dependencies": {"fixture-pkg": "1.0.0"}},
            "node_modules/fixture-pkg": {
                "version": version,
                "resolved": f"https://registry.example/fixture-pkg-{version}.tgz",
                "integrity": f"sha512-fixture-{version}",
            },
            # npm omits optional packages that do not apply to this platform.
            "node_modules/optional-other-platform": {
                "version": "2.0.0",
                "optional": True,
                "os": ["win32"],
            },
        },
    }


with tempfile.TemporaryDirectory() as td:
    canonical = Path(td) / "canonical"
    canonical.mkdir()
    run("git", "init", "-q", "-b", "main", cwd=canonical)
    run("git", "config", "user.email", "fixture@example.com", cwd=canonical)
    run("git", "config", "user.name", "fixture", cwd=canonical)

    (canonical / "bin").mkdir()
    shutil.copy2(ROOT / "bin" / "worktree.sh", canonical / "bin" / "worktree.sh")
    (canonical / "mcp-server").mkdir()
    (canonical / "mcp-server" / "package.json").write_text(json.dumps({
        "name": "fixture", "version": "1.0.0", "dependencies": {"fixture-pkg": "1.0.0"},
    }))
    (canonical / "mcp-server" / "package-lock.json").write_text(json.dumps(lock("1.0.0")))
    (canonical / ".gitignore").write_text(".venv/\nout/\nmcp-server/node_modules/\n")
    run("git", "add", "bin/worktree.sh", "mcp-server/package.json",
        "mcp-server/package-lock.json", ".gitignore", cwd=canonical)
    run("git", "commit", "-qm", "fixture", cwd=canonical)

    runtime = canonical / "mcp-server" / "node_modules"
    runtime.mkdir()
    installed = runtime / ".package-lock.json"
    bad = lock("0.9.0")
    bad["packages"].pop("")
    installed.write_text(json.dumps(bad))

    worktree = Path(td) / "worktree"
    run("git", "worktree", "add", "-q", "-b", "fixture-worktree", str(worktree), cwd=canonical)
    link_path = worktree / "mcp-server" / "node_modules"
    marker = runtime / "fixture-pkg-marker"
    plumb = [ZSH, str(canonical / "bin" / "worktree.sh"), "--plumb", str(worktree)]

    def check_not_linked(label):
        assert not link_path.is_symlink(), (
            f"{label}: node_modules is a symlink (npm ci through it empties the canonical folder)")

    # WR579.S1.J1: even when the shared runtime matches this worktree's lock
    # exactly, plumbing must NOT link it. On 2026-10-28 `npm ci` through such a
    # link emptied the canonical mcp-server/node_modules.
    good = lock("1.0.0")
    good["packages"].pop("")
    installed.write_text(json.dumps(good))
    marker.write_text("x")
    run(*plumb, cwd=worktree)
    check_not_linked("matching lock")

    # A pre-fix worktree carries a symlink into the canonical folder. Plumbing
    # drops the link (never its target).
    link_path.symlink_to(runtime)
    third = run(*plumb, cwd=worktree)
    check_not_linked("pre-fix symlink")
    assert marker.exists() and installed.exists(), "canonical runtime must survive plumbing"
    assert "dropped shared node_modules symlink" in third.stdout

    # A real directory in the worktree is somebody's own install: left alone.
    link_path.mkdir()
    (link_path / "own").write_text("mine")
    run(*plumb, cwd=worktree)
    assert (link_path / "own").exists() and not link_path.is_symlink()

    # Simulate npm ci wiping the worktree's node_modules: canonical stays intact.
    shutil.rmtree(link_path)
    assert marker.exists() and installed.exists()

print("worktree-runtime-lock-selftest: node_modules is never linked into a worktree")
