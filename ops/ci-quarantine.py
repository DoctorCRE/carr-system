#!/usr/bin/env python3
"""Run test suites, retain first failures, and publish same-tree flake receipts."""
import argparse
import hashlib
from datetime import date, datetime, timezone
import json
import os
import re
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / 'ops/config/ci-quarantine.json'
PREFIX = 'CARR_FLAKE_RESULT '


def load_manifest(path):
    data = json.loads(Path(path).read_text())
    if data.get('version') != 1 or not isinstance(data.get('tests'), list):
        raise ValueError('expected version 1 and tests array')
    entries = {}
    for entry in data['tests']:
        if not isinstance(entry, dict):
            raise ValueError('quarantine entries must be objects')
        if any(not isinstance(entry.get(field), str) or not entry[field].strip()
               for field in ('test', 'loop', 'owner', 'expires', 'reason')):
            raise ValueError('each entry needs test, loop, owner, expires and reason')
        name = entry['test']
        if name in entries or name.startswith('/') or '..' in Path(name).parts:
            raise ValueError(f'duplicate or unsafe test path: {name}')
        if not re.fullmatch(r'https://github\.com/jbookout/(carr-system|doctorcre-app)/issues/[1-9][0-9]*', entry['loop']):
            raise ValueError(f'{name}: loop must link to its GitHub fix issue')
        if date.fromisoformat(entry['expires']) <= datetime.now(timezone.utc).date():
            raise ValueError(f"{name}: expired {entry['expires']}; fix and remove or renew with loop evidence")
        entries[name] = entry
    return entries


def source_identity(repo):
    from git_env import scrubbed_env
    env = scrubbed_env()
    def git(*arguments):
        return subprocess.check_output(['git', '-C', str(repo), *arguments], env=env, stderr=subprocess.DEVNULL)
    sha = git('rev-parse', 'HEAD').decode().strip()
    tree = git('rev-parse', 'HEAD^{tree}').decode().strip()
    digest = hashlib.sha256(git('diff', '--binary', 'HEAD'))
    for raw in git('ls-files', '--others', '--exclude-standard', '-z').split(b'\0'):
        if raw:
            path = Path(os.fsdecode(raw))
            if path.parts[0] not in ('out', '_to_delete') and (Path(repo) / path).is_file():
                digest.update(raw)
                digest.update((Path(repo) / path).read_bytes())
    return {'sha': sha, 'tree': tree, 'fingerprint': digest.hexdigest()}


def ordinary_failure(code):
    return type(code) is int and 0 < code < 124 and code != 78


def run(args):
    entries = load_manifest(args.manifest)
    entry = entries.get(args.test)
    log = Path(args.log)
    log.parent.mkdir(parents=True, exist_ok=True)
    identity = json.loads(Path(args.identity_file).read_text()) if args.identity_file else source_identity(args.repo)
    with log.open('w') as output:
        first = subprocess.run(args.command, stdout=output, stderr=subprocess.STDOUT).returncode
    status = 'passed' if first == 0 else 'failed'
    effective = first
    rerun = None
    if ordinary_failure(first) and source_identity(args.repo) == identity:
        with Path(str(log) + '.rerun.log').open('w') as output:
            rerun = subprocess.run(args.command, stdout=output, stderr=subprocess.STDOUT).returncode
        if not ordinary_failure(rerun) and rerun != 0:
            # A configuration error on retry cannot turn the first failure into a skip.
            effective = first if rerun == 78 else rerun
        if rerun == 0 and source_identity(args.repo) == identity:
            status = 'flake-candidate'
    candidate = status == 'flake-candidate'
    if entry and ordinary_failure(first) and (rerun == 0 or ordinary_failure(rerun)):
        if source_identity(args.repo) != identity:
            raise ValueError("source changed during quarantine attempts")
        status = 'quarantined-failure'
        effective = 0
        print(f"QUARANTINED {args.test} · owner {entry['owner']} · loop {entry['loop']} · expires {entry['expires']}")
    receipt = {'version': 1, 'test': args.test, 'first_exit': first, 'rerun_exit': rerun, 'status': status, 'candidate': candidate, **identity}
    Path(str(log) + '.result.json').write_text(json.dumps(receipt) + '\n')
    print(PREFIX + json.dumps(receipt, separators=(',', ':')))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary and (entry or candidate):
        with open(summary, 'a') as output:
            output.write(f"- {args.test}: {status}; first exit {first}; rerun {rerun}." +
                (f" Loop {entry['loop']}; owner {entry['owner']}; expires {entry['expires']}." if entry else " Fix-loop proposal pending.") + '\n')
    if first and (args.print_log or status == 'quarantined-failure'):
        for attempt_log in (log, Path(str(log) + '.rerun.log')):
            if not attempt_log.exists():
                continue
            redacted = subprocess.run([sys.executable, str(REPO / 'ops/ci-secret-scan.py'), '--redact'],
                input=attempt_log.read_text(errors='replace'), capture_output=True, text=True)
            if redacted.returncode == 0:
                print('\n'.join(redacted.stdout.splitlines()[-80:]))
            else:
                print('test output WITHHELD: redaction failed', file=sys.stderr)
    return effective


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    runner = commands.add_parser('run')
    runner.add_argument('--print-log', action='store_true')
    runner.add_argument('--identity-file')
    runner.add_argument('--repo', default=str(REPO))
    runner.add_argument('--test', required=True)
    runner.add_argument('--manifest', default=str(MANIFEST))
    runner.add_argument('--log', required=True)
    runner.add_argument('command', nargs=argparse.REMAINDER)
    validator = commands.add_parser('validate')
    validator.add_argument('--manifest', default=str(MANIFEST))
    snapshot = commands.add_parser('snapshot')
    snapshot.add_argument('--repo', default=str(REPO))
    report = commands.add_parser('report')
    report.add_argument('log')
    args = parser.parse_args()
    if args.action == 'report':
        for line in Path(args.log).read_text(errors='replace').splitlines():
            if line.startswith(PREFIX) or line.startswith('QUARANTINED '):
                print(line)
        return 0
    if args.action == 'snapshot':
        print(json.dumps(source_identity(args.repo)))
        return 0
    if args.action == 'validate':
        try:
            entries = load_manifest(args.manifest)
            for name in entries:
                if not (REPO / name).is_file():
                    raise ValueError(f'{name}: test no longer exists; remove the quarantine entry')
            legacy = json.loads((REPO / 'ops/config/ci-check-scope.json').read_text())
            if legacy.get('quarantined'):
                raise ValueError('legacy skip-quarantine entries must move to ci-quarantine.json with loop, owner and expiry')
            return 0
        except (ValueError, OSError, KeyError) as exc:
            print(f'quarantine invalid: {exc}', file=sys.stderr)
            return 2
    if args.command[:1] == ['--']:
        args.command = args.command[1:]
    try:
        return run(args)
    except (ValueError, OSError, KeyError) as exc:
        print(f'quarantine invalid: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
