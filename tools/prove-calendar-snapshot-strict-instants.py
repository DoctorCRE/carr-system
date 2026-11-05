#!/usr/bin/env python3
"""Observe calendar timestamp contracts. Fixture evidence never attests production."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from proof_release import read_release, release_revision

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 1048576
STAMP = re.compile(r'([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.[0-9]+)?(?:Z|[+-]([0-9]{2}):?([0-9]{2}))\Z')
FIXTURE = {
    'release': {'ok': True, 'env': {'value': 'production'}, 'git_sha': {'value': '1' * 40}},
    'expected_revision': '1' * 40,
    'calendar': {'state': 'partial', 'observed_at': '2026-03-02T10:00:00Z',
                 'sources': {'outlook_calendar': {'state': 'partial'}},
                 'notes': ['1 stored meeting could not be shown.'],
                 'items': [{'start': '2026-03-02T12:00:00Z', 'end': '2026-03-02T13:00:00Z',
                            'times': ['2026-03-02T12:00:00.123456+01:30']}],
                 'follow_up_reviews': [{'start': '2026-03-02T08:00:00Z', 'end': '2026-03-02T09:00:00Z'}],
                 'malformed_items': 1},
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def instant(value):
    require(isinstance(value, str), 'instant must be a string')
    match = STAMP.fullmatch(value)
    require(match is not None, 'instant requires an explicit zone')
    year, month, day, hour, minute, second = map(int, match.groups()[:6])
    require(int(match[7] or 0) <= 23 and int(match[8] or 0) <= 59, 'invalid offset')
    datetime(year, month, day, hour, minute, second)
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def validate_calendar(value, now):
    require(isinstance(value, dict), 'invalid calendar contract')
    state = value.get('state')
    require(state in ('ready', 'partial', 'empty'), 'calendar source unavailable')
    observed = instant(value.get('observed_at'))
    items = value.get('items')
    reviews = value.get('follow_up_reviews', [])
    notes = value.get('notes')
    require(isinstance(items, list) and len(items) <= 100, 'invalid meeting collection')
    require(isinstance(reviews, list) and len(reviews) <= 100, 'invalid review collection')
    require(isinstance(notes, list) and all(isinstance(n, str) and n.strip() for n in notes), 'invalid coverage notes')
    require((state == 'partial') == bool(notes), 'coverage notes disagree with state')
    require(state != 'ready' or bool(items), 'ready calendar lacks meetings')
    require(state != 'empty' or not items, 'empty calendar contains meetings')
    source = value.get('sources', {}).get('outlook_calendar', {}).get('state')
    require(source in ('ready', 'partial', 'stale'), 'missing usable calendar source')
    stale = (now - observed).total_seconds() > 18 * 3600
    future = observed > now
    require(not stale or source == 'stale' and state == 'partial' and any('18 hours' in n for n in notes), 'stale coverage not disclosed')
    require(not future or state == 'partial' and any('future' in n for n in notes), 'future coverage not disclosed')
    require(source == 'ready' or state == 'partial', 'source gap not disclosed')
    malformed = value.get('malformed_items', 0)
    require(type(malformed) is int and malformed >= 0, 'invalid malformed count')
    require(not malformed or state == 'partial' and any('could not be shown' in n for n in notes), 'malformed coverage not disclosed')
    alternatives = 0
    for row in items + reviews:
        require(isinstance(row, dict), 'invalid calendar row')
        start, end = instant(row.get('start')), instant(row.get('end'))
        require(end >= start, 'calendar interval reversed')
    for row in items:
        require(instant(row['end']) > now and (instant(row['start']) - now).total_seconds() < 7 * 86400, 'meeting outside calendar horizon')
        times = row.get('times', [])
        require(isinstance(times, list), 'invalid alternate collection')
        for stamp in times:
            instant(stamp)
        alternatives += len(times)
    for row in reviews:
        start, end = instant(row['start']), instant(row['end'])
        require(end > start and end <= observed and end <= now and (now - end).total_seconds() < 86400,
                'review outside observation window')
    require(not reviews or not stale and not future, 'reviews require current observation')
    return {'meetings': len(items), 'reviews': len(reviews), 'alternate_times': alternatives,
            'malformed_items': malformed, 'state': state}


def command(argv, deadline):
    remaining = deadline - time.monotonic()
    require(remaining > 0, 'total deadline exceeded')
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(argv, cwd=ROOT, stdout=output, stderr=errors, start_new_session=True)
        try:
            code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise ValueError('total deadline exceeded') from None
        require(time.monotonic() <= deadline, 'total deadline exceeded')
        require(code == 0, 'read failed or authentication refused')
        require(output.tell() <= MAX_BYTES, 'response exceeds one MiB')
        output.seek(0)
        return output.read().decode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--live', action='store_true', help='Read production release and one calendar through ./run.sh call.')
    modes.add_argument('--fixture', action='store_true', help='Use committed synthetic responses and a stub local verb process.')
    parser.add_argument('--timeout-seconds', type=float, default=30, help='Total deadline, greater than zero and at most 30.')
    parser.add_argument('--expected-revision', help='Full merged source SHA. Defaults to checkout HEAD for live proof.')
    parser.add_argument('--fixture-file', type=Path, help='Synthetic release/calendar JSON override. Requires --fixture.')
    parser.add_argument('--fixture-wrapper', type=Path, help='Stub run.sh executable. Requires --fixture.')
    args = parser.parse_args()
    if not 0 < args.timeout_seconds <= 30:
        parser.error('timeout must be greater than zero and at most 30')
    if args.live and (args.fixture_file or args.fixture_wrapper):
        parser.error('fixture options require --fixture')
    deadline = time.monotonic() + args.timeout_seconds
    try:
        if args.fixture:
            fixture = json.loads(args.fixture_file.read_text()) if args.fixture_file else FIXTURE
            release = fixture['release']
            expected = args.expected_revision or fixture['expected_revision']
            now = datetime(2026, 3, 2, 11, tzinfo=timezone.utc)
            if args.fixture_wrapper:
                calendar_command = [str(args.fixture_wrapper.resolve()), 'call', 'calendar-meeting-prep', '{}']
            else:
                calendar_command = [sys.executable, '-c', 'import sys; print(sys.argv[1])', json.dumps(fixture['calendar'])]
        else:
            expected = args.expected_revision or command(['git', 'rev-parse', 'HEAD'], deadline).strip()
            release = read_release(lambda argv: command(argv, deadline), lambda: deadline - time.monotonic())
            calendar_command = [str(ROOT / 'run.sh'), 'call', 'calendar-meeting-prep', '{}']
            now = datetime.now(timezone.utc)
        deployed = release_revision(release)
        require(isinstance(expected, str) and re.fullmatch('[a-f0-9]{40}', expected), 'invalid expected revision')
        if args.fixture:
            require(expected == deployed, 'fixture release revision mismatch')
        else:
            command(['git', 'merge-base', '--is-ancestor', expected, deployed], deadline)
        raw = command(calendar_command, deadline)
        counts = validate_calendar(json.loads(raw), now)
        require(time.monotonic() <= deadline, 'total deadline exceeded')
        print(json.dumps({'result': 'PASS', 'mode': 'fixture' if args.fixture else 'live',
                          'git_sha': deployed, 'calendar_reads': 1, **counts,
                          'rollover_evidence': 'separate synthetic Node regression only'}))
        return 0
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as error:
        print(json.dumps({'result': 'FAIL', 'reason': str(error) if isinstance(error, ValueError) else type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
