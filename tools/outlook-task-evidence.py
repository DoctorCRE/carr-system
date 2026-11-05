#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.outlook_task_candidates import intake_evidence


def main() -> int:
    parser = argparse.ArgumentParser(description='Validate held task evidence and emit counts. Admission is disabled.')
    parser.add_argument('database', type=Path)
    parser.add_argument('account_key')
    parser.add_argument('store_generation')
    args = parser.parse_args()
    try:
        counts = intake_evidence(args.database, args.account_key, args.store_generation)
    except (ValueError, OSError):
        print('task evidence rejected', file=sys.stderr)
        return 2
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
