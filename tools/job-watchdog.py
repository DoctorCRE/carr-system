#!/usr/bin/env python3
"""Run registered jobs, scan deterministic evidence, or print an orchestrator digest."""
import argparse
import os
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "lib"))
from launchd_hold import read_holds
import job_watchdog as watchdog  # noqa: E402


def main():
    hold = read_holds().get("com.carr.job-watchdog")
    if hold:
        print(hold.describe())
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.environ.get("CARR_WATCHDOG_CONFIG", str(SOURCE / "ops/config/job-watchdog.json")))
    parser.add_argument("--root", default=os.environ.get("CARR_JOB_ROOT"))
    commands = parser.add_subparsers(dest="mode", required=True)
    run = commands.add_parser("run")
    run.add_argument("card")
    run.add_argument("executor")
    run.add_argument("minutes", type=float)
    run.add_argument("argv", nargs=argparse.REMAINDER)
    commands.add_parser("scan")
    commands.add_parser("vendor-watch", help="Run only the hourly vendor release checks; no agent actions")
    commands.add_parser("digest")
    args = parser.parse_args()
    config = watchdog.load_config(args.config)
    if os.environ.get("CARR_JOB_BOARD"):
        config["board"] = os.environ["CARR_JOB_BOARD"]
    root = Path(args.root).resolve() if args.root else Path(watchdog.command(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], config, SOURCE).strip()).parent
    if args.mode == "run":
        argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        return watchdog.run_job(root, config, args.card, args.executor, args.minutes, argv)
    if args.mode == "digest":
        print(watchdog.digest(root, config))
        return 0
    return watchdog.scan(root, config, args.config, vendor_only=args.mode == "vendor-watch")


if __name__ == "__main__":
    raise SystemExit(main())
