#!/usr/bin/env python3
# doctrine: job-watchdog
"""Give the orchestrator open watchdog findings without running a scan or action."""
import json
import os
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "lib"))
import job_watchdog as watchdog  # noqa: E402


def main():
    config = watchdog.load_config(os.environ.get("CARR_WATCHDOG_CONFIG", str(SOURCE / "ops/config/job-watchdog.json")))
    try:
        root = Path(os.environ["CARR_JOB_ROOT"]) if os.environ.get("CARR_JOB_ROOT") else Path(
            watchdog.command(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], config, SOURCE).strip()).parent
        context = watchdog.digest(root, config)
    except (OSError, ValueError, RuntimeError) as exc:
        context = "Orchestrator watchdog digest unavailable; inspect out/watchdog/findings.jsonl: " + str(exc)
    if context:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
