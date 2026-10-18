"""Monthly rehearsal evidence and one deduplicated orchestrator remediation loop."""
import hashlib
import json
import socket
import fcntl
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

ACTION = ('on breach: ops/studio-failover-health.py --reconcile opens/updates one loop; '
          'owner orchestrator; fix missing prerequisites and rerun the MacBook dry-run; '
          'verify a complete current-contract rehearsal; auto-clear on a fresh ready receipt')


def contract_hash(root):
    config = json.loads((root / 'ops/config/studio-failover.v1.json').read_text())
    paths = ['ops/config/studio-failover.v1.json', 'lib/studio_failover.py', 'ops/studio-failover.py',
             'ops/studio-job-guard.py', *[j['source'] for j in config['jobs']]]
    return hashlib.sha256(json.dumps([(p, hashlib.sha256((root / p).read_bytes()).hexdigest())
                                     for p in sorted(paths)]).encode()).hexdigest()


def evaluate(report, now, expected, required_steps):
    valid = isinstance(report, dict) and report.get('schema') == 'carr-failover-rehearsal/v1'
    reason = 'missing or unreadable rehearsal'
    if valid:
        try:
            age = (now - datetime.fromisoformat(report['verified_at'].replace('Z', '+00:00'))).total_seconds()
            valid = (0 <= age <= 35 * 86400 and report.get('mode') == 'dry-run'
                     and report.get('target') == 'macbook' and report.get('contract_hash') == expected
                     and report.get('ready') is True and report.get('missing') == []
                     and report.get('steps') == required_steps
                     and {s['action'] for s in report.get('steps', [])} >=
                         {'preflight', 'fence-source', 'transfer-leader', 'install', 'start', 'verify'})
            reason = 'rehearsal stale, failed, incomplete, or contract changed'
        except (ValueError, TypeError, KeyError): valid = False
    status = 'ok' if valid else 'warn'
    detail = 'fresh MacBook dry-run verified' if valid else reason
    if not valid and isinstance(report, dict) and report.get('missing'):
        detail += '; missing: ' + '; '.join(report['missing'])[:2500]
    return {'status': status, 'detail': detail, 'line': f'{"OK" if valid else "WARN"} Studio failover {detail} · {ACTION}'}


def read_report(root, config):
    if socket.gethostname() == config['hosts']['macbook']['hostname']:
        return json.loads((root / 'out/studio-failover/latest.json').read_text())
    # Fixed, path-only receipt; never run a failover operation from the health path.
    r = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                        config['hosts']['macbook']['ssh'], 'cat ~/carr-system/out/studio-failover/latest.json'],
                       capture_output=True, text=True, timeout=15)
    if r.returncode: return None
    return json.loads(r.stdout)


def reconcile(result, path, verb):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _reconcile(result, path, verb)


def _reconcile(result, path, verb):
    from lib.studio_failover import write_json
    try: state = json.loads(path.read_text())
    except FileNotFoundError: state = {}
    if result['status'] == 'warn':
        if state.get('loop_id'):
            if state.get('detail') != result['detail']:
                key = str(uuid.uuid5(uuid.UUID(state['open_key']), result['detail']))
                response = verb('update-loop', {'idempotency_key': key, 'loop_id': state['loop_id'],
                                                'body': result['detail'] + '. ' + ACTION})
                if not response.get('ok'): return 'record_failed'
                write_json(path, {**state, 'detail': result['detail']})
                return 'updated'
            return 'open'
        key = state.setdefault('open_key', str(uuid.uuid4()))
        write_json(path, state)
        response = verb('add-loop', {'idempotency_key': key, 'kind': 'open_loop',
            'owner': 'Claude', 'domain': 'system', 'blocker': 'capability',
            'blocker_detail': 'MacBook takeover lacks a current passing prerequisite rehearsal',
            'body': 'Studio failover readiness requires orchestrator remediation. ' + result['detail'] + '. ' + ACTION})
        if not response.get('ok') or not response.get('loop_id'): return 'record_failed'
        write_json(path, {**state, 'loop_id': response['loop_id'], 'detail': result['detail']})
        return 'opened'
    if state.get('loop_id'):
        key = state.setdefault('close_key', str(uuid.uuid4()))
        write_json(path, state)
        response = verb('close-loop', {'idempotency_key': key, 'loop_id': state['loop_id'],
            'resolution': 'done', 'outcome': 'Fresh current-contract MacBook dry-run verified every prerequisite.'})
        if not response.get('ok'): return 'record_failed'
        write_json(path, {})
        return 'cleared'
    return 'none'
