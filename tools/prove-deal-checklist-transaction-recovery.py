#!/usr/bin/env python3
"""Observe bounded deal reads and release ancestry. Fixture mode never calls production.

Live success does not prove an absent-storage transition. Run the disposable
PostgreSQL regression at the same revision separately.
"""
import argparse
import json
import os
import signal
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
STATES = {'current', 'unavailable', 'source_changed', 'deal_type_unknown'}
SCENARIOS = ('current', 'unavailable', 'source_changed', 'auth', 'empty', 'invalid', 'aborted', 'wrong-id', 'wrong-release', 'dishonest', 'bad-checklist', 'timeout')
DEAL_ID = '11111111-1111-4111-8111-111111111111'


def stub(scenario, verb, arguments):
    if scenario == 'timeout':
        time.sleep(2)
    if scenario == 'auth':
        return {'error': 'not_authenticated'}
    if scenario == 'aborted':
        return {'error': '25P02'}
    if scenario == 'invalid':
        return {'schema_version': 'unknown'}
    if verb == 'deal-room-board':
        return {'schema_version': 'local-deals-board.v2', 'deals': [] if scenario == 'empty' else [{'id': str(uuid.UUID(int=i))} for i in range(1, 6)]}
    if verb != 'get-deal-room' or set(arguments) != {'deal'} or arguments['deal'] not in [str(uuid.UUID(int=i)) for i in range(1, 6)]:
        raise ValueError('fixture_contract')
    state = scenario if scenario in STATES else 'current'
    deal_id = arguments['deal']
    result = {'schema_version': 'deal-timeline.v1', 'id': deal_id if scenario != 'wrong-id' else DEAL_ID,
            'deal_id': deal_id, 'next_actions': [], 'critical_dates': [],
            'checklist': {'schema_version': 'deal-checklist.v1', 'template_state': state,
                          'phases': [], 'due_items': [], 'requests_state': 'unavailable'}}
    if scenario == 'dishonest':
        result['checklist'].update(template_state='unavailable', phases=[{}])
    if scenario == 'bad-checklist':
        result['checklist']['schema_version'] = 'unknown'
    return result


def require(condition, code):
    if not condition:
        raise ValueError(code)


def prove(args):
    deadline = time.monotonic() + args.timeout_seconds

    def remaining():
        value = deadline - time.monotonic()
        require(value > 0, 'timeout')
        return value

    def process(command):
        with subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, start_new_session=True) as child:
            try:
                stdout, _ = child.communicate(timeout=remaining())
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.communicate()
                raise
            require(child.returncode == 0, 'process_failed')
        require(len(stdout) <= 1048576, 'response_too_large')
        remaining()
        return stdout

    def call(verb, payload):
        command = ([sys.executable, str(Path(__file__).resolve()), '--fixture-verb', args.scenario, verb]
                   if args.fixture else [str(ROOT / 'run.sh'), 'call', verb])
        value = json.loads(process(command + [json.dumps(payload)]))
        require(isinstance(value, dict) and not value.get('error') and value.get('ok') is not False, 'verb_failed')
        return value

    expected = process(['git', 'rev-parse', '--verify', args.expected_revision + '^{commit}']).strip()
    if args.fixture:
        release = {'ok': True, 'env': {'value': 'production'}, 'git_sha': {'value': expected}}
        if args.scenario == 'wrong-release':
            release['git_sha']['value'] = 'invalid'
    else:
        with urllib.request.urlopen('https://api.doctorcre.com/release', timeout=remaining()) as response:
            release = json.loads(response.read(1048577))
        remaining()
    require(isinstance(release, dict) and release.get('ok') is True and
            release.get('env', {}).get('value') == 'production', 'release_invalid')
    revision = release.get('git_sha', {}).get('value', '')
    require(isinstance(revision, str) and re.fullmatch('[0-9a-f]{40}', revision), 'release_invalid')
    process(['git', 'merge-base', '--is-ancestor', expected, revision])
    board = call('deal-room-board', {'workspace': 'all'})
    require(board.get('schema_version') == 'local-deals-board.v2' and isinstance(board.get('deals'), list), 'board_invalid')
    ids = list(dict.fromkeys(row['id'] for row in board['deals'] if isinstance(row, dict) and isinstance(row.get('id'), str)))[:args.max_records]
    require(ids, 'no_usable_deals')
    states = []
    for deal_id in ids:
        require(str(uuid.UUID(deal_id)) == deal_id, 'deal_id_invalid')
        room = call('get-deal-room', {'deal': deal_id})
        require(room.get('schema_version') == 'deal-timeline.v1' and room.get('id') == deal_id and
                room.get('deal_id') == deal_id and isinstance(room.get('next_actions'), list) and
                isinstance(room.get('critical_dates'), list), 'deal_invalid')
        checklist = room.get('checklist')
        require(isinstance(checklist, dict) and checklist.get('schema_version') == 'deal-checklist.v1' and
                checklist.get('template_state') in STATES and isinstance(checklist.get('phases'), list) and
                isinstance(checklist.get('due_items'), list), 'checklist_invalid')
        if checklist['template_state'] in {'unavailable', 'source_changed'}:
            require(checklist['phases'] == [] and checklist['due_items'] == [], 'checklist_dishonest')
        else:
            require(checklist.get('requests_state') in {'current', 'unavailable'}, 'requests_invalid')
        states.append(checklist['template_state'])
    remaining()
    print(json.dumps({'deal_reads': len(states), 'record_reads': len(states) + 1, 'states': states}))


def main():
    if len(sys.argv) == 5 and sys.argv[1] == '--fixture-verb':
        print(json.dumps(stub(sys.argv[2], sys.argv[3], json.loads(sys.argv[4]))))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--live', action='store_true')
    mode.add_argument('--fixture', action='store_true')
    parser.add_argument('--timeout-seconds', type=float, default=30)
    parser.add_argument('--max-records', type=int, default=3)
    parser.add_argument('--expected-revision', default='HEAD', help='Merged implementation revision; must be an ancestor of deployed release')
    parser.add_argument('--scenario', choices=SCENARIOS, default='current', help='Synthetic fixture response')
    args = parser.parse_args()
    if not (0 < args.timeout_seconds <= 30 and 1 <= args.max_records <= 3):
        parser.error('budgets require 0 < timeout <= 30 and 1 <= max-records <= 3')
    if args.live and args.scenario != 'current':
        parser.error('scenarios require --fixture')
    try:
        prove(args)
        return 0
    except (ValueError, KeyError, TypeError, AttributeError, OSError, subprocess.SubprocessError):
        print('proof_failed', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
