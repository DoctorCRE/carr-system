#!/usr/bin/env python3
"""Cloudflare spend guard: a deterministic kill switch, because Cloudflare has none.

WHY. Budget alerts "are informational only. They do not pause or cap usage"
(https://developers.cloudflare.com/billing/manage/budget-alerts/). This job
reads the account's usage, compares it with ops/config/cloudflare-spend-guard.v1.json,
and acts. Two readers feed one receipt:

  run   (daily dollar backstop) the Billable Usage API: the invoice's own
        numbers, one to two days behind.
  fast  (every 15 minutes, by design) one GraphQL Analytics request with
        month-to-date Workers requests, a CPU upper bound, KV, R2 and
        Durable Object operation counts, the last 15 minutes per Worker and
        the same 15 minutes on each of the previous 7 days. Minutes behind.

Verdicts, lowest to highest:
  OK       nothing to do.
  WARN     an included allowance is at 50% or more, or the local loop-watch
           heartbeat is stale. Report only.
  RUNAWAY  (fast) one Worker's 15-minute traffic is 5x its own trailing
           median and over a floor, or more than half of it errors. Writes the
           receipt and alert first, then disables workers.dev on that Worker
           only if it is a STAGING Worker; any other Worker is alert-only.
  STOP     any metered amount, an allowance at 80%, a service with usage the
           config cannot map, or a staging burst. Disables workers.dev on the
           STAGING Workers and writes the STOPPED receipt E2E must pass.
           Production is report-only unless stop_production_workers is true.
  UNKNOWN  usage could not be read. Holds E2E; changes no Worker.

STOP and RUNAWAY holds are sticky: only `restore`, typed by a human at a
terminal, lifts them, and even then E2E stays held until a fresh non-STOP run.
Before each disable POST the prior state is fsynced as "pending"; a read-back
then marks it "confirmed" or "unverified". Every run and restore holds an
exclusive lock in the state directory and re-reads the receipt under it.

SOURCES (read raw, 2026-10-08):
  usage    GET /accounts/{id}/billable-usage/info and /billable-usage (v1 Alpha),
           https://github.com/cloudflare/api-schemas (openapi.json),
           https://developers.cloudflare.com/billing/manage/billable-usage/
  fast     POST https://api.cloudflare.com/client/v4/graphql,
           https://developers.cloudflare.com/analytics/graphql-api/ (limits,
           account-based-rate-limiting, tutorials/querying-workers-metrics),
           /kv/observability/metrics-analytics/, /r2/platform/metrics-analytics/,
           /durable-objects/observability/metrics-and-analytics/
  action   GET/POST /accounts/{id}/workers/scripts/{script}/subdomain. The
           schema marks it x-fern-availability: deprecated and names no
           successor; a failed action says so loudly.

No model is called. Thresholds and targets come only from the config file;
there are no environment or command-line overrides. The token is read from
~/.config/carr/tokens.env, cleaned, and never printed or stored.
"""

from __future__ import annotations

import argparse
import calendar
import contextlib
import errno
import fcntl
import importlib.util
import json
import math
import os
import re
import secrets
import statistics
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / 'ops' / 'config' / 'cloudflare-spend-guard.v1.json'
API = 'https://api.cloudflare.com/client/v4'
GRAPHQL = f'{API}/graphql'
RECEIPT_SCHEMA = 'carr-cloudflare-spend-guard-receipt.v1'
LEVELS = ('OK', 'WARN', 'RUNAWAY', 'STOP', 'UNKNOWN')
EXIT_CODES = {'OK': 0, 'WARN': 1, 'STOP': 2, 'UNKNOWN': 3, 'ACTION_FAILED': 4, 'RUNAWAY': 5}
STICKY_HOLDS = ('spend', 'runaway', 'receipt-corrupt')
RESTORE_PHRASE = 'restore staging workers.dev'
SCRIPT_CHARS = set('abcdefghijklmnopqrstuvwxyz0123456789-_')
TOKEN_CHARS = {chr(c) for c in range(0x21, 0x7f)}
COST_FIELDS = ('BilledCost', 'ContractedCost', 'EffectiveCost', 'ListCost')
OFF = {'enabled': False, 'previews_enabled': False}
CLOCK_SKEW = timedelta(minutes=5)
DEPRECATED_NOTE = ('the workers.dev subdomain endpoint is marked deprecated in Cloudflare\'s API schema with no named '
                   'successor; check the schema before trusting this action')
_BEARER = re.compile(r'Bearer\s+\S+')


class UsageUnavailable(Exception):
    """Usage could not be read or did not have the documented shape."""


class ApiError(Exception):
    """A Cloudflare call failed. The message never carries the token."""


# ── secrets stay out of every output ─────────────────────────────────────────

def clean_token(raw):
    """The token without surrounding whitespace, or None unless it is printable ASCII with no spaces."""
    if not isinstance(raw, str):
        return None
    token = raw.strip()
    return token if token and set(token) <= TOKEN_CHARS else None


def redact(value, secret=None):
    """Bearer values, and the token itself when known, removed at any depth."""
    if isinstance(value, str):
        value = _BEARER.sub('Bearer [redacted]', value)
        return value.replace(secret, '[redacted]') if secret else value
    if isinstance(value, list):
        return [redact(v, secret) for v in value]
    if isinstance(value, dict):
        return {k: redact(v, secret) for k, v in value.items()}
    return value


def describe(exc: BaseException) -> str:
    """Our own messages (built without the token, then redacted); anything else only by type."""
    if isinstance(exc, (UsageUnavailable, ApiError)):
        return redact(str(exc))
    if isinstance(exc, OSError) and isinstance(exc.errno, int):
        return f'{type(exc).__name__}({errno.errorcode.get(exc.errno, exc.errno)})'
    return type(exc).__name__


# ── configuration ────────────────────────────────────────────────────────────

def _need(condition, message):
    if not condition:
        raise ValueError(f'cloudflare spend guard config: {message}')


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_config(cfg: dict) -> dict:
    need = _need
    account = cfg.get('account_id')
    need(isinstance(account, str) and len(account) == 32 and all(c in '0123456789abcdef' for c in account),
         'account_id must be a 32-character hex account tag')
    need(isinstance(cfg.get('token_name'), str) and cfg['token_name'], 'token_name required')
    need(isinstance(cfg.get('state_dir'), str) and cfg['state_dir'], 'state_dir required')
    for key in ('request_timeout_seconds', 'max_usage_age_hours', 'e2e_max_evaluation_age_hours',
                'loop_watch_heartbeat_stale_seconds'):
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
    _validate_fast(cfg)
    if 'loop_watch' in cfg:
        _loop_watch().validate_settings(cfg['loop_watch'])
    return cfg


def _validate_fast(cfg: dict) -> None:
    need = _need
    fp: dict = cfg['fast_path'] if isinstance(cfg.get('fast_path'), dict) else {}
    need(bool(fp), 'fast_path required')
    need(fp.get('cpu_time_unit') in ('microseconds', 'milliseconds'), 'fast_path.cpu_time_unit must be microseconds '
                                                                      'or milliseconds')
    for key in ('max_query_span_days', 'query_limit', 'burst_window_minutes', 'burst_requests_staging'):
        need(isinstance(fp.get(key), int) and fp[key] > 0, f'fast_path.{key} must be a positive integer')
    need(fp['query_limit'] <= 10000, 'fast_path.query_limit must not exceed 10000')
    need(fp['max_query_span_days'] <= 7, 'fast_path.max_query_span_days must not exceed 7')
    for key in ('r2_class_b_actions', 'r2_free_actions'):
        need(isinstance(fp.get(key), list) and all(isinstance(a, str) and a for a in fp[key]), f'fast_path.{key}')
    metrics = {'workers.requests', 'workers.cpu_ms_upper_bound', 'kv.read', 'kv.write', 'kv.delete', 'kv.list',
               'r2.class_a', 'r2.class_b', 'do.requests'}
    seen = set()
    for entry in fp.get('allowances') or []:
        need(entry.get('metric') in metrics and entry['metric'] not in seen, f"fast_path allowance {entry.get('key')} "
                                                                             'needs a unique known metric')
        seen.add(entry['metric'])
        need(_number(entry.get('included_per_period')) and entry['included_per_period'] > 0,
             f"fast_path allowance {entry.get('key')} needs a positive included_per_period")
    r: dict = cfg['runaway'] if isinstance(cfg.get('runaway'), dict) else {}
    need(bool(r), 'runaway required')
    for key in ('floor_requests', 'spike_multiple', 'history_days', 'min_history_days', 'error_min_requests'):
        need(isinstance(r.get(key), int) and r[key] > 0, f'runaway.{key} must be a positive integer')
    need(r['min_history_days'] <= r['history_days'] <= 28, 'runaway needs min_history_days <= history_days <= 28')
    need(_number(r.get('error_ratio_above')) and 0 < r['error_ratio_above'] < 1, 'runaway.error_ratio_above in (0,1)')


def load_config(path: Path = CONFIG_PATH) -> dict:
    return validate_config(json.loads(Path(path).read_text()))


_LOOP_WATCH = None


def _loop_watch():
    global _LOOP_WATCH
    if _LOOP_WATCH is None:
        spec = importlib.util.spec_from_file_location('cloudflare_loop_watch',
                                                      Path(__file__).with_name('cloudflare_loop_watch.py'))
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _LOOP_WATCH = module
    return _LOOP_WATCH


def _state_dir(cfg: dict, state_dir) -> Path:
    return Path(state_dir) if state_dir is not None else Path(cfg['state_dir']).expanduser()


# ── Cloudflare calls ─────────────────────────────────────────────────────────

class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would resend the Authorization header somewhere we did not choose."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ApiError(f'{req.get_method()} refused an HTTP {code} redirect')


_OPENER = urllib.request.build_opener(_RefuseRedirect)


def urllib_http(method, url, headers, body, timeout):
    """The production transport: (status, parsed JSON). Network errors raise."""
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers = {**headers, 'Content-Type': 'application/json'}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    try:
        return status, json.loads(raw)
    except ValueError:
        raise UsageUnavailable(f'{method} response body is not JSON (HTTP {status})') from None


def _headers(token):
    return {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}


def _api(http, cfg, token, method, path, body=None):
    url = f"{API}/accounts/{cfg['account_id']}{path}"
    status, payload = http(method, url, _headers(token), body, cfg['request_timeout_seconds'])
    if status != 200 or not isinstance(payload, dict) or payload.get('success') is not True:
        detail = ''
        if isinstance(payload, dict) and isinstance(payload.get('errors'), list) and payload['errors']:
            first = payload['errors'][0]
            if isinstance(first, dict):
                detail = f" error {first.get('code')}: {redact(str(first.get('message'))[:160], token)}"
        raise ApiError(f'{method} {path} returned HTTP {status}{detail}')
    return payload.get('result')


def read_token(cfg: dict):
    """The guard's own token from tokens.env, cleaned. None when absent, unsafe or malformed."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from credential_env import TokensFilePermissionError, load_carr_tokens
    try:
        return clean_token(load_carr_tokens([cfg['token_name']]).get(cfg['token_name']))
    except TokensFilePermissionError:
        return None


# ── reading billable usage ───────────────────────────────────────────────────

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


def _billing_cycle_start(cfg, token, http, now) -> datetime:
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
    return max(_period_start(_time(s.get('billing_cycle_anchor_timestamp'), 'billing_cycle_anchor_timestamp'), now)
               for s in active)


def _row_cost(record) -> float:
    present = [f for f in COST_FIELDS if record.get(f) is not None]
    if not present:
        raise UsageUnavailable('usage row has no cost column')
    return max(_quantity(record, f) for f in present)


def read_usage(cfg: dict, token: str, http, now: datetime) -> dict:
    cycle_start = _billing_cycle_start(cfg, token, http, now)
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
        cost = _row_cost(record)
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


# ── deciding on billable usage ───────────────────────────────────────────────

def _usd(value: float) -> str:
    return f'${value:,.2f}' if value >= 0.01 or value == 0 else f'${value:.4f}'


def _fraction_check(cfg, key, used, included, reasons, raise_to, suffix=''):
    t = cfg['thresholds']
    fraction = used / included
    label = f'{key} at {fraction:.0%} of {included:,}{suffix}'
    if fraction >= t['stop_allowance_fraction']:
        reasons.append(label)
        raise_to('STOP')
    elif fraction >= t['warn_allowance_fraction']:
        reasons.append(label)
        raise_to('WARN')
    return fraction


def evaluate(cfg: dict, usage: dict) -> dict:
    t = cfg['thresholds']
    services = usage['services']
    level = 'OK'
    reasons: list[str] = []

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
        fractions[entry['key']] = _fraction_check(cfg, entry['key'], sum(r['consumed'] for r in rows),
                                                  entry['included_per_period'], reasons, raise_to)
    for name, s in sorted(services.items()):
        if name not in mapped and s['consumed'] > 0:
            # Fail closed: usage the config cannot weigh against an allowance could be anything.
            reasons.append(f"unmapped service {name} ({s['family'] or 'no family'}) has usage; map it in the config "
                           '(run map-services)')
            raise_to('STOP')
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


def _fsync_dir(state_dir: Path) -> None:
    directory = os.open(state_dir, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


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
    _fsync_dir(state_dir)
    with (state_dir / 'history.jsonl').open('a') as handle:
        handle.write(json.dumps(event, sort_keys=True) + '\n')


def _saver(state_dir: Path, receipt: dict, token):
    def save(event: dict) -> None:
        _write_receipt(state_dir, redact(receipt, token), redact(event, token))
    return save


def _blank_receipt() -> dict:
    return {'schema': RECEIPT_SCHEMA, 'state': 'CLEAR', 'hold': None, 'hold_since': None,
            'disabled_workers': [], 'last_evaluation': None, 'last_fast': None}


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


@contextlib.contextmanager
def _locked(state_dir: Path):
    """One writer at a time: every run, fast poll and restore holds this for its whole read-decide-write."""
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(state_dir / 'guard.lock', os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _load_for_update(state: Path, now: datetime) -> dict:
    try:
        return read_receipt(state) or _blank_receipt()
    except ValueError:
        pass
    # Salvage before heal: the unreadable receipt may hold the restore list. Keep it under a name
    # that cannot overwrite an earlier quarantine, and hold until a human acknowledges it.
    for _ in range(10):
        name = f"receipt.corrupt.{now.strftime('%Y%m%dT%H%M%SZ')}.{secrets.token_hex(4)}.json"
        try:
            os.link(receipt_path(state), state / name)
            break
        except FileExistsError:
            continue
    else:
        raise OSError(errno.EEXIST, 'could not pick a free quarantine name')
    receipt_path(state).unlink()
    return dict(_blank_receipt(), state='STOPPED', hold='receipt-corrupt', hold_since=_iso(now), quarantined_file=name)


# ── acting ───────────────────────────────────────────────────────────────────

def _subdomain(http, cfg, token, script, body=None):
    path = f'/workers/scripts/{script}/subdomain'
    result = _api(http, cfg, token, 'POST' if body else 'GET', path, body)
    if not isinstance(result, dict) or not all(isinstance(result.get(k), bool) for k in ('enabled', 'previews_enabled')):
        raise ApiError(f'{path} returned no enabled/previews_enabled state')
    return {'enabled': result['enabled'], 'previews_enabled': result['previews_enabled']}


def _disable(http, cfg, token, scripts, receipt, save, now) -> list:
    """Write-ahead: the prior state is fsynced as pending before each POST, then confirmed by read-back."""
    errors = []
    for script in scripts:
        try:
            prior = _subdomain(http, cfg, token, script)
        except Exception as exc:  # noqa: BLE001 — one Worker's failure must not stop the others
            errors.append(f'{script}: {describe(exc)}')
            continue
        if prior == OFF:
            continue
        entry = next((w for w in receipt['disabled_workers'] if w['script'] == script), None)
        if entry is None:
            entry = {'script': script, 'prior': prior, 'status': 'pending'}
            receipt['disabled_workers'].append(entry)
            try:
                save({'at': _iso(now), 'event': 'disable-pending', 'script': script})
            except Exception as exc:  # noqa: BLE001 — without a durable restore record, never POST
                receipt['disabled_workers'].remove(entry)
                errors.append(f'{script}: write-ahead failed ({describe(exc)}); not disabled')
                continue
        else:
            entry['status'] = 'pending'  # redeployed since the last stop: keep the original prior state
        try:
            _subdomain(http, cfg, token, script, OFF)
        except Exception as exc:  # noqa: BLE001
            errors.append(f'{script}: disable failed ({describe(exc)}); entry left pending')
            continue
        try:
            after = _subdomain(http, cfg, token, script)
        except Exception as exc:  # noqa: BLE001
            entry['status'] = 'unverified'
            errors.append(f'{script}: disable sent but read-back failed ({describe(exc)})')
            continue
        if after == OFF:
            entry['status'] = 'confirmed'
        else:
            entry['status'] = 'unverified'
            errors.append(f'{script}: still reachable on workers.dev after disable')
    return errors


def _hold(receipt, hold, now):
    if receipt['hold'] in (None, 'unknown') or (hold == 'spend' and receipt['hold'] == 'runaway'):
        receipt['hold'], receipt['hold_since'] = hold, _iso(now)
    receipt['state'] = 'STOPPED'


def _spawner(spawn_reporter):
    return spawn_reporter or (lambda: _loop_watch().spawn_reporter())


def _report_finding(state: Path, key: str, defect_class: str, claimed: str, actual: str, now: datetime, spawn) -> None:
    """Rule 1f3a7372: an unattended run's finding goes to the record layer, via the local outbox, never only a log."""
    _loop_watch().queue_report(state, key, {'defect_class': defect_class, 'claimed': claimed, 'actual': actual[:1500],
                                            'source_unread': str(receipt_path(state))}, now=now)
    spawn()


def _report_new_hold(cfg, state, receipt, hold_before, reasons, now, spawn) -> None:
    hold = receipt['hold']
    if hold in STICKY_HOLDS and hold != hold_before:
        _report_finding(state, f"spend-guard:{hold}:{receipt['hold_since']}", 'cloudflare-spend-guard-stop',
                        'Cloudflare usage stays inside the included allowances in '
                        'ops/config/cloudflare-spend-guard.v1.json',
                        f"spend guard hold {hold} at {receipt['hold_since']}: {'; '.join(reasons)}; workers.dev "
                        f"disabled: {', '.join(w['script'] for w in receipt['disabled_workers']) or 'none'}; E2E held "
                        'until ./run.sh cloudflare-spend-guard restore', now, spawn)


def _stop_targets(cfg) -> list:
    return cfg['staging_workers'] + (cfg['production_workers'] if cfg['stop_production_workers'] else [])


def _production(cfg) -> dict:
    return {'mode': 'stop' if cfg['stop_production_workers'] else 'report-only', 'workers': list(cfg['production_workers'])}


def run(cfg: dict, *, http, token, now: datetime, state_dir=None, spawn_reporter=None) -> dict:
    state = _state_dir(cfg, state_dir)
    raw, token = token, clean_token(token)
    with _locked(state):
        receipt = _load_for_update(state, now)
        hold_before = None if receipt['hold'] == 'receipt-corrupt' and receipt.get('quarantined_file') and \
            not receipt_path(state).exists() else receipt['hold']
        save = _saver(state, receipt, token)
        usage, action_errors = None, []
        try:
            if raw is None:
                raise UsageUnavailable(f"API token {cfg['token_name']} is not configured in tokens.env")
            if token is None:
                raise UsageUnavailable(f"API token {cfg['token_name']} is not printable ASCII")
            usage = read_usage(cfg, token, http, now)
            decision = evaluate(cfg, usage)
        except Exception as exc:  # noqa: BLE001 — any unreadable answer is UNKNOWN, never a crash
            decision = {'verdict': 'UNKNOWN', 'reasons': [f'usage unreadable: {describe(exc)}'], 'metered_usd': None,
                        'peak_allowance': None}

        verdict = decision['verdict']
        if verdict == 'STOP':
            _hold(receipt, 'spend', now)
            action_errors = _disable(http, cfg, token, _stop_targets(cfg), receipt, save, now)
        elif verdict == 'UNKNOWN':
            if receipt['hold'] is None:
                receipt['hold'], receipt['hold_since'] = 'unknown', _iso(now)
        elif receipt['hold'] == 'unknown':
            receipt['hold'], receipt['hold_since'] = None, None
        receipt['state'] = 'STOPPED' if receipt['hold'] else 'CLEAR'
        if usage:
            receipt['period_start'] = usage['period_start']
        receipt['last_evaluation'] = {
            'at': _iso(now), 'verdict': verdict, 'reasons': decision['reasons'],
            'metered_usd': decision['metered_usd'], 'peak_allowance': decision['peak_allowance'],
            'data_through': usage['data_through'] if usage else None,
            'data_basis_at': (usage['data_through'] or usage['period_start']) if usage else None,
            'action_errors': action_errors, 'production': _production(cfg)}
        save({'at': _iso(now), 'event': 'run', 'verdict': verdict, 'state': receipt['state'],
              'reasons': decision['reasons'], 'action_errors': action_errors})
        _report_new_hold(cfg, state, redact(receipt, token), hold_before, redact(decision['reasons'], token), now,
                         _spawner(spawn_reporter))
    return redact({'verdict': verdict, 'reasons': decision['reasons'], 'production': _production(cfg),
                   'action_errors': action_errors, 'state': receipt['state']}, token)


def restore(cfg: dict, *, http, token, now: datetime, state_dir=None, ack_quarantine=None) -> dict:
    """Run by a human: re-enable what the guard disabled, lift the hold, and leave E2E held until a fresh run."""
    state = _state_dir(cfg, state_dir)
    token = clean_token(token)
    result: dict = {'restored': False, 'errors': [], 'notes': [], 'quarantined_file': None, 'message': ''}
    with _locked(state):
        try:
            receipt = read_receipt(state)
        except ValueError:
            result.update(message='receipt unreadable', errors=['run the guard once so it quarantines the receipt'])
            return result
        if receipt is None or (receipt['hold'] is None and not receipt['disabled_workers']):
            result.update(restored=True, message='nothing to restore')
            return result
        quarantined = receipt.get('quarantined_file')
        result['quarantined_file'] = quarantined
        if receipt['hold'] == 'receipt-corrupt' and (not ack_quarantine or Path(ack_quarantine).name != quarantined):
            result.update(message='hold kept', errors=[
                f'hold is receipt-corrupt: inspect {state / str(quarantined)} (it may list Workers to re-enable by '
                f'hand), then rerun with --ack-quarantine {quarantined}'])
            return result
        if receipt['disabled_workers'] and not token:
            result.update(message='receipt unchanged', errors=[f"API token {cfg['token_name']} is not configured"])
            return result
        remaining = []
        for entry in receipt['disabled_workers']:
            script = entry['script']
            try:
                current = _subdomain(http, cfg, token, script)
                if current != OFF:
                    result['notes'].append(
                        f"{script}: skipped; workers.dev is enabled={current['enabled']} "
                        f"previews_enabled={current['previews_enabled']}, not the off state the guard left. "
                        'Left as it is and dropped from the restore list')
                    continue
                _subdomain(http, cfg, token, script, entry['prior'])
                if _subdomain(http, cfg, token, script) != entry['prior']:
                    raise ApiError('state after restore does not match the recorded prior state')
            except Exception as exc:  # noqa: BLE001
                result['errors'].append(f'{script}: {describe(exc)}')
                remaining.append(entry)
        receipt['disabled_workers'] = remaining
        if not remaining:
            receipt.update(hold=None, hold_since=None, state='CLEAR', restored_at=_iso(now), last_evaluation=None,
                           last_fast=None, quarantined_file=None)
        _saver(state, receipt, token)({'at': _iso(now), 'event': 'restore', 'state': receipt['state'],
                                       'errors': result['errors'], 'notes': result['notes']})
    result.update(restored=not remaining, message='hold lifted; E2E stays held until a fresh non-STOP run'
                  if not remaining else 'hold kept; some Workers were not restored')
    return redact(result, token)


# ── the fast path: one GraphQL request ───────────────────────────────────────

def _gql_time(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _spans(start: datetime, end: datetime, days: int) -> list:
    spans, cursor = [], start
    while cursor < end:
        stop = min(cursor + timedelta(days=days), end)
        spans.append((cursor, stop if stop == end else stop - timedelta(seconds=1)))
        cursor = stop
    return spans or [(start, end)]


def _date_spans(start: datetime, end: datetime, days: int) -> list:
    first, last, spans = start.date(), end.date(), []
    while first <= last:
        stop = min(first + timedelta(days=days - 1), last)
        spans.append((first.isoformat(), stop.isoformat()))
        first = stop + timedelta(days=1)
    return spans


def build_fast_query(cfg: dict, period_start: datetime, now: datetime) -> tuple[str, list]:
    """One request, one account scope. Returns the query and its (alias, kind) plan."""
    fp, limit = cfg['fast_path'], cfg['fast_path']['query_limit']
    nodes, plan = [], []

    def node(alias, kind, dataset, flt, fields):
        nodes.append(f'{alias}: {dataset}(limit: {limit}, filter: {{{flt}}}) {{ {fields} }}')
        plan.append((alias, kind))

    def dt(a, b):
        return f'datetime_geq: "{_gql_time(a)}", datetime_leq: "{_gql_time(b)}"'

    spans = _spans(period_start, now, fp['max_query_span_days'])
    for i, (a, b) in enumerate(spans):
        node(f'w{i}', 'mtd', 'workersInvocationsAdaptive', dt(a, b),
             'sum { requests subrequests errors } quantiles { cpuTimeP99 } dimensions { scriptName datetimeHour }')
        node(f'r2{i}', 'r2', 'r2OperationsAdaptiveGroups', dt(a, b), 'sum { requests } dimensions { actionType }')
    for i, (a, b) in enumerate(_date_spans(period_start, now, fp['max_query_span_days'])):
        date = f'date_geq: "{a}", date_leq: "{b}"'
        node(f'kv{i}', 'kv', 'kvOperationsAdaptiveGroups', date, 'sum { requests } dimensions { actionType }')
        node(f'do{i}', 'do', 'durableObjectsInvocationsAdaptiveGroups', date, 'sum { requests }')
    window = timedelta(minutes=fp['burst_window_minutes'])
    node('cur', 'cur', 'workersInvocationsAdaptive', dt(now - window, now),
         'sum { requests errors } dimensions { scriptName status }')
    for k in range(1, cfg['runaway']['history_days'] + 1):
        then = now - timedelta(days=k)
        node(f'h{k}', f'h{k}', 'workersInvocationsAdaptive', dt(then - window, then),
             'sum { requests } dimensions { scriptName }')
    query = ('query SpendGuardFast { viewer { accounts(filter: {accountTag: "%s"}) { %s } } }'
             % (cfg['account_id'], ' '.join(nodes)))
    return query, plan


def _count(row, *path):
    value = row
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    if value is None and path[0] == 'quantiles':
        return None
    if not _number(value) or value < 0:
        raise UsageUnavailable(f"{'.'.join(path)} is not a non-negative number")
    return float(value)


def read_fast(cfg: dict, token: str, http, now: datetime, period_start: datetime) -> dict:
    query, plan = build_fast_query(cfg, period_start, now)
    status, payload = http('POST', GRAPHQL, _headers(token), {'query': query}, cfg['request_timeout_seconds'])
    if not isinstance(payload, dict):
        raise UsageUnavailable(f'GraphQL returned HTTP {status} with no JSON object')
    if payload.get('errors'):
        errors = payload['errors'] if isinstance(payload['errors'], list) else [payload['errors']]
        first = errors[0] if isinstance(errors[0], dict) else {}
        code = (first.get('extensions') or {}).get('code') if isinstance(first.get('extensions'), dict) else None
        raise ApiError(f"GraphQL error{' ' + str(code) if code else ''}: {redact(str(first.get('message'))[:160], token)}")
    if status != 200:
        raise ApiError(f'GraphQL returned HTTP {status}')
    accounts = ((payload.get('data') or {}).get('viewer') or {}).get('accounts')
    if not isinstance(accounts, list) or len(accounts) != 1 or not isinstance(accounts[0], dict):
        raise UsageUnavailable('GraphQL answer has no single account')
    data = accounts[0]
    fp = cfg['fast_path']
    cpu_factor = 0.001 if fp['cpu_time_unit'] == 'microseconds' else 1.0
    metrics = dict.fromkeys(('workers.requests', 'workers.subrequests', 'workers.cpu_ms_upper_bound', 'kv.read',
                             'kv.write', 'kv.delete', 'kv.list', 'r2.class_a', 'r2.class_b', 'do.requests'), 0.0)
    current: dict[str, dict] = {}
    history: list[dict] = [{} for _ in range(cfg['runaway']['history_days'])]
    for alias, kind in plan:
        rows = data.get(alias)
        if not isinstance(rows, list):
            raise UsageUnavailable(f'GraphQL node {alias} missing')
        if len(rows) >= fp['query_limit']:
            raise UsageUnavailable(f'GraphQL node {alias} hit its row limit; counts would be incomplete')
        for row in rows:
            if not isinstance(row, dict):
                raise UsageUnavailable(f'GraphQL node {alias} row is not an object')
            requests = _count(row, 'sum', 'requests')
            dims = row.get('dimensions') or {}
            if kind == 'mtd':
                p99 = _count(row, 'quantiles', 'cpuTimeP99')
                if p99 is None and requests > 0:
                    raise UsageUnavailable('cpuTimeP99 missing for a bucket with requests')
                metrics['workers.requests'] += requests
                metrics['workers.subrequests'] += _count(row, 'sum', 'subrequests')
                metrics['workers.cpu_ms_upper_bound'] += requests * (p99 or 0.0) * cpu_factor
            elif kind == 'kv':
                action = str(dims.get('actionType') or '').lower()
                # An operation type this code does not know counts against the smallest allowance.
                metrics[f'kv.{action}' if action in ('read', 'write', 'delete', 'list') else 'kv.write'] += requests
            elif kind == 'r2':
                action = str(dims.get('actionType') or '')
                if action in fp['r2_free_actions']:
                    continue
                metrics['r2.class_b' if action in fp['r2_class_b_actions'] else 'r2.class_a'] += requests
            elif kind == 'do':
                metrics['do.requests'] += requests
            else:
                script = dims.get('scriptName')
                if not isinstance(script, str) or not script:
                    raise UsageUnavailable(f'GraphQL node {alias} row has no scriptName')
                if kind == 'cur':
                    entry = current.setdefault(script, {'requests': 0.0, 'errors': 0.0})
                    entry['requests'] += requests
                    failed = requests if dims.get('status') not in (None, 'success', 'clientDisconnected') else 0.0
                    entry['errors'] += max(_count(row, 'sum', 'errors'), failed)
                else:
                    day = history[int(kind[1:]) - 1]
                    day[script] = day.get(script, 0.0) + requests
    return {'metrics': metrics, 'current': current, 'history': history,
            'period_start': _gql_time(period_start), 'queries': 1}


def detect_runaways(cfg: dict, current: dict, history: list) -> tuple[list, list]:
    """Per Worker: 15-minute requests against its own trailing median for the same window, and its error ratio."""
    r = cfg['runaway']
    runaways, floor_only = [], []
    for script, now in sorted(current.items()):
        requests, errors = now['requests'], now['errors']
        days = [day.get(script, 0.0) for day in history]
        seen = sum(1 for d in days if d > 0)
        reasons = []
        if seen < r['min_history_days']:
            floor_only.append(script)
            if requests >= r['floor_requests']:
                reasons.append(f"{requests:,.0f} requests in the window >= floor {r['floor_requests']:,} "
                               f"(floor only: {seen}d of history)")
        else:
            median = statistics.median(days)
            if requests >= r['floor_requests'] and requests >= r['spike_multiple'] * median:
                reasons.append(f"{requests:,.0f} requests in the window = {requests / median if median else math.inf:.1f}x "
                               f"its {len(days)}-day median {median:,.0f} (limit {r['spike_multiple']}x, floor "
                               f"{r['floor_requests']:,})")
        if requests > r['error_min_requests'] and errors / requests > r['error_ratio_above']:
            reasons.append(f"error ratio {errors / requests:.0%} over {requests:,.0f} requests "
                           f"(limit {r['error_ratio_above']:.0%} above {r['error_min_requests']})")
        if reasons:
            runaways.append({'script': script, 'requests': requests, 'errors': errors, 'history_days': seen,
                             'reasons': reasons})
    return runaways, floor_only


def evaluate_fast(cfg: dict, data: dict) -> dict:
    level = 'OK'
    reasons: list[str] = []

    def raise_to(new):
        nonlocal level
        level = max(level, new, key=LEVELS.index)

    metrics = data['metrics']
    for entry in cfg['fast_path']['allowances']:
        suffix = (' (upper bound: requests x cpuTimeP99 per hour bucket)'
                  if entry['metric'] == 'workers.cpu_ms_upper_bound' else '')
        _fraction_check(cfg, entry['key'], metrics[entry['metric']], entry['included_per_period'], reasons, raise_to,
                        suffix)
    window = cfg['fast_path']['burst_window_minutes']
    burst = sum(data['current'].get(s, {}).get('requests', 0.0) for s in cfg['staging_workers'])
    if burst > cfg['fast_path']['burst_requests_staging']:
        reasons.append(f"staging burst {burst:,.0f} requests in {window} min > {cfg['fast_path']['burst_requests_staging']:,}")
        raise_to('STOP')
    runaways, floor_only = detect_runaways(cfg, data['current'], data['history'])
    for item in runaways:
        reasons.append(f"RUNAWAY {item['script']}: {'; '.join(item['reasons'])}")
        raise_to('RUNAWAY')
    return {'verdict': level, 'reasons': reasons, 'runaways': runaways, 'floor_only': floor_only}


def _heartbeat_check(cfg, receipt, state, now, spawn) -> str | None:
    lw = _loop_watch()
    beat = lw.read_heartbeat(state)
    limit = cfg['loop_watch_heartbeat_stale_seconds']
    if beat is not None and -CLOCK_SKEW <= now - beat <= timedelta(seconds=limit):
        return None
    age = 'missing' if beat is None else f'{round((now - beat).total_seconds() / 60)} min old'
    reason = f'loop-watch heartbeat {age} (limit {limit // 60} min): the local loop killer is not running'
    marker = beat.timestamp() if beat else 'missing'
    if receipt.get('heartbeat_alerted') != marker:
        receipt['heartbeat_alerted'] = marker
        lw.queue_report(state, f'loop-watch:heartbeat-stale:{marker}', {
            'defect_class': 'loop-watch-heartbeat-stale',
            'claimed': 'loop-watch scans every minute and writes its heartbeat',
            'actual': reason, 'source_unread': str(state / 'loop-watch.json')}, now=now)
        spawn()
    return reason


def _fast_period(cfg, receipt, token, http, now) -> datetime:
    cached = receipt.get('period_start')
    if cached:
        start = _time(cached, 'period_start')
        if timedelta(0) <= now - start < timedelta(days=31):
            return start
    start = _billing_cycle_start(cfg, token, http, now)
    receipt['period_start'] = start.strftime('%Y-%m-%dT%H:%MZ')
    return start


def fast(cfg: dict, *, http, token, now: datetime, state_dir=None, spawn_reporter=None) -> dict:
    """The 15-minute poll: one GraphQL request; writes only when the verdict, hold or action changes."""
    state = _state_dir(cfg, state_dir)
    raw, token = token, clean_token(token)
    spawn = _spawner(spawn_reporter)
    with _locked(state):
        receipt = _load_for_update(state, now)
        hold_before = None if receipt['hold'] == 'receipt-corrupt' and receipt.get('quarantined_file') and \
            not receipt_path(state).exists() else receipt['hold']
        receipt.setdefault('last_fast', None)
        before = json.dumps(receipt, sort_keys=True)
        save = _saver(state, receipt, token)
        action_errors: list = []
        try:
            if raw is None:
                raise UsageUnavailable(f"API token {cfg['token_name']} is not configured in tokens.env")
            if token is None:
                raise UsageUnavailable(f"API token {cfg['token_name']} is not printable ASCII")
            data = read_fast(cfg, token, http, now, _fast_period(cfg, receipt, token, http, now))
            decision = evaluate_fast(cfg, data)
        except Exception as exc:  # noqa: BLE001
            decision = {'verdict': 'UNKNOWN', 'reasons': [f'analytics unreadable: {describe(exc)}'], 'runaways': [],
                        'floor_only': []}
        heartbeat = _heartbeat_check(cfg, receipt, state, now, spawn)
        if heartbeat:
            decision['reasons'].append(heartbeat)
            if decision['verdict'] == 'OK':
                decision['verdict'] = 'WARN'
        verdict, runaways = decision['verdict'], decision['runaways']
        staging_runaways = [r['script'] for r in runaways if r['script'] in cfg['staging_workers']]
        if runaways:
            # Receipt and alert first, then the action.
            alert = '; '.join(f"RUNAWAY {r['script']} ({'disable workers.dev' if r['script'] in staging_runaways else 'alert only'})"
                              for r in runaways)
            print(f'ALERT cloudflare runaway: {alert}', file=sys.stderr)
            save({'at': _iso(now), 'event': 'runaway-alert', 'runaways': runaways})
        if verdict == 'STOP':
            _hold(receipt, 'spend', now)
            action_errors = _disable(http, cfg, token, _stop_targets(cfg), receipt, save, now)
        elif staging_runaways and verdict == 'RUNAWAY':
            _hold(receipt, 'runaway', now)
            action_errors = _disable(http, cfg, token, staging_runaways, receipt, save, now)
        receipt['state'] = 'STOPPED' if receipt['hold'] else 'CLEAR'
        signature = [verdict, receipt['hold'], sorted((w['script'], w['status']) for w in receipt['disabled_workers']),
                     sorted(r['script'] for r in runaways), receipt.get('period_start'), bool(action_errors)]
        previous = (receipt.get('last_fast') or {}).get('signature')
        changed = previous != json.loads(json.dumps(signature))
        if changed:
            receipt['last_fast'] = {'since': _iso(now), 'verdict': verdict, 'reasons': decision['reasons'],
                                    'runaways': runaways, 'action_errors': action_errors, 'signature': signature}
        if json.dumps(receipt, sort_keys=True) != before:
            save({'at': _iso(now), 'event': 'fast', 'verdict': verdict, 'state': receipt['state'],
                  'reasons': decision['reasons'], 'action_errors': action_errors})
        if runaways and changed:
            _report_finding(state, f"spend-guard:runaway:{','.join(r['script'] for r in runaways)}:{_iso(now)}",
                            'cloudflare-runaway-worker',
                            'each Worker\'s 15-minute traffic stays near its own trailing median',
                            redact(f"{'; '.join(decision['reasons'])}; action: "
                                   f"{'workers.dev disabled on ' + ', '.join(staging_runaways) if staging_runaways else 'alert only'}",
                                   token), now, spawn)
        if receipt['hold'] != 'runaway':
            _report_new_hold(cfg, state, redact(receipt, token), hold_before, redact(decision['reasons'], token), now,
                             spawn)
    floor = (f" · floor only (history < {cfg['runaway']['min_history_days']}d): {', '.join(decision['floor_only'])}"
             if decision['floor_only'] else '')
    line = (f"{verdict} cloudflare fast · {'; '.join(decision['reasons']) or 'within allowances'}{floor}; "
            f"E2E {'held' if receipt['state'] != 'CLEAR' else 'clear'} · {bound_action(cfg)}")
    return redact({'verdict': verdict, 'reasons': decision['reasons'], 'runaways': runaways,
                   'action_errors': action_errors, 'state': receipt['state'], 'line': line}, token)


# ── read-only helpers ────────────────────────────────────────────────────────

def map_services(cfg: dict, *, http, token, now: datetime) -> list:
    """Dry run: every ServiceName the Billable Usage API reports, and where the config maps it. Writes nothing."""
    token = clean_token(token)
    if token is None:
        return [f"API token {cfg['token_name']} is not configured"]
    rows = _api(http, cfg, token, 'GET', '/billable-usage')
    if not isinstance(rows, list):
        raise UsageUnavailable('usage result is not a list')
    owner = {name: entry['key'] for entry in cfg['allowances'] for name in entry['service_names']}
    seen: dict[str, dict] = {}
    for record in rows:
        if isinstance(record, dict) and isinstance(record.get('ServiceName'), str):
            s = seen.setdefault(record['ServiceName'], {'family': record.get('ServiceFamilyName'), 'units': set(),
                                                        'consumed': 0.0})
            s['units'].add(record.get('ConsumedUnit') or '')
            s['consumed'] += record.get('ConsumedQuantity') or 0
    return [f"{name} | family {s['family']} | unit {sorted(s['units'])} | consumed {s['consumed']:,.0f} | "
            f"{owner.get(name, 'UNMAPPED')}" for name, s in sorted(seen.items())]


def e2e_gate(cfg: dict, *, now: datetime, state_dir=None):
    """(allowed, reason). Every E2E dispatch calls this first; anything but a fresh, data-backed CLEAR holds."""
    try:
        receipt = read_receipt(_state_dir(cfg, state_dir))
    except (ValueError, OSError) as exc:
        return False, f'HOLD: spend-guard receipt unreadable ({describe(exc)})'
    if receipt is None:
        return False, 'HOLD: no spend-guard receipt; the guard has not run'
    evaluation = receipt.get('last_evaluation')
    if receipt.get('state') != 'CLEAR':
        evaluation = evaluation or {}
        return False, (f"HOLD: receipt STOPPED (hold {receipt.get('hold')}, since {receipt.get('hold_since')}); "
                       f"last verdict {evaluation.get('verdict')}: {'; '.join(evaluation.get('reasons') or [])}")
    if not evaluation:
        return False, 'HOLD: no spend evaluation since the last restore (or ever); run the guard'
    if evaluation.get('verdict') not in ('OK', 'WARN'):
        return False, f"HOLD: last verdict {evaluation.get('verdict')}: {'; '.join(evaluation.get('reasons') or [])}"
    try:
        age = now - _time(evaluation.get('at'), 'last_evaluation.at')
        basis = _time(evaluation.get('data_basis_at'), 'last_evaluation.data_basis_at')
    except UsageUnavailable as exc:
        return False, f'HOLD: receipt incomplete ({exc})'
    if age < -CLOCK_SKEW:
        return False, f"HOLD: last evaluation is {round(-age.total_seconds() / 60)} min in the future; check the clock"
    if age > timedelta(hours=cfg['e2e_max_evaluation_age_hours']):
        return False, f"HOLD: last spend evaluation is {round(age.total_seconds() / 3600)}h old"
    if now - basis > timedelta(hours=cfg['max_usage_age_hours']):
        return False, (f"HOLD: the usage data behind the last CLEAR ends {evaluation.get('data_basis_at')}, older than "
                       f"{cfg['max_usage_age_hours']}h")
    last_fast = receipt.get('last_fast') or {}
    if last_fast and last_fast.get('verdict') not in ('OK', 'WARN'):
        return False, f"HOLD: fast poll verdict {last_fast.get('verdict')}: {'; '.join(last_fast.get('reasons') or [])}"
    return True, f"CLEAR: last verdict {evaluation.get('verdict')} at {evaluation.get('at')}"


def bound_action(cfg: dict) -> str:
    production = 'also stopped' if cfg['stop_production_workers'] else 'report-only'
    return (f"on breach: STOP disables workers.dev on {', '.join(cfg['staging_workers'])} and writes the STOPPED "
            "receipt E2E dispatch must pass; RUNAWAY disables only the runaway staging Worker; UNKNOWN writes the "
            f"receipt only; production {production} · owner joe · remediation open Cloudflare Billing > Billable "
            "Usage or Workers analytics, cut the named driver, then run ./run.sh cloudflare-spend-guard restore at a "
            "terminal · verify the next guard run prints OK or WARN and ./run.sh cloudflare-spend-guard check-e2e "
            "exits 0 · auto-clear UNKNOWN on the next readable run; STOP and RUNAWAY only by restore")


def health_line(receipt, cfg: dict | None = None) -> str:
    cfg = cfg or load_config()
    if not receipt or not receipt.get('last_evaluation'):
        return f'UNKNOWN cloudflare spend · guard has not run since setup or the last restore; E2E held · {bound_action(cfg)}'
    ev = receipt['last_evaluation']
    parts = []
    if ev.get('metered_usd') is not None:
        parts.append(f"metered {_usd(ev['metered_usd'])}")
    if ev.get('peak_allowance'):
        parts.append(f"peak {ev['peak_allowance']['key']} {ev['peak_allowance']['fraction']:.0%}")
    parts.append(f"data through {ev.get('data_through') or 'n/a'}")
    parts.extend(ev.get('reasons') or [])
    last_fast = receipt.get('last_fast')
    if last_fast:
        parts.append(f"fast {last_fast.get('verdict')} since {last_fast.get('since')}")
    disabled = [f"{w['script']} ({w.get('status', 'confirmed')})" for w in receipt.get('disabled_workers') or []]
    parts.append(f"workers.dev disabled: {', '.join(disabled) if disabled else 'none'}")
    if ev.get('action_errors'):
        parts.append(f"action errors: {'; '.join(ev['action_errors'])}")
    held = 'held' if receipt.get('state') != 'CLEAR' else 'clear'
    return f"{ev['verdict']} cloudflare spend · {'; '.join(parts)}; E2E {held} · {bound_action(cfg)}"


def health_row(cfg: dict, *, state_dir=None) -> tuple[str, bool, bool]:
    """(line, failed, not_run) for tools/health-check.py. Reads only the local receipt."""
    try:
        receipt = read_receipt(_state_dir(cfg, state_dir))
    except (ValueError, OSError) as exc:
        return f'UNKNOWN cloudflare spend · receipt unreadable ({describe(exc)}) · {bound_action(cfg)}', True, False
    line = health_line(receipt, cfg)
    if not receipt or not receipt.get('last_evaluation'):
        return line, True, True
    last_fast = receipt.get('last_fast') or {}
    failed = (receipt.get('state') != 'CLEAR' or receipt['last_evaluation'].get('verdict') not in ('OK', 'WARN')
              or bool(last_fast and last_fast.get('verdict') not in ('OK', 'WARN')))
    return line, failed, False


# ── command line ─────────────────────────────────────────────────────────────

def parse_args(argv):
    parser = argparse.ArgumentParser(prog='run.sh cloudflare-spend-guard', description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('run', help='daily: read billable usage, decide, act on staging, write the receipt')
    sub.add_parser('fast', help='15-minute poll: one GraphQL request, runaway detector, act on change')
    restore_parser = sub.add_parser('restore', help='at a terminal: re-enable what the guard disabled, lift the hold')
    restore_parser.add_argument('--ack-quarantine', metavar='FILE',
                                help='the quarantined receipt you inspected (needed for a receipt-corrupt hold)')
    sub.add_parser('check-e2e', help='run the guard (when a token exists), then exit 0 only if E2E may proceed')
    sub.add_parser('health', help='print the last evaluation as a health row (no network)')
    sub.add_parser('map-services', help='dry run: list Billable Usage service names and their mapping; writes nothing')
    sub.add_parser('loop-watch', help='one local dead-loop scan (tools/cloudflare_loop_watch.py); no network')
    return parser.parse_args(argv)


def _report_actions(errors):
    for error in errors:
        print(f'ACTION FAILED: {error} ({DEPRECATED_NOTE})', file=sys.stderr)


def _exit_for(verdict, action_errors):
    if verdict in ('STOP', 'RUNAWAY') and action_errors:
        return EXIT_CODES['ACTION_FAILED']
    return EXIT_CODES[verdict]


def main(argv=None, *, http=None, token_reader=None, now=None, state_dir=None, cfg=None, isatty=None,
         prompt=None, spawn_reporter=None) -> int:
    """Keyword arguments exist for tests only; the command line has no threshold or target overrides."""
    try:
        return _main(sys.argv[1:] if argv is None else argv, http=http or urllib_http,
                     token_reader=token_reader or read_token, now=now or datetime.now(timezone.utc),
                     state_dir=state_dir, cfg=cfg, isatty=isatty or (lambda: sys.stdin.isatty()),
                     prompt=prompt or input, spawn_reporter=spawn_reporter)
    except Exception as exc:  # noqa: BLE001 — a crash is UNKNOWN (exit 3), never mistaken for WARN
        print(f'cloudflare spend guard crashed: {describe(exc)}; treat as UNKNOWN', file=sys.stderr)
        return EXIT_CODES['UNKNOWN']


def _main(argv, *, http, token_reader, now, state_dir, cfg, isatty, prompt, spawn_reporter) -> int:
    args = parse_args(argv)
    if args.command == 'loop-watch':
        return _loop_watch().main(['scan'])
    cfg = cfg or load_config()
    state = _state_dir(cfg, state_dir)
    if args.command == 'run':
        result = run(cfg, http=http, token=token_reader(cfg), now=now, state_dir=state, spawn_reporter=spawn_reporter)
        print(health_line(read_receipt(state), cfg))
        _report_actions(result['action_errors'])
        return _exit_for(result['verdict'], result['action_errors'])
    if args.command == 'fast':
        result = fast(cfg, http=http, token=token_reader(cfg), now=now, state_dir=state, spawn_reporter=spawn_reporter)
        print(result['line'])
        _report_actions(result['action_errors'])
        return _exit_for(result['verdict'], result['action_errors'])
    if args.command == 'restore':
        if not isatty():
            print('restore needs an interactive terminal and a typed confirmation; nothing changed', file=sys.stderr)
            return 1
        try:
            receipt = read_receipt(state)
        except ValueError:
            receipt = None
        if receipt:
            print(f"hold {receipt.get('hold')} since {receipt.get('hold_since')}; workers to restore: "
                  f"{', '.join(w['script'] for w in receipt.get('disabled_workers') or []) or 'none'}")
            if receipt.get('quarantined_file'):
                print(f"quarantined receipt: {state / receipt['quarantined_file']}")
        typed = prompt(f'Type "{RESTORE_PHRASE}" to continue: ')
        if (typed or '').strip() != RESTORE_PHRASE:
            print('restore cancelled; nothing changed', file=sys.stderr)
            return 1
        result = restore(cfg, http=http, token=token_reader(cfg), now=now, state_dir=state,
                         ack_quarantine=args.ack_quarantine)
        print(f"restore: {result['message']}")
        if result['quarantined_file']:
            print(f"quarantined receipt (kept for inspection): {state / result['quarantined_file']}")
        for note in result['notes']:
            print(f'note: {note}')
        for error in result['errors']:
            print(f'error: {error}', file=sys.stderr)
        return 0 if result['restored'] else 1
    if args.command == 'check-e2e':
        token = token_reader(cfg)
        if not clean_token(token):
            print(f"HOLD: {cfg['token_name']} not configured; cannot evaluate spend")
            return 1
        run(cfg, http=http, token=token, now=now, state_dir=state, spawn_reporter=spawn_reporter)
        allowed, reason = e2e_gate(cfg, now=now, state_dir=state)
        print(reason)
        return 0 if allowed else 1
    if args.command == 'map-services':
        for line in map_services(cfg, http=http, token=token_reader(cfg), now=now):
            print(line)
        return 0
    line, _, _ = health_row(cfg, state_dir=state)
    print(line)
    try:
        evaluation = (read_receipt(state) or {}).get('last_evaluation') or {}
    except ValueError:
        evaluation = {}
    return EXIT_CODES[evaluation.get('verdict', 'UNKNOWN')]


if __name__ == '__main__':
    sys.exit(main())
