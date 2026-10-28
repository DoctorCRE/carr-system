#!/usr/bin/env python3
"""Build and verify CI evidence, including per-program execution records."""
# doctrine: engineering-workflow-sop
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = 'ci-tree-evidence'


def class_groups(root=ROOT):
    text = (root / '.github/workflows/ci.yml').read_text()
    block = text.split('        classes:\n', 1)[1].split('    #', 1)[0]
    return re.findall(r'^          - "([^"]+)"', block, re.M)


def class_jobs(root=ROOT):
    """Read display-name admission from the workflow's class matrix metadata."""
    script = """const yaml=require('js-yaml'),fs=require('fs');
    const job=yaml.load(fs.readFileSync(0,'utf8')).jobs.classes;
    console.log(JSON.stringify({name:job.name,matrix:job.strategy.matrix}));"""
    job = json.loads(subprocess.check_output(['node', '-e', script],
        input=(root / '.github/workflows/ci.yml').read_text(),
        cwd=root / 'mcp-server', text=True, timeout=30))
    if len(job['matrix']['job_names']) != 1:
        raise ValueError('CI class names must be a single metadata dimension')
    groups = {}
    for row in job['matrix']['job_names'][0].values():
        if row['name'] in groups or row['classes'] not in job['matrix']['classes']:
            raise ValueError('ambiguous or unknown CI class job')
        groups[row['name']] = row['classes']
    if set(groups.values()) != set(job['matrix']['classes']):
        raise ValueError('CI class job metadata is incomplete')
    return groups


def hosted_verdict(*, drift=False):
    """Use the scheduler's terminal results, never the eventually consistent jobs API."""
    if os.environ.get('CI_DRAFT') != 'false':
        message = 'Draft: deferred; no full-suite evidence'
        print(message)
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write(message + '\n')
        return 1
    needs = json.loads(os.environ['CI_NEEDS'])
    lanes = ('check',) if drift else ('scope', 'classes', 'migration-fixtures')
    results = {name: needs[name]['result'] for name in lanes}
    allowed = {name: {'success'} for name in lanes}
    if not drift:
        scope = needs['scope']
        full = scope.get('outputs', {}).get('full') if scope['result'] == 'success' else None
        if full not in ('true', 'false'):
            print('CI verdict: scope receipt unavailable; refusing success')
            return 1
        if full == 'false':
            allowed['migration-fixtures'].add('skipped')
    lines = [f"{name}: {'PASSED' if result == 'success' else str(result).upper()}"
             for name, result in results.items()]
    print('\n'.join(lines))
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
        summary.write('Job dependency results:\n\n' + '\n'.join('- ' + line for line in lines) + '\n')
    return 0 if all(result in allowed[name] for name, result in results.items()) else 1


def contract_digest(root=ROOT):
    digest = hashlib.sha256()
    for name in ('.github/workflows/ci.yml', 'ops/ci-evidence.py', 'ops/ci.sh', 'ops/ci-unit.py',
                 'ops/ci-pushfloor.py', 'ops/ci-migration.py', 'ops/ci-gates.py', 'ops/ci-secret.py',
                 'ops/node_test_summary.py',
                 'ops/ci-node-file.mjs',
                 'requirements.lock',
                 'mcp-server/package-lock.json', '.nvmrc', '.python-version'):
        digest.update(name.encode() + b'\0' + (root / name).read_bytes() + b'\0')
    return digest.hexdigest()


def can_reuse(run, jobs, receipt, *, tree, head_tree, tested_tree, contract, groups):
    required = {'ops/ci.sh --strict', *('ops/ci.sh --strict --only ' + g for g in groups)}
    green = {j.get('name') for j in jobs if j.get('conclusion') == 'success'}
    return (run.get('name') == 'CI' and run.get('event') == 'pull_request'
            and run.get('status') == 'completed' and run.get('conclusion') == 'success'
            and required <= green and receipt.get('schema') == 'carr-ci-evidence/v1'
            and receipt.get('event') == 'pull_request' and receipt.get('draft') is False
            and receipt.get('run_id') == run.get('id')
            and receipt.get('run_attempt') == run.get('run_attempt', 1)
            and receipt.get('head_sha') == run.get('head_sha')
            and receipt.get('tree_sha') == tree == head_tree == tested_tree
            and receipt.get('contract') == contract and receipt.get('groups') == groups)


class GitHub:
    def __init__(self, repo):
        self.repo = repo

    def api(self, path, *, binary=False):
        result = subprocess.run(['gh', 'api', f'repos/{self.repo}/{path}'],
                                capture_output=True, timeout=60, check=True)
        return result.stdout if binary else json.loads(result.stdout)

    def tree(self, sha):
        return self.api(f'git/commits/{sha}')['tree']['sha']

    def receipt(self, run):
        artifacts = self.api(f'actions/runs/{run["id"]}/artifacts?per_page=100')['artifacts']
        matches = [a for a in artifacts if a['name'] == ARTIFACT and not a['expired']]
        if len(matches) != 1:
            return None
        raw = self.api(f'actions/artifacts/{matches[0]["id"]}/zip', binary=True)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            return json.loads(archive.read('ci-evidence.json'))


def resolve(github, sha, tree, contract, groups):
    for pr in github.api(f'commits/{sha}/pulls'):
        if not pr.get('merged_at') or pr['base']['ref'] != 'main':
            continue
        head = pr['head']['sha']
        if github.tree(head) != tree:
            continue
        runs = github.api(f'actions/workflows/ci.yml/runs?event=pull_request&head_sha={head}&per_page=100')['workflow_runs']
        if not runs:
            continue
        run = max(runs, key=lambda r: (r['id'], r.get('run_attempt', 1)))
        if run.get('status') != 'completed' or run.get('conclusion') != 'success':
            continue
        receipt = github.receipt(run)
        if receipt is None:
            continue
        jobs = github.api(f'actions/runs/{run["id"]}/jobs?per_page=100')['jobs']
        if can_reuse(run, jobs, receipt, tree=tree, head_tree=github.tree(head),
                     tested_tree=github.tree(receipt['tested_sha']),
                     contract=contract, groups=groups):
            return {'reused': True, 'source_run_id': run['id'],
                    'source_run_attempt': run.get('run_attempt', 1),
                    'source_pr': pr['number'], 'source_head_sha': head,
                    'source_tested_sha': receipt['tested_sha']}
    return {'reused': False}


def program_records(rows, output, *, environ=os.environ, root=ROOT):
    """Bind ci.sh's per-program rows to the tested source (WR-000542).

    Rows are class, name, exit, start, end. Every attempt keeps its own rows in
    execution order, so a retried program still shows its first failure. A
    record already at `output` for the same run, attempt, job and source is
    extended (local-pg-ci adds its acceptance programs after ci.sh exits).
    """
    programs = []
    for line in (rows.read_text().splitlines() if rows.is_file() else []):
        klass, name, code, start, end = line.split('\t')
        elapsed = float(end.replace(',', '.')) - float(start.replace(',', '.'))
        programs.append({'class': klass, 'program': name, 'exit': int(code),
                         'elapsed_s': round(elapsed, 3)})
    number = lambda key: int(environ[key]) if environ.get(key, '').isdigit() else None
    header = {'schema': 'carr-ci-programs/v1', 'run_id': number('GITHUB_RUN_ID'),
              'run_attempt': number('GITHUB_RUN_ATTEMPT'), 'job': environ.get('GITHUB_JOB'),
              'tested_sha': git('HEAD', root), 'tree_sha': git('HEAD^{tree}', root),
              'contract': contract_digest(root)}
    if output.is_file():
        previous = json.loads(output.read_text())
        if {k: previous.get(k) for k in header} == header:
            programs = previous['programs'] + programs
    output.write_text(json.dumps({**header, 'programs': programs}, indent=2) + '\n')
    return header | {'programs': programs}


def git(value, root=ROOT):
    return subprocess.check_output(['git', 'rev-parse', value], cwd=root, text=True).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['record', 'resolve', 'verdict', 'drift-verdict', 'programs'])
    parser.add_argument('--output')
    parser.add_argument('--rows', type=Path)
    args = parser.parse_args()
    if args.action == 'programs':
        if not (args.output and args.rows):
            parser.error('programs needs --rows and --output')
        program_records(args.rows, Path(args.output))
        return 0
    if args.action in ('verdict', 'drift-verdict'):
        try:
            return hosted_verdict(drift=args.action == 'drift-verdict')
        except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
            print('CI verdict: dependency results unavailable; refusing success')
            return 1
    if not args.output:
        parser.error('--output is required for record/resolve')
    if args.action == 'record':
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        if os.environ['GITHUB_EVENT_NAME'] == 'pull_request' and event['pull_request']['draft'] is not False:
            print('Draft: deferred; no full-suite evidence')
            return 1
    groups, contract = class_groups(), contract_digest()
    sha, tree = git('HEAD'), git('HEAD^{tree}')
    if args.action == 'record':
        result = {'schema': 'carr-ci-evidence/v1', 'run_id': int(os.environ['GITHUB_RUN_ID']),
                  'run_attempt': int(os.environ['GITHUB_RUN_ATTEMPT']),
                  'event': os.environ['GITHUB_EVENT_NAME'], 'action': event.get('action'),
                  'draft': event.get('pull_request', {}).get('draft', False),
                  'head_sha': event.get('pull_request', {}).get('head', {}).get('sha', sha),
                  'tested_sha': sha, 'tree_sha': tree, 'contract': contract, 'groups': groups}
    else:
        try:
            result = resolve(GitHub(os.environ['GITHUB_REPOSITORY']), sha, tree, contract, groups)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile):
            result = {'reused': False, 'reason': 'source evidence unavailable; run classes'}
        result.update(main_sha=sha, tree_sha=tree, contract=contract, groups=groups)
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write(f'reused={str(result["reused"]).lower()}\n')
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write('## Main canary evidence\n\n' + json.dumps(result, sort_keys=True) + '\n')
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
