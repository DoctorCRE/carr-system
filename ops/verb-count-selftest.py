#!/usr/bin/env python3
"""Regression test verb counting from a clean detached-style source tree."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "mcp-server"
COUNT = ROOT / "ops" / "verb-count.sh"


with tempfile.TemporaryDirectory(prefix="verb-count-selftest-") as tmp:
    worker = Path(tmp) / "mcp-server"
    shutil.copytree(SOURCE, worker, ignore=shutil.ignore_patterns("node_modules"))
    (Path(tmp) / "lib").mkdir()
    shutil.copy(ROOT / "lib/deal_schedule.schema.json", Path(tmp) / "lib")
    assert not (worker / "node_modules").exists(), "fixture must have no dependencies"
    baseline = subprocess.run(["sh", str(COUNT), str(SOURCE)], cwd=ROOT,
                              capture_output=True, text=True, check=False)
    assert baseline.returncode == 0, baseline.stderr
    result = subprocess.run(["sh", str(COUNT), str(worker)], cwd=ROOT,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == baseline.stdout.strip(), (result.stdout, baseline.stdout)

print("verb-count: clean detached-style source imports with current runtime")
