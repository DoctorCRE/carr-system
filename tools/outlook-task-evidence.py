#!/usr/bin/env python3
"""Validate an evidence database and report counts without admitting tasks."""
import argparse
import json
from pathlib import Path
import sys
import sqlite3
import subprocess
from contextlib import closing

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.outlook_task_candidates import intake_evidence


def main() -> int:
    parser = argparse.ArgumentParser(description='Validate held task evidence and emit counts. Admission is disabled.')
    parser.add_argument('database', type=Path)
    parser.add_argument('account_key')
    parser.add_argument('store_generation')
    parser.add_argument('--studio-receipt', action='store_true',
                        help='Emit a service receipt bound to this clean installed checkout.')
    args = parser.parse_args()
    try:
        counts = intake_evidence(args.database, args.account_key, args.store_generation)
        if args.studio_receipt:
            root = Path(__file__).resolve().parents[1]
            revision = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                                               text=True, stderr=subprocess.DEVNULL, timeout=10).strip()
            subprocess.run(['git', '-C', str(root), 'diff', '--quiet', 'HEAD', '--',
                            'tools/outlook-task-evidence.py', 'lib/outlook_task_candidates.py',
                            'vendor/hxprobe', 'spec/outlook', 'ops/hxstore-atlas.py'], check=True, timeout=10, stderr=subprocess.DEVNULL)
            with closing(sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                for row in db.execute('SELECT candidate_json FROM task_candidates'):
                    if json.loads(row[0])['source']['decoder_revision'] != revision:
                        raise ValueError('decoder_revision')
            print('outlook-task-evidence-receipt ' + json.dumps({
                'state': 'succeeded', 'runtime_sha': revision, 'counts': counts}, sort_keys=True))
            return 0
    except (ValueError, OSError, sqlite3.Error, subprocess.SubprocessError):
        print('task evidence rejected', file=sys.stderr)
        return 2
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
