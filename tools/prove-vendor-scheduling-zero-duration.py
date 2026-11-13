#!/usr/bin/env python3
"""Observe natural zero-duration calendar evidence without publishing or sending."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import os
import re
import signal
import selectors
import subprocess
import sys
import time
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.release_reader import read as read_http

MAX_BYTES = 1048576


class Insufficient(Exception):
    pass


def instant(value):
    if not isinstance(value, str):
        raise Insufficient()
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise Insufficient()
    return parsed.timestamp()

def observe(args, receipt):
    deadline = time.monotonic() + args.timeout_seconds

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise Insufficient()
        return value

    def decode(raw):
        if len(raw) > MAX_BYTES:
            raise Insufficient()
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get('error') or value.get('isError'):
            raise Insufficient()
        return value

    def release():
        response = read_http(args.origin.rstrip('/') + '/release', timeout=remaining(), max_bytes=MAX_BYTES)
        if response.status != 200:
            raise OSError('release_unavailable')
        value = decode(response.body)
        sha = value.get('git_sha', {}).get('value')
        if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha):
            raise Insufficient()
        return sha

    def call(verb, payload):
        receipt['record_reads'] += 1
        with subprocess.Popen([args.call_wrapper, 'call', verb, json.dumps(payload)],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True) as process:
            try:
                output = bytearray()
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        if not selector.select(timeout=remaining()):
                            raise Insufficient()
                        chunk = os.read(process.stdout.fileno(), min(65536, MAX_BYTES + 1 - len(output)))
                        if not chunk:
                            break
                        output.extend(chunk)
                        if len(output) > MAX_BYTES:
                            raise Insufficient()
                if process.wait(timeout=remaining()):
                    raise Insufficient()
                return decode(output)
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    receipt['release_sha'] = release()
    vendor = call('read-vendor-meetings', {'view': 'research', 'limit': 1})
    agenda = call('morning-brief', {}).get('sections', {}).get('calendar')
    if release() != receipt['release_sha']:
        receipt['status'] = 'release_changed'
        return 78
    calendar = vendor.get('calendar')
    if vendor.get('schema') != 'vendor-meetings.v1' or not isinstance(calendar, dict) or not isinstance(agenda, dict):
        raise Insufficient()
    scheduling = calendar.get('scheduling')
    if not isinstance(scheduling, dict):
        raise Insufficient()
    for section in (calendar, agenda):
        if (section.get('state') != 'ready' or section.get('malformed_items') or section.get('notes')
                or section.get('sources', {}).get('outlook_calendar', {}).get('state') != 'ready'):
            raise Insufficient()
    observed = instant(calendar.get('observed_at'))
    if instant(agenda.get('observed_at')) != observed:
        raise Insufficient()
    window = scheduling.get('window', {})
    start, end = instant(window.get('start')), instant(window.get('end'))
    now = datetime.now(timezone.utc).timestamp()
    if not 0 <= now - start <= args.timeout_seconds or end - start != 86400 or not 0 <= start - observed <= 18 * 3600:
        raise Insufficient()
    rows = agenda.get('items')
    vendors = vendor.get('items')
    if not isinstance(rows, list) or not isinstance(vendors, list) or len(vendors) > 1 or len(rows) + len(vendors) > 20:
        raise Insufficient()
    receipt['records_examined'] = len(rows) + len(vendors)
    eligible = 0
    for row in rows:
        if not isinstance(row, dict) or row.get('time_changed') or row.get('conflict'):
            raise Insufficient()
        first, last = instant(row.get('start')), instant(row.get('end'))
        if last < first or (row.get('all_day') and last == first):
            raise Insufficient()
        if not row.get('all_day') and start < first < end and first == last:
            eligible += 1
    receipt['eligible_records'] = eligible
    if not eligible:
        raise Insufficient()
    receipt['assertions'] = 2
    passed = scheduling.get('state') == 'unavailable' and scheduling.get('items') == []
    receipt['passed_assertions'] = int(scheduling.get('state') == 'unavailable') + int(scheduling.get('items') == [])
    receipt['status'] = 'passed' if passed else 'contradiction'
    return 0 if passed else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--call-wrapper', required=True)
    parser.add_argument('--timeout-seconds', type=float, default=30)
    args = parser.parse_args()
    origin = urllib.parse.urlsplit(args.origin)
    if (origin.scheme not in {'http', 'https'} or not origin.netloc or origin.username or origin.password
            or origin.query or origin.fragment or origin.path not in {'', '/'} or not 0 < args.timeout_seconds <= 30):
        parser.error('supply an HTTP origin and a timeout greater than zero and at most thirty seconds')
    receipt = {'schema': 'vendor-scheduling-zero-duration-proof.v1', 'status': 'insufficient_natural_evidence',
               'record_reads': 0, 'records_examined': 0, 'eligible_records': 0, 'assertions': 0, 'passed_assertions': 0}

    def expired(signum, frame):
        raise TimeoutError()

    previous = signal.signal(signal.SIGALRM, expired)
    try:
        try:
            signal.setitimer(signal.ITIMER_REAL, args.timeout_seconds)
            code = observe(args, receipt)
        finally:
            try:
                signal.setitimer(signal.ITIMER_REAL, 0)
            finally:
                signal.signal(signal.SIGALRM, previous)
    except (Insufficient, OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        code = 78
    print(json.dumps(receipt, sort_keys=True))
    return code


if __name__ == '__main__':
    sys.exit(main())
