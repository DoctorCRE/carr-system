#!/usr/bin/env python3
"""Report failed local scheduled jobs and low disk, or print their digest."""
import argparse
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
    parser.add_argument("mode", choices=["scan", "digest"])
    args = parser.parse_args()
    hold = read_holds().get("com.carr.job-watchdog")
    if hold:
        print(hold.describe())
        return 0
    config = watchdog.load_config(args.config)
    root = Path(args.root).resolve() if args.root else Path(watchdog.command(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], config, SOURCE).strip()).parent
    if args.mode == "digest":
        print(watchdog.digest(root, config))
        return 0
    return watchdog.scan(root, config)


if __name__ == "__main__":
    raise SystemExit(main())
