#!/usr/bin/env python3
"""Read MacBook rehearsal evidence; reconcile the bound orchestrator loop on request."""
import argparse
import json
import subprocess
import sys
import socket
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.studio_failover_health import contract_hash, evaluate, read_report, reconcile
from lib.studio_failover import plan


def call_verb(name, payload):
    r = subprocess.run([str(ROOT / 'run.sh'), 'call', name, json.dumps(payload)],
                       capture_output=True, text=True, timeout=30)
    try: return json.loads(r.stdout) if r.returncode == 0 else {'ok': False}
    except ValueError: return {'ok': False}


def check(root=ROOT, reconcile_loop=False):
    config = json.loads((root / 'ops/config/studio-failover.v1.json').read_text())
    try: report = read_report(root, config)
    except (OSError, ValueError, subprocess.SubprocessError): report = None
    result = evaluate(report, datetime.now(timezone.utc), contract_hash(root), plan(config, {}, 'macbook')['steps'])
    if reconcile_loop:
        if socket.gethostname() != config['hosts']['macbook']['hostname']:
            # One host owns the loop ledger so Studio and MacBook health cannot mint two loops.
            r = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                                config['hosts']['macbook']['ssh'],
                                'cd ~/carr-system && .venv/bin/python ops/studio-failover-health.py --reconcile'],
                               capture_output=True, text=True, timeout=45)
            if r.stdout.startswith(('OK Studio failover ', 'WARN Studio failover ')):
                return {'status': 'ok' if r.returncode == 0 else 'warn', 'line': r.stdout.strip()}
            result['status'] = 'warn'
            result['line'] += '; MacBook remediation recorder unavailable'
            return result
        try: outcome = reconcile(result, root / 'out/studio-failover/loop.json', call_verb)
        except Exception: outcome = 'record_failed'
        result['line'] += '; record ' + outcome
        if outcome == 'record_failed':
            result['status'] = 'warn'
            result['line'] = result['line'].replace('OK Studio failover ', 'WARN Studio failover ', 1)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reconcile', action='store_true')
    result = check(reconcile_loop=parser.parse_args().reconcile)
    print(result['line'])
    raise SystemExit(0 if result['status'] == 'ok' else 2)
