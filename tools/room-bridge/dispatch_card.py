"""Bind dispatches to the progress board before any executor can launch."""
from pathlib import Path
from datetime import datetime, timezone
import fcntl
import json
import os
import pwd
import re
import socket
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib import progress_hierarchy as hierarchy
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import progress_board as board

CAPTURE = ('File one in one call: ./run.sh call report-problem with situation, title, '
           'desired_outcome, acceptance_criteria and a UUID idempotency_key. '
           'Then send with --wr WR-<number> --slice S1.')


def prepare(ref, slice_ref, title, description, desk, call, pr=None):
    if not ref or not re.fullmatch(r'WR-\d{1,12}', ref):
        raise ValueError('--wr requires a captured Work Request. ' + CAPTURE)
    if not re.fullmatch(r'S[1-9]\d*', slice_ref or ''):
        raise ValueError('--slice requires S<number>; use S1 when the WR has no slice plan')
    hierarchy.validate_copy(title, description)
    ref = f'WR-{int(ref[3:]):06d}'
    try:
        request = hierarchy.load_request(ref, call)
    except (RuntimeError, KeyError, TypeError, ValueError) as error:
        raise ValueError(f'Could not verify captured Work Request {ref}: {error}. ' + CAPTURE) from error
    slices = request.get('slices') or [{'slice_id': 'implicit', 'ordinal': 1}]
    selected = next((s for i, s in enumerate(slices, 1)
                     if s.get('ordinal', i) == int(slice_ref[1:])), None)
    if selected is None:
        raise ValueError(f'{slice_ref} is not registered for {ref}; a WR without a slice plan uses S1')
    with board.board_lock(board.ALL_REPOS_BOARD):
        path = board.state_path(board.ALL_REPOS_BOARD)
        state = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(state, dict):
            raise ValueError('The progress board must be a JSON object; repair it before dispatch')
        state.setdefault('project', board.ALL_REPOS_BOARD)
        state.setdefault('title', 'All repositories')
        registry = state.setdefault('work_requests', {})
        existing = registry.get(ref, {})
        registry[ref] = {**request, **{k: existing[k] for k in
                            ('card_title', 'card_description', 'proposed_copy') if k in existing}}
        at = board.stamp()
        tasks = state.setdefault('tasks', {})
        if pr:
            match = re.fullmatch(r'(carr|app|factory)#([1-9]\d*)', pr)
            if not match:
                raise ValueError('--pr requires carr#<number>, app#<number>, or factory#<number>')
            repo = next(repo for repo, alias in hierarchy.REPOSITORIES.items() if alias == match[1])
            task = next((t for t in tasks.values() if t.get('repo') == repo
                         and t.get('pr') == int(match[2])), None)
            if task is None or task.get('work_request') != ref or task.get('slice_id') != selected['slice_id']:
                raise ValueError(f'No existing PR card for {pr} in {ref}.{slice_ref}; sync the board before review or fix')
            task.update(updated_at=at, executor=desk)
        else:
            key = 'dispatch:' + str(uuid.uuid4())
            task = {'work_request': ref, 'slice_id': selected['slice_id'], 'job_id': key,
                    'card_title': title, 'card_description': description, 'title': title,
                    'status': 'running', 'stage': 'build', 'executor': desk,
                    'created_at': at, 'updated_at': at}
            tasks[key] = task
        board.write_json(state)
        return {k: task[k] for k in ('work_request', 'card_id', 'card_title', 'card_description')}


def instruction(card):
    return ('Keep this job on its existing card through reviews and fixes. '
            'Include these lines in the PR body. When the PR opens, extend Card ID '
            'with .carr#<number>, .app#<number>, or .factory#<number>, keeping J.\n'
            f"Work Request: {card['work_request']}\nCard ID: {card['card_id']}\n"
            f"Card title: {card['card_title']}\nCard description: {card['card_description']}")


def emergency(results, desk, reason, record):
    if not reason.strip() or '\n' in reason:
        raise ValueError('--emergency requires a non-empty single-line reason')
    receipt = {'event': 'emergency_dispatch', 'audit_id': str(uuid.uuid4()),
               'dispatched_at': board.stamp(), 'desk': desk, 'reason': reason,
               'actor': {'uid': os.getuid(), 'user': pwd.getpwuid(os.getuid()).pw_name,
                         'host': socket.gethostname(), 'pid': os.getpid()}}
    record(results, receipt)
    return ('Include these emergency audit lines in the PR body.\n'
            'Emergency dispatch: ' + receipt['audit_id'] + '\n'
            'Emergency reason: ' + reason + '\n'
            'Emergency actor: ' + receipt['actor']['user'] + '@' + receipt['actor']['host'])


def emergency_report(results):
    now = datetime.now(timezone.utc)
    uses = []
    if results.exists():
        with results.open() as source:
            fcntl.flock(source, fcntl.LOCK_SH)
            for line in source:
                row = json.loads(line)
                if row.get('event') == 'emergency_dispatch':
                    uses.append(row)
    return {'schema': 'dispatch-emergencies.v1', 'through': now.isoformat(),
            'source': str(results), 'log_exists': results.exists(), 'uses': uses}
