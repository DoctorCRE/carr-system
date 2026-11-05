#!/usr/bin/env python3
"""Observe bounded vendor trust evidence without writes or private output."""
import argparse
from datetime import datetime, timezone
import http.cookiejar
import json
import math
from pathlib import Path
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 1048576
SAFE_INTEGER = 9007199254740991


class Unavailable(Exception):
    pass


class Contradiction(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Unavailable('redirect_refused')


def origin(value):
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme not in {'https', 'http'} or not parsed.hostname or parsed.username
            or parsed.password or parsed.path not in {'', '/'} or parsed.query or parsed.fragment
            or parsed.scheme == 'http' and parsed.hostname not in {'localhost', '127.0.0.1', '::1'}):
        raise Unavailable('origin_invalid')
    return value.rstrip('/')


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d))?', value):
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).replace(
            tzinfo=timezone.utc) if len(value) == 10 else datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def count(value):
    return type(value) is int and 0 <= value <= SAFE_INTEGER


def check_relationship(stats, now):
    if not isinstance(stats, dict):
        raise Unavailable('relationship_missing')
    coverage = timestamp(stats.get('coverage_verified_at'))
    attested = coverage is not None and coverage <= now
    tier = stats.get('computed_tier')
    fields = ['deals_worked', 'deals_referred', 'won', 'lost']
    if tier == 'Unrated':
        if any(stats.get(key) is not None for key in [*fields, 'win_rate']):
            raise Contradiction('unrated_statistics_exposed')
        return 'unverified'
    if tier not in {'Trial', 'Established', 'Proven'}:
        raise Unavailable('tier_missing')
    if (not attested or not all(count(stats.get(key)) for key in fields)
            or stats['won'] + stats['lost'] > stats['deals_worked']):
        raise Contradiction('rated_evidence_invalid')
    resolved = stats['won'] + stats['lost']
    rate = stats.get('win_rate')
    if resolved == 0:
        if rate is not None:
            raise Contradiction('unresolved_rate_exposed')
    elif (type(rate) not in {int, float} or not math.isfinite(rate) or not 0 <= rate <= 1
          or not math.isclose(rate, stats['won'] / resolved, rel_tol=1e-12, abs_tol=1e-12)):
        raise Contradiction('rate_invalid')
    return 'rated'


def cookie_jar(path):
    if path.stat().st_size > MAX_BYTES:
        raise Unavailable('session_too_large')
    state = json.loads(path.read_text())
    if not isinstance(state, dict) or not isinstance(state.get('cookies'), list) or not state['cookies']:
        raise Unavailable('session_cookies_missing')
    jar = http.cookiejar.CookieJar(policy=http.cookiejar.DefaultCookiePolicy(
        strict_ns_domain=http.cookiejar.DefaultCookiePolicy.DomainStrictNonDomain))
    for row in state['cookies']:
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ['name', 'value', 'domain', 'path']):
            raise Unavailable('session_cookie_invalid')
        domain = row['domain']
        expires = row.get('expires', -1)
        if type(expires) not in {int, float} or not math.isfinite(expires):
            raise Unavailable('session_cookie_invalid')
        jar.set_cookie(http.cookiejar.Cookie(
            0, row['name'], row['value'], None, False, domain, domain.startswith('.'),
            domain.startswith('.'), row['path'], True, row.get('secure', True),
            int(expires) if expires > 0 else None, expires <= 0, None, None, {}, False))
    return jar


def observe(args):
    app, carr = origin(args.origin), origin(args.carr_origin)
    jar = cookie_jar(args.storage_state)
    public = urllib.request.build_opener(NoRedirect())
    authenticated = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPCookieProcessor(jar))
    deadline = time.monotonic() + args.timeout_seconds

    def get(base, path, opener):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Unavailable('deadline_exceeded')
        with opener.open(base + path, timeout=remaining) as response:
            data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise Unavailable('response_too_large')
        value = json.loads(data)
        if not isinstance(value, dict):
            raise Unavailable('response_invalid')
        return value

    app_identity = get(app, '/app-release', public)
    app_release = app_identity.get('source_commit')
    worker = get(carr, '/release', public)
    worker_release = worker.get('git_sha', {}).get('value')
    if (app_identity.get('service') != 'doctorcre-app' or app_identity.get('environment') != 'production'
            or worker.get('ok') is not True or worker.get('env', {}).get('value') != 'production'):
        raise Unavailable('release_identity_invalid')
    if not all(isinstance(sha, str) and re.fullmatch('[0-9a-f]{40}', sha) for sha in [app_release, worker_release]):
        raise Unavailable('release_identity_missing')
    totals = {'rated': 0, 'unverified': 0, 'rows': 0, 'pages': 0}
    now = datetime.now(timezone.utc)
    for page in range(1, 6):
        payload = get(app, '/api/v1/business/vendors?contract=vendor-directory.v1&scope=team&page=' + str(page), authenticated)
        rows = payload.get('rows')
        if (not isinstance(rows, list) or len(rows) > 25 or payload.get('page') != page
                or payload.get('page_size') != 25 or not count(payload.get('page_count')) or payload['page_count'] < page):
            raise Unavailable('pagination_invalid')
        totals['pages'] += 1
        for row in rows[:100 - totals['rows']]:
            if not isinstance(row, dict):
                raise Unavailable('vendor_invalid')
            totals[check_relationship(row.get('relationship'), now)] += 1
            totals['rows'] += 1
        if page >= payload['page_count'] or totals['rows'] >= 100:
            break
    result = {'app_release': app_release, 'carr_release': worker_release, **totals}
    if not totals['rated'] or not totals['unverified']:
        raise Unavailable('required_populations_missing')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--carr-origin', required=True)
    parser.add_argument('--storage-state', type=Path, required=True)
    parser.add_argument('--timeout-seconds', type=float, default=30)
    args = parser.parse_args()
    if not 0 < args.timeout_seconds <= 30:
        parser.error('timeout must be greater than zero and at most thirty seconds')

    def timeout(*_):
        raise Unavailable('deadline_exceeded')

    signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, args.timeout_seconds)
    try:
        result = observe(args)
        print(json.dumps({'status': 'observed', **result}))
        return 0
    except Contradiction as error:
        print(json.dumps({'status': 'contradiction', 'reason': str(error)}))
        return 1
    except (Unavailable, OSError, ValueError, KeyError, TypeError, AttributeError):
        print(json.dumps({'status': 'unavailable', 'reason': 'evidence_or_authorization_unavailable'}))
        return 78
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == '__main__':
    sys.exit(main())
