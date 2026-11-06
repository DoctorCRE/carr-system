#!/usr/bin/env python3
"""Observe live feed coverage and separately replay the release-pinned projection."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.release_reader import read as read_http
SOURCE = 'mcp-server/src/notifications.ts'
MAX_BYTES = 1048576

REPLAY = r'''
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const { notificationFeedProjection: feed, notificationPreferenceProjection: preference } =
  await import(pathToFileURL(process.argv[1]));
class ToolError extends Error {
  constructor(fields) { super(fields.error); Object.assign(this, fields); }
}
let input = '';
for await (const chunk of process.stdin) input += chunk;
const live = JSON.parse(input);
const prefs = quiet_now => ({ok: true, exists: false, device_opt_in: false,
  quiet_hours_start: null, quiet_hours_end: null, timezone: 'UTC', version: 1, quiet_now});
const row = {id: '11111111-1111-4111-8111-111111111111', severity: 'failure', reason: 'r',
  subject_type: 'deal', subject_ref: 'd', deep_link: '/deals/d',
  created_at: '2026-09-18T00:00:00+00:00', read_at: null, delivery: []};
const facts = {ok: true, unread_count: 1, notifications: [row]};
let cases = 0;
function refused(value, p = null) {
  assert.throws(() => feed(value, p, ToolError), error => error.error === 'notification_feed_unavailable');
  cases++;
}
const invalid = [null, false, 1, 'feed', [], {}, {ok: true},
  ...[undefined, null, -1, 1.5, '1'].map(unread_count => ({...facts, unread_count})),
  ...[undefined, null, {}, [null], [1], ['row']].map(notifications => ({...facts, notifications}))];
for (const key of Object.keys(row)) {
  const partial = {...row}; delete partial[key];
  invalid.push({...facts, notifications: [partial]});
}
for (const delivery of [null, {}, [null], [1], [{}], [{channel: 'device'}],
  [{channel: 'email', state: 'pending'}], [{channel: 'device', state: 'unknown'}]]) {
  invalid.push({...facts, notifications: [{...row, delivery}]});
}
for (const f of invalid) refused(f);
for (const p of [prefs(true), prefs(false), null, {}, {ok: false, quiet_now: true}, {ok: true, quiet_now: true}]) {
  for (const historical of [false, true]) {
    const f = {...facts, notifications: [{...row, delivery: historical ?
      [{channel: 'device', state: 'suppressed_quiet_hours'}] : []}]};
    const answer = feed(f, p, ToolError);
    const current = p?.exists === false && p.quiet_now;
    assert.equal(answer.quiet_now, !!current);
    assert.equal(answer.notifications[0].quiet_suppressed, !!current || historical);
    cases++; refused({ok: true}, p);
  }
}
assert.deepEqual(feed({ok: true, unread_count: 0, notifications: []}, null, ToolError),
  {ok: true, unread_count: 0, notifications: [], quiet_now: false});
cases++;
let count = 0;
if (live.feed.error === 'notification_feed_unavailable') {
  assert.notEqual(live.feed.ok, true);
} else {
  assert.equal(typeof live.feed.quiet_now, 'boolean');
  const projected = feed(live.feed, prefs(live.feed.quiet_now), ToolError);
  assert.deepEqual(live.feed, projected);
  assert.ok(projected.notifications.length <= 20);
  count = projected.notifications.length;
}
if (live.preferences.error) {
  assert.equal(live.preferences.error, 'notification_preferences_unavailable');
  assert.notEqual(live.preferences.ok, true);
} else {
  assert.deepEqual(live.preferences, preference(live.preferences, ToolError));
}
process.stdout.write(JSON.stringify({replay_cases: cases, notifications: count,
  live_feed: live.feed.error ? 'named_refusal' : 'valid_explicit_feed'}));
'''


class Unavailable(Exception):
    pass


class Contradiction(Exception):
    pass


def observe(origin, wrapper, seconds):
    deadline = time.monotonic() + seconds

    def remaining():
        duration = deadline - time.monotonic()
        if duration <= 0:
            raise Unavailable('deadline_exhausted')
        return duration

    def command(args, payload=None, allow_refusal=False, contradiction=False):
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.PIPE,
                                       stdout=output, stderr=errors, start_new_session=True)
            try:
                process.communicate(payload, timeout=remaining())
            except BaseException:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise
            if output.tell() > MAX_BYTES or errors.tell() > MAX_BYTES:
                raise Unavailable('response_too_large')
            output.seek(0)
            raw = output.read()
            if process.returncode and not allow_refusal:
                if contradiction:
                    raise Contradiction('live_contract_or_release_replay_failed')
                raise Unavailable('command_unavailable')
            if process.returncode:
                errors.seek(0)
                diagnostic = errors.read()
                if b'TypeError' in raw + diagnostic:
                    raise Contradiction('unhandled_live_type_error')
                candidates = [raw, diagnostic]
                for candidate in candidates:
                    if b'TOOL ERROR ' in candidate:
                        candidate = candidate.split(b'TOOL ERROR ', 1)[1]
                    try:
                        value = json.loads(candidate)
                    except ValueError:
                        continue
                    if isinstance(value, dict) and value.get('error') in (
                            'notification_feed_unavailable', 'notification_preferences_unavailable'):
                        return json.dumps(value).encode()
                raise Unavailable('wrapper_unavailable')
            return raw

    def release():
        response = read_http(origin.rstrip('/') + '/release', timeout=remaining(), max_bytes=MAX_BYTES)
        if response.status != 200:
            raise OSError('release_unavailable')
        raw = response.body
        if len(raw) > MAX_BYTES:
            raise Unavailable('release_too_large')
        data = json.loads(raw)
        sha = data.get('git_sha')
        if isinstance(sha, dict):
            sha = sha.get('value')
        if not isinstance(sha, str) or not re.fullmatch('[a-f0-9]{40}', sha):
            raise Unavailable('release_identity_unavailable')
        return sha

    def expired(_signal, _frame):
        raise Unavailable('deadline_exhausted')

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        sha = release()
        change = command(['git', 'log', '-1', '--format=%H', '--', SOURCE]).decode().strip()
        if not re.fullmatch('[a-f0-9]{40}', change):
            raise Unavailable('change_identity_unavailable')
        command(['git', '--no-replace-objects', 'merge-base', '--is-ancestor', change, sha])
        source = command(['git', '--no-replace-objects', 'show', f'{sha}:{SOURCE}'])
        raw_feed = command([str(wrapper), 'call', 'notification-feed', '{"limit":20}'], allow_refusal=True)
        raw_preferences = command([str(wrapper), 'call', 'read-notification-preferences', '{}'], allow_refusal=True)
        try:
            feed, preferences = json.loads(raw_feed), json.loads(raw_preferences)
        except ValueError:
            raise Contradiction('malformed_live_json') from None
        with tempfile.TemporaryDirectory(prefix='notification-feed-replay-') as directory:
            path = Path(directory) / 'notifications.ts'
            path.write_bytes(source)
            raw = command(['node', '--experimental-strip-types', '--input-type=module', '-e', REPLAY, str(path)],
                          json.dumps({'feed': feed, 'preferences': preferences}).encode(), contradiction=True)
            counts = json.loads(raw)
        if release() != sha:
            raise Contradiction('release_changed')
        remaining()
        return 0, {'result': 'PASS', 'git_sha': sha, **counts,
                   'live_evidence': 'deployed_read_only_operation',
                   'replay_evidence': 'synthetic_release_pinned_source_no_database',
                   'release_reads': 2, 'wrapper_calls': 2, 'source_dependencies': 1}
    except Contradiction as error:
        return 1, {'result': 'FAIL', 'reason': str(error)}
    except (Unavailable, OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
        return 78, {'result': 'UNAVAILABLE', 'reason': 'release_source_or_wrapper_inaccessible'}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--call-wrapper', required=True, type=Path)
    parser.add_argument('--timeout-seconds', type=float, default=30)
    args = parser.parse_args()
    origin = urllib.parse.urlsplit(args.origin)
    if (origin.scheme not in ('https', 'http') or not origin.hostname or origin.username or origin.password
            or origin.query or origin.fragment or origin.path not in ('', '/')
            or origin.scheme == 'http' and origin.hostname not in ('localhost', '127.0.0.1', '::1')):
        parser.error('origin must be HTTPS or loopback HTTP without credentials, path, query or fragment')
    if not 0 < args.timeout_seconds <= 30:
        parser.error('timeout must be greater than zero and at most 30')
    code, receipt = observe(args.origin, args.call_wrapper.resolve(), args.timeout_seconds)
    print(json.dumps(receipt))
    return code


if __name__ == '__main__':
    sys.exit(main())
