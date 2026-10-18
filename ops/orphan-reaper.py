#!/usr/bin/env python3
"""Reap sustained CPU-burning orphan session shells. Control orphan-process-reaper.

Only sampled high CPU throughout a continuous observation window counts. A missed
poll, PID reuse, CPU drop or reboot resets the window. Native census failures stop
the run before signaling. Launchd job trees remain protected after reparenting
when their identities were observed. State and pending record writes survive runs.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTECTED = re.compile(r'com\.carr\.|merge[-_]queue|dot[-_/ ]*(?:relay|feeder|supervisor)|dispatch\.py|(?:^|\s)(?:\S*/)?codex(?:\s|$)|\bnode\b', re.I)
SHELLS = {'sh', 'bash', 'zsh', 'dash', 'ksh', 'fish'}


@dataclass(frozen=True)
class Process:
    pid: int
    ppid: int
    uid: int
    started: str
    age: float
    cpu: float
    comm: str
    args: str
    cwd: str


def identity(p):
    return f'{p.pid}:{p.uid}:{p.started}'


def load_config(path):
    config = json.loads(path.read_text())
    expected = {'version', 'cpu_threshold', 'min_age_seconds', 'term_grace_seconds',
                'max_sample_gap_seconds', 'max_targets'}
    if not isinstance(config, dict) or set(config) != expected or type(config['version']) is not int or config['version'] != 1:
        raise ValueError('invalid orphan reaper config')
    for key in expected - {'version'}:
        value = config[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f'invalid {key}')
    for key in ('max_targets',):
        if type(config[key]) is not int:
            raise ValueError(f'{key} must be an integer')
    if config['max_sample_gap_seconds'] < 300 or config['term_grace_seconds'] != 10:
        raise ValueError('poll gap must cover five minutes; TERM grace must be ten seconds')
    return config


def session_shell(p, uid, home):
    if Path(p.comm).name not in SHELLS or p.uid != uid:
        return False
    roots = [home / '.claude/shell-snapshots', home / '.codex/shell_snapshots',
             home / '.codex/shell-snapshots']
    for root in roots:
        path = re.escape(str(root)) + r'/[^\s\x27";]+'
        if re.search(r'(?:^|[;\s])(?:source|\.)\s+[\x27"]?' + path, p.args):
            return True
        if re.match(r'^\S+\s+[\x27"]?' + path, p.args):
            return True
    cwd = os.path.normpath(p.cwd)
    scratch = f'/private/tmp/claude-{uid}/'
    return cwd.startswith(scratch)


def protected_identities(table, managed, old):
    protected = {identity(p) for p in table if p.pid in managed or identity(p) in old
                 or PROTECTED.search(p.args)}
    parents = {p.pid for p in table if identity(p) in protected}
    changed = True
    while changed:
        changed = False
        for p in table:
            if p.ppid in parents and p.pid not in parents:
                parents.add(p.pid)
                protected.add(identity(p))
                changed = True
    return protected


def eligible(p, protected, config, uid, home):
    return (p.pid > 1 and p.ppid == 1 and identity(p) not in protected
            and not PROTECTED.search(p.args) and session_shell(p, uid, home)
            and math.isfinite(p.cpu) and p.cpu > config['cpu_threshold']
            and math.isfinite(p.age) and p.age > config['min_age_seconds'])


def scan(table, managed, previous, now, config, uid, home):
    protected = protected_identities(table, managed, previous.get('protected', []))
    digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    old = previous.get('observations', {}) if previous.get('config_digest') == digest else {}
    observations, candidates = {}, []
    for p in table:
        if not eligible(p, protected, config, uid, home):
            continue
        key = identity(p)
        prior = old.get(key)
        continuous = prior and 0 <= now - prior['last'] <= config['max_sample_gap_seconds']
        since = prior['since'] if continuous else now
        observations[key] = {'since': since, 'last': now}
        if now - since > config['min_age_seconds']:
            candidates.append(p)
    return {'observations': observations, 'protected': sorted(protected), 'config_digest': digest,
            'pending': previous.get('pending', []),
            'attempted': sorted(set(previous.get('attempted', [])) & {identity(p) for p in table})}, candidates


def parse_ps(text, now):
    rows = []
    for line in text.splitlines():
        fields = line.split(None, 10)
        if len(fields) < 10:
            raise ValueError('incomplete process census')
        pid, ppid, uid = map(int, fields[:3])
        cpu = float(fields[3])
        started = ' '.join(fields[4:9])
        age = now - time.mktime(time.strptime(started, '%a %b %d %H:%M:%S %Y'))
        if not math.isfinite(cpu) or cpu < 0 or age < 0:
            raise ValueError('invalid process census value')
        rows.append(Process(pid, ppid, uid, started, age, cpu, fields[9], fields[10] if len(fields) > 10 else '', ''))
    if not rows or len({p.pid for p in rows}) != len(rows):
        raise ValueError('empty or duplicate process census')
    return rows


def parse_launchctl(text):
    lines = text.splitlines()
    if not lines or lines[0].split() != ['PID', 'Status', 'Label']:
        raise ValueError('unproved launchd inventory')
    managed = set()
    for line in lines[1:]:
        fields = line.split()
        if len(fields) != 3:
            raise ValueError('incomplete launchd inventory')
        int(fields[1])
        if fields[0] != '-':
            managed.add(int(fields[0]))
    return managed


def run_command(argv, *, cwd=None, timeout=10):
    child = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, stdin=subprocess.DEVNULL, start_new_session=True,
                             env=dict(os.environ, LC_ALL='C'))
    try:
        stdout, stderr = child.communicate(timeout=timeout)
        return subprocess.CompletedProcess(argv, child.returncode, stdout, stderr)
    finally:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()


def collect_native(config):
    def read(argv, accepted=(0,)):
        result = run_command(argv)
        if result.returncode not in accepted:
            raise RuntimeError(f'{argv[0]} census failed with exit {result.returncode}')
        return result.stdout
    managed = parse_launchctl(read(['/bin/launchctl', 'list']))
    table = parse_ps(read(['/bin/ps', '-ww', '-axo', 'pid=,ppid=,uid=,pcpu=,lstart=,comm=,args=']), time.time())
    shells = [p for p in table if p.uid == os.getuid() and p.ppid == 1
              and Path(p.comm).name in SHELLS and p.cpu > config['cpu_threshold']]
    if len(shells) > config['max_targets']:
        raise RuntimeError('orphan shell census cap exceeded')
    cwd = {}
    if shells:
        text = read(['/usr/sbin/lsof', '-a', '-p', ','.join(str(p.pid) for p in shells), '-d', 'cwd', '-Fn'], (0, 1))
        pid = None
        for line in text.splitlines():
            if line.startswith('p'):
                pid = int(line[1:])
            elif line.startswith('n') and pid is not None:
                cwd[pid] = line[1:]
    return [replace(p, cwd=cwd.get(p.pid, '')) for p in table], managed


def save(path, data):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as f:
        temporary = Path(f.name)
        try:
            json.dump(data, f, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)


def read_state(path):
    if not path.exists():
        return {}
    state = json.loads(path.read_text())
    if not isinstance(state, dict) or set(state) != {'observations', 'protected', 'pending', 'config_digest', 'attempted'}:
        raise ValueError('invalid reaper state')
    if not isinstance(state['config_digest'], str) or not re.fullmatch('[a-f0-9]{64}', state['config_digest']):
        raise ValueError('invalid config digest')
    if not isinstance(state['observations'], dict) or not isinstance(state['protected'], list) or not isinstance(state['pending'], list):
        raise ValueError('invalid reaper state shape')
    for row in state['observations'].values():
        if not isinstance(row, dict) or set(row) != {'since', 'last'}:
            raise ValueError('invalid observation')
        if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in row.values()) or row['since'] > row['last']:
            raise ValueError('invalid observation time')
    if any(not isinstance(key, str) for key in state['protected']):
        raise ValueError('invalid protected process')
    if not isinstance(state['attempted'], list) or any(not isinstance(key, str) for key in state['attempted']):
        raise ValueError('invalid attempted process')
    for payload in state['pending']:
        if not isinstance(payload, dict) or not payload.get('idempotency_key'):
            raise ValueError('invalid pending finding')
    return state


def record_defect(repo, payload):
    try:
        result = run_command([str(repo / 'run.sh'), 'call', 'record-defect', json.dumps(payload)],
                             cwd=repo, timeout=30)
    except subprocess.SubprocessError:
        raise RuntimeError('record-defect transport failed; finding remains pending') from None
    if result.returncode != 0:
        raise RuntimeError('record-defect failed; finding remains pending')
    try:
        response = json.loads(result.stdout[result.stdout.index('{'):])
        if response.get('ok') is not True:
            raise ValueError('record write refused')
    except (ValueError, AttributeError):
        raise RuntimeError('record-defect returned no successful receipt; finding remains pending') from None


def run_once(repo, config, *, dry_run=False, collect=None, clock=time.monotonic,
             send_signal=os.kill, sleep=time.sleep, reporter=None, uid=None, home=None):
    collect = collect or (lambda: collect_native(config))
    reporter = reporter or (lambda payload: record_defect(repo, payload))
    uid, home = os.getuid() if uid is None else uid, home or Path.home()
    out = repo / 'out'
    out.mkdir(exist_ok=True)
    path = out / 'orphan-reaper-state.json'
    deadline = clock() + 240
    with (out / 'orphan-reaper.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('another orphan reaper is running') from None
        state = read_state(path)
        def flush_pending():
            for _ in range(config['max_targets']):
                if not state.get('pending'):
                    return
                if clock() >= deadline:
                    raise RuntimeError('reaper deadline expired; findings remain pending')
                try:
                    reporter(state['pending'][0])
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    raise RuntimeError('record reporting unavailable; findings remain pending') from None
                state['pending'].pop(0)
                save(path, state)

        def persist_action(payload, row):
            payload['actual'] = ('An orphan session shell sustained high CPU beyond the observation limit. '
                                 + json.dumps(row))
            save(path, state)
            with (out / 'orphan-reaper.jsonl').open('a') as log:
                log.write(json.dumps(row) + '\n')
                log.flush()
                os.fsync(log.fileno())

        def signal_action(p, sig, payload, row):
            field = sig.name
            row[field] = 'unconfirmed'
            persist_action(payload, row)
            try:
                send_signal(p.pid, sig)
            except ProcessLookupError:
                row[field] = 'process_missing'
                persist_action(payload, row)
                return False
            except OSError:
                row[field] = 'failed'
                persist_action(payload, row)
                raise
            row[field] = 'sent'
            persist_action(payload, row)
            return True

        try:
            table, managed = collect()
            state, candidates = scan(table, managed, state, clock(), config, uid, home)
            candidates = [p for p in candidates if identity(p) not in state['attempted']]
            if len(candidates) > config['max_targets']:
                raise RuntimeError('reap batch cap exceeded')
            if dry_run:
                print(json.dumps({'dry_run': True, 'would_reap': [p.pid for p in candidates]}))
                return len(candidates)
            save(path, state)
            count = 0
            terminated = []
            for candidate in candidates:
                fresh, managed = collect()
                state, ready = scan(fresh, managed, state, clock(), config, uid, home)
                save(path, state)
                current = {identity(p): p for p in fresh}
                p = current.get(identity(candidate))
                if p is None or p not in ready:
                    continue
                occurred = datetime.fromtimestamp(time.time(), timezone.utc)
                row = {'pid': p.pid, 'uid': p.uid, 'started': p.started,
                       'shell': Path(p.comm).name, 'cpu': p.cpu, 'age': p.age,
                       'occurred_at': occurred.isoformat()}
                payload = {'idempotency_key': str(uuid.uuid4()), 'defect_class': 'orphan-session-cpu-burner',
                           'occurred_on': occurred.date().isoformat(),
                           'claimed': 'Session subprocess cleanup confines background work to its owning session.',
                           'rule_violated': '36856823', 'detected_by': 'check',
                           'source_unread': 'orphan-process-reaper process census'}
                state['pending'].append(payload)
                state['attempted'].append(identity(p))
                if not signal_action(p, signal.SIGTERM, payload, row):
                    continue
                terminated.append((p, payload, row))
                count += 1
            if terminated:
                end = clock() + config['term_grace_seconds']
                while clock() < end:
                    sleep(min(.2, end - clock()))
                for original, payload, row in terminated:
                    fresh, managed = collect()
                    state, ready = scan(fresh, managed, state, clock(), config, uid, home)
                    save(path, state)
                    current = {identity(p): p for p in fresh}
                    p = current.get(identity(original))
                    if p is not None and p in ready:
                        signal_action(p, signal.SIGKILL, payload, row)
        finally:
            if not dry_run:
                active_error = sys.exc_info()[0] is not None
                try:
                    flush_pending()
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    if not active_error:
                        raise
                    print('record reporting unavailable; findings remain pending', file=sys.stderr)
        return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--config', type=Path, default=ROOT / 'ops/config/orphan-reaper.v1.json')
    args = parser.parse_args()
    # Bound the complete one-shot, including census, grace and record transport.
    def expired(*_):
        raise RuntimeError('reaper run exceeded 240 seconds')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(240)
    try:
        count = run_once(ROOT, load_config(args.config), dry_run=args.dry_run)
        print(json.dumps({'reaped': count}) if not args.dry_run else '')
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'orphan-process-reaper stopped: {exc}', file=sys.stderr)
        return 1
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    raise SystemExit(main())
