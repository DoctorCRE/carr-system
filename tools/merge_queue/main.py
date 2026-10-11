#!/usr/bin/env python3
"""Durable per-repository FIFO queues. Run, enqueue and inspect here."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import runpy
import signal
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ops'))
sys.path.insert(0, str(ROOT))
from git_env import scrubbed_env
from lib.secret_redaction import redacted_tail, sensitive_env_values
from lib import dot_review_receipts
from lib.github_rate_limit import GitHubReadBudget, GitHubReadPaused, GitHubBudgetLockTimeout, resource_for, split_response
REPOS = ('jbookout/carr-system', 'jbookout/doctorcre-app', 'jbookout/software-factory')
HOLD = 'do_not_merge'
SHA = re.compile(r'^[0-9a-f]{40}$')
REVIEW = runpy.run_path(str(ROOT / 'ops/release-pipeline.py'))
REVIEW_CONFIG = json.loads((ROOT / 'ops/config/release-pipeline.v1.json').read_text())
WAIT_REGISTRY = json.loads(Path(__file__).with_name('wait_registry.json').read_text())
BOUNDS = WAIT_REGISTRY['bounds']
WAIT_SITES = WAIT_REGISTRY['sites']
MAX_ATTEMPTS = BOUNDS['retry']['attempts']
AUTO_ENQUEUE_CAP = BOUNDS['auto_enqueue']['attempts']
CI_WAIT_SECONDS = BOUNDS['ci']['seconds']


class WaitExpired(RuntimeError):
    pass


def bounded_items(policy, iterable):
    """Every finite source loop has an iteration cap from the registry."""
    limit = BOUNDS[policy]['attempts']
    for index, item in enumerate(iterable):
        if index >= limit:
            raise WaitExpired(f'{policy} exceeded {limit} iterations; reconcile before retry')
        yield item


class CommandFailed(RuntimeError):
    pass


class MergeRejected(RuntimeError):
    pass


class ActionRejected(RuntimeError):
    pass


class ReadRejected(RuntimeError):
    pass


class ActionUncertain(RuntimeError):
    pass


class ActionExhausted(RuntimeError):
    pass


class GitHubExhausted(RuntimeError):
    pass


class Cancelled(Exception):
    pass


def gh_api_read(argv):
    method, fields, body = None, [], False
    for index, arg in bounded_items('snapshot', enumerate(argv[3:], 3)):
        if arg in ('-X', '--method'):
            method = argv[index + 1]
        elif arg.startswith('--method='):
            method = arg.split('=', 1)[1]
        elif arg.startswith('-X'):
            method = arg[2:]
        elif arg in ('-f', '-F', '--field', '--raw-field'):
            fields.append(argv[index + 1])
        elif arg.startswith(('--field=', '--raw-field=')):
            fields.append(arg.split('=', 1)[1])
        elif arg.startswith(('-f', '-F')):
            fields.append(arg[2:])
        elif arg == '--input' or arg.startswith('--input='):
            body = True
    if argv[2] == 'graphql':
        return any(re.match(r'query=\s*(?:query\b|\{)', field) for field in bounded_items('snapshot', fields))
    return (method or ('POST' if fields or body else 'GET')).upper() in ('GET', 'HEAD')


def command(argv, *, cwd=None, data=None, timeout=None, observe=None, diagnostics=False):
    if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
        raise ValueError('transport timeout must be positive and finite')
    timeout = min(timeout or BOUNDS['command']['seconds'], BOUNDS['command']['seconds'])
    try:
        p = subprocess.run(argv, cwd=cwd, input=data, text=True, capture_output=True,
                           env=scrubbed_env() if argv[0] == 'git' else None,
                           stdin=None if data is not None else subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        detail = f'{Path(argv[0]).name} transport deadline expired; reconcile before retry'
        if diagnostics:
            stderr = exc.stderr.decode(errors='replace') if isinstance(exc.stderr, bytes) else exc.stderr
            detail += '; stderr tail: ' + (redacted_tail(stderr, known_secrets=sensitive_env_values(os.environ)).strip() or '(empty)')
        raise WaitExpired(detail) from None
    if observe is not None:
        observe(p)
    if p.returncode:
        if argv[:3] == ['gh', 'pr', 'merge'] and 'GraphQL:' in p.stderr and any(
                phrase in p.stderr.lower() for phrase in bounded_items('snapshot', ('pull request is not mergeable',
                                                         'required status check',
                                                         'base branch policy prohibits'))):
            raise MergeRejected('GitHub rejected the merge; verify provider state before retry')
        status = re.search(r'HTTP (4[0-9]{2})', p.stderr)
        read = (argv[:2] == ['gh', 'api'] and gh_api_read(argv)) or argv[:3] == ['gh', 'pr', 'checks']
        if argv[0] == 'gh' and status and read:
            raise ReadRejected(f'GitHub read rejected (HTTP {status[1]}); reread before retry')
        if argv[0] == 'gh' and status:
            raise ActionRejected(f'GitHub rejected the action (HTTP {status[1]}); reread before retry')
        detail = f'{Path(argv[0]).name} {argv[1]} failed (exit {p.returncode})'
        if diagnostics:
            detail += '; stderr tail: ' + (redacted_tail(p.stderr, known_secrets=sensitive_env_values(os.environ)).strip() or '(empty)')
            raise CommandFailed(detail)
        raise RuntimeError(detail)
    return p.stdout


class Queue:
    def __init__(self, state: Path, root: Path = ROOT, gap: float = 2.0):
        self.state, self.root, self.gap = state, root, gap
        state.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(state / 'queue.sqlite3', timeout=BOUNDS['sqlite']['seconds'])
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY, repo TEXT NOT NULL, pr INTEGER NOT NULL,
                approved TEXT NOT NULL, note TEXT NOT NULL, phase TEXT NOT NULL DEFAULT 'pending',
                tested TEXT, outcome TEXT, UNIQUE(repo,pr,approved));
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, entry_id INTEGER, repo TEXT, pr INTEGER,
                outcome TEXT NOT NULL, detail TEXT NOT NULL, logged INTEGER DEFAULT 0,
                published INTEGER DEFAULT 0, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS actions (
                key TEXT PRIMARY KEY, kind TEXT NOT NULL, repo TEXT NOT NULL, pr INTEGER NOT NULL,
                head TEXT NOT NULL, payload TEXT NOT NULL, phase TEXT NOT NULL DEFAULT 'planned', desk TEXT);
            CREATE TABLE IF NOT EXISTS github_failures (
                scope TEXT NOT NULL, key TEXT NOT NULL, is_read INTEGER NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL, entry_id INTEGER,
                PRIMARY KEY(scope,key));
            CREATE TABLE IF NOT EXISTS waits (
                owner TEXT NOT NULL, kind TEXT NOT NULL, deadline REAL NOT NULL,
                PRIMARY KEY(owner,kind));
            CREATE TABLE IF NOT EXISTS service_stops (key TEXT PRIMARY KEY, reason TEXT NOT NULL);
        ''')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            additions = {
                'entries': {'merge_attempts': 'INTEGER NOT NULL DEFAULT 0',
                            'transient_attempts': 'INTEGER NOT NULL DEFAULT 0',
                            'read_attempts': 'INTEGER NOT NULL DEFAULT 0',
                            'github_attempts': 'INTEGER NOT NULL DEFAULT 0',
                            'auto_attempts': 'INTEGER NOT NULL DEFAULT 0',
                            'ci_since': 'REAL', 'ci_head': 'TEXT'},
                'actions': {'attempts': 'INTEGER NOT NULL DEFAULT 0', 'expected_base': 'TEXT',
                            'dispatcher_pid': 'INTEGER', 'dispatcher_identity': 'TEXT', 'receipt': 'TEXT'},
                'github_failures': {'stopped': 'INTEGER NOT NULL DEFAULT 0'}}
            for table, columns in bounded_items('snapshot', additions.items()):
                existing = {r['name'] for r in bounded_items('snapshot', self.db.execute(f'PRAGMA table_info({table})'))}
                for name, definition in bounded_items('snapshot', columns.items()):
                    if name not in existing:
                        self.db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
        self.last_gh = 0.0
        self.budget = GitHubReadBudget(spacing=gap, lock_timeout=BOUNDS['budget_lock']['seconds'],
                                      cancel=self.check_cancelled)
        self.stopped = False
        self._lock_depth = 0
        self._next_page = None
        self._github_scope = 'automatic'
        self._github_entry = None
        self.children: dict[str, subprocess.Popen] = {}
        self._repo_ready: set[str] = set()

    @contextmanager
    def runner_lock(self):
        if self._lock_depth:
            yield
            return
        with (self.state / 'agent.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_depth = 1
            try:
                yield
            finally:
                self._lock_depth = 0

    def check_cancelled(self):
        if self.stopped:
            raise Cancelled()

    def deadline(self, owner, kind):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO waits(owner,kind,deadline) VALUES(?,?,?)',
                            (owner, kind, time.time() + BOUNDS[kind]['seconds']))
        return self.db.execute('SELECT deadline FROM waits WHERE owner=? AND kind=?', (owner, kind)).fetchone()[0]

    def wait(self, e, kind, outcome, detail):
        """A successful-but-unfinished observation cannot replenish its deadline."""
        deadline = self.deadline(f'entry:{e["id"]}', kind)
        if time.time() >= deadline:
            reason = f'{detail}; {kind} deadline expired ({BOUNDS[kind]["seconds"]} seconds)'
            if kind == 'provider_merge' or e['tested'] is not None:
                reason += '; merge intent retained; reconcile provider state before retry'
            self.report(e, outcome + '_timeout', reason, 'blocked')
            return False
        self.report(e, outcome, detail)
        return True

    def expire_dispatch(self, a):
        kind = 'dispatch_process' if a['phase'] == 'issued' else 'dispatch_desk'
        if time.time() < self.deadline(f'action:{a["key"]}', kind):
            return False
        with self.db:
            self.db.execute("UPDATE actions SET phase='uncertain',desk=NULL WHERE key=?", (a['key'],))
            reason = f'{kind} deadline expired; inspect dispatcher and receipt before retry; reservation released'
            self.event(a['repo'], a['pr'], 'dispatch_timeout', reason)
            self.db.execute("UPDATE entries SET phase='blocked',outcome='dispatch_timeout' WHERE repo=? AND pr=? AND phase='conflict'",
                            (a['repo'], a['pr']))
        child = self.children.pop(a['key'], None)
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=BOUNDS['child_reap']['seconds'])
            except subprocess.TimeoutExpired:
                child.kill()
                try:
                    child.wait(timeout=BOUNDS['child_reap']['seconds'])
                except subprocess.TimeoutExpired:
                    with self.db:
                        self.event(a['repo'], a['pr'], 'dispatch_reap_timeout', 'Dispatcher kill sent; reap deadline expired; inspect process before using desk')
        return True

    @contextmanager
    def github_scope(self, scope):
        old = self._github_scope, self._github_entry
        self._github_scope, self._github_entry = scope, None
        try:
            yield
        finally:
            self._github_scope, self._github_entry = old

    def gh(self, *args, decode=False, validate=None):
        operation = list(args)
        for index, arg in bounded_items('snapshot', enumerate(operation)):
            if arg.startswith(('body=', 'expected_head_sha=')):
                operation[index] = arg.split('=', 1)[0] + '='
            elif index and operation[index - 1] == '--match-head-commit':
                operation[index] = '<head>'
        read = gh_api_read(['gh', *args]) if args[0] == 'api' else args[:2] == ('pr', 'checks')
        return self.bounded_operation(operation, read, lambda: self._gh_request(args, decode, validate),
                                      'GitHub command or response failed')

    def bounded_operation(self, operation, read, request, detail, *, entry_budget=True, scope=None):
        self.check_cancelled()
        scope = self._github_scope if scope is None else scope
        entry = self._github_entry if entry_budget else None
        key = hashlib.sha256(json.dumps(operation, separators=(',', ':')).encode()).hexdigest()
        stopped = self.db.execute('SELECT detail FROM github_failures WHERE scope=? AND key=? AND stopped=1',
                                  (f'{scope}:entry:{entry}', key)).fetchone() if entry is not None else None
        if stopped:
            raise WaitExpired(f'{stopped["detail"]}; stopped; reconcile before retry')
        failure = self.db.execute('SELECT * FROM github_failures WHERE scope=? AND key=?',
                                  (scope, key)).fetchone()
        if failure and (failure['stopped'] or failure['attempts'] >= MAX_ATTEMPTS):
            if failure['stopped']:
                raise WaitExpired(f'{failure["detail"]}; stopped; reconcile before retry')
            raise GitHubExhausted(f'{failure["detail"]}; exhausted {MAX_ATTEMPTS} attempts')
        if entry is not None:
            attempts = self.db.execute('SELECT github_attempts FROM entries WHERE id=?', (entry,)).fetchone()[0]
            if attempts >= MAX_ATTEMPTS:
                raise GitHubExhausted(f'Entry exhausted {MAX_ATTEMPTS} external operation attempts')
        try:
            return request()
        except GitHubReadPaused:
            raise
        except (RuntimeError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
            if isinstance(exc, (GitHubBudgetLockTimeout, subprocess.TimeoutExpired)):
                exc = WaitExpired('External transport or lock deadline expired; intent retained; reconcile before retry')
            if isinstance(exc, (ReadRejected, ActionRejected, MergeRejected, CommandFailed)):
                detail = str(exc)
            if isinstance(exc, WaitExpired):
                detail = str(exc)
            with self.db:
                self.db.execute('INSERT INTO github_failures(scope,key,is_read,attempts,detail,entry_id) VALUES(?,?,?,1,?,?) '
                                'ON CONFLICT(scope,key) DO UPDATE SET attempts=attempts+1,detail=excluded.detail,entry_id=COALESCE(github_failures.entry_id,excluded.entry_id)',
                                (scope, key, int(read), detail, entry))
                if entry is not None:
                    self.db.execute('UPDATE entries SET github_attempts=github_attempts+1 WHERE id=?', (entry,))
                if isinstance(exc, WaitExpired):
                    if entry is None:
                        self.db.execute('UPDATE github_failures SET stopped=1 WHERE scope=? AND key=?', (scope, key))
                    else:
                        self.db.execute('INSERT INTO github_failures(scope,key,is_read,attempts,detail,entry_id,stopped) VALUES(?,?,?,0,?,?,1) '
                                        'ON CONFLICT(scope,key) DO UPDATE SET stopped=1,detail=excluded.detail',
                                        (f'{scope}:entry:{entry}', key, int(read), detail, entry))
                attempts = self.db.execute('SELECT attempts FROM github_failures WHERE scope=? AND key=?',
                                           (scope, key)).fetchone()[0]
                if entry is not None:
                    attempts = max(attempts, self.db.execute('SELECT github_attempts FROM entries WHERE id=?', (entry,)).fetchone()[0])
            if isinstance(exc, (KeyError, TypeError, ValueError)):
                exc = RuntimeError(detail)
            exc.github_attempts = attempts
            raise exc

    def _gh_request(self, args, decode, validate):
        with self.budget.call_slot(timeout=BOUNDS['pacing_lock']['seconds']) as mark_started:
            read = gh_api_read(['gh', *args]) if args[0] == 'api' else args[:2] == ('pr', 'checks')
            resource = resource_for(list(args))
            slot, reserved_delay = self.budget.reserve(resource, with_slot=True)
            clock = self.budget.clock
            slot_deadline = time.monotonic() + reserved_delay
            end = time.monotonic() + BOUNDS['spacing']['seconds']
            delay = max(slot - clock(), slot_deadline - time.monotonic(), self.last_gh + self.gap - time.monotonic())
            if delay > BOUNDS['spacing']['seconds']:
                raise WaitExpired('Shared pacing reservation exceeds spacing deadline; entry stopped')
            while time.monotonic() < end:
                self.check_cancelled()
                if clock() < slot or time.monotonic() < max(slot_deadline, self.last_gh + self.gap):
                    time.sleep(min(.2, max(0, end - time.monotonic())))
                    continue
                self.budget.check(resource)
                self.check_cancelled()
                if time.monotonic() >= end:
                    raise WaitExpired('Shared pacing wait exceeded spacing deadline; entry stopped')
                if clock() >= slot and time.monotonic() >= max(slot_deadline, self.last_gh + self.gap):
                    break
            else:
                raise WaitExpired('Shared pacing wait exceeded spacing deadline; entry stopped')
            include = args[0] == 'api' and '--include' not in args
            observed_at = time.time()
            def observe(p):
                headers, _ = split_response(p.stdout)
                self.budget.observe(resource, headers, p.stderr, observed_at)
            try:
                mark_started()
                try:
                    out = command(['gh', *args, *(['--include'] if include else [])], cwd=self.root, observe=observe)
                except Cancelled:
                    if read:
                        raise
                    raise ActionUncertain('Cancellation during response observation; mutation intent retained') from None
                headers, body = split_response(out)
                self._next_page = bool(re.search(r';\s*rel="next"', headers.get('link', ''))) if out.startswith('HTTP/') else None
                result = body if include else out
                if decode:
                    result = json.loads(result)
                    if args[:2] == ('api', 'graphql') and (not isinstance(result, dict) or result.get('errors')):
                        raise RuntimeError('GitHub GraphQL response contains errors')
                    if validate is not None:
                        validate(result)
                return result
            finally:
                self.last_gh = time.monotonic()

    def api(self, path, *args, validate=None):
        return self.gh('api', path, *args, decode=True, validate=validate)

    def api_pages(self, path, collection=None):
        pages = []
        for page in bounded_items('pages', range(1, BOUNDS['pages']['attempts'] + 1)):
            separator = '&' if '?' in path else '?'
            def validate(data):
                rows = data[collection] if collection else data
                if not isinstance(rows, list):
                    raise ValueError('GitHub did not return a page list')
                for row in bounded_items('snapshot', rows):
                    if not isinstance(row, dict):
                        raise ValueError('GitHub returned an invalid page row')
                    if '/pulls?' in path:
                        self.validate_pr(row, summary=True)
                        if not isinstance(row['title'], str):
                            raise ValueError('GitHub returned an invalid PR title')
                    elif '/comments?' in path:
                        if not isinstance(row['body'], str):
                            raise ValueError('GitHub returned an invalid comment')
                    elif '/check-runs?' in path:
                        if not isinstance(row['id'], int) or not isinstance(row['name'], str):
                            raise ValueError('GitHub returned an invalid check run')
                        if not isinstance(row['status'], str) or row['conclusion'] is not None and not isinstance(row['conclusion'], str):
                            raise ValueError('GitHub returned an invalid check run')
                        app = row.get('app', {})
                        if not isinstance(app, dict) or app.get('id') is not None and not isinstance(app['id'], int):
                            raise ValueError('GitHub returned an invalid check run')
                    elif '/statuses?' in path:
                        if not isinstance(row['context'], str) or not isinstance(row['state'], str):
                            raise ValueError('GitHub returned an invalid status')
                    elif '/files?' in path:
                        if not isinstance(row['filename'], str):
                            raise ValueError('GitHub returned an invalid filename')
                if page == BOUNDS['pages']['attempts'] and (self._next_page is True or self._next_page is None and len(rows) >= 100):
                    raise WaitExpired('GitHub pagination limit reached; refusing partial read; reconcile before retry')
            data = self.api(f'{path}{separator}page={page}', validate=validate)
            rows = data[collection] if collection else data
            pages.extend(rows)
            if self._next_page is False or (self._next_page is None and len(rows) < 100):
                return pages

    def pages(self, path):
        return self.api_pages(path)

    def pr(self, repo, n):
        return self.api(f'repos/{repo}/pulls/{n}', validate=self.validate_pr)

    def validate_pr(self, p, summary=False):
        if not isinstance(p, dict) or p.get('state') not in ('open', 'closed'):
            raise ValueError('GitHub did not return a PR')
        if not isinstance(p['number'], int) or not SHA.fullmatch(p['head']['sha']) or not SHA.fullmatch(p['base']['sha']):
            raise ValueError('GitHub returned an incomplete PR')
        if not isinstance(p['head']['ref'], str) or not isinstance(p['base']['ref'], str) or not p['head']['ref'] or not p['base']['ref']:
            raise ValueError('GitHub returned an incomplete PR')
        if not isinstance(p.get('labels', []), list) or any(not isinstance(x['name'], str) for x in bounded_items('snapshot', p.get('labels', []))):
            raise ValueError('GitHub returned invalid PR labels')
        if not summary and not p.get('merged') and not isinstance(p.get('mergeable_state'), str):
            raise ValueError('GitHub returned an incomplete PR')

    def held(self, p):
        return HOLD in {x['name'] for x in bounded_items('snapshot', p.get('labels', []))}

    def enqueue(self, repo, n, approved, note=''):
        if repo not in REPOS or n <= 0 or not SHA.fullmatch(approved):
            raise ValueError('expected authorized repo, positive PR and full approved SHA')
        with self.db:
            cur = self.db.execute('INSERT OR IGNORE INTO entries(repo,pr,approved,note) VALUES(?,?,?,?)',
                                  (repo, n, approved, note))
            if cur.rowcount:
                self.event(repo, n, 'queued', f'Approved head {approved}', cur.lastrowid)
        return self.db.execute('SELECT id FROM entries WHERE repo=? AND pr=? AND approved=?',
                               (repo, n, approved)).fetchone()[0]

    def event(self, repo, n, outcome, detail, entry_id=None):
        prev = self.db.execute('SELECT outcome,detail FROM events WHERE repo=? AND pr=? ORDER BY id DESC LIMIT 1', (repo, n)).fetchone()
        if prev and tuple(prev) == (outcome, detail):
            return
        self.db.execute('INSERT INTO events(entry_id,repo,pr,outcome,detail,created) VALUES(?,?,?,?,?,?)',
                        (entry_id, repo, n, outcome, detail, time.time()))

    def report(self, e, outcome, detail, phase=None):
        with self.db:
            self.db.execute('UPDATE entries SET outcome=? WHERE id=?', (outcome, e['id']))
            if phase is not None:
                self.db.execute('UPDATE entries SET phase=?,outcome=? WHERE id=?', (phase, outcome, e['id']))
            prev = self.db.execute('SELECT outcome,detail FROM events WHERE entry_id=? ORDER BY id DESC LIMIT 1',
                                   (e['id'],)).fetchone()
            if not prev or tuple(prev) != (outcome, detail):
                self.event(e['repo'], e['pr'], outcome, detail, e['id'])

        if outcome == 'fresh_review' and phase == 'review':
            self.request_review(e['repo'], e['pr'])

    def request_review(self, repo, n):
        router = self.root / 'bin/dot-review.py'
        if not router.exists():
            with self.db:
                self.event(repo, n, 'review_handoff_unavailable', 'Install bin/dot-review.py before dispatching review')
            return
        try:
            receipt = command([sys.executable, str(router), '--orch', str(self.root / 'out/orch'),
                               'submit', repo, str(n)], timeout=BOUNDS['command']['seconds'])
            with self.db:
                self.event(repo, n, 'review_handoff', receipt[-2000:])
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            with self.db:
                self.event(repo, n, 'review_handoff_failed', type(exc).__name__ + '; no reviewer dispatch inferred')

    def flush_events(self):
        for ev in bounded_items('snapshot', self.db.execute('SELECT * FROM events WHERE logged=0 ORDER BY id').fetchall()):
            with (self.state / 'queue.log').open('a') as f:
                f.write(json.dumps(dict(ev), sort_keys=True) + '\n')
                f.flush()
                os.fsync(f.fileno())
            with self.db:
                self.db.execute('UPDATE events SET logged=1 WHERE id=?', (ev['id'],))
        for ev in bounded_items('snapshot', self.db.execute('SELECT * FROM events WHERE published=0 ORDER BY id').fetchall()):
            if not ev['published']:
                stage = 'merged' if ev['outcome'] in ('merged', 'already_merged') else ('ci' if ev['outcome'] == 'waiting_ci' else 'review')
                health = 'healthy' if ev['outcome'] in ('queued', 'merged', 'updated', 'retargeted') else 'question'
                card = ('app-pr-' if ev['repo'] == REPOS[1] else
                        'factory-pr-' if ev['repo'] == REPOS[2] else 'pr-') + str(ev['pr'])
                try:
                    self.bounded_operation(['progress-board'], False, lambda: command([sys.executable, str(self.root / 'tools/progress_board.py'), 'task', 'carr-v5', card,
                             '--title', f"{ev['repo'].split('/')[-1]} PR #{ev['pr']}", '--executor', 'Merge queue', '--creation-defaults',
                             '--repo', ev['repo'], '--pr', str(ev['pr']), '--status', 'review', '--stage', stage,
                             '--health', health, '--note', f"Merge queue: {ev['outcome']}. {ev['detail']}"],
                            cwd=self.root, timeout=BOUNDS['board']['seconds'], diagnostics=True), 'Progress board command failed',
                            entry_budget=False, scope='progress-board')
                except (Cancelled, GitHubReadPaused):
                    return
                except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
                    exhausted = isinstance(exc, (GitHubExhausted, WaitExpired)) or getattr(exc, 'github_attempts', 0) >= MAX_ATTEMPTS
                    with (self.state / 'queue.log').open('a') as f:
                        f.write(json.dumps({'event_id': ev['id'],
                                            'outcome': 'progress_board_write_exhausted' if exhausted else 'progress_board_write_failed',
                                            'detail': str(exc),
                                            'retry': 'attempts exhausted; queue continues' if exhausted else 'next poll; queue continues'}) + '\n')
                    break
                with self.db:
                    self.db.execute('UPDATE events SET published=1 WHERE id=?', (ev['id'],))

    def git(self, repo, *args, data=None):
        d = self.state / 'repos' / (repo.split('/')[-1] + '.git')
        if not d.exists():
            d.parent.mkdir(parents=True, exist_ok=True)
            command(['git', 'init', '--bare', str(d)])
        if repo not in self._repo_ready:
            # init can succeed before remote-add times out. Reconcile that partial bootstrap.
            remote = subprocess.run(['git', '-C', str(d), 'remote', 'get-url', 'origin'],
                                    text=True, capture_output=True, env=scrubbed_env(),
                                    timeout=BOUNDS['command']['seconds'])
            if remote.returncode == 2:
                command(['git', '-C', str(d), 'remote', 'add', 'origin', f'https://github.com/{repo}.git'])
            elif remote.returncode != 0:
                raise RuntimeError('Git remote readback failed; bootstrap stopped')
            self._repo_ready.add(repo)
        return command(['git', '-C', str(d), *args], data=data)

    def fetch(self, repo, *heads):
        return self.bounded_operation(['git', 'fetch', repo, *sorted(set(heads))], True,
                                      lambda: self.git(repo, 'fetch', '--quiet', 'origin',
                                                       '+refs/heads/main:refs/remotes/origin/main',
                                                       *dict.fromkeys(heads)), 'Git fetch failed')

    def patch(self, repo, head):
        base = self.git(repo, 'merge-base', 'origin/main', head).strip()
        diff = self.git(repo, 'diff', '--binary', base, head)
        ids = command(['git', 'patch-id', '--stable'], data=diff).split()
        return ids[0] if ids else None

    def covered(self, repo, approved, head):
        if approved == head:
            return True
        if getattr(self, '_dot_reviews', {}).get((repo, approved)):
            return False
        self.fetch(repo, approved, head)
        old = self.patch(repo, approved)
        return bool(old and old == self.patch(repo, head))

    def approval(self, repo, n):
        comments = self.pages(f'repos/{repo}/issues/{n}/comments?per_page=100')
        cfg = REVIEW_CONFIG['app' if repo == REPOS[1] else 'worker']
        last, receipt = dot_review_receipts.deciding(list(bounded_items('snapshot', comments)),
                                                    repo, n, policy=REVIEW, config=cfg,
                                                    api=lambda path: self.pages(path) if '/commits?' in path else self.pr(repo, n))
        if last and REVIEW['verdict'](last.get('body', ''), cfg) == 'approve':
            sha = REVIEW['reviewed_header_sha'](last.get('body', ''))
            if not hasattr(self, '_dot_reviews'):
                self._dot_reviews = {}
            self._dot_reviews[(repo, sha)] = receipt is not None
            return sha
        return None

    def green(self, repo, n, head):
        path = f'repos/{repo}/commits/{head}'
        runs = self.api_pages(path + '/check-runs?per_page=100', 'check_runs')
        statuses = self.pages(path + '/statuses?per_page=100')
        latest = {}
        for run in bounded_items('snapshot', runs):
            key = (run.get('app', {}).get('id'), run['name'])
            if key not in latest or run['id'] > latest[key]['id']:
                latest[key] = run
        contexts = {}
        for s in bounded_items('snapshot', statuses):
            if s['context'] not in contexts:
                contexts[s['context']] = s
        if not latest and not contexts:
            return False
        if any(r['status'] != 'completed' or r['conclusion'] not in ('success', 'skipped', 'neutral')
               for r in bounded_items('snapshot', latest.values())) or any(s['state'] != 'success' for s in bounded_items('snapshot', contexts.values())):
            return False
        def validate(checks):
            if not isinstance(checks, list) or any(not isinstance(c, dict) or 'bucket' not in c for c in bounded_items('snapshot', checks)):
                raise ValueError('GitHub did not return required checks')
        checks = self.gh('pr', 'checks', str(n), '-R', repo, '--required', '--json', 'bucket', decode=True, validate=validate)
        return all(c['bucket'] in ('pass', 'skipping') for c in bounded_items('snapshot', checks))

    def action(self, kind, repo, n, head, payload, expected_base=None):
        key = f'{kind}:{repo}:{n}:{head}:{payload}'
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO actions(key,kind,repo,pr,head,payload,expected_base) VALUES(?,?,?,?,?,?,?)',
                            (key, kind, repo, n, head, payload, expected_base))
        a = self.db.execute('SELECT * FROM actions WHERE key=?', (key,)).fetchone()
        if a['phase'] == 'done':
            return
        if a['phase'] == 'exhausted':
            raise ActionExhausted(f'{kind} exhausted {a["attempts"]} attempts at {head}')
        p = self.pr(repo, n)
        if self.stopped:
            return
        if self.held(p):
            with self.db:
                self.event(repo, n, 'held', f'Skipped {kind}; {HOLD} label present')
            return
        if (kind == 'update' and p['head']['sha'] != head) or (kind == 'retarget' and p['base']['ref'] == payload):
            with self.db:
                self.db.execute("UPDATE actions SET phase='done' WHERE key=?", (key,))
            return
        if a['phase'] == 'issued':
            # A crashed or timed-out mutation may still be processing server-side.
            with self.db:
                self.event(repo, n, 'action_uncertain', f'{kind} at {head}; blocked pending provider readback before retry')
            raise ActionUncertain(f'{kind} at {head}; provider outcome remains uncertain')
        if p['head']['sha'] != head or p['state'] != 'open':
            return
        if kind == 'retarget' and (not a['expected_base'] or p['base']['ref'] != a['expected_base']):
            with self.db:
                self.db.execute("UPDATE actions SET phase='done' WHERE key=?", (key,))
                self.event(repo, n, 'base_changed', f'Retarget skipped; expected {a["expected_base"]}, observed {p["base"]["ref"]}')
            return
        if kind == 'update' and (not self.behind(repo, p) or p.get('mergeable') is not True):
            return
        with self.db:
            self.db.execute("UPDATE actions SET phase='issued',attempts=attempts+1 WHERE key=?", (key,))
        try:
            if kind == 'update':
                self.api(f'repos/{repo}/pulls/{n}/update-branch', '-X', 'PUT', '-f', f'expected_head_sha={head}')
            else:
                self.gh('pr', 'edit', str(n), '-R', repo, '--base', payload)
        except (Cancelled, GitHubReadPaused):
            with self.db:
                self.db.execute("UPDATE actions SET phase='planned',attempts=attempts-1 WHERE key=?", (key,))
            raise
        except (ActionRejected, OSError):
            with self.db:
                exhausted = a['attempts'] + 1 >= MAX_ATTEMPTS
                self.db.execute('UPDATE actions SET phase=? WHERE key=?', ('exhausted' if exhausted else 'planned', key))
                self.event(repo, n, 'action_exhausted' if exhausted else 'action_rejected', f'{kind} rejected on attempt {a["attempts"] + 1}')
            if exhausted:
                raise ActionExhausted(f'{kind} exhausted {MAX_ATTEMPTS} attempts at {head}')
            raise
        with self.db:
            self.event(repo, n, 'updated' if kind == 'update' else 'retargeted', f'{kind} requested at {head}')
            # update-branch is asynchronous. Reconcile on the next read, never resend it blindly.
            if kind == 'retarget':
                self.db.execute("UPDATE actions SET phase='done' WHERE key=?", (key,))

    def conflict(self, repo, p):
        n, head = p['number'], p['head']['sha']
        self.fetch(repo, head)
        base = p['base']['sha']
        self.fetch(repo, base)
        d = self.state / 'repos' / (repo.split('/')[-1] + '.git')
        merge = subprocess.run(['git', '-C', str(d), 'merge-tree', '--write-tree', '--name-only',
                                '-z', base, head], text=True, capture_output=True, timeout=BOUNDS['command']['seconds'], env=scrubbed_env())
        if merge.returncode != 1:
            raise RuntimeError('conflicting-files readback not ready')
        files = merge.stdout.split('\0')[1:]
        files = files[:files.index('')] if '' in files else files
        if not files:
            raise RuntimeError('conflicting-files readback empty')
        brief_dir = self.root / 'out/orch/queue/codex'
        brief_dir.mkdir(parents=True, exist_ok=True)
        brief = brief_dir / f"merge-conflict-{repo.split('/')[-1]}-{n}-{head}.txt"
        text = (f'Conflict repair for https://github.com/{repo}/pull/{n}\nHead: {head}\n'
                f'Base: {p["base"]["ref"]} {base}\nConflicting files:\n' + '\n'.join(files) +
                '\nUse an isolated worktree. Inspect and resolve by a reviewed source change; never force-push. '
                'Run relevant tests and request fresh review. Do not merge. '
                'Do not read credentials, encrypted stores or production dumps. '
                'Executor: latest GPT Sol, high effort.\n')
        if not brief.exists():
            brief.write_text(text)
        key = f'conflict:{repo}:{n}:{head}'
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO actions(key,kind,repo,pr,head,payload) VALUES(?,?,?,?,?,?)',
                            (key, 'dispatch', repo, n, head, str(brief)))
            self.event(repo, n, 'conflict', f'Fix brief {brief}; files: {", ".join(files)}')

    def dispatch_conflicts(self):
        with self.runner_lock():
            try:
                self._dispatch_conflicts()
            except WaitExpired as exc:
                with self.db:
                    self.db.execute("UPDATE actions SET phase='uncertain',desk=NULL WHERE kind='dispatch' AND phase IN ('planned','issued')")
                    self.event(REPOS[0], 0, 'dispatch_transport_stopped', str(exc))
                    self.db.execute("UPDATE entries SET phase='blocked',outcome='dispatch_transport_stopped' WHERE phase='conflict'")

    def dispatcher_identity(self, pid, receipt):
        if not pid:
            return None
        rows = command(['ps', '-axo', 'pid=,lstart=,command=']).splitlines()
        text = next((line.strip() for line in bounded_items('snapshot', rows) if line.split() and line.split()[0] == str(pid)), '')
        if str(self.root / 'tools/room-bridge/dispatch.py') not in text or str(receipt) not in text:
            return None
        return hashlib.sha256(text.encode()).hexdigest()

    def _dispatch_conflicts(self):
        if self.stopped:
            return
        # Expire reservations before any fallible registry or process read.
        for a in bounded_items('snapshot', self.db.execute("SELECT * FROM actions WHERE kind='dispatch' AND phase IN ('planned','issued')").fetchall()):
            self.expire_dispatch(a)
        exited = set()
        for key, child in bounded_items('snapshot', list(self.children.items())):
            if child.poll() is not None:
                child.wait(timeout=BOUNDS['child_reap']['seconds'])
                self.children.pop(key)
                exited.add(key)
        if not self.db.execute("SELECT 1 FROM actions WHERE kind='dispatch' AND phase IN ('planned','issued')").fetchone():
            return
        registry_path = Path(os.environ.get('CARR_HERMES_DESKS', Path.home() / '.config/carr/hermes-desks.json'))
        registry = json.loads(registry_path.read_text())['desks']
        ps = [line.split() for line in bounded_items('snapshot', command(['ps', '-axo', 'command=']).splitlines())]
        for a in bounded_items('snapshot', self.db.execute("SELECT * FROM actions WHERE kind='dispatch' AND phase='issued'").fetchall()):
            if self.expire_dispatch(a):
                continue
            results = Path(a['receipt']) if a['receipt'] else Path(a['payload']).with_suffix('.dispatch.jsonl')
            row = None
            if results.exists():
                try:
                    rows = [json.loads(line) for line in bounded_items('snapshot', results.read_text().splitlines(keepends=True))
                            if line.strip() and line.endswith('\n')]
                except (ValueError, OSError):
                    with self.db:
                        self.event(a['repo'], a['pr'], 'dispatch_receipt_invalid', f'Readback failed: {results}; other briefs continue')
                    rows = []
                row = rows[-1] if rows else None
                if row and row.get('desk') == a['desk']:
                    outcome = 'dispatch_finished' if row.get('status') == 'completed' and row.get('thread_id') else 'dispatch_failed'
                    with self.db:
                        self.db.execute("UPDATE actions SET phase='done',desk=NULL WHERE key=?", (a['key'],))
                        self.event(a['repo'], a['pr'], outcome, f'{a["desk"]}: {row.get("status")}; receipt {results}')
                    continue
            child = self.children.get(a['key'])
            try:
                identity = self.dispatcher_identity(a['dispatcher_pid'], results) if child is None else None
            except (RuntimeError, OSError, subprocess.TimeoutExpired):
                # An unreadable process inventory is not evidence of an orphan.
                continue
            legacy_live = not a['dispatcher_pid'] and any(
                str(self.root / 'tools/room-bridge/dispatch.py') in args and str(results) in args
                and a['desk'] in args for args in bounded_items('snapshot', ps))
            live = child is not None or legacy_live or (a['dispatcher_identity'] is not None and identity == a['dispatcher_identity'])
            if a['key'] in exited or not live:
                with self.db:
                    self.db.execute("UPDATE actions SET phase='uncertain',desk=NULL WHERE key=?", (a['key'],))
                    self.event(a['repo'], a['pr'], 'dispatch_uncertain', f'Dispatcher absent without a valid receipt {results}; desk {a["desk"]} released; inspect before retry')
        for a in bounded_items('snapshot', self.db.execute("SELECT * FROM actions WHERE kind='dispatch' AND phase='planned' ORDER BY rowid").fetchall()):
            if self.expire_dispatch(a):
                continue
            if self.stopped:
                return
            try:
                p = self.pr(a['repo'], a['pr'])
            except GitHubReadPaused as exc:
                with self.db:
                    self.event(a['repo'], a['pr'], 'github_paused', str(exc))
                continue
            except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                with self.db:
                    exhausted = isinstance(exc, (GitHubExhausted, WaitExpired)) or getattr(exc, 'github_attempts', 0) >= MAX_ATTEMPTS
                    if exhausted:
                        self.db.execute("UPDATE actions SET phase='exhausted',desk=NULL WHERE key=?", (a['key'],))
                    self.event(a['repo'], a['pr'], 'dispatch_exhausted' if exhausted else 'dispatch_waiting', str(exc))
                continue
            if self.held(p):
                continue
            if p['head']['sha'] != a['head'] or p['state'] != 'open':
                with self.db:
                    self.db.execute("UPDATE actions SET phase='done' WHERE key=?", (a['key'],))
                continue
            bridge = self.root / 'tools/room-bridge/dispatch.py'
            reserved = {r['desk'] for r in bounded_items('snapshot', self.db.execute("SELECT desk FROM actions WHERE phase='issued' AND desk IS NOT NULL"))}
            free = next((name for name, e in bounded_items('snapshot', sorted(registry.items()))
                         if e.get('kind') == 'codex-session' and re.fullmatch(r'gpt-[0-9.]+-sol', e.get('model', ''))
                         and e.get('effort') == 'high' and e.get('sandbox') == 'workspace-write'
                         and e.get('last_auth') is True and e.get('thread_id') is None
                         and not e.get('busy', False) and name not in reserved
                         and not any(name in args for args in bounded_items('snapshot', ps))), None)
            if free is None:
                with self.db:
                    if not self.db.execute("SELECT 1 FROM events WHERE repo=? AND pr=? AND outcome='dispatch_waiting'", (a['repo'], a['pr'])).fetchone():
                        self.event(a['repo'], a['pr'], 'dispatch_waiting', 'No free authenticated Codex Sol high desk; brief remains queued')
                continue
            results = Path(a['payload']).with_suffix('.dispatch.jsonl')
            argv = [sys.executable, str(bridge), '--registry', str(registry_path), '--results', str(results), 'send', free,
                    Path(a['payload']).read_text(), '--fresh']
            if '--family' in command([sys.executable, str(bridge), 'send', '--help']):
                argv += ['--family', 'sol']
            if self.stopped:
                return
            # Persist the intent before spawning. Uncertain dispatches are not automatically repeated.
            with self.db:
                self.db.execute("UPDATE actions SET phase='issued',desk=?,receipt=?,attempts=attempts+1 WHERE key=?", (free, str(results), a['key']))
                self.deadline(f'action:{a["key"]}', 'dispatch_process')
                self.event(a['repo'], a['pr'], 'dispatch_started', f'{free}, {registry[free]["model"]}, {registry[free]["effort"]}; {results}')
            with Path(a['payload']).with_suffix('.dispatch.log').open('a') as log:
                try:
                    self.check_cancelled()
                    self.children[a['key']] = subprocess.Popen(argv, cwd=self.root, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
                    child = self.children[a['key']]
                    with self.db:
                        self.db.execute('UPDATE actions SET dispatcher_pid=? WHERE key=?', (child.pid, a['key']))
                    try:
                        identity = self.dispatcher_identity(child.pid, results)
                    except (RuntimeError, OSError, subprocess.TimeoutExpired):
                        identity = None
                    with self.db:
                        self.db.execute('UPDATE actions SET dispatcher_identity=? WHERE key=?', (identity, a['key']))
                except Cancelled:
                    with self.db:
                        self.db.execute("UPDATE actions SET phase='planned',desk=NULL,attempts=attempts-1 WHERE key=?", (a['key'],))
                    return
                except OSError:
                    with self.db:
                        phase = 'exhausted' if a['attempts'] + 1 >= MAX_ATTEMPTS else 'planned'
                        self.db.execute('UPDATE actions SET phase=?,desk=NULL WHERE key=?', (phase, a['key']))
                        self.event(a['repo'], a['pr'], 'dispatch_not_started', 'Process did not start; brief remains queued')

    def behind(self, repo, p):
        if p['mergeable_state'] == 'behind':
            return True
        # Drafts mask BEHIND with BLOCKED/DRAFT. Check main ancestry only after clean mergeability is proven.
        if p.get('draft') and p.get('mergeable') is True and p['mergeable_state'] in ('blocked', 'draft'):
            self.fetch(repo, p['head']['sha'])
            d = self.state / 'repos' / (repo.split('/')[-1] + '.git')
            result = subprocess.run(['git', '-C', str(d), 'merge-base', '--is-ancestor',
                                     'origin/main', p['head']['sha']], capture_output=True, env=scrubbed_env(),
                                    timeout=BOUNDS['command']['seconds'])
            if result.returncode not in (0, 1):
                raise RuntimeError('draft ancestry readback failed')
            return result.returncode == 1
        return False

    def refresh(self, repo, merged_branch):
        complete = True
        for summary in bounded_items('snapshot', self.pages(f'repos/{repo}/pulls?state=open&per_page=100')):
            if self.stopped:
                return False
            n = summary['number']
            try:
                p = self.pr(repo, n)
                if self.held(p):
                    with self.db:
                        self.event(repo, n, 'held', 'Skipped post-merge refresh; do_not_merge label present')
                    continue
                if p['base']['ref'] == merged_branch:
                    self.action('retarget', repo, n, p['head']['sha'], 'main', expected_base=p['base']['ref'])
                    p = self.pr(repo, n)
                if p['base']['ref'] != 'main':
                    with self.db:
                        self.event(repo, n, 'stacked', f'Waiting for base {p["base"]["ref"]}')
                    continue
                if p.get('mergeable') is False or p['mergeable_state'] == 'dirty':
                    self.conflict(repo, p)
                elif self.behind(repo, p) and p.get('mergeable') is True:
                    self.action('update', repo, n, p['head']['sha'], 'main')
                elif p['mergeable_state'] == 'unknown' or p.get('mergeable') is None:
                    raise RuntimeError('GitHub is still computing mergeability')
                else:
                    with self.db:
                        self.event(repo, n, 'unchanged', f'Post-merge refresh: {p["mergeable_state"]} at {p["head"]["sha"]}')
            except GitHubReadPaused as exc:
                with self.db:
                    self.event(repo, n, 'github_paused', str(exc))
                raise
            except (WaitExpired, subprocess.TimeoutExpired):
                raise WaitExpired('Post-merge refresh transport deadline expired; reconcile before retry') from None
            except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                complete = False
                with self.db:
                    self.event(repo, n, 'refresh_waiting', str(exc))
        return complete

    def discover(self):
        for repo in bounded_items('snapshot', REPOS):
            if self.stopped:
                return
            try:
                prs = self.pages(f'repos/{repo}/pulls?state=open&per_page=100')
            except GitHubReadPaused as exc:
                with self.db:
                    self.event(repo, 0, 'github_paused', str(exc))
                continue
            except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                with self.db:
                    self.event(repo, 0, 'discovery_unreadable', str(exc))
                continue
            for p in bounded_items('snapshot', prs):
                if self.held(p):
                    continue
                if self.db.execute('SELECT 1 FROM entries WHERE repo=? AND pr=? AND phase IN (\'pending\',\'merging\',\'refreshing\')',
                                   (repo, p['number'])).fetchone():
                    continue
                e = None
                old_entry, self._github_entry = self._github_entry, None
                try:
                    approved = self.approval(repo, p['number'])
                    if not approved:
                        continue
                    entry = self.enqueue(repo, p['number'], approved, 'auto-enqueued from independent review')
                    e = self.db.execute('SELECT * FROM entries WHERE id=?', (entry,)).fetchone()
                    self._github_entry = entry
                    if e['auto_attempts'] >= AUTO_ENQUEUE_CAP:
                        with self.db:
                            self.event(repo, p['number'], 'auto_enqueue_exhausted', f'Automatic enqueue cap reached for {approved}', entry)
                        continue
                    if e['phase'] in ('review', 'conflict') and self.covered(repo, approved, p['head']['sha']):
                        with self.db:
                            self.db.execute('UPDATE entries SET auto_attempts=auto_attempts+1 WHERE id=?', (entry,))
                        self.report(e, 'fresh_review_accepted', f'Independent approval covers {p["head"]["sha"]}', 'pending')
                    elif e['phase'] == 'pending' and e['auto_attempts'] == 0:
                        with self.db:
                            self.db.execute('UPDATE entries SET auto_attempts=1 WHERE id=?', (entry,))
                except GitHubReadPaused as exc:
                    with self.db:
                        self.event(repo, p['number'], 'github_paused', str(exc), e['id'] if e else None)
                except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                    exhausted = isinstance(exc, (GitHubExhausted, WaitExpired)) or getattr(exc, 'github_attempts', 0) >= MAX_ATTEMPTS
                    if e is not None:
                        self.report(e, 'discovery_exhausted' if exhausted else 'discovery_waiting', str(exc), 'blocked' if exhausted else None)
                    else:
                        with self.db:
                            self.event(repo, p['number'], 'discovery_unreadable', str(exc))
                finally:
                    self._github_entry = old_entry

    def tick(self):
        with self.runner_lock():
            repos = [r[0] for r in bounded_items('snapshot', self.db.execute("SELECT repo FROM entries WHERE phase IN ('pending','merging','merge_rejected','refreshing') GROUP BY repo ORDER BY MIN(id)"))]
            for repo in bounded_items('snapshot', repos):
                if self.stopped:
                    return
                self._tick_repo(repo)

    def _tick_repo(self, queued_repo):
        for e in bounded_items('snapshot', self.db.execute("SELECT * FROM entries WHERE repo=? AND phase IN ('pending','merging','merge_rejected','refreshing') ORDER BY id", (queued_repo,)).fetchall()):
            repo, n, approved = e['repo'], e['pr'], e['approved']
            self._github_entry = None if e['phase'] == 'merge_rejected' else e['id']
            try:
                if time.time() >= self.deadline(f'entry:{e["id"]}', 'entry'):
                    if e['ci_since'] is not None and time.time() - e['ci_since'] >= CI_WAIT_SECONDS:
                        self.report(e, 'ci_timeout', 'Hosted checks did not turn green before the CI deadline', 'exhausted')
                    else:
                        self.report(e, 'entry_timeout', 'Entry deadline expired; intent retained; reconcile before retry', 'blocked')
                    continue
                p = self.pr(repo, n)
                if p.get('merged'):
                    mc = p.get('merge_commit_sha')
                    if not mc or not SHA.fullmatch(mc):
                        raise RuntimeError('merge commit not available yet')
                    self.fetch(repo, mc)
                    self.git(repo, 'merge-base', '--is-ancestor', mc, 'origin/main')
                    if e['tested'] is None:
                        self.report(e, 'already_merged', f'Imported intent reconciled: {mc} on origin/main', 'done')
                        continue
                    self.report(e, 'merged', f'Merge commit {mc} confirmed on origin/main', 'refreshing')
                    if not self.refresh(repo, p['head']['ref']):
                        if self.retry(e, 'Post-merge refresh remains incomplete'):
                            continue
                        return
                    self.report(e, 'merged', f'Merge commit {mc} confirmed; post-merge refresh complete', 'done')
                    return
                if p['state'] != 'open':
                    self.report(e, 'closed', 'PR closed without merge', 'done')
                    continue
                if self.held(p):
                    self.report(e, 'held', 'do_not_merge label present')
                    continue
                if e['phase'] == 'merge_rejected':
                    if self.merge_pending(repo, n, e['tested']):
                        if self.wait(e, 'provider_merge', 'merge_pending', 'GitHub owns an automatic or queued merge'):
                            return
                        continue
                    if e['merge_attempts'] >= MAX_ATTEMPTS:
                        self.report(e, 'merge_exhausted', f'GitHub rejected {MAX_ATTEMPTS} merge attempts', 'exhausted')
                    else:
                        self.report(e, 'reconciled', 'GitHub rejected the request and now proves no pending merge', 'pending')
                    return
                if p['base']['ref'] != 'main':
                    self.report(e, 'stacked', f'Waiting for base {p["base"]["ref"]} to merge')
                    continue
                if e['phase'] == 'merging':
                    if self.failure(e, 'Merge was issued; reconcile GitHub state before any retry',
                                    'transient_attempts', 'merge_uncertain', 'merge_uncertain_exhausted', 'blocked'):
                        continue
                    return
                head = p['head']['sha']
                if self.approval(repo, n) != approved:
                    self.report(e, 'fresh_review', 'Latest independent verdict does not approve queued head', 'review')
                    continue
                if not self.covered(repo, approved, head):
                    self.report(e, 'fresh_review', f'Patch differs from approved head; review {head}', 'review')
                    continue
                if p.get('mergeable') is False or p['mergeable_state'] == 'dirty':
                    self.conflict(repo, p)
                    self.report(e, 'conflict', f'Conflict brief filed for {head}', 'conflict')
                    continue
                if self.behind(repo, p) and p.get('mergeable') is True:
                    self.action('update', repo, n, head, 'main')
                    self.report(e, 'updating', f'Waiting for clean update of {head}')
                    return
                if p['mergeable_state'] == 'unknown' or p.get('mergeable') is None:
                    if self.wait(e, 'mergeability', 'waiting_mergeability', 'GitHub mergeability is unknown'):
                        return
                    continue
                if not self.green(repo, n, head):
                    since = e['ci_since'] if e['ci_head'] == head and e['ci_since'] is not None else time.time()
                    with self.db:
                        self.db.execute('UPDATE entries SET ci_since=?,ci_head=? WHERE id=?', (since, head, e['id']))
                    if time.time() - since >= CI_WAIT_SECONDS:
                        self.report(e, 'ci_timeout', f'Hosted checks did not turn green within 75 minutes at {head}', 'exhausted')
                    else:
                        self.report(e, 'waiting_ci', f'Waiting for green hosted checks on {head}')
                    if time.time() - since >= CI_WAIT_SECONDS:
                        continue
                    return
                current = self.pr(repo, n)
                if self.held(current) or current['head']['sha'] != head or current['base']['ref'] != 'main':
                    self.report(e, 'head_moved', 'Head, base or hold changed during checks')
                    return
                if self.approval(repo, n) != approved:
                    self.report(e, 'fresh_review', 'Independent verdict changed during checks', 'review')
                    return
                if self.stopped:
                    return
                marker = f'APPROVE\nReviewed-SHA: {head}\n\nOrchestrator merge queue: independent approval of {approved}; patch unchanged; hosted checks green.'
                self.api(f'repos/{repo}/issues/{n}/comments', '-f', f'body={marker}')
                if current.get('draft'):
                    self.gh('pr', 'ready', str(n), '-R', repo)
                current = self.pr(repo, n)
                if self.held(current) or current['head']['sha'] != head or current['base']['ref'] != 'main':
                    return
                if self.stopped:
                    return
                if e['merge_attempts'] >= MAX_ATTEMPTS:
                    self.report(e, 'merge_exhausted', f'Merge attempt cap reached at {head}', 'exhausted')
                    return
                with self.db:
                    self.db.execute("UPDATE entries SET phase='merging',tested=?,merge_attempts=merge_attempts+1 WHERE id=?", (head, e['id']))
                try:
                    self.gh('pr', 'merge', str(n), '-R', repo, '--squash', '--match-head-commit', head)
                except ActionRejected as exc:
                    raise MergeRejected(str(exc)) from exc
                except (Cancelled, GitHubReadPaused):
                    with self.db:
                        self.db.execute("UPDATE entries SET phase='pending',merge_attempts=merge_attempts-1 WHERE id=?", (e['id'],))
                    raise
                self.report(e, 'merge_requested', f'Guarded squash merge requested at {head}')
                return
            except GitHubReadPaused as exc:
                self.report(e, 'github_paused', str(exc))
                continue
            except MergeRejected as exc:
                self.report(e, 'merge_rejected', str(exc), 'merge_rejected')
                return
            except ActionUncertain as exc:
                if self.failure(e, str(exc), 'transient_attempts', 'action_uncertain', 'action_uncertain_exhausted', 'blocked'):
                    continue
                return
            except ActionExhausted as exc:
                self.report(e, 'action_exhausted', str(exc), 'exhausted')
                continue
            except WaitExpired as exc:
                self.report(e, 'wait_exhausted', str(exc), 'blocked')
                continue
            except subprocess.TimeoutExpired:
                self.report(e, 'wait_exhausted', 'Ancestry or conflict transport deadline expired; reconcile before retry', 'blocked')
                continue
            except GitHubExhausted as exc:
                self.report(e, 'github_exhausted', str(exc), 'blocked')
                continue
            except ReadRejected as exc:
                if self.failure(e, str(exc), 'read_attempts', 'read_rejected', 'read_exhausted', 'blocked', exc):
                    continue
                return
            except ActionRejected as exc:
                if self.failure(e, str(exc), 'transient_attempts', 'action_rejected', 'action_exhausted', 'blocked', exc):
                    continue
                return
            except Cancelled:
                return
            except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                if self.retry(e, str(exc), exc):
                    continue
                return
            finally:
                self._github_entry = None

    def failure(self, e, detail, counter, pending, exhausted_outcome, phase, exc=None):
        with self.db:
            self.db.execute(f'UPDATE entries SET {counter}={counter}+1 WHERE id=?', (e['id'],))
            attempts = self.db.execute(f'SELECT {counter} FROM entries WHERE id=?', (e['id'],)).fetchone()[0]
        attempts = max(attempts, getattr(exc, 'github_attempts', 0),
                       self.db.execute('SELECT github_attempts FROM entries WHERE id=?', (e['id'],)).fetchone()[0])
        exhausted = attempts >= MAX_ATTEMPTS
        self.report(e, exhausted_outcome if exhausted else pending,
                    f'{detail}; {attempts} of {MAX_ATTEMPTS} attempts', phase if exhausted else None)
        return exhausted

    def retry(self, e, detail, exc=None):
        return self.failure(e, detail, 'transient_attempts', 'retry', 'retry_exhausted', 'exhausted', exc)

    def merge_pending(self, repo, n, expected):
        owner, name = repo.split('/')
        query = ('query($owner:String!,$repo:String!,$n:Int!){repository(owner:$owner,name:$repo){'
                 'pullRequest(number:$n){headRefOid state autoMergeRequest{enabledAt} mergeQueueEntry{id}}}}')
        def validate(data):
            p = data['data']['repository']['pullRequest']
            if p['headRefOid'] != expected or p['state'] != 'OPEN':
                raise RuntimeError('merge intent readback changed; retry observation')
            p['autoMergeRequest'], p['mergeQueueEntry']
        data = self.api('graphql', '-f', f'query={query}', '-f', f'owner={owner}', '-f', f'repo={name}', '-F', f'n={n}', validate=validate)
        p = data['data']['repository']['pullRequest']
        return p['autoMergeRequest'] is not None or p['mergeQueueEntry'] is not None

    def reconcile(self, entry, retry=False):
        with self.runner_lock(), self.github_scope(f'reconcile:{entry}'):
            self._reconcile(entry, retry)

    def _reconcile(self, entry, retry=False):
        e = self.db.execute('SELECT * FROM entries WHERE id=?', (entry,)).fetchone()
        if not e or e['phase'] not in ('merging', 'merge_rejected', 'exhausted', 'blocked') or not e['tested']:
            raise RuntimeError('reconciliation requires a persisted merge intent')
        p = self.pr(e['repo'], e['pr'])
        if p.get('merged'):
            if retry:
                self.rearm_reads(entry)
                self.report(e, 'reconciled', 'Provider reports merged; resume commit confirmation and post-merge refresh', 'merging')
            return
        if self.held(p) or p['base']['ref'] != 'main':
            raise RuntimeError('head is held or base changed; no retry authorized')
        if self.merge_pending(e['repo'], e['pr'], e['tested']):
            self.report(e, 'merge_pending', 'Provider still owns queued or automatic merge; waiting')
        elif retry:
            if e['merge_attempts'] >= MAX_ATTEMPTS:
                self.report(e, 'merge_exhausted', 'Provider readback complete; merge attempt cap reached', 'exhausted')
            else:
                self.rearm_reads(entry)
                self.report(e, 'reconciled', 'Explicit retry after exact-head provider readback proved no pending merge', 'pending')
        else:
            self.report(e, 'retry_available', 'Provider proved no pending merge; explicit reconcile --retry can rearm it')

    def rearm_reads(self, entry):
        with self.db:
            self.db.execute('UPDATE entries SET transient_attempts=0,read_attempts=0,github_attempts=0 WHERE id=?', (entry,))
            self.db.execute("DELETE FROM github_failures WHERE (scope='automatic' OR scope=?) AND is_read=1 AND entry_id=?",
                            (f'automatic:entry:{entry}', entry))
            self.db.execute('DELETE FROM github_failures WHERE scope=? AND stopped=1', (f'automatic:entry:{entry}',))
            self.db.execute('DELETE FROM waits WHERE owner=?', (f'entry:{entry}',))

    def reconcile_action(self, key, retry=False):
        with self.runner_lock(), self.github_scope('reconcile-action:' + hashlib.sha256(key.encode()).hexdigest()):
            self._reconcile_action(key, retry)

    def _reconcile_action(self, key, retry=False):
        a = self.db.execute('SELECT * FROM actions WHERE key=?', (key,)).fetchone()
        if not a or a['kind'] not in ('update', 'retarget') or a['phase'] != 'issued':
            raise RuntimeError('reconciliation requires an issued update or retarget intent')
        p = self.pr(a['repo'], a['pr'])
        completed = (a['kind'] == 'update' and p['head']['sha'] != a['head']) or (a['kind'] == 'retarget' and p['base']['ref'] == a['payload'])
        if completed or p['state'] != 'open':
            with self.db:
                self.db.execute("UPDATE actions SET phase='done' WHERE key=?", (key,))
            if retry:
                self.resume_action_entries(a)
            return
        if self.held(p) or p['head']['sha'] != a['head']:
            raise RuntimeError('action head moved or is held; no retry')
        if a['kind'] == 'retarget' and (not a['expected_base'] or p['base']['ref'] != a['expected_base']):
            raise RuntimeError('retarget source base changed or is unknown; no retry')
        if a['kind'] == 'update' and (not self.behind(a['repo'], p) or p.get('mergeable') is not True):
            raise RuntimeError('action no longer has a clean BEHIND head')
        if retry:
            with self.db:
                self.db.execute('UPDATE actions SET phase=? WHERE key=?',
                                ('exhausted' if a['attempts'] >= MAX_ATTEMPTS else 'planned', key))
                self.event(a['repo'], a['pr'], 'action_reconciled', 'Explicit retry after current head/base/label readback; next effect remains expected-head guarded')
            if a['attempts'] < MAX_ATTEMPTS:
                self.resume_action_entries(a)

    def resume_action_entries(self, a):
        for e in bounded_items('snapshot', self.db.execute("SELECT * FROM entries WHERE repo=? AND pr=? AND phase='blocked' AND outcome IN ('action_uncertain_exhausted','wait_exhausted') AND tested IS NULL",
                                 (a['repo'], a['pr'])).fetchall()):
            self.rearm_reads(e['id'])
            self.report(e, 'action_reconciled', 'Provider action readback complete; resume current head and independent approval checks', 'pending')

    def import_legacy(self, legacy):
        rows, sources = [], {}
        for name in bounded_items('snapshot', ('merge-queue.txt', 'merge-queue.done')):
            path = legacy / name
            if not path.exists():
                continue
            data = path.read_bytes()
            sources[name] = hashlib.sha256(data).hexdigest()
            # Legacy enqueue appends a complete newline-terminated row. A partial tail is retried next poll.
            for line in bounded_items('snapshot', data.decode().splitlines(keepends=True)):
                if not line.endswith('\n'):
                    continue
                if not line.strip() or line.lstrip().startswith('#'):
                    continue
                parts = line.split(maxsplit=3)
                if len(parts) < 3 or parts[0] not in REPOS or not parts[1].isdigit() or not SHA.fullmatch(parts[2]):
                    raise ValueError(f'Invalid legacy queue row in {name}; no rows imported')
                rows.append((parts[0], int(parts[1]), parts[2], parts[3] if len(parts) == 4 else ''))
        for row in bounded_items('snapshot', rows):
            self.enqueue(*row)
        receipt = self.state / 'legacy-import.json'
        temporary = receipt.with_suffix('.tmp')
        temporary.write_text(json.dumps({'sources': sources, 'rows': len(rows),
                                         'distinct': len({r[:3] for r in bounded_items('snapshot', rows)}),
                                         'policy': 'Ignore pointers and .done as success; reconcile GitHub.'}, indent=2))
        temporary.replace(receipt)

    def migrate_holds(self, legacy, apply=False):
        patterns = []
        path = legacy / 'merge-holds.txt'
        if path.exists():
            for line in bounded_items('snapshot', path.read_text().splitlines()):
                if not line.strip() or line.lstrip().startswith('#'):
                    continue
                parts = line.split(maxsplit=2)
                if len(parts) < 2 or parts[0] not in REPOS:
                    raise ValueError('Invalid legacy hold; migration requires review')
                patterns.append((parts[0], re.compile(parts[1], re.I)))
        registry = legacy / 'registry-merge-hold.txt'
        allowlist = {int(x) for x in bounded_items('snapshot', registry.read_text().split()) if x.isdigit()} if registry.exists() else set()
        plan = []
        for repo in bounded_items('snapshot', REPOS):
            for p in bounded_items('snapshot', self.pages(f'repos/{repo}/pulls?state=open&per_page=100')):
                if self.held(p):
                    continue
                reasons = []
                if re.search(r'\bdo_not_merge\b', p['title'], re.I):
                    reasons.append('legacy title marker')
                if any(r == repo and pattern.search(p['title']) for r, pattern in bounded_items('snapshot', patterns)):
                    reasons.append('legacy merge-holds title pattern')
                if repo == REPOS[0] and registry.exists() and p['number'] not in allowlist:
                    files = self.pages(f'repos/{repo}/pulls/{p["number"]}/files?per_page=100')
                    if any(f['filename'].startswith(('migrations/', 'mcp-server/src/scac-mutation-registry')) for f in bounded_items('snapshot', files)):
                        reasons.append('legacy registry freeze; PR is outside its allowlist')
                if reasons:
                    plan.append({'repo': repo, 'pr': p['number'], 'reasons': reasons})
        if apply:
            for repo in bounded_items('snapshot', sorted({r['repo'] for r in bounded_items('snapshot', plan)})):
                self.gh('label', 'create', HOLD, '-R', repo, '--color', 'B60205', '--force',
                        '--description', 'Single merge hold; queue and refresh skip this PR')
            for r in bounded_items('snapshot', plan):
                self.api(f'repos/{r["repo"]}/issues/{r["pr"]}/labels', '-X', 'POST', '-f', f'labels[]={HOLD}')
                if not self.held(self.pr(r['repo'], r['pr'])):
                    raise RuntimeError('hold label readback failed')
                with self.db:
                    self.event(r['repo'], r['pr'], 'held', '; '.join(r['reasons']))
            (self.state / 'hold-migration.json').write_text(json.dumps({'legacy': str(legacy.resolve()),
                                                                      'applied': time.time(), 'plan': plan}))
        return plan

    def archive_legacy(self, legacy):
        success = self.db.execute("SELECT * FROM entries WHERE phase='done' AND tested IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        if success is None:
            raise RuntimeError('archive requires a confirmed merge made by this agent')
        p = self.pr(success['repo'], success['pr'])
        if not p.get('merged') or p['head']['sha'] != success['tested']:
            raise RuntimeError('agent merge readback failed')
        self.fetch(success['repo'], p['merge_commit_sha'])
        self.git(success['repo'], 'merge-base', '--is-ancestor', p['merge_commit_sha'], 'origin/main')
        self.import_legacy(legacy)
        target = legacy / '_to_delete'
        target.mkdir(exist_ok=True)
        for path in bounded_items('snapshot', legacy.iterdir()):
            if path.is_file() and path.name.startswith(('merge-', 'auto-enqueue', 'wait-green-enqueue', 'gate-merge')) and '.sh' in path.name:
                dest = target / path.name
                if dest.exists():
                    dest = target / (path.name + '.' + hashlib.sha256(path.read_bytes()).hexdigest()[:12])
                shutil.move(str(path), str(dest))
        python = self.root / '.venv/bin/python'
        argv = [str(python), str(self.root / 'tools/merge_queue/main.py'), '--state', str(self.state), 'enqueue']
        (legacy / 'merge-enqueue.sh').write_text('#!/bin/zsh\nexec ' + shlex.join(argv) + ' "$@"\n')
        (legacy / 'merge-enqueue.sh').chmod(0o755)

    def install_agent(self, target, apply=False):
        body = (self.root / 'ops/launchd/com.carr.merge-queue.plist').read_text().replace('{{REPO}}', str(self.root))
        target.parent.mkdir(parents=True, exist_ok=True)
        if not apply:
            target.write_text(body)
            return
        from lib.carr_paths import canonical_checkout
        from lib.machine_role import is_primary
        if self.root.resolve() != Path(canonical_checkout()).resolve() or not is_primary(str(self.root)):
            raise RuntimeError('agent activation requires the canonical checkout on the primary machine')
        branch = command(['git', '-C', str(self.root), 'branch', '--show-current']).strip()
        if branch != 'main' or command(['git', '-C', str(self.root), 'rev-parse', 'HEAD']).strip() != command(['git', '-C', str(self.root), 'rev-parse', 'origin/main']).strip():
            raise RuntimeError('agent activation requires current main after review')
        config = runpy.run_path(str(self.root / 'ops/config-as-code.py'))
        matches = target.exists() and target.read_text() == body
        outcome = config['install_launchd_plist'](target.name, str(target), body, matches,
                                               timeout=BOUNDS['installer']['seconds'])
        if outcome not in ('loaded', 'kept'):
            raise RuntimeError(f'agent installation {outcome}; reconcile launchd before retry')

    def run(self, poll=None, discover=True, legacy=None):
        poll = min(BOUNDS['poll']['seconds'], poll if poll is not None else BOUNDS['poll']['seconds'])
        if poll < 0:
            raise ValueError('poll must be nonnegative')
        if legacy is not None:
            receipt = self.state / 'hold-migration.json'
            if not receipt.exists() or json.loads(receipt.read_text()).get('legacy') != str(legacy.resolve()):
                raise RuntimeError('handover requires migrate-holds --apply and label readback first')
        with self.runner_lock():
            self.stopped = False
            def stop(*_):
                self.stopped = True
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            next_discover = 0.0
            while not self.stopped:
                try:
                    if legacy is not None and not self.db.execute("SELECT 1 FROM service_stops WHERE key='legacy_import'").fetchone():
                        try:
                            self.import_legacy(legacy)
                        except (RuntimeError, ValueError, OSError) as exc:
                            with self.db:
                                self.db.execute("INSERT OR REPLACE INTO service_stops(key,reason) VALUES('legacy_import',?)", (str(exc),))
                            with (self.state / 'queue.log').open('a') as f:
                                f.write(json.dumps({'outcome': 'legacy_import_stopped', 'detail': str(exc), 'reason': 'Repair input and explicitly import it before clearing the stop; existing entries continue'}) + '\n')
                    if discover and time.monotonic() >= next_discover:
                        next_discover = time.monotonic() + BOUNDS['discovery']['seconds']
                        try:
                            self.discover()
                        except GitHubReadPaused:
                            pass
                        except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                            with (self.state / 'queue.log').open('a') as f:
                                f.write(json.dumps({'outcome': 'discovery_failed', 'detail': str(exc)}) + '\n')
                    self.tick()
                    self.dispatch_conflicts()
                except Cancelled:
                    pass
                except sqlite3.OperationalError:
                    self.stopped = True
                    with (self.state / 'queue.log').open('a') as f:
                        f.write(json.dumps({'outcome': 'storage_stopped', 'detail': 'SQLite deadline or storage failure; service stopped; restore storage before restart'}) + '\n')
                except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                    with (self.state / 'queue.log').open('a') as f:
                        f.write(json.dumps({'outcome': 'poll_failed', 'detail': str(exc)}) + '\n')
                finally:
                    if not self.stopped:
                        self.flush_events()
                end = time.monotonic() + poll
                while not self.stopped and time.monotonic() < end:
                    time.sleep(min(.2, max(0, end - time.monotonic())))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state', type=Path, default=ROOT / 'out/merge-queue')
    p.add_argument('--root', type=Path, default=ROOT)
    sub = p.add_subparsers(dest='cmd', required=True)
    run = sub.add_parser('run')
    run.add_argument('--poll', type=float, default=BOUNDS['poll']['seconds'])
    run.add_argument('--no-discover', action='store_true')
    run.add_argument('--legacy', type=Path)
    enq = sub.add_parser('enqueue')
    enq.add_argument('repo', choices=REPOS)
    enq.add_argument('pr', type=int)
    enq.add_argument('approved')
    enq.add_argument('note', nargs='?', default='')
    sub.add_parser('status')
    reconcile = sub.add_parser('reconcile')
    reconcile.add_argument('entry', type=int)
    reconcile.add_argument('--retry', action='store_true')
    action_reconcile = sub.add_parser('reconcile-action')
    action_reconcile.add_argument('key')
    action_reconcile.add_argument('--retry', action='store_true')
    installer = sub.add_parser('install-agent')
    installer.add_argument('--output', type=Path)
    installer.add_argument('--apply', action='store_true')
    for name in bounded_items('snapshot', ('import-legacy', 'migrate-holds', 'archive-legacy')):
        migration = sub.add_parser(name)
        migration.add_argument('legacy', type=Path)
        if name == 'migrate-holds':
            migration.add_argument('--apply', action='store_true')
    a = p.parse_args()
    q = Queue(a.state, a.root)
    if a.cmd == 'run':
        q.run(a.poll, not a.no_discover, a.legacy)
    elif a.cmd == 'enqueue':
        print(q.enqueue(a.repo, a.pr, a.approved, a.note))
    elif a.cmd == 'import-legacy':
        q.import_legacy(a.legacy)
    elif a.cmd == 'migrate-holds':
        print(json.dumps(q.migrate_holds(a.legacy, a.apply), indent=2))
    elif a.cmd == 'archive-legacy':
        q.archive_legacy(a.legacy)
    elif a.cmd == 'install-agent':
        target = a.output or (Path.home() / 'Library/LaunchAgents/com.carr.merge-queue.plist' if a.apply else a.state / 'agent-plan.plist')
        q.install_agent(target, a.apply)
        print(target)
    elif a.cmd == 'reconcile':
        q.reconcile(a.entry, a.retry)
    elif a.cmd == 'reconcile-action':
        q.reconcile_action(a.key, a.retry)
    else:
        print(json.dumps([dict(r) for r in bounded_items('snapshot', q.db.execute('SELECT * FROM entries ORDER BY id'))], indent=2))

if __name__ == '__main__':
    main()
