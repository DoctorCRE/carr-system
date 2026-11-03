"""Publish a private, account-bound FTS index from an explicit decoded research copy."""
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile


class IndexError(ValueError):
    """The research copy or publication location violates the index contract."""


FIELDS = ('id', 'sender', 'sender_name', 'recipients', 'subject', 'body',
          'message_id', 'body_kind', 'sent_unix', 'sent_utc')
SCHEMA_VERSION = 1
SCHEMA = """
PRAGMA user_version=1;
CREATE TABLE index_metadata (id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL);
CREATE TABLE messages (
 id INTEGER PRIMARY KEY, sender TEXT, sender_name TEXT, recipients TEXT,
 subject TEXT, body TEXT, message_id TEXT, body_kind TEXT, sent_unix INTEGER, sent_utc TEXT,
 account_id INTEGER NOT NULL, source_ref TEXT NOT NULL UNIQUE
);
CREATE INDEX messages_date ON messages(sent_unix,id);
CREATE VIRTUAL TABLE messages_fts USING fts5(
 sender,sender_name,recipients,subject,body,
 content='messages',content_rowid='id',tokenize='porter unicode61'
);
"""


def _private_file(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise IndexError('database_requires_private_regular_file')


def _parent(path):
    if not path.is_absolute() or '..' in path.parts:
        raise IndexError('absolute_canonical_path_required')
    current = Path(path.anchor)
    for part in path.parent.parts[1:]:
        current /= part
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid()):
            raise IndexError('unsafe_parent')
        writable = info.st_mode & 0o022
        sticky_root = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        if writable and not sticky_root:
            raise IndexError('unsafe_parent')
    info = path.parent.lstat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise IndexError('parent_requires_owner_only_directory')


def _check_file(path, *, optional=False):
    try:
        _private_file(path.lstat())
    except FileNotFoundError:
        if not optional:
            raise
    for suffix in ('-wal', '-shm', '-journal'):
        if os.path.lexists(str(path) + suffix):
            raise IndexError('database_sidecars_not_supported')


def _readonly(path):
    connection = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    connection.execute('PRAGMA trusted_schema=OFF')
    return connection


def _validate_source(db, account_id):
    expected = {'messages': set(FIELDS), 'message_keys': {'message_id', 'local_key'},
                'message_objects': {'local_key', 'account_id'}}
    for table, columns in expected.items():
        kind = db.execute('SELECT type FROM sqlite_master WHERE name=?', (table,)).fetchone()
        if kind != ('table',) or not columns <= {
                row[1] for row in db.execute(f'PRAGMA table_info({table})')}:
            raise IndexError('invalid_decoded_schema')
    if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
        raise IndexError('invalid_decoded_database')
    invalid = db.execute('''SELECT 1 FROM messages m WHERE
        NOT EXISTS (SELECT 1 FROM message_keys k JOIN message_objects o
          ON o.local_key=k.local_key WHERE k.message_id=m.id AND o.account_id=?)
        OR EXISTS (SELECT 1 FROM message_keys k LEFT JOIN message_objects o
          ON o.local_key=k.local_key WHERE k.message_id=m.id
          AND (o.account_id IS NULL OR o.account_id!=?)) LIMIT 1''',
        (account_id, account_id)).fetchone()
    if invalid:
        raise IndexError('unbound_or_cross_account_rows')


def _populate(source, target, mailbox, account_id, revision, digest, built_at):
    with closing(_readonly(source)) as src, closing(sqlite3.connect(target)) as dst:
        _validate_source(src, account_id)
        dst.execute('PRAGMA temp_store=MEMORY')
        dst.executescript(SCHEMA)
        count = full = preview = undated = 0
        earliest = latest = None
        for row in src.execute('SELECT ' + ','.join(FIELDS) + ' FROM messages ORDER BY id'):
            if (not isinstance(row[0], int) or row[0] <= 0
                    or any(value is not None and not isinstance(value, str) for value in row[1:8])
                    or (row[8] is not None and not isinstance(row[8], int))
                    or (row[9] is not None and not isinstance(row[9], str))):
                raise IndexError('invalid_decoded_row')
            dst.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                        (*row, account_id, f'{digest}:{row[0]}'))
            count += 1
            full += row[7] == 'full'
            preview += row[7] == 'preview'
            if row[8] is None:
                undated += 1
            else:
                earliest = row[8] if earliest is None else min(earliest, row[8])
                latest = row[8] if latest is None else max(latest, row[8])
        dst.execute('INSERT INTO messages_fts(messages_fts) VALUES(?)', ('rebuild',))
        metadata = dict(schema_version=SCHEMA_VERSION, mailbox=mailbox, account_id=account_id,
                        source_revision=revision, source_sha256=digest, built_at=built_at,
                        observed_min_unix=earliest, observed_max_unix=latest,
                        message_count=count, full_count=full, preview_count=preview,
                        undated_count=undated, coverage='unknown', build_state='complete')
        dst.execute('INSERT INTO index_metadata VALUES(1,?)',
                    (json.dumps(metadata, sort_keys=True),))
        dst.commit()
        if dst.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise IndexError('invalid_generated_index')
    return dict(schema_version=SCHEMA_VERSION, build_state='complete', message_count=count)


def build_index(source, index, *, mailbox, account_id, source_revision, clock=None):
    """Replace one private index; source must be a closed, sidecar-free decoded copy.

    Paths must be absolute without symlink components. Root-owned sticky temporary
    ancestors are allowed, but both immediate parents must be owned 0700 directories.
    The caller supplies the mailbox label; every message must independently bind to
    account_id in the decoder tables. This does not attest source completeness.
    A killed build may leave an owner-only generation for manual removal.
    """
    if (not isinstance(account_id, int) or isinstance(account_id, bool) or account_id < 0
            or not isinstance(mailbox, str) or not mailbox.strip() or len(mailbox) > 256
            or not isinstance(source_revision, str) or not source_revision.strip()
            or len(source_revision) > 256):
        raise IndexError('invalid_binding')
    source, index = Path(source), Path(index)
    _parent(source)
    _parent(index)
    _check_file(source)
    _check_file(index, optional=True)
    if source == index:
        raise IndexError('source_is_destination')
    built_at = clock() if clock else datetime.now(timezone.utc).isoformat()
    lock = index.with_name('.' + index.name + '.lock')
    lock_fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        _private_file(os.fstat(lock_fd))
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise IndexError('writer_busy') from error
        _check_file(index, optional=True)
        with tempfile.TemporaryDirectory(prefix='.email-search-', dir=index.parent) as generation:
            snapshot = Path(generation) / 'source.db'
            target = Path(generation) / 'index.db'
            source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(source_fd, 'rb') as reader:
                before = os.fstat(reader.fileno())
                _private_file(before)
                with os.fdopen(os.open(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as writer:
                    shutil.copyfileobj(reader, writer)
                after = os.fstat(reader.fileno())
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                        after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise IndexError('source_changed_during_snapshot')
            _check_file(source)
            with snapshot.open('rb') as reader:
                digest = hashlib.file_digest(reader, 'sha256').hexdigest()
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            try:
                result = _populate(snapshot, target, mailbox, account_id, source_revision, digest, built_at)
            except sqlite3.Error as error:
                raise IndexError('invalid_decoded_database') from error
            _check_file(target)
            with target.open('rb') as reader:
                os.fsync(reader.fileno())
            snapshot.unlink()
            _parent(index)
            _check_file(index, optional=True)
            os.replace(target, index)
            directory_fd = os.open(index.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return result
    finally:
        os.close(lock_fd)
