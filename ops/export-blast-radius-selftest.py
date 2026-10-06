#!/usr/bin/env python3
"""One failing export target must not cancel the targets after it.

WHY THIS EXISTS. This is the same defect twice, through two different doors, and
the second time nothing caught it because the first fix was a comment plus a
list comprehension.

2026-08-02: `all(...)` short-circuited, an unbootstrapped decision-history target
aborted the sweep, and five generated files went stale. Fixed by running every
target and failing afterwards.

2026-08-25 through 08-27 (loop #535): the sweep died again, for three nights, on
the SAME shape. run_export() returns False for a failure it handles, but
keep_generation() re-raises OSError once its EDEADLK retry budget is spent —
deliberately, because skipping the dated copy would discard the rollback
guarantee. OneDrive's FileProvider locks the curriculum dashboard intermittently
around the 02:05 window; `curriculum` is the FIRST key in TARGETS; the raise
escaped the comprehension and took down all six exports. client-roster.xlsx,
lead-registry.xlsx, lead-router-2026-07-13.xlsx, panhandle-team-deals.json and
vendors.xlsx sat stale at 2026-08-25T07:18 while the chain reported one cause as
two red steps.

THE CONTRACT UNDER TEST, and the reason this file exists rather than a third
comment: a target that RAISES is that target's failure and nothing more. The
sweep continues, every remaining target runs, and the exit code still reports
the failure. Case 3 is the regression guard proper — it fails against the
comprehension the fix replaced.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_VENV = REPO / ".venv" / "bin" / "python"
PY = _VENV if _VENV.exists() else Path(sys.executable)

# The probe drives the REAL main(), with only TARGETS and run_export replaced.
# Stubbing main() itself would test the stub. CARR_MD_RENDERS_RETIRED pins the
# cutoff flag so the probe never opens a database connection.
PROBE = r'''
import sys
from exporters import run_exports

RAN = []

def fake_run_export(key, rel, fn, bootstrap=False):
    RAN.append(key)
    if key == "raiser":
        raise OSError(11, "Resource deadlock avoided")
    if key == "returns-false":
        return False
    return True

run_exports.run_export = fake_run_export
run_exports.TARGETS = {
    "raiser":        ("a.json", None),
    "returns-false": ("b.json", None),
    "healthy-one":   ("c.json", None),
    "healthy-two":   ("d.json", None),
}
sys.argv = ["run_exports"]

code = 0
try:
    run_exports.main()
except SystemExit as e:
    code = e.code or 0

print("RAN=" + ",".join(RAN))
print("EXIT=" + str(code))
'''


def probe():
    """Run the probe and return (ordered target keys that ran, exit code)."""
    out = subprocess.run(
        [str(PY), "-c", PROBE],
        cwd=REPO,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "CARR_MD_RENDERS_RETIRED": "0",
             "HOME": str(Path.home())},
    )
    if out.returncode != 0:
        # A probe that DIES is not a broken probe, it is the defect itself: the
        # exception escaped main() instead of being confined to its target. Say
        # that, rather than making the next reader parse a traceback to find out.
        tail = (out.stderr or out.stdout).strip().splitlines()[-1:] or ["(no output)"]
        raise AssertionError(
            "the sweep ABORTED on a raising target instead of confining it — "
            f"exception escaped exporters.run_exports.main(): {tail[0]}")
    ran, code = None, None
    for line in out.stdout.splitlines():
        if line.startswith("RAN="):
            ran = [k for k in line[4:].split(",") if k]
        elif line.startswith("EXIT="):
            code = int(line[5:])
    if ran is None or code is None:
        raise AssertionError(f"probe printed nothing usable:\n{out.stdout}\n{out.stderr}")
    return ran, code


def main() -> int:
    failures = []
    ran, code = probe()

    # 1. A raising target does not stop the sweep. This is the whole loop #535
    #    finding: `raiser` is first, and the three after it must still run.
    if ran != ["raiser", "returns-false", "healthy-one", "healthy-two"]:
        failures.append(
            f"a raising first target cancelled the rest of the sweep: ran {ran}")

    # 2. The failure is still reported. Isolating the blast radius must not
    #    downgrade a failed export into a green run — that would trade three
    #    stale nights for silent ones, which is worse.
    if code == 0:
        failures.append("sweep exited 0 despite a raising target and a False target")

    # 3. Both failure shapes count. run_export returning False and run_export
    #    raising are the same outcome to the caller; only one of them was
    #    handled before this fix.
    if "healthy-two" not in ran:
        failures.append("the last target never ran, so isolation is partial")

    for f in failures:
        print(f"FAIL  {f}", file=sys.stderr)
    if failures:
        return 1
    print(f"ok  export blast radius confined — {len(ran)} targets ran, "
          f"exit {code} still reports the failures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
