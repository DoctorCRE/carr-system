#!/usr/bin/env python3
"""Hermetic freeze of bin/monthly-gate.py — the CONTRACT, not the ledger.

bin/monthly-gate.py loads tools/ops-record.py by a path computed from its own
`__file__`, so the only way to hand it a fake ledger without touching the real
one is to copy the unmodified gate into a synthetic root next to a stub
tools/ops-record.py and run it there. That mirrors
ops/cc-version-sentinel-selftest.py's approach for bin/cc-version-sentinel.sh,
which faces the same problem (a script that resolves its own dependencies
relative to its own location, not the caller's).

WHAT IS PINNED, straight from bin/monthly-gate.py's own contract comment:
  - exit 0 PROCEED when the ledger cannot be reached at all (missing
    tools/ops-record.py, or a stub that raises importing it) — FAIL-OPEN is
    deliberate; the alternative silently skips a month.
  - exit 1 STOP when a stubbed connection returns a completed-run row.
  - exit 0 PROCEED when it returns none.
  - --quiet prints nothing, on any of the above.
  - the default --run-key is monthly.completed (COMPLETION_KEY in the gate).

RUN IT:
    .venv/bin/python ops/monthly-gate-selftest.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "bin" / "monthly-gate.py"
FAILED: list[str] = []

# A minimal stand-in for tools/ops-record.py's `connect()` contract: a context
# manager yielding a connection whose .cursor() is a context manager yielding
# a cursor with .execute(query, params) and .fetchone(). The outcome (row or
# none) and where to log the query params are read from the environment at
# CALL time, so one stub file serves every case below.
STUB_OPS_RECORD = """
import contextlib
import datetime
import os


class _Cursor:
    def __init__(self):
        self._row = None

    def execute(self, query, params):
        capture = os.environ.get("SELFTEST_CAPTURE")
        if capture:
            with open(capture, "a", encoding="utf-8") as fh:
                fh.write(repr(params) + "\\n")
        if os.environ.get("SELFTEST_OUTCOME") == "row":
            self._row = (datetime.datetime(2026, 8, 1, 9, 0, tzinfo=datetime.timezone.utc),)
        else:
            self._row = None

    def fetchone(self):
        return self._row


class _Conn:
    def cursor(self):
        return contextlib.nullcontext(_Cursor())


@contextlib.contextmanager
def connect(role):
    yield _Conn()
"""

# Simulates the ledger module itself being broken (import-time failure), the
# other half of the fail-open contract alongside a missing file entirely.
RAISING_OPS_RECORD = "raise RuntimeError('selftest: ledger module intentionally broken')\n"


def check(label: str, condition: bool, detail: str = "") -> None:
    print(("  ok    " if condition else "  FAIL  ") + label + (f" — {detail}" if detail else ""))
    if not condition:
        FAILED.append(label)


def build_root(tmp: Path, ops_record_source: str | None) -> Path:
    """A synthetic repo: bin/monthly-gate.py copied byte-for-byte (never
    modified), tools/ops-record.py written per-case (or omitted entirely to
    model a missing ledger module)."""
    root = tmp / "repo"
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True)
    gate = bin_dir / "monthly-gate.py"
    gate.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    gate.chmod(0o755)
    if ops_record_source is not None:
        tools_dir = root / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "ops-record.py").write_text(ops_record_source, encoding="utf-8")
    return root


def run_gate(root: Path, args: list[str], extra_env: dict[str, str] | None = None) -> "subprocess.CompletedProcess[str]":
    gate = root / "bin" / "monthly-gate.py"
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, str(gate), *args], cwd=root, env=env,
                          capture_output=True, text=True, check=False)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="monthly-gate-missing-") as tmp:
        root = build_root(Path(tmp), None)
        result = run_gate(root, ["playbook-review-monthly"])
        check("missing tools/ops-record.py fails open with PROCEED",
              result.returncode == 0 and "PROCEED" in result.stdout,
              f"rc={result.returncode} stdout={result.stdout!r}")

    with tempfile.TemporaryDirectory(prefix="monthly-gate-raises-") as tmp:
        root = build_root(Path(tmp), RAISING_OPS_RECORD)
        result = run_gate(root, ["playbook-review-monthly"])
        check("a ledger module that raises on import fails open with PROCEED",
              result.returncode == 0 and "PROCEED" in result.stdout and "ledger unreachable" in result.stdout,
              f"rc={result.returncode} stdout={result.stdout!r}")

    with tempfile.TemporaryDirectory(prefix="monthly-gate-row-") as tmp:
        root = build_root(Path(tmp), STUB_OPS_RECORD)
        result = run_gate(root, ["playbook-review-monthly"], {"SELFTEST_OUTCOME": "row"})
        check("a completed-run row stops the routine",
              result.returncode == 1 and "STOP" in result.stdout,
              f"rc={result.returncode} stdout={result.stdout!r}")

    with tempfile.TemporaryDirectory(prefix="monthly-gate-none-") as tmp:
        root = build_root(Path(tmp), STUB_OPS_RECORD)
        result = run_gate(root, ["playbook-review-monthly"], {"SELFTEST_OUTCOME": "none"})
        check("no completed-run row proceeds",
              result.returncode == 0 and "PROCEED" in result.stdout,
              f"rc={result.returncode} stdout={result.stdout!r}")

    with tempfile.TemporaryDirectory(prefix="monthly-gate-quiet-") as tmp:
        root = build_root(Path(tmp), STUB_OPS_RECORD)
        stopped = run_gate(root, ["playbook-review-monthly", "--quiet"], {"SELFTEST_OUTCOME": "row"})
        check("--quiet prints nothing on the STOP path",
              stopped.returncode == 1 and stopped.stdout == "" and stopped.stderr == "",
              f"rc={stopped.returncode} stdout={stopped.stdout!r} stderr={stopped.stderr!r}")
        proceeded = run_gate(root, ["playbook-review-monthly", "--quiet"], {"SELFTEST_OUTCOME": "none"})
        check("--quiet prints nothing on the PROCEED path",
              proceeded.returncode == 0 and proceeded.stdout == "" and proceeded.stderr == "",
              f"rc={proceeded.returncode} stdout={proceeded.stdout!r} stderr={proceeded.stderr!r}")

    with tempfile.TemporaryDirectory(prefix="monthly-gate-runkey-") as tmp:
        root = build_root(Path(tmp), STUB_OPS_RECORD)
        capture = Path(tmp) / "query-params.log"
        run_gate(root, ["playbook-review-monthly"], {"SELFTEST_OUTCOME": "none", "SELFTEST_CAPTURE": str(capture)})
        default_params = capture.read_text(encoding="utf-8").strip()
        check("the default --run-key is monthly.completed",
              default_params == "('playbook-review-monthly', 'monthly.completed')",
              f"captured={default_params!r}")

        capture.unlink()
        run_gate(root, ["playbook-review-monthly", "--run-key", "custom.key"],
                 {"SELFTEST_OUTCOME": "none", "SELFTEST_CAPTURE": str(capture)})
        custom_params = capture.read_text(encoding="utf-8").strip()
        check("--run-key overrides the default",
              custom_params == "('playbook-review-monthly', 'custom.key')",
              f"captured={custom_params!r}")

    print(f"monthly gate selftest — {len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
