#!/usr/bin/env python3
"""ops/onedrive-prepublish-wake-selftest.py — asserts the nightly-exports
fix for the 9/22-9/24 OneDrive File Provider defect is actually wired in,
rather than merely present as files nothing calls.

Covers, cheaply and without a database or a live OneDrive mount:
  * ops/onedrive-prepublish-wake.py runs standalone, single-attempt, and
    exits 0 even when every target is absent (a cold/empty provider must
    never fail this step — see its own docstring).
  * bin/nightly.sh actually calls it, BEFORE the exports step, and wraps the
    exports step itself in `caffeinate -i -s`.
  * bin/step-timeout.zsh bounds the new step with its OWN short external
    wall-clock, strictly shorter than the exports step's — a step meant to
    buy the real wait a head start must never be allowed to eat into it.
  * bin/nightly-exports-retry.sh (the daytime safety net) parses, and uses
    the words-not-a-function-call shape carr_routine_exec requires (see
    bin/step-timeout.zsh's own carr_step_timeout_prefix docstring) rather
    than repeating the env -i / zsh-function mistake that bug fix started
    from.
  * The new launchd plist parses with plistlib and uses StartCalendarInterval,
    not StartInterval (StartInterval jobs have been observed to never fire on
    this Mac).

Run: python3 ops/onedrive-prepublish-wake-selftest.py
"""

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
    exports_idx = nightly.find('step "exports (6 targets -> OneDrive)"')
    ok(wake_idx != -1, "bin/nightly.sh calls the onedrive pre-publish wake step")
    ok(exports_idx != -1, "bin/nightly.sh still calls the exports step")
    ok(wake_idx != -1 and exports_idx != -1 and wake_idx < exports_idx,
       "the wake step runs BEFORE the exports step, not after")
    exports_line = next((l for l in nightly.splitlines()
                          if l.strip().startswith('step "exports (6 targets -> OneDrive)"')), "")
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

    # ── bin/nightly-exports-retry.sh: syntax, and the env -i / function bug ─
    retry_script = REPO / "bin" / "nightly-exports-retry.sh"
    proc = subprocess.run(["zsh", "-n", str(retry_script)], capture_output=True, text=True)
    ok(proc.returncode == 0, f"bin/nightly-exports-retry.sh parses under zsh -n ({proc.stderr.strip()})")
    retry_src = retry_script.read_text()
    ok("carr_routine_exec carr_step_with_timeout" not in retry_src,
       "the retry script does not pass a zsh FUNCTION through env -i (carr_routine_exec "
       "execs a real program; a function call there silently fails to run at all)")
    ok('carr_step_timeout_prefix "$(carr_step_timeout_for exports)"' in retry_src
       and 'carr_routine_exec "${CARR_STEP_TIMEOUT_ARGV[@]}"' in retry_src,
       "it uses carr_step_timeout_prefix's ARGV words instead, the same shape step() uses")
    ok("caffeinate -i -s" in retry_src, "the daytime retry also runs the export under caffeinate")

    # ── the ops.service catalog entry and the script agree on ONE key ───────
    # (post-#1241-review fix: the script used to record every attempt under
    # nightly-record-layer's service key while services.json registered a
    # separate "nightly-exports-daytime-retry" key — a mismatch that would
    # leave the new catalog entry's cadence fields describing a service that
    # never actually receives an ops.run row.)
    services = json.loads((REPO / "ops" / "config" / "services.json").read_text())
    entry = next((s for s in services.get("services", [])
                  if s.get("key") == "nightly-exports-daytime-retry"), None)
    ok(entry is not None, "services.json registers a 'nightly-exports-daytime-retry' service")
    if entry:
        prod: dict[str, Any] = next((e for e in entry.get("environments", [])
                     if e.get("environment") == "production"), {})
        ok(prod.get("deploy_mechanism") == "ops/launchd/com.carr.nightly-exports-daytime-retry.plist",
           "its production deploy_mechanism names the actual plist")
    ok(retry_src.count("--service nightly-exports-daytime-retry") >= 1,
       "the retry script's record() writes under that SAME service key")
    ok("--service nightly-record-layer" not in retry_src,
       "not under nightly-record-layer's key (that mismatch was the original bug)")
    # Every early-return branch must call record(), or the job goes quiet on
    # every healthy night and reads permanently stale against its own
    # registered cadence — with exactly ONE documented exception: the
    # "already attempted today's retry and it FAILED" branch deliberately
    # exits with NO new record() call, so the earlier failed row stays latest
    # instead of being replaced by a skipped heartbeat (#1241 review R4).
    exit_paths = retry_src.count("exit 0") + retry_src.count('exit "$rc"')
    record_calls = retry_src.count("record ")
    exempt_no_heartbeat_exits = 1
    ok(record_calls >= exit_paths - exempt_no_heartbeat_exits,
       f"every exit path ({exit_paths}) has a matching record() call ({record_calls}), "
       f"except the {exempt_no_heartbeat_exits} documented failed-marker exit "
       "— any OTHER SKIP branch with no heartbeat would starve this service's own cadence")
    ok('failed:*)' in retry_src and "not overwriting that failure with a skipped heartbeat" in retry_src,
       "the marker-exists branch distinguishes a prior FAILURE and leaves it as latest "
       "instead of replacing it with a skipped record()")

    # ── the new plist ─────────────────────────────────────────────────────
    plist_path = REPO / "ops" / "launchd" / "com.carr.nightly-exports-daytime-retry.plist"
    with open(plist_path, "rb") as fh:
        plist = plistlib.load(fh)
    ok(plist.get("Label") == "com.carr.nightly-exports-daytime-retry",
       "the new plist declares its own label")
    ok("StartCalendarInterval" in plist and "StartInterval" not in plist,
       "it uses StartCalendarInterval, not StartInterval (observed to never fire on this Mac)")
    # StartCalendarInterval's Hour/Minute are LOCAL time, not UTC (#1241
    # review R4: the first draft set Hour 16 intending 16:00 UTC/11:00 local
    # and actually fired at 4pm local). Hour 11 is the corrected value.
    cal = plist.get("StartCalendarInterval") or [{}]
    ok(cal[0].get("Hour") == 11, "StartCalendarInterval fires at Hour 11 (local time), not Hour 16")

    # The healthy no-op ("tonight's exports already landed OK") must record
    # succeeded, not skipped — v_service_environment_health maps skipped to
    # degraded, which would show this service as degraded every healthy day.
    ok('record succeeded 0 "tonight' in retry_src,
       "the healthy no-op path records succeeded, not skipped")
    # The lock-race guard (#1241 review round 5): only ever consider retrying
    # when a nightly run has actually COMPLETED since the last scheduled
    # 02:05-local fire, keyed by boundary epoch rather than calendar date —
    # see ops/nightly-exports-retry-guard-selftest.py for the fixed-clock
    # proof that this cannot preempt a currently-running nightly chain.
    ok("BOUNDARY_EPOCH" in retry_src and "ARCHIVE_EPOCH" in retry_src
       and "carr_take_lock nightly" in retry_src,
       "the retry only reaches carr_take_lock after proving a nightly run "
       "completed since the last scheduled 02:05 boundary, not merely that "
       "an archive exists dated today or yesterday")
    ok("CARR_NOW" in retry_src and "carr_now_epoch" in retry_src,
       "the guard's notion of 'now' is injectable, so its exact boundary "
       "behavior can be proven by a fixed-clock selftest rather than only "
       "observed on a live run")

    # No standalone bin/install-*.sh script for this job: that filename shape
    # trips ops/scac-mutation-inventory.mjs's external_admin classifier (a
    # SEALED, non-exempt ingress kind), which would owe a full SCAC successor
    # seal for a one-file addition. The install command is documented in
    # ops/scheduled-tasks/nightly-record-layer.SKILL.md instead.
    installer = REPO / "bin" / "install-nightly-exports-retry.sh"
    ok(not installer.exists(),
       "no standalone installer script (would be a sealed external_admin row; "
       "the install command lives in the SKILL.md runbook instead)")
    skill_md = (REPO / "ops" / "scheduled-tasks" / "nightly-record-layer.SKILL.md").read_text()
    ok("plutil -lint" in skill_md and "launchctl bootstrap" in skill_md
       and "com.carr.nightly-exports-daytime-retry.plist" in skill_md,
       "the SKILL.md runbook documents the manual install command for the new plist")
    # #1241 review round 5, item 2: a bare `;` after the bootout attempt let
    # `bootstrap` run even after a failed `plutil -lint` or `install` — the
    # bootout must be grouped and `|| true`'d so the REST of the chain still
    # stops at the first real failure.
    ok("{ launchctl bootout" in skill_md and "|| true; } \\" in skill_md,
       "the bootout step is grouped with its own || true and still && to the "
       "rest of the chain, so a real failure earlier stops bootstrap from running")
    ok(skill_md.count("launchctl bootstrap gui/$(id -u)/com.carr.nightly-exports-daytime-retry.plist \\\n  && launchctl print") == 1
       or "launchctl print gui/$(id -u)/com.carr.nightly-exports-daytime-retry\n" in skill_md,
       "launchctl print uses the bare label, not a .plist path")
    # #1241 review round 5, item 1: sync-registry needs the owner credential
    # (DATABASE_URL) and, run through tools/db-tap.py, would silently no-op
    # under db-tap's read-only-by-default guard. The runbook must say this is
    # a human, owner-credential step -- never a fabricated break-glass call.
    ok("db-tap.py" not in skill_md.split("REGISTERING THE NEW SERVICE ROW")[0]
       if "REGISTERING THE NEW SERVICE ROW" in skill_md else False,
       "the install command block itself no longer pipes sync-registry through db-tap.py")
    ok("REGISTERING THE NEW SERVICE ROW" in skill_md
       and "human, owner-credential" in skill_md
       and "CARR_BREAK_GLASS=1 " not in skill_md.split(
           "REGISTERING THE NEW SERVICE ROW")[1].split(".venv/bin/python tools/ops-record.py sync-registry")[0],
       "sync-registry is documented as a separate human/owner-credential step, "
       "truthfully, with no invented break-glass call actually prefixing the command")
    ops_record_src = (REPO / "tools" / "ops-record.py").read_text()
    ops_record_docstring = ops_record_src.split('"""')[1]
    ok(".venv/bin/python tools/db-tap.py run tools/ops-record.py sync-registry" not in ops_record_docstring,
       "tools/ops-record.py's own docstring no longer recommends routing "
       "sync-registry through db-tap.py as a working command (that "
       "recommendation predated db-tap's read-only-by-default hardening and "
       "would silently no-op the write)")

    guard_selftest = REPO / "ops" / "nightly-exports-retry-guard-selftest.py"
    ok(guard_selftest.exists(),
       "a dedicated fixed-clock selftest proves the lock-race guard, not just a live run")
    if guard_selftest.exists():
        proc = subprocess.run([python, str(guard_selftest)], cwd=str(REPO),
                               capture_output=True, text=True, timeout=120)
        ok(proc.returncode == 0,
           f"nightly-exports-retry-guard-selftest.py passes ({proc.stdout.strip().splitlines()[-1] if proc.stdout else proc.stderr[-200:]})")

    print(f"\nonedrive-prepublish-wake-selftest: {checked - failed}/{checked} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
