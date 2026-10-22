"""Read-only evidence for a headless Codex deadline. Never resumes a turn."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
import re

from desks import DeskError
from ops.git_env import scrubbed_env


class Artifact:
    """Optional task stanza: Completion artifact: {"kind":"report_line",...}."""

    def __init__(self, task, cwd, env=None):
        self.cwd, self.env = cwd, scrubbed_env(env)
        match = re.search(r'^Completion artifact: (.+)$', task, re.M)
        self.spec = None
        self.offset = 0
        if not match:
            return
        try:
            self.spec = json.loads(match[1])
            if self.spec['kind'] == 'pushed_head':
                return
            if (self.spec['kind'] != 'report_line'
                    or not isinstance(self.spec['contains'], str) or not self.spec['contains']):
                raise ValueError('expected report_line with contains')
            self.path = Path(self.spec['path'])
            if not self.path.is_absolute():
                self.path = Path(cwd) / self.path
            self.offset = self.path.stat().st_size if self.path.exists() else 0
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise DeskError('bad_completion_artifact', str(exc)) from exc

    def readback(self):
        if self.spec is None:
            return {'status': 'undeclared'}
        if self.spec['kind'] == 'pushed_head':
            try:
                def git(*args):
                    return subprocess.run(['git', '-C', str(self.cwd), *args], env=self.env,
                        capture_output=True, text=True, check=True, timeout=5,
                        stdin=subprocess.DEVNULL).stdout.strip()
                head = git('rev-parse', 'HEAD')
                branch = git('symbolic-ref', 'HEAD')
                remote = git('ls-remote', '--exit-code', 'origin', branch).split()
                verified = remote == [head, branch]
                return {'kind': 'pushed_head', 'status': 'verified' if verified else 'unverified',
                        'head': head, 'branch': branch}
            except (OSError, subprocess.SubprocessError):
                return {'kind': 'pushed_head', 'status': 'unverified'}
        try:
            with self.path.open('rb') as report:
                report.seek(self.offset)
                tail = report.read(1024 * 1024).decode('utf-8')
            verified = any(self.spec['contains'] in line
                           for line in tail.splitlines(keepends=True) if line.endswith('\n'))
            return {'kind': 'report_line', 'status': 'verified' if verified else 'unverified'}
        except (OSError, UnicodeError):
            return {'kind': 'report_line', 'status': 'unverified'}


def native_thread(thread_id, env, started_at):
    """Read only this invocation's timestamped rollout, never an older answer."""
    state = {'state': 'unavailable', 'result': '', 'last_progress_at': None}
    if not isinstance(thread_id, str) or not re.fullmatch(r'[0-9a-fA-F-]{36}', thread_id):
        return state
    home = Path((env if env is not None else os.environ).get('CODEX_HOME') or Path.home() / '.codex')
    try:
        path = next((home / 'sessions').glob(f'*/*/*/rollout-*-{thread_id}.jsonl'), None)
        if path is None:
            return state
        with path.open('rb') as rollout:
            rollout.seek(0, 2)
            offset = max(0, rollout.tell() - 2 * 1024 * 1024)
            rollout.seek(offset)
            if offset:
                rollout.readline()
            tail = rollout.read(2 * 1024 * 1024).decode('utf-8', errors='replace')
        for line in tail.splitlines():
            try:
                row = json.loads(line)
                at = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00')).timestamp()
                payload = row['payload']
                kind = payload['type']
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            if at < started_at:
                continue
            if kind == 'task_started':
                state.update(state='in_progress', result='')
            elif kind == 'task_complete':
                answer = payload.get('last_agent_message')
                state.update(state='completed',
                             result=answer.strip() if isinstance(answer, str) else state['result'])
            elif kind == 'turn_aborted':
                state.update(state='interrupted', result='')
            elif row.get('type') in ('response_item', 'event_msg'):
                state['last_progress_at'] = at
                if (kind == 'message' and payload.get('role') == 'assistant'
                        and payload.get('phase') == 'final_answer'):
                    content = payload.get('content')
                    if isinstance(content, list):
                        state['result'] = ''.join(part['text'] for part in content
                            if isinstance(part, dict) and isinstance(part.get('text'), str)).strip()
    except OSError:
        pass
    return state
