"""Bounded reconciliation of the Studio runtime to a verified production release."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from git_env import scrubbed_env
from lib.local_jobs import append, locked, read_latest, stamp
from lib.outlook_settings import load_settings

RELEASE_URL = 'https://api.doctorcre.com/release'
LABEL = 'com.carr.studio-runtime-auto-refresh'
SHA = re.compile(r'[0-9a-f]{40}')


def command(*args, timeout=60):
    return subprocess.run(list(map(str, args)), check=True, capture_output=True,
                          text=True, timeout=timeout, env=scrubbed_env()).stdout.strip()


def released_sha():
    with urlopen(RELEASE_URL, timeout=20) as response:
        live = json.loads(response.read(65537))
    sha = live['git_sha']['value']
    if not isinstance(sha, str) or not SHA.fullmatch(sha) or live.get('env', {}).get('value') != 'production':
        raise ValueError('production release identity unavailable')
    runs = json.loads(command('gh', 'run', 'list', '-R', 'DoctorCRE/carr-system',
                              '--workflow', 'deploy.yml', '--status', 'success', '--limit', '20',
                              '--json', 'displayTitle,event,headBranch,conclusion'))
    if not any(r.get('displayTitle') == f'Production release {sha}'
               and r.get('headBranch') == 'main' and r.get('conclusion') == 'success'
               and r.get('event') in {'workflow_run', 'workflow_dispatch'} for r in runs):
        raise ValueError('no successful production deploy receipt for release SHA')
    return sha


def save(path, state):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as handle:
        json.dump(state, handle)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def tick(root, host, *, now=None):
    live_clock = now is None
    now = time.time() if live_clock else now
    root.mkdir(parents=True, exist_ok=True)
    with locked(root / 'lock', blocking=False):
        path = root / 'state.json'
        state = json.loads(path.read_text()) if path.exists() else {'failures': {}}
        def receipt(status, sha=None):
            append(root / 'runs.jsonl', {'at': stamp(now), 'status': status, 'sha': sha})
            return status
        if now - state.get('checked_at', 0) < 300:
            return receipt('wait')
        state['checked_at'] = now
        save(path, state)
        try:
            sha = host.release()
            if not isinstance(sha, str) or not SHA.fullmatch(sha):
                raise ValueError('invalid release SHA')
            if host.installed() == sha:
                state['failures'].pop(sha, None)
                save(path, state)
                return receipt('current', sha)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            return receipt('refused')
        if state['failures'].get(sha, 0) >= 2:
            host.alert(sha, now)
            return receipt('open', sha)
        if live_clock:
            now = time.time()
        if now - state.get('attempted_at', 0) < 600:
            return receipt('cooldown', sha)
        state['attempted_at'] = now
        state['failures'][sha] = state['failures'].get(sha, 0) + 1
        save(path, state)
        receipt('attempt', sha)
        try:
            host.refresh(sha)
            if host.installed() != sha:
                raise ValueError('runtime SHA readback mismatch')
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            if state['failures'][sha] >= 2:
                host.alert(sha, now)
                return receipt('open', sha)
            return receipt('failed', sha)
        state['failures'].pop(sha, None)
        save(path, state)
        return receipt('refreshed', sha)


class Host:
    release = staticmethod(released_sha)

    def __init__(self, source):
        self.source = source.resolve()
        self.runtime = load_settings().repo

    def installed(self):
        return command('git', '-C', self.runtime, 'rev-parse', 'HEAD')

    def refresh(self, sha):
        command('git', '-C', self.source, 'fetch', 'origin', 'main', timeout=120)
        command('git', '-C', self.source, 'merge-base', '--is-ancestor', sha, 'origin/main')
        with tempfile.TemporaryDirectory(prefix='carr-released-outlook-') as temporary:
            source = Path(temporary) / 'source'
            command('git', 'clone', '--shared', '--no-checkout', self.source, source)
            command('git', '-C', source, 'remote', 'set-url', 'origin',
                    'https://github.com/DoctorCRE/carr-system.git')
            command('git', '-C', source, 'checkout', '--detach', sha)
            for item in ('.venv', 'out', 'mcp-server/node_modules'):
                target = self.source / item
                link = source / item
                if target.exists() and not link.exists():
                    link.parent.mkdir(parents=True, exist_ok=True)
                    link.symlink_to(target.resolve())
            command(sys.executable, self.source / 'bin/refresh-outlook-runtime',
                    '--sha', sha, '--source', source, '--released', '--apply', timeout=None)

    def alert(self, sha, now):
        key = f'job_failed:{LABEL}:exit_1'
        previous = read_latest(self.source / 'out/watchdog/findings.jsonl').get(key, {})
        if previous.get('release_sha') == sha and not previous.get('cleared_at'):
            return
        finding = {'key': key, 'kind': 'job_failed', 'subject': LABEL, 'exit_code': 1,
                   'release_sha': sha,
                   'reason': f'Runtime refresh breaker open for {sha}; two failures; retries stopped.',
                   'first_seen': stamp(now), 'cleared_at': None}
        append(self.source / 'out/watchdog/findings.jsonl', finding)
        inbox = self.source / 'out/watchdog/orchestrator-inbox.jsonl'
        with locked(inbox.with_suffix('.lock')):
            append(inbox, {'key': key, 'owner': 'orchestrator', 'delivered_at': stamp(now), 'finding': finding})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    try:
        status = tick(source / 'out/studio-runtime-auto-refresh', Host(source))
        print('studio-runtime-auto-refresh ' + status)
        return 1 if status in {'failed', 'open', 'refused'} else 0
    except BlockingIOError:
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'studio-runtime-auto-refresh refused: {type(error).__name__}')
        return 1
