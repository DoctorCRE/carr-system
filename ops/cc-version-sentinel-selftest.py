#!/usr/bin/env python3
"""Hermetic regression checks for the Claude version sentinel latch."""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "bin/cc-version-sentinel.sh"
HELPER = REPO / "bin/cc-version-string.sh"
# The exact fragment rule a8c55a47 says must exist in ONE file only — the
# sentinel used to build this string inline before it moved to HELPER.
CLI_AWK_FRAGMENT = "--version 2>/dev/null | awk '{print $1}'"
FAILED: list[str] = []


def check(label: str, condition: bool) -> None:
    print(("  ok    " if condition else "  FAIL  ") + label)
    if not condition:
        FAILED.append(label)


def observed_cli() -> str:
    for candidate in (Path("/opt/homebrew/bin/claude"),
                      Path("/usr/local/bin/claude")):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            result = subprocess.run([str(candidate), "--version"],
                                    capture_output=True, text=True, check=False)
            value = result.stdout.split(maxsplit=1)[0] if result.stdout.split() else ""
            if value:
                return value
    return "none"


def run(home: Path, notifier: Path, notifier_calls: Path, log: Path) -> None:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CARR_CC_VERSION_NOTIFY_COMMAND"] = str(notifier)
    env["CARR_CC_VERSION_NOTIFY_CALLS"] = str(notifier_calls)
    env["CARR_CC_VERSION_SENTINEL_LOG"] = str(log)
    subprocess.run(["zsh", str(SCRIPT)], cwd=REPO, env=env, check=True,
                   capture_output=True, text=True)


def run_with_broken_helper(scratch: Path, home: Path, notifier: Path,
                            notifier_calls: Path, log: Path) -> "subprocess.CompletedProcess[str]":
    """Copies the real sentinel next to a helper stub that always fails, so
    REPO resolves — via the sentinel's own `dirname "$0"` logic, not cwd — to
    a tree where bin/cc-version-string.sh cannot succeed. Proves a helper
    failure surfaces as the sentinel's own FAIL line and a nonzero exit,
    never a silent `set -eu` abort with nothing in the log to say why."""
    bin_dir = scratch / "bin"
    bin_dir.mkdir(parents=True)
    broken_sentinel = bin_dir / "cc-version-sentinel.sh"
    broken_sentinel.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    broken_sentinel.chmod(0o755)
    broken_helper = bin_dir / "cc-version-string.sh"
    broken_helper.write_text("#!/bin/zsh\nexit 9\n", encoding="utf-8")
    broken_helper.chmod(0o755)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CARR_CC_VERSION_NOTIFY_COMMAND"] = str(notifier)
    env["CARR_CC_VERSION_NOTIFY_CALLS"] = str(notifier_calls)
    env["CARR_CC_VERSION_SENTINEL_LOG"] = str(log)
    return subprocess.run(["zsh", str(broken_sentinel)], cwd=scratch, env=env,
                          check=False, capture_output=True, text=True)


def marker_field(marker: str, field: str) -> str:
    prefix = field + ": "
    return next((line[len(prefix):] for line in marker.splitlines()
                 if line.startswith(prefix)), "")


def notification_count(path: Path) -> int:
    if not path.exists():
        return 0
    return len(path.read_text(encoding="utf-8").splitlines())


def main() -> int:
    bin_files = list((REPO / "bin").glob("*.sh"))
    owners = [p for p in bin_files if CLI_AWK_FRAGMENT in p.read_text(encoding="utf-8")]
    check("CLI-discovery construction lives in exactly one file (the helper)",
          owners == [HELPER])

    with tempfile.TemporaryDirectory(prefix="cc-sentinel-") as tmp:
        home = Path(tmp)
        notifier_calls = home / "notifier-calls.log"
        notifier = home / "fake-osascript"
        notifier.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$CARR_CC_VERSION_NOTIFY_CALLS\"\n",
            encoding="utf-8",
        )
        notifier.chmod(0o755)
        sentinel_log = home / "cc-version-sentinel.log"
        task = home / ".claude/scheduled-tasks/cc-update-audit"
        task.mkdir(parents=True)
        app = home / "Library/Application Support/Claude/claude-code/2.0.0"
        app.mkdir(parents=True)
        cli = observed_cli()
        last = f"cli={cli} app=1.0.0"
        (task / "last-audited-version.txt").write_text(last + "\n", encoding="utf-8")

        run(home, notifier, notifier_calls, sentinel_log)
        pending = task / "pending-version.txt"
        first = pending.read_text(encoding="utf-8")
        first_detected_at = marker_field(first, "detected_at")
        check("first distinct version pair writes a pending marker",
              "current: cli=" + cli + " app=2.0.0" in first
              and "last_audited: " + last in first
              and bool(first_detected_at))
        check("first distinct pair sends exactly one notification",
              notification_count(notifier_calls) == 1)

        run(home, notifier, notifier_calls, sentinel_log)
        second = pending.read_text(encoding="utf-8")
        check("identical pending pair does not rewrite the marker", second == first)
        check("identical pending pair preserves detected_at",
              marker_field(second, "detected_at") == first_detected_at)
        check("identical pending pair sends no second notification",
              notification_count(notifier_calls) == 1)
        log = sentinel_log.read_text(encoding="utf-8")
        check("identical pending pair records a quiet latch decision",
              "already notified for this change" in log)

        app_new = home / "Library/Application Support/Claude/claude-code/3.0.0"
        app_new.mkdir()
        time.sleep(1.1)
        run(home, notifier, notifier_calls, sentinel_log)
        third = pending.read_text(encoding="utf-8")
        check("a new version pair replaces the marker",
              "current: cli=" + cli + " app=3.0.0" in third
              and third != second)
        check("a new version pair changes detected_at",
              marker_field(third, "detected_at") != first_detected_at)
        check("a new version pair sends exactly one more notification",
              notification_count(notifier_calls) == 2)

        log_lines = sentinel_log.read_text(encoding="utf-8").splitlines()
        check("isolated log records exactly three run outcomes",
              sum("cc-version-sentinel CHANGE" in line for line in log_lines) == 3
              and sum("marker written, Joe notified" in line for line in log_lines) == 2
              and sum("already notified for this change" in line for line in log_lines) == 1)

    with tempfile.TemporaryDirectory(prefix="cc-sentinel-helper-fail-") as tmp2:
        scratch = Path(tmp2) / "repo"
        home2 = Path(tmp2) / "home"
        task2 = home2 / ".claude/scheduled-tasks/cc-update-audit"
        task2.mkdir(parents=True)
        (task2 / "last-audited-version.txt").write_text("cli=1.0.0 app=1.0.0\n", encoding="utf-8")
        log2 = Path(tmp2) / "cc-version-sentinel.log"
        notifier2 = Path(tmp2) / "fake-osascript"
        notifier2.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        notifier2.chmod(0o755)
        result = run_with_broken_helper(scratch, home2, notifier2, Path(tmp2) / "notifier-calls.log", log2)
        check("a failing version helper exits nonzero, not a silent set -eu abort",
              result.returncode != 0)
        log2_text = log2.read_text(encoding="utf-8") if log2.exists() else ""
        check("a failing version helper's exit reaches the sentinel's own FAIL line",
              "FAIL version helper failed" in log2_text)

    with tempfile.TemporaryDirectory(prefix="cc-sentinel-converge-") as tmp3:
        home3 = Path(tmp3)
        task3 = home3 / ".claude/scheduled-tasks/cc-update-audit"
        task3.mkdir(parents=True)
        cli3 = observed_cli()
        audited = f"cli={cli3} app=1.0.0"
        (task3 / "last-audited-version.txt").write_text(audited + "\n", encoding="utf-8")
        app3 = home3 / "Library/Application Support/Claude/claude-code/1.0.0"
        app3.mkdir(parents=True)
        stale_pending = task3 / "pending-version.txt"
        stale_pending.write_text(
            "current: cli=" + cli3 + " app=9.9.9\n"
            "last_audited: " + audited + "\n"
            "detected_at: 2020-01-01T00:00:00Z\n",
            encoding="utf-8",
        )
        notifier3 = home3 / "fake-osascript"
        notifier3.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        notifier3.chmod(0o755)
        notifier_calls3 = home3 / "notifier-calls.log"
        log3 = home3 / "cc-version-sentinel.log"
        run(home3, notifier3, notifier_calls3, log3)
        check("current converging back to last_audited clears a stale pending marker",
              not stale_pending.exists())
        log3_text = log3.read_text(encoding="utf-8")
        check("clearing the stale marker is logged with its own OK line",
              "cleared stale pending marker" in log3_text)
        check("convergence still records the ordinary no-change OK line",
              "OK no change" in log3_text)
        check("clearing a stale marker on convergence sends no notification",
              notification_count(notifier_calls3) == 0)

    print(f"Claude version sentinel selftest — {len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
