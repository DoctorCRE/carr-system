#!/usr/bin/env python3
"""Run canonical CI classes and retain their verified verdicts and evidence."""
from __future__ import annotations

from dataclasses import dataclass
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
CI_SELFTEST_TIMEOUT_SECONDS = 120
CLASS_DESCRIPTIONS = {
    'pushfloor': 'seeded defect that must not leave the machine (local pre-push floor)',
    'unit': 'seeded failing unit test',
    'types': 'seeded shape mistake in a data hand-off',
    'gates': 'seeded enforcement-layer regression',
    'secret': 'seeded credential in the tree',
    'dependency': 'seeded unpinned or vulnerable dependency',
    'migration': 'seeded bad migration / trigger permission',
    'binding': 'seeded binding or config drift',
    'artifact': 'seeded artifact mismatch (verb loss)',
    'hxprobe': 'vendored HxStore reader tests and release build with locked dependencies',
}
USAGE = ('Usage: ops/ci.sh [--strict] [--only classes] '
         '[--migration-part all|core|proofs|gates|acceptance|fixtures|reconstruction|equivalence|shadow] '
         '[--unit-part all|fast|mcp|practice] [--gate-shard 1/2|2/2] [--list] [--gate-programs]')


@dataclass(frozen=True)
class Options:
    strict: bool = False
    only: tuple[str, ...] | None = None
    migration_part: str = 'all'
    unit_part: str = 'all'
    gate_shard: int = 1
    gate_shards: int = 1
    listing: bool = False
    gate_programs: bool = False


def parse_options(argv):
    values = {}
    only = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        value = argv[i + 1] if i + 1 < len(argv) else ''
        if arg in ('-h', '--help'):
            print(USAGE)
            return None
        if arg == '--strict':
            values['strict'] = True
        elif arg == '--only':
            if only is None:
                only = []
            only.extend(value.replace(',', ' ').split())
            i += 1
        elif arg in ('--unit-part', '--migration-part'):
            values[arg[2:].replace('-', '_')] = value
            i += 1
        elif arg == '--gate-shard':
            if value not in ('1/2', '2/2'):
                raise ValueError('gate shard must be 1/2 or 2/2')
            values.update(gate_shard=int(value[0]), gate_shards=2)
            i += 1
        elif arg in ('--list', '--gate-programs'):
            values['listing' if arg == '--list' else 'gate_programs'] = True
        else:
            raise ValueError(f'unknown argument: {arg}')
        i += 1
    options = Options(only=tuple(only) if only is not None else None, **values)
    if options.unit_part not in ('all', 'fast', 'mcp', 'practice'):
        raise ValueError('unknown unit part')
    if options.migration_part not in ('all', 'core', 'proofs', 'gates', 'acceptance',
                                      'fixtures', 'reconstruction', 'equivalence', 'shadow'):
        raise ValueError('unknown migration part')
    return options


def execute(command, **kwargs):
    try:
        result = subprocess.run([str(arg) for arg in command], **kwargs)
        return result.returncode if result.returncode >= 0 else 128 - result.returncode
    except OSError as error:
        target = kwargs.get('stderr')
        message = f'{command[0]}: {error}\n'.encode()
        (target if hasattr(target, 'write') else sys.stderr.buffer).write(message)
        return 127 if isinstance(error, FileNotFoundError) else 126


def capture(command, *, quiet=False):
    return subprocess.run([str(arg) for arg in command], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL if quiet else None,
                          text=True, errors='replace').stdout.rstrip('\n')


class Controller:
    def __init__(self, options, python):
        self.options = options
        self.python = python
        self.program_records = os.environ.pop('CARR_CI_PROGRAM_RECORDS', '')
        self.logdir = Path(tempfile.mkdtemp(prefix='carr-ci.', dir=os.environ.get('RUNNER_TEMP') or '/tmp'))
        self.failed = []
        self.skipped = []
        self.timings = []
        self.class_name = ''

    def verdict(self, kind, message):
        color, label = {'ok': ('32', 'OK'), 'bad': ('31', 'FAIL'), 'skip': ('33', 'SKIP')}[kind]
        print(f'  \033[{color}m{label}\033[0m{" " * (6 - len(label))}{self.class_name:<11} {message}')
        if kind == 'bad':
            self.failed.append(self.class_name)
        elif kind == 'skip':
            self.skipped.append(self.class_name)

    def run_quiet(self, name, command, **kwargs):
        log = self.logdir / f'{name}.log'
        start = time.time()
        with log.open('wb') as stream:
            rc = execute(command, stdout=stream, stderr=stream, **kwargs)
        Path(str(log) + '.rc').write_text(f'{rc}\n')
        with (self.logdir / 'programs.tsv').open('a') as stream:
            stream.write(f'{self.class_name}\t{name}\t{rc}\t{start}\t{time.time()}\n')
        return rc

    def show(self, name, *, tail=None, error=False):
        data = (self.logdir / f'{name}.log').read_bytes()
        if tail is not None:
            data = b''.join(io.BytesIO(data).readlines()[-tail:])
        target = sys.stderr if error else sys.stdout
        target.flush()
        target.buffer.write(data)
        target.flush()

    def fail_tail(self, name):
        log = self.logdir / f'{name}.log'
        data = log.read_bytes()
        window = data if data.count(b'\n') < 200 else b''.join(io.BytesIO(data).readlines()[-80:])
        try:
            result = subprocess.run([self.python, 'ops/ci-secret-scan.py', '--redact'],
                                    input=window, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            if result.returncode == 0:
                sys.stderr.buffer.write(result.stdout)
                sys.stderr.flush()
                return
        except OSError:
            pass
        print('        gate output WITHHELD: ops/ci-secret-scan.py --redact could not run, '
              f'and unredacted child output is never printed. The captured log is at {log}.', file=sys.stderr)

    def finish(self, rc):
        if self.program_records and execute(
                [self.python, ROOT / 'ops/ci-evidence.py', 'programs', '--rows', self.logdir / 'programs.tsv',
                 '--output', self.program_records], stdout=subprocess.DEVNULL):
            print(f'ci-program-records: NOT WRITTEN to {self.program_records}', file=sys.stderr)
        if rc == 0:
            shutil.rmtree(self.logdir)
            return
        for status in self.logdir.glob('*.rc'):
            if status.read_text().strip() == '0':
                log = Path(str(status)[:-3])
                if log.suffix != '.log':
                    log = Path(str(log) + '.log')
                log.unlink(missing_ok=True)
                status.unlink()
        if os.environ.get('GITHUB_ACTIONS') == 'true':
            for log in self.logdir.glob('*.log'):
                redacted = Path(str(log) + '.redacted')
                with log.open('rb') as source, redacted.open('wb') as target:
                    status_code = execute([self.python, 'ops/ci-secret-scan.py', '--redact'], stdin=source, stdout=target)
                if status_code:
                    redacted.unlink(missing_ok=True)
        print(f'ci-retained-logs: {self.logdir}', file=sys.stderr)

    def check_hxprobe(self):
        rc = self.run_quiet('hxprobe', ['bash', '-c',
            'cd "$1/vendor/hxprobe" && cargo test --locked && cargo build --release --locked', '_', ROOT])
        if rc == 0:
            self.show('hxprobe')
            self.verdict('ok', 'locked Rust tests and release build passed')
        else:
            self.fail_tail('hxprobe')
            self.verdict('bad', 'locked Rust tests or release build failed')

    def check_unit(self):
        if not self.options.strict and os.environ.get('GITHUB_ACTIONS') != 'true' and not os.environ.get('CARR_CI_DATABASE_URL'):
            self.verdict('skip', 'SKIP-for-local: CARR_CI_DATABASE_URL is unset; unit runs in hosted CI')
            return
        if self.run_quiet('unit', [self.python, 'ops/ci-unit.py', '--part', self.options.unit_part]) == 0:
            self.show('unit')
            self.verdict('ok', 'required Node package checks executed')
        else:
            self.fail_tail('unit')
            self.verdict('bad', 'required Node package checks failed or did not execute')

    def check_types(self):
        if self.run_quiet('worker-types', ['npm', '--prefix', 'mcp-server', 'run', 'typecheck']):
            self.show('worker-types', tail=25, error=True)
            self.verdict('bad', 'Worker verb contract types failed')
            return
        if not os.access('bin/type-check.sh', os.X_OK):
            self.verdict('skip', 'bin/type-check.sh not present')
            return
        rc = self.run_quiet('types', ['./bin/type-check.sh'])
        if rc == 0:
            self.verdict('ok', 'Worker verb contracts and Python types pass')
        elif rc == 78:
            version = 'unknown'
            for python in (str(ROOT / '.venv/bin/python'), 'python3'):
                try:
                    result = subprocess.run([python, '-c', 'import sys;print("%d.%d"%sys.version_info[:2])'],
                                            capture_output=True, text=True)
                    if result.returncode == 0:
                        version = result.stdout.strip()
                        break
                except OSError:
                    continue
            if version == 'unknown' or (version.startswith('3.') and len(version) == 3):
                message = f'mypy excluded by the requirements pin on Python {version} (pin needs >= 3.10) — accepted machine state'
            else:
                message = f'mypy not installed on Python {version} (pinned in requirements.lock, so CI always has it)'
            self.verdict('skip', message)
        else:
            self.show('types', tail=25, error=True)
            self.verdict('bad', 'mypy found shape mistakes — see the errors above')

    def check_gates(self):
        if self.run_quiet('registry-check', ['node', 'ops/scac-mutation-inventory.mjs']):
            self.fail_tail('registry-check')
            self.verdict('bad', 'committed registry differs from source; run ./run.sh regen registry')
            return
        os.environ['CARR_HOOK_FIXTURE'] = '1'
        result = self.logdir / 'gates-result'
        rc = execute([self.python, 'ops/ci-gates.py', '--logdir', self.logdir, '--result', result,
                      '--python', self.python, '--timeout-helper', ROOT / 'bin/with-timeout.py',
                      '--timeout', CI_SELFTEST_TIMEOUT_SECONDS, '--shard', self.options.gate_shard,
                      '--shards', self.options.gate_shards])
        summary = result.read_text().rstrip('\n') if result.is_file() else ''
        self.verdict('ok' if rc == 0 and summary else 'bad', summary or f'gate runner exited {rc} without a verdict')

    def check_secret(self):
        self.runner_verdict('ops/ci-secret.py', ['--python', self.python], 'secret runner')

    def check_pushfloor(self):
        deferred = ['--gates-deferred'] if self.options.only is not None and 'gates' not in self.options.only else []
        self.runner_verdict('ops/ci-pushfloor.py', deferred, 'push floor runner')

    def runner_verdict(self, runner, arguments, label):
        result = subprocess.run([self.python, runner, '--logdir', str(self.logdir), *arguments],
                                stdout=subprocess.PIPE, text=True, errors='replace')
        message = result.stdout.rstrip('\n')
        rc = result.returncode if result.returncode >= 0 else 128 - result.returncode
        self.verdict('ok' if rc == 0 and message else 'bad', message or f'{label} exited {rc} without a verdict')

    def check_dependency(self):
        flags = ['--strict'] if self.options.strict else []
        if self.run_quiet('dep', [self.python, 'ops/ci-dep-check.py', *flags]) == 0:
            lines = (self.logdir / 'dep.log').read_text(errors='replace').splitlines()
            self.verdict('ok', lines[-1] if lines else '')
        else:
            self.show('dep', error=True)
            self.verdict('bad', 'see above')

    def check_migration(self):
        result = self.logdir / 'migration-result'
        result.touch()
        flags = ['--strict'] if self.options.strict else []
        rc = execute([self.python, 'ops/ci-migration.py', '--logdir', self.logdir, '--result', result,
                      '--python', self.python, '--part', self.options.migration_part,
                      '--prior-failed', len(self.failed), *flags])
        reported = reported_bad = False
        for line in result.read_text().splitlines():
            kind, _, message = line.partition('\t')
            if kind in ('ok', 'bad', 'skip'):
                self.verdict(kind, message)
                reported = True
                reported_bad |= kind == 'bad'
            elif kind == 'timing':
                print(message)
        if not reported or (rc != 0 and not reported_bad):
            self.verdict('bad', f'migration runner exited {rc} without a verdict')

    def check_binding(self):
        problems = []
        if Path('mcp-server/wrangler.toml').is_file():
            script = r'''
const fs=require("fs"), t=fs.readFileSync("mcp-server/wrangler.toml","utf8");
const need=["name","main","compatibility_date"];
const missing=need.filter(k=>!new RegExp("^\\s*"+k+"\\s*=","m").test(t));
if(missing.length){console.error("wrangler.toml missing: "+missing.join(", "));process.exit(1);}
if(/DATABASE_URL\s*=/.test(t)){console.error("wrangler.toml declares a DATABASE_URL inline; it belongs in a secret");process.exit(1);}
'''
            if self.run_quiet('binding-wrangler', ['node', '-e', script]):
                problems.append('wrangler.toml')
                self.show('binding-wrangler', error=True)
        wrangler = os.access('mcp-server/node_modules/.bin/wrangler', os.X_OK)
        if wrangler and self.run_quiet('binding-worker-boot', ['./bin/worker-boot-check.sh']):
            problems.append('worker-boot')
            self.show('binding-worker-boot', tail=40, error=True)
        if problems:
            self.verdict('bad', 'drift: ' + ' '.join(problems))
        elif not wrangler:
            self.verdict('skip', 'worker boot not checked; wrangler not installed (run npm ci in mcp-server/)')
        else:
            self.verdict('ok', 'worker config declared; worker boot passed')

    def check_artifact(self):
        if self.run_quiet('doctorcre-artifact', [self.python, 'tools/release-manifest.py',
                'doctorcre-artifact', 'verify', '--pin', 'ops/config/doctorcre-artifact.v1.json']):
            self.show('doctorcre-artifact', tail=20, error=True)
            self.verdict('bad', 'the exact DoctorCRE release artifact did not match its CARR pin')
            return
        with (self.logdir / 'artifact.log').open('wb') as error:
            shipping = subprocess.run(['sh', 'ops/verb-count.sh', str(ROOT / 'mcp-server'),
                                      '--check-retirements', '0'], stderr=error,
                                     stdout=subprocess.PIPE, text=True, errors='replace').stdout.rstrip('\n')
        if not shipping:
            self.show('artifact', error=True)
            self.verdict('bad', 'registry import or retirement validation failed')
            return
        base = capture(['git', 'merge-base', 'HEAD', 'origin/main'], quiet=True)
        if base == capture(['git', 'rev-parse', 'HEAD']):
            base = capture(['git', 'rev-parse', '-q', '--verify', 'HEAD^'])
        if not base:
            self.verdict('skip', f'{shipping} verbs counted, but no base commit; the shrink comparison did not run')
            return
        with (self.logdir / 'artifact-count.log').open('wb') as output, (self.logdir / 'artifact.log').open('wb') as error:
            rc = execute(['sh', 'ops/verb-loss-vs-base.sh', ROOT, base], stdout=output, stderr=error)
        if rc == 0:
            self.verdict('ok', f'{shipping} verbs; loss checked by name against base {base[:12]} and committed retirements')
        else:
            self.show('artifact', error=True)
            self.verdict('bad', f'verb loss against base {base[:12]} does not match the committed retirement ledger')

    def run(self):
        print(f'carr-system CI — {capture(["git", "rev-parse", "--short", "HEAD"])} on '
              f'{capture(["git", "rev-parse", "--abbrev-ref", "HEAD"])}')
        if self.options.strict:
            print('  strict: a SKIP counts as a failure')
        print()
        for name in self.options.only or ():
            if name not in CLASS_DESCRIPTIONS:
                print(f'ci.sh: no such class: {name} (try --list)', file=sys.stderr)
                return 64
        for name in CLASS_DESCRIPTIONS:
            if self.options.only is not None and name not in self.options.only:
                continue
            self.class_name = name
            start = int(time.time())
            getattr(self, f'check_{name}')()
            self.timings.append(f'{name}={int(time.time()) - start}s')
        print('\nci-timing:' + ((' ' + ' '.join(self.timings)) if self.timings else ''))
        if self.failed:
            print('ci-failed-classes: ' + ' '.join(self.failed))
        if self.skipped:
            print('ci-skipped-classes: ' + ' '.join(self.skipped))
        if self.failed:
            floor = ' '.join(name for name in self.failed if name in ('pushfloor', 'secret')) or 'none'
            relocated = ' '.join(name for name in self.failed if name not in ('pushfloor', 'secret')) or 'none'
            print(f'ci-floor-verdict: floor: {floor} relocated: {relocated}')
        print()
        if self.failed:
            print(f'CI FAILED — {len(self.failed)} class(es) failed, {len(self.skipped)} skipped')
            return 1
        if self.skipped and self.options.strict:
            print(f'CI FAILED — {len(self.skipped)} class(es) SKIPPED under --strict.')
            print('In CI every check is available, so a skip means a check stopped running.')
            return 1
        if self.skipped:
            print(f'CI passed — {len(self.skipped)} class(es) skipped locally (they run in CI under --strict)')
        else:
            print('CI passed — every class green')
        return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    os.chdir(ROOT)
    python = os.environ.get('CARR_CI_PYTHON', sys.executable)
    os.environ['CARR_CI_PYTHON'] = python
    try:
        options = parse_options(argv)
    except ValueError as error:
        print(f'ci.sh: {error}', file=sys.stderr)
        return 64
    if options is None:
        return 0
    if options.listing:
        print('ci.sh check classes — each is the gate for one doctrine seeded-failure class:')
        for name, description in CLASS_DESCRIPTIONS.items():
            print(f'  {name:<11} {description}')
        return 0
    controller = Controller(options, python)
    rc = 1
    try:
        if options.gate_programs:
            execute([python, 'ops/ci-gates.py', '--gate-programs'])
            rc = 0
        else:
            rc = controller.run()
        return rc
    finally:
        controller.finish(rc)


if __name__ == '__main__':
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    raise SystemExit(main())
