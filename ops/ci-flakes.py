#!/usr/bin/env python3
"""Measure same-tree CI flakes and propose deduplicated GitHub fix loops."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import re
import subprocess
import zipfile
from urllib.parse import quote
import json
from pathlib import Path


def analyze(observations):
    failures = {}
    rows = []
    for item in sorted(observations, key=lambda row: row['at']):
        key = (item.get('repo'), item['workflow'], item['tree'], item['test'])
        if item['result'] == 'fail':
            failures[key] = item
        elif item['result'] == 'pass' and key in failures:
            failure = failures.pop(key)
            rows.append({'repo': item.get('repo'), 'test': item['test'], 'tree': item['tree'],
                'workflow': item['workflow'], 'fail_url': failure['url'], 'pass_url': item['url']})
    return rows


def node_outcomes(text):
    outcomes = {}
    for line in text.splitlines():
        match = re.search(r'(?<!\w)(not ok|ok)\s+\d+\s+(?:-\s+)?(\S+\.test\.(?:mjs|js))(?=\s|$)', line)
        if match:
            outcomes[Path(match[2]).name] = ('fail' if match[1] == 'not ok' else 'pass', line)
    return outcomes


def observations_from_logs(repo, run, attempt, logs, tests):
    observations = []
    for filename, text in logs.items():
        if '/' in filename:
            continue
        text = re.sub(r'\x1b\[[0-9;]*m', '', text)
        match = re.search(r'git log -1 --format=%H[^\n]*\n[^\n]*?\b([a-f0-9]{40})\b', text)
        if not match:
            continue
        sha = match[1]
        for test in tests:
            base = Path(test).name
            if test.startswith(('ops/', 'tools/')):
                failed = any('FAIL' in line and re.search(r'(?<![A-Za-z0-9_-])' + re.escape(base) + r'(?![A-Za-z0-9_-])', line)
                             for line in text.splitlines())
                passed = bool(re.search(r'\bOK\s+gates\s', text))
                if re.search(r'not run[^\n]*' + re.escape(base), text, re.I) or re.search(r'NOT RUN:[^\n]*' + re.escape(base), text):
                    passed = False
            else:
                outcome = node_outcomes(text).get(base)
                failed = outcome is not None and outcome[0] == 'fail'
                passed = (outcome is not None and outcome[0] == 'pass') or bool(re.search(r'\bOK\s+unit\s', text))
            if failed or passed:
                event_lines = [line for line in text.splitlines()
                    if (failed and 'FAIL' in line and base in line)
                    or (not failed and re.search(r'\bOK\s+(gates|unit)\s', line))]
                timestamp = re.match(r'(\d{4}-\d{2}-\d{2}T\S+)', event_lines[-1]) if event_lines else None
                at = timestamp[1] if timestamp else run['created_at'] + f':{attempt:04d}'
                observations.append({'repo': repo, 'test': test, 'source_sha': sha, 'tree': sha,
                    'workflow': run['name'], 'result': 'fail' if failed else 'pass',
                    'at': at,
                    'url': f'https://github.com/{repo}/actions/runs/{run["id"]}/attempts/{attempt}'})
    return observations


def gh_api(endpoint, payload=None, binary=False):
    command = ['gh', 'api', endpoint]
    if payload is not None:
        command += ['-X', 'POST', '--input', '-']
    result = subprocess.run(command, input=json.dumps(payload).encode() if payload is not None else None,
        capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError(f'GitHub API failed for {endpoint}: {result.stderr.decode(errors="replace").strip()}')
    return result.stdout if binary else json.loads(result.stdout)


def paged(endpoint):
    rows = []
    for page in range(1, 1000):
        batch = gh_api(endpoint + ('&' if '?' in endpoint else '?') + f'per_page=100&page={page}')
        rows.extend(batch)
        if len(batch) < 100:
            return rows
    raise RuntimeError('pagination did not finish')


def list_runs(repo, start, end, cache):
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f'v2:{repo}:{start}:{end}'.encode()).hexdigest()[:20]
    target = cache / f'runs-{key}.json'
    if target.exists():
        return json.loads(target.read_text())
    endpoint = f"repos/{repo}/actions/runs?created={quote(start.isoformat(timespec='seconds'))}..{quote(end.isoformat(timespec='seconds'))}&per_page=100"
    first = gh_api(endpoint)
    if first['total_count'] > 1000:
        middle = start + (end - start) / 2
        if end - start < timedelta(seconds=2):
            raise RuntimeError('more than 1000 runs per second; cannot prove full coverage')
        runs = list_runs(repo, start, middle, cache) + list_runs(repo, middle, end, cache)
        runs = list({row['id']: row for row in runs}.values())
    else:
        runs = first['workflow_runs']
        for page in range(2, (first['total_count'] + 99) // 100 + 1):
            runs += gh_api(endpoint + f'&page={page}')['workflow_runs']
    target.write_text(json.dumps(runs))
    return runs


def read_logs(repo, run_id, attempt, cache):
    target = cache / f'logs-{run_id}-{attempt}.zip'
    if not target.exists():
        content = gh_api(f'repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/logs', binary=True)
        target.write_bytes(content)
    with zipfile.ZipFile(target) as archive:
        return {name: archive.read(name).decode(errors='replace') for name in archive.namelist() if name.endswith('.txt')}


def failed_tests(logs, inventory):
    tests = set()
    by_base = {Path(name).name: name for name in inventory}
    for text in logs.values():
        clean = re.sub(r'\x1b\[[0-9;]*m', '', text)
        for line in clean.splitlines():
            if re.search(r'\b(FAIL|TIMEOUT)\b', line):
                for base in re.findall(r'([A-Za-z0-9_-]+(?:-selftest\.py|test[-_][A-Za-z0-9_-]+\.py|test[-_][A-Za-z0-9_-]+\.sh))', line):
                    tests.add(by_base.get(base, 'ops/' + base))
        for base, (outcome, _) in node_outcomes(clean).items():
            if outcome == 'fail':
                tests.add(by_base.get(base, 'mcp-server/test/' + base))
    return sorted(tests)


def history(repo, since, until, cache):
    runs = list_runs(repo, since, until, cache)
    selected = [r for r in runs if r['name'] in ('CI', 'main canary', 'e2e', 'DB acceptance')]
    groups = {}
    for run in selected:
        groups.setdefault((run['workflow_id'], run['head_sha']), []).append(run)
    candidates = [r for group in groups.values()
        if any(r['run_attempt'] > 1 for r in group)
        or (any(r['conclusion'] == 'failure' for r in group) and any(r['conclusion'] == 'success' for r in group))
        for r in group if r['conclusion'] in ('success', 'failure')]
    inventory = [str(p.relative_to(Path(__file__).resolve().parents[1]))
        for folder in ('ops', 'tools', 'mcp-server/test')
        for p in (Path(__file__).resolve().parents[1] / folder).rglob('*') if p.is_file()]
    tasks = [(run, attempt) for run in candidates for attempt in range(1, run['run_attempt'] + 1)]
    results = []
    gaps = []
    def fetch(task):
        run, attempt = task
        try:
            return run, attempt, read_logs(repo, run['id'], attempt, cache), None
        except (RuntimeError, zipfile.BadZipFile) as exc:
            return run, attempt, {}, str(exc)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for run, attempt, logs, error in pool.map(fetch, tasks):
            if error:
                gaps.append({'run': run['id'], 'attempt': attempt, 'error': error})
            else:
                results.append((run, attempt, logs))
    tests = sorted({test for _, _, logs in results for test in failed_tests(logs, inventory)})
    observations = [row for run, attempt, logs in results
        for row in observations_from_logs(repo, run, attempt, logs, tests)]
    rows = analyze(observations)
    return {'repo': repo, 'since': since.isoformat(), 'until': until.isoformat(), 'runs': len(runs),
        'ci_runs': len(selected), 'candidate_runs': len(candidates), 'log_attempts': len(tasks),
        'log_gaps': gaps, 'rows': rows, 'observations': observations,
        'identity': 'Checked-out commit from job git log -1 --format=%H; exact commit equality implies exact tree equality.'}


def propose(repo, rows):
    if repo not in ('jbookout/carr-system', 'jbookout/doctorcre-app'):
        raise ValueError('unsupported repository')
    issues = paged(f'repos/{repo}/issues?state=all')
    loops = []
    for row in rows:
        name = row['test']
        if not re.fullmatch(r'[A-Za-z0-9_./-]+\.(py|sh|mjs|js|tsx|ts)', name) or '..' in Path(name).parts:
            raise ValueError('invalid test path')
        key = hashlib.sha256(f'{repo}:{name}'.encode()).hexdigest()
        marker = f'<!-- ci-flake:{key} -->'
        issue = next((i for i in issues if 'pull_request' not in i and marker in (i.get('body') or '')), None)
        if issue is None:
            expiry = (datetime.now(timezone.utc).date() + timedelta(days=7)).isoformat()
            issue = gh_api(f'repos/{repo}/issues', {
                'title': f'Fix unreliable test: {name}',
                'body': f'{marker}\nOwner: QA Engineer (orchestrator).\nProposed quarantine expiry: {expiry}.\n\n'
                    f'{name} failed then passed on the same source tree.\n'
                    f'Failure: {row["fail_url"]}\nPass: {row["pass_url"]}\nSource identity: `{row["tree"]}`.\n\n'
                    'Propose an explicit quarantine entry with this loop, owner, reason and expiry. '
                    'This issue does not add quarantine automatically.\n'
                    'Trace the failure to its root cause, reproduce it, and fix the test or runner. '
                    'Verify repeated runs under the original CI concurrency, remove quarantine, then close this loop.'})
            issues.append(issue)
        elif issue.get('state') == 'closed':
            command = ['gh', 'api', f'repos/{repo}/issues/{issue["number"]}', '-X', 'PATCH', '-f', 'state=open']
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, timeout=120)
        loops.append({'test': name, 'loop': issue['html_url'], 'owner': 'qa-engineer'})
    return loops


def candidate_rows(repo, run, attempt, logs):
    if (run.get('head_repository') or {}).get('full_name') != repo or run['name'] not in ('CI', 'main canary'):
        return []
    rows = {}
    for filename, text in logs.items():
        if '/' in filename:
            continue
        checkout = re.search(r'git log -1 --format=%H[^\n]*\n[^\n]*?\b([a-f0-9]{40})\b', text)
        if not checkout:
            continue
        for line in text.splitlines():
            if 'CARR_FLAKE_RESULT ' not in line:
                continue
            try:
                receipt = json.loads(line.split('CARR_FLAKE_RESULT ', 1)[1])
            except json.JSONDecodeError:
                continue
            if (receipt.get('version') != 1 or receipt.get('candidate') is not True
                or receipt.get('sha') != checkout[1] or receipt.get('rerun_exit') != 0
                or not isinstance(receipt.get('first_exit'), int)
                or receipt['first_exit'] in (0, 78) or not 0 < receipt['first_exit'] < 124
                or receipt.get('fingerprint') != hashlib.sha256(b'').hexdigest()
                or not re.fullmatch(r'[a-f0-9]{40}', receipt.get('tree', ''))):
                continue
            url = f'https://github.com/{repo}/actions/runs/{run["id"]}/attempts/{attempt}'
            rows[receipt['test']] = {'repo': repo, 'test': receipt['test'], 'tree': receipt['tree'],
                'workflow': run['name'], 'fail_url': url, 'pass_url': url}
    if len(rows) > 20:
        raise ValueError('more than 20 candidates; inspect the CI runner before proposing')
    return list(rows.values())


def consume(repo, run_id, attempt, cache):
    run = gh_api(f'repos/{repo}/actions/runs/{run_id}/attempts/{attempt}')
    logs = read_logs(repo, run_id, attempt, cache)
    rows = candidate_rows(repo, run, attempt, logs)
    return propose(repo, rows) if rows else []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    analyzer = commands.add_parser('analyze')
    analyzer.add_argument('input')
    proposer = commands.add_parser('propose')
    proposer.add_argument('--repo', required=True)
    proposer.add_argument('--input', required=True)
    collector = commands.add_parser('history')
    collector.add_argument('--repo', required=True)
    collector.add_argument('--since', required=True)
    collector.add_argument('--until', default=datetime.now(timezone.utc).isoformat())
    collector.add_argument('--cache', required=True)
    collector.add_argument('--output', required=True)
    consumer = commands.add_parser('consume')
    consumer.add_argument('--repo', required=True)
    consumer.add_argument('--run-id', required=True, type=int)
    consumer.add_argument('--attempt', required=True, type=int)
    consumer.add_argument('--cache', required=True)
    reconciler = commands.add_parser('reconcile')
    reconciler.add_argument('--repo', required=True)
    reconciler.add_argument('--cache', required=True)
    args = parser.parse_args()
    if args.command == 'reconcile':
        end = datetime.now(timezone.utc)
        cache = Path(args.cache)
        cache.mkdir(parents=True, exist_ok=True)
        runs = list_runs(args.repo, end - timedelta(days=2), end, cache)
        for run in runs:
            if run['name'] in ('CI', 'main canary') and run['status'] == 'completed':
                print(json.dumps(consume(args.repo, run['id'], run['run_attempt'], cache)))
        return 0
    if args.command == 'consume':
        cache = Path(args.cache)
        cache.mkdir(parents=True, exist_ok=True)
        print(json.dumps(consume(args.repo, args.run_id, args.attempt, cache), indent=2))
        return 0
    if args.command == 'history':
        start = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(args.until).replace(tzinfo=timezone.utc)
        report = history(args.repo, start, end, Path(args.cache))
        Path(args.output).write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({k: v for k, v in report.items() if k not in ('observations', 'rows', 'log_gaps')}))
        print(f'{len(report["rows"])} same-commit failure/pass pairs; {len(report["log_gaps"])} unavailable log attempts')
        return 0
    if args.command == 'propose':
        print(json.dumps(propose(args.repo, json.loads(Path(args.input).read_text())), indent=2))
        return 0
    print(json.dumps(analyze(json.loads(Path(args.input).read_text())), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
