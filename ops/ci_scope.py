"""Select affected CI classes. Uncertain inputs always require the full suite."""
from __future__ import annotations

import argparse
from fnmatch import fnmatchcase
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DOC_PATHS = ('docs/**', '.agents/skills/**', 'tools/pstack/*.md',
             'AGENTS.md', 'CLAUDE.md', 'README.md', 'CONTRIBUTING.md', 'CHANGELOG.md')
INFRA_PREFIXES = ('ops/', '.github/', 'contracts/', 'migrations/')
PRODUCT_CLASSES = {'lint', 'pushfloor', 'unit', 'types', 'gates', 'secret', 'binding', 'artifact'}
PATH_CLASSES = (
    ('practice-plugin/', PRODUCT_CLASSES),
)
PR_FAST_CLASSES = {'lint', 'pushfloor', 'types', 'secret', 'binding', 'artifact'}
PR_JOBS = {
    'lint': {'lint'},
    'fast': {'pushfloor', 'unit', 'types', 'binding', 'artifact'},
    'gates': {'gates'},
    'secret': {'secret'},
    'unit': {'unit'},
    'unit-coverage': {'unit'},
    'launchd-python39': {'gates'},
}


def select_paths(paths, all_classes):
    all_classes = set(all_classes)
    selected = {'lint'}
    if not paths:
        return all_classes
    for path in paths:
        name = Path(path).name
        if (path.startswith(INFRA_PREFIXES) or name.endswith(('.lock', '.lockb', '.lock.json')) or name in
                ('package-lock.json', 'npm-shrinkwrap.json', 'pnpm-lock.yaml', 'go.sum',
                 'package.json', 'Cargo.toml', 'requirements.txt', 'pyproject.toml')):
            return all_classes
        if any(fnmatchcase(path, pattern) for pattern in DOC_PATHS):
            continue
        matched = next((classes for prefix, classes in PATH_CLASSES if path.startswith(prefix)), None)
        if matched is None:
            return all_classes
        selected.update(matched)
    return selected & all_classes


def changed_paths(root, base, head):
    return subprocess.check_output(
        ['git', 'diff', '--name-only', '--no-renames', '-z', base, head], cwd=root,
        stderr=subprocess.DEVNULL, timeout=30).decode('utf-8').rstrip('\0').split('\0')


def diff_revisions(root, base, environ):
    if environ.get('GITHUB_EVENT_NAME') == 'pull_request':
        event = json.loads(Path(environ['GITHUB_EVENT_PATH']).read_text())['pull_request']
        base, head = (event[key]['sha'] for key in ('base', 'head'))
        if not all(isinstance(sha, str) and re.fullmatch('[0-9a-f]{40}', sha) for sha in (base, head)):
            raise ValueError('invalid PR revisions')
    else:
        head = 'HEAD'
    merge_base = subprocess.check_output(['git', 'merge-base', base, head], cwd=root,
                                        text=True, stderr=subprocess.DEVNULL, timeout=30).strip()
    return merge_base, head


def selection(all_classes, *, root=ROOT, base=None, environ=os.environ):
    event_name = environ.get('GITHUB_EVENT_NAME', '')
    full = {'classes': sorted(all_classes), 'reason': 'full suite outside PR selection'}
    if event_name and event_name != 'pull_request' or not (event_name == 'pull_request' or base):
        return full
    try:
        base, head = diff_revisions(root, base, environ)
        paths = changed_paths(root, base, head)
        return {'classes': sorted(select_paths(paths, all_classes)),
                'reason': 'merge-base changed paths', 'paths': paths}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return {**full, 'reason': 'diff unavailable; conservative full suite'}


def required_jobs(classes):
    if 'dependency' in classes:
        return ['classes', 'scope', 'migration-fixtures', 'unit', 'unit-coverage', 'launchd-python39']
    return sorted(job for job, affected in PR_JOBS.items() if affected.intersection(classes))


def main():
    from ci import CLASS_DESCRIPTIONS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base')
    parser.add_argument('--hosted', action='store_true')
    args = parser.parse_args()
    result = ({'classes': sorted(PR_FAST_CLASSES), 'reason': 'PR fast subset; full suite guards main'}
              if args.hosted and os.environ.get('GITHUB_EVENT_NAME') == 'pull_request'
              else selection(CLASS_DESCRIPTIONS, base=args.base))
    result['full'] = set(result['classes']) == set(CLASS_DESCRIPTIONS)
    result['jobs'] = required_jobs(result['classes'])
    print(json.dumps(result, sort_keys=True))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write(f'full={str(result["full"]).lower()}\n')
            output.write('classes=' + json.dumps(result['classes']) + '\n')
            for job in PR_JOBS:
                output.write(f'{job}={str(job in result["jobs"]).lower()}\n')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write('CI path selection\n\n```json\n' + json.dumps(result, indent=2) + '\n```\n')


if __name__ == '__main__':
    main()
