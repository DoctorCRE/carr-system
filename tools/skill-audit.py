#!/usr/bin/env python3
"""Verify skill use from native session evidence for an exact PR head."""
import argparse
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from html import escape
import json
from pathlib import Path
import re
import subprocess
import sys
import time as clock
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

REPOS = ('DoctorCRE/carr-system', 'jbookout/doctorcre-app')
ZONE = ZoneInfo('America/Chicago')
CHECKS = ('poteto_mode', 'prove_it_works', 'tdd', 'grill_with_docs',
          'codebase_design', 'tdd_behavior', 'prove_behavior')


def audit_events(*args, **kwargs):
    from skill_audit import audit_events as audit
    return audit(*args, **kwargs)


def instant(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def references(text):
    refs = re.findall(r'https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)', text)
    refs += re.findall(r'([\w.-]+/[\w.-]+)\s*#(\d+)', text)
    return {repo.lower() + '#' + number for repo, number in refs}


def command_references(event):
    command = event.get('command', '')
    if not re.search(r'\bgh\s+pr\s+(?:create|view|checks|merge|edit)|approve-merge\.sh', command):
        return set()
    refs = references(command + '\n' + event.get('output', ''))
    repo = re.search(r'--repo\s+[\"\']?([\w.-]+/[\w.-]+)', command)
    number = re.search(r'\bgh\s+pr\s+(?:view|checks|merge|edit)\s+[\"\']?(\d+)', command)
    if repo and number:
        refs.add(repo[1].lower() + '#' + number[1])
    approval = re.search(r'approve-merge\.sh\s+[\"\']?([\w.-]+/[\w.-]+)[\"\']?\s+[\"\']?(\d+)', command)
    if approval:
        refs.add(approval[1].lower() + '#' + approval[2])
    return refs


def session_windows(sessions, jobs):
    windows = []
    by_thread = {}
    for job in jobs:
        if job.get('thread_id') and job.get('dispatched_at'):
            by_thread.setdefault(job['thread_id'], []).append(job)
    for session in sessions:
        events = session['events']
        assigned = sorted(by_thread.get(session['id'], []), key=lambda j: j['dispatched_at'])
        headers = [e for e in events if e.get('role') == 'user' and 'Job checkout:' in e.get('text', '')]
        used_headers = set()
        if assigned:
            for i, job in enumerate(assigned):
                start = instant(job['dispatched_at'])
                end = instant(assigned[i+1]['dispatched_at']) if i+1 < len(assigned) else datetime.max.replace(tzinfo=timezone.utc)
                own = next((e for e in headers if start <= instant(e['timestamp']) < end
                            and (not job.get('checkout_path') or job['checkout_path'] in e['text'])), None)
                if own:
                    used_headers.add(own['timestamp'])
                    later = [instant(e['timestamp']) for e in headers if instant(e['timestamp']) > instant(own['timestamp'])]
                    if later:
                        end = min(end, min(later))
                selected = [e for e in events if e.get('timestamp') and start <= instant(e['timestamp']) < end]
                refs = references(job.get('result', '') + '\n' + job.get('task', ''))
                refs.update(*(command_references(e) for e in selected))
                windows.append(dict(id=session['id'], job_id=job.get('msg_id', ''),
                                    desk=job.get('desk', ''), brief=job.get('task', ''),
                                    prs=sorted(refs), events=selected))
            for index, header in enumerate(headers):
                if header['timestamp'] in used_headers:
                    continue
                start = instant(header['timestamp'])
                end = instant(headers[index+1]['timestamp']) if index+1 < len(headers) else datetime.max.replace(tzinfo=timezone.utc)
                selected = [e for e in events if e.get('timestamp') and start <= instant(e['timestamp']) < end]
                refs = set().union(*(command_references(e) for e in selected))
                windows.append(dict(id=session['id'], job_id='', desk='active session',
                                    brief=header['text'], prs=sorted(refs), events=selected))
        else:
            starts = [i for i, e in enumerate(events) if e.get('role') == 'user'
                      and e.get('text', '').strip() and '<hook_prompt' not in e.get('text', '')
                      and 'AGENTS.md instructions for' not in e.get('text', '')]
            if not starts:
                starts = [0]
            for i, start in enumerate(starts):
                end = starts[i+1] if i+1 < len(starts) else len(events)
                selected = events[start:end]
                refs = set().union(*(command_references(e) for e in selected))
                windows.append(dict(id=session['id'], job_id='', desk='native session',
                                    brief=events[start].get('text', session.get('brief', '')) if events else '',
                                    prs=sorted(refs), events=selected))
    return windows


def head_evidence(window, head):
    evidence = []
    for i, event in enumerate(window['events']):
        command = event.get('command', '')
        if event.get('exit_code') != 0 or not re.search(r'\bgit\s+(?:-C\s+\S+\s+)?(?:push|commit|rev-parse|show|log)\b', command):
            continue
        hashes = re.findall(r'(?<![0-9a-f])[0-9a-f]{8,40}(?![0-9a-f])', event.get('output', ''))
        if not any(head.startswith(value) for value in hashes):
            continue
        if any(e.get('source_edit') for e in window['events'][i+1:]):
            continue
        evidence.append(dict(timestamp=event['timestamp'], command=command, exit_code=0))
    return evidence


def audit_pr(pr, info, diff, windows):
    head = info['head']['sha']
    candidates = [(w, head_evidence(w, head)) for w in windows if pr.lower() in w['prs']]
    candidates = [(w, proof) for w, proof in candidates if proof]
    if not candidates:
        return dict(subject=pr, head=head, verdict='UNATTRIBUTED', checks={},
                    reason='No job window has a successful Git result for this exact PR head.')
    results = []
    for window, proof in candidates:
        result = audit_events(window['events'], diff=diff, brief=window['brief'])
        result.update(subject=pr, head=head, session=window['id'], job_id=window.get('job_id', ''))
        result['checks']['head_binding'] = dict(verdict='VERIFIED', reason='Exact head observed in this job window.', evidence=proof)
        results.append(result)
    # A later job that produced the current head cannot borrow an earlier job's pass.
    return max(results, key=lambda r: r['checks']['head_binding']['evidence'][-1]['timestamp'])


def audit_remote_pr(github, pr, info, windows):
    repo, number = pr.split('#')
    head = info['head']['sha']
    try:
        base = info['base']['sha']
        diff = github.get(f'repos/{repo}/compare/{base}...{head}', diff=True)
        if not isinstance(diff, str) or (diff.strip() and not diff.startswith('diff --git ')):
            raise ValueError('GitHub did not return a readable PR diff.')
        report = audit_pr(pr, info, diff, windows)
        current = github.get(f'repos/{repo}/pulls/{number}')['head']['sha']
        if current != head:
            raise ValueError('PR head changed during the audit; rerun for the new head.')
        return report
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        return dict(subject=pr, head=head, verdict='MISSING',
                    checks={'head_binding': dict(verdict='MISSING', reason=str(error), evidence=[])})


def evidence_lines(report):
    from skill_audit import redact
    lines = ['  ' + label + ': ' + redact(report[key])
             for key, label in (('head', 'Head'), ('session', 'Session')) if report.get(key)]
    if report.get('reason'):
        lines.append('  ' + redact(report['reason']))
    for name, check in report.get('checks', {}).items():
        lines.append(f"  {name}: {check['verdict']} - {redact(check.get('reason', ''))}")
        for proof in check.get('evidence', []):
            command = redact(proof.get('command', ''))[:500]
            lines.append(f"    {proof.get('timestamp', '')} {json.dumps(command, ensure_ascii=False)}")
    return lines


def render_html(summary, reports):
    from skill_audit import redact
    rows = []
    for report in reports:
        cells = ''.join('<td>' + escape(report.get('checks', {}).get(name, {}).get('verdict', 'MISSING')) + '</td>' for name in CHECKS)
        details = '\n'.join(evidence_lines(report)) or report.get('reason', 'No attributable session evidence.')
        rows.append('<tr><th>' + escape(report['subject']) + '</th><td>' + escape(report['verdict']) + '</td>' + cells + '</tr>'
                    '<tr><td colspan="9"><details><summary>Evidence and reasons</summary><pre>' + escape(redact(details)) + '</pre></details></td></tr>')
    headers = ''.join('<th>' + escape(n.replace('_', ' ')) + '</th>' for n in CHECKS)
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Skill use audit</title><style>body{font:16px system-ui;margin:2rem;color:#172536}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:.6rem;border-bottom:1px solid #ccd3db;vertical-align:top}th{position:sticky;top:0;background:#edf2f7}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}summary{cursor:pointer}h1{font-size:1.5rem}.table{overflow:auto}</style><h1>' + escape(redact(summary)) + '</h1><p>Current PR heads. Job windows use dispatch timestamps. Dates use America/Chicago. VERIFIED requires every applicable check; a skill menu never counts. Source additions or modifications require codebase-design conservatively.</p><div class="table"><table><thead><tr><th>PR or job</th><th>Verdict</th>' + headers + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div></html>'


class Github:
    def __init__(self):
        self.last_call = 0.0

    def get(self, path, *, diff=False):
        clock.sleep(max(0, 2 - (clock.monotonic() - self.last_call)))
        args = ['gh', 'api', path]
        if diff:
            args += ['--header', 'Accept: application/vnd.github.diff']
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=120)
        finally:
            self.last_call = clock.monotonic()
        if result.returncode:
            raise ValueError('GitHub read failed; the audit is incomplete.')
        return result.stdout if diff else json.loads(result.stdout)

    def pages(self, path):
        page = 1
        while True:
            separator = '&' if '?' in path else '?'
            rows = self.get(path + separator + urlencode(dict(per_page=100, page=page)))
            yield from rows
            if len(rows) < 100:
                return
            page += 1

    def prs_on(self, day):
        start = datetime.combine(day, time.min, ZONE).astimezone(timezone.utc)
        end = datetime.combine(day + timedelta(days=1), time.min, ZONE).astimezone(timezone.utc)
        found = []
        for repo in REPOS:
            for pr in self.pages(f'repos/{repo}/pulls?state=all&sort=updated&direction=desc'):
                updated = instant(pr['updated_at'])
                if updated < start:
                    break
                active = start <= updated < end or start <= instant(pr['created_at']) < end
                if not active and updated >= end:
                    # GitHub's current updated_at alone cannot recover older activity.
                    for event in self.pages(f'repos/{repo}/issues/{pr["number"]}/timeline'):
                        stamp = event.get('created_at') or event.get('committer', {}).get('date')
                        if stamp and start <= instant(stamp) < end:
                            active = True
                            break
                if active:
                    found.append((f'{repo}#{pr["number"]}', pr))
        return found


def dispatch_jobs(home):
    path = home / '.config/carr/hermes-dispatch-results.jsonl'
    if not path.exists():
        return []
    rows = {}
    with path.open() as source:
        for number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                raise ValueError(f'Dispatch evidence is malformed at line {number}.') from None
            if row.get('msg_id'):
                rows[row['msg_id']] = row
    return list(rows.values())


def collect_sessions(home, since):
    from skill_audit import load_session
    paths = list((home / '.codex/sessions').glob('*/*/*/rollout-*.jsonl'))
    paths += list((home / '.claude/projects').glob('*/*.jsonl'))
    sessions = []
    for path in paths:
        if path.is_symlink() or path.stat().st_mtime < since.timestamp():
            continue
        sessions.append(load_session(path, start=since.isoformat()))
    return sessions


def missing_job(job):
    return dict(subject='job ' + job.get('desk', 'unknown') + '/' + job.get('msg_id', 'unknown'),
                verdict='UNATTRIBUTED', checks={}, reason='No readable session window and PR diff are attributable to this completed job.')


def daily_summary(day, reports, jobs):
    counts = Counter(r['verdict'] for r in reports)
    missing_tdd = sum(any(r.get('checks', {}).get(name, {}).get('verdict') == 'MISSING'
                          for name in ('tdd', 'tdd_behavior')) for r in reports)
    return (f"{day}: {len(reports)} PRs, {counts['VERIFIED']} verified, {missing_tdd} missing TDD, "
            f"{counts['UNATTRIBUTED']} unattributed, {counts['MISSING']} with missing evidence; "
            f"{len(jobs)} completed jobs, {sum(j['verdict']=='VERIFIED' for j in jobs)} verified.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--pr', help='OWNER/REPO#N, audited at its current head')
    selection.add_argument('--date', type=date.fromisoformat, help='America/Chicago calendar day')
    parser.add_argument('--html', type=Path, help='write the same audit as an HTML page')
    parser.add_argument('--expected-head', help='refuse if the current PR head differs from the approval pin')
    args = parser.parse_args(argv)
    github = Github()
    try:
        if args.pr:
            match = re.fullmatch(r'([\w.-]+/[\w.-]+)#([1-9]\d*)', args.pr)
            if not match:
                parser.error('--pr must be OWNER/REPO#N')
            pr_list = [(args.pr, github.get(f'repos/{match[1]}/pulls/{match[2]}'))]
            if args.expected_head and args.expected_head != pr_list[0][1]['head']['sha']:
                raise ValueError('Current PR head differs from the approved head.')
        else:
            if args.expected_head:
                parser.error('--expected-head requires --pr')
            pr_list = github.prs_on(args.date)
        home = Path.home()
        jobs = dispatch_jobs(home)
        selected_jobs = [j for j in jobs if j.get('status') == 'completed' and j.get('dispatched_at')
                         and args.date and instant(j['dispatched_at']).astimezone(ZONE).date() == args.date]
        stamps = [instant(pr['created_at']) for _, pr in pr_list] + [instant(j['dispatched_at']) for j in selected_jobs]
        since = min(stamps) - timedelta(days=1) if stamps else datetime.combine(args.date, time.min, ZONE)
        # Dispatch starts, including resumed threads, are stronger bounds than PR creation.
        refs = {pr.lower() for pr, _ in pr_list}
        related = [j for j in jobs if refs & references(j.get('result', '') + '\n' + j.get('task', ''))]
        if related:
            since = min(since, *(instant(j['dispatched_at']) for j in related))
        windows = session_windows(collect_sessions(home, since), jobs)
        reports = []
        for pr, info in pr_list:
            reports.append(audit_remote_pr(github, pr, info, windows))
        job_reports = []
        for job in selected_jobs:
            candidates = [w for w in windows if w.get('job_id') == job['msg_id']]
            report = missing_job(job)
            if candidates:
                window = candidates[-1]
                linked = [r for r in reports if r.get('job_id') == job['msg_id']]
                if linked:
                    report = {**linked[-1], 'subject': report['subject']}
                elif not any(e.get('edits') for e in window['events']):
                    report = audit_events(window['events'], diff='', brief=window['brief'])
                    report['subject'] = missing_job(job)['subject']
            job_reports.append(report)
        if args.date:
            summary = daily_summary(args.date.isoformat(), reports, job_reports)
        else:
            report = reports[0]
            summary = f"{report['subject']} at {report['head']}: {report['verdict']}"
        print(summary)
        for report in reports + job_reports:
            cells = ' | '.join(report.get('checks', {}).get(n, {}).get('verdict', 'MISSING') for n in CHECKS)
            print(f"{report['subject']} | {report['verdict']} | {cells}")
            print('\n'.join(evidence_lines(report)) or '  ' + report.get('reason', 'No evidence.'))
        if args.html:
            args.html.parent.mkdir(parents=True, exist_ok=True)
            args.html.write_text(render_html(summary, reports + job_reports))
        return 0 if all(r['verdict'] == 'VERIFIED' for r in reports + job_reports) else 1
    except (ValueError, OSError, subprocess.SubprocessError, KeyError) as exc:
        from skill_audit import redact
        print('Audit incomplete: ' + redact(str(exc)), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
