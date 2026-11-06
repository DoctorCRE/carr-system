#!/usr/bin/env python3
"""Observe current-attempt producer containment while auto-filing stays held."""
import argparse
import json
from pathlib import Path
import re
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt-file', type=Path, required=True)
    parser.add_argument('--runtime-sha', required=True)
    args = parser.parse_args()
    try:
        if not re.fullmatch(r'[0-9a-f]{40}', args.runtime_sha):
            raise ValueError('full runtime SHA required')
        with args.receipt_file.open('rb') as stream:
            data = stream.read(1048577)
        if len(data) > 1048576:
            raise ValueError('receipt exceeds 1 MiB')
        text = data.decode().strip()
        prefix = 'outlook-triage-receipt '
        if not text.startswith(prefix):
            raise ValueError('expected one outlook-triage receipt')
        receipt = json.loads(text[len(prefix):])
        if (receipt['runtime_sha'] != args.runtime_sha or receipt['state'] != 'skipped'
                or receipt['reason'] not in ('capability_not_verified', 'executor_unavailable')):
            raise ValueError('expected runtime and held triage outcome required')
        run = receipt['counts']['run']
        if (run['snapshot_validated'] is not True or run['held'] is not True
                or type(run['observed']) is not int or run['observed'] <= 0
                or type(run['moved']) is not int or run['moved'] != 0
                or type(run['exit_code']) is not int or run['exit_code'] != 78):
            raise ValueError('validated nonempty snapshot, zero moves and held exit 78 required')
        print('Studio containment OBSERVED: validated snapshot, nonempty observations, zero moves, held exit 78; no trusted receipt ingested')
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('Studio containment BLOCKED: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__))
        return 1


if __name__ == '__main__':
    sys.exit(main())
