#!/usr/bin/env python3
"""Hermetic proof that the scheduled-writer slice does not follow CARR_VAULT.

Each normal path receives a poisoned CARR_VAULT path that would be a failure to
open.  The child stubs record their arguments and environment, so this proves
the invoked scheduler route rather than merely inspecting source text.
"""
from __future__ import annotations

import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _normal_path(entry):
    """What the registry says a Drive dependency's NORMAL path now does.

    Accepting an entry for retirement sets replacement.status to "accepted"
    and moves the descriptive value to replacement.normal_path, because one
    field cannot carry both "what the normal path does" and "has Joe accepted
    this for retirement". Reading both keys keeps these assertions true either
    side of an acceptance, rather than passing only while the entry is
    un-accepted and raising KeyError the day it is.
    """
    replacement = entry["replacement"]
    return replacement.get("normal_path", replacement.get("status"))



ROOT = Path(__file__).resolve().parents[1]
POISON = "/DO-NOT-READ-OR-WRITE-DRIVE"
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(("  ok    " if condition else "  FAIL  ") + label + (f" — {detail}" if detail else ""))
    if not condition:
        FAILED.append(label)


def fake_python(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/usr/bin/python3\n"
        "import json, os, sys\n"
        "with open(os.environ['CAPTURE'], 'a', encoding='utf-8') as out:\n"
        " out.write(json.dumps({'args': sys.argv[1:], 'env': dict(os.environ)}) + '\\n')\n",
        encoding="utf-8",
    )
    path.chmod(0o700)


def run_learning(script_name: str, root: Path) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
    script = root / "bin" / script_name
    script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "bin" / script_name, script)
    # Both learning scripts source bin/routine-credential-env.sh for the shared
    # db.env sourceability guard, so the fixture bin/ must carry it too.
    shutil.copy2(ROOT / "bin" / "routine-credential-env.sh",
                 script.parent / "routine-credential-env.sh")
    fake_python(root / ".venv" / "bin" / "python")
    home = root / "home"
    db = home / ".config" / "carr" / "db.env"
    db.parent.mkdir(parents=True)
    db.write_text("CARR_DB_JOBS_URL=postgresql://jobs:fixture@db/carr\n", encoding="utf-8")  # ci-secret-scan: allow — fixture
    capture = root / f"{script_name}.jsonl"
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "CARR_VAULT": POISON,
           "CAPTURE": str(capture)}
    result = subprocess.run(["/bin/zsh", str(script)], env=env, text=True, capture_output=True,
                            check=False)
    rows = [json.loads(line) for line in capture.read_text(encoding="utf-8").splitlines()]
    return result, rows


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for name, expected in (
            ("learning-weekly.sh", 2),
            ("learning-monthly.sh", 2),  # monthly chain plus corrections sweep
        ):
            result, rows = run_learning(name, root / name)
            all_text = json.dumps(rows)
            check(f"{name} normal run completes with its canonical report output",
                  result.returncode == 0 and len(rows) == expected,
                  f"rc={result.returncode} rows={len(rows)} stderr={result.stderr!r}")
            check(f"{name} children receive no poisoned Drive root", POISON not in all_text)
            check(f"{name} reports target repo-local out/Learning",
                  any(str(root / name / "out" / "Learning") in str(row["args"]) for row in rows))

        prompt = (ROOT / "ops/scheduled-tasks/social-metrics-pull-weekly.SKILL.md").read_text()
        slugs = re.findall(r'r = Report\("(weekly-learning|correction-miner)"',
                           (ROOT / "pipelines/learning_jobs.py").read_text())
        pull = (ROOT / "pipelines/pull_placement_metrics.py").read_text()
        check("social metrics verifies the weekly producer's repo-local reports",
              re.findall(r"^- ~/carr-system/out/Learning/([^\s`]+)", prompt, re.M)
              == ["placement-pull-latest.md"] + [f"{slug}-latest.md" for slug in slugs]
              and 'Path(a.report_dir) / "placement-pull-latest.md"' in pull)
        check("social metrics has no retired report destination",
              "Automation/Learning/" not in prompt)

        dashboard_python = ROOT / ".venv" / "bin" / "python"
        if not dashboard_python.exists():
            dashboard_python = Path(sys.executable)
        dashboard = subprocess.run([str(dashboard_python), str(ROOT / "generators" / "build-open-items-dashboard.py")],
                                   env={"PATH": "/usr/bin:/bin", "CARR_VAULT": POISON},
                                   text=True, capture_output=True, check=False)
        check("legacy dashboard generator remains recovery-only",
              dashboard.returncode != 0 and "MISSING_CANONICAL_SEAM" in dashboard.stderr and POISON not in dashboard.stderr,
              dashboard.stdout + dashboard.stderr)

        recovery = subprocess.run([str(dashboard_python), str(ROOT / "generators" / "build-open-items-dashboard.py"),
                                   "--recovery"], env={"PATH": "/usr/bin:/bin"}, text=True,
                                  capture_output=True, check=False)
        check("dashboard recovery requires a nonblank reason", recovery.returncode != 0 and
              "nonblank --reason" in recovery.stderr)

        nightly = (ROOT / "bin" / "nightly.sh").read_text(encoding="utf-8")
        normal = nightly[nightly.index('unset CARR_VAULT CARR_EXPORT_LIVE'):]
        check("nightly clears ambient Drive before launching jobs",
              normal.index('unset CARR_VAULT CARR_EXPORT_LIVE CARR_DRIVE_RECOVERY')
              < normal.index('carr_routine_exec'))
        registry = json.loads((ROOT / "ops/config/drive-dependencies.v1.json").read_text())
        registry_rows = {row["id"]: row for row in registry["entries"]}
        check("Drive registry names the record-native dashboard replacement",
              _normal_path(registry_rows["normal-dashboard-render"])
              == "normal_path_repointed_to_record_native_boards")

    print(f"scheduled Drive writer selftest — {len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
