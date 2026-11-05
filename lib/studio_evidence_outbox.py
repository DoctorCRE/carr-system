"""Durable delivery of Studio claims, never a source of canonical proof truth.

The future authenticated transport must deduplicate by execution UUID and digest,
verify the producer and local readbacks, and enforce its supplied timeout.
"""
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from uuid import UUID


@dataclass(frozen=True)
class Capture:
    run_id: str
    installation_id: str
    host_id: str
    target: str
    runtime_sha: str
    command_sha256: str
    helper_sha256: str
    plists_sha256: str
    executables_sha256: str
    install_started_at: datetime
    install_ended_at: datetime
    started_at: datetime
    ended_at: datetime

    def encode(self):
        """Validate staging claims without asserting that their contents are true."""
        try:
            if any(str(UUID(value)) != value for value in (self.run_id, self.installation_id)):
                raise ValueError('canonical UUID required')
            if (self.target != 'studio' or not isinstance(self.host_id, str)
                    or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}', self.host_id)):
                raise ValueError('registered Studio host required')
            for field, size in ((self.runtime_sha, 40), (self.command_sha256, 64),
                                (self.helper_sha256, 64), (self.plists_sha256, 64),
                                (self.executables_sha256, 64)):
                if not isinstance(field, str) or not re.fullmatch('[0-9a-f]{'+str(size)+'}', field):
                    raise ValueError('full lowercase source and content digests required')
            times = (self.install_started_at, self.install_ended_at, self.started_at, self.ended_at)
            if any(not isinstance(t, datetime) or t.utcoffset() is None for t in times):
                raise ValueError('aware intervals required')
            if not all(a <= b for a, b in zip(times, times[1:])):
                raise ValueError('execution must follow installation')
        except (TypeError, AttributeError) as error:
            raise ValueError('invalid Studio claim') from error
        data = asdict(self)
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.astimezone(timezone.utc).isoformat()
        return json.dumps(data, sort_keys=True, separators=(',', ':'))

    @classmethod
    def decode(cls, payload):
        data = json.loads(payload)
        for field in ('install_started_at', 'install_ended_at', 'started_at', 'ended_at'):
            data[field] = datetime.fromisoformat(data[field])
        capture = cls(**data)
        if capture.encode() != payload:
            raise ValueError('stored claim changed')
        return capture


@dataclass(frozen=True)
class Acknowledgement:
    run_id: str
    digest: str
    receipt_id: str


class Refused(Exception):
    """An authenticated ingestion refusal. Its details never enter local logs."""


class Outbox:
    """Explicit private SQLite file. Retain audit rows and refuse at capacity."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Exclusive creation avoids changing permissions on someone else's file.
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
        with closing(self._connect()) as conn, conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS claims (
                    run_id TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('pending','acknowledged','refused')),
                    receipt_id TEXT);
                CREATE TABLE IF NOT EXISTS breaker (
                    id INTEGER PRIMARY KEY CHECK(id=1), failures INTEGER NOT NULL);
                INSERT OR IGNORE INTO breaker VALUES (1,0);
            ''')

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute('PRAGMA synchronous=FULL')
        return conn

    def enqueue(self, capture):
        payload = capture.encode()
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with closing(self._connect()) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT payload, state FROM claims WHERE run_id=?', (capture.run_id,)).fetchone()
            if row:
                if row[0] != payload:
                    raise ValueError('execution UUID already has a different claim')
                return row[1]
            if conn.execute('SELECT count(*) FROM claims').fetchone()[0] >= 1000:
                raise OverflowError('Studio claim outbox full; archive requires diagnosed recovery')
            conn.execute('INSERT INTO claims VALUES (?,?,?,?,NULL)',
                         (capture.run_id, payload, digest, 'pending'))
        return 'pending'

    def resume(self):
        """Explicit diagnosed recovery, never automatically reset on restart."""
        with closing(self._connect()) as conn, conn:
            conn.execute('UPDATE breaker SET failures=0 WHERE id=1')

    def flush(self, send):
        """Send at most 20 rows. Matching acknowledgement only means delivery.

        The transport enforces timeout=5 and authenticates the receiver. A process
        exit before commit replays identical keys, including remotely accepted rows.
        """
        with closing(self._connect()) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            failures = conn.execute('SELECT failures FROM breaker WHERE id=1').fetchone()[0]
            if failures < 2:
                rows = conn.execute("SELECT run_id,payload,digest FROM claims WHERE state='pending' ORDER BY rowid LIMIT 20").fetchall()
                for run_id, payload, digest in rows:
                    if hashlib.sha256(payload.encode()).hexdigest() != digest:
                        raise ValueError('stored claim digest mismatch')
                    claim = Capture.decode(payload)
                    try:
                        ack = send(claim, digest, timeout=5)
                    except Refused:
                        conn.execute("UPDATE claims SET state='refused' WHERE run_id=?", (run_id,))
                        failures = 0
                    except (OSError, TimeoutError):
                        failures += 1
                        break
                    else:
                        try:
                            matched = (isinstance(ack, Acknowledgement) and ack.run_id == run_id
                                       and ack.digest == digest and str(UUID(ack.receipt_id)) == ack.receipt_id)
                        except (ValueError, TypeError, AttributeError):
                            matched = False
                        if not matched:
                            failures = 2
                            break
                        conn.execute("UPDATE claims SET state='acknowledged',receipt_id=? WHERE run_id=?",
                                     (ack.receipt_id, run_id))
                        failures = 0
                conn.execute('UPDATE breaker SET failures=? WHERE id=1', (failures,))
            counts = dict(conn.execute('SELECT state,count(*) FROM claims GROUP BY state').fetchall())
        return {key: counts.get(key, 0) for key in ('acknowledged', 'refused', 'pending')} | {'paused': failures >= 2}
