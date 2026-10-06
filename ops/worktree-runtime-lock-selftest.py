#!/usr/bin/env python3
"""A worktree never links a shared npm runtime from a different lock."""

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

    first = run(ZSH, str(canonical / "bin" / "worktree.sh"), "--plumb", str(worktree), cwd=worktree)
    link_path = worktree / "mcp-server" / "node_modules"
    assert "does not match this worktree's package-lock.json" in first.stdout
    assert not link_path.exists() and not link_path.is_symlink()

    good = lock("1.0.0")
    good["packages"].pop("")
    installed.write_text(json.dumps(good))
    second = run(ZSH, str(canonical / "bin" / "worktree.sh"), "--plumb", str(worktree), cwd=worktree)
    assert link_path.is_symlink()
    assert os.path.realpath(link_path) == os.path.realpath(runtime)
    assert "ok  node_modules" in second.stdout

    # A cache can drift after a worktree was plumbed. Re-running the same path
    # must remove the now-unsafe untracked symlink, not leave it trusted.
    installed.write_text(json.dumps(bad))
    third = run(ZSH, str(canonical / "bin" / "worktree.sh"), "--plumb", str(worktree), cwd=worktree)
    assert "does not match this worktree's package-lock.json" in third.stdout
    assert not link_path.exists() and not link_path.is_symlink()

print("worktree-runtime-lock-selftest: stale shared npm cache refused")
