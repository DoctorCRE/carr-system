#!/usr/bin/env python3
"""Report failed local scheduled jobs and low disk, or print their digest."""
import argparse
import json
import os
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "lib"))
from launchd_hold import read_holds
import job_watchdog as watchdog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.environ.get("CARR_WATCHDOG_CONFIG", str(SOURCE / "ops/config/job-watchdog.json")))
    parser.add_argument("--root", default=os.environ.get("CARR_JOB_ROOT"))
    parser.add_argument("mode", choices=["scan", "digest", "inbox", "social-batch-result"])
    parser.add_argument("--week-start")
    parser.add_argument("--queue-count", type=int)
    parser.add_argument("--queue-complete", action="store_true")
    parser.add_argument("--failure", choices=sorted(watchdog.SOCIAL_BATCH_FAILURES - {"zero_drafts"}))
    args = parser.parse_args()
    hold = read_holds().get("com.carr.job-watchdog")
    if hold and args.mode not in {"social-batch-result", "inbox"}:
        print(hold.describe())
        return 0
    config = watchdog.load_config(args.config)
    root = Path(args.root).resolve() if args.root else Path(watchdog.command(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], config, SOURCE).strip()).parent
    if args.mode == "inbox":
        path = watchdog.path_at(root, config["paths"]["orchestrator_inbox"])
        print(json.dumps(list(watchdog.read_latest(path).values())))
        return 0
    if args.mode == "digest":
        print(watchdog.digest(root, config))
        return 0
    if args.mode == "social-batch-result":
        try:
            result = watchdog.report_social_batch(
                root, config, week_start=args.week_start, queue_count=args.queue_count,
                queue_complete=args.queue_complete, failure=args.failure)
        except ValueError as exc:
            parser.error(str(exc))
        count = result["queue_count"] if result["queue_count"] is not None else "unknown"
        failure = result["failure_class"] or "none"
        print(f"SOCIAL_BATCH_RESULT state={result['state']} week_start={result['week_start']} "
              f"queue_count={count} complete={str(result['queue_complete']).lower()} "
              f"failure_class={failure}")
        if result["state"] == "failed":
            print(watchdog.digest(root, config))
            return 1
        return 0
    return watchdog.scan(root, config)


if __name__ == "__main__":
    raise SystemExit(main())
