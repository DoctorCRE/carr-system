#!/usr/bin/env python3
# doctrine: runbook
"""Selftest for ops/githooks/client-name-warn.py, the warn-only pre-push note.

Hermetic: INVENTED names in a temp list, a throwaway git repository built
through ops/git_env.fixture_env, and a `main...feature` range standing in for
`origin/main...HEAD`. It proves:

  * a planted ADDED line is warned with file:line and a kind, never the name;
  * a name only on an unchanged (context) line is not reported;
  * a clean diff prints nothing;
  * with no local list the check is silent;
  * the exit code is 0 in every case, including a range git cannot resolve,
    and the pre-push hook discards it anyway;
  * a git call that never answers is cut off by the note's own timeout;
  * the REAL pre-push hook, executed in a fixture checkout whose changed file
    carries a 30-second textconv, finishes under a deadline and runs both
    reporters (the note passes --no-textconv).
"""
from __future__ import annotations

import io
import importlib.util
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from git_env import fixture_env  # noqa: E402

ENV = fixture_env()
spec = importlib.util.spec_from_file_location("client_name_warn", HERE / "githooks" / "client-name-warn.py")
assert spec is not None and spec.loader is not None
warn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(warn)

NAMES = ["Zebulon Quaxmire", "Glimmerstone Dental Arts"]
checks: list[tuple[str, bool]] = []


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=ENV,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run(repo: Path, rng: str, names_file: Path | None) -> tuple[int, str]:
    saved = {k: os.environ.get(k) for k in (warn.NAMES_ENV, warn.ROSTER_ENV)}
    missing = str(repo.parent / "absent")
    os.environ[warn.NAMES_ENV] = str(names_file) if names_file else missing
    os.environ[warn.ROSTER_ENV] = missing
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = warn.main(["client-name-warn.py", rng, str(repo)])
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return rc, out.getvalue() + err.getvalue()


with tempfile.TemporaryDirectory(prefix="client-name-warn-") as tmp:
    root = Path(tmp)
    names_file = root / "names.local.txt"
    names_file.write_text("\n".join(NAMES) + "\n")
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, env=ENV)
    git(repo, "config", "user.email", "selftest@example.invalid")
    git(repo, "config", "user.name", "selftest")
    (repo / "old.md").write_text("line one\nmet Zebulon Quaxmire long ago\nline three\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "switch", "-q", "-c", "feature")

    (repo / "clean.md").write_text("a dentist in the panhandle\n")
    (repo / "old.md").write_text("line one\nmet Zebulon Quaxmire long ago\nline three, edited\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "clean change")
    rc, text = run(repo, "main...feature", names_file)
    checks.append(("a clean diff is silent (a name on an untouched line is not reported)",
                   rc == 0 and text == ""))

    (repo / "notes.md").write_text("intro\nfollow up with glimmerstone DENTAL arts friday\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "planted")
    rc, text = run(repo, "main...feature", names_file)
    checks.append(("a planted added line is warned with file:line and a kind",
                   rc == 0 and "notes.md:2" in text and "local list line 2" in text))
    checks.append(("the warning never prints the name",
                   "glimmerstone" not in text.lower() and "quaxmire" not in text.lower()))
    checks.append(("the warning carries the gradual-conversion note", "pseudonym" in text))

    rc, text = run(repo, "main...feature", None)
    checks.append(("no local list: silent, exit 0", rc == 0 and text == ""))

    rc, text = run(repo, "no-such-ref...feature", names_file)
    checks.append(("an unresolvable range: exit 0, nothing printed", rc == 0 and text == ""))

    # A git that never answers: the note's own subprocess must be bounded.
    fake_bin = root / "fake-bin"
    fake_bin.mkdir()
    (fake_bin / "git").write_text("#!/bin/sh\nexec sleep 30\n")
    os.chmod(fake_bin / "git", 0o755)
    saved_path = os.environ.get("PATH", "")
    saved_timeout = getattr(warn, "GIT_TIMEOUT_SECONDS", None)
    os.environ["PATH"] = f"{fake_bin}{os.pathsep}{saved_path}"
    setattr(warn, "GIT_TIMEOUT_SECONDS", 1)
    started = time.monotonic()
    try:
        rc, text = run(repo, "main...feature", names_file)
    finally:
        os.environ["PATH"] = saved_path
        if saved_timeout is not None:
            setattr(warn, "GIT_TIMEOUT_SECONDS", saved_timeout)
    checks.append(("a git call that hangs is cut off by the note's timeout: exit 0, silent",
                   rc == 0 and text == "" and time.monotonic() - started < 10))


def run_hook(root: Path, deadline: float) -> tuple[bool, int, str]:
    """Execute the REAL pre-push hook in a fixture checkout, under a deadline.

    The fixture holds copies of the hook and the two reporters it calls, and
    no ops/ci.sh, so only the reporters run. Returns (finished, rc, output).
    The hook runs in its own process group so a hang is killed whole.
    """
    env = dict(ENV)
    env[warn.NAMES_ENV] = str(root / "names.local.txt")
    env[warn.ROSTER_ENV] = str(root / "absent")
    sha = subprocess.run(["git", "-C", str(root / "hookrepo"), "rev-parse", "HEAD"], env=ENV,
                         check=True, capture_output=True, text=True).stdout.strip()
    proc = subprocess.Popen(["sh", "ops/githooks/pre-push", "origin", "https://example.invalid/r.git"],
                            cwd=root / "hookrepo", env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            start_new_session=True)
    try:
        out, _ = proc.communicate(f"refs/heads/feature {sha} refs/heads/feature {'0' * 40}\n",
                                  timeout=deadline)
        return True, proc.returncode, out
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        return False, -1, ""


with tempfile.TemporaryDirectory(prefix="client-name-warn-hook-") as tmp:
    root = Path(tmp)
    (root / "names.local.txt").write_text("\n".join(NAMES) + "\n")
    hr = root / "hookrepo"
    (hr / "ops" / "githooks").mkdir(parents=True)
    for rel in ("ops/githooks/pre-push", "ops/githooks/client-name-warn.py",
                "ops/pr-size-check.py", "ops/jev_intake.py", "lib/review_tiers.py",
                "ops/config/review-tiers.v1.json"):
        (hr / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(HERE.parent / rel, hr / rel)
    subprocess.run(["git", "init", "-q", "-b", "main", str(hr)], check=True, env=ENV)
    git(hr, "config", "user.email", "selftest@example.invalid")
    git(hr, "config", "user.name", "selftest")
    # A converter that would hold the push for 30 seconds if anything ran it.
    git(hr, "config", "diff.slow.textconv", "sleep 30; cat")
    (hr / ".git" / "info" / "exclude").write_text("ops/\n")
    (hr / ".gitattributes").write_text("*.dat diff=slow\n")
    (hr / "blob.dat").write_bytes(b"\0before\n")
    (hr / "notes.md").write_text("intro\n")
    git(hr, "add", ".gitattributes", "blob.dat", "notes.md")
    git(hr, "commit", "-q", "-m", "base")
    git(hr, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(hr, "switch", "-q", "-c", "feature")
    (hr / "blob.dat").write_bytes(b"\0after\n")
    (hr / "notes.md").write_text("intro\nfollow up with Zebulon Quaxmire\n")
    (hr / "big.py").write_text("".join(f"x = {i}\n" for i in range(400)))
    git(hr, "add", "blob.dat", "notes.md", "big.py")
    git(hr, "commit", "-q", "-m", "feature")

    finished, rc, out = run_hook(root, deadline=15)
    checks.append(("the real hook finishes under a deadline despite a 30s textconv on a changed file",
                   finished and rc == 0))
    checks.append(("the real hook runs the client-name note (planted line reported)",
                   "notes.md:2" in out and "quaxmire" not in out.lower()))
    checks.append(("the real hook runs the decomposition note after it",
                   "hard to review in one piece" in out))

hook = (HERE / "githooks" / "pre-push").read_text()
checks.append(("the pre-push hook discards the note's exit status",
               'client-name-warn.py" "origin/main...HEAD" "$REPO_ROOT" || true' in hook))

failed = [label for label, ok in checks if not ok]
for label, ok in checks:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
print(f"client-name-warn-selftest: {len(checks) - len(failed)}/{len(checks)} passed")
sys.exit(1 if failed else 0)
