#!/usr/bin/env python3
"""Cloudflare spend guard: a deterministic kill switch, because Cloudflare has none.

WHY. Budget alerts "are informational only. They do not pause or cap usage"
(https://developers.cloudflare.com/billing/manage/budget-alerts/). This job
reads the account's current-period billable usage, compares it with the
thresholds in ops/config/cloudflare-spend-guard.v1.json, and acts:

  OK       nothing to do.
  WARN     an included allowance is at 50% or more, or a service with usage
           has no allowance mapping yet. Report only.
  STOP     any metered (beyond-included) usage, or an allowance at 80%.
           Disables the workers.dev subdomain of the STAGING Workers and
           writes a STOPPED receipt that E2E dispatch must pass. Production
           Workers are report-only unless stop_production_workers is true.
  UNKNOWN  usage could not be read. Writes the STOPPED receipt for E2E and
           changes no Worker.

A STOP hold is sticky: only `restore`, run separately by a human, lifts it and
re-enables exactly the Workers this guard disabled, to the state each had
before. An UNKNOWN hold clears on the next readable run. An unreadable receipt
is moved aside (receipt.corrupt.<time>.json) and holds like STOP, because it
may carry the restore list.

SOURCES (read raw before coding, 2026-10-08):
  usage    GET /accounts/{id}/billable-usage/info and /billable-usage
           (Version 1, Alpha). Same data as the Billable Usage dashboard,
           which "comes from the same system that generates your monthly
           invoice". https://github.com/cloudflare/api-schemas (openapi.json),
           https://developers.cloudflare.com/billing/manage/billable-usage/
  action   GET/POST /accounts/{id}/workers/scripts/{script}/subdomain,
           body {"enabled", "previews_enabled"}; token group Workers Scripts
           Write. Same schema file.

No model is called. Thresholds and targets come only from the config file;
there are no environment or command-line overrides. The API token is read by
this script from ~/.config/carr/tokens.env and is never printed.
"""

from __future__ import annotations

import argparse
import calendar
import json
import math
import os
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / 'ops' / 'config' / 'cloudflare-spend-guard.v1.json'
API = 'https://api.cloudflare.com/client/v4'
RECEIPT_SCHEMA = 'carr-cloudflare-spend-guard-receipt.v1'
LEVELS = ('OK', 'WARN', 'STOP', 'UNKNOWN')
EXIT_CODES = {'OK': 0, 'WARN': 1, 'STOP': 2, 'UNKNOWN': 3}
SCRIPT_CHARS = set('abcdefghijklmnopqrstuvwxyz0123456789-_')


class UsageUnavailable(Exception):
    """Usage could not be read or did not have the documented shape."""


class ApiError(Exception):
    """A Cloudflare call failed. The message never carries the token."""


# ── configuration ────────────────────────────────────────────────────────────

def validate_config(cfg: dict) -> dict:
    def need(condition, message):
        if not condition:
            raise ValueError(f'cloudflare spend guard config: {message}')

    account = cfg.get('account_id')
    need(isinstance(account, str) and len(account) == 32 and all(c in '0123456789abcdef' for c in account),
         'account_id must be a 32-character hex account tag')
    need(isinstance(cfg.get('token_name'), str) and cfg['token_name'], 'token_name required')
    need(isinstance(cfg.get('state_dir'), str) and cfg['state_dir'], 'state_dir required')
    for key in ('request_timeout_seconds', 'max_usage_age_hours', 'e2e_max_evaluation_age_hours'):
        need(_number(cfg.get(key)) and cfg[key] > 0, f'{key} must be a positive number')
    t = cfg.get('thresholds') or {}
    need(set(t) == {'warn_allowance_fraction', 'stop_allowance_fraction', 'stop_metered_usd_above'},
         'thresholds must name exactly warn_allowance_fraction, stop_allowance_fraction, stop_metered_usd_above')
    need(all(_number(v) for v in t.values()), 'thresholds must be numbers')
    need(0 < t['warn_allowance_fraction'] < t['stop_allowance_fraction'] <= 1,
         'need 0 < warn_allowance_fraction < stop_allowance_fraction <= 1')
    need(t['stop_metered_usd_above'] >= 0, 'stop_metered_usd_above must be >= 0')
    for name in ('staging_workers', 'production_workers'):
        need(isinstance(cfg.get(name), list)
             and all(isinstance(w, str) and w and set(w) <= SCRIPT_CHARS for w in cfg[name]),
             f'{name} must be a list of Worker script names')
        need(len(set(cfg[name])) == len(cfg[name]), f'{name} has duplicates')
    staging: list[str] = cfg['staging_workers']
    production: list[str] = cfg['production_workers']
    need(bool(staging), 'staging_workers must name at least one Worker')
    need(not set(staging) & set(production), 'a Worker cannot be both staging and production')
    need(cfg.get('stop_production_workers') in (True, False), 'stop_production_workers must be true or false')
    keys: set[str] = set()
    names: set[str] = set()
    for entry in cfg.get('allowances') or []:
        need(isinstance(entry.get('key'), str) and entry['key'] not in keys, 'allowance keys must be unique strings')
        keys.add(entry['key'])
        need(_number(entry.get('included_per_period')) and entry['included_per_period'] > 0,
             f"allowance {entry['key']} needs a positive included_per_period")
        need(isinstance(entry.get('consumed_unit'), str), f"allowance {entry['key']} needs consumed_unit (may be empty)")
        services = entry.get('service_names')
        need(isinstance(services, list) and all(isinstance(s, str) and s for s in services),
             f"allowance {entry['key']} service_names must be a list of strings")
        need(not names & set(services), f"allowance {entry['key']} repeats a service name")
        names |= set(services)
    return cfg


def load_config(path: Path = CONFIG_PATH) -> dict:
    return validate_config(json.loads(Path(path).read_text()))


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _state_dir(cfg: dict, state_dir) -> Path:
    return Path(state_dir) if state_dir is not None else Path(cfg['state_dir']).expanduser()


# ── Cloudflare calls ─────────────────────────────────────────────────────────

def urllib_http(method, url, headers, body, timeout):
    """The production transport: (status, parsed JSON). Network errors raise."""
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers = {**headers, 'Content-Type': 'application/json'}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    try:
        return status, json.loads(raw)
    except ValueError:
        raise UsageUnavailable(f'{method} response body is not JSON (HTTP {status})') from None


def _api(http, cfg, token, method, path, body=None):
    url = f"{API}/accounts/{cfg['account_id']}{path}"
    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
    status, payload = http(method, url, headers, body, cfg['request_timeout_seconds'])
    if status != 200 or not isinstance(payload, dict) or payload.get('success') is not True:
        detail = ''
        if isinstance(payload, dict) and isinstance(payload.get('errors'), list) and payload['errors']:
            first = payload['errors'][0]
            if isinstance(first, dict):
                detail = f" error {first.get('code')}: {str(first.get('message'))[:160]}"
        raise ApiError(f'{method} {path} returned HTTP {status}{detail}')
    return payload.get('result')


def read_token(cfg: dict):
    """The guard's own token, from tokens.env. None when absent or unsafe."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from credential_env import TokensFilePermissionError, load_carr_tokens
    try:
        return load_carr_tokens([cfg['token_name']]).get(cfg['token_name']) or None
    except TokensFilePermissionError:
        return None


# ── reading usage ────────────────────────────────────────────────────────────

def _time(value, field):
    if not isinstance(value, str):
        raise UsageUnavailable(f'{field} missing')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise UsageUnavailable(f'{field} is not a timestamp') from None
    if parsed.tzinfo is None:
        raise UsageUnavailable(f'{field} has no timezone')
    return parsed.astimezone(timezone.utc)


def _quantity(record, field, *, required=True):
    value = record.get(field)
    if value is None and not required:
        return 0.0
    if not _number(value) or value < 0:
        raise UsageUnavailable(f'{field} is not a non-negative number')
    return float(value)


def _period_start(anchor: datetime, now: datetime) -> datetime:
    """Most recent billing-cycle start at or before now (anchor day clamped to month length)."""
    year, month = now.year, now.month
    for _ in range(2):
        day = min(anchor.day, calendar.monthrange(year, month)[1])
        start = anchor.replace(year=year, month=month, day=day)
        if start <= now:
            return start
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    raise UsageUnavailable('billing cycle anchor is in the future')


def read_usage(cfg: dict, token: str, http, now: datetime) -> dict:
    info = _api(http, cfg, token, 'GET', '/billable-usage/info')
    if not isinstance(info, dict) or info.get('covered') is not True:
        raise UsageUnavailable('account is not covered by the Billable Usage API (Pay-as-you-go only)')
    subscriptions = info.get('subscriptions')
    if not isinstance(subscriptions, list):
        raise UsageUnavailable('subscriptions missing')
    active = [s for s in subscriptions if isinstance(s, dict)
              and (s.get('end_timestamp') is None or _time(s['end_timestamp'], 'end_timestamp') > now)]
    if not active:
        raise UsageUnavailable('no active usage-based subscription')
    cycle_start = max(_period_start(_time(s.get('billing_cycle_anchor_timestamp'), 'billing_cycle_anchor_timestamp'),
                                    now) for s in active)

    rows = _api(http, cfg, token, 'GET', '/billable-usage')
    if not isinstance(rows, list):
        raise UsageUnavailable('usage result is not a list')
    services: dict[str, dict] = {}
    periods: set[datetime] = set()
    newest: datetime | None = None
    for record in rows:
        if not isinstance(record, dict):
            raise UsageUnavailable('usage row is not an object')
        if record.get('BillingCurrency') != 'USD':
            raise UsageUnavailable(f"billing currency {record.get('BillingCurrency')!r} is not USD")
        name = record.get('ServiceName')
        if not isinstance(name, str) or not name:
            raise UsageUnavailable('ServiceName missing')
        cost_field = 'BilledCost' if record.get('BilledCost') is not None else 'ContractedCost'
        cost = _quantity(record, cost_field)
        consumed = _quantity(record, 'ConsumedQuantity')
        beyond = max(_quantity(record, 'CumulatedPricingQuantity', required=False),
                     _quantity(record, 'PricingQuantity', required=False))
        periods.add(_time(record.get('BillingPeriodStart'), 'BillingPeriodStart'))
        end = _time(record.get('ChargePeriodEnd'), 'ChargePeriodEnd')
        newest = end if newest is None or end > newest else newest
        unit = record.get('ConsumedUnit') or ''
        if not isinstance(unit, str):
            raise UsageUnavailable('ConsumedUnit is not a string')
        service = services.setdefault(name, {'consumed': 0.0, 'usd': 0.0, 'beyond_included': 0.0, 'units': set(),
                                             'family': str(record.get('ServiceFamilyName') or '')})
        service['consumed'] += consumed
        service['usd'] += cost
        service['beyond_included'] = max(service['beyond_included'], beyond)
        service['units'].add(unit)
    if len(periods) > 1:
        raise UsageUnavailable('usage rows span more than one billing period')
    max_age = timedelta(hours=cfg['max_usage_age_hours'])
    if newest is None and now - cycle_start > max_age:
        raise UsageUnavailable(f"no usage rows {round((now - cycle_start).total_seconds() / 3600)}h into the billing period")
    if newest is not None and now - newest > max_age:
        raise UsageUnavailable(f"newest usage ends {newest.strftime('%Y-%m-%dT%H:%MZ')}, older than {cfg['max_usage_age_hours']}h")
    return {'period_start': (periods.pop() if periods else cycle_start).strftime('%Y-%m-%dT%H:%MZ'),
            'data_through': newest.strftime('%Y-%m-%dT%H:%MZ') if newest else None,
            'rows': len(rows), 'services': services}


# ── deciding ─────────────────────────────────────────────────────────────────

def _usd(value: float) -> str:
    return f'${value:,.2f}' if value >= 0.01 or value == 0 else f'${value:.4f}'


def evaluate(cfg: dict, usage: dict) -> dict:
    t = cfg['thresholds']
    services = usage['services']
    level, reasons = 'OK', []

    def raise_to(new):
        nonlocal level
        level = max(level, new, key=LEVELS.index)

    metered = sum(s['usd'] for s in services.values())
    if metered > t['stop_metered_usd_above']:
        drivers = ', '.join(f"{name} {_usd(s['usd'])}" for name, s in sorted(services.items()) if s['usd'] > 0)
        reasons.append(f'metered {_usd(metered)} beyond included ({drivers})')
        raise_to('STOP')
    for name, s in sorted(services.items()):
        if s['beyond_included'] > 0 and s['usd'] == 0:
            reasons.append(f'{name} has beyond-included quantity not yet priced')
            raise_to('STOP')

    mapped, fractions = set(), {}
    for entry in cfg['allowances']:
        rows = [services[n] for n in entry['service_names'] if n in services]
        mapped |= set(entry['service_names'])
        for name in entry['service_names']:
            if name in services and services[name]['units'] - {entry['consumed_unit']}:
                raise UsageUnavailable(f"{name} reports unit {sorted(services[name]['units'])}, "
                                       f"config expects {entry['consumed_unit']!r}")
        fraction = sum(r['consumed'] for r in rows) / entry['included_per_period']
        fractions[entry['key']] = fraction
        label = f"{entry['key']} at {fraction:.0%} of {entry['included_per_period']:,}"
        if fraction >= t['stop_allowance_fraction']:
            reasons.append(label)
            raise_to('STOP')
        elif fraction >= t['warn_allowance_fraction']:
            reasons.append(label)
            raise_to('WARN')
    for name, s in sorted(services.items()):
        if name not in mapped and s['consumed'] > 0:
            reasons.append(f"unmapped service {name} ({s['family'] or 'no family'}) has usage; add its allowance")
            raise_to('WARN')
    peak = max(fractions.items(), key=lambda kv: kv[1]) if fractions else None
    return {'verdict': level, 'reasons': reasons, 'metered_usd': round(metered, 6),
            'peak_allowance': {'key': peak[0], 'fraction': round(peak[1], 6)} if peak else None}


# ── receipt ──────────────────────────────────────────────────────────────────

def receipt_path(state_dir) -> Path:
    return Path(state_dir) / 'receipt.json'


def read_receipt(state_dir):
    """The durable receipt, or None when absent. Raises ValueError when corrupt."""
    try:
        receipt = json.loads(receipt_path(state_dir).read_text())
    except FileNotFoundError:
        return None
    if not isinstance(receipt, dict) or receipt.get('schema') != RECEIPT_SCHEMA:
        raise ValueError('spend-guard receipt has the wrong shape')
    return receipt


def read_history(state_dir) -> list:
    path = Path(state_dir) / 'history.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines() if line] if path.exists() else []


def _write_receipt(state_dir: Path, receipt: dict, event: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix='.receipt.', dir=state_dir)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(receipt, handle, indent=1, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, receipt_path(state_dir))
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    directory = os.open(state_dir, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    with (state_dir / 'history.jsonl').open('a') as handle:
        handle.write(json.dumps(event, sort_keys=True) + '\n')


def _blank_receipt() -> dict:
    return {'schema': RECEIPT_SCHEMA, 'state': 'CLEAR', 'hold': None, 'hold_since': None,
            'disabled_workers': [], 'last_evaluation': None}


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# ── acting ───────────────────────────────────────────────────────────────────

def _subdomain(http, cfg, token, script, body=None):
    path = f'/workers/scripts/{script}/subdomain'
    result = _api(http, cfg, token, 'POST' if body else 'GET', path, body)
    if not isinstance(result, dict) or not all(isinstance(result.get(k), bool) for k in ('enabled', 'previews_enabled')):
        raise ApiError(f'{path} returned no enabled/previews_enabled state')
    return {'enabled': result['enabled'], 'previews_enabled': result['previews_enabled']}


def _disable(http, cfg, token, scripts, receipt) -> list:
    errors, recorded = [], {w['script'] for w in receipt['disabled_workers']}
    off = {'enabled': False, 'previews_enabled': False}
    for script in scripts:
        try:
            prior = _subdomain(http, cfg, token, script)
            if prior == off:
                continue
            if _subdomain(http, cfg, token, script, off) != off:
                raise ApiError(f'{script} still reachable on workers.dev after disable')
            if script not in recorded:
                receipt['disabled_workers'].append({'script': script, 'prior': prior})
        except (ApiError, UsageUnavailable, OSError, ValueError) as exc:
            errors.append(f'{script}: {exc}')
    return errors


def run(cfg: dict, *, http, token, now: datetime, state_dir=None) -> dict:
    state = _state_dir(cfg, state_dir)
    try:
        receipt = read_receipt(state) or _blank_receipt()
    except ValueError:
        # Salvage before heal: the unreadable receipt may hold the restore list, so it is kept
        # aside and the hold stays until a human runs restore.
        receipt_path(state).rename(state / f"receipt.corrupt.{now.strftime('%Y%m%dT%H%M%SZ')}.json")
        receipt = dict(_blank_receipt(), state='STOPPED', hold='receipt-corrupt', hold_since=_iso(now))
    production = {'mode': 'stop' if cfg['stop_production_workers'] else 'report-only',
                  'workers': list(cfg['production_workers'])}
    usage, action_errors = None, []
    try:
        if not token:
            raise UsageUnavailable(f"API token {cfg['token_name']} is not configured in tokens.env")
        usage = read_usage(cfg, token, http, now)
        decision = evaluate(cfg, usage)
    except (UsageUnavailable, ApiError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        decision = {'verdict': 'UNKNOWN', 'reasons': [f'usage unreadable: {exc}'], 'metered_usd': None,
                    'peak_allowance': None}

    verdict = decision['verdict']
    if verdict == 'STOP':
        targets = cfg['staging_workers'] + (cfg['production_workers'] if cfg['stop_production_workers'] else [])
        action_errors = _disable(http, cfg, token, targets, receipt)
        if receipt['hold'] != 'spend':
            receipt['hold'], receipt['hold_since'] = 'spend', _iso(now)
    elif verdict == 'UNKNOWN':
        if receipt['hold'] is None:
            receipt['hold'], receipt['hold_since'] = 'unknown', _iso(now)
    elif receipt['hold'] == 'unknown':
        receipt['hold'], receipt['hold_since'] = None, None
    receipt['state'] = 'STOPPED' if receipt['hold'] else 'CLEAR'
    receipt['last_evaluation'] = {
        'at': _iso(now), 'verdict': verdict, 'reasons': decision['reasons'],
        'metered_usd': decision['metered_usd'], 'peak_allowance': decision['peak_allowance'],
        'data_through': usage['data_through'] if usage else None, 'action_errors': action_errors,
        'production': production}
    _write_receipt(state, receipt, {'at': _iso(now), 'event': 'run', 'verdict': verdict, 'state': receipt['state'],
                                    'reasons': decision['reasons'], 'action_errors': action_errors})
    return {'verdict': verdict, 'reasons': decision['reasons'], 'production': production,
            'action_errors': action_errors, 'state': receipt['state']}


def restore(cfg: dict, *, http, token, now: datetime, state_dir=None) -> dict:
    """Separately run by a human: re-enable what the guard disabled and lift the hold."""
    state = _state_dir(cfg, state_dir)
    receipt = read_receipt(state)
    if receipt is None or (receipt['hold'] is None and not receipt['disabled_workers']):
        return {'restored': True, 'errors': [], 'message': 'nothing to restore'}
    if not token:
        return {'restored': False, 'errors': [f"API token {cfg['token_name']} is not configured"],
                'message': 'receipt unchanged'}
    remaining, errors = [], []
    for entry in receipt['disabled_workers']:
        try:
            if _subdomain(http, cfg, token, entry['script'], entry['prior']) != entry['prior']:
                raise ApiError('state after restore does not match the recorded prior state')
        except (ApiError, UsageUnavailable, OSError, ValueError) as exc:
            errors.append(f"{entry['script']}: {exc}")
            remaining.append(entry)
    receipt['disabled_workers'] = remaining
    if not remaining:
        receipt.update(hold=None, hold_since=None, state='CLEAR', restored_at=_iso(now))
    _write_receipt(state, receipt, {'at': _iso(now), 'event': 'restore', 'state': receipt['state'], 'errors': errors})
    return {'restored': not remaining, 'errors': errors,
            'message': 'hold lifted' if not remaining else 'hold kept; some Workers were not restored'}


# ── reading the receipt: E2E gate and health ─────────────────────────────────

def e2e_gate(cfg: dict, *, now: datetime, state_dir=None):
    """(allowed, reason). Every E2E dispatch must call this first; anything but a fresh CLEAR holds."""
    try:
        receipt = read_receipt(_state_dir(cfg, state_dir))
    except (ValueError, OSError) as exc:
        return False, f'HOLD: spend-guard receipt unreadable ({exc})'
    if receipt is None:
        return False, 'HOLD: no spend-guard receipt; the guard has not run'
    evaluation = receipt.get('last_evaluation') or {}
    if receipt.get('state') != 'CLEAR':
        return False, (f"HOLD: receipt STOPPED (hold {receipt.get('hold')}, since {receipt.get('hold_since')}); "
                       f"last verdict {evaluation.get('verdict')}: {'; '.join(evaluation.get('reasons') or [])}")
    try:
        age = now - _time(evaluation.get('at'), 'last_evaluation.at')
    except UsageUnavailable:
        return False, 'HOLD: receipt has no evaluation time'
    if age > timedelta(hours=cfg['e2e_max_evaluation_age_hours']):
        return False, f"HOLD: last spend evaluation is {round(age.total_seconds() / 3600)}h old"
    return True, f"CLEAR: last verdict {evaluation.get('verdict')} at {evaluation.get('at')}"


def bound_action(cfg: dict) -> str:
    production = 'also stopped' if cfg['stop_production_workers'] else 'report-only'
    return (f"on breach: STOP disables workers.dev on {', '.join(cfg['staging_workers'])} and writes the STOPPED "
            f"receipt E2E dispatch must pass; UNKNOWN writes the receipt only; production {production} · owner joe · "
            "remediation open Cloudflare Billing > Billable Usage, cut the named driver, then run "
            "./run.sh cloudflare-spend-guard restore · verify next guard run prints OK or WARN and "
            "./run.sh cloudflare-spend-guard check-e2e exits 0 · auto-clear UNKNOWN on the next readable run; "
            "STOP only by restore")


def health_line(receipt, cfg: dict | None = None) -> str:
    cfg = cfg or load_config()
    if not receipt or not receipt.get('last_evaluation'):
        return f'UNKNOWN cloudflare spend · guard has not run; E2E held · {bound_action(cfg)}'
    ev = receipt['last_evaluation']
    parts = []
    if ev.get('metered_usd') is not None:
        parts.append(f"metered {_usd(ev['metered_usd'])}")
    if ev.get('peak_allowance'):
        parts.append(f"peak {ev['peak_allowance']['key']} {ev['peak_allowance']['fraction']:.0%}")
    parts.append(f"data through {ev.get('data_through') or 'n/a'}")
    parts.extend(ev.get('reasons') or [])
    disabled = [w['script'] for w in receipt.get('disabled_workers') or []]
    parts.append(f"workers.dev disabled: {', '.join(disabled) if disabled else 'none'}")
    if ev.get('action_errors'):
        parts.append(f"action errors: {'; '.join(ev['action_errors'])}")
    held = 'held' if receipt.get('state') != 'CLEAR' else 'clear'
    return f"{ev['verdict']} cloudflare spend · {'; '.join(parts)}; E2E {held} · {bound_action(cfg)}"


# ── command line ─────────────────────────────────────────────────────────────

def parse_args(argv):
    parser = argparse.ArgumentParser(prog='run.sh cloudflare-spend-guard', description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('run', help='read usage, decide, act on staging, write the receipt')
    sub.add_parser('restore', help='re-enable the Workers this guard disabled and lift the hold')
    sub.add_parser('check-e2e', help='exit 0 only when E2E dispatch may proceed')
    sub.add_parser('health', help='print the last evaluation as a health row (no network)')
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    cfg = load_config()
    now = datetime.now(timezone.utc)
    if args.command == 'run':
        run(cfg, http=urllib_http, token=read_token(cfg), now=now)
        receipt = read_receipt(_state_dir(cfg, None))
        print(health_line(receipt, cfg))
        return EXIT_CODES[receipt['last_evaluation']['verdict']]
    if args.command == 'restore':
        result = restore(cfg, http=urllib_http, token=read_token(cfg), now=now)
        print(f"restore: {result['message']}" + (f" ({'; '.join(result['errors'])})" if result['errors'] else ''))
        return 0 if result['restored'] else 1
    if args.command == 'check-e2e':
        allowed, reason = e2e_gate(cfg, now=now)
        print(reason)
        return 0 if allowed else 1
    try:
        receipt = read_receipt(_state_dir(cfg, None))
    except (ValueError, OSError):
        receipt = None
    print(health_line(receipt, cfg))
    evaluation = (receipt or {}).get('last_evaluation') or {}
    return EXIT_CODES[evaluation.get('verdict', 'UNKNOWN')]


if __name__ == '__main__':
    sys.exit(main())
