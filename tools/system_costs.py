"""Read-only billing collection and the shared monthly display contract."""

import calendar
import json
import hashlib
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from statistics import median
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
from jev_spend_health import _loop_lock, _save_state, USAGE_LOG, FACTORY_USAGE_LOG, LOOP_STATE, ACTION
from credential_env import load_carr_tokens
from lib import record_call

SCHEMA = 'carr-system-costs.v1'
ROOT = Path(__file__).resolve().parents[1]



def amount(value):
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError('invalid amount')
    return result


def normalize(provider, response, rates=None):
    rows = []
    if provider == 'github':
        for item in response['usageItems']:
            rows.append({'day': date.fromisoformat(item['date'][:10]).isoformat(),
                         'usd': float(amount(item['netAmount'])), 'driver': item['sku']})
    elif provider == 'neon':
        for project in response['projects']:
            consumed = defaultdict(Decimal)
            for period in project['periods']:
                for frame in sorted(period['consumption'], key=lambda f: f['timeframe_start']):
                    for metric in frame['metrics']:
                        name = metric['metric_name']
                        unit = rates[name]
                        day = date.fromisoformat(frame['timeframe_start'][:10]).isoformat()
                        value = amount(metric['value'])
                        key = (day[:7], name)
                        included = amount(unit.get('included_units_per_month', 0))
                        charged = max(Decimal(0), consumed[key] + value - included) - max(Decimal(0), consumed[key] - included)
                        consumed[key] += value
                        rows.append({'day': day,
                                     'usd': float(charged * amount(unit['usd']) / amount(unit['units'])),
                                     'driver': name})
    elif provider in ('claude', 'openai'):
        for bucket in response['data']:
            day = (bucket.get('starting_at', '')[:10] if provider == 'claude' else
                   datetime.fromtimestamp(bucket['start_time'], timezone.utc).date().isoformat())
            for item in bucket['results']:
                money = item if provider == 'claude' else item['amount']
                if money['currency'].lower() != 'usd':
                    raise ValueError('non USD response')
                rows.append({'day': date.fromisoformat(day).isoformat(),
                             'usd': float(amount(money['amount'] if provider == 'claude' else money['value']) /
                                          (100 if provider == 'claude' else 1)),
                             'driver': 'API usage'})
    else:
        raise ValueError('unsupported provider')
    return {'state': 'ready', 'rows': rows}


def jev_usage(path, price, start=None, through=None):
    rows, unknown = [], 0
    with Path(path).open() as handle:
        for line in handle:
            try:
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError('invalid usage receipt')
                if item.get('cache_hit') is True:
                    continue
                if item.get('ok') is False and not (200 <= (item.get('http_status') or 0) < 300):
                    continue
                stamp = datetime.fromisoformat(item['ts'].replace('Z', '+00:00'))
                if stamp.tzinfo is None:
                    raise ValueError('missing timezone')
                day = stamp.astimezone(timezone.utc).date()
                if (start and day < start) or (through and day > through):
                    continue
                tokens = item['usage']['input_tokens']
                rows.append({'day': day.isoformat(),
                             'usd': float(amount(tokens) * amount(price) / 1000000),
                             'driver': str(item.get('caller') or item.get('call_site') or 'unattributed')})
            except (ValueError, KeyError, TypeError, ArithmeticError, AttributeError):
                unknown += 1
    return {'state': 'partial' if unknown else 'ready', 'reason': f'{unknown} unreadable/missing usage receipts' if unknown else None,
            'rows': rows, 'estimated': True}


def jev_sources(local, price, start, through, extra_logs=(FACTORY_USAGE_LOG,)):
    rows, reasons, seen = [], [], set()
    for path in (local, *extra_logs):
        path = Path(path).resolve()
        if path in seen:
            continue
        seen.add(path)
        try:
            source = jev_usage(path, price, start=start, through=through)
            rows.extend(source['rows'])
            if source.get('reason'):
                reasons.append(source['reason'])
        except (OSError, UnicodeError):
            reasons.append('Local/factory Jev ledger unavailable')
    # The current Worker verb exposes only today's aggregate. It cannot
    # establish the closed-day history needed for projections or clearing.
    reasons.append('Worker historical receipts unavailable; local/factory spend is a known lower bound')
    return {'state': 'partial', 'rows': rows, 'reason': '; '.join(reasons), 'estimated': True}


def summarize(config, sources, through, observed_at=None, month=None):
    month = month or through.strftime('%Y-%m')
    first = date.fromisoformat(month + '-01')
    days_in_month = calendar.monthrange(first.year, first.month)[1]
    elapsed = min(days_in_month, max(0, (through - first).days + 1))
    start = first - timedelta(days=1)
    start = start.replace(day=1)
    providers, alerts = [], []
    for provider, plan in config['providers'].items():
        source = sources.get(provider, {'state': 'unavailable', 'reason': 'billing source missing', 'rows': []})
        fixed = plan.get('monthly_usd')
        if fixed is None and plan.get('annual_usd') is not None:
            fixed = float(amount(plan['annual_usd']) / 12)
        daily = defaultdict(lambda: defaultdict(Decimal))
        invoices = defaultdict(Decimal)
        for row in source.get('rows', []):
            day = date.fromisoformat(row['day'])
            if start <= day <= through:
                if row.get('basis') == 'invoice':
                    invoices[day.strftime('%Y-%m')] += amount(row['usd'])
                else:
                    daily[day][str(row['driver'])] += amount(row['usd'])
        series = []
        day = start
        while day <= through:
            drivers = daily[day]
            if fixed is not None:
                drivers['Fixed subscription (daily accrual)'] += amount(fixed) / calendar.monthrange(day.year, day.month)[1]
            series.append({'day': day.isoformat(), 'usd': float(sum(drivers.values())),
                           'drivers': {k: float(v) for k, v in sorted(drivers.items())}})
            day += timedelta(days=1)
        for period, invoiced in invoices.items():
            period_rows = [r for r in series if r['day'].startswith(period)]
            accrued = sum(r['drivers'].get('Fixed subscription (daily accrual)', 0) for r in period_rows)
            # Invoice totals include the plan charge. Keep only the excess
            # over its known accrual, and never treat postings as a daily rate.
            remainder = max(0, float(invoiced) - accrued)
            if period_rows and remainder:
                period_rows[-1]['drivers']['Invoice charges above fixed accrual'] = remainder
                period_rows[-1]['usd'] += remainder
        current = [row for row in series if row['day'].startswith(month)]
        mtd = sum(row['usd'] for row in current)
        usage_mtd = sum(float(amount(r['usd'])) for r in source.get('rows', [])
                        if r.get('basis') != 'invoice' and r['day'].startswith(month) and r['day'] <= through.isoformat())
        projection = max(float(fixed or 0), float(invoices[month])) + (usage_mtd / elapsed * days_in_month if elapsed else 0)
        state = source['state']
        reasons = [source.get('reason')] if source.get('reason') else []
        if fixed is None:
            state = 'partial'
            reasons.append('subscription plan/price unconfirmed')
        elif fixed > 0 and state == 'unavailable':
            state = 'partial'
        sites = defaultdict(float)
        if provider == 'jev':
            for row in current:
                for site, usd in row['drivers'].items():
                    if site != 'Fixed subscription (daily accrual)':
                        sites[site] += usd
        view = {'provider': provider, 'label': plan['label'], 'plan': plan['plan'],
                'mtd_usd': round(mtd, 6), 'projection_usd': round(projection, 6),
                'budget_usd': plan.get('budget_usd'), 'state': state, 'reason': '; '.join(reasons) or None,
                'estimated': source.get('estimated', False), 'daily': series, 'call_sites': dict(sites)}
        providers.append(view)
        if series:
            today = series[-1]
            daily_warning = plan.get('daily_warning_usd')
            if daily_warning is not None and today['usd'] > float(amount(daily_warning)):
                driver = max(today['drivers'], key=today['drivers'].get)
                alerts.append({'provider': provider, 'driver': driver, 'kind': 'daily_warning',
                               'amount_usd': round(today['usd'], 6), 'threshold_usd': float(amount(daily_warning))})
            prior = [r['usd'] for r in series if through - timedelta(days=14) <= date.fromisoformat(r['day']) < through]
            if len(prior) == 14 and source['state'] == 'ready' and today['usd'] > 2 * median(prior):
                driver = max(today['drivers'], key=today['drivers'].get)
                alerts.append({'provider': provider, 'driver': driver, 'kind': 'daily_spike',
                               'amount_usd': round(today['usd'], 6), 'threshold_usd': round(2 * median(prior), 6)})
        budget = plan.get('budget_usd')
        if budget is not None and projection > float(amount(budget)):
            drivers = defaultdict(float)
            for row in current:
                for driver, usd in row['drivers'].items():
                    drivers[driver] += usd
            alerts.append({'provider': provider, 'driver': max(drivers, key=drivers.get) if drivers else 'fixed subscription',
                           'kind': 'budget_projection', 'amount_usd': round(projection, 6), 'threshold_usd': budget})
    months = []
    for period in sorted({r['day'][:7] for p in providers for r in p['daily']} | {month}):
        totals = {p['provider']: round(sum(r['usd'] for r in p['daily'] if r['day'].startswith(period)), 6) for p in providers}
        months.append({'month': period, 'usd': round(sum(totals.values()), 6), 'providers': totals})
    total_projection = sum(p['projection_usd'] for p in providers)
    if total_projection > config['budget_usd']:
        biggest = max(providers, key=lambda p: p['projection_usd'])
        alerts.append({'provider': 'system', 'driver': biggest['label'], 'kind': 'budget_projection',
                       'amount_usd': round(total_projection, 6), 'threshold_usd': config['budget_usd']})
    return {'schema': SCHEMA, 'month': month, 'through': through.isoformat(),
            'observed_at': observed_at or datetime.now(timezone.utc).isoformat(),
            'state': 'ready' if all(p['state'] == 'ready' for p in providers) else 'partial',
            'providers': providers, 'months': months, 'alerts': alerts, 'action': ACTION,
            'budget_usd': config['budget_usd'], 'projection_usd': round(total_projection, 6)}


def unavailable(reason='source_unavailable'):
    return {'schema': SCHEMA, 'state': 'unavailable', 'reason': reason,
            'providers': [], 'months': [], 'alerts': [], 'action': ACTION}


def load_snapshot(path=ROOT / 'out/system-costs.json', now=None):
    try:
        data = json.loads(Path(path).read_text())
        now = now or datetime.now(timezone.utc)
        stamp = datetime.fromisoformat(data['observed_at'].replace('Z', '+00:00'))
        if data['schema'] != SCHEMA or stamp.tzinfo is None or not timedelta(0) <= now - stamp <= timedelta(hours=36):
            return unavailable()
        if data['state'] not in ('ready', 'partial'):
            return unavailable()
        date.fromisoformat(data['through'])
        date.fromisoformat(data['month'] + '-01')
        result = {key: data[key] for key in ('schema', 'state', 'month', 'through', 'observed_at')}
        result['action'] = ACTION
        if not isinstance(data['providers'], list) or len(data['providers']) > 30 or len(data['months']) > 24 or len(data['alerts']) > 100:
            raise ValueError('invalid snapshot size')
        def text(value):
            if not isinstance(value, str) or not 0 < len(value) <= 500:
                raise ValueError('invalid display text')
            return value
        result['providers'] = []
        for row in data['providers']:
            clean = {key: text(row[key]) for key in ('provider', 'label', 'plan', 'state')}
            clean['reason'] = text(row['reason']) if row.get('reason') is not None else None
            clean['estimated'] = row.get('estimated') is True
            if clean['state'] not in ('ready', 'partial', 'unavailable'):
                raise ValueError('invalid provider state')
            for key in ('mtd_usd', 'projection_usd', 'budget_usd'):
                clean[key] = float(amount(row[key])) if row.get(key) is not None else None
            clean['daily'] = []
            if len(row['daily']) > 62 or len(row['call_sites']) > 500:
                raise ValueError('invalid daily size')
            for day in row['daily']:
                if not isinstance(day['drivers'], dict) or len(day['drivers']) > 500:
                    raise ValueError('invalid drivers size')
                clean['daily'].append({'day': date.fromisoformat(day['day']).isoformat(), 'usd': float(amount(day['usd'])),
                                       'drivers': {text(k): float(amount(v)) for k, v in day['drivers'].items()}})
            clean['call_sites'] = {text(k): float(amount(v)) for k, v in row['call_sites'].items()}
            result['providers'].append(clean)
        result['months'] = [{'month': date.fromisoformat(row['month'] + '-01').strftime('%Y-%m'),
                             'usd': float(amount(row['usd'])), 'providers': {text(k): float(amount(v)) for k, v in row['providers'].items()}}
                            for row in data['months']]
        result['alerts'] = [{**{key: text(row[key]) for key in ('provider', 'driver', 'kind')},
                             'amount_usd': float(amount(row['amount_usd'])), 'threshold_usd': float(amount(row['threshold_usd']))}
                            for row in data['alerts']]
        for key in ('projection_usd', 'budget_usd'):
            if key in data:
                result[key] = float(amount(data[key]))
        return result
    except (OSError, ValueError, KeyError, TypeError, ArithmeticError, AttributeError):
        return unavailable()


def report_order(report):
    stamp = datetime.fromisoformat(report.get('observed_at', report['through'] + 'T00:00:00+00:00').replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('report observation requires timezone')
    return date.fromisoformat(report['through']), stamp.astimezone(timezone.utc)


def cost_verb(name, payload):
    """A confirmed reply, or a named refusal marked ok:false; anything uncertain raises."""
    result = record_call.call_verb(name, payload, timeout=35)
    answer = result.reply
    if result.kind == record_call.REFUSED and isinstance(answer, dict) \
            and isinstance(answer.get('error'), str) and answer['error']:
        return {**answer, 'ok': False}
    if not result.ok or not isinstance(answer, dict):
        raise RuntimeError(result.describe())
    return answer


def reconcile(report, path, run_verb=cost_verb, *, legacy_path=None):
    """Serialize durable intents; uncertain outcomes replay, refusals re-decide."""
    with _loop_lock(path):
        try:
            state = json.loads(Path(path).read_text())
        except FileNotFoundError:
            state = {'open': {}, 'episodes': {}, 'pending': None, 'revision': 0}
        state.setdefault('revision', 0)
        if legacy_path is not None and not state.get('legacy_imported'):
            with _loop_lock(legacy_path):
                try:
                    legacy = json.loads(Path(legacy_path).read_text())
                except FileNotFoundError:
                    legacy = {}
                if legacy.get('loop_id') and 'jev' not in state['open']:
                    state['open']['jev'] = {'loop_id': legacy['loop_id'], 'fingerprint': '',
                                            'through': legacy.get('day', report['through'])}
                    state['episodes'].setdefault('jev', 1)
                state['legacy_imported'] = True
                _save_state(path, state)

        def complete_pending():
            pending = state.get('pending')
            if not pending:
                return
            answer = run_verb(pending['name'], pending['payload'])
            if answer.get('ok') is not True:
                if answer.get('error') not in ('version_conflict', 'loop_not_open', 'key_reuse'):
                    raise RuntimeError('loop action unconfirmed')
                # A structured refusal confirms no write. A timeout or lost
                # response leaves this intent intact for exact-key replay.
                state['pending'] = None
                state.setdefault('refusals', {})[pending['provider']] = str(answer['error'])
                _save_state(path, state)
                return
            provider = pending['provider']
            if pending['name'] == 'close-loop':
                state['open'].pop(provider, None)
            else:
                after = pending['after']
                if pending['name'] == 'add-loop':
                    if not answer.get('loop_id'):
                        raise RuntimeError('loop id missing')
                    after['loop_id'] = answer['loop_id']
                state['open'][provider] = after
            state.get('refusals', {}).pop(provider, None)
            state['pending'] = None
            _save_state(path, state)

        def remote_current(provider):
            current = state['open'].get(provider)
            if not current:
                return None, None
            answer = run_verb('read-loop', {'loop_id': current['loop_id']})
            row = answer.get('loop', answer)
            if row.get('loop_id') != current['loop_id'] or type(row.get('version')) is not int:
                raise RuntimeError('read-loop returned no matching version')
            if row.get('status', 'open') != 'open':
                state['open'].pop(provider, None)
                state.get('refusals', {}).pop(provider, None)
                _save_state(path, state)
                return None, None
            return current, row['version']

        def write(name, payload, provider, after):
            state['revision'] += 1
            manifest = json.dumps([name, provider, state['revision'], payload], sort_keys=True)
            payload['idempotency_key'] = hashlib.sha256(manifest.encode()).hexdigest()
            state['pending'] = {'name': name, 'payload': payload, 'provider': provider, 'after': after}
            _save_state(path, state)
            complete_pending()

        complete_pending()
        highwater = state.get('highwater')
        if highwater and report_order(report) < report_order(highwater):
            return len(state['open'])
        state['highwater'] = {key: report[key] for key in ('through', 'observed_at') if key in report}
        _save_state(path, state)
        grouped = defaultdict(list)
        for alert in report.get('alerts', []):
            grouped[alert['provider']].append(alert)
        for provider, alerts in grouped.items():
            body = f"System cost spike for {provider} through {report['through']} UTC. " + '; '.join(
                f"{a['kind']}: ${a['amount_usd']:.3f} > ${a['threshold_usd']:.3f}; driver {a['driver']}" for a in alerts)
            body += '. ' + ACTION
            fingerprint = hashlib.sha256(body.encode()).hexdigest()
            current, version = remote_current(provider)
            if current and current['fingerprint'] == fingerprint:
                continue
            if not current:
                state['episodes'][provider] = state['episodes'].get(provider, 0) + 1
            payload = {'body': body}
            if current:
                payload.update(loop_id=current['loop_id'], base_version=version)
                name = 'update-loop'
            else:
                payload.update(kind='open_loop', domain='system', owner='claude', marker='none',
                               blocker='other_lane', blocker_detail=f'Finance orchestrator must reconcile {provider} billing driver with its owning workload lane')
                name = 'add-loop'
            after = {**(current or {}), 'fingerprint': fingerprint, 'through': report['through']}
            write(name, payload, provider, after)
        for provider, current in list(state['open'].items()):
            if provider in grouped or report['through'] <= current['through']:
                continue
            ready = report['state'] == 'ready' if provider == 'system' else any(
                p['provider'] == provider and p['state'] == 'ready' for p in report['providers'])
            if not ready:
                continue
            current, version = remote_current(provider)
            if current:
                write('close-loop', {'loop_id': current['loop_id'], 'base_version': version,
                                     'resolution': 'done', 'outcome': f"Auto-clear: {provider} complete UTC day {report['through']} within daily and projection thresholds."},
                      provider, {})
        return len(state['open'])


def health_row(report, loop_result='not reconciled'):
    status = 'WARN' if report.get('alerts') else 'OK' if report['state'] == 'ready' else 'UNKNOWN'
    total = sum(p.get('mtd_usd') or 0 for p in report.get('providers', []))
    return f"{status} system costs · known MTD ${total:.2f}; {report['state']} coverage; loops {loop_result} · {ACTION}"


TOKEN_NAMES = {'cloudflare': 'CLOUDFLARE_BILLING_READ_TOKEN', 'neon': 'NEON_API_KEY',
               'claude': 'ANTHROPIC_ADMIN_API_KEY', 'openai': 'OPENAI_ADMIN_KEY',
               'github': 'GITHUB_BILLING_READ_TOKEN', 'grok': 'XAI_MANAGEMENT_READ_TOKEN',
               'domains': 'DOMAIN_BILLING_READ_TOKEN'}


def read_tokens():
    names = set(TOKEN_NAMES.values())
    tokens = {k: os.environ[k] for k in names if os.environ.get(k)}
    for filename in ('tokens.env', 'db.env'):
        loaded = load_carr_tokens(names, path=Path.home() / '.config/carr' / filename)
        for key, value in loaded.items():
            if value and key not in tokens:
                tokens[key] = value
    return tokens


def get_json(url, headers):
    request = urllib.request.Request(url, headers=headers, method='GET')
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def collect(config, tokens, fetch=get_json, now=None, jev_path=None):
    now = now or datetime.now(timezone.utc)
    through = now.date() - timedelta(days=1)
    first = now.date().replace(day=1)
    start = (first - timedelta(days=1)).replace(day=1)
    start_iso, end_iso = start.isoformat() + 'T00:00:00Z', now.date().isoformat() + 'T00:00:00Z'
    sources = {}
    for provider in config['providers']:
        name = TOKEN_NAMES.get(provider, 'local usage ledger')
        token = tokens.get(name)
        source = {'state': 'unavailable', 'reason': f'Missing {name}', 'rows': []}
        try:
            if provider == 'jev':
                guard = json.loads((ROOT / 'ops/config/jev-cost-guard.v1.json').read_text())
                config = {**config, 'providers': {**config['providers'], 'jev': {
                    **config['providers']['jev'], 'daily_warning_usd': guard['daily_warning_usd']}}}
                source = jev_sources(jev_path or USAGE_LOG, guard['price_usd_per_million_input_tokens'], start, through)
            elif provider == 'neon' and token:
                cfg = config['neon']
                neon_start = max(start, now.date() - timedelta(days=60))
                base = {'from': neon_start.isoformat() + 'T00:00:00Z', 'to': end_iso, 'granularity': 'daily', 'org_id': cfg['org_id'],
                        'metrics': ','.join(cfg['rates']), 'limit': 100}
                projects, seen, cursor = [], set(), None
                for _ in range(100):
                    query = {**base, **({'cursor': cursor} if cursor else {})}
                    response = fetch('https://console.neon.tech/api/v2/consumption_history/v2/projects?' + urllib.parse.urlencode(query),
                                     {'Authorization': f'Bearer {token}'})
                    projects.extend(response['projects'])
                    pagination = response.get('pagination', {})
                    if not isinstance(pagination, dict):
                        raise ValueError('invalid pagination')
                    cursor = pagination.get('cursor')
                    if not cursor:
                        break
                    if cursor in seen:
                        raise ValueError('repeated pagination cursor')
                    seen.add(cursor)
                else:
                    raise ValueError('pagination limit')
                if cfg.get('plan') and any(period.get('period_plan') != cfg['plan']
                                          for project in projects for period in project['periods']):
                    sources[provider] = {'state': 'unavailable', 'reason': 'Neon plan changed; configured meter rates require reconciliation', 'rows': []}
                    continue
                source = normalize('neon', {'projects': projects}, cfg['rates'])
                source.update(estimated=True, reason=cfg.get('estimate_note'))
                if neon_start > start:
                    source.update(state='partial', reason='Older daily history outside Neon 60-day retention; known retained costs only')
            elif provider == 'github' and token:
                combined = []
                for period in (start, first):
                    response = fetch(f"https://api.github.com/users/{config.get('github_user', 'jbookout')}/settings/billing/usage?year={period.year}&month={period.month}",
                                     {'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2026-03-10'})
                    combined.extend(response['usageItems'])
                source = normalize('github', {'usageItems': combined})
            elif provider in ('claude', 'openai') and token:
                params = {'starting_at': start_iso, 'ending_at': end_iso} if provider == 'claude' else {
                    'start_time': int(datetime.combine(start, datetime.min.time(), timezone.utc).timestamp()),
                    'end_time': int(datetime.combine(now.date(), datetime.min.time(), timezone.utc).timestamp())}
                base_url = ('https://api.anthropic.com/v1/organizations/cost_report' if provider == 'claude' else
                            'https://api.openai.com/v1/organization/costs')
                headers = {'x-api-key': token, 'anthropic-version': '2023-06-01'} if provider == 'claude' else {'Authorization': f'Bearer {token}'}
                buckets, seen, page = [], set(), None
                for _ in range(100):
                    response = fetch(base_url + '?' + urllib.parse.urlencode({**params, **({'page': page} if page else {})}), headers)
                    buckets.extend(response['data'])
                    if not response.get('has_more'):
                        break
                    page = response['next_page']
                    if not page or page in seen:
                        raise ValueError('invalid page')
                    seen.add(page)
                else:
                    raise ValueError('pagination limit')
                source = normalize(provider, {'data': buckets})
            elif provider == 'cloudflare' and token:
                account = config.get('cloudflare_account_id')
                if not account:
                    raise ValueError('cloudflare_account_id missing')
                response = fetch(f'https://api.cloudflare.com/client/v4/accounts/{account}/billing/history', {'Authorization': f'Bearer {token}'})
                if response.get('success') is not True:
                    raise ValueError('billing response unsuccessful')
                source = {'state': 'partial', 'reason': 'Invoice postings only; daily usage attribution unavailable', 'rows': []}
                for item in response['result']:
                    if item.get('currency', '').lower() != 'usd':
                        raise ValueError('non USD billing')
                    if item.get('type') == 'invoice' and item.get('action') in ('charge', 'invoice'):
                        source['rows'].append({'day': item['occurred_at'][:10], 'usd': float(amount(item['amount'])), 'driver': 'Invoice charge', 'basis': 'invoice'})
            elif provider in ('grok', 'domains'):
                source['reason'] = (f'{name} present but billing contract/reader unconfigured' if token else f'Missing {name}')
                source['reason'] += '; subscription/renewal totals supplied by committed config'
            elif provider == 'github' and not token:
                source['reason'] = f'Missing {name} with Plan:read; repo-scoped gh token is not billing authority'
        except urllib.error.HTTPError as exc:
            source = {'state': 'unavailable', 'reason': f'{name} billing HTTP {exc.code}', 'rows': []}
        except (OSError, ValueError, KeyError, TypeError, ArithmeticError, AttributeError):
            source = {'state': 'unavailable', 'reason': f'{provider} billing source unreadable or invalid', 'rows': []}
        sources[provider] = source
    return summarize(config, sources, through, observed_at=now.isoformat(), month=first.strftime('%Y-%m'))


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'ops/config/system-costs.v1.json')
    parser.add_argument('--jev-log', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'out/system-costs.json')
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--alerts', action='store_true')
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text())
    report = collect(config, read_tokens(), jev_path=args.jev_log)
    coordinator = ROOT / 'out/system-cost-collector.json'
    with _loop_lock(coordinator):
        try:
            previous = json.loads(coordinator.read_text())
        except FileNotFoundError:
            previous = None
        candidates = [previous] if previous else []
        for path in {args.output, ROOT / 'out/system-costs.json'}:
            snapshot = load_snapshot(path)
            if snapshot['state'] != 'unavailable':
                candidates.append(snapshot)
        previous = max(candidates, key=report_order) if candidates else None
        if previous and report_order(report) < report_order(previous):
            _save_state(coordinator, {key: previous[key] for key in ('through', 'observed_at')})
            print('Ignored stale system cost collection; newer evidence retained')
            return 0
        _save_state(args.output, report)
        _save_state(coordinator, {key: report[key] for key in ('through', 'observed_at')})
        loops_path = ROOT / 'out/system-cost-loops.json'
        loop_result = reconcile(report, loops_path, legacy_path=LOOP_STATE) if args.alerts else 'disabled'
        refusals = json.loads(loops_path.read_text()).get('refusals', {}) if args.alerts and loops_path.exists() else {}
        if refusals:
            loop_result = f"{loop_result} open; refused providers {', '.join(sorted(refusals))}"
        if args.publish:
            import progress_board
            published_costs = load_snapshot(args.output)
            if not progress_board.state_path('system-costs').exists():
                try:
                    progress_board.command_init(argparse.Namespace(project='system-costs', title='System costs', costs=published_costs))
                except SystemExit as exc:
                    if str(exc) != 'board already exists: system-costs' or not progress_board.state_path('system-costs').exists():
                        raise
                    progress_board.publish_board('system-costs', costs=published_costs)
            else:
                progress_board.publish_board('system-costs', costs=published_costs)
    print(health_row(report, loop_result))
    return 0 if report['state'] == 'ready' and not refusals else 1
