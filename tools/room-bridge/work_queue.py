"""One durable order for cards, executors and the progress board.

Requeue moves a card after all existing cards. Eligibility filters that order;
it never maintains a per-desk order. Running work is not preempted.

The canonical database is beside CARR_HERMES_RESULTS, or the default dispatch
results file. CARR_WORK_QUEUE_DB selects an explicit isolated store. A job's
--results destination never selects another queue. SQLite WAL, synchronous
FULL, BEGIN IMMEDIATE mutations and the runner's nonblocking flock preserve
executor ownership.

export() returns room-work-queue/v1, including full ordered cards and events.

board_projection() retains every unfinished card, twenty finished cards and
one hundred recent events, with explicit omission counts. export() retains
all history. Raw task prompts and executor output stay local.

import_results() is an explicit, idempotent migration. Historical rows become
done cards and unknown timing stays unknown. Pending shell-chain steps must
be reconciled against receipts and entered through send in their intended
order; this module never executes or infers work from old shell scripts.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import sqlite3
import time

STAGES = ('queued', 'building', 'review', 'fixing', 'merging', 'done', 'failed', 'blocked', 'parked')
EXECUTABLE = ('queued', 'building', 'review', 'fixing')
TERMINAL = ('done', 'failed', 'parked')
FIELDS = {'wr', 'slice', 'title', 'description', 'agent', 'family', 'model', 'effort',
          'prs', 'ci_state', 'findings'}


def default_path() -> Path:
    if os.environ.get('CARR_WORK_QUEUE_DB'):
        return Path(os.environ['CARR_WORK_QUEUE_DB']).expanduser().resolve()
    results = Path(os.environ.get('CARR_HERMES_RESULTS', Path.home() / '.config/carr/hermes-dispatch-results.jsonl'))
    return results.expanduser().resolve().with_name('work-queue.sqlite3')


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


class QueueChanged(ValueError):
    pass


class WorkQueue:
    def __init__(self, path: Path, *, read_only=False):
        self.path = Path(path)
        self.read_only = read_only
        if read_only:
            self.db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=30)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(self.path, timeout=30)
        self.db.row_factory = sqlite3.Row
        if read_only:
            return
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS items (
                card_id TEXT PRIMARY KEY, position INTEGER NOT NULL,
                stage TEXT NOT NULL, desk TEXT NOT NULL, ready INTEGER NOT NULL,
                task TEXT NOT NULL, options TEXT NOT NULL, data TEXT NOT NULL,
                claim TEXT, owner TEXT, review_round INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, card_id TEXT NOT NULL, at TEXT NOT NULL,
                actor TEXT NOT NULL, reason TEXT NOT NULL, kind TEXT NOT NULL,
                before_json TEXT NOT NULL, after_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS stage_runs (
                id INTEGER PRIMARY KEY, card_id TEXT NOT NULL, stage TEXT NOT NULL,
                enqueued_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                started REAL, elapsed REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS publication (
                id INTEGER PRIMARY KEY CHECK(id=1), dirty INTEGER NOT NULL DEFAULT 0,
                published INTEGER NOT NULL DEFAULT 0, last_attempt REAL NOT NULL DEFAULT 0,
                error TEXT);
            INSERT OR IGNORE INTO publication(id) VALUES(1);
            CREATE TABLE IF NOT EXISTS imports (source TEXT PRIMARY KEY, card_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS desk_quota_stops (
                desk TEXT PRIMARY KEY, marked_at TEXT NOT NULL);
        ''')

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def transaction(self):
        with self.db:
            self.db.execute('BEGIN' if self.read_only else 'BEGIN IMMEDIATE')
            yield

    def _item(self, card):
        row = self.db.execute('SELECT * FROM items WHERE card_id=?', (card,)).fetchone()
        if row is None:
            raise ValueError(f'unknown card: {card}')
        return dict(row)

    def _event(self, card, before, after, actor, reason, kind):
        if not actor.strip() or not reason.strip():
            raise ValueError('actor and reason are required')
        cursor = self.db.execute('''INSERT INTO events
            (card_id,at,actor,reason,kind,before_json,after_json) VALUES(?,?,?,?,?,?,?)''',
            (card, timestamp(), actor, reason, kind, json.dumps(before), json.dumps(after)))
        if kind != 'import':
            self.db.execute('UPDATE publication SET dirty=? WHERE id=1', (cursor.lastrowid,))

    @staticmethod
    def _state(row):
        return {k: row[k] for k in ('position', 'stage', 'desk', 'ready')}

    def _tail(self):
        return self.db.execute('SELECT COALESCE(MAX(position),0)+1 FROM items').fetchone()[0]

    def enqueue(self, card, *, desk, task, actor, reason='dispatch enqueued', options=None, **fields):
        if not card.strip() or not desk.strip() or not task.strip():
            raise ValueError('card, desk and task must be nonempty')
        self._validate_fields(fields)
        data = dict(wr='', slice='', title='', description='', agent=desk, family='', model='',
                    effort='', prs=[], ci_state='unknown', findings=[])
        data.update(fields)
        with self.transaction():
            if self.db.execute('SELECT 1 FROM items WHERE card_id=?', (card,)).fetchone():
                raise ValueError('card already exists; use --stage or requeue for another stage')
            self.db.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?,NULL,NULL,0)',
                            (card, self._tail(), 'queued', desk, 1, task,
                             json.dumps(options or {}), json.dumps(data)))
            self.db.execute('INSERT INTO stage_runs(card_id,stage,enqueued_at) VALUES(?,?,?)',
                            (card, 'queued', timestamp()))
            self._event(card, {}, self._state(self._item(card)), actor, reason, 'enqueue')
        return self.get(card)

    def prioritize(self, card, *, before=None, actor, reason='explicit priority'):
        with self.transaction():
            old = self._item(card)
            order = [r[0] for r in self.db.execute('SELECT card_id FROM items ORDER BY position')
                     if r[0] != card]
            index = order.index(before) if before is not None else 0
            order.insert(index, card)
            for position, key in enumerate(order, 1):
                self.db.execute('UPDATE items SET position=? WHERE card_id=?', (position, key))
            self._event(card, self._state(old), self._state(self._item(card)), actor, reason, 'prioritize')
        return self.get(card)

    def get(self, card):
        self._item(card)
        return next(i for i in self.export()['items'] if i['card_id'] == card)

    def export(self):
        with self.transaction():
            history = [dict(id=r['id'], card_id=r['card_id'], at=r['at'], actor=r['actor'],
                            reason=r['reason'], kind=r['kind'], **{'from': json.loads(r['before_json']),
                                                               'to': json.loads(r['after_json'])})
                       for r in self.db.execute('SELECT * FROM events ORDER BY id')]
            items = []
            for r in self.db.execute('SELECT * FROM items ORDER BY position'):
                data = json.loads(r['data'])
                result = data.pop('last_result', {})
                item = {**data, **{k: r[k] for k in
                        ('card_id', 'position', 'stage', 'desk', 'ready', 'review_round')}}
                item['result_status'] = result.get('status')
                item['checkout_cleanup'] = result.get('checkout_cleanup')
                item['required_skills'] = json.loads(r['options']).get('required_skills', [])
                answer = str(result.get('result') or '').strip().splitlines()
                question = answer[0] if answer and answer[0].startswith('QUESTION:') else ''
                cleanup = result.get('checkout_cleanup') or {}
                item['result_detail'] = question or str(result.get('detail') or cleanup.get('detail') or '')[:500]
                item['dispatch_ref'] = result.get('msg_id')
                item['thread_id'] = result.get('thread_id')
                item['claim'] = r['claim']
                item['ready'] = bool(item['ready'])
                item['active'] = r['claim'] is not None
                item['stages'] = [dict(s) for s in self.db.execute(
                    'SELECT stage,enqueued_at,started_at,finished_at,elapsed FROM stage_runs WHERE card_id=? ORDER BY id',
                    (r['card_id'],))]
                item['total_time_s'] = sum(s['elapsed'] for s in item['stages'])
                running = self.db.execute('SELECT started FROM stage_runs WHERE card_id=? AND started IS NOT NULL AND finished_at IS NULL', (r['card_id'],)).fetchall()
                item['total_time_s'] += sum(max(0, time.time() - s[0]) for s in running)
                item['findings_opened'] = len(item['findings'])
                item['findings_resolved'] = sum(f['resolved'] for f in item['findings'])
                item['last_event'] = next((e for e in reversed(history) if e['card_id'] == r['card_id']), None)
                item['merge_position'] = None
                items.append(item)
            merging = [i for i in items if i['stage'] == 'merging']
            for pos, item in enumerate(merging, 1):
                item['merge_position'] = pos
            return {'schema': 'room-work-queue/v1', 'revision': history[-1]['id'] if history else 0,
                    'items': items, 'history': history, 'publication': self.publication()}

    def _end_stage(self, card):
        run = self.db.execute('SELECT * FROM stage_runs WHERE card_id=? ORDER BY id DESC LIMIT 1', (card,)).fetchone()
        if run and not run['finished_at']:
            elapsed = max(0, time.time() - run['started']) if run['started'] is not None else 0
            self.db.execute('UPDATE stage_runs SET finished_at=?,elapsed=? WHERE id=?',
                            (timestamp(), elapsed, run['id']))


    def _stage(self, card, stage, ready):
        self._end_stage(card)
        self.db.execute('UPDATE items SET stage=?,ready=? WHERE card_id=?', (stage, ready, card))
        at = timestamp()
        self.db.execute('INSERT INTO stage_runs(card_id,stage,enqueued_at,finished_at) VALUES(?,?,?,?)',
                        (card, stage, at, at if stage in TERMINAL else None))

    def claim(self, *, eligible_desks, actor, owner, card_id=None, expected_candidates=None):
        import uuid
        with self.transaction():
            if expected_candidates is not None and self.candidates() != expected_candidates:
                raise QueueChanged('work order changed before claim')
            busy = {r[0] for r in self.db.execute('SELECT desk FROM items WHERE claim IS NOT NULL')}
            selected = next((dict(r) for r in self.db.execute('SELECT * FROM items WHERE ready=1 AND claim IS NULL ORDER BY position')
                             if r['desk'] in eligible_desks and r['desk'] not in busy
                             and (card_id is None or r['card_id'] == card_id)), None)
            if selected is None:
                return None
            card = selected['card_id']
            if selected['stage'] not in EXECUTABLE:
                return None
            if selected['stage'] == 'review' and selected['review_round'] >= 3:
                self._stage(card, 'blocked', False)
                self._event(card, self._state(selected), self._state(self._item(card)), actor,
                            'three review rounds exhausted', 'stage')
                return None
            if selected['stage'] == 'queued':
                self._stage(card, 'building', False)
            self.db.execute('UPDATE stage_runs SET started_at=?,started=? WHERE id=(SELECT MAX(id) FROM stage_runs WHERE card_id=?)',
                            (timestamp(), time.time(), card))
            token = str(uuid.uuid4())
            data = json.loads(selected['data'])
            data.pop('last_result', None)
            self.db.execute('UPDATE items SET data=? WHERE card_id=?', (json.dumps(data), card))
            self.db.execute('UPDATE items SET claim=?,owner=?,ready=0,review_round=review_round+? WHERE card_id=?',
                            (token, json.dumps(owner), int(selected['stage'] == 'review'), card))
            self._event(card, self._state(selected), self._state(self._item(card)), actor, 'executor claimed', 'claim')
            return self._item(card)

    def execution(self, card, token):
        row = self._item(card)
        if row['claim'] != token:
            raise ValueError('stale executor claim')
        return row

    def attach(self, card, token, owner):
        with self.transaction():
            self.execution(card, token)
            self.db.execute('UPDATE items SET owner=? WHERE card_id=?', (json.dumps(owner), card))

    def finish(self, card, token, result, *, actor):
        with self.transaction():
            old = self._item(card)
            if not token or old['claim'] != token:
                raise ValueError('stale executor claim')
            data = json.loads(old['data'])
            data.update({k: result[k] for k in ('model', 'effort', 'family') if result.get(k)})
            data['last_result'] = result
            status = result.get('status')
            if status == 'quota_exhausted':
                self.db.execute('INSERT INTO desk_quota_stops(desk,marked_at) VALUES(?,?) '
                                'ON CONFLICT(desk) DO UPDATE SET marked_at=excluded.marked_at',
                                (old['desk'], timestamp()))
            elif status == 'completed':
                self.db.execute('DELETE FROM desk_quota_stops WHERE desk=?', (old['desk'],))
            # Delivered turns still own their desk. Their session must report completion.
            if status in ('delivered', 'delivered_live', 'backgrounded'):
                self.db.execute('UPDATE items SET data=? WHERE card_id=?', (json.dumps(data), card))
                self._event(card, self._state(old), self._state(old), actor, 'awaiting desk completion receipt', 'delivered')
                return
            target = 'review' if status == 'completed' else 'failed'
            if result.get('termination_confirmed') is False:
                target = 'blocked'
            self._stage(card, target, False)
            if target == 'blocked':
                self.db.execute('UPDATE items SET data=? WHERE card_id=?', (json.dumps(data), card))
            else:
                self.db.execute('UPDATE items SET claim=NULL,owner=NULL,data=? WHERE card_id=?', (json.dumps(data), card))
            self._event(card, self._state(old), self._state(self._item(card)), actor,
                        f'executor {status}', 'stage')


    def transition(self, card, stage, *, actor, reason):
        allowed = {'queued': {'blocked', 'parked', 'failed'},
                   'building': {'review', 'blocked', 'failed', 'parked'},
                   'review': {'fixing', 'merging', 'blocked', 'failed', 'parked'},
                   'fixing': {'review', 'blocked', 'failed', 'parked'},
                   'merging': {'done', 'blocked', 'failed', 'parked'}}
        with self.transaction():
            old = self._item(card)
            if old['claim']:
                raise ValueError('active executor requires a completion receipt')
            if stage not in allowed.get(old['stage'], set()):
                raise ValueError(f'invalid transition: {old["stage"]} -> {stage}; use requeue to resume')
            if stage == 'merging':
                data = json.loads(old['data'])
                self._validate_merge(data)
            self._stage(card, stage, False)
            self._event(card, self._state(old), self._state(self._item(card)), actor, reason, 'stage')
        return self.get(card)

    def requeue(self, card, *, stage, actor, reason, task=None, options=None, desk=None):
        if stage not in EXECUTABLE:
            raise ValueError('requeue stage must be queued, building, review or fixing')
        with self.transaction():
            old = self._item(card)
            if old['claim']:
                raise ValueError('active executor requires a completion receipt')
            if stage == 'review' and old['review_round'] >= 3:
                raise ValueError('three review rounds exhausted')
            if task is None:
                data = json.loads(old['data'])
                findings = [f for f in data['findings'] if not f['resolved']]
                task = (f'Work queue stage {stage} for {card}.\n'
                        f'PRs: {json.dumps(data["prs"])}\nOpen findings: {json.dumps(findings)}\n\n'
                        + old['task'])
            self._stage(card, stage, True)
            self.db.execute('UPDATE items SET position=?,desk=?,task=?,options=? WHERE card_id=?',
                            (self._tail(), desk or old['desk'], task or old['task'],
                             json.dumps(options) if options is not None else old['options'], card))
            order = [r[0] for r in self.db.execute('SELECT card_id FROM items ORDER BY position')]
            for position, key in enumerate(order, 1):
                self.db.execute('UPDATE items SET position=? WHERE card_id=?', (position, key))
            self._event(card, self._state(old), self._state(self._item(card)), actor, reason, 'requeue')
            self.db.execute('DELETE FROM desk_quota_stops WHERE desk=?', (desk or old['desk'],))
        return self.get(card)


    @staticmethod
    def _validate_fields(fields):
        if fields.keys() - FIELDS:
            raise ValueError('unknown metadata fields')
        for key in fields.keys() - {'prs', 'findings'}:
            if not isinstance(fields[key], str):
                raise ValueError(f'{key} must be a string')
        for key in ('findings', 'prs'):
            if key in fields and (not isinstance(fields[key], list) or any(not isinstance(v, dict) for v in fields[key])):
                raise ValueError(f'{key} must be a list of objects')
        for finding in fields.get('findings', []):
            if (not finding.get('id') or finding.get('level') not in ('P0', 'P1', 'P2', 'P3')
                    or not finding.get('summary') or type(finding.get('resolved')) is not bool):
                raise ValueError('findings require id, P0-P3 level, summary and resolved boolean')
        for pr in fields.get('prs', []):
            if not pr.get('repo') or type(pr.get('number')) is not int or pr['number'] <= 0:
                raise ValueError('PR requires repo and positive number')

    def update(self, card, *, actor, reason, **fields):
        self._validate_fields(fields)
        with self.transaction():
            row = self._item(card)
            data = json.loads(row['data'])
            before = {k: data[k] for k in fields}
            if 'findings' in fields:
                findings = {f['id']: f for f in data['findings']}
                findings.update({f['id']: f for f in fields['findings']})
                fields['findings'] = list(findings.values())
            data.update(fields)
            if row['stage'] == 'merging':
                self._validate_merge(data)
            self.db.execute('UPDATE items SET data=? WHERE card_id=?', (json.dumps(data), card))
            self._event(card, before, fields, actor, reason, 'details')
        return self.get(card)

    @staticmethod
    def _validate_merge(data):
        if not data['prs'] or any(not re.fullmatch(r'[0-9a-f]{40}', pr.get('head') or '') for pr in data['prs']):
            raise ValueError('merge stage requires PRs with pinned 40-character heads')


    def publication(self):
        return dict(self.db.execute('SELECT * FROM publication WHERE id=1').fetchone())

    def publish(self, publish_board, *, now=None):
        now = time.time() if now is None else now
        with self.transaction():
            row = self.publication()
            if row['dirty'] <= row['published'] or (row['last_attempt'] and now - row['last_attempt'] < 2):
                return False
            revision = row['dirty']
            self.db.execute('UPDATE publication SET last_attempt=? WHERE id=1', (now,))
        try:
            publish_board()
        except Exception as exc:
            with self.transaction():
                self.db.execute('UPDATE publication SET error=? WHERE id=1', (str(exc)[:500],))
            return False
        with self.transaction():
            self.db.execute('UPDATE publication SET published=?,error=NULL WHERE id=1', (revision,))
        return True

    def import_results(self, path, *, actor):
        import hashlib
        imported, rejected = 0, []
        with Path(path).open() as source:
            for line_number, line in enumerate(source, 1):
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict) or not row.get('desk') or not row.get('status'):
                        raise ValueError('not a dispatch result')
                except (ValueError, TypeError):
                    rejected.append(line_number)
                    continue
                key = str(row.get('msg_id') or hashlib.sha256(line.encode()).hexdigest())
                card = f'legacy:{key}'
                with self.transaction():
                    if self.db.execute('SELECT 1 FROM imports WHERE source=?', (key,)).fetchone():
                        continue
                    data = dict(wr='', slice='', title='', description='', agent=row['desk'], family=row.get('family', ''),
                                model=row.get('model', ''), effort=row.get('effort', ''), prs=[], ci_state='unknown',
                                findings=[], last_result=row, timing_unknown=True)
                    self.db.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?,NULL,NULL,0)',
                                    (card, self._tail(), 'done', row['desk'], 0, row.get('task', ''), '{}', json.dumps(data)))
                    self.db.execute('INSERT INTO stage_runs(card_id,stage,enqueued_at,started_at,finished_at) VALUES(?,?,?,?,?)',
                                    (card, 'done', row.get('dispatched_at') or timestamp(), row.get('dispatched_at'), row.get('finished_at')))
                    self.db.execute('INSERT INTO imports VALUES(?,?)', (key, card))
                    self._event(card, {}, self._state(self._item(card)), actor, 'historical dispatch result; timing may be unknown', 'import')
                    imported += 1
        return {'imported': imported, 'rejected_lines': rejected}

    def candidates(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM items WHERE ready=1 AND claim IS NULL ORDER BY position')]

    def quota_exhausted_desks(self):
        return {row['desk']: row['marked_at'] for row in
                self.db.execute('SELECT desk,marked_at FROM desk_quota_stops')}

    def active(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM items WHERE claim IS NOT NULL ORDER BY position')]

    def recover(self, terminated, *, actor):
        blocked = []
        with self.transaction():
            for row in self.active():
                result = json.loads(row['data']).get('last_result', {})
                if result.get('status') in ('delivered', 'delivered_live', 'backgrounded'):
                    continue
                if row['stage'] != 'blocked' and terminated(json.loads(row['owner'])):
                    self._stage(row['card_id'], 'blocked', False)
                    self._event(row['card_id'], self._state(row), self._state(self._item(row['card_id'])),
                                actor, 'executor owner terminated; reconcile effects before completion or retry', 'interrupted')
                    blocked.append(row['card_id'])
        return blocked


def board_projection(path=None):
    """Optional local projection; absence leaves existing boards unchanged."""
    if path is None:
        path = default_path()
    if not Path(path).exists():
        return None
    with WorkQueue(path, read_only=True) as queue:
        snapshot = queue.export()
    finished = [i for i in snapshot['items'] if i['stage'] in TERMINAL]
    retained = {i['card_id'] for i in finished[-20:]}
    snapshot['items'] = [i for i in snapshot['items'] if i['stage'] not in TERMINAL or i['card_id'] in retained]
    history_count = len(snapshot['history'])
    snapshot['history'] = snapshot['history'][-100:]
    def clean(value):
        if not isinstance(value, str):
            return value
        if re.search(r'@|/(?:Users|home|private|tmp)/|(?:token|secret|password|authorization)\s*[:=]|[a-f0-9]{32,}', value, re.I):
            return 'Details withheld'
        return value.splitlines()[0][:220] if value else ''
    event_fields = {'id', 'card_id', 'at', 'actor', 'reason', 'kind'}
    def event(row):
        if not row:
            return None
        return {key: clean(value) for key, value in row.items() if key in event_fields}
    fields = {'card_id', 'position', 'stage', 'desk', 'ready', 'review_round', 'wr', 'slice',
              'title', 'agent', 'family', 'model', 'effort', 'ci_state', 'active',
              'total_time_s', 'findings_opened', 'findings_resolved', 'merge_position', 'timing_unknown'}
    for item in snapshot['items']:
        projected = {key: clean(value) for key, value in item.items() if key in fields}
        detail = str(item.get('result_detail') or '')
        if detail.startswith('QUESTION:'):
            projected['result_detail'] = clean(detail)
        elif (item.get('checkout_cleanup') or {}).get('detail'):
            projected['result_detail'] = clean(item['checkout_cleanup']['detail'])
        projected['checkout_cleanup'] = {key: clean(value)
            for key, value in (item.get('checkout_cleanup') or {}).items() if key in ('status', 'detail')}
        projected['prs'] = [{key: ref[key] for key in ('repo', 'number', 'head') if key in ref}
                            for ref in item.get('prs', []) if isinstance(ref, dict)
                            and re.fullmatch(r'(DoctorCRE/carr-system|jbookout/(doctorcre-app|software-factory))', str(ref.get('repo', '')))]
        projected['merge_complete'] = bool(item.get('prs')) and item['stage'] == 'done'
        projected['last_event'] = event(item.get('last_event'))
        projected['stages'] = item.get('stages', [])
        projected['findings'] = [{key: clean(finding[key]) for key in ('id','level','summary','resolved') if key in finding}
                                 for finding in item.get('findings', [])]
        item.clear()
        item.update(projected)
    snapshot['history'] = [event(row) for row in snapshot['history']]
    snapshot['publication'] = {key: value for key, value in snapshot.get('publication', {}).items() if key != 'error'}
    snapshot['omitted'] = {'finished': max(0, len(finished) - 20), 'events': max(0, history_count - 100)}
    return snapshot
