#!/usr/bin/env python3
"""Bound recovery writes behind trusted observations and a durable attempt cap."""
from contextlib import closing
from dataclasses import dataclass
import re
import sqlite3


@dataclass(frozen=True)
class Drop:
    repository: str
    number: int
    event_id: str
    head: str
    approval_id: int
    group_sha: str
    reason: str
    actor: str
    runs: tuple
    runs_complete: bool


@dataclass(frozen=True)
class Observation:
    head: str
    approval_id: int | None
    held: bool
    state: str
    queued: bool


def cause(drop):
    if drop.actor != 'github-merge-queue':
        return 'human-or-unknown-removal'
    if drop.reason != 'failed_checks':
        return drop.reason or 'unknown'
    if (drop.runs_complete is not True or not drop.runs or len(drop.runs) > 100
            or any(c.get('event') != 'merge_group' or c.get('head_sha') != drop.group_sha or c.get('status') != 'completed'
                   for c in drop.runs)):
        return 'incomplete-group-checks'
    failed = [c.get('conclusion') for c in drop.runs if c.get('conclusion') != 'success']
    if failed and all(value == 'startup_failure' for value in failed):
        return 'startup_failure'
    if len(set(failed)) == 1:
        return {'failure': 'test-failure', 'cancelled': 'cancelled',
                'timed_out': 'timed_out'}.get(failed[0], 'failed_checks')
    return 'failed_checks'


def recover(drop, database, observe, arm):
    """Caller supplies trusted enqueue facts and S14's fresh-observation/native-write seam.

    arm(drop) must recheck head, approval, holds and required checks immediately
    before native match-head arming. An exception is an ambiguous write, never a
    reason to repeat it. Keep the database on durable coordinator-owned storage.
    """
    if (drop.repository != 'DoctorCRE/carr-system' or type(drop.number) is not int
            or drop.number < 1 or not isinstance(drop.event_id, str)
            or not 1 <= len(drop.event_id) <= 256
            or not re.fullmatch('[0-9a-f]{40}', drop.head)
            or not re.fullmatch('[0-9a-f]{40}', drop.group_sha)
            or type(drop.approval_id) is not int or drop.approval_id < 1):
        raise ValueError('Recovery requires a CARR PR and bound enqueue/event identities')
    if str(database) == ':memory:':
        raise ValueError('Recovery requires a durable journal')
    key = (drop.repository, drop.number, drop.event_id)
    with closing(sqlite3.connect(database, timeout=5)) as db, db:
        db.execute('''CREATE TABLE IF NOT EXISTS recovery (
            repository TEXT NOT NULL, number INTEGER NOT NULL, event_id TEXT NOT NULL,
            head TEXT NOT NULL, cause TEXT NOT NULL, attempted INTEGER NOT NULL,
            state TEXT NOT NULL, PRIMARY KEY(repository, number, event_id))''')
        db.execute('CREATE INDEX IF NOT EXISTS recovery_head ON recovery(repository,number,head)')
        db.execute('BEGIN IMMEDIATE')
        prior = db.execute('SELECT state FROM recovery WHERE repository=? AND number=? AND event_id=?', key).fetchone()
        if prior:
            return {'state': 'duplicate', 'previous': prior[0]}
        why = cause(drop)
        state = 'diagnosis'
        if why == 'startup_failure':
            current = observe(drop)
            if current.head != drop.head:
                why = 'head-changed'
            elif type(current.approval_id) is not int or current.approval_id != drop.approval_id:
                why = 'approval-changed'
            elif current.held is not False:
                why = 'held'
            elif current.queued is not False or current.state != 'open':
                why = 'already-queued-or-closed'
            else:
                attempts = db.execute('''SELECT cause, state FROM recovery
                    WHERE repository=? AND number=? AND head=? AND attempted=1''',
                    (drop.repository, drop.number, drop.head)).fetchall()
                if any(row[1] in {'reserved', 'uncertain'} for row in attempts):
                    why = 'unresolved-write'
                elif len(attempts) >= 2 or sum(row[0] == why for row in attempts) >= 2:
                    why = 'retry-limit'
                else:
                    state = 'reserved'
        db.execute('INSERT INTO recovery VALUES(?,?,?,?,?,?,?)',
                   (*key, drop.head, why, int(state == 'reserved'), state))
        db.commit()
        if state != 'reserved':
            return {'state': state, 'reason': why}
        # Commit before the external write so a crash never erases an attempt.
        try:
            receipt = arm(drop)
            if not isinstance(receipt, dict) or receipt.get('state') != 'armed' or receipt.get('head_sha') != drop.head:
                raise ValueError('Native adapter did not confirm head-bound arming')
        except Exception:
            state = 'uncertain'
        else:
            state = 'armed'
        db.execute('UPDATE recovery SET state=? WHERE repository=? AND number=? AND event_id=?', (state, *key))
        return {'state': state, 'reason': why}
