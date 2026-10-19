#!/usr/bin/env python3
"""Exit 1 for a proven no-op, 0 to proceed, 2 for invalid local evidence."""
import argparse
from datetime import datetime
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.headless_tasks import config, fields, ledger_rows
from lib.scheduled_step0 import CHICAGO, monthly_gate, stop_reason, version_gate


def main() -> int:
    repo = REPO
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task_id')
    args = parser.parse_args()
    if args.task_id == 'cc-update-audit':
        unchanged = version_gate(repo, Path.home())
        print('STOP version_unchanged' if unchanged else 'PROCEED')
        return int(unchanged)
    try:
        task = config(repo)['tasks'][args.task_id]
        rows = ledger_rows(repo/'out/headless'/args.task_id/'ledger.jsonl')
        days = fields(task['cron'])[2] if task.get('monthly') else None
        reason = stop_reason(args.task_id, datetime.now(CHICAGO), rows, monthly_days=days)
        if reason:
            print(f'STOP {reason}')
            return 1
        if task.get('monthly'):
            return monthly_gate(repo, args.task_id)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        print('ERROR invalid_or_unavailable_step0_evidence', file=sys.stderr)
        return 2
    print('PROCEED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
