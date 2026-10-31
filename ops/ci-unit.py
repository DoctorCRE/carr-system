#!/usr/bin/env python3
"""Execute every required Node package and refuse unmeasured success."""
import argparse
import json
from ci_unit_shards import plan
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from node_test_summary import FIELDS, test_counts

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ('mcp-server', 'practice-plugin')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--part", choices=("all", "fast", "mcp", "practice"), default="all")
    args = parser.parse_args()
    shard = os.environ.get('CARR_CI_UNIT_SHARD')
    selected = None
    if shard:
        if args.part != 'mcp' or shard not in ('1', '2', '3', '4'):
            parser.error('CARR_CI_UNIT_SHARD requires --part mcp and shard 1 through 4')
        selected = plan()[int(shard) - 1]
    packages = {'fast': ('practice-plugin',), 'mcp': ('mcp-server',),
                'practice': ('practice-plugin',), 'all': PACKAGES}[args.part]
    failed = False
    totals = dict.fromkeys(FIELDS, 0)
    with tempfile.TemporaryDirectory(prefix='carr-ci-unit-') as directory:
        for package in packages:
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
                if selected is not None:
                    env['CARR_CI_UNIT_SUITES'] = json.dumps(selected)
            log = Path(directory) / f'{package}.log'
            try:
                with log.open('w') as output:
                    result = subprocess.run(
                        [sys.executable, str(ROOT / 'bin/with-timeout.py'), '600' if shard else '1200',
                         *(['node', '--test', 'practice-plugin/test/calculators.test.mjs',
                            'practice-plugin/test/verticals.test.mjs'] if args.part == 'fast' else
                           ['npm', '--prefix', package, 'test'])], cwd=ROOT,
                        env=env, stdout=output, stderr=subprocess.STDOUT)
                output = log.read_text(errors='replace')
                for line in output.splitlines():
                    if line.startswith('suite-timing: '):
                        print(line)
                exceptions = (json.loads((ROOT / 'ops/config/hosted-suite-exceptions.json').read_text())
                              if package == 'mcp-server' else {})
                completed = []
                suite_error = None
                for line in output.splitlines():
                    if not line.startswith('suite-result: '):
                        continue
                    suite = json.loads(line.removeprefix('suite-result: '))
                    executed = test_counts(suite['summary'])
                    if (suite['code'] or executed is None or (executed['pass'] == 0 and suite['file'] not in exceptions)
                            or executed['fail'] or executed['cancelled'] or executed['todo']):
                        suite_error = f"suite {suite['file']}: zero passing tests or incomplete execution"
                        print(suite_error)
                    else:
                        if executed['pass'] == 0:
                            print(f"unit exception {suite['file']}: {exceptions[suite['file']]}")
                        completed.append(suite['file'])
                counts = test_counts(output)
                if result.returncode:
                    reason = f'command exited {result.returncode}'
                elif counts is None:
                    reason = 'no complete test counts; required check did not report execution'
                elif counts['pass'] == 0:
                    reason = 'zero passing tests; required check was empty or skipped'
                elif counts['fail'] or counts['cancelled'] or counts['todo']:
                    reason = 'required tests failed, were cancelled, or remain todo'
                elif suite_error:
                    reason = suite_error
                elif selected is not None and sorted(completed) != sorted(selected):
                    reason = 'shard coverage mismatch; not every assigned suite completed'
                else:
                    if selected is not None:
                        destination = Path(os.environ['CARR_CI_UNIT_RECEIPT'])
                        destination.write_text(json.dumps({'shard': int(shard), 'suites': completed}))
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
