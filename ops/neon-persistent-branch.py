#!/usr/bin/env python3
"""Create the policy-registered schema-initialized Neon project after live budget admission."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
import re
import subprocess
import urllib.error
from pathlib import Path
import sys
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
from lib.platform_metering import MeteringRefusal, authorize_metered_execution
from lib.release_reader import NoRedirect, read
from tools.system_costs import normalize, summarize

API = 'https://console.neon.tech/api/v2'
GATE = 'neon-persistent-branch'


class ProviderError(MeteringRefusal):
    """Only sanitized provider details may cross the CLI interface."""

    def __init__(self, status, message):
        self.status = status
        self.message = message
        self.creation_outcome = 'unknown'
        super().__init__(f'Neon HTTP {status}: {message}')

    def receipt(self):
        return {'creation_confirmed': False, 'http_status': self.status,
                'provider_message': self.message, 'creation_outcome': self.creation_outcome}


def _provider_error(status, body, token):
    try:
        message = json.loads(body).get('message')
    except (ValueError, AttributeError):
        message = None
    if not isinstance(message, str):
        message = 'provider returned no JSON error message'
    message = message.replace(token, '[redacted]') if token else message
    message = re.sub(r'(?i)\b(?:postgres(?:ql)?|https?)://[^\s"\']+', '[redacted URI]', message)
    message = re.sub(r'(?i)\bBearer\s+\S+', 'Bearer [redacted]', message)
    message = re.sub(r'(?i)\b(password|token|secret|api[_-]?key)\s*[:=]\s*\S+',
                     r'\1=[redacted]', message)
    return ProviderError(status, ' '.join(message.split())[:1000])


def _monthly_costs(api, costs, now, project_id):
    config = costs['neon']
    first = now.date().replace(day=1)
    end = first.replace(year=first.year + 1, month=1) if first.month == 12 else first.replace(month=first.month + 1)
    query = {'org_id': config['org_id'], 'from': first.isoformat() + 'T00:00:00Z',
             'to': end.isoformat() + 'T00:00:00Z', 'granularity': 'monthly',
             'metrics': ','.join(config['rates']), 'limit': 100}
    projects, seen, cursor = [], set(), None
    for _ in range(100):
        page = api('GET', '/consumption_history/v2/projects?' + urllib.parse.urlencode(
            {**query, **({'cursor': cursor} if cursor else {})}))
        projects.extend(page['projects'])
        cursor = page.get('pagination', {}).get('cursor')
        if not cursor:
            break
        if cursor in seen:
            raise MeteringRefusal('consumption pagination repeated')
        seen.add(cursor)
    else:
        raise MeteringRefusal('consumption pagination exceeded limit')
    if (project_id not in {p['project_id'] for p in projects}
            or len({p['project_id'] for p in projects}) != len(projects)):
        raise MeteringRefusal('missing or repeated consumption projects')
    for project in projects:
        if project['project_id'] == project_id and not project['periods']:
            raise MeteringRefusal('missing consumption periods')
        for period in project['periods']:
            if period['period_plan'] != config['plan'] or not period['consumption']:
                raise MeteringRefusal('unknown plan or missing consumption')
            for frame in period['consumption']:
                metrics = frame['metrics']
                names = [m['metric_name'] for m in metrics]
                # Neon omits zero-valued metrics from otherwise complete timeframes.
                if (frame['timeframe_start'] != query['from']
                        or frame['timeframe_end'] != query['to']
                        or not names or not set(names) <= set(config['rates'])
                        or len(names) != len(set(names))):
                    raise MeteringRefusal('incomplete current monthly consumption')
    source = normalize('neon', {'projects': projects}, config['rates'])
    report = summarize(costs, {'neon': source}, now.date())
    return next(provider for provider in report['providers'] if provider['provider'] == 'neon')


def create_registered_project(name, policy, costs, api, intent, now, *, initialize=None):
    registration = policy['execution_gates'][GATE]['registration']
    if name != registration['name'] or costs['neon']['org_id'] != registration['org_id']:
        raise MeteringRefusal('unknown persistent registration or billing organization')
    if intent.exists():
        raise MeteringRefusal('creation intent exists; reconcile provider state before recovery')
    source = registration['source']
    project = source['project_id']
    project_path = f'/projects/{project}'
    source_project = api('GET', project_path)['project']
    if source_project['org_id'] != registration['org_id']:
        raise MeteringRefusal('project organization differs from registration')
    inventory = api('GET', project_path + '/branches')
    if inventory.get('pagination', {}).get('cursor'):
        raise MeteringRefusal('branch inventory is incomplete')
    branches = inventory['branches']
    parents = [b for b in branches if b['id'] == source['branch_id']]
    if len(parents) != 1 or parents[0]['name'] != source['branch_name']:
        raise MeteringRefusal('registered production source is absent or renamed')
    projects_path = '/projects?' + urllib.parse.urlencode({'org_id': registration['org_id'], 'limit': 100})
    projects = api('GET', projects_path)
    if (projects.get('pagination', {}).get('cursor')
            or not isinstance(projects.get('projects'), list)
            or not all(isinstance(p, dict) and isinstance(p.get('name'), str) for p in projects['projects'])):
        raise MeteringRefusal('project inventory is incomplete')
    existing = sum(p['name'] == name for p in projects['projects'])
    spending = api('GET', f"/organizations/{registration['org_id']}/billing/spending_limit")
    cents = spending['spending_limit_cents']
    if isinstance(cents, bool) or not isinstance(cents, int) or cents < 0:
        raise MeteringRefusal('organization spending limit is not configured')
    monthly = _monthly_costs(api, costs, now, project)
    request = {'registration': registration, 'existing_registered_projects': existing,
               'monthly_projection_usd': monthly['projection_usd'],
               'organization_spending_limit_usd': cents / 100,
               'meter_month': now.strftime('%Y-%m')}
    decision = authorize_metered_execution(policy, GATE, request, today=now.date())
    payload = {'project': {'name': name, 'org_id': registration['org_id'],
               'region_id': source_project['region_id'], 'pg_version': source_project['pg_version'],
               'branch': {'name': name},
               'default_endpoint_settings': {k: (0 if k == 'suspend_timeout_seconds' else v)
                   for k, v in registration['endpoint'].items() if k != 'type'}}}
    # Keep the intent even on timeout: POST may have succeeded without a reply.
    with intent.open('x') as handle:
        json.dump({'registration': name, 'requested_at': now.isoformat()}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        result = api('POST', '/projects', payload)
    except ProviderError as exc:
        if 400 <= exc.status < 500 and exc.status != 408:
            try:
                inventory = api('GET', projects_path)
                absent = (not inventory.get('pagination', {}).get('cursor')
                          and isinstance(inventory.get('projects'), list)
                          and all(isinstance(b, dict) and isinstance(b.get('name'), str)
                                  and b['name'] != name for b in inventory['projects']))
            except Exception:
                absent = False
            if absent:
                intent.unlink()
                exc.creation_outcome = 'not_created'
        raise
    destination = result['project']
    branch, endpoints = result['branch'], result['endpoints']
    if (destination.get('name') != name
            or destination.get('org_id') != registration['org_id']
            or not isinstance(destination.get('id'), str) or not destination['id']
            or destination['id'] == project
            or not isinstance(branch.get('id'), str) or not branch['id']
            or branch['id'] == source['branch_id'] or branch['name'] != name
            or branch.get('parent_id') is not None
            or branch.get('project_id') != destination['id'] or branch.get('default') is not True or len(endpoints) != 1
            or endpoints[0]['branch_id'] != branch['id']
            or any(endpoints[0].get(k) != v for k, v in registration['endpoint'].items())):
        raise MeteringRefusal('provider result differs from registration; reconcile creation intent')
    with intent.open('w') as handle:
        json.dump({'registration': name, 'requested_at': now.isoformat(),
                   'project_id': destination['id'], 'branch_id': branch['id']}, handle)
        handle.flush()
        os.fsync(handle.fileno())

    def connection_uri(project_id, branch_id):
        query = urllib.parse.urlencode({'branch_id': branch_id, 'database_name': source['database_name'],
                                        'role_name': source['role_name'], 'pooled': 'false'})
        return api('GET', f'/projects/{project_id}/connection_uri?' + query)['uri']
    proof = (initialize or initialize_schema)(connection_uri(project, source['branch_id']),
              connection_uri(destination['id'], branch['id']),
              Path(registration['initialization']['dump_path']))
    if proof.get('schema_parity') is not True or proof.get('zero_rows') is not True:
        raise MeteringRefusal('schema initialization proof failed; reconcile creation intent')
    return {'gate': decision['gate'], 'project_id': destination['id'], 'branch_id': branch['id'],
            'endpoint_id': endpoints[0]['id'], 'schema_proof': proof, 'name': name,
            'monthly_cost_ceiling_usd': registration['monthly_cost_ceiling_usd'],
            'monthly_spend_usd': monthly['mtd_usd'],
            'monthly_projection_usd': request['monthly_projection_usd'], 'creation_confirmed': True}


CATALOG = """
WITH objects AS (
 SELECT n.nspname, CASE c.relkind WHEN 'r' THEN 'table' WHEN 'p' THEN 'table'
   WHEN 'v' THEN 'view' WHEN 'm' THEN 'materialized_view' WHEN 'S' THEN 'sequence'
   WHEN 'f' THEN 'foreign_table' END AS kind
 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE c.relkind IN ('r','p','v','m','S','f')
 UNION ALL
 SELECT n.nspname, 'function' FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 UNION ALL
 SELECT n.nspname, 'trigger' FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
 JOIN pg_namespace n ON n.oid=c.relnamespace WHERE NOT t.tgisinternal
)
SELECT nspname, kind, count(*) FROM objects
WHERE nspname !~ '^pg_' AND nspname <> 'information_schema'
GROUP BY nspname, kind ORDER BY nspname, kind
"""


def initialize_schema(source_uri, destination_uri, path):
    """Dump structure only, restore atomically, and refuse incomplete or nonempty schemas.

    Ownership, ACLs and role-bound policies are intentionally deferred to the
    remainder that provisions test identities. No production roles are copied.
    """
    import psycopg
    from psycopg import sql

    def catalog(uri):
        with psycopg.connect(uri, connect_timeout=20,
                options='-c default_transaction_read_only=on -c statement_timeout=30000') as db:
            return db.execute(CATALOG).fetchall()

    expected = catalog(source_uri)
    if any(kind == 'foreign_table' for _, kind, _ in expected):
        raise MeteringRefusal('foreign tables need a separately reviewed schema import')
    if not expected:
        raise MeteringRefusal('production schema catalog is empty')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Exclusive creation prevents overwriting an earlier private evidence artifact.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as dump:
        env = {**os.environ, 'PGDATABASE': source_uri,
               'PGOPTIONS': '-c default_transaction_read_only=on -c statement_timeout=120000'}
        subprocess.run(['pg_dump', '--schema-only', '--no-owner', '--no-privileges',
                        '--no-policies', '--no-subscriptions', '--no-security-labels',
                        '--lock-wait-timeout=10s', '--no-password'],
                       env=env, stdout=dump, stderr=subprocess.PIPE, check=True, timeout=300)
        dump.flush()
        os.fsync(dump.fileno())
    with path.open() as dump:
        subprocess.run(['psql', '-X', '--no-password', '--set=ON_ERROR_STOP=1',
                        '--single-transaction'],
                       env={**os.environ, 'PGDATABASE': destination_uri,
                            'PGOPTIONS': '-c statement_timeout=120000 -c lock_timeout=10000'},
                       stdin=dump, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       check=True, timeout=300)
    actual = catalog(destination_uri)
    if expected != actual:
        raise MeteringRefusal('restored schema object counts differ from production')
    with psycopg.connect(destination_uri, connect_timeout=20,
            options='-c default_transaction_read_only=on -c row_security=off -c statement_timeout=30000') as db:
        tables = db.execute("""SELECT n.nspname, c.relname FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE (c.relkind IN ('r','p') OR (c.relkind = 'm' AND c.relispopulated))
            AND n.nspname !~ '^pg_'
            AND n.nspname <> 'information_schema' ORDER BY 1,2""").fetchall()
        for schema, table in tables:
            if db.execute(sql.SQL('SELECT EXISTS (SELECT 1 FROM {} LIMIT 1)').format(
                    sql.Identifier(schema, table))).fetchone()[0]:
                raise MeteringRefusal('restored user table is not empty')
    return {'schema_parity': True, 'zero_rows': True, 'user_tables_checked': len(tables),
            'objects_by_schema_kind': [list(row) for row in actual]}


def _provider(token):
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json',
               'User-Agent': 'doctorcre-neon-persistent-branch/1'}
    opener = urllib.request.build_opener(NoRedirect)

    def api(method, path, payload=None):
        if method == 'GET':
            reply = read(API + path, headers=headers, timeout=20, max_bytes=4_000_000)
            if reply.status != 200 or len(reply.body) > 4_000_000:
                raise _provider_error(reply.status, reply.body, token)
            return json.loads(reply.body)
        request = urllib.request.Request(API + path, method=method, headers=headers,
                                         data=json.dumps(payload).encode())
        try:
            with opener.open(request, timeout=30) as response:
                if response.status != 201:
                    raise _provider_error(response.status, response.read(64_000), token)
                return json.load(response)
        except urllib.error.HTTPError as exc:
            with exc:
                try:
                    body = exc.read(64_000)
                except OSError:
                    body = b''
            raise _provider_error(exc.code, body, token) from None
    return api


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('registration')
    args = parser.parse_args()
    try:
        policy = json.loads((ROOT / 'ops/config/platform-metering.v1.json').read_text())
        costs = json.loads((ROOT / 'ops/config/system-costs.v1.json').read_text())
        token = os.environ.get('NEON_API_KEY')
        if not token:
            raise MeteringRefusal('NEON_API_KEY must be supplied by the credential broker')
        state = Path.home() / '.cache/carr/neon-persistent-branch'
        state.mkdir(parents=True, exist_ok=True)
        with (state / 'create.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = create_registered_project(args.registration, policy, costs, _provider(token),
                                              state / 'creation-intent.json', datetime.now(timezone.utc))
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        # Provider exceptions and responses can contain credentials and connection URIs.
        if isinstance(exc, ProviderError):
            print(json.dumps(exc.receipt(), sort_keys=True))
        detail = str(exc) if isinstance(exc, MeteringRefusal) else type(exc).__name__
        print('persistent branch creation refused: ' + detail, file=sys.stderr)
        return 77


if __name__ == '__main__':
    raise SystemExit(main())
