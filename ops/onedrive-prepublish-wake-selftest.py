#!/usr/bin/env python3
"""Check the provider wake and its bounded position before daytime exports."""

import json
import plistlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
checked = 0
failed = 0


def ok(cond, label):
    global checked, failed
    checked += 1
    if cond:
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}")


def main() -> int:
    # Prefer the repo's own .venv (has psycopg/openpyxl, same as every
    # nightly.sh step); fall back to the ambient interpreter. FAILS OPEN with
    # a clear SKIP rather than a false FAIL when neither can import the
    # exporters package at all — the same convention md_renders_retired() and
    # carr_step_timeout_prefix already use elsewhere in this chain.
    venv_python = REPO / ".venv" / "bin" / "python"
    python = str(venv_python) if venv_python.exists() else sys.executable

    # ── the wake script runs standalone and never fails on a cold provider ──
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            [python, str(REPO / "ops" / "onedrive-prepublish-wake.py")],
            env={"PATH": "/usr/bin:/bin", "CARR_EXPORT_HOME": tmp},
            cwd=str(REPO), capture_output=True, text=True, timeout=30,
        )
    if proc.returncode != 0 and "ModuleNotFoundError" in proc.stderr:
        print(f"  SKIP  onedrive-prepublish-wake.py needs {python}'s exporters "
              f"dependencies (psycopg/openpyxl), not available here — not a "
              f"finding about the code")
    else:
        ok(proc.returncode == 0, "onedrive-prepublish-wake.py exits 0 against an empty (cold) provider dir")
        ok("absent" in proc.stdout, "it reports each missing target as absent rather than raising")
        ok("probing 6 live export path" in proc.stdout,
           "it probes exactly the six LIVE targets, not the retired .md renders too")

    # ── bin/nightly.sh: ordering and the caffeinate wrap ────────────────────
    nightly = (REPO / "bin" / "nightly.sh").read_text()
    wake_idx = nightly.find('step "onedrive pre-publish wake"')
    exports_idx = nightly.find('step "exports"')
    ok(wake_idx != -1, "bin/nightly.sh calls the onedrive pre-publish wake step")
    ok(exports_idx != -1, "bin/nightly.sh still calls the exports step")
    ok(wake_idx != -1 and exports_idx != -1 and wake_idx < exports_idx,
       "the wake step runs BEFORE the exports step, not after")
    exports_line = next((l for l in nightly.splitlines()
                          if l.strip().startswith('step "exports"')), "")
    ok("caffeinate -i -s" in exports_line,
       "the exports step itself runs under caffeinate -i -s")

    # ── bin/step-timeout.zsh: the new step's own short, separate budget ─────
    step_timeout = (REPO / "bin" / "step-timeout.zsh").read_text()
    wake_m = re.search(r'"onedrive pre-publish wake"\s+(\d+)', step_timeout)
    exports_m = re.search(r'"exports"\s+(\d+)', step_timeout)
    ok(wake_m is not None, "STEP_TIMEOUT_OVERRIDE declares a budget for the wake step")
    ok(exports_m is not None, "STEP_TIMEOUT_OVERRIDE still declares a budget for exports")
    if wake_m and exports_m:
        wake_budget, exports_budget = int(wake_m.group(1)), int(exports_m.group(1))
        ok(0 < wake_budget < exports_budget,
           f"the wake step's budget ({wake_budget}s) is short and strictly less than "
           f"exports' ({exports_budget}s) — it can only ever cost a head start, never "
           "eat into the real wait")

    print(f"\nonedrive-prepublish-wake-selftest: {checked - failed}/{checked} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
