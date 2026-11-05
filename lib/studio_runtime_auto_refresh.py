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
from lib.release_reader import read
from lib.worker_release_receipt import discover

from ops.git_env import scrubbed_env
from lib.local_jobs import append, locked, read_latest, stamp
from lib.outlook_settings import load_settings

RELEASE_URL = 'https://api.doctorcre.com/release'
LABEL = 'com.carr.studio-runtime-auto-refresh'
SHA = re.compile(r'[0-9a-f]{40}')


def command(*args, timeout=60):
    return subprocess.run(list(map(str, args)), check=True, capture_output=True,
                          text=True, timeout=timeout, env=scrubbed_env()).stdout.strip()


def released_receipt():
    response = read(RELEASE_URL, timeout=20)
    if response.status != 200:
        raise ValueError(f"production release HTTP {response.status}")
    return discover(json.loads(response.body))


def released_sha():
    return released_receipt()['source_sha']


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
        installation_started_at = None
        def receipt(status, sha=None):
            row = {'at': stamp(now), 'status': status, 'sha': sha}
            worker = getattr(host, 'worker_receipt', None)
            if isinstance(worker, dict):
                row['worker_receipt'] = worker
            if status in {'current', 'refreshed'}:
                row['installed_sha'] = sha
            if status == 'refreshed':
                row['installation_started_at'] = installation_started_at
                row['installation_completed_at'] = stamp(time.time() if live_clock else now)
            append(root / 'runs.jsonl', row)
            return status
        try:
            host.recover()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            return receipt('refused')
        if now - state.get('checked_at', 0) < 300:
            return receipt('wait')
        if state.get('discovery_failures', 0) >= 2:
            host.alert(None, now)
            return receipt('open')
        state['checked_at'] = now
        state['discovery_failures'] = state.get('discovery_failures', 0) + 1
        save(path, state)
        try:
            sha = host.release()
            if not isinstance(sha, str) or not SHA.fullmatch(sha):
                raise ValueError('invalid release SHA')
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            if state['discovery_failures'] >= 2:
                host.alert(None, now)
                return receipt('open')
            return receipt('refused')
        state['discovery_failures'] = 0
        save(path, state)
        try:
            if host.installed() == sha:
                host.verify_current(sha)
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
        installation_started_at = stamp(time.time() if live_clock else now)
        try:
            host.refresh(sha)
            if host.installed() != sha:
                raise ValueError('runtime SHA readback mismatch')
            host.verify_current(sha)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            if state['failures'][sha] >= 2:
                host.alert(sha, now)
                return receipt('open', sha)
            return receipt('failed', sha)
        state['failures'].pop(sha, None)
        save(path, state)
        return receipt('refreshed', sha)


class Host:
    def release(self):
        self.worker_receipt = released_receipt()
        return self.worker_receipt['source_sha']

    def __init__(self, source):
        self.worker_receipt = None
        self.source = source.resolve()
        self.settings = load_settings()
        self.runtime = self.settings.repo

    def recover(self):
        from lib.outlook_runtime_refresh import Host as RefreshHost
        host = RefreshHost(self.source, '', self.settings)
        lock = self.runtime.parent / ('.' + self.runtime.name + '-refresh.lock')
        with locked(lock, blocking=False):
            try:
                if host.recovery_pending():
                    host.recover()
            finally:
                host.locks.close()

    def verify_current(self, sha):
        from lib.outlook_runtime_refresh import Host as RefreshHost, LABELS
        from lib.scheduled_jobs import disabled, listed
        host = RefreshHost(self.source, '', self.settings)
        lock = self.runtime.parent / ('.' + self.runtime.name + '-refresh.lock')
        with locked(lock, blocking=False):
            if host.recovery_pending():
                raise ValueError('runtime recovery pending')
            if self.installed() != sha:
                raise ValueError('runtime SHA changed during readback')
            if not self.settings.decoder.is_file():
                raise ValueError('runtime decoder missing')
            self.settings.verify_helper()
            live = listed(host.command('launchctl', 'list').stdout)
            overrides = disabled(host.command('launchctl', 'print-disabled', host.domain).stdout)
            if any(label not in live or overrides.get(label, False) for label in LABELS):
                raise ValueError('Outlook jobs are not all registered and enabled')

    def installed(self):
        return command('git', '-C', self.runtime, 'rev-parse', 'HEAD')

    def refresh(self, sha):
        command('git', '-C', self.source, 'fetch', 'origin', 'main', timeout=120)
        main = command('git', '-C', self.source, 'rev-parse', 'origin/main')
        command('git', '-C', self.source, 'merge-base', '--is-ancestor', sha, main)
        with tempfile.TemporaryDirectory(prefix='carr-released-outlook-') as temporary:
            source = Path(temporary) / 'source'
            command('git', 'clone', '--shared', '--no-checkout', self.source, source)
            command('git', '-C', source, 'remote', 'set-url', 'origin',
                    'https://github.com/DoctorCRE/carr-system.git')
            command('git', '-C', source, 'update-ref', 'refs/remotes/origin/main', main)
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
        if previous and previous.get('release_sha') == sha and not previous.get('cleared_at'):
            return
        target = f'for {sha}' if sha else 'discovery'
        finding = {'key': key, 'kind': 'job_failed', 'subject': LABEL, 'exit_code': 1,
                   'release_sha': sha,
                   'reason': f'Runtime refresh {target} breaker open; two failures; retries stopped.',
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
