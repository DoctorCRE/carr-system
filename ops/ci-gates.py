#!/usr/bin/env python3
# Run the gates class with ordered discovery, shard ownership, and isolated fixtures.
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


PROGRAM_PATTERNS = (
    'ops/*-selftest.py', 'tools/test-*.py', 'tools/test_*.py', 'tools/*-selftest.py',
    'tools/room-bridge/test_*_unit.py', 'tools/room-bridge/test_activation_reliability.py',
    'tools/dictation-rig/tests/test_*.py',
    'test/doc_drift/test_*.py', 'test/doc_drift_product/test_*.py',
    'tools/test-*.sh', 'tools/test_*.sh',
)
INVARIANTS = (
    'rule-load-layer-check', 'rule-classification-parity-check',
    'selftest-git-isolation-check', 'sync-core-rule-ids', 'rule-route-coverage',
    'migration-order-gate', 'migration-safety-gate',
)
CLOCK_ALLOW = (
    'lead-automation-concurrency.postgres.mjs', 'invoice-review-regressions.postgres.mjs',
    'salesforce-read-run-store-rw02-postgres.test.mjs',
    'program-controller-census-role-boundary.v5.test.mjs',
    'recovery-matrix.v5.test.mjs', 'shared-oauth-worker.test.mjs',
)


def gate_programs():
    return [str(path) for pattern in PROGRAM_PATTERNS for path in sorted(Path('.').glob(pattern))
            if path.is_file()]


def git_output(*args):
    return subprocess.check_output(['git', *args], stderr=subprocess.DEVNULL).decode().rstrip('\n')


def tree_fingerprint():
    return tuple(git_output(*args) for args in (
        ('rev-parse', 'HEAD'), ('status', '--porcelain', '--untracked-files=no'),
        ('diff', '--cached', '--name-only')))


def check_clocks():
    base = git_output('merge-base', 'origin/main', 'HEAD')
    changed = git_output('diff', '--name-only', '--diff-filter=ACMR', base, '--', 'mcp-server/test')
    untracked = git_output('ls-files', '--others', '--exclude-standard', '--', 'mcp-server/test')
    paths = sorted({p for p in (changed + '\n' + untracked).splitlines()
                    if Path(p).suffix in {'.js', '.mjs', '.cjs'} and Path(p).name not in CLOCK_ALLOW})
    if not paths:
        return True
    return subprocess.run(['grep', '-nE', r'Date\s*\.\s*now\s*\(\s*\)|new\s+Date\s*\(\s*\)',
                           '--', *paths]).returncode == 1


class Gates:
    def __init__(self, args):
        self.args = args
        self.logdir = Path(args.logdir)
        self.failures = []
        self.skipped = []
        self.count = 0
        self.index = 0
        self.timed_out = False
        scope = Path('ops/config/ci-check-scope.json')
        self.scope = json.loads(scope.read_text()) if scope.is_file() else {}
        self.portable = (os.environ.get('CARR_CI_PORTABLE_ONLY') == '1'
                         or os.environ.get('GITHUB_ACTIONS', '').lower() == 'true')

    def excluded_reason(self, name):
        for entry in self.scope.get('run_by', []):
            if entry['check'] == name and Path('ops', entry['owner']).is_file():
                return 'RUN BY ' + entry['owner'] + ': ' + entry['reason']
        if self.portable:
            for entry in self.scope.get('local_only', []):
                if entry['check'] == name:
                    return 'LOCAL-ONLY: ' + entry['reason']
        return ''

    def select(self, programs, suffix):
        for path in programs:
            if not path.endswith(suffix):
                continue
            name = Path(path).name
            why = self.excluded_reason(name)
            if suffix == '.py' and why.startswith('RUN BY '):
                print(f'        owned    {name} — {why}', file=sys.stderr)
                continue
            if why:
                self.skipped.append(name)
                print(f'        \033[33mnot run\033[0m  {name} — {why}', file=sys.stderr)
                continue
            self.index += 1
            if (self.index - 1) % self.args.shards + 1 == self.args.shard:
                self.count += 1
                yield path

    def run(self, name, command, isolated=False):
        log = self.logdir / (name + '.log')
        start = time.time()
        env = os.environ.copy()
        temporary = self.logdir / ('tmp-' + name.removeprefix('gate-'))
        if isolated:
            temporary.mkdir(parents=True, exist_ok=True)
            env.update(TMPDIR=str(temporary), TMP=str(temporary), TEMP=str(temporary))
        with log.open('wb') as out:
            try:
                rc = subprocess.run(command, stdout=out, stderr=subprocess.STDOUT, env=env).returncode
            except OSError as error:
                out.write(f'{command[0]}: {error.strerror}\n'.encode())
                rc = 127
            if rc < 0:
                rc = 128 - rc
            if isolated:
                leaked = sorted(temporary.iterdir()) if temporary.is_dir() else []
                if rc == 0 and leaked:
                    out.write(('leaked temporary fixtures: ' + ' '.join(p.name for p in leaked[:5])
                               + ' \n').encode())
                    rc = 75
                shutil.rmtree(temporary, ignore_errors=True)
        status = self.logdir / (name + ('.rc' if isolated else '.log.rc'))
        status.write_text(f'{rc}\n')
        return name, rc, start, time.time()

    def record(self, result):
        name, rc, start, end = result
        with (self.logdir / 'programs.tsv').open('a') as out:
            out.write(f'gates\t{name}\t{rc}\t{start}\t{end}\n')
        return rc

    def tail(self, name, lines=None):
        log = self.logdir / (name + '.log')
        data = log.read_bytes()
        if lines is not None:
            sys.stderr.buffer.write(b''.join(data.splitlines(keepends=True)[-lines:]))
            return
        window = data if len(data.splitlines()) < 200 else b''.join(data.splitlines(keepends=True)[-80:])
        redacted = subprocess.run([self.args.python, 'ops/ci-secret-scan.py', '--redact'],
                                  input=window, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if redacted.returncode == 0:
            sys.stderr.buffer.write(redacted.stdout)
        else:
            print('        gate output WITHHELD: ops/ci-secret-scan.py --redact could not run, '
                  f'and unredacted child output is never printed. The captured log is at {log}.',
                  file=sys.stderr)

    def execute(self, name, *command):
        return self.record(self.run(name, command))

    def check(self):
        os.environ['CARR_HOOK_FIXTURE'] = '1'
        try:
            clocks_ok = check_clocks()
        except (OSError, subprocess.CalledProcessError):
            clocks_ok = False
        if not clocks_ok:
            self.failures.append('test-clocks')
        if self.execute('codemap', self.args.python, 'tools/codemap.py', '--check'):
            self.failures.append('codemap')
            self.tail('codemap')
        if Path('vendor/hxprobe/Cargo.toml').is_file():
            if self.execute('hxprobe-prepare', 'bash', '-c', 'cd vendor/hxprobe && cargo build --locked'):
                self.tail('hxprobe-prepare')
                return 1, 'HxStore worker preparation failed'
        before = tree_fingerprint()
        eligible = list(self.select(gate_programs(), '.py'))
        timeout = self.args.timeout * self.args.jobs
        os.environ.update(CI_TIMEOUT_HELPER=self.args.timeout_helper,
                          CI_SELFTEST_TIMEOUT_SECONDS=str(self.args.timeout),
                          LOGDIR=str(self.logdir), PY=self.args.python, pooled_timeout=str(timeout))
        with ThreadPoolExecutor(max_workers=self.args.jobs) as pool:
            results = list(pool.map(lambda path: self.run('gate-' + Path(path).name,
                [self.args.python, self.args.timeout_helper, str(timeout), self.args.python, path],
                isolated=True), eligible))
        for result in results:
            rc = self.record(result)
            name = result[0]
            base = name.removeprefix('gate-')
            if rc == 0:
                sys.stdout.buffer.write((self.logdir / (name + '.log')).read_bytes())
            elif rc == 124:
                self.failures.append('TIMEOUT:' + base)
                self.timed_out = True
                print(f'        \033[31mTIMEOUT\033[0m  {base} — exceeded {timeout}s (pool-scaled); '
                      'reporting all completed Python gate results', file=sys.stderr)
                self.tail(name)
            elif rc == 78:
                self.skipped.append(base)
                last = (self.logdir / (name + '.log')).read_bytes().split(b'\n')
                last = last[-2] if last[-1:] == [b''] and len(last) > 1 else last[-1]
                sys.stderr.buffer.write(
                    f'        \033[33mnot run\033[0m  {base} — NOT CONFIGURED (exit 78): '.encode()
                    + last + b'\n')
            else:
                self.failures.append(base)
                self.tail(name)
        if not self.timed_out:
            for path in self.select(gate_programs(), '.sh'):
                base = Path(path).name
                name = 'gate-' + base
                rc = self.execute(name, self.args.python, self.args.timeout_helper,
                                  str(self.args.timeout), path)
                if rc == 124:
                    self.failures.append('TIMEOUT:' + base)
                    print(f'        \033[31mTIMEOUT\033[0m  {base} — exceeded {self.args.timeout}s; '
                          'aborting remaining gate selftests', file=sys.stderr)
                    self.tail(name, 12)
                    break
                if rc:
                    self.failures.append(base)
                    self.tail(name)
        if self.args.shard == 1:
            if self.execute('safety-hooks', self.args.python, 'hooks/gate-integrity.py', '--strict'):
                self.failures.append('gate-integrity')
                self.tail('safety-hooks', 12)
            for inv in INVARIANTS:
                if not Path('ops', inv + '.py').is_file():
                    continue
                args = ['--check'] if inv == 'sync-core-rule-ids' else []
                if self.execute('gate-' + inv, self.args.python, f'ops/{inv}.py', *args):
                    self.failures.append(inv)
                    self.tail('gate-' + inv, 12)
        if tree_fingerprint() != before:
            self.failures.append('tree-mutated-by-selftests')
            print('  A selftest in this class CHANGED THE WORKING TREE it was invoked in.\n'
                  '  HEAD, tracked-file state, or the index differs from before the suite ran.\n'
                  '  This is the 2026-08-14 failure mode: git exports GIT_DIR into hooks and\n'
                  '  it OVERRIDES cwd, so a fixture built with cwd=<tempdir> and no scrub\n'
                  '  writes into whatever repository GIT_DIR names.\n'
                  '  Find the culprit with: ops/selftest-git-isolation-check.py\n'
                  '  Recover with a mixed reset; do not commit anything from this tree until\n'
                  '  you have read what changed:', file=sys.stderr)
            print('\n'.join(git_output('status', '--porcelain', '--untracked-files=no').splitlines()[:20]),
                  file=sys.stderr)
        if self.failures:
            return 1, 'failed: ' + ' '.join(self.failures)
        if self.skipped:
            return 0, f'{self.count} suites + safety hook installation · NOT RUN: ' + ' '.join(self.skipped)
        return 0, f'{self.count} selftest suites + safety hook installation'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--gate-programs', action='store_true')
    parser.add_argument('--logdir')
    parser.add_argument('--result')
    parser.add_argument('--python', default=os.environ.get('CARR_CI_PYTHON', sys.executable))
    parser.add_argument('--timeout-helper', default='bin/with-timeout.py')
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--jobs', type=int, default=int(os.environ.get('CARR_CI_GATE_JOBS') or '4'))
    parser.add_argument('--shard', type=int, default=1)
    parser.add_argument('--shards', type=int, default=1)
    args = parser.parse_args()
    if args.gate_programs:
        print('\n'.join(gate_programs()))
        return 0
    rc, summary = Gates(args).check()
    Path(args.result).write_text(summary + '\n')
    return rc


if __name__ == '__main__':
    sys.exit(main())
