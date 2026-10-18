"""Manual host transfer and a durable, off-host single-leader guard."""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shlex
import signal
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

TRANSFER_LOCK = 638148226000001


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    with temp.open('x') as f: json.dump(data, f, indent=2); f.write('\n')
    temp.chmod(0o600)
    temp.replace(path)


def plan(config, snapshot, target):
    missing = []
    for flag, description in [('canonical', 'clean canonical checkout on main'),
                              ('leader_ready', 'off-host leader schema, grants and direct DB connection'),
                              ('gui', 'logged-in launchd GUI domain')]:
        if not snapshot.get(flag): missing.append(description)
    steps = [{'action': 'preflight', 'target': target},
             {'action': 'fence-source', 'detail': 'demote and verify source, or bind a power-off receipt'},
             {'action': 'transfer-leader', 'detail': 'compare owner and epoch under exclusive transfer lock'}]
    for job in config['jobs']:
        for path in [job['source'], *job.get('credentials', []), *job.get('state', []),
                     *job.get('executables', [])]:
            if not snapshot.get('paths', {}).get(path): missing.append(job['label'] + ': ' + path)
        if job.get('state') and not snapshot.get('state_verified', {}).get(job['label']):
            missing.append(job['label'] + ': verified state restoration receipt')
        steps.append({'action': 'install', 'label': job['label'], 'source': job['source']})
        steps.append({'action': 'start' if job.get('enabled', True) else 'preserve-disabled', 'label': job['label']})
    for role in config.get('manual_roles', []):
        if not snapshot.get('manual_verified', {}).get(role['name']):
            missing.append(role['name'] + ': ' + role['prerequisite'])
    steps.append({'action': 'verify', 'detail': 'leader readback, guarded definitions and launchctl registration'})
    return {'target': target, 'ready': not missing, 'missing': missing, 'steps': steps}


def transfer(host, source, target):
    if not host.fence(source):
        raise RuntimeError('source_not_fenced')
    host.claim(source, target)
    try:
        host.install()
        host.start()
        if not host.verify():
            raise RuntimeError('target_verification_failed; leadership retained at target')
    except Exception:
        host.abort()
        raise


def connection(kind='jobs', home=None):
    """Use dedicated non-pooled DSNs. Never log the DSN or connector exceptions."""
    import psycopg
    path = Path(home or Path.home()) / '.config/carr/failover.env'
    if path.stat().st_mode & 0o077: raise RuntimeError('failover credential file requires mode 600')
    values = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'): continue
        key, sep, value = line.partition('=')
        if sep:
            tokens = shlex.split(value)
            if len(tokens) == 1: values[key.strip()] = tokens[0]
    key = 'CARR_FAILOVER_JOBS_URL' if kind == 'jobs' else 'CARR_FAILOVER_AUTHORITY_URL'
    dsn = values.get(key, '')
    parsed = urlsplit(dsn)
    expected = 'carr_jobs' if kind == 'jobs' else 'carr_authority_joe'
    if parsed.scheme not in ('postgres', 'postgresql') or parsed.username != expected:
        raise RuntimeError('failover credential identity missing or invalid')
    query = parse_qs(parsed.query)
    if (not (parsed.hostname or '').endswith('.neon.tech')
        or '-pooler' in parsed.hostname or parsed.port == 6432
        or set(query) - {'sslmode', 'channel_binding'}
        or query.get('sslmode') not in (['require'], ['verify-ca'], ['verify-full'])):
        raise RuntimeError('session locks require a direct off-host Neon endpoint with TLS')
    conn = psycopg.connect(dsn, autocommit=True, connect_timeout=5,
                           options='-c statement_timeout=5000',
                           keepalives_idle=5, keepalives_interval=2, keepalives_count=2)
    if conn.execute('select session_user,current_user').fetchone() != (expected, expected):
        conn.close()
        raise RuntimeError('failover credential identity mismatch')
    return conn


class Leader:
    def __init__(self, conn):
        self.conn = conn
        self.epoch = None

    def read(self):
        row = self.conn.execute("select host,epoch from ops.studio_leader where singleton").fetchone()
        if not row: raise RuntimeError('leader row missing')
        return row

    def acquire(self, host, label):
        if not self.conn.execute('select pg_try_advisory_lock_shared(%s)', (TRANSFER_LOCK,)).fetchone()[0]:
            return False
        owner, self.epoch = self.read()
        key = int.from_bytes(hashlib.sha256(('carr:studio-job:' + label).encode()).digest()[:8], 'big', signed=True)
        return owner == host and self.conn.execute('select pg_try_advisory_lock(%s)', (key,)).fetchone()[0]

    def healthy(self, host):
        return self.read() == (host, self.epoch)

    def claim(self, source, target, evidence):
        _, epoch = self.read()
        return self.conn.execute('select ops.transfer_studio_leader(%s,%s,%s,%s::jsonb)',
                                 (source, target, epoch, json.dumps(evidence))).fetchone()[0]

    def close(self):
        self.conn.close()


def group_alive(child):
    # Darwin can report EPERM for killpg(..., 0) after the group has exited.
    r = subprocess.run(['ps', '-axo', 'pgid=,stat='], capture_output=True, text=True, timeout=5)
    if r.returncode: raise RuntimeError('process_group_census_failed')
    return any(parts[0] == str(child.pid) and not parts[1].startswith('Z')
               for line in r.stdout.splitlines() if len(parts := line.split()) >= 2)


def stop_group(child):
    """Stop all descendants before releasing locks, even if the group leader exited."""
    for sig, timeout in ((signal.SIGTERM, 5), (signal.SIGKILL, 2)):
        if not group_alive(child): break
        try: os.killpg(child.pid, sig)
        except ProcessLookupError: break
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            child.poll()
            if not group_alive(child): return
            time.sleep(.1)
    child.wait()
    if group_alive(child): raise RuntimeError('process_group_survived_kill')


def run_guarded(authority, host, label, launch, disarm=lambda: None):
    child = None
    old_handlers = {}
    try:
        if not authority.acquire(host, label): return 75
        def interrupted(signum, frame): raise InterruptedError('guard interrupted')
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            old_handlers[sig] = signal.signal(sig, interrupted)
        child = launch()
        while child.poll() is None or group_alive(child):
            if not authority.healthy(host):
                disarm()
                return 76
            time.sleep(.5)
        return child.returncode
    except Exception:
        disarm()
        return 76
    finally:
        for sig in old_handlers: signal.signal(sig, signal.SIG_IGN)
        try:
            if child is not None: stop_group(child)
        finally:
            authority.close()
            for sig, handler in old_handlers.items(): signal.signal(sig, handler)


def guarded_plist(body, repo, host, labels):
    p = plistlib.loads(body.encode() if isinstance(body, str) else body)
    if p.get('Label') not in labels: return body
    p.pop('AbandonProcessGroup', None)
    args = p.get('ProgramArguments') or [p['Program']]
    if str(repo / 'ops/studio-job-guard.py') not in args:
        p.pop('Program', None)
        p['ProgramArguments'] = [str(repo / '.venv/bin/python'), str(repo / 'ops/studio-job-guard.py'),
                                 host, p['Label'], '--', *args]
    return plistlib.dumps(p).decode()


def managed_body(body, repo, home=None):
    """The config reconciler preserves guards after preparation; no settings edits."""
    home = Path(home or Path.home())
    marker = home / '.config/carr/failover-host.json'
    if not marker.exists(): return body
    host = json.loads(marker.read_text())['host']
    config = json.loads((repo / 'ops/config/studio-failover.v1.json').read_text())
    return guarded_plist(body, repo, host, {j['label'] for j in config['jobs']})
