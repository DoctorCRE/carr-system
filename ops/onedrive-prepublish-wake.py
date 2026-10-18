"""ops/onedrive-prepublish-wake.py — a single-shot nudge to the OneDrive File
Provider, run as its own nightly.sh step immediately BEFORE "exports (6
targets -> OneDrive)".

WHY THIS EXISTS (defect: nightly.exports failed 9/22, 9/23, 9/24; on 9/24 it
consumed the entire 1800s step wall-clock with no partial output at all — see
out/nightly-runs/nightly-20260924T080221Z.log). Jev put the OneDrive File
Provider idling overnight at 0.71.

exporters.common.wait_for_provider() already waits up to
PROVIDER_WAIT_BUDGET_SECONDS (1200s) for the provider before the exports
begin, and bin/nightly.sh's "exports" step already runs under an 1800s
EXTERNAL wall-clock (bin/with-timeout.py, which kills the whole process
group). On 9/24, only that external wrapper bounded a wedged read: the
warm-up then read in-process, where a Python deadline could run only between
probes. That is consistent with zero poll lines before the 1800s kill.
The warm-up now isolates file reads in bounded child processes, so a wedged
read returns a cold-file observation and cannot prevent provider recovery.
This earlier nudge still gives hydration a head start before exports begin.

THIS STEP DOES NOT REPLACE wait_for_provider(). It buys it a head start: one
plain stat + small read per live export path, single attempt, no internal
retry loop and no internal budget of its own — the boundedness comes entirely
from the SAME external per-step wall-clock every other nightly.sh step
already uses (bin/step-timeout.zsh / bin/with-timeout.py), configured short
(see STEP_TIMEOUT_OVERRIDE["onedrive pre-publish wake"] in
bin/step-timeout.zsh) so that even a fully wedged probe costs the chain only
that step's own small budget, never the exports step's larger one.

A cold or unreadable file here is NOT a failure: this step's only job is to
ask the File Provider to start hydrating before the real writes need it to
have already done so. Every outcome is reported and the step exits 0
regardless, matching step()'s existing "the chain survives a step that
fails, never survives one that hangs" contract — this step cannot itself
make the chain hang any longer than its own short external timeout allows.
"""

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from exporters.common import EXPORT_HOME, PROVIDER_PROBE_BYTES  # noqa: E402
from exporters.targets import TARGETS  # noqa: E402


def _wake_one(path: Path) -> str:
    t0 = time.monotonic()
    if not path.exists():
        return f"absent ({time.monotonic() - t0:.2f}s)"
    try:
        with path.open("rb") as stream:
            stream.read(PROVIDER_PROBE_BYTES)
    except OSError as error:
        return f"cold, errno={error.errno} ({time.monotonic() - t0:.2f}s): {error}"
    return f"warm ({time.monotonic() - t0:.2f}s)"


def main() -> int:
    # SIX LIVE TARGETS, NOT ALL OF exporters.targets.TARGETS. TARGETS still
    # carries the retired .md renders (exporters/run_exports.py's
    # md_renders_retired() drops them at export time, matching the "SIX
    # TARGETS, NOT SEVEN" note in bin/nightly.sh). Probing those too would
    # spend this step's short, deliberately-tight budget on OneDrive paths
    # the real exports step never writes any more, leaving less of it for
    # the six that matter. Filtered the same way run_exports.py filters,
    # without its DB round trip: by the same rel-path suffix check.
    paths = sorted({EXPORT_HOME / rel for rel, _fn in TARGETS.values()
                     if not rel.lower().endswith(".md")})
    if not paths:
        print("[onedrive-prepublish-wake] no live (non-retired) export targets registered")
        return 0
    print(f"[onedrive-prepublish-wake] probing {len(paths)} live export path(s) "
          f"under {EXPORT_HOME}")
    for path in paths:
        outcome = _wake_one(path)
        print(f"[onedrive-prepublish-wake] {path.name}: {outcome}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
