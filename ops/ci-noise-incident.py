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
        batch = api(f'repos/{repo}/issues?state=all&per_page=100&page={page}')
        matches.extend(row for row in batch if not row.get('pull_request')
                       and row['title'] == TITLE and MARKER in (row.get('body') or ''))
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
        issue = api(f'repos/{repo}/issues', {'title': TITLE, 'body': body, 'assignees': ['jbookout']})
    elif code != 0 or issue['state'] == 'open':
        issue = api(f'repos/{repo}/issues/{issue["number"]}',
                    {'body': body, 'state': 'closed' if code == 0 else 'open'})
    return {'state': status, 'issue': issue['number'], 'probe_exit': code}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    repo = os.environ['GITHUB_REPOSITORY']
    run_url = (f'{os.environ["GITHUB_SERVER_URL"]}/{repo}/actions/runs/'
               f'{os.environ["GITHUB_RUN_ID"]}')
    probe = subprocess.run([sys.executable, str(Path(__file__).with_name('edge-liveness.py'))],
                           capture_output=True, text=True, timeout=180)
    details = probe.stdout + probe.stderr
    print(details, end='')
    Path('edge-liveness-probe.json').write_text(json.dumps({
        'probe_exit': probe.returncode, 'details': details, 'run_url': run_url}, indent=2))
    result = publish(repo, probe.returncode, details, run_url)
    print(json.dumps(result))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as output:
            output.write(f'Probe exit **{probe.returncode}**. Incident delivery succeeded. '
                         f'Issue: {result["issue"]}. See the probe artifact for the health evidence.\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
