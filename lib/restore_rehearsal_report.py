"""Interpret restore-rehearse output without a model or access to backup data."""
import json
import re
import sys


def report(text):
    lines = [line.strip() for line in text.splitlines()]
    verdicts = [i for i, line in enumerate(lines) if line.startswith('RESTORE REHEARSAL:')]
    deleted = [i for i, line in enumerate(lines)
               if re.fullmatch(r'teardown: branch \S+ deleted', line)]
    failed = any(line.startswith(('REHEARSAL FAILED:', 'teardown: COULD NOT DELETE',
                                  'teardown: KEEPING')) for line in lines)
    passed = (len(verdicts) == len(deleted) == 1
              and lines[verdicts[0]] == 'RESTORE REHEARSAL: PASS'
              and deleted[0] > verdicts[0] and not failed)
    return {'verdict': 'PASS' if passed else 'FAIL'}


if __name__ == '__main__':
    result = report(sys.stdin.read())
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result['verdict'] == 'PASS' else 1)
