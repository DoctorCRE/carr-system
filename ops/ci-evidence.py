#!/usr/bin/env python3
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


def contract_digest(root=ROOT):
    digest = hashlib.sha256()
    for name in ('.github/workflows/ci.yml', 'ops/ci.sh', 'requirements.lock',
                 'mcp-server/package-lock.json', '.nvmrc', '.python-version'):
        digest.update(name.encode() + b'\0' + (root / name).read_bytes() + b'\0')
    return digest.hexdigest()


def can_reuse(run, jobs, receipt, *, tree, head_tree, tested_tree, contract, groups):
    required = {'ops/ci.sh --strict', *('ops/ci.sh --strict --only ' + g for g in groups)}
    green = {j.get('name') for j in jobs if j.get('conclusion') == 'success'}
    return (run.get('name') == 'CI' and run.get('event') == 'pull_request'
            and run.get('status') == 'completed' and run.get('conclusion') == 'success'
            and required <= green and receipt.get('schema') == 'carr-ci-evidence/v1'
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


def git(value):
    return subprocess.check_output(['git', 'rev-parse', value], cwd=ROOT, text=True).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['record', 'resolve'])
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    groups, contract = class_groups(), contract_digest()
    sha, tree = git('HEAD'), git('HEAD^{tree}')
    if args.action == 'record':
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        result = {'schema': 'carr-ci-evidence/v1', 'run_id': int(os.environ['GITHUB_RUN_ID']),
                  'run_attempt': int(os.environ['GITHUB_RUN_ATTEMPT']),
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
