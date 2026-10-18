from __future__ import annotations

import fcntl
import platform
import time
from dataclasses import replace
from pathlib import Path
from lib.launchd_hold import read_holds
from lib import repair_loop

THRESHOLD_SECONDS = 7 * 86400
ACTION = ("on breach: open/update one deduplicated repair loop per label · owner claude (Platform Engineer) · "
          "repair the hold's named cause, verify the job's expected output, then remove its hold "
          "and run config-as-code.py install --apply · verify check no longer reports HELD · "
          "auto-clear when the hold is removed or its dated review renews it to at most 7d")


def check(home, verb, now=None):
    now = time.time() if now is None else now
    directory = Path(home) / ".config/carr"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        with (directory / "launchd-hold-health.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            holds = read_holds(home)
            path = directory / "launchd-hold-health.json"
            state = repair_loop.read_state(path, lambda value: isinstance(value, dict) and
                                           all(isinstance(row, dict) for row in value.values()))
            def save():
                repair_loop.save_state(path, state)
            for label, hold in holds.items():
                row = state.setdefault(label, {})
                # First observation uses the legacy file age conservatively.
                # Later unrelated file edits never renew this label.
                if hold.age_source != "hold start":
                    row.setdefault("first_seen", min(now, hold.since))
                    holds[label] = replace(hold, since=row["first_seen"], age_source="first seen")
                else:
                    row["first_seen"] = hold.since
            save()
            stale = {label: hold for label, hold in holds.items() if now - hold.since > THRESHOLD_SECONDS}
            for label in list(state):
                row = state[label]
                hold = stale.get(label)
                desired = None
                if hold:
                    body = (f"Launchd hold older than 7 days on {platform.node()}: {label}, {hold.reason}. "
                            f"Hold start epoch {hold.since}; measured from {hold.age_source}. "
                            "Repair the named cause and verify the job's expected output before "
                            "removing the hold and running config-as-code.py install --apply. "
                            "Auto-clear when absent or explicitly reviewed and renewed to at most 7 days.")
                    desired = {"kind": "open_loop", "owner": "claude", "domain": "system", "body": body,
                               "blocker": "other_lane", "blocker_detail":
                               f"Platform Engineer must repair the held {label} job on {platform.node()}: {hold.reason}"}
                outcome = (f"Verified {label}'s hold is absent." if label not in holds else
                           f"Verified {label} carries a dated renewal to at most 7 days.")
                if label in holds and holds[label].age_source != "hold start":
                    outcome = None
                repair_loop.reconcile(row, desired, verb, save, outcome=outcome, refresh=True)
                if label not in holds:
                    del state[label]
                    save()
            if stale:
                summary = "; ".join(hold.describe(now) for hold in stale.values())
                return f"WARN launchd holds {summary} · repair loops recorded · {ACTION}", 1
            return f"OK launchd holds {len(holds)} held; none older than 7d · {ACTION}", 0
    except (OSError, ValueError, RuntimeError) as exc:
        return f"WARN launchd holds response failed ({type(exc).__name__}: {exc}) · {ACTION}", 1
