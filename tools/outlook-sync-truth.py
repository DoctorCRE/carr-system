#!/usr/bin/env python3
"""Count decoded-field agreements against explicit gzip Osa GetMessage log copies."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.outlook_sync_truth import agreement_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--decoded', required=True, type=Path,
                        help='Decoded hxprobe SQLite research copy, opened read-only')
    parser.add_argument('--logs', nargs='*', default=[], type=Path,
                        help='Explicit gzip Osa GetMessage response copies')
    args = parser.parse_args()
    report = agreement_report(args.decoded, args.logs)
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'complete' else 1


if __name__ == '__main__':
    sys.exit(main())
