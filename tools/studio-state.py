#!/usr/bin/env python3
"""Package allowlisted Studio state and recover it into a new staging tree."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

SCHEMA = 'carr-studio-state/v1'
FILES = (
    'repo/out/outlook-intake/state.json',
    'repo/out/outlook-intake/needs-refresh.json',
    'repo/out/outlook-send/ledger.jsonl',
    'home/.local/state/carr/outlook-send/ledger.jsonl',
    'home/.config/carr/outlook-nightly-sweep.folders.md',
    'repo/exporters/dossier-roster.local.json',
    'repo/tools/dictation-rig/vocab-local.txt',
    'repo/ops/config/client-names.local.txt',
)
DIRECTORIES = {
    'repo/out/calendar-archive': re.compile(r'calendar-(joe|dell)-[0-9]{8}\.ics'),
    'repo/out/boards': re.compile(r'[a-zA-Z0-9_-]+(?:-answers)?\.jsonl?'),
}
EXCLUDED = ('exporters/templates', 'frozen-sources', 'Outlook profile',
            'vault and attachment bytes', 'credentials and account recovery factors')
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024


def allowed(name: str) -> bool:
    path = PurePosixPath(name)
    return (str(path) == name and not path.is_absolute() and '..' not in path.parts
            and (name in FILES or any(str(path.parent) == folder and pattern.fullmatch(path.name)
                                     for folder, pattern in DIRECTORIES.items())))


def safe_path(path: Path) -> None:
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError('symlink in state path')


def signature(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def inventory(repo: Path, home: Path) -> tuple[dict[str, Path], list[str]]:
    roots = {'repo': repo.absolute(), 'home': home.absolute()}
    found, missing = {}, []
    for name in (*FILES, *DIRECTORIES):
        root, relative = name.split('/', 1)
        path = roots[root] / relative
        safe_path(path)
        if not path.exists():
            missing.append(name)
        elif name in FILES:
            found[name] = path
        else:
            if not path.is_dir():
                raise ValueError('state directory is not a directory')
            for child in sorted(path.iterdir()):
                candidate = name + '/' + child.name
                if allowed(candidate):
                    safe_path(child)
                    found[candidate] = child
    return found, missing


def pack(repo: Path, home: Path) -> tuple[bytes, dict]:
    paths, missing = inventory(repo, home)
    contents, facts, observed = {}, {}, {}
    total = 0
    for name, path in paths.items():
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_FILE_BYTES:
                raise ValueError('state member must be a bounded regular file with no hard links')
            data = source.read(MAX_FILE_BYTES + 1)
            if signature(before) != signature(os.fstat(source.fileno())) or len(data) != before.st_size:
                raise ValueError('state changed during capture; keep writers fenced')
        total += len(data)
        if total > MAX_ARCHIVE_BYTES // 2:
            raise ValueError('state package exceeds memory limit')
        contents[name] = data
        observed[name] = signature(before)
        facts[name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    if not contents:
        raise ValueError('no allowlisted state exists')
    revision = subprocess.run(['git', '-C', str(Path(__file__).resolve().parents[1]), 'rev-parse', 'HEAD'],
                              capture_output=True, env={'PATH': '/usr/bin:/bin', 'LANG': 'C'}, timeout=10)
    source_revision = revision.stdout.decode().strip()
    if revision.returncode or not re.fullmatch(r'[0-9a-f]{40}', source_revision):
        raise ValueError('source revision cannot be identified')
    manifest = {'schema': SCHEMA, 'source_revision': source_revision,
                'created_at': datetime.now(timezone.utc).isoformat(),
                'files': facts, 'missing': missing, 'excluded': list(EXCLUDED)}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name, data in {'manifest.json': json.dumps(manifest, sort_keys=True).encode(), **contents}.items():
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(data), 0o600
            archive.addfile(member, io.BytesIO(data))
    after, after_missing = inventory(repo, home)
    if set(after) != set(paths) or missing != after_missing:
        raise ValueError('state inventory changed during capture')
    for name, path in after.items():
        if signature(path.stat()) != observed[name]:
            raise ValueError('state changed during capture; keep writers fenced')
    return buffer.getvalue(), manifest


def age_binary() -> str:
    for name in ('/opt/homebrew/bin/age', '/usr/local/bin/age', '/usr/bin/age'):
        if Path(name).is_file() and os.access(name, os.X_OK):
            return name
    raise ValueError('age is unavailable at a trusted executable path')


def crypt(arguments: list[str], data: bytes) -> bytes:
    child_env = {'PATH': '/usr/bin:/bin', 'LANG': 'C'}
    result = subprocess.run([age_binary(), *arguments], input=data, capture_output=True,
                            env=child_env, timeout=300)
    if result.returncode:
        raise ValueError('age operation failed; no output was published')
    if len(result.stdout) > MAX_ARCHIVE_BYTES:
        raise ValueError('age output exceeds state-package limit')
    return result.stdout


def publish(path: Path, data: bytes) -> None:
    safe_path(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.studio-state-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path)
    finally:
        os.unlink(temporary)


def unpack(data: bytes) -> tuple[dict[str, bytes], dict]:
    members = {}
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError('state package exceeds memory limit')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:') as archive:
        for member in archive:
            if (member.name in members or not member.isfile() or not 0 <= member.size <= MAX_FILE_BYTES
                    or member.name != 'manifest.json' and not allowed(member.name)):
                raise ValueError('invalid or unallowlisted state member')
            source = archive.extractfile(member)
            assert source is not None
            members[member.name] = source.read(MAX_FILE_BYTES + 1)
    manifest = json.loads(members.pop('manifest.json'))
    if (not isinstance(manifest, dict) or manifest.get('schema') != SCHEMA
            or not isinstance(manifest.get('files'), dict)
            or not isinstance(manifest.get('missing'), list)
            or not re.fullmatch(r'[0-9a-f]{40}', str(manifest.get('source_revision', '')))
            or set(members) != set(manifest['files'])):
        raise ValueError('state manifest does not match its members')
    missing = manifest['missing']
    if (not members or any(not isinstance(name, str) or name not in (*FILES, *DIRECTORIES) for name in missing)
            or len(set(missing)) != len(missing)
            or set(FILES) - set(members) != set(missing) & set(FILES)
            or any(name == absent or name.startswith(absent + '/') for name in members for absent in missing)):
        raise ValueError('state manifest does not account for allowlisted state')
    for name, content in members.items():
        if manifest['files'][name] != {'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}:
            raise ValueError('state member digest does not match its manifest')
    return members, manifest


def read_ciphertext(path: Path) -> bytes:
    safe_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARCHIVE_BYTES:
            raise ValueError('ciphertext must be a bounded regular file')
        data = source.read(MAX_ARCHIVE_BYTES + 1)
        if signature(info) != signature(os.fstat(source.fileno())):
            raise ValueError('ciphertext changed during read')
    if len(data) > MAX_ARCHIVE_BYTES or not data.startswith(b'age-encryption.org/v1\n'):
        raise ValueError('archive input must be bounded age ciphertext')
    return data


def restore(archive: Path, identity: Path, staging: Path) -> dict:
    safe_path(staging)
    if staging.exists():
        raise ValueError('staging destination already exists')
    contents, manifest = unpack(crypt(['--decrypt', '--identity', str(identity)], read_ciphertext(archive)))
    temporary = Path(tempfile.mkdtemp(prefix='.studio-state-', dir=staging.parent))
    try:
        for name, data in contents.items():
            destination = temporary / name
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with destination.open('xb') as output:
                os.chmod(destination, 0o600)
                output.write(data)
        with (temporary / 'manifest.json').open('x') as output:
            os.chmod(output.name, 0o600)
            json.dump(manifest, output, sort_keys=True)
        # mkdir reserves the caller's new destination without overwriting an existing tree.
        staging.mkdir(mode=0o700)
        try:
            for child in temporary.iterdir():
                child.rename(staging / child.name)
        except BaseException:
            shutil.rmtree(staging)
            raise
    finally:
        shutil.rmtree(temporary)
    return {'state': 'staged', 'files': len(contents), 'missing': manifest['missing'],
            'send_reconciliation_required': True, 'writers_enabled': False}


def archive_r2(path: Path, digest: str, quota_bytes: int | None) -> dict:
    if quota_bytes is None or quota_bytes <= 0:
        return {'state': 'owed', 'reason': 'quota_unconfirmed'}
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
    import r2_archive as r2
    if quota_bytes > r2.DEFAULT_QUOTA_GB * r2.GB:
        return {'state': 'owed', 'reason': 'quota_unconfirmed'}
    key = f'studio-state/{digest}/{path.name}'
    try:
        ledger = r2.load_ledger()
        r2.reconcile(ledger)
        cap, provenance = quota_bytes, 'operator-reviewed current configured cap'
        result = r2.upload(str(path), key, digest, path.stat().st_size, cap, provenance, ledger)
    except r2.QuotaExceeded:
        return {'state': 'owed', 'reason': 'quota_refused', 'key': key}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return {'state': 'owed', 'reason': 'upload_failed', 'key': key}
    try:
        binary = r2.wrangler_bin()
        if not binary:
            raise ValueError('archive readback unavailable')
        readback = subprocess.run([binary, 'r2', 'object', 'get', f'{r2.BUCKET}/{key}', '--remote', '--pipe'],
                                  capture_output=True, timeout=120, env=r2._wrangler_env())
        if readback.returncode or hashlib.sha256(readback.stdout).hexdigest() != digest:
            raise ValueError('archive readback digest does not match ciphertext')
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'state': 'owed', 'reason': 'readback_failed', 'key': key}
    return {'state': 'archived', 'key': key, 'uploaded': result['uploaded'], 'sha256': digest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    backup = commands.add_parser('backup')
    backup.add_argument('--repo', type=Path, required=True)
    backup.add_argument('--home', type=Path, required=True)
    backup.add_argument('--recipient', required=True)
    backup.add_argument('--output', type=Path, required=True)
    backup.add_argument('--writers-fenced', action='store_true', required=True)
    backup.add_argument('--archive-r2', action='store_true')
    backup.add_argument('--r2-quota-bytes', type=int)
    recovery = commands.add_parser('restore')
    recovery.add_argument('--archive', type=Path, required=True)
    recovery.add_argument('--identity', type=Path, required=True)
    recovery.add_argument('--staging', type=Path, required=True)
    retry = commands.add_parser('archive')
    retry.add_argument('--archive', type=Path, required=True)
    retry.add_argument('--r2-quota-bytes', type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == 'restore':
            receipt = restore(args.archive, args.identity, args.staging)
        elif args.command == 'archive':
            encrypted = read_ciphertext(args.archive)
            digest = hashlib.sha256(encrypted).hexdigest()
            receipt = {'state': 'encrypted', 'sha256': digest,
                       'archive': archive_r2(args.archive, digest, args.r2_quota_bytes)}
        else:
            data, manifest = pack(args.repo, args.home)
            encrypted = crypt(['--encrypt', '--recipient', args.recipient], data)
            if not encrypted.startswith(b'age-encryption.org/v1\n'):
                raise ValueError('age did not return ciphertext')
            publish(args.output, encrypted)
            digest = hashlib.sha256(encrypted).hexdigest()
            archived = (archive_r2(args.output, digest, args.r2_quota_bytes)
                        if args.archive_r2 else {'state': 'not_requested'})
            receipt = {'state': 'encrypted', 'sha256': digest, 'files': len(manifest['files']),
                       'missing': manifest['missing'], 'archive': archived}
        print(json.dumps(receipt, sort_keys=True))
        return 0 if receipt.get('archive', {}).get('state') != 'owed' else 1
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.SubprocessError):
        print(json.dumps({'state': 'failed', 'reason': 'state_package_refused'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
