#!/usr/bin/env python3
"""Rotate explicitly marked Claude orchestrators before compaction. Fail open."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys

MARKER = 'CARR-Session-Role: orchestrator'
TOKEN_FIELDS = ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')
HANDOFF = '''Rotate this orchestrator now before context compaction.
Use write-doctrine-section through the record layer to write team-memory section
handoff/orchestrator-latest with this session's attach_id and current base_version.
Include program status, exact branches and PRs, decisions, next actions, and EVERY
running delegated job with its task id and the thread/link each will report to.
Mint a successor card through dispatch-job before launching it with t3_thread_launch.
The successor prompt must include the returned Card: line and this exact role line:
CARR-Session-Role: orchestrator
Tell the successor: Start with /pstack:poteto-mode in its Orchestrate playbook.
Resume from team-memory handoff/orchestrator-latest and pstack's Session pickup
playbook. Run as the coordinator that owns the program, never the code (rule
974655e1). pstack lives at /Users/booko/.claude/plugins/marketplaces/cursor-plugins/pstack.
Preserve the running jobs' reporting destinations; do not duplicate their work.
Post the successor's returned link to Joe, then stop this session.'''


def snapshot(path, session):
    first_prompt = None
    latest = None
    with Path(path).open() as transcript:
        for line in transcript:
            record = json.loads(line)
            if record.get('sessionId', session) != session or record.get('isSidechain'):
                continue
            if record.get('type') == 'user' and first_prompt is None and not record.get('isMeta'):
                content = record['message']['content']
                if isinstance(content, list):
                    content = '\n'.join(b.get('text', '') for b in content if b.get('type') == 'text')
                first_prompt = content
            if record.get('type') == 'assistant':
                latest = record['message'].get('usage')
    if not isinstance(first_prompt, str) or MARKER not in first_prompt.splitlines():
        return None
    if latest is None:
        return 0
    values = [latest[field] for field in TOKEN_FIELDS]
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError('invalid usage counters')
    return sum(values)


def decide(event):
    kind = event.get('hook_event_name')
    if kind not in {'Stop', 'PreCompact'} or event.get('agent_id'):
        return
    if kind == 'PreCompact' and event.get('trigger') != 'auto':
        return
    session = event['session_id']
    if not isinstance(session, str) or not session:
        return
    tokens = snapshot(event['transcript_path'], session)
    if tokens is None:
        return
    threshold = int(os.environ.get('CARR_ORCHESTRATOR_ROTATION_TOKENS', '500000'))
    if threshold <= 0:
        return
    directory = Path.home() / '.cache/carr/orchestrator-rotation'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(session.encode()).hexdigest() + '.json')
    with path.open('a+') as state_file:
        fcntl.flock(state_file, fcntl.LOCK_EX)
        state_file.seek(0)
        state = json.loads(state_file.read() or '{}')
        if kind == 'PreCompact':
            if state.get('compact_blocked'):
                return
            state['compact_blocked'] = True
        else:
            if state.get('instructed') or (tokens < threshold and not state.get('compact_blocked')):
                return
            state['instructed'] = True
        state_file.seek(0)
        state_file.truncate()
        json.dump(state, state_file)
        state_file.flush()
        return {'decision': 'block', 'reason': HANDOFF}


def main():
    try:
        result = decide(json.load(sys.stdin))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return 0
    if result:
        print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
