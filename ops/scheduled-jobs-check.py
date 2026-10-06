#!/usr/bin/env python3
"""Read installed CARR launchd jobs, user cron and canonical checkout drift."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import scheduled_jobs as jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(jobs.SOURCE / "ops/config/scheduled-jobs.v1.json"))
    parser.add_argument("--fixture", help="read a captured evidence fixture instead of this machine")
    parser.add_argument("--now", type=float, default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--capture", action="store_true", help="print a draft manifest from live definitions; expected states require review")
    args = parser.parse_args()
    try:
        if args.capture:
            print(json.dumps(jobs.capture_manifest(), indent=2))
            return 0
        snapshot = json.loads(Path(args.fixture).read_text()) if args.fixture else None
        rows = jobs.check(args.manifest, snapshot, args.now)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        rows = [{"label": "checker", "code": "evidence_unavailable",
                 "key": "scheduled_jobs:checker:evidence_unavailable", "owner": "orchestrator",
                 "detail": "manifest or evidence could not be interpreted (" + type(exc).__name__ + ")",
                 "fix": "restore the versioned manifest or named evidence source and rerun"}]
    if args.json:
        print(json.dumps(rows))
    else:
        for line in jobs.render(rows):
            print(line)
        if not rows:
            print("OK scheduled jobs: declared jobs match live evidence and canonical main")
    return int(bool(rows))


if __name__ == "__main__":
    raise SystemExit(main())
