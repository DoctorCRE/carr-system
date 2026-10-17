#!/usr/bin/env python3
"""Local-only, read-only production migration ledger proof.

No production writes occur. Hosted CI runs offline fixtures instead.
"""
# ci: runs-outside-ci — live production reads are local-only gates
# doctrine: scripted-release-pipeline
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
import migrate
from lib.secret_redaction import redact_text, sensitive_env_values


def ledger_proof(repository, applied):
    migrations = [(name, '', digest) for name, digest in sorted(repository.items())]
    try:
        if not applied:
            raise ValueError('production migration ledger is empty')
        migrate.validate_applied_ledger(migrations, applied)
    except ValueError as exc:
        return dict(ok=False, error=str(exc))
    return dict(ok=True, applied=len(applied),
                pending=[name for name, sql, digest in migrate.pending_migrations(migrations, applied)])


def checked(command, env, label, timeout=1200):
    try:
        result = subprocess.run([str(item) for item in command], env=env,
                                capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError(f'{label} could not complete') from None
    if result.returncode:
        from psycopg.conninfo import conninfo_to_dict
        secrets = sensitive_env_values(env)
        for name in ('PGDATABASE', 'DATABASE_URL'):
            if env.get(name):
                secrets.append(env[name])
                if '=' in env[name] or '://' in env[name]:
                    secrets.extend(value for key, value in conninfo_to_dict(env[name]).items() if key == 'password')
        detail = redact_text(result.stderr.decode('utf-8', errors='replace'), known_secrets=secrets)
        raise RuntimeError(f'{label} failed (exit {result.returncode}): {detail[:1500]}')
    return result.stdout.decode('utf-8')


def production_dsn():
    # This is migrate-prod.sh's existing status credential route. Capture the
    # helper's output in memory; never invoke migrate.py's writeful dry run.
    script = '''set -eu
if [ -z "${NEON_API_KEY:-}" ] && [ -f "$HOME/.config/carr/db.env" ]; then
  . "$1/bin/routine-credential-env.sh"
  carr_require_sourceable_db_env migration-production-proof >/dev/null 2>&1
  set -a; . "$HOME/.config/carr/db.env"; set +a
fi
exec "$2" "$1/tools/migrate-prod-support.py" neon-dsn --project-id steep-field-48688294
'''
    return checked(['zsh', '-c', script, 'production-proof', ROOT, sys.executable],
                   os.environ, 'production status credential resolution', timeout=100).strip()


def read_source(dsn):
    import psycopg
    from psycopg import IsolationLevel
    with psycopg.connect(dsn, options='-c default_transaction_read_only=on -c statement_timeout=120000') as conn:
        conn.read_only = True
        conn.isolation_level = IsolationLevel.REPEATABLE_READ
        if conn.execute("select current_setting('transaction_read_only')").fetchone()[0] != 'on':
            raise RuntimeError('production read-only transaction not established')
        applied = dict(conn.execute('select filename, sha256 from schema_migrations').fetchall())
        conn.rollback()
    return applied


def live_proof():
    migrations = migrate.load_migrations()
    validate_snapshot = runpy.run_path(str(ROOT / 'ops/migration-safety-gate.py'))['ledger_problems']
    problems = validate_snapshot({name: sql for name, sql, digest in migrations},
                                (ROOT / 'db/schema.sql').read_text())
    if problems:
        raise RuntimeError('; '.join(problems))
    return dict(ledger=ledger_proof({name: digest for name, sql, digest in migrations},
                                   read_source(production_dsn())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, default=ROOT / 'out/migration-production-proof/report.json')
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS', '').lower() == 'true' or os.environ.get('CARR_CI_PORTABLE_ONLY') == '1':
        print('LOCAL-ONLY: production ledger proof not run on hosted/portable runners')
        return 2
    try:
        result = live_proof()
    except Exception as exc:
        # Never print a database exception or CalledProcessError containing a DSN.
        result = dict(ok=False, error=type(exc).__name__, detail=str(exc) if type(exc) is RuntimeError else 'proof could not complete; no production writes attempted')
    result['ok'] = result.get('ledger', {}).get('ok', False)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n')
    if 'ledger' in result:
        print('ledger:', 'PASS' if result['ledger']['ok'] else 'FAIL', json.dumps(result['ledger']))
    if 'detail' in result:
        print(result['detail'])
    print('proof report:', args.report)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
