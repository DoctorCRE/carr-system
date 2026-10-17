#!/usr/bin/env python3
"""Verify or distribute shared Cloudflare tokens without displaying their values.

Check: python3 tools/cloudflare-tokens.py check
Sync a verified machine's copy: python3 tools/cloudflare-tokens.py sync --from-machine macbook
Rotate: pipe JSON containing CLOUDFLARE_API_TOKEN and/or CLOUDFLARE_SPEND_GUARD_TOKEN
from the secure token source into: python3 tools/cloudflare-tokens.py sync --stdin
Never put values in command arguments. Both sync modes attempt studio, macbook and dell,
verify the candidate before installation, preserve other dotenv entries, then verify the
installed value on each machine. A nonzero exit means at least one copy is unconfirmed.
Rerun the same path for an offline machine when it returns. Keep the old token valid until
all required machines verify. Run check after sync; nightly health clears the loops.
"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import cloudflare_tokens as cf


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('operation', choices=('check', 'sync'))
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--from-machine', choices=cf.MACHINES)
    source.add_argument('--stdin', action='store_true')
    args = parser.parse_args(argv)
    if args.operation == 'check':
        rows = cf.health_rows()
        for row in rows:
            print(row['line'])
        return int(any(row['status'] != 'ok' for row in rows))
    if not args.stdin and not args.from_machine:
        parser.error('sync requires --stdin or --from-machine')
    try:
        if args.from_machine:
            code = cf.remote_source(export=True)
            result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                                     args.from_machine, 'python3 -c ' + shlex.quote(code)],
                                    capture_output=True, text=True, timeout=40)
            if result.returncode:
                raise ValueError('source verification failed')
            values = json.loads(result.stdout)
        else:
            values = json.load(sys.stdin)
        if not isinstance(values, dict) or not values or set(values) - set(cf.TOKEN_NAMES):
            raise ValueError('invalid input')
        if any(not isinstance(v, str) or not v or '\n' in v or '\r' in v for v in values.values()):
            raise ValueError('invalid value')
    except Exception:
        print('FAIL source unavailable, unverified or invalid; no targets updated')
        return 1
    failed = False
    for name, value in values.items():
        for machine, state in cf.sync(name, value).items():
            print(f'{machine} {name} {state}')
            failed |= state != 'ok'
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
