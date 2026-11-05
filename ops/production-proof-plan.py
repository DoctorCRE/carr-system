#!/usr/bin/env python3
"""Execute proof fixtures at admission without attesting production behavior."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from delivery_plan import Source, bind_requirements, classify_paths, requirement_paths, validate

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = 'contracts/production-proof-plan.v1.json'


def read(path):
    if path.stat().st_size > 1048576:
        raise ValueError('input exceeds 1 MiB')
    return path.read_text()


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, timeout=15).strip()


def product_paths(paths, contract):
    return classify_paths(paths, contract)['product_paths']


def local_file(root, name, prefix):
    if (not isinstance(name, str) or not name.startswith(prefix)
            or any(part in {'', '.', '..'} for part in name.split('/'))):
        raise ValueError('invalid fixture path')
    path = root / name
    if any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
        raise ValueError('fixture path escape or symlink')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('fixture path escape')
    if not path.is_file():
        raise ValueError('fixture receipt file missing: ' + name)
    return path


def check_receipt(execution, receipt, repo, deadline):
    for check in execution['checks']:
        if time.monotonic() >= deadline:
            raise TimeoutError('proof execution deadline exceeded')
        path, operator, expected = check['path'], check['operator'], check['value']
        if not re.fullmatch(r'[a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)*', path):
            raise ValueError('invalid receipt check path')
        if operator == 'equals' and (path.split('.')[-1] in {'source_sha', 'source_commit', 'release_sha', 'sha'}):
            raise ValueError('release SHA equality is not ancestry; use contains_commit')
        actual = receipt
        try:
            for key in path.split('.'):
                actual = actual[key]
        except (KeyError, TypeError):
            raise ValueError('receipt missing check field: ' + path) from None
        if operator == 'equals':
            passed = type(actual) is type(expected) and actual == expected
        elif operator == 'at_least':
            if type(expected) is not int or expected < 0:
                raise ValueError('at_least requires a nonnegative integer')
            passed = type(actual) is int and actual >= expected
        elif operator == 'contains_commit':
            if not all(isinstance(v, str) and re.fullmatch(r'[0-9a-f]{40}', v) for v in (expected, actual)):
                raise ValueError('ancestry requires full commit SHAs')
            env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
            env.update(GIT_NO_REPLACE_OBJECTS='1', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_GRAFT_FILE=os.devnull)
            def git(*args):
                return subprocess.run(['git', '--no-replace-objects', '-C', str(repo), *args],
                    env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, timeout=max(0.001, min(15, deadline - time.monotonic())))
            passed = all(git('cat-file', '-t', sha).stdout.strip() == b'commit'
                         for sha in (expected, actual)) and git('merge-base', '--is-ancestor', expected, actual).returncode == 0
        else:
            raise ValueError('unregistered receipt operator')
        if not passed:
            raise ValueError('receipt predicate failed: ' + path)


def execution_budget(requirement):
    return {'deadline': time.monotonic() + requirement['safety']['max_runtime_seconds'],
            'bytes': requirement['safety']['max_artifact_bytes']}


def assertion_execution(assertion, root, contract, budget, *, fixtures=True, receipt=None, repo=None):
    if ('execution' in assertion) == ('deferred' in assertion):
        raise ValueError('assertion requires exactly one execution or deferred declaration')
    if 'deferred' in assertion:
        return 'DEFERRED ' + assertion['id'] + ' blocked by ' + assertion['deferred']['blocking_unit']
    deadline = min(budget['deadline'], time.monotonic() + assertion['deadline_seconds'])
    runner = contract['x-runners'][assertion['runner']]
    if runner.get('fixture_interface') != 'receipt-predicate.v1':
        raise ValueError('registered runner has no fixture interface for this declaration')
    execution = assertion['execution']
    if execution['command'] != ['python3', runner['path'], '--observe-receipt']:
        raise ValueError('command must match the registered receipt invocation')
    binding = runner.get('sources', {}).get(runner['path'])
    if binding != hashlib.sha256((ROOT / runner['path']).read_bytes()).hexdigest():
        raise ValueError('registered receipt runner source binding mismatch')
    if not fixtures:
        check_receipt(execution, receipt, repo, deadline)
        return 'receipt predicate observed; provenance and production acceptance unproven'
    for name in ('fixture_pass', 'fixture_fail'):
        text = read(local_file(root, execution[name], 'proofs/fixtures/'))
        budget['bytes'] -= len(text.encode())
        if budget['bytes'] < 0:
            raise ValueError('fixture receipts exceed declared artifact bytes')
        fixture = json.loads(text)
        try:
            check_receipt(execution, fixture, root, deadline)
        except ValueError as error:
            if name == 'fixture_pass':
                raise ValueError('passing fixture refused: ' + str(error)) from None
        else:
            if name == 'fixture_fail':
                raise ValueError('failing fixture unexpectedly passed')
    return 'fixture executed ' + assertion['id'] + ' (pass accepted; fail refused; live unproven)'


def admission(body, paths, root, contract):
    product = product_paths(paths, contract)
    clean = re.sub(r'<!--.*?-->', '', body, flags=re.S)
    headings = list(re.finditer(r'^##\s+(.+?)\s*$', clean, re.M))
    matches = [i for i, h in enumerate(headings) if h[1].lower() == 'production proof']
    if len(matches) != 1:
        raise ValueError('exactly one Production proof section required')
    i = matches[0]
    section = clean[headings[i].end():headings[i+1].start() if i+1 < len(headings) else len(clean)].strip()
    non_product = section.lower().startswith('n/a:')
    if non_product:
        reason = section[4:].strip()
        if product or len(reason) < 3 or reason.lower() in {'todo', 'tbd', 'placeholder'}:
            raise ValueError('n/a requires non-product paths and a real reason')
    match = re.fullmatch(r'`?(proofs/requirements/[A-Za-z0-9][A-Za-z0-9._-]*\.json)`?', section)
    if not non_product and not match:
        raise ValueError('Production proof must name one proofs/requirements/<change>.json')
    if not non_product and match[1] not in requirement_paths(paths):
        raise ValueError('Production proof must name a changed requirement')
    root = root.resolve()

    def requirement_bytes(name):
        path = root / name
        if any(p.is_symlink() for p in [path, path.parent, path.parent.parent]) or not path.resolve().is_relative_to(root):
            raise ValueError('requirement path escape or symlink')
        return read(path).encode()

    covered = set()
    if requirement_paths(paths):
        source = Source(root)
        sha = source.git('rev-parse', 'HEAD').decode().strip()
        _, covered = bind_requirements(paths, contract, source, sha, requirement_bytes)
    required_targets = set(classify_paths(paths, contract)['required_targets'])
    if required_targets - covered:
        raise ValueError('changed runtime target missing from declaration')
    results = []
    deferred = 0
    for name in requirement_paths(paths):
        requirement = json.loads(requirement_bytes(name))
        budget = execution_budget(requirement)
        results.extend(assertion_execution(a, root, contract, budget) for a in requirement['assertions'])
        deferred += sum('deferred' in a for a in requirement['assertions'])
    if results:
        print(json.dumps({'schema': 'carr-proof-admission-receipt.v1',
            'state': 'deferred' if deferred else 'fixtures-verified',
            'fixture_runs': len(results) - deferred, 'deferred_assertions': deferred}))
    if non_product:
        return 'non-product declaration accepted (admission only)' + ('; ' + '; '.join(results) if results else '')
    return 'proof declaration accepted (admission only; execution unproven); ' + '; '.join(results)


def activated(created, edited, activation):
    parse = lambda value: datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parse(created) >= parse(activation) or bool(edited and parse(edited) >= parse(activation))


def github_metadata(event):
    owner, name = event['repository']['full_name'].split('/')
    query = ('query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name)'
             '{pullRequest(number:$number){createdAt lastEditedAt body headRefOid}}}')
    result = json.loads(command('gh', 'api', 'graphql', '-f', 'query=' + query,
                               '-f', 'owner=' + owner, '-f', 'name=' + name,
                               '-F', 'number=' + str(event['pull_request']['number'])))
    pr = result['data']['repository']['pullRequest']
    if pr['body'] != (event['pull_request']['body'] or '') or pr['headRefOid'] != event['pull_request']['head']['sha']:
        raise ValueError('stale PR event; current body/head differs')
    return pr


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--body-file', type=Path)
    parser.add_argument('--files-file', type=Path)
    parser.add_argument('--base', default='origin/main')
    parser.add_argument('--observe-receipt', action='store_true')
    parser.add_argument('--requirement', type=Path)
    parser.add_argument('--assertion')
    parser.add_argument('--receipt-file', type=Path)
    parser.add_argument('--repo', type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        contract = json.loads(read(ROOT / CONTRACT_PATH))
        if args.observe_receipt:
            if not all((args.requirement, args.assertion, args.receipt_file)):
                raise ValueError('--requirement, --assertion and --receipt-file required')
            requirement = json.loads(read(args.requirement))
            validate(requirement, contract)
            matches = [a for a in requirement['assertions'] if a['id'] == args.assertion]
            if len(matches) != 1 or 'deferred' in matches[0]:
                raise ValueError('one executable assertion required; deferred proof is not observable')
            result = assertion_execution(matches[0], args.root, contract, execution_budget(requirement), fixtures=False,
                receipt=json.loads(read(args.receipt_file)), repo=args.repo)
            print('Production proof: ' + result)
            return 0
        if args.body_file:
            body = read(args.body_file)
            base = args.base
            head = 'HEAD'
        elif os.environ.get('GITHUB_EVENT_NAME') == 'pull_request':
            event = json.loads(read(Path(os.environ['GITHUB_EVENT_PATH'])))
            base = event['pull_request']['base']['sha']
            head = event['pull_request']['head']['sha']
            history = command('git', 'log', '--first-parent', '--diff-filter=A', '--format=%cI', base, '--', CONTRACT_PATH)
            if not history:
                print('Production proof: rollout not merged into base; admission not required')
                return 0
            metadata = github_metadata(event)
            if not activated(metadata['createdAt'], metadata['lastEditedAt'], history.splitlines()[-1]):
                print('Production proof: unchanged pre-rollout PR is grandfathered')
                return 0
            body = event['pull_request']['body'] or ''
        else:
            print('Production proof: PR admission not evaluated; local use --body-file and --base')
            return 0
        paths = json.loads(read(args.files_file)) if args.files_file else command(
            'git', 'diff', '--name-only', '--no-renames', '-z', base + '...' + head).rstrip('\0').split('\0')
        print('Production proof: ' + admission(body, paths, args.root, contract))
        return 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print('Production proof REFUSED: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__))
        return 1


if __name__ == '__main__':
    sys.exit(main())
