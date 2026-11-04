#!/usr/bin/env python3
"""Observe one supplied Studio run receipt without starting or refreshing a job."""
import argparse
import json
from pathlib import Path
import re
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt-file', type=Path, required=True)
    parser.add_argument('--service', required=True)
    parser.add_argument('--runtime-sha', required=True)
    parser.add_argument('--count', required=True, help='Dot-separated path within receipt counts')
    parser.add_argument('--minimum', type=int, required=True)
    args = parser.parse_args()
    try:
        if not re.fullmatch(r'[0-9a-f]{40}', args.runtime_sha) or args.minimum < 0:
            raise ValueError('full runtime SHA and nonnegative minimum required')
        with args.receipt_file.open('rb') as stream:
            data = stream.read(1048577)
        if len(data) > 1048576:
            raise ValueError('receipt exceeds 1 MiB')
        prefix = args.service + '-receipt '
        text = data.decode().strip()
        if not text.startswith(prefix):
            raise ValueError('expected one completed service receipt')
        receipt = json.loads(text[len(prefix):])
        if receipt['state'] != 'succeeded' or receipt['runtime_sha'] != args.runtime_sha:
            raise ValueError('successful receipt for expected full runtime SHA required')
        count = receipt['counts']
        for key in args.count.split('.'):
            count = count[key]
        if type(count) is not int or count < args.minimum:
            raise ValueError('receipt count does not meet declared minimum')
        print('Studio proof OBSERVED: expected runtime and count predicate matched; no trusted receipt ingested')
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('Studio proof BLOCKED: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__))
        return 1


if __name__ == '__main__':
    sys.exit(main())
