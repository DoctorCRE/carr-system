#!/usr/bin/env python3
"""Run an explicitly requested local job with a deadline and recorded exit."""
import argparse
import os
from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "lib"))
import local_jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=os.environ.get("CARR_JOB_ROOT"))
    parser.add_argument("card")
    parser.add_argument("executor")
    parser.add_argument("minutes", type=float)
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    config = local_jobs.load_config()
    if os.environ.get("CARR_JOB_BOARD"):
        config["board"] = os.environ["CARR_JOB_BOARD"]
    root = Path(args.root).resolve() if args.root else Path(local_jobs.command(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], config, SOURCE).strip()).parent
    argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
    return local_jobs.run_job(root, config, args.card, args.executor, args.minutes, argv)


if __name__ == "__main__":
    raise SystemExit(main())
