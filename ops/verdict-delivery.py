#!/usr/bin/env python3
"""Transport independent verdicts and arm native merges from trusted GitHub facts."""
import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.loadpy import load_module_from_path

gate = load_module_from_path('delivery_review_gate', str(ROOT / 'ops/review-gate.py'))
REPO = 'DoctorCRE/carr-system'
APP = 'doctorcre-carr-review[bot]'
REQUESTS = 0
MAX_REQUESTS = 100
HOLDS = {2988: 'Explicit hold awaiting Joe; WR823.S14.J1 must not arm this PR'}


@dataclass(frozen=True)
class Verdict:
    decision: str
    sha: str
    reviewer: str
    body: str


def parse_verdict(body):
    if not isinstance(body, str) or len(body.encode()) > 60000:
        raise ValueError('Verdict exceeds comment budget')
    lines = body.splitlines()
    if (len(lines) < 3 or lines[0] not in ('VERDICT: APPROVE', 'VERDICT: CHANGES')
            or not re.fullmatch(r'Reviewed-SHA: [0-9a-f]{40}', lines[1])
            or not re.fullmatch(r'Reviewer: \S[^\r\n]{0,199}', lines[2])):
        raise ValueError('Expected VERDICT, full Reviewed-SHA and Reviewer header')
    for prefix in ('VERDICT:', 'Reviewed-SHA:', 'Reviewer:'):
        if sum(line.startswith(prefix) for line in lines) != 1:
            raise ValueError('Duplicate verdict header')
    if any(line.startswith('Verdict-Intake:') for line in lines):
        raise ValueError('Intake marker is reserved')
    return Verdict('approve' if lines[0] == 'VERDICT: APPROVE' else 'request-changes',
                   lines[1][14:], lines[2][10:], body)


def intake(event, transport):
    comment = event.get('comment', {})
    if (not transport or transport.lower() == APP.lower() or event.get('action') != 'created'
            or 'pull_request' not in event.get('issue', {})
            or event.get('sender', {}).get('login', '').lower() != transport.lower()
            or comment.get('user', {}).get('login', '').lower() != transport.lower()
            or type(comment.get('id')) is not int or comment['id'] < 1):
        raise ValueError('Only a newly created PR verdict from the configured transport is admitted')
    parse_verdict(comment.get('body'))
    return event['issue']['number'], comment['body'], comment['id']


def collection(value, key=None):
    items = value[key] if key else value
    if (not isinstance(items, list) or len(items) >= 100
            or key and value.get('total_count') != len(items)):
        raise ValueError('Incomplete GitHub collection; refusing truncated evidence')
    return items


def held(pr, number):
    return HOLDS.get(number) or ('merge-hold label' if any(
        label['name'].lower() == 'merge-hold' for label in pr['labels']) else None)


def ready(pr):
    return (pr['state'] == 'open' and pr['draft'] is False and pr['base']['ref'] == 'main'
            and pr['head']['repo']['full_name'] == REPO)


def deliver(number, read, write, *, app_login, verdict=None, intake_id=None,
            arm_enabled=False, dry_run=False):
    if type(number) is not int or number < 1 or app_login != APP:
        raise ValueError('Invalid PR number or review App identity')
    pr = read(f'pulls/{number}')
    sha = pr['head']['sha']
    result = {'schema': 'carr-verdict-delivery.v1', 'repository': REPO,
              'pull_request': number, 'head_sha': sha}
    def state(name, reason):
        return dict(result, state=name, reason=reason)
    if held(pr, number):
        return state('held', held(pr, number))
    if not ready(pr):
        return state('refused', 'PR is not an open same-repository main candidate')
    reviews = collection(read(f'pulls/{number}/reviews?per_page=100'))
    if verdict is not None:
        report = parse_verdict(verdict)
        if report.sha != sha:
            return state('head-moved', 'Verdict does not describe the current head')
        if report.reviewer.lower() in {pr['user']['login'].lower(), app_login.lower()}:
            return state('refused', 'Reviewer must be independent of the author and submission App')
        if type(intake_id) is not int or intake_id < 1:
            raise ValueError('Missing durable comment identity')
        marker = f'Verdict-Intake: {intake_id}'
        expected_state = 'APPROVED' if report.decision == 'approve' else 'CHANGES_REQUESTED'
        app_reviews = sorted((r for r in reviews if r.get('user', {}).get('login') == app_login
                              and r.get('commit_id') == sha),
                             key=lambda r: r.get('submitted_at') or '')
        consumed = {int(match[1]) for r in app_reviews
                    for line in r.get('body', '').splitlines()
                    if (match := re.fullmatch(r'Verdict-Intake: ([1-9][0-9]*)', line))}
        if intake_id in consumed:
            return state('refused', 'Verdict intake already consumed at this head')
        if any(previous > intake_id for previous in consumed):
            return state('refused', 'Newer verdict intake already consumed at this head')
        duplicate = any(r.get('state') == expected_state
                        and r.get('body', '').rsplit('\nVerdict-Intake: ', 1)[0] == report.body
                        for r in app_reviews[-1:])
        if not duplicate:
            current = read(f'pulls/{number}')
            if current['head']['sha'] != sha:
                return state('head-moved', 'Head moved before review submission')
            if held(current, number) or not ready(current):
                return state('held', 'PR became held or unavailable before submission')
            if dry_run:
                return state('would-submit', report.decision)
            write({'kind': 'submit', 'number': number, 'decision': report.decision,
                   'sha': sha, 'body': report.body + '\n' + marker})
            return state('submitted', 'Native App review bound to the reviewed SHA')
    failure = gate.review_failure(pr, reviews, app_login)
    if failure:
        return state('refused', failure)
    if not arm_enabled:
        return state('approved-current-head', 'Arming disabled pending App permission and configuration')
    queue = read(f'queue/{number}')
    if queue['headRefOid'] != sha:
        return state('head-moved', 'Head moved before queue readback')
    current = read(f'pulls/{number}')
    if current['head']['sha'] != sha:
        return state('head-moved', 'Head moved before arming')
    if held(current, number) or not ready(current):
        return state('held', 'PR became held or unavailable before arming')
    # Reviews can change without a push. Re-read immediately before the write.
    if gate.review_failure(current, collection(read(f'pulls/{number}/reviews?per_page=100')), app_login):
        return state('refused', 'Approval changed before arming')
    if current.get('auto_merge'):
        return state('armed', 'Native auto-merge already enabled')
    if queue['isInMergeQueue']:
        return state('armed', 'PR is already in the native merge queue')
    if dry_run:
        return state('would-arm', 'Current-head App approval; GitHub enforces required checks')
    write({'kind': 'arm', 'number': number, 'sha': sha})
    return state('armed', 'Native merge queue requested with matching head')


def gh(args, payload=None, token=None):
    global REQUESTS
    if REQUESTS >= MAX_REQUESTS:
        raise ValueError('GitHub request budget exhausted')
    REQUESTS += 1
    env = dict(os.environ)
    if token:
        env['GH_TOKEN'] = token
    result = subprocess.run(['gh', *args], input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, timeout=30, env=env)
    if result.returncode:
        raise RuntimeError('GitHub command failed; no automatic retry')
    if len(result.stdout.encode()) > 2_000_000:
        raise ValueError('GitHub response exceeds budget')
    return result.stdout


def github_read(path):
    if path.startswith('queue/'):
        number = int(path.removeprefix('queue/'))
        if number < 1:
            raise ValueError('Invalid queue PR number')
        query = ('query($number:Int!){repository(owner:"DoctorCRE",name:"carr-system")'
                 '{pullRequest(number:$number){headRefOid isInMergeQueue '
                 'mergeQueueEntry{enqueuer{login __typename}}}}}')
        data = json.loads(gh(['api', 'graphql', '-f', 'query=' + query, '-F', f'number={number}']))
        if data.get('errors'):
            raise ValueError('Native merge queue read failed')
        return data['data']['repository']['pullRequest']
    return json.loads(gh(['api', path if path.startswith('apps/') else f'repos/{REPO}/{path}']))


def github_write(action):
    token = os.environ.get('CARR_REVIEW_APP_TOKEN')
    if not token:
        raise ValueError('Review App token unavailable')
    if action['kind'] == 'submit':
        submit = load_module_from_path('delivery_submit', str(ROOT / 'ops/review-submit.py'))
        submit.submit(action['number'], action['decision'], action['body'], action['sha'], REPO, token=token)
    else:
        gh(['pr', 'merge', str(action['number']), '--repo', REPO,
            '--auto', '--match-head-commit', action['sha']], token=token)


def candidates(event):
    if 'pull_request' in event:
        return [event['pull_request']['number']]
    run = event.get('workflow_run', {})
    if run:
        sha = run['head_sha']
        prs = run.get('pull_requests') or collection(github_read(f'commits/{sha}/pulls?per_page=100'))
    else:
        # A commit-status event can have no PR association. Bound the lookup by SHA.
        sha = event.get('sha') or event.get('check_run', {}).get('head_sha')
        if not sha or not re.fullmatch(r'[a-f0-9]{40}', sha):
            return []
        prs = collection(github_read(f'commits/{sha}/pulls?per_page=100'))
    if len(prs) > 20:
        raise ValueError('PR event fan-out exceeds 20')
    return sorted({p['number'] for p in prs})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['event', 'dry-run', 'publish'])
    parser.add_argument('--number', type=int)
    parser.add_argument('--body-file', type=Path)
    args = parser.parse_args()
    receipts = []
    try:
        if args.mode == 'publish':
            body = args.body_file.read_text()
            parse_verdict(body)
            if not args.number or args.number < 1:
                raise ValueError('Provide a positive PR number')
            response = json.loads(gh(['api', f'repos/{REPO}/issues/{args.number}/comments',
                                      '--method', 'POST', '--input', '-'], {'body': body}))
            print(json.dumps({'comment_id': response['id'], 'url': response['html_url']}))
            return 0
        if args.mode == 'dry-run':
            receipts.append(deliver(args.number, github_read, github_write, app_login=APP,
                                    dry_run=True, arm_enabled=True))
        else:
            event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
            if os.environ['GITHUB_REPOSITORY'] != REPO:
                raise ValueError('Workflow repository mismatch')
            if 'comment' in event:
                number, body, comment_id = intake(event, os.environ.get('CARR_VERDICT_TRANSPORT_LOGIN', ''))
                receipts.append(deliver(number, github_read, github_write, app_login=APP,
                                        verdict=body, intake_id=comment_id))
                if receipts[-1]['state'] == 'submitted' and parse_verdict(body).decision == 'approve':
                    receipts.append(deliver(number, github_read, github_write, app_login=APP,
                        arm_enabled=os.environ.get('CARR_VERDICT_ARM_ENABLED') == 'true'))
            else:
                for number in candidates(event):
                    receipts.append(deliver(number, github_read, github_write, app_login=APP,
                        arm_enabled=os.environ.get('CARR_VERDICT_ARM_ENABLED') == 'true'))
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        receipts.append({'state': 'refused', 'reason': type(error).__name__})
        print(json.dumps(receipts, sort_keys=True))
        return 1
    print(json.dumps(receipts, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
