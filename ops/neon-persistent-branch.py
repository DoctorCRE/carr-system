#!/usr/bin/env python3
"""Create the policy-registered schema-only Neon branch after live budget admission."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
import re
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


def create_registered_branch(name, policy, costs, api, intent, now):
    registration = policy['execution_gates'][GATE]['registration']
    if name != registration['name'] or costs['neon']['org_id'] != registration['org_id']:
        raise MeteringRefusal('unknown persistent registration or billing organization')
    if intent.exists():
        raise MeteringRefusal('creation intent exists; reconcile provider state before recovery')
    project = registration['project_id']
    project_path = f'/projects/{project}'
    if api('GET', project_path)['project']['org_id'] != registration['org_id']:
        raise MeteringRefusal('project organization differs from registration')
    inventory = api('GET', project_path + '/branches')
    if inventory.get('pagination', {}).get('cursor'):
        raise MeteringRefusal('branch inventory is incomplete')
    branches = inventory['branches']
    parents = [b for b in branches if b['id'] == registration['parent_id']]
    if len(parents) != 1 or parents[0]['name'] != registration['parent_name']:
        raise MeteringRefusal('registered production source is absent or renamed')
    existing = sum(b['name'] == name for b in branches)
    spending = api('GET', f"/organizations/{registration['org_id']}/billing/spending_limit")
    cents = spending['spending_limit_cents']
    if isinstance(cents, bool) or not isinstance(cents, int) or cents < 0:
        raise MeteringRefusal('organization spending limit is not configured')
    monthly = _monthly_costs(api, costs, now, project)
    request = {'registration': registration, 'existing_registered_branches': existing,
               'monthly_projection_usd': monthly['projection_usd'],
               'organization_spending_limit_usd': cents / 100,
               'meter_month': now.strftime('%Y-%m')}
    decision = authorize_metered_execution(policy, GATE, request, today=now.date())
    # Launch's default encodes the registered five-minute suspension as zero.
    payload = {'branch': {k: registration[k] for k in ('name', 'parent_id', 'init_source')},
               'endpoints': [{**registration['endpoint'], 'suspend_timeout_seconds': 0}]}
    # Keep the intent even on timeout: POST may have succeeded without a reply.
    with intent.open('x') as handle:
        json.dump({'registration': name, 'requested_at': now.isoformat()}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        result = api('POST', f'/projects/{project}/branches', payload)
    except ProviderError as exc:
        if 400 <= exc.status < 500 and exc.status != 408:
            try:
                inventory = api('GET', project_path + '/branches')
                absent = (not inventory.get('pagination', {}).get('cursor')
                          and isinstance(inventory.get('branches'), list)
                          and all(isinstance(b, dict) and isinstance(b.get('name'), str)
                                  and b['name'] != name for b in inventory['branches']))
            except Exception:
                absent = False
            if absent:
                intent.unlink()
                exc.creation_outcome = 'not_created'
        raise
    branch, endpoints = result['branch'], result['endpoints']
    if (not isinstance(branch.get('id'), str) or not branch['id']
            or branch['id'] == registration['parent_id'] or branch['name'] != name
            or branch['init_source'] != registration['init_source'] or len(endpoints) != 1
            or endpoints[0]['branch_id'] != branch['id']
            or any(endpoints[0].get(k) != v for k, v in registration['endpoint'].items())):
        raise MeteringRefusal('provider result differs from registration; reconcile creation intent')
    return {'gate': decision['gate'], 'branch_id': branch['id'], 'name': name,
            'monthly_cost_ceiling_usd': registration['monthly_cost_ceiling_usd'],
            'monthly_spend_usd': monthly['mtd_usd'],
            'monthly_projection_usd': request['monthly_projection_usd'], 'creation_confirmed': True}


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
            result = create_registered_branch(args.registration, policy, costs, _provider(token),
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
