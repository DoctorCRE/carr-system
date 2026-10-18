#!/usr/bin/env python3
"""Feature retirement health through the audited record door, without DB access."""
import argparse
import json
from pathlib import Path
import subprocess
import uuid

BOUND='on breach: create/update deduplicated owner loop; retire and remove gates after review or record a reviewed future date; verify list-feature-switches and consumer tests; auto-clear when retired or no longer overdue'

def render(payload):
    if not isinstance(payload,dict) or payload.get('ok') is not True or not isinstance(payload.get('overdue'),list):
        raise ValueError('feature switch check unavailable')
    if any(not isinstance(row,dict) for row in payload['overdue']):
        raise ValueError('feature switch overdue row unavailable')
    lines=[payload.get('line')]+[row.get('line') for row in payload['overdue']]
    if any(not isinstance(line,str) or not all(word in line for word in ('on breach:','owner','retire','verify','auto-clear')) for line in lines):
        raise ValueError('feature switch response has no bound action')
    return (1 if payload['overdue'] else 0),lines

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--fixture');args=parser.parse_args()
    try:
        if args.fixture:
            print('FIXTURE-DERIVED feature switch health; no record writes or live evidence.')
            fixture=json.loads(Path(args.fixture).read_text())
            if not isinstance(fixture,dict):
                raise ValueError('feature switch fixture unavailable')
            payload=fixture.get('feature_switches')
        else:
            root=Path(__file__).resolve().parents[1]
            result=subprocess.run([str(root/'run.sh'),'call','check-feature-switches',json.dumps({'idempotency_key':str(uuid.uuid4())})],
                cwd=root,capture_output=True,text=True,timeout=30,check=True)
            payload=json.loads(result.stdout)
        code,lines=render(payload)
        for line in lines:print(line)
        return code
    except (ValueError,TypeError,KeyError,OSError,subprocess.SubprocessError):
        print(f'UNKNOWN feature switches: check unavailable · {BOUND}; retry the record check before reporting a count.')
        return 2
if __name__=='__main__':raise SystemExit(main())
