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

        calendar_root = root / "calendar"
        (calendar_root / "bin").mkdir(parents=True)
        capture = calendar_root / "calendar.json"
        canonical = calendar_root / "bin" / "calendar-eventkit-capture.sh"
        canonical.write_text(
            "#!/bin/sh\n[ -z \"${CARR_VAULT+x}\" ] || exit 99\n: > \"$CAPTURE\"\n",
            encoding="utf-8",
        )
        canonical.chmod(0o700)
        env = {"PATH": "/usr/bin:/bin", "CARR_REPO": str(calendar_root), "CARR_VAULT": POISON,
               "CAPTURE": str(capture)}
        result = subprocess.run(["/bin/bash", str(ROOT / "bin" / "fetch-calendar.sh")],
                                env=env, text=True, capture_output=True, check=False)
        captured_env = capture.read_text(encoding="utf-8") if capture.exists() else ""
        check("calendar normal run uses the EventKit-to-record seam", result.returncode == 0,
              f"rc={result.returncode} stderr={result.stderr!r}")
        check("calendar normal child receives no poisoned Drive root", POISON not in json.dumps(captured_env))

        missing = subprocess.run(["/bin/bash", str(ROOT / "bin" / "fetch-calendar.sh")],
                                 env={"PATH": "/usr/bin:/bin", "CARR_REPO": str(root / "missing"),
                                      "CARR_VAULT": POISON}, text=True, capture_output=True, check=False)
        check("calendar names a missing canonical seam rather than falling back to Drive",
              missing.returncode == 78 and "MISSING_CANONICAL_SEAM" in missing.stderr and POISON not in missing.stderr)

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

        hard = subprocess.run(["/bin/sh", str(ROOT / "bin" / "routine-canonical-seam-refusal.sh"),
                               "record-backed dashboard destination"], text=True, capture_output=True,
                              check=False)
        admin = subprocess.run(["/bin/sh", str(ROOT / "bin" / "routine-admin-refusal.sh"),
                                "backup capability unavailable"], text=True, capture_output=True,
                               check=False)
        check("missing canonical seams are hard failures, distinct from admin skips",
              hard.returncode == 69 and "MISSING_CANONICAL_SEAM" in hard.stderr
              and admin.returncode == 78 and "routine capability refused" in admin.stderr)

        nightly = (ROOT / "bin" / "nightly.sh").read_text(encoding="utf-8")
        normal, _recovery = nightly.split('if [ "$RECOVERY" -eq 1 ]; then\n  export CARR_EXPORT_LIVE=1', 1)
        check("nightly normal route clears ambient Drive before its first child",
              "unset CARR_VAULT CARR_EXPORT_LIVE" in normal)
        # AN EXACT COUNT, NOT A FLOOR (changed 2026-08-22). This asserted at
        # least eleven Drive projections, which was the wrong shape twice over.
        # A floor cannot notice a projection being REMOVED once the total is
        # comfortably above it, and removal is the direction this file has been
        # moving all week as classes retire. It also counted every mention of
        # the helper's name, including its own definition and the prose about
        # it, so the number never meant what it looked like.
        #
        # Counting step invocations exactly means any change in either
        # direction fails here and gets read by a person — which is what you
        # want, because adding a Drive projection and retiring one are both
        # decisions rather than housekeeping. When this fails, update the number
        # in the same commit that changes the chain and say which step moved.
        #
        # One as of 2026-08-23, down from thirteen. The graph left once its
# last raw disk read was sourced from the records instead. Only
# upstream corroborate is left, and it waits on an external intake.
# Two as of 2026-08-23, down from thirteen. The corpus push left once the
# tool learned to hold its vault rows itself, so its twelve live
# home-rooted rows stopped being switched off by a retired root.
# Before that, three. The consumer boards and
# lead promote left last: neither was ever blocked on a destination,
# only on a pool read that gave up before trying the path that worked.
# Before that, five as of 2026-08-22. The portability mirror
# left last, repointed to the same OneDrive root as the exports.
# Before that, six: the exports gained a
        # canonical destination (CARR OneDrive, decision bdbb7441), the section
        # index and system graph were found already writing inside the repo and
        # only unmarked, and four retired outright — cutover readiness, the
        # vault drift watch at both ends, and the settings mirror.
        projections = len(re.findall(r'^drive_projection "', nightly, re.M))
        # THE NORMAL PATH IS A TOMBSTONE NOW, NOT AN EXECUTED REFUSAL (2026-08-23).
        # This used to also require the literal string
        # "routine-canonical-seam-refusal.sh" somewhere in the file, which the
        # chain no longer launches. That assertion was satisfied by ANY mention of
        # the filename, including a comment — it went on passing after the chain
        # stopped calling the script, which is the same prose-mention hazard
        # ops/nightly-capability-skip-selftest.py strips comments to avoid.
        #
        # So this reads the drive_projection function body instead: the recovery
        # branch must still run the real command, and the normal branch must route
        # to `tombstone` rather than executing anything at all. The 69 exit
        # contract of bin/routine-canonical-seam-refusal.sh is still asserted, by
        # running the script — see the check a few lines above, which is where a
        # claim about that file's behaviour belongs.
        body = re.search(r'^drive_projection\(\) \{.*?^\}', nightly, re.M | re.S)
        body_text = body.group(0) if body else ""
        check("every remaining Drive projection is routed through the recovery envelope",
              projections == 1 and "RECOVERY NONCANONICAL" in nightly
              and bool(body_text) and 'step "RECOVERY NONCANONICAL $label" "$@"' in body_text,
              f"{projections} drive_projection step(s) in bin/nightly.sh")
        check("a refused Drive projection is gated out, not executed to produce its refusal",
              'tombstone "$label"' in body_text
              and "routine-canonical-seam-refusal.sh" not in body_text,
              "the normal branch of drive_projection() still launches a refusal process")
        check("nightly recognizes the record-native dashboard replacement",
              "open-items dashboard replaced by record-native Front Door" in nightly
              and 'drive_projection "open-items dashboard' not in nightly)
        registry = json.loads((ROOT / "ops/config/drive-dependencies.v1.json").read_text())
        registry_rows = {row["id"]: row for row in registry["entries"]}
        check("Drive registry names the record-native dashboard replacement",
              _normal_path(registry_rows["normal-dashboard-render"])
              == "normal_path_repointed_to_record_native_front_door")

    print(f"scheduled Drive writer selftest — {len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
