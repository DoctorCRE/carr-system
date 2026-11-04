"""Publish a private, account-bound FTS index from an explicit decoded research copy."""
from contextlib import closing
from datetime import datetime, timezone
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile


class IndexError(ValueError):
    """The research copy or publication location violates the index contract."""


FIELDS = ('id', 'sender', 'sender_name', 'recipients', 'subject', 'body',
          'message_id', 'body_kind', 'sent_unix', 'sent_utc')
MAX_INDEX_BYTES = 512 * 1024 * 1024
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


def _reject_source_alias(source, destination):
    try:
        destination_info = destination.resolve().stat()
    except FileNotFoundError:
        return
    if os.path.samestat(source.resolve().stat(), destination_info):
        raise IndexError('source_is_destination')


def _validate_source(db, account_id, select_account=False):
    expected = {'messages': set(FIELDS), 'message_keys': {'message_id', 'local_key'},
                'message_objects': {'local_key', 'account_id'}}
    for table, columns in expected.items():
        kind = db.execute('SELECT type FROM sqlite_master WHERE name=?', (table,)).fetchone()
        if kind != ('table',) or not columns <= {
                row[1] for row in db.execute(f'PRAGMA table_info({table})')}:
            raise IndexError('invalid_decoded_schema')
    if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
        raise IndexError('invalid_decoded_database')
    if select_account:
        return
    invalid = db.execute('''SELECT 1 FROM messages m WHERE
        NOT EXISTS (SELECT 1 FROM message_keys k JOIN message_objects o
          ON o.local_key=k.local_key WHERE k.message_id=m.id AND o.account_id=?)
        OR EXISTS (SELECT 1 FROM message_keys k LEFT JOIN message_objects o
          ON o.local_key=k.local_key WHERE k.message_id=m.id
          AND (o.account_id IS NULL OR o.account_id!=?)) LIMIT 1''',
        (account_id, account_id)).fetchone()
    if invalid:
        raise IndexError('unbound_or_cross_account_rows')


def _populate(source, target, mailbox, account_id, revision, digest, built_at, select_account):
    with closing(_readonly(source)) as src, closing(sqlite3.connect(target)) as dst:
        _validate_source(src, account_id, select_account)
        dst.execute('PRAGMA temp_store=MEMORY')
        page_size = dst.execute('PRAGMA page_size').fetchone()[0]
        dst.execute(f'PRAGMA max_page_count={MAX_INDEX_BYTES // page_size}')
        dst.executescript(SCHEMA)
        count = full = preview = undated = 0
        earliest = latest = None
        selection = ''' WHERE EXISTS (SELECT 1 FROM message_keys k JOIN message_objects o
            ON o.local_key=k.local_key WHERE k.message_id=m.id AND o.account_id=:account)
            AND NOT EXISTS (SELECT 1 FROM message_keys k LEFT JOIN message_objects o
            ON o.local_key=k.local_key WHERE k.message_id=m.id
            AND (o.account_id IS NULL OR o.account_id!=:account))''' if select_account else ''
        for row in src.execute('SELECT ' + ','.join('m.' + f for f in FIELDS) +
                               ' FROM messages m' + selection + ' ORDER BY m.id',
                               {'account': account_id} if select_account else {}):
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


def build_index(source, index, *, mailbox, account_id, source_revision, clock=None, allow_empty=False, select_account=False):
    """Replace one private index; source must be a closed, sidecar-free decoded copy.

    Paths must be absolute without symlink components. Root-owned sticky temporary
    ancestors are allowed, but both immediate parents must be owned 0700 directories.
    The caller supplies the mailbox label; every message must independently bind to
    account_id in the decoder tables. This does not attest source completeness.
    A killed build may leave an owner-only generation for manual removal.
    Replacing an existing index with zero messages requires allow_empty=True.
    select_account projects positively bound, non-conflicting rows from a multi-account
    decode. Such intake builds reuse an identical source/revision generation.
    Publications are capped at MAX_INDEX_BYTES.
    """
    if (type(select_account) is not bool
            or not isinstance(account_id, int) or isinstance(account_id, bool)
            or not 0 <= account_id <= 2**63 - 1
            or not isinstance(mailbox, str) or not mailbox.strip() or len(mailbox) > 256
            or not isinstance(source_revision, str) or not source_revision.strip()
            or len(source_revision) > 256):
        raise IndexError('invalid_binding')
    source, index = Path(source), Path(index)
    lock = index.with_name('.' + index.name + '.lock')
    _reject_source_alias(source, index)
    _reject_source_alias(source, lock)
    _parent(source)
    _parent(index)
    _check_file(source)
    _check_file(index, optional=True)
    built_at = clock() if clock else datetime.now(timezone.utc).isoformat()
    lock_fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        _private_file(os.fstat(lock_fd))
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise IndexError('writer_busy') from error
        _check_file(index, optional=True)
        _reject_source_alias(source, index)
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
            if select_account and index.exists():
                try:
                    with closing(_readonly(index)) as existing:
                        coverage = _index_metadata(existing, account_id, source_revision, digest)
                        metadata = json.loads(existing.execute('SELECT document FROM index_metadata').fetchone()[0])
                    if metadata['mailbox'] == mailbox:
                        return dict(schema_version=SCHEMA_VERSION, build_state='complete',
                                    message_count=coverage['message_count'])
                except (IndexError, sqlite3.Error, ValueError, KeyError, TypeError):
                    pass
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            try:
                result = _populate(snapshot, target, mailbox, account_id, source_revision, digest, built_at, select_account)
            except sqlite3.Error as error:
                raise IndexError('invalid_decoded_database') from error
            _check_file(target)
            with target.open('rb') as reader:
                os.fsync(reader.fileno())
            snapshot.unlink()
            _parent(index)
            _check_file(index, optional=True)
            _reject_source_alias(source, index)
            if result['message_count'] == 0 and index.exists() and not allow_empty:
                raise IndexError('empty_source_would_replace_index')
            os.replace(target, index)
            directory_fd = os.open(index.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return result
    finally:
        os.close(lock_fd)


def _literal_phrase(query):
    if (not isinstance(query, str) or not query.strip() or len(query) > 512
            or any(ord(c) < 32 for c in query)
            or any(c in query for c in '*:(){}[]^')
            or re.search(r'\b(?:AND|OR|NOT|NEAR)\b', query)
            or not any(c.isalnum() for c in query)):
        raise IndexError('invalid_literal_query')
    return '"' + query.strip().replace('"', '""') + '"'


def _index_metadata(db, account_id, source_revision, source_digest):
    with closing(sqlite3.connect(':memory:')) as expected:
        expected.executescript(SCHEMA)
        schema = 'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name'
        if (db.execute('PRAGMA user_version').fetchone() != (SCHEMA_VERSION,)
                or db.execute(schema).fetchall() != expected.execute(schema).fetchall()):
            raise IndexError('invalid_index_schema')
    row = db.execute('SELECT document FROM index_metadata WHERE id=1').fetchone()
    if row is None or len(row[0]) > 8192:
        raise IndexError('invalid_index_metadata')
    metadata = json.loads(row[0])
    if (not isinstance(metadata, dict) or metadata.get('schema_version') != SCHEMA_VERSION
            or metadata.get('build_state') != 'complete'
            or metadata.get('account_id') != account_id
            or metadata.get('coverage') != 'unknown'):
        raise IndexError('incomplete_or_unbound_index')
    try:
        built_at = datetime.fromisoformat(metadata['built_at'])
        if (built_at.tzinfo is None or built_at.utcoffset() is None
                or not isinstance(metadata['source_revision'], str)
                or not metadata['source_revision'].strip()
                or len(metadata['source_revision']) > 256
                or not isinstance(metadata['source_sha256'], str)
                or not re.fullmatch(r'[0-9a-f]{64}', metadata['source_sha256'])):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise IndexError('index_freshness_unavailable') from None
    if source_revision is not None and (metadata['source_revision'] != source_revision
            or metadata['source_sha256'] != source_digest):
        raise IndexError('stale_index')
    observed = db.execute('''SELECT count(*),min(sent_unix),max(sent_unix),
        sum(sent_unix IS NULL) FROM messages''').fetchone()
    if (any(value is not None and type(value) is not int for value in observed)
            or observed[:3] != (metadata.get('message_count'), metadata.get('observed_min_unix'),
                         metadata.get('observed_max_unix'))
            or (observed[3] or 0) != metadata.get('undated_count')
            or db.execute('SELECT 1 FROM messages WHERE account_id IS NOT ? LIMIT 1',
                          (account_id,)).fetchone()):
        raise IndexError('invalid_index_binding_or_coverage')
    return dict(message_count=observed[0], observed_min_unix=observed[1],
                observed_max_unix=observed[2], undated_count=observed[3] or 0,
                completeness='unknown', built_at=metadata['built_at'],
                freshness=('matches_current_decoded_source' if source_revision is not None
                           else 'published_snapshot_capture_recency_unverified'))


def query_index(index, *, source=None, source_revision=None, account_id, query, limit=20, cursor=None, excerpts=False, published_snapshot=False):
    """Read one completed private index using a literal FTS phrase.

    All words form one phrase, including quoted names and email addresses. FTS
    operators, column selectors and wildcards are unsupported. Results sort by
    descending timestamp then id, with undated rows last. Cursors bind the query
    and account to the database bytes; rebuilding invalidates prior pages.
    By default the caller supplies the current closed decoded copy and revision.
    published_snapshot=True instead reads only the retained index and explicitly
    reports that capture recency is unverified. It cannot be combined with a source.
    Freshness is exact source byte identity, not a maximum age. Missing or invalid
    provenance and changed sources refuse every page before MATCH. A timestamp
    describes publication age only; it does not prove mailbox capture recency.
    Excerpts expose up to 240 subject and 500 body characters locally.
    """
    phrase = _literal_phrase(query)
    if (type(account_id) is not int or not 0 <= account_id <= 2**63 - 1
            or type(limit) is not int or not 1 <= limit <= 100
            or type(excerpts) is not bool
            or type(published_snapshot) is not bool
            or (published_snapshot and (source is not None or source_revision is not None))
            or (not published_snapshot and (not isinstance(source_revision, str)
                or not source_revision.strip() or len(source_revision) > 256))):
        raise IndexError('invalid_query_options')
    try:
        source_digest = None
        if not published_snapshot:
            source = Path(source)
            _parent(source)
            _check_file(source)
            with source.open('rb') as reader:
                before_source = os.fstat(reader.fileno())
                _private_file(before_source)
                source_digest = hashlib.file_digest(reader, 'sha256').hexdigest()
                after_source = os.fstat(reader.fileno())
            _check_file(source)
            if ((before_source.st_size, before_source.st_mtime_ns, before_source.st_ctime_ns) !=
                    (after_source.st_size, after_source.st_mtime_ns, after_source.st_ctime_ns)
                    or not os.path.samestat(after_source, source.stat())):
                raise IndexError('stale_index')
        path = Path(index)
        _parent(path)
        _check_file(path)
        with path.open('rb') as reader:
            before = os.fstat(reader.fileno())
            generation = hashlib.file_digest(reader, 'sha256').hexdigest()
        binding = hashlib.sha256(f'{generation}\n{account_id}\n{phrase}'.encode()).hexdigest()
        offset = 0
        if cursor is not None:
            if not isinstance(cursor, str) or len(cursor) > 256:
                raise IndexError('invalid_cursor')
            page = json.loads(base64.b64decode(cursor, altchars=b'-_', validate=True))
            if (not isinstance(page, list) or len(page) != 2 or page[0] != binding
                    or type(page[1]) is not int or not 0 < page[1] <= 1_000_000):
                raise IndexError('stale_or_invalid_cursor')
            offset = page[1]
        with closing(_readonly(path)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            db.execute('SELECT count(*) FROM sqlite_master').fetchone()
            after = path.lstat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise IndexError('index_changed_during_open')
            _check_file(path)
            coverage = _index_metadata(db, account_id, source_revision, source_digest)
            where = ''' FROM messages m JOIN messages_fts f ON f.rowid=m.id
                WHERE messages_fts MATCH ? AND m.account_id=?'''
            count = db.execute('SELECT count(*)' + where, (phrase, account_id)).fetchone()[0]
            columns = 'm.id,m.sent_unix,m.source_ref'
            if excerpts:
                columns += ',substr(m.subject,1,240),substr(m.body,1,500)'
            rows = db.execute('SELECT ' + columns + where +
                              ' ORDER BY m.sent_unix DESC,m.id DESC LIMIT ? OFFSET ?',
                              (phrase, account_id, limit, offset)).fetchall()
            results = []
            for row in rows:
                if (type(row[0]) is not int or (row[1] is not None and type(row[1]) is not int)
                        or not isinstance(row[2], str)
                        or not re.fullmatch(r'[0-9a-f]{64}:[0-9]{1,19}', row[2])):
                    raise IndexError('invalid_index_row')
                item = dict(sent_unix=row[1], source_ref=row[2])
                if excerpts:
                    item['excerpt'] = dict(subject=row[3], body=row[4])
                results.append(item)
        if not published_snapshot:
            _check_file(source)
            current_source = source.stat()
            if (after_source.st_dev, after_source.st_ino, after_source.st_size,
                    after_source.st_mtime_ns, after_source.st_ctime_ns) != (
                    current_source.st_dev, current_source.st_ino, current_source.st_size,
                    current_source.st_mtime_ns, current_source.st_ctime_ns):
                raise IndexError('stale_index')
        next_offset = offset + len(rows)
        next_cursor = None
        if next_offset < count:
            if next_offset > 1_000_000:
                raise IndexError('pagination_limit_reached')
            next_cursor = base64.urlsafe_b64encode(json.dumps([binding, next_offset]).encode()).decode()
        return dict(match_count=count, results=results, next_cursor=next_cursor, coverage=coverage,
                    meaning='No indexed match is not proof of no prior relationship.')
    except IndexError:
        raise
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise IndexError('private_index_query_refused') from None
