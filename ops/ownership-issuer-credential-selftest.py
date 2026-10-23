#!/usr/bin/env python3
"""Offline contract checks for WR126's inert ownership-issuer runtime.

This suite intentionally uses the pure planner and temporary JSON fixtures. It
does not contact Neon, Wrangler, Cloudflare, a Worker, or any credential
store.  The sentinel check makes secret-byte output a hard failure even when
the fixture is deliberately hostile.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PLANNER = ROOT / "bin" / "provision-canonical-ownership-runtime.mjs"
WRANGLER = ROOT / "mcp-server" / "wrangler.toml"
SENTINEL = "WR126-SENTINEL-DO-NOT-PRINT"


def check(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}" if detail else label)


def run_planner(*args: str, expect: int = 0) -> dict:
    result = subprocess.run(
        ["node", str(PLANNER), "--dry-run", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={"PATH": os.environ.get("PATH", "")},
    )
    check("planner exit status", result.returncode == expect,
          f"wanted {expect}, got {result.returncode}: {result.stderr.strip()}")
    check("planner does not emit sentinel bytes", SENTINEL not in result.stdout + result.stderr)
    if expect == 0:
        return json.loads(result.stdout)
    return {}


def main() -> int:
    failures: list[str] = []
    try:
        check("planner exists", PLANNER.is_file())
        check("planner declares two generation logins",
              "carr_ownership_issuer_g1" in PLANNER.read_text() and
              "carr_ownership_issuer_g2" in PLANNER.read_text())
        check("planner uses the shared NOLOGIN capability",
              "carr_ownership_issuer" in PLANNER.read_text())

        initial = run_planner("--operation", "readback")
        state = initial["result"]
        slots = state["generations"]
        check("two distinct secret slots",
              slots["1"]["secret_slot"] != slots["2"]["secret_slot"])
        check("readback names slots but never presents their values",
              all(isinstance(row["secret_slot"], str) for row in slots.values()))
        check("readback is explicitly dry-run", initial["dry_run"] is True)

        with tempfile.TemporaryDirectory(prefix="wr126-ownership-selftest-") as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            staged = run_planner("--operation", "stage", "--generation", "1",
                                 "--state-file", str(state_path))
            staged_state = staged["result"]
            check("disabled stages only to canary_only", staged_state["mode"] == "canary_only")
            check("generation 1 becomes active", staged_state["generations"]["1"]["state"] == "active")

            state_path.write_text(json.dumps(staged_state), encoding="utf-8")
            rotated = run_planner("--operation", "rotate", "--generation", "2",
                                  "--mode", "canary_only", "--state-file", str(state_path))
            rotated_state = rotated["result"]
            check("rotation activates generation 2", rotated_state["active_generation"] == 2)
            check("rotation drains generation 1", rotated_state["generations"]["1"]["state"] == "draining")

            state_path.write_text(json.dumps(rotated_state), encoding="utf-8")
            attended = run_planner("--operation", "rotate", "--generation", "2",
                                   "--mode", "attended_active", "--state-file", str(state_path))
            attended_state = attended["result"]
            check("canary_only promotes only through attended_active",
                  attended_state["mode"] == "attended_active")

            state_path.write_text(json.dumps(attended_state), encoding="utf-8")
            drained = run_planner("--operation", "drain", "--generation", "2",
                                  "--state-file", str(state_path))
            drained_state = drained["result"]
            check("drain removes active pointer", drained_state["active_generation"] is None)
            check("drain preserves draining state", drained_state["generations"]["2"]["state"] == "draining")

            state_path.write_text(json.dumps(drained_state), encoding="utf-8")
            revoked = run_planner("--operation", "revoke", "--generation", "2",
                                  "--state-file", str(state_path))
            revoked_state = revoked["result"]
            check("revoke returns to disabled when no active slot remains",
                  revoked_state["mode"] == "disabled")

        run_planner("--operation", "stage", "--generation", "1", expect=0)
        run_planner("--operation", "stage", "--generation", "1", "--mode", "attended_active",
                    expect=64)
        # No live path exists, even if a caller omits the dry-run switch.
        live = subprocess.run(["node", str(PLANNER), "--operation", "readback"],
                              cwd=ROOT, text=True, capture_output=True, check=False)
        check("live provisioning is refused", live.returncode == 78)
        check("live refusal does not echo sentinel", SENTINEL not in live.stdout + live.stderr)

        wrangler = WRANGLER.read_text(encoding="utf-8")
        check("production runtime remains disabled",
              wrangler.count('CANONICAL_OWNERSHIP_RUNTIME_MODE = "disabled"') == 1)
    except (AssertionError, KeyError, json.JSONDecodeError, OSError) as error:
        failures.append(str(error))

    if failures:
        print("ownership issuer credential selftest — FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("ownership issuer credential selftest — PASS (offline dry-run only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
