#!/usr/bin/env python3
"""Observe natural exact task cohorts without accepting tasks or writing records."""
import argparse
from calendar import monthrange
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.proof_deadline import proof_deadline
from lib.release_reader import read

MAX_BYTES = 1048576
SPACE = '\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
WHITESPACE = re.compile('[' + SPACE + ']+')


class Unavailable(Exception):
    def __init__(self, status):
        self.status = status


def normalized(text):
    return WHITESPACE.sub(' ', text).strip(' ').lower()


def object_json(raw):
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise Unavailable('invalid_contract')
    return value


def valid_date(value):
    if value is None or value in ('infinity', '-infinity'):
        return True
    if not isinstance(value, str):
        return False
    match = re.fullmatch(r'([0-9]{4,7})-([0-9]{2})-([0-9]{2})( BC)?', value)
    if not match:
        return False
    year, month, day = map(int, match.group(1, 2, 3))
    if year == 0 or not 1 <= month <= 12:
        return False
    return 1 <= day <= monthrange(1 - year if match.group(4) else year, month)[1]


def inspect(value, receipt):
    if value.get('schema_version') != 'call-outcomes.v1' or value.get('send') is not False:
        raise Unavailable('invalid_contract')
    calls = value.get('calls')
    if not isinstance(calls, list) or len(calls) > 50:
        raise Unavailable('invalid_contract')
    passed = True
    for call in calls:
        if not isinstance(call, dict) or not isinstance(call.get('open_cards'), list):
            raise Unavailable('invalid_contract')
        for card in call['open_cards']:
            receipt['inspected_cards'] += 1
            if receipt['inspected_cards'] > 1000 or not isinstance(card, dict):
                raise Unavailable('invalid_contract')
            if card.get('kind') != 'assigned_action':
                continue
            payload, comparison = card.get('payload'), card.get('existing_action_comparison')
            if (not isinstance(payload, dict) or not isinstance(comparison, dict)
                    or comparison.get('schema_version') != 'post-call-action-comparison.v2'
                    or not isinstance(payload.get('action'), str) or payload.get('assignee') not in {'joe', 'dell'}
                    or not valid_date(payload.get('due_on'))):
                raise Unavailable('invalid_contract')
            if comparison.get('status') == 'assignee_unavailable':
                continue
            related = comparison.get('related_open_tasks')
            exact = comparison.get('exact_match')
            if not isinstance(related, list) or len(related) > 5 or (exact is not None and not isinstance(exact, dict)):
                raise Unavailable('invalid_contract')
            tasks = ([exact] if exact is not None else []) + related
            for task in tasks:
                if (not isinstance(task, dict) or not isinstance(task.get('description'), str)
                        or not isinstance(task.get('id'), str)
                        or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', task['id'])
                        or task.get('source') not in {'next_action', 'capture_post_call_action'}
                        or not isinstance(task.get('owner'), str) or not valid_date(task.get('due_on'))):
                    raise Unavailable('invalid_contract')
                text = task['description']
                edges = text[:len(text) - len(text.lstrip(SPACE))] + text[len(text.rstrip(SPACE)):]
                edge = any(char != ' ' for char in edges)
                if task.get('status') != 'open' or not edge or task['owner'] != payload['assignee'] or normalized(text) != normalized(payload['action']):
                    continue
                receipt['eligible_cohorts'] += 1
                effect = comparison.get('date_effect')
                identity = (comparison.get('status') == 'exact_match' and exact is not None
                            and exact['id'] == task['id'] and exact['source'] == task['source'] and task.get('status') == 'open')
                dates = False
                if isinstance(effect, dict) and {'reason', 'proposed_due_on', 'current_due_on', 'resulting_due_on', 'changes_due_on'} <= effect.keys():
                    proposed, current = payload.get('due_on'), task.get('due_on')
                    reason = effect.get('reason')
                    resulting = proposed if reason == 'stated_date' else current
                    dates = (reason in {'stated_date', 'defaulted_date', 'unstated_date'}
                             and (reason == 'unstated_date') == (proposed is None)
                             and effect.get('proposed_due_on') == proposed and effect.get('current_due_on') == current
                             and effect.get('resulting_due_on') == resulting
                             and effect.get('changes_due_on') is (resulting != current))
                for name, success in [('exact_identity_and_owner', identity), ('preserved_date_context', dates)]:
                    receipt['assertions'][name] = receipt['assertions'].get(name, True) and success
                    passed = passed and success
    if not receipt['eligible_cohorts']:
        raise Unavailable('natural_cohort_unavailable')
    receipt['status'] = 'passed' if passed else 'contradiction'
    return 0 if passed else 1


def observe(args, receipt):
    deadline = time.monotonic() + args.timeout_seconds

    def remaining():
        left = deadline - time.monotonic()
        if left <= 0:
            raise Unavailable('timeout')
        return left

    def release():
        try:
            response = read(args.origin.rstrip('/') + '/release', timeout=remaining(), max_bytes=MAX_BYTES)
        except (TimeoutError, urllib.error.URLError) as error:
            if isinstance(error, TimeoutError) or isinstance(error.reason, TimeoutError):
                raise Unavailable('timeout')
            raise
        remaining()
        raw = response.body
        if response.status != 200 or len(raw) > MAX_BYTES:
            raise Unavailable('release_unavailable')
        value = object_json(raw)
        sha, version = value.get('git_sha'), value.get('worker_version')
        if (value.get('ok') is not True or value.get('env', {}).get('value') != 'production'
                or not isinstance(sha, dict) or not re.fullmatch('[0-9a-f]{40}', str(sha.get('value')))
                or not isinstance(version, dict) or not isinstance(version.get('id'), str) or not version['id']):
            raise Unavailable('release_unavailable')
        return {'git_sha': sha['value'], 'worker_version': version['id']}

    receipt['release_identity'] = release()
    receipt['record_reads'] += 1
    with subprocess.Popen([args.call_wrapper, 'call', 'read-call-outcomes', '{}'], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, start_new_session=True) as process:
        streams = {process.stdout: bytearray(), process.stderr: bytearray()}
        try:
            with selectors.DefaultSelector() as selector:
                for stream in streams:
                    selector.register(stream, selectors.EVENT_READ)
                while selector.get_map():
                    events = selector.select(timeout=remaining())
                    if not events:
                        raise Unavailable('timeout')
                    for key, _ in events:
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        streams[key.fileobj].extend(chunk)
                        if sum(map(len, streams.values())) > MAX_BYTES:
                            raise Unavailable('response_too_large')
            code = process.wait(timeout=remaining())
            stdout, stderr = (bytes(streams[s]) for s in (process.stdout, process.stderr))
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    if release() != receipt['release_identity']:
        raise Unavailable('release_changed')
    refusal = None
    if b'TOOL ERROR ' in stderr:
        try:
            refusal, _ = json.JSONDecoder().raw_decode(stderr.split(b'TOOL ERROR ', 1)[1].decode())
        except (ValueError, UnicodeError):
            raise Unavailable('invalid_refusal')
    if not code:
        value = object_json(stdout)
        refusal = value if value.get('error') else refusal
    if refusal is not None:
        if not isinstance(refusal, dict) or not isinstance(refusal.get('error'), str):
            raise Unavailable('invalid_refusal')
        error = refusal['error']
        receipt['refusal_class'] = error if error in {'unauthorized', 'forbidden', 'human_only', 'service_unavailable', 'assignee_not_provisioned'} else 'other_declared_error'
        raise Unavailable('declared_refusal')
    if code:
        raise Unavailable('transport_failure')
    return inspect(value, receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--call-wrapper', required=True)
    parser.add_argument('--timeout-seconds', type=float, default=30)
    args = parser.parse_args()
    url = urllib.parse.urlsplit(args.origin)
    if (url.scheme not in {'http', 'https'} or not url.netloc or url.username or url.password
            or url.query or url.fragment or url.path not in {'', '/'} or not 0 < args.timeout_seconds <= 30):
        parser.error('supply an HTTP origin and a deadline greater than zero and at most thirty seconds')
    receipt = {'schema': 'post-call-exact-edge-whitespace-proof.v1', 'status': 'unavailable',
               'record_reads': 0, 'inspected_cards': 0, 'eligible_cohorts': 0, 'assertions': {}}

    try:
        with proof_deadline(args.timeout_seconds, Unavailable, 'timeout'):
            code = observe(args, receipt)
    except Unavailable as error:
        receipt['status'] = error.status
        code = 78
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        receipt['status'] = 'unavailable_transport_or_contract'
        code = 78
    print(json.dumps(receipt, sort_keys=True))
    return code


if __name__ == '__main__':
    sys.exit(main())
