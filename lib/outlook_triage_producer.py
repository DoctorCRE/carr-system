"""Bounded research-copy projection. Local observations never authorize moves."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import resource
import sqlite3
import stat
import subprocess
import tempfile
import time

from lib.outlook_settings import load_settings
from lib.outlook_triage_snapshot import MAX_BYTES, MAX_ROWS, read_snapshot

SOURCE_BYTES = 256 * 1024 * 1024
DECODED_BYTES = 256 * 1024 * 1024
FILE_CAP = 4096
DEADLINE_SECONDS = 180


class ProducerError(ValueError):
    pass


def _check(deadline):
    if time.monotonic() >= deadline:
        raise ProducerError('deadline_exceeded')


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _read(path, cap, deadline):
    _check(deadline)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > cap:
            raise ProducerError('byte_cap_or_invalid_file')
        chunks, size = [], 0
        while chunk := handle.read(min(1024 * 1024, cap + 1 - size)):
            _check(deadline)
            chunks.append(chunk)
            size += len(chunk)
            if size > cap:
                raise ProducerError('byte_cap_or_invalid_file')
        if (_identity(before) != _identity(os.fstat(handle.fileno()))
                or _identity(before) != _identity(path.stat(follow_symlinks=False))):
            raise ProducerError('source_changed')
    return b''.join(chunks)


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _copy_files(source, manifest, stage, deadline):
    rows = manifest['files']
    if not isinstance(rows, list) or not 0 < len(rows) <= FILE_CAP:
        raise ProducerError('file_cap')
    seen, total, hashes = set(), 0, []
    for row in rows:
        relative = Path(row['path'])
        if (relative.is_absolute() or '..' in relative.parts or not relative.parts
                or str(relative) != row['path'] or row['path'] in seen
                or not (row['path'] == 'HxStore.hxd' or relative.parts[0] == 'Files')):
            raise ProducerError('manifest_path_invalid')
        seen.add(row['path'])
        if type(row['size']) is not int or row['size'] < 0:
            raise ProducerError('manifest_invalid')
        total += row['size']
        if total > SOURCE_BYTES:
            raise ProducerError('source_byte_cap')
        path = source / relative
        if any((source / Path(*relative.parts[:i])).is_symlink()
               for i in range(1, len(relative.parts) + 1)):
            raise ProducerError('manifest_path_invalid')
        raw = _read(path, row['size'], deadline)
        digest = _hash(raw)
        if len(raw) != row['size'] or digest != row['sha256']:
            raise ProducerError('source_changed')
        hashes.append((row['path'], digest))
        if stage is not None:
            target = stage / relative
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with target.open('xb') as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(raw)
    if 'HxStore.hxd' not in seen:
        raise ProducerError('manifest_invalid')
    return hashes


def project(db, settings, guide, snapshot_digest, decoder_pin, *, max_messages=MAX_ROWS,
            deadline=None):
    """Project decoded local keys; missing mover semantics remain empty/unknown."""
    deadline = deadline or time.monotonic() + DEADLINE_SECONDS
    _check(deadline)
    if db.stat().st_size > DECODED_BYTES:
        raise ProducerError('decoded_byte_cap')
    con = sqlite3.connect(db.as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        con.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        if con.execute('SELECT count(*) FROM messages').fetchone()[0] > max_messages:
            raise ProducerError('message_cap')
        if con.execute('SELECT count(*) FROM folders').fetchone()[0] > MAX_ROWS:
            raise ProducerError('folder_cap')
        folders = {r[0]: r[1:] for r in con.execute(
            'SELECT id,account_id,name,parent_status,parent_id FROM folders')}
        links = {}
        for message, folder in con.execute('SELECT message_id,folder_id FROM message_folders'):
            _check(deadline)
            links.setdefault(message, set()).add(folder)
        rows = con.execute('SELECT id,sender,subject,body,recipients,local_keys,folder_links_unresolved '
                           'FROM messages ORDER BY id').fetchall()
        account_id = settings.folder_account_id
        def owned(row):
            fs = links.get(row[0], set())
            return bool(fs) and all(f in folders and folders[f][0] == account_id for f in fs)
        if not account_id or not any(owned(r) and not r[6] and r[1]
                and r[1].strip().casefold() == settings.account.casefold()
                and any((folders[f][1] or '').casefold() in ('sent','sent items','sent mail')
                        for f in links[r[0]]) for r in rows):
            raise ProducerError('account_binding_unverified')
        def local(kind, key):
            return f'hx-local:{account_id}:{kind}:{key}'
        paths = {}
        for key, (owner, name, status, parent) in folders.items():
            parts, seen, current = [], set(), key
            while current in folders and current not in seen:
                seen.add(current)
                owner, name, status, parent = folders[current]
                if owner != account_id or not name or '/' in name:
                    break
                parts.append(name)
                if status == 'top':
                    paths[key] = '/'.join(reversed(parts))
                    break
                if status != 'folder':
                    break
                current = parent
        inboxes = [key for key, path in paths.items() if path == 'Inbox']
        if len(inboxes) != 1:
            raise ProducerError('inbox_unresolved')
        observed = datetime.now(timezone.utc).isoformat()
        observations = []
        counts = dict(observed=0, outside_account=0, ownership_unverified=0,
                      ownership_missing=0, ownership_dangling=0,
                      ownership_conflicting=0, folder_links_unresolved=0, moved=0)
        for row in rows:
            _check(deadline)
            ident, sender, subject, body, recipients, key, unresolved = row
            fs = links.get(ident, set())
            if fs and all(f in folders and folders[f][0] != account_id for f in fs):
                counts['outside_account'] += 1
                continue
            if not owned(row) or unresolved:
                counts['ownership_unverified'] += 1
                reason = ('ownership_missing' if not fs else
                          'ownership_dangling' if any(f not in folders for f in fs) else
                          'ownership_conflicting' if not owned(row) else
                          'folder_links_unresolved')
                counts[reason] += 1
                continue
            source = local('folder', next(iter(fs))) if len(fs) == 1 else ''
            observations.append(dict(account=settings.account,
                message_id=local('message', key) if key and key.isdecimal() else '',
                revision='', source_folder_id=source, read_state='unknown',
                snapshot_digest=snapshot_digest, decoder_pin=decoder_pin, observed_at=observed,
                sender=sender, subject=subject, body=body, recipients=[], content_complete=False))
        counts['observed'] = len(observations)
        version = _hash(guide.encode())
        value = dict(schema='outlook-triage-snapshot/v1',account=settings.account,
            inbox_folder_id=local('folder',inboxes[0]),observed_at=observed,
            snapshot_digest=snapshot_digest,decoder_pin=decoder_pin,routing_version=version,
            observations=observations,routing=dict(version=version,guide=guide,find_results={},complete=False),
            catalog=dict(version=snapshot_digest,folders=[dict(account=settings.account,
                         folder_id=local('folder',key),path=path) for key,path in sorted(paths.items())]),
            effect_state='none', availability=dict(identity_scope='account_bound_local_copy_only',
                provider_identity='unavailable',message_revision='unavailable',read_state='unavailable',
                content_completeness='unverified',recipients='unverified',server_coverage='unknown',
                copy_capture_time='unknown',mailbox_freshness='unknown',routing_lookups='unavailable'))
        return value, counts
    finally:
        con.close()


def _decoder_limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (DECODED_BYTES, DECODED_BYTES))


def produce(source, output, settings):
    """Decode an existing research copy and atomically publish private planning input."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    source, output = Path(source).absolute(), Path(output).absolute()
    if source.is_symlink() or not source.is_dir():
        raise ProducerError('source_invalid')
    source = source.resolve()
    if output.is_relative_to(source):
        raise ProducerError('output_inside_source')
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = output.parent.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ProducerError('output_directory_invalid')
    if output.parent.resolve().is_relative_to(source):
        raise ProducerError('output_inside_source')
    if output.exists() or output.is_symlink():
        previous = output.lstat()
        if (not stat.S_ISREG(previous.st_mode) or previous.st_uid != os.getuid()
                or stat.S_IMODE(previous.st_mode) != 0o600):
            raise ProducerError('previous_output_invalid')
        prior = json.loads(_read(output, MAX_BYTES, deadline))
        if prior.get('effect_state') != 'none':
            raise ProducerError('reconciliation_required')
    manifest_raw = _read(source / 'manifest.json', MAX_BYTES, deadline)
    manifest = json.loads(manifest_raw)
    if manifest['schema'] != 'outlook-research-snapshot/v1':
        raise ProducerError('manifest_invalid')
    receipt_raw = _read(settings.vendor / '.carr-build.json', MAX_BYTES, deadline)
    receipt = json.loads(receipt_raw)
    binary = _read(settings.decoder, SOURCE_BYTES, deadline)
    pin = _hash(binary)
    if (pin != receipt['binary_sha256'] or not isinstance(receipt['source_digest'], str)
            or len(receipt['source_digest']) != 64):
        raise ProducerError('decoder_pin_mismatch')
    guide = _read(settings.folder_guide, MAX_BYTES, deadline).decode()
    with tempfile.TemporaryDirectory(prefix='.triage-', dir=output.parent) as temporary:
        stage = Path(temporary)
        original = _copy_files(source, manifest, stage, deadline)
        decoder = stage / 'decoder'
        decoder.write_bytes(binary); decoder.chmod(0o700)
        db = stage / 'decoded.db'
        result = subprocess.run([str(decoder),'db',str(stage/'HxStore.hxd'),str(db),str(stage/'Files')],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            timeout=max(0.001, deadline-time.monotonic()),preexec_fn=_decoder_limits)
        if result.returncode:
            raise ProducerError('decoder_failed')
        value, counts = project(db,settings,guide,_hash(manifest_raw),pin,deadline=deadline)
        value['provenance'] = dict(manifest_sha256=_hash(manifest_raw),
            source_files_sha256=_hash(json.dumps(original,sort_keys=True).encode()),
            decoder_binary_sha256=pin,decoder_source_digest=receipt['source_digest'],
            decoded_sha256=_hash(_read(db,DECODED_BYTES,deadline)))
        raw = (json.dumps(value,sort_keys=True) + '\n').encode()
        if len(raw) > MAX_BYTES:
            raise ProducerError('output_byte_cap')
        staged = stage / 'snapshot.json'
        with staged.open('xb') as handle:
            os.fchmod(handle.fileno(),0o600)
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        read_snapshot(staged,settings,150)
        if (_copy_files(source,manifest,None,deadline) != original
                or _read(source/'manifest.json',MAX_BYTES,deadline) != manifest_raw
                or _read(settings.vendor/'.carr-build.json',MAX_BYTES,deadline) != receipt_raw
                or _hash(_read(settings.decoder,SOURCE_BYTES,deadline)) != pin
                or _read(settings.folder_guide,MAX_BYTES,deadline).decode() != guide):
            raise ProducerError('source_changed')
        _check(deadline)
        os.replace(staged,output)
        directory_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--copy',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args(argv)
    try:
        counts = produce(args.copy,args.output,load_settings())
        print(json.dumps(dict(state='observed_held',reason='mover_semantics_unavailable',counts=counts),sort_keys=True))
        return 78
    except ProducerError as exc:
        print(json.dumps(dict(state='refused',reason=str(exc),moved=0)))
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error,subprocess.SubprocessError):
        print(json.dumps(dict(state='refused',reason='source_or_decoder_invalid',moved=0)))
    return 78


if __name__ == '__main__':
    raise SystemExit(main())
