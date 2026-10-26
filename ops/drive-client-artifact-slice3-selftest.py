#!/usr/bin/env python3
"""Hermetic normal-mode boundary tests for Phase 4 client-artifact slice 3."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv/bin/python"
if not PY.exists():
    PY = next((parent / ".venv/bin/python" for parent in ROOT.parents
               if (parent / ".venv/bin/python").exists()), Path(sys.executable))


def run(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=ROOT, env=env, text=True, capture_output=True, timeout=30)


with tempfile.TemporaryDirectory(prefix="drive-client-artifact-slice3-") as td:
    poison = Path(td) / "DO-NOT-READ-OR-WRITE-DRIVE"
    poison.mkdir()
    marker = poison / "sentinel"
    marker.write_text("unchanged")
    env = dict(os.environ, CARR_VAULT=str(poison))
    cases = {
        "audit-template-colors": (str(PY), "fill-engine/audit_template_colors.py"),
        "space-search": (str(PY), "pipelines/build-space-search.py", str(Path(td) / "search")),
        "dso-match": (str(PY), "pipelines/dso-match.py"),
        "map-radar-lanes": (str(PY), "pipelines/map_radar_lanes.py", "--all"),
        "prepare-document": (str(PY), "pipelines/prepare_document.py", "-"),
    }
    for label, command in cases.items():
        proc = run(*command, env=env)
        output = proc.stdout + proc.stderr
        assert proc.returncode == 2, f"{label}: expected fail-closed exit 2, got {proc.returncode}: {output}"
        assert "canonical seam missing:" in output, f"{label}: missing named seam: {output}"
        assert str(poison) not in output, f"{label}: normal mode resolved poisoned vault: {output}"
        assert marker.read_text() == "unchanged", f"{label}: normal mode wrote poisoned vault"

        proc = run(*command, "--recovery", env=env)
        assert proc.returncode == 2 and "nonblank --reason" in proc.stdout + proc.stderr, (
            f"{label}: recovery without reason was accepted: {proc.stdout}{proc.stderr}")


print("drive client artifact slice3 selftest: Drive boundaries passed")
