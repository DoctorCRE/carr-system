"""Refresh only the installer-owned Outlook lane after draining its writers."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from lib.outlook_settings import load_settings
from lib.scheduled_jobs import disabled, listed
from git_env import scrubbed_env

LABELS = tuple('com.carr.outlook-' + service for service in ('intake', 'triage', 'outbox'))


def refresh(host, *, apply=False, timeout=300):
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('drain timeout must be finite and positive')
    if host.recovery_pending():
        if not apply:
            return 'recovery pending; rerun with --apply to restore previous state'
        host.recover()
    host.validate()
    if host.current():
        return 'already current'
    host.plan()
    if not apply:
        return 'dry run'
    host.snapshot()
    success = False
    try:
        host.quiesce()
        deadline = host.clock() + timeout
        while not host.drained():
            if host.clock() >= deadline:
                raise TimeoutError('Outlook drain timed out; runtime unchanged')
            host.sleep(1)
        host.swap()
        host.verify()
        success = True
    finally:
        host.restore(rollback=not success)
    return 'refreshed'


class Host:
    clock = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)

    def __init__(self, source, sha, settings):
        self.source, self.sha, self.settings = source.resolve(), sha, settings
        self.runtime = settings.repo
        self.domain = f'gui/{os.getuid()}'
        self.journal = self.runtime.parent / ('.' + self.runtime.name + '-refresh')
        self.locks = ExitStack()
        self.locked = False

    def command(self, *args, check=True, cwd=None):
        return subprocess.run(list(map(str, args)), cwd=cwd, check=check, text=True,
                              capture_output=True, timeout=1800, env=scrubbed_env())

    def git(self, repo, *args):
        return self.command('git', '-C', repo, *args).stdout.strip()

    def validate(self):
        if self.source == self.runtime.resolve():
            raise ValueError('source must be main, separate from the pinned runtime')
        if not re.fullmatch(r'[0-9a-f]{40}', self.sha):
            raise ValueError('--sha must be the full main commit SHA')
        url = self.git(self.source, 'remote', 'get-url', 'origin')
        if not re.fullmatch(r'(?:https://github\.com/|git@github\.com:)DoctorCRE/carr-system(?:\.git)?', url):
            raise ValueError('source origin must be DoctorCRE/carr-system')
        remote = self.git(self.source, 'ls-remote', 'origin', 'refs/heads/main').split()
        if not remote:
            raise ValueError('remote main SHA is unavailable')
        remote = remote[0]
        if (self.git(self.source, 'symbolic-ref', '--short', 'HEAD') != 'main'
                or self.git(self.source, 'rev-parse', 'HEAD') != self.sha or remote != self.sha
                or self.git(self.source, 'status', '--porcelain', '--untracked-files=no')):
            raise ValueError('source must be clean main at the requested current remote main SHA')
        if self.git(self.runtime, 'status', '--porcelain', '--untracked-files=no'):
            raise ValueError('runtime must be clean')
        self.previous = self.git(self.runtime, 'rev-parse', 'HEAD')
        self.git(self.source, 'merge-base', '--is-ancestor', self.previous, self.sha)
        retired = 'com.carr.outlook-refresh'
        if self.plist_path(retired).exists() or retired in listed(self.command('launchctl', 'list').stdout):
            raise ValueError('retired Outlook refresh job must be reconciled separately before this three-label refresh')
        state = self.runtime / 'out/outlook-intake/state.json'
        if self.settings.decoder.exists() and state.exists() and 'mail_ids' not in json.loads(state.read_text()):
            raise ValueError('legacy intake reconciliation is required before refresh; no installer was run')
        if not {'intake', 'triage', 'outbox'} <= set(self.settings.services):
            raise ValueError('refresh requires all three Outlook services configured')

    def current(self):
        return self.previous == self.sha

    def plan(self):
        print(f'Outlook runtime {self.runtime}: {self.previous} -> {self.sha}')
        print('Disable intake, triage and outbox; drain jobs and locks; remove idle registrations; '
              'run intake and triage installers; verify SHA and ancestry; restore prior registrations. '
              'No send is terminated. Default is dry run; use --apply to act.')

    def recovery_pending(self):
        return (self.journal / 'state.json').exists()

    def plist_path(self, label):
        return Path.home() / 'Library/LaunchAgents' / (label + '.plist')

    def snapshot(self):
        live = listed(self.command('launchctl', 'list').stdout)
        overrides = disabled(self.command('launchctl', 'print-disabled', self.domain).stdout)
        states = {label: {'registered': label in live, 'disabled': overrides.get(label, False)} for label in LABELS}
        paths = [self.settings.vendor, self.settings.copy_helper, self.settings.copy_helper.with_suffix('.pin')]
        paths += [self.plist_path(label) for label in LABELS]
        if self.journal.exists() and not self.recovery_pending():
            shutil.rmtree(self.journal)
        self.journal.mkdir(mode=0o700, exist_ok=False)
        artifacts = []
        for index, path in enumerate(paths):
            backup = self.journal / str(index)
            exists = path.exists()
            if exists:
                if path.is_dir():
                    shutil.copytree(path, backup, symlinks=True)
                else:
                    shutil.copy2(path, backup)
            artifacts.append({'path': str(path), 'backup': str(backup), 'exists': exists})
        state = {'previous': self.previous, 'states': states, 'artifacts': artifacts, 'swapped': False}
        self.write_state(state)

    def write_state(self, state):
        path = self.journal / 'state.tmp'
        path.write_text(json.dumps(state))
        path.replace(self.journal / 'state.json')

    def quiesce(self):
        for label in LABELS:
            self.command('launchctl', 'disable', self.domain + '/' + label)

    def drained(self):
        if not self.locked:
            locks = ExitStack()
            try:
                paths = [self.runtime / 'out/outlook-intake/lock', self.settings.send_state / 'outbox/lock',
                         self.settings.send_state / 'lock']
                for path in paths:
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    handle = locks.enter_context(os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600), 'a'))
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                locks.close()
                return False
            self.locks = locks
            self.locked = True
        live = listed(self.command('launchctl', 'list').stdout)
        return not any(live.get(label, {}).get('pid', '-').isdigit() for label in LABELS)

    def swap(self):
        state = json.loads((self.journal / 'state.json').read_text())
        for label, prior in state['states'].items():
            if prior['registered']:
                self.command('launchctl', 'bootout', self.domain + '/' + label)
        state['swapped'] = True
        self.write_state(state)
        self.command('/bin/zsh', self.source / 'bin/install-outlook-intake.sh', cwd=self.source)
        self.command('/bin/zsh', self.runtime / 'bin/install-outlook-triage.sh', cwd=self.runtime)
        for label in LABELS:
            path = self.plist_path(label)
            path.write_bytes(self.settings.render_plist(self.runtime / 'ops/launchd' / (label + '.plist')))
            path.chmod(0o644)

    def verify(self):
        if self.git(self.runtime, 'rev-parse', 'HEAD') != self.sha:
            raise ValueError('installed runtime SHA differs from requested main SHA')
        self.git(self.source, 'merge-base', '--is-ancestor', self.git(self.runtime, 'rev-parse', 'HEAD'), 'origin/main')
        self.settings.verify_helper()

    def recover(self):
        self.quiesce()
        deadline = self.clock() + 300
        while not self.drained():
            if self.clock() >= deadline:
                raise TimeoutError('recovery drain timed out; snapshot retained')
            self.sleep(1)
        self.restore(rollback=True)

    def restore(self, *, rollback):
        state = json.loads((self.journal / 'state.json').read_text())
        try:
            if rollback and state['swapped']:
                live = listed(self.command('launchctl', 'list').stdout)
                for label in LABELS:
                    if label in live:
                        self.command('launchctl', 'bootout', self.domain + '/' + label)
                self.git(self.runtime, 'checkout', '--detach', state['previous'])
                for artifact in state['artifacts']:
                    path, backup = Path(artifact['path']), Path(artifact['backup'])
                    if path.is_dir():
                        shutil.rmtree(path)
                    elif path.exists():
                        path.unlink()
                    if artifact['exists']:
                        if backup.is_dir():
                            shutil.copytree(backup, path, symlinks=True)
                        else:
                            shutil.copy2(backup, path)
            live = listed(self.command('launchctl', 'list').stdout)
            for label, prior in state['states'].items():
                if prior['registered'] and label not in live:
                    self.command('launchctl', 'enable', self.domain + '/' + label)
                    self.command('launchctl', 'bootstrap', self.domain, self.plist_path(label))
                self.command('launchctl', 'disable' if prior['disabled'] else 'enable', self.domain + '/' + label)
            shutil.rmtree(self.journal)
        finally:
            self.locks.close()
            self.locked = False


def main():
    parser = argparse.ArgumentParser(description='Safely refresh the three pinned Outlook jobs. Dry run by default. '
                                     'Run from a clean main checkout with --sha FULL_MAIN_SHA, then repeat with --apply.')
    parser.add_argument('--sha', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--timeout', type=float, default=300, help='maximum drain wait in seconds (default 300)')
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout must be positive')
    host = Host(Path(__file__).resolve().parents[1], args.sha, load_settings())
    try:
        if args.apply:
            lock_path = host.runtime.parent / ('.' + host.runtime.name + '-refresh.lock')
            with lock_path.open('a') as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                print(refresh(host, apply=True, timeout=args.timeout))
        else:
            print(refresh(host))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'refresh failed: {error}; recovery snapshot: {host.journal}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
