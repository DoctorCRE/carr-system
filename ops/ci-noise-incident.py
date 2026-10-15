#!/usr/bin/env python3
"""Deliver edge probe incidents through one issue instead of failed cron runs."""
# doctrine: engineering-workflow-sop
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

TITLE = 'Edge liveness incident'
MARKER = '<!-- ci-noise-incident:edge-liveness:v1 -->'
LABEL = 'ci-edge-liveness'
AUTHOR = {'id': 41898282, 'login': 'github-actions[bot]', 'type': 'Bot'}


def github(endpoint, payload=None):
    args = ['gh', 'api', endpoint]
    if payload is not None:
        args += ['--method', 'PATCH' if '/issues/' in endpoint else 'POST', '--input', '-']
    result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(f'incident delivery failed: {result.stderr.strip()}')
    return json.loads(result.stdout)


def publish(repo, code, details, run_url, api=github):
    if code not in (0, 1, 78):
        raise ValueError(f'probe did not produce a liveness verdict: exit {code}')
    matches = []
    for page in range(1, 1001):
        batch = api(f'repos/{repo}/issues?state=all&labels={LABEL}&per_page=100&page={page}')
        if not isinstance(batch, list) or any(not isinstance(row, dict) for row in batch):
            raise RuntimeError('malformed incident issue response')
        matches.extend(row for row in batch if not row.get('pull_request')
                       and all(row.get('user', {}).get(key) == value for key, value in AUTHOR.items())
                       and any(label.get('name') == LABEL for label in row.get('labels', [])))
        if len(batch) < 100:
            break
    else:
        raise RuntimeError('incident issue pagination incomplete')
    if len(matches) > 1:
        raise RuntimeError('duplicate incident issues require reconciliation')
    issue = matches[0] if matches else None
    status = {0: 'Recovered', 1: 'ALARM: a registered service has gone quiet',
              78: 'BLIND: ledger unavailable; no service health verdict'}[code]
    body = (f'{MARKER}\n\n{status}\n\nProbe exit: {code}\n'
            f'Observed: {datetime.now(timezone.utc).isoformat()}\n\n'
            f'[Latest probe run]({run_url})\n\n```text\n{details[-28000:]}\n```\n\n'
            'This issue is the incident alert. Scheduled observations update its body, '
            'without comments or a new failed-workflow email. Recovery closes it; '
            'a new incident reopens it. The probe result above remains authoritative.')
    if issue is None:
        if code == 0:
            return {'state': 'healthy', 'issue': None, 'probe_exit': code}
        # Ensure the label exists before GitHub accepts it on the new issue.
        labels = []
        for page in range(1, 1001):
            batch = api(f'repos/{repo}/labels?per_page=100&page={page}')
            if not isinstance(batch, list) or any(not isinstance(row, dict) for row in batch):
                raise RuntimeError('malformed incident label response')
            labels.extend(row.get('name') for row in batch)
            if len(batch) < 100:
                break
        else:
            raise RuntimeError('incident label pagination incomplete')
        if LABEL not in labels:
            api(f'repos/{repo}/labels', {'name': LABEL, 'color': 'B60205'})
        issue = api(f'repos/{repo}/issues', {'title': TITLE, 'body': body,
                                          'labels': [LABEL], 'assignees': ['jbookout']})
    elif code != 0 or issue['state'] == 'open' or not any(
            user.get('login') == 'jbookout' for user in issue.get('assignees', []) if isinstance(user, dict)):
        issue = api(f'repos/{repo}/issues/{issue["number"]}',
                    {'body': body, 'state': 'closed' if code == 0 else 'open', 'assignees': ['jbookout']})
    return {'state': status, 'issue': issue['number'], 'probe_exit': code}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    repo = os.environ['GITHUB_REPOSITORY']
    run_url = (f'{os.environ["GITHUB_SERVER_URL"]}/{repo}/actions/runs/'
               f'{os.environ["GITHUB_RUN_ID"]}')
    verdict = {'status': 'ERROR', 'probe_exit': None, 'run_url': run_url}
    try:
        probe = subprocess.run([sys.executable, str(Path(__file__).with_name('ci-noise-probe.py'))],
                               capture_output=True, text=True, timeout=180)
        verdict['probe_exit'] = probe.returncode
        completed = json.loads(probe.stdout)
        expected = {0: 'HEALTHY', 1: 'ALARM', 78: 'BLIND'}.get(probe.returncode)
        if (not isinstance(completed, dict) or expected is None
                or completed.get('schema') != 'edge-liveness-verdict/v1'
                or type(completed.get('probe_exit')) is not int
                or completed['probe_exit'] != probe.returncode or completed.get('status') != expected):
            raise ValueError('probe did not complete a valid verdict')
        verdict['status'] = expected
    except (ValueError, subprocess.TimeoutExpired, OSError):
        pass
    Path('edge-liveness-probe.json').write_text(json.dumps(verdict, indent=2))
    print(json.dumps(verdict))
    if verdict['status'] == 'ERROR':
        print('BLIND/ERROR: probe failed; service health is unknown', file=sys.stderr)
        return 1
    result = publish(repo, probe.returncode, verdict['status'], run_url)
    print(json.dumps(result))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as output:
            output.write(f'Probe exit **{probe.returncode}**. Incident delivery succeeded. '
                         f'Issue: {result["issue"]}. See the probe artifact for the health evidence.\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
