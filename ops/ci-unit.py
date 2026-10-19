#!/usr/bin/env python3
"""Execute every required Node package and refuse unmeasured success."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from node_test_summary import FIELDS, test_counts

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ('mcp-server', 'control-room', 'workspace', 'practice-plugin')
PREFIX = 'CARR_FLAKE_RESULT '


def package_counts(output):
    receipts = [json.loads(line[len(PREFIX):]) for line in output.splitlines()
                if line.startswith(PREFIX)]
    if not receipts:
        return test_counts(output)
    total = dict.fromkeys(FIELDS, 0)
    for receipt in receipts:
        counts = receipt.get('test_counts')
        if (receipt.get('first_exit') != 0 or receipt.get('status') != 'passed'
                or not isinstance(counts, dict) or set(counts) != set(FIELDS)
                or any(type(value) is not int or value < 0 for value in counts.values())
                or counts['tests'] != sum(counts[name] for name in FIELDS[1:])):
            return None
        for name in FIELDS:
            total[name] += counts[name]
    return total


def main():
    failed = False
    totals = dict.fromkeys(FIELDS, 0)
    with tempfile.TemporaryDirectory(prefix='carr-ci-unit-') as directory:
        for package in PACKAGES:
            manifest = ROOT / package / 'package.json'
            if not manifest.is_file():
                print(f'FAIL unit {package}: required package.json is missing')
                failed = True
                continue
            env = dict(os.environ)
            preload = (ROOT / 'ops/ci-node-file.mjs').as_uri()
            env['NODE_OPTIONS'] = f"{env.get('NODE_OPTIONS', '')} --import={preload}"
            if package == 'mcp-server':
                env['F03_PARITY_REQUIRE_PYTHON'] = '1'
            log = Path(directory) / f'{package}.log'
            try:
                with log.open('w') as output:
                    result = subprocess.run(
                        [sys.executable, str(ROOT / 'bin/with-timeout.py'), '900',
                         'npm', '--prefix', package, 'test'], cwd=ROOT,
                        env=env, stdout=output, stderr=subprocess.STDOUT)
                output = log.read_text(errors='replace')
                for line in output.splitlines():
                    if line.startswith((PREFIX, 'QUARANTINED ')):
                        print(line)
                counts = package_counts(output)
                if result.returncode:
                    reason = f'command exited {result.returncode}'
                elif counts is None:
                    reason = 'no complete test counts; required check did not report execution'
                elif counts['pass'] == 0:
                    reason = 'zero passing tests; required check was empty or skipped'
                elif counts['fail'] or counts['cancelled'] or counts['todo']:
                    reason = 'required tests failed, were cancelled, or remain todo'
                else:
                    for name in FIELDS:
                        totals[name] += counts[name]
                    print(f"OK unit {package}: {counts['tests']} tests, "
                          f"{counts['pass']} passed, {counts['skipped']} skipped")
                    continue
                print(f'FAIL unit {package}: {reason}')
                print('\n'.join(output.splitlines()[-25:]))
            except (OSError, ValueError, TypeError) as exc:
                print(f'FAIL unit {package}: required check could not complete ({type(exc).__name__})')
            failed = True
    print(f"unit totals: {totals['tests']} tests, {totals['pass']} passed, "
          f"{totals['skipped']} skipped in passing packages")
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
