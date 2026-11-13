#!/usr/bin/env python3
"""Observe scheduled social delivery and retain evidence for the watchdog digest."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'lib'), str(ROOT / 'tools')]
from local_jobs import locked
from social_batch import ACCOUNTS, API, Blotato, Stop, http_transport, ledger_rows, parse_utc, stamp
from datetime import datetime, timezone

INTERVAL_SECONDS = 86400
MAX_VISIBILITY_ATTEMPTS = 2
RECEIPT = 'delivery.json'
PUBLIC_HOSTS = {'linkedin.com', 'www.linkedin.com', 'x.com', 'www.x.com',
                'twitter.com', 'www.twitter.com', 'facebook.com', 'www.facebook.com',
                'instagram.com', 'www.instagram.com'}


def public_url(url):
    if not isinstance(url, str):
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
        return (parsed.scheme == 'https' and parsed.hostname in PUBLIC_HOSTS
                and parsed.port in (None, 443) and not parsed.username and not parsed.password)
    except (ValueError, TypeError):
        return False


def discover(out, accounts):
    posts = {}
    errors = []
    try:
        rows = ledger_rows(out)
        for row in rows:
            if row.get('event') != 'posted':
                continue
            parse_utc(row['utc'])
            key = row['week'] + '/' + row['draft']
            posts[key] = {'platform': row['platform'], 'account': accounts.get(row['platform']),
                          'submission_id': row.get('submission_id'), 'topic': row['draft'],
                          'original_fire_time': row['utc']}
        for path in sorted(out.glob('*/batch.json')):
            for row in json.loads(path.read_text())['posts']:
                key = path.parent.name + '/' + row['draft']
                if key in posts:
                    posts[key]['topic'] = row.get('text', row['draft']).split('\n')[0][:100]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        errors.append('batch discovery is incomplete or malformed')
    if not posts:
        errors.append('no scheduled-post evidence in batch logs')
    if any(not p['account'] for p in posts.values()):
        errors.append('account identity is unavailable')
    return posts, errors


def atomic_receipt(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.delivery-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(value, handle, sort_keys=True)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Deferred(Exception):
    pass


def check(out, transport, *, accounts, now=time.time, sleep=time.sleep,
          interval_seconds=INTERVAL_SECONDS, max_requests=40):
    if interval_seconds <= 0 or not 1 <= max_requests <= 40:
        raise ValueError('positive interval and request cap from 1 to 40 required')
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    with locked(out / 'delivery.lock'):
        started = now()
        path = out / RECEIPT
        try:
            prior = json.loads(path.read_text()) if path.exists() else {}
            if prior and prior.get('schema') != 'carr-social-delivery/v1':
                raise ValueError('unsupported receipt')
        except (ValueError, OSError):
            prior = {}
        source, errors = discover(out, accounts)
        posts = dict(prior.get('posts', {}))
        requests, failures = 0, 0
        last_request = prior.get('last_request_at', 0)
        backoff = prior.get('backoff_until', 0)
        stop_reason = 'rate_limited' if started < backoff else None

        def send(method, url, headers, data):
            nonlocal requests, failures, last_request, backoff, stop_reason
            if stop_reason or requests >= max_requests or now() - started >= 109:
                raise Deferred()
            if method not in ('GET', 'HEAD') or data is not None:
                raise ValueError('observer permits reads only')
            pause = max(0, 1.1 - (now() - last_request))
            if pause:
                sleep(pause)
            if now() - started >= 109:
                raise Deferred()
            requests += 1
            last_request = now()
            try:
                status, raw = transport(method, url, headers, data)
            except (Stop, OSError, TimeoutError):
                status, raw = 503, b'{}'
            if status == 429 and url.startswith(API + '/'):
                stop_reason, backoff = 'rate_limited', now() + 60
            elif status in (401, 403) and url.startswith(API + '/'):
                stop_reason = 'authentication'
            if url.startswith(API + '/'):
                failures = failures + 1 if status >= 500 else 0
            if failures >= 3:
                stop_reason = 'transport_failures'
            if stop_reason:
                raise Deferred()
            return status, raw

        api = Blotato(send)
        deferred = []

        def visibility(post, key):
            if post.get('visibility') in ('verified', 'cannot_verify'):
                return
            if not public_url(post.get('publicUrl')):
                post['visibility'] = 'cannot_verify'
                return
            if post.get('visibility_attempts', 0) >= MAX_VISIBILITY_ATTEMPTS:
                post['visibility'] = 'cannot_verify'
                return
            before = requests
            try:
                code, _ = send('HEAD', post['publicUrl'], {}, None)
                post['visibility_attempts'] = post.get('visibility_attempts', 0) + 1
                post['visibility'] = ('verified' if code == 200 else 'cannot_verify'
                                      if post['visibility_attempts'] >= MAX_VISIBILITY_ATTEMPTS else 'unverified')
            except Deferred:
                if requests > before:
                    post['visibility_attempts'] = post.get('visibility_attempts', 0) + 1
                    if post['visibility_attempts'] >= MAX_VISIBILITY_ATTEMPTS:
                        post['visibility'] = 'cannot_verify'
                if post.get('visibility') != 'cannot_verify':
                    deferred.append(key + ':visibility')

        for key in sorted(source, key=lambda k: posts.get(k, {}).get('last_status_attempt_at') or posts.get(k, {}).get('observed_at') or ''):
            base = source[key]
            previous = posts.get(key, {})
            if previous.get('submission_id') != base['submission_id']:
                previous = {}
            if previous.get('status') in ('published', 'cancelled'):
                continue
            post = {**base, 'current_fire_time': previous.get('current_fire_time', base['original_fire_time']),
                    'observed_at': previous.get('observed_at'), 'status': 'not_due',
                    'visibility': 'unverified', 'publicUrl': None, 'errorMessage': None}
            if parse_utc(post['current_fire_time']).timestamp() > started:
                post['status'] = 'moved' if previous.get('status') == 'moved' else 'not_due'
                posts[key] = post
                continue
            post['status'] = 'unresolved'
            if not base['submission_id'] or not base['account']:
                post['observed_at'] = stamp(datetime.fromtimestamp(now(), timezone.utc))
                post['errorMessage'] = 'missing submission ID or account; delivery cannot be verified'
            else:
                before = requests
                try:
                    if not isinstance(base['submission_id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]+', base['submission_id']):
                        raise ValueError('invalid submission ID')
                    value = api.status(base['submission_id'])
                    post['observed_at'] = stamp(datetime.fromtimestamp(now(), timezone.utc))
                    post['publisher_status'] = value.get('status') if value else None
                    if value is None:
                        post['status'] = 'cancelled'
                    if value:
                        post['publicUrl'] = value.get('publicUrl')
                        post['errorMessage'] = value.get('errorMessage')
                        status = value['status']
                        if status in ('published', 'failed', 'cancelled'):
                            post['status'] = status
                        elif status == 'scheduled' and value.get('scheduledTime'):
                            when = parse_utc(value['scheduledTime'])
                            post['current_fire_time'] = value['scheduledTime']
                            if when.timestamp() > now():
                                post['status'] = 'moved'
                    if post['status'] == 'unresolved':
                        post['errorMessage'] = post['errorMessage'] or 'missing or ambiguous publisher outcome'
                except Deferred:
                    deferred.append(key)
                    post = dict(previous) if previous else {**post, 'status': 'deferred', 'errorMessage': None}
                except (Stop, ValueError, TypeError):
                    post['observed_at'] = stamp(datetime.fromtimestamp(now(), timezone.utc))
                    post['errorMessage'] = 'publisher status unavailable or malformed'
                if requests > before:
                    post['last_status_attempt_at'] = stamp(datetime.fromtimestamp(now(), timezone.utc))
            posts[key] = post
        for key in source:
            post = posts.get(key, {})
            if post.get('status') == 'published':
                visibility(post, key)
        result = {'schema': 'carr-social-delivery/v1', 'observed_at': started,
                  'interval_seconds': interval_seconds, 'coverage': 'unknown' if errors else 'observed',
                  'errors': errors, 'posts': posts, 'deferred': deferred, 'requests': requests,
                  'stop_reason': stop_reason, 'backoff_until': backoff, 'last_request_at': last_request}
        atomic_receipt(path, result)
        return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'out/social-batch')
    parser.add_argument('--interval-seconds', type=int, default=INTERVAL_SECONDS)
    args = parser.parse_args(argv)
    accounts = json.loads(ACCOUNTS.read_text())
    opener = urllib.request.build_opener(NoRedirect)
    transport = http_transport(opener=lambda request, timeout: opener.open(request, timeout=10))
    result = check(args.out, transport, accounts=accounts, interval_seconds=args.interval_seconds)
    print(json.dumps({'coverage': result['coverage'], 'requests': result['requests'],
                      'deferred': len(result['deferred']), 'stop_reason': result['stop_reason']}))
    return 0 if result['coverage'] == 'observed' and not result['stop_reason'] and not result['deferred'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
