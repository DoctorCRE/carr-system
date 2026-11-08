#!/usr/bin/env python3
"""Read-only exact-card proof. A containing release and an authorized read wrapper are required."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.release_reader import read

SHA = re.compile(r'[a-f0-9]{40}')
PARSER = """
import {parseActionCardResponse} from './mcp-server/src/action-card-targets.ts';
let body='';for await(const chunk of process.stdin)body+=chunk;
console.log(JSON.stringify(parseActionCardResponse(JSON.parse(body))));
"""


class Insufficient(Exception):
    pass


def remaining(deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise Insufficient('deadline')
    return left


def command(argv, deadline, body=None):
    with subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, cwd=ROOT, start_new_session=True) as process:
        try:
            stdout, stderr = process.communicate(body, timeout=remaining(deadline))
            if len(stdout) > 1048576:
                raise Insufficient('response_too_large')
            return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def containing(expected, observed, deadline, fixture=None):
    if not SHA.fullmatch(expected) or not isinstance(observed, str) or not SHA.fullmatch(observed):
        raise Insufficient('release_identity_unknown')
    if expected == observed:
        return
    if fixture is not None:
        valid = expected in fixture.get('ancestors', [])
    else:
        valid = command(['git', '--no-replace-objects', 'merge-base', '--is-ancestor', expected, observed], deadline).returncode == 0
    if not valid:
        raise Insufficient('release_does_not_contain_merge')


def inspect(responses, targets, deadline):
    kinds = set()
    for response, target in zip(responses, targets, strict=True):
        parsed = command(['node', '--input-type=module', '-e', PARSER], deadline, json.dumps(response))
        if parsed.returncode:
            raise AssertionError('response_contract_failed')
        value = json.loads(parsed.stdout)
        card = value['card']
        if any(card[key] != target[key] for key in ('kind', 'id', 'source_version')) or value['ref'] != target['ref']:
            raise AssertionError('exact_source_mismatch')
        if card['state'] in ('stale', 'retired'):
            raise Insufficient('current_live_targets_required')
        kinds.add(card['kind'])
    if kinds != {'reply_draft', 'post_call_review', 'feedback_event'}:
        raise Insufficient('all_three_card_kinds_required')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-merge', required=True)
    parser.add_argument('--targets', type=Path)
    parser.add_argument('--fixture', type=Path)
    parser.add_argument('--call-wrapper', type=Path)
    parser.add_argument('--deadline-seconds', type=float, default=45)
    args = parser.parse_args()
    deadline = time.monotonic() + min(max(args.deadline_seconds, .1), 60)
    try:
        fixture = json.loads(args.fixture.read_text()) if args.fixture else None
        if fixture is not None:
            release, targets = fixture['release'], fixture['targets']
        else:
            reply = read('https://api.doctorcre.com/release', timeout=remaining(deadline), max_bytes=1048576)
            if reply.status != 200 or len(reply.body) > 1048576:
                raise Insufficient('release_unavailable')
            release = json.loads(reply.body)
            targets = json.loads(args.targets.read_text()) if args.targets else []
        observed = release.get('git_sha', {}).get('value')
        if release.get('env', {}).get('value') != 'production':
            raise Insufficient('production_identity_required')
        containing(args.expected_merge, observed, deadline, fixture)
        if not isinstance(targets, list) or len(targets) != 3:
            raise Insufficient('three_exact_targets_required')
        if fixture is not None:
            responses, foreign, unknown = fixture['responses'], fixture['foreign'], fixture['unknown']
        else:
            if not args.call_wrapper:
                raise Insufficient('authorized_human_read_wrapper_required')
            def resolve(ref):
                result = command([str(args.call_wrapper.resolve()), 'resolve-action-link-v2', json.dumps({'ref': ref})], deadline)
                if not result.stdout:
                    raise Insufficient('authorized_resolution_unavailable')
                return json.loads(result.stdout)
            responses = [resolve(target['ref']) for target in targets]
            foreign = resolve(targets[0]['foreign_ref'])
            unknown = resolve(targets[0]['unknown_ref'])
        inspect(responses, targets, deadline)
        if foreign != {'error': 'action_link_not_found'} or unknown != foreign:
            raise AssertionError('foreign_unknown_parity_failed')
        print(json.dumps({'state': 'passed', 'release_sha': observed, 'targets': 3, 'fixture': fixture is not None}))
        return 0
    except AssertionError as error:
        print(json.dumps({'state': 'assertion_failure', 'reason': str(error)}))
        return 1
    except (Insufficient, ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'state': 'insufficient_evidence', 'reason': str(error)[:120]}))
        return 78


if __name__ == '__main__':
    raise SystemExit(main())
