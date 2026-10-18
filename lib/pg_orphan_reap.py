"""Reap only old orphaned postmasters from named temporary fixture directories."""
import argparse
from dataclasses import dataclass
import fnmatch
from pathlib import Path
import shlex
import subprocess
import tempfile

PATTERNS = (
    'carr-local-pg-ci.*', 'release-abandon-*', 'successor-postgres-*',
    'one-step-rule-pg.*', 'pr*-assurance.*', 'shadowx.*', 'cmu-pg.*',
    'pr1493-pg', 'ci-db-gate-*', 'ci-gate-selftest-*', 'carr-migration-shadow.*',
    'carr-catchup-writer-*', 'carr-activation-repin-*', 'carr-jev-aging-*',
    'dot-reader-test-*', 'dot-independent-restore-*', 'carr-f08-e2e-*', 'carr-f08-0597-*',
    'local-deals-*', 'doc-catchup-*', 'lease-radar-*', 'doc-activity-*', 'wr182-pg-*', 'node-pg-life-*',
)


@dataclass(frozen=True)
class Orphan:
    pid: int
    age: int
    data: Path
    pg_ctl: Path


def temporary_data(path):
    path = Path(path)
    roots = {Path('/tmp').resolve(), Path(tempfile.gettempdir()).resolve()}
    normalized = path.resolve()
    for root in roots:
        try:
            parts = normalized.relative_to(root).parts
        except ValueError:
            continue
        if len(parts) not in (1, 2) or (len(parts) == 2 and parts[1] not in ('data', 'integration-data')):
            continue
        if any(fnmatch.fnmatchcase(parts[0], pattern) for pattern in PATTERNS):
            return True
    return False


def select_orphans(output, threshold=7200):
    selected = []
    for line in output.splitlines():
        fields = line.split(None, 3)
        if len(fields) != 4:
            continue
        try:
            pid, parent = map(int, fields[:2])
            age = elapsed_seconds(fields[2])
            args = shlex.split(fields[3])
            if parent != 1 or age <= threshold or not args or Path(args[0]).name != 'postgres':
                continue
            data = Path(args[args.index('-D') + 1])
        except (ValueError, IndexError):
            continue
        if data.is_absolute() and temporary_data(data):
            selected.append(Orphan(pid, age, data, Path(args[0]).with_name('pg_ctl')))
    return selected


def process_output():
    return subprocess.check_output(['ps', '-axo', 'pid=,ppid=,etime=,command='], text=True)


def elapsed_seconds(value):
    if value.isdigit():
        return int(value)
    days, clock = value.split('-', 1) if '-' in value else ('0', value)
    parts = [int(part) for part in clock.split(':')]
    if len(parts) not in (2, 3) or any(part < 0 for part in parts):
        raise ValueError('invalid process elapsed time')
    return int(days) * 86400 + sum(part * 60 ** i for i, part in enumerate(reversed(parts)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    failed = False
    for orphan in select_orphans(process_output()):
        print(f'{"would stop" if args.dry_run else "stop"} pid={orphan.pid} age={orphan.age}s data={orphan.data}')
        if args.dry_run:
            continue
        if not any(p.pid == orphan.pid and p.data == orphan.data and p.pg_ctl == orphan.pg_ctl
                   for p in select_orphans(process_output())):
            continue
        try:
            if int((orphan.data / 'postmaster.pid').read_text().splitlines()[0]) != orphan.pid:
                raise RuntimeError('postmaster PID changed')
            subprocess.run([str(orphan.pg_ctl), '-D', str(orphan.data), '-m', 'fast', '-w', 'stop'],
                           check=True, capture_output=True, timeout=60)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            print(f'reap failed pid={orphan.pid}: {exc}')
            failed = True
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
