#!/usr/bin/env python3
"""Print unusually slow completed runs once. Workflows own their timeouts."""
import argparse
from datetime import datetime
import json
import subprocess


def slow_runs(rows, minutes):
    lines = []
    for row in rows:
        if row["status"] != "completed" or not row.get("startedAt"):
            continue
        start = datetime.fromisoformat(row["startedAt"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(row["updatedAt"].replace("Z", "+00:00"))
        elapsed = (end - start).total_seconds() / 60
        if elapsed > minutes:
            lines.append(f"{row['workflowName']} took {elapsed:.1f} min {row['url']}")
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", action="append")
    parser.add_argument("--minutes", type=float, default=15)
    args = parser.parse_args()
    for repo in args.repo or ("jbookout/carr-system", "jbookout/doctorcre-app", "jbookout/software-factory"):
        result = subprocess.run(["gh", "run", "list", "--repo", repo, "--status", "completed",
                                 "--limit", "50", "--json", "workflowName,startedAt,updatedAt,url,status"],
                                check=True, capture_output=True, text=True, timeout=30)
        for line in slow_runs(json.loads(result.stdout), args.minutes):
            print(f"{repo} {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
