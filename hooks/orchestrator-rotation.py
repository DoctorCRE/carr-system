#!/usr/bin/env python3
"""Rotate explicitly marked Claude orchestrators before compaction. Fail open."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from orchestrator_session import transcript_snapshot
TOKEN_FIELDS = ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')
HANDOFF = '''Rotate this orchestrator now before context compaction.
Use write-doctrine-section through the record layer to write team-memory section
handoff/orchestrator-latest with this session's attach_id and current base_version.
Start with a HANDOFF-HEADER v1 of at most 2,000 bytes containing status, retiring
T3 thread id, blockers, next action, and paths to detailed state and job reports.
Put the complete running-job inventory in the body below that header.
Successors read the header first. Capture each bootstrap command's stdout with
set -o pipefail; <command> | python3 hooks/orchestrator-rotation.py --bootstrap-capture out/bootstrap
Results over 40,000 bytes stay in a private file; only its path, size and SHA-256
enter context. Inspect needed sections from that file. Required source reads still apply.
Include program status, exact branches and PRs, decisions, next actions, and EVERY
running delegated job with its task id and the thread/link each will report to.
Include your retiring T3 thread id in both the handoff and successor prompt.
Do not use the Claude session id as the T3 thread id.
Mint a successor card through dispatch-job before launching it with t3_thread_launch.
The successor prompt must include the returned Card: line and this exact role line:
CARR-Session-Role: orchestrator
Tell the successor: Start with /pstack:poteto-mode in its Orchestrate playbook.
As part of your opening act, call t3_thread_organize with action "unpin" and
threadId set to the retiring T3 thread id named in this launch prompt and handoff,
only if it is pinned.
Resume from team-memory handoff/orchestrator-latest and pstack's Session pickup
playbook. Run as the coordinator that owns the program, never the code (rule
974655e1). pstack lives at /Users/booko/.claude/plugins/marketplaces/cursor-plugins/pstack.
Read pstack's program store at /Users/booko/.claude/orchestrate/doctorcre-delivery
and its preferences.md standing orders. Use /Users/booko/.claude/plugins/marketplaces/cursor-plugins/pstack/skills/poteto-mode/scripts/orch/orch.ts for state.
Read ~/.claude/rules/pstack-models.md and ~/.claude/rules/pstack-routing.md.
Preserve the running jobs' reporting destinations; do not duplicate their work.
Post the successor's returned link to Joe, then stop this session.'''


def snapshot(path, session):
    marked, latest = transcript_snapshot(path, session)
    if not marked:
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


def capture_bootstrap(directory):
    """Stream opaque bootstrap output to disk before deciding what enters context."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    size = 0
    checksum = hashlib.sha256()
    with tempfile.NamedTemporaryFile(dir=directory, prefix='bootstrap-', suffix='.txt',
                                     delete=False) as output:
        path = Path(output.name)
        try:
            while chunk := sys.stdin.buffer.read(65536):
                output.write(chunk)
                size += len(chunk)
                checksum.update(chunk)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    if size <= 40000:
        try:
            sys.stdout.buffer.write(path.read_bytes())
        finally:
            path.unlink()
    else:
        print(json.dumps({'schema': 'orchestrator-bootstrap/v1', 'path': str(path),
                          'bytes': size, 'sha256': checksum.hexdigest()}, separators=(',', ':')))
    return 0


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--bootstrap-capture':
        return capture_bootstrap(sys.argv[2])
    try:
        result = decide(json.load(sys.stdin))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return 0
    if result:
        print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
