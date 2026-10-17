#!/usr/bin/env python3
"""External-fault liveness through Queue.run, durable restarts and reconciliation."""
import ast
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
import signal
import subprocess
import os
import fcntl
import sqlite3
import sys
import time
import shutil
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('queue_fixture', ROOT / 'tools/test_merge_queue.py')
assert spec is not None and spec.loader is not None
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)
mq = fixture_module.module
@dataclass(frozen=True)
class Effect:
    name: str
    sites: tuple
    scenario: str = 'normal'


EFFECTS = (
    Effect('pr_read', (('pr', 'api'),)),
    Effect('required_checks', (('green', 'gh'),)),
    Effect('update', (('action', 'api'),), 'behind'),
    Effect('retarget', (('action', 'gh'),), 'retarget'),
    Effect('merge', (('_tick_repo', 'gh'),)),
    Effect('merge_intent', (('merge_pending', 'api'),), 'rejected'),
    Effect('open_prs', (('discover', 'pages'), ('refresh', 'pages'), ('migrate_holds', 'pages'))),
    Effect('files', (('migrate_holds', 'pages'),), 'migration'),
    Effect('create_label', (('migrate_holds', 'gh'),), 'migration'),
    Effect('apply_label', (('migrate_holds', 'api'),), 'migration'),
)


def operation(argv):
    if argv[0] == 'gh':
        a = argv[1:]
        if a[0] == 'api':
            if a[1] == 'graphql':
                repo = next(x[6:] for x in a if x.startswith('owner=')) + '/' + next(x[5:] for x in a if x.startswith('repo='))
                return 'merge_intent', repo, int(next(x[2:] for x in a if x.startswith('n=')))
            bits = a[1].split('?')[0].split('/')
            repo = '/'.join(bits[1:3])
            if bits[3] == 'pulls' and len(bits) == 4:
                return 'open_prs', repo, None
            if bits[3] == 'commits':
                return ('check_runs' if bits[-1] == 'check-runs' else 'statuses'), repo, bits[4]
            n = int(bits[4])
            if bits[3] == 'pulls':
                if len(bits) > 5 and bits[5] == 'commits':
                    return 'dot_authors', repo, n
                return ('pr_read' if len(bits) == 5 else 'update' if bits[5] == 'update-branch' else 'files'), repo, n
            if bits[-1] == 'labels':
                return 'apply_label', repo, n
            return ('stamp' if any(x.startswith('body=') for x in a) else 'approval'), repo, n
        return {'checks': 'required_checks', 'edit': 'retarget', 'ready': 'ready', 'merge': 'merge', 'create': 'create_label'}[a[1]], a[a.index('-R') + 1], None if a[0] == 'label' else int(a[2])
    if argv[0] == 'git' and 'fetch' in argv:
        name = Path(argv[argv.index('-C') + 1]).name.removesuffix('.git')
        return 'fetch', 'jbookout/' + name, None
    return None


class RunLoop:
    def __init__(self, fixture, effect, fault):
        self.f, self.effect, self.fault = fixture, effect, fault
        self.now = 10000.0
        self.active = True
        self.failures = 0
        self.seen = Counter()
        self.merged = []
        self.reconciliations = 0
        self.polls = 0
        self.discoveries = 0
        self.commands = mq.command
        self.legacy = fixture.root / 'legacy'
        self.legacy.mkdir()
        (self.legacy / 'registry-merge-hold.txt').write_text('2 3 4 5 6 90')

    def target(self, op, argv):
        name, repo, owner = op
        if name != self.effect.name or repo not in (None, mq.REPOS[0]):
            return False
        if name in ('check_runs', 'statuses'):
            return owner == self.f.approved
        if name == 'fetch':
            return self.f.updated in argv
        return owner in (None, 1)

    def request(self, argv, **kwargs):
        assert all(isinstance(arg, str) for arg in argv), 'external transport received an invalid argument'
        op = operation(argv)
        if op is None:
            return self.commands(argv, **kwargs)
        self.seen[op[0]] += 1
        if self.active and self.fault not in ('pause', 'after_timeout') and self.target(op, argv):
            self.failures += 1
            if self.fault == 'timeout':
                raise mq.WaitExpired('injected external transport deadline expired')
            if argv[0] == 'gh':
                read = mq.gh_api_read(argv) if argv[1] == 'api' else argv[1:3] == ['pr', 'checks']
                raise (mq.ReadRejected if read else mq.ActionRejected)('injected persistent external rejection')
            raise OSError('injected persistent external failure')
        if argv[0] != 'gh':
            return self.commands(argv, **kwargs)
        a = argv[1:]
        self.f.data['calls'].append(a)
        name, repo, n = op
        p = self.f.data['prs'].get(f'{repo}#{n}')
        if name == 'pr_read':
            result = p
        elif name == 'open_prs':
            result = [p for p in self.f.data['prs'].values() if p['repo'] == repo and p['state'] == 'open']
        elif name == 'check_runs':
            result = {'check_runs': [{'id': 1, 'name': 'CI', 'app': {'id': 1}, 'status': 'completed', 'conclusion': 'success'}]}
        elif name == 'statuses':
            result = []
        elif name == 'required_checks':
            result = [{'bucket': 'pass'}]
            if n == 5:
                p['head']['sha'] = self.f.changed
            elif n == 6:
                p['labels'] = [{'name': mq.HOLD}]
        elif name == 'update':
            p['head']['sha'], p['mergeable_state'] = self.f.updated, 'clean'
            result = {'message': 'Updated'}
        elif name == 'retarget':
            p['base']['ref'], p['mergeable_state'] = 'main', 'clean'
            result = {}
        elif name == 'ready':
            p['draft'] = False
            result = {}
        elif name == 'merge':
            if self.effect.scenario == 'rejected' and n == 1 and self.active:
                raise mq.MergeRejected('injected provider merge rejection')
            head = a[a.index('--match-head-commit') + 1]
            assert head == p['head']['sha'], 'wrong-head merge'
            assert not any(x['name'] == mq.HOLD for x in p['labels']), 'hold bypass'
            p.update(merged=True, state='closed', merge_commit_sha=self.f.merge_sha)
            self.commands(['git', '-C', str(self.f.remote), 'update-ref', 'refs/heads/main', self.f.merge_sha])
            self.merged.append((repo, n, head))
            result = {}
        elif name == 'merge_intent':
            result = {'data': {'repository': {'pullRequest': {'headRefOid': p['head']['sha'], 'state': 'OPEN', 'autoMergeRequest': None, 'mergeQueueEntry': None}}}}
        elif name == 'files':
            result = [{'filename': 'migrations/test.sql'}] if n == 1 else []
        elif name == 'create_label':
            result = {'name': mq.HOLD}
        elif name == 'apply_label':
            p['labels'] = [{'name': mq.HOLD}]
            result = p['labels']
        else:
            raise AssertionError(f'unregistered effect: {op}')
        self.f.save()
        if self.active and self.fault == 'after_timeout' and self.target(op, argv):
            self.failures += 1
            raise mq.WaitExpired('injected mutation response deadline expired')
        out = json.dumps(result)
        return 'HTTP/2.0 200 OK\n\n' + out if '--include' in a else out

    def seed(self):
        f, scenario = self.f, self.effect.scenario
        if scenario == 'retarget':
            p = f.pr(90)
            p.update(merged=True, state='closed', merge_commit_sha=f.merge_sha)
            f.save()
            self.commands(['git', '-C', str(f.remote), 'update-ref', 'refs/heads/main', f.merge_sha])
            entry = f.q.enqueue(mq.REPOS[0], 90, f.approved)
            with f.q.db:
                f.q.db.execute("UPDATE entries SET phase='merging',tested=? WHERE id=?", (f.approved, entry))
        f.pr(1, head=f.updated if scenario == 'fetch' else f.approved,
             state='behind' if scenario == 'behind' else 'clean', draft=scenario == 'draft',
             base='branch-90' if scenario == 'retarget' else 'main')
        entry = f.q.enqueue(mq.REPOS[0], 1, f.approved)
        if scenario == 'fetch':
            with f.q.db:
                f.q.db.execute("UPDATE entries SET phase='review' WHERE id=?", (entry,))
        f.pr(2, head=f.approved if scenario == 'fetch' else f.updated)
        f.q.enqueue(mq.REPOS[0], 2, f.approved)
        f.pr(3, held=True)
        f.pr(4, head=f.changed)
        f.pr(5, head=f.updated)
        f.pr(6, head=f.updated)
        for n in (3, 4, 5, 6):
            f.q.enqueue(mq.REPOS[0], n, f.approved)
        for repo in mq.REPOS[1:]:
            f.pr(2, repo=repo)
            f.q.enqueue(repo, 2, f.approved)
        return entry

    def session(self, polls=8, reconcile=True):
        q = self.f.q
        deadline = self.now + polls * 30
        saved_handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        def sleep(seconds):
            self.now += max(seconds, .01)
            if self.now >= deadline:
                q.stopped = True
        def discover():
            self.discoveries += 1
            return original_discover()
        original_discover = q.discover
        original_flush = q.flush_events
        def flush():
            self.polls += 1
            return original_flush()
        with ExitStack() as stack:
            stack.enter_context(patch.object(mq, 'command', self.request))
            stack.enter_context(patch.object(mq, 'time', types.SimpleNamespace(time=lambda: self.now, monotonic=lambda: self.now, sleep=sleep)))
            stack.enter_context(patch.object(q, 'discover', discover))
            stack.enter_context(patch.object(q, 'flush_events', flush))
            if self.fault == 'pause':
                original_request, original_reserve = q._gh_request, q.budget.reserve
                current = []
                def request(args, decode, validate):
                    current[:] = ['gh', *args]
                    return original_request(args, decode, validate)
                def reserve(resource, **kwargs):
                    if self.active and self.target(operation(current), current):
                        raise mq.GitHubReadPaused(self.now + 900)
                    return original_reserve(resource, **kwargs)
                stack.enter_context(patch.object(q, '_gh_request', request))
                stack.enter_context(patch.object(q.budget, 'reserve', reserve))
            if self.effect.scenario == 'migration':
                try:
                    q.migrate_holds(self.legacy, apply=True)
                except (RuntimeError, OSError, subprocess.TimeoutExpired):
                    pass
            q.run(poll=30)
            q.stopped = False
            for e in (q.db.execute("SELECT id FROM entries WHERE tested IS NOT NULL AND phase IN ('merging','merge_rejected','exhausted','blocked')").fetchall() if reconcile else []):
                self.reconciliations += 1
                try:
                    q.reconcile(e['id'], retry=not self.active)
                except (RuntimeError, OSError, subprocess.TimeoutExpired):
                    pass
            for a in (q.db.execute("SELECT key FROM actions WHERE kind IN ('update','retarget') AND phase='issued'").fetchall() if reconcile else []):
                self.reconciliations += 1
                try:
                    q.reconcile_action(a['key'], retry=not self.active)
                except (RuntimeError, OSError, subprocess.TimeoutExpired):
                    pass
        for s, handler in saved_handlers.items():
            signal.signal(s, handler)


class LivenessTests(unittest.TestCase):


    def test_failed_discovery_keeps_tick_and_the_discovery_schedule_running(self):
        f = fixture_module.QueueTests()
        f.setUp()
        try:
            loop = RunLoop(f, EFFECTS[0], 'error')
            loop.active = False
            loop.seed()
            original = f.q.discover
            times = []
            def fail_first():
                times.append(loop.now)
                if len(times) == 1:
                    raise OSError('injected discovery failure before tick')
                return original()
            with patch.object(f.q, 'discover', fail_first):
                loop.session(polls=22)
            self.assertEqual(len(times), 3)
            self.assertTrue(all(b - a >= 300 for a, b in zip(times, times[1:])))
            self.assertTrue({(r, 2) for r in mq.REPOS} <= {(r, n) for r, n, _ in loop.merged})
            self.assertIn('discovery_failed', (f.state / 'queue.log').read_text())
        finally:
            f.doCleanups()

    def test_persistent_faults_release_other_work_across_restarts(self):
        for effect in EFFECTS:
            for fault in ('error', 'timeout'):
                with self.subTest(effect=effect.name, fault=fault):
                    f = fixture_module.QueueTests()
                    f.setUp()
                    try:
                        loop = RunLoop(f, effect, fault)
                        entry = loop.seed()
                        for _ in range(4):
                            loop.session(polls=16)
                            f.restart()
                        self.assertGreater(loop.failures, 0, 'Fault site was never reached')
                        limit = 8 if effect.name == 'merge_intent' else 4
                        self.assertLessEqual(loop.failures, limit, 'Persistent fault exceeded automatic and reconciliation budgets')
                        count = loop.failures
                        loop.session()
                        self.assertEqual(loop.failures, count, 'Restart replenished the failure budget')
                        progressed = {(repo, n) for repo, n, _ in loop.merged}
                        self.assertTrue({(repo, 2) for repo in mq.REPOS} <= progressed,
                                        'Persistent fault starved another repository or the next PR')
                        loop.active = False
                        f.restart()
                        loop.session()
                        f.restart()
                        loop.session()
                        row = f.q.db.execute('SELECT * FROM entries WHERE id=?', (entry,)).fetchone()
                        events = f.q.db.execute('SELECT outcome,detail FROM events WHERE repo=? AND pr IN (0,1,90)', (mq.REPOS[0],)).fetchall()
                        recovered = (mq.REPOS[0], 1) in {(repo, n) for repo, n, _ in loop.merged}
                        visible = any(e['detail'] and any(word in (e['outcome'] + e['detail']).lower() for word in ('exhaust', 'blocked', 'held')) for e in events)
                        self.assertTrue(recovered or visible, f'Entry silently stuck: {dict(row)}')
                        self.assertGreaterEqual(loop.discoveries, 4)
                        self.assertGreaterEqual(loop.polls, 4)
                    finally:
                        f.doCleanups()


    def test_resource_and_mutation_pauses_do_not_charge_or_issue_intents(self):
        for name in ('merge_intent', 'update', 'merge'):
            with self.subTest(effect=name):
                f = fixture_module.QueueTests()
                f.setUp()
                try:
                    effect = next(e for e in EFFECTS if e.name == name)
                    loop = RunLoop(f, effect, 'pause')
                    entry = loop.seed()
                    for _ in range(3):
                        loop.session()
                        f.restart()
                    row = f.q.db.execute('SELECT * FROM entries WHERE id=?', (entry,)).fetchone()
                    self.assertEqual((row['github_attempts'], row['read_attempts'], row['transient_attempts']),
                                     (1 if name == 'merge_intent' else 0, 0, 0))
                    if name in ('update', 'merge'):
                        self.assertEqual(row['merge_attempts'], 0)
                        self.assertNotIn((mq.REPOS[0], 1), {(r, n) for r, n, _ in loop.merged})
                        actions = f.q.db.execute('SELECT phase,attempts FROM actions WHERE pr=1').fetchall()
                        self.assertTrue(all(tuple(a) == ('planned', 0) for a in actions))
                    progressed = {(r, n) for r, n, _ in loop.merged}
                    self.assertTrue({(r, 2) for r in mq.REPOS} <= progressed)
                    loop.active = False
                    loop.session()
                    f.restart()
                    loop.session()
                    self.assertIn((mq.REPOS[0], 1), {(r, n) for r, n, _ in loop.merged})
                finally:
                    f.doCleanups()

    def test_rate_limit_pause_is_scheduling_and_recovers_after_restarts(self):
        f = fixture_module.QueueTests()
        f.setUp()
        try:
            loop = RunLoop(f, EFFECTS[0], 'pause')
            loop.active = False
            loop.seed()
            limiter = Path(f.env['GH_LIMITER_DIR'])
            limiter.mkdir()
            (limiter / 'cooldown').write_text(str(__import__('time').time() + 900))
            for _ in range(6):
                loop.session(polls=1)
                self.assertEqual(f.q.db.execute('SELECT COUNT(*) FROM github_failures').fetchone()[0], 0)
                self.assertEqual(tuple(f.q.db.execute('SELECT SUM(github_attempts),SUM(read_attempts),SUM(transient_attempts) FROM entries').fetchone()), (0, 0, 0))
                f.restart()
            self.assertEqual(loop.merged, [])
            (limiter / 'cooldown').unlink()
            loop.session(polls=12)
            self.assertTrue({(repo, 2) for repo in mq.REPOS} <= {(repo, n) for repo, n, _ in loop.merged})
            self.assertIn((mq.REPOS[0], 1), {(repo, n) for repo, n, _ in loop.merged})
        finally:
            f.doCleanups()

    def test_persisted_wait_deadlines_release_the_next_pr(self):
        for scenario in ('mergeability_deadline', 'provider_merge_deadline', 'red_ci_deadline', 'rate_pause_deadline'):
            with self.subTest(scenario=scenario):
                f = fixture_module.QueueTests(); f.setUp()
                try:
                    loop = RunLoop(f, EFFECTS[0], 'error'); loop.active = False
                    entry = loop.seed()
                    if scenario == 'mergeability_deadline':
                        f.data['prs'][mq.REPOS[0]+'#1']['mergeable_state'] = 'unknown'
                    if scenario == 'provider_merge_deadline':
                        with f.q.db:
                            f.q.db.execute("UPDATE entries SET phase='merge_rejected',tested=?,merge_attempts=1 WHERE id=?", (f.approved, entry))
                    original = loop.request
                    injected = []
                    def request(argv, **kw):
                        op = operation(argv)
                        if op and op[0] == 'merge_intent' and scenario == 'provider_merge_deadline':
                            injected.append(op)
                            return json.dumps({'data': {'repository': {'pullRequest': {
                                'headRefOid': f.approved, 'state': 'OPEN', 'autoMergeRequest': {'enabledAt': 'now'}, 'mergeQueueEntry': None}}}})
                        if op and op[:3] == ('required_checks', mq.REPOS[0], 1) and scenario == 'red_ci_deadline':
                            injected.append(op); return '[{"bucket":"fail"}]'
                        if op and op[:3] == ('pr_read', mq.REPOS[0], 1) and scenario == 'rate_pause_deadline':
                            injected.append(op); raise mq.GitHubReadPaused(loop.now + 900)
                        if op and op[:3] == ('pr_read', mq.REPOS[0], 1): injected.append(op)
                        return original(argv, **kw)
                    loop.request = request
                    for _ in range(4):
                        loop.session(polls=40); f.restart()
                    row = f.q.db.execute('SELECT * FROM entries WHERE id=?', (entry,)).fetchone()
                    self.assertIn(row['phase'], ('blocked', 'exhausted'))
                    self.assertTrue(injected, 'fault was never exercised')
                    self.assertIn((mq.REPOS[0], 2), {(r,n) for r,n,_ in loop.merged})
                    self.assertNotIn((mq.REPOS[0], 1), {(r,n) for r,n,_ in loop.merged})
                    if scenario == 'provider_merge_deadline':
                        self.assertEqual(row['tested'], f.approved)
                        event = f.q.db.execute('SELECT detail FROM events WHERE entry_id=? AND outcome=?', (entry, 'merge_pending_timeout')).fetchone()
                        self.assertIn('reconcile', event[0])
                finally:
                    f.doCleanups()

    def test_successful_mutation_with_lost_response_is_not_repeated(self):
        for name in ('merge', 'update', 'retarget'):
            with self.subTest(effect=name):
                f = fixture_module.QueueTests(); f.setUp()
                try:
                    effect = next(e for e in EFFECTS if e.name == name)
                    loop = RunLoop(f, effect, 'after_timeout'); loop.seed()
                    loop.session(reconcile=False); f.restart()
                    loop.active = False
                    for _ in range(2): loop.session(); f.restart()
                    self.assertEqual(loop.failures, 1)
                    self.assertEqual(sum(r == mq.REPOS[0] and n == 1 for r,n,_ in loop.merged), 1)
                    self.assertIn((mq.REPOS[0], 2), {(r,n) for r,n,_ in loop.merged})
                finally: f.doCleanups()

    def test_legacy_import_bound_stops_that_component_and_keeps_the_loop_live(self):
        f=fixture_module.QueueTests(); f.setUp()
        try:
            loop=RunLoop(f,EFFECTS[0],'error'); loop.active=False; loop.seed()
            (f.state/'hold-migration.json').write_text(json.dumps({'legacy':str(loop.legacy.resolve())}))
            original_run=f.q.run; calls=[]
            def run(*args,**kw): return original_run(*args,**kw,legacy=loop.legacy)
            def expired(legacy): calls.append(legacy); raise mq.WaitExpired('legacy input snapshot cap reached')
            with patch.object(f.q,'run',run),patch.object(f.q,'import_legacy',expired):
                loop.session()
            f.restart()
            original_run=f.q.run
            with patch.object(f.q,'run',run),patch.object(f.q,'import_legacy',expired):
                loop.session()
            self.assertEqual(len(calls),1,'restart retried a stopped legacy importer')
            self.assertIn('snapshot cap',f.q.db.execute("SELECT reason FROM service_stops WHERE key='legacy_import'").fetchone()[0])
            self.assertTrue({(r,2) for r in mq.REPOS} <= {(r,n) for r,n,_ in loop.merged})
        finally: f.doCleanups()

    def test_frozen_wall_clock_expires_spacing_wait_without_dispatch(self):
        f = fixture_module.QueueTests(); f.setUp()
        try:
            now, sleeps = [100.0], []
            f.q.budget.clock = lambda: 100.0
            with f.q.budget.state() as data:
                data[f.q.budget.shared] = {'next_start': 102.0}
            def sleep(seconds):
                sleeps.append(seconds)
                now[0] += seconds
            with patch.object(mq.time, 'monotonic', side_effect=lambda: now[0]), \
                    patch.object(mq.time, 'sleep', side_effect=sleep), \
                    patch.dict(mq.BOUNDS['spacing'], seconds=3), \
                    patch.object(mq, 'command') as command:
                with self.assertRaises(mq.WaitExpired):
                    f.q.api('repos/example/repo')
            command.assert_not_called()
            self.assertAlmostEqual(now[0], 103.0)
            self.assertGreater(len(sleeps), 1)
        finally: f.doCleanups()

    def test_cancellation_during_reserved_slot_wait_prevents_dispatch(self):
        f = fixture_module.QueueTests(); f.setUp()
        try:
            now = [100.0]
            f.q.budget.clock = lambda: 100.0
            with f.q.budget.state() as data:
                data[f.q.budget.shared] = {'next_start': 102.0}
            def sleep(seconds):
                now[0] += seconds
                f.q.stopped = True
            with patch.object(mq.time, 'monotonic', side_effect=lambda: now[0]), \
                    patch.object(mq.time, 'sleep', side_effect=sleep), \
                    patch.object(mq, 'command') as command:
                with self.assertRaises(mq.Cancelled):
                    f.q.api('repos/example/repo')
            command.assert_not_called()
        finally: f.doCleanups()

    def test_bounded_transports_and_contention(self):
        with self.assertRaises(mq.WaitExpired):
            list(mq.bounded_items('snapshot', range(mq.BOUNDS['snapshot']['attempts'] + 1)))
        with self.assertRaises(mq.WaitExpired):
            mq.command([sys.executable, '-c', 'import time; time.sleep(20)'], timeout=.02)
        f = fixture_module.QueueTests(); f.setUp()
        try:
            with (f.state / 'agent.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):
                    with f.q.runner_lock(): pass
            connection = sqlite3.connect(f.state / 'queue.sqlite3')
            try:
                connection.execute('BEGIN IMMEDIATE')
                f.q.db.execute('PRAGMA busy_timeout=10')
                with self.assertRaises(sqlite3.OperationalError):
                    f.q.enqueue(mq.REPOS[0], 9, f.approved)
            finally: connection.close()
            delay = mq.BOUNDS['spacing']['seconds'] + 1
            with patch.object(f.q.budget, 'reserve', return_value=(f.q.budget.clock() + delay, delay)):
                with self.assertRaises(mq.WaitExpired): f.q.pr(mq.REPOS[0], 1)
            pacing = Path(str(f.q.budget.path) + '.call.lock')
            with pacing.open('a') as lock, patch.dict(mq.BOUNDS['pacing_lock'], seconds=.02):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(mq.WaitExpired):
                    f.q.api('repos/example/repo')
            page = [{'id': i, 'body': '', 'author_association': 'OWNER'} for i in range(100)]
            def full_page(*args, validate):
                validate(page)
                return page
            with patch.object(f.q, 'api', side_effect=full_page):
                with self.assertRaisesRegex(mq.WaitExpired, 'pagination'):
                    f.q.pages('repos/example/repo/issues/1/comments?per_page=100')
        finally: f.doCleanups()


    def test_budget_contention_times_out_and_sigterm_cancels_the_real_loop(self):
        f = fixture_module.QueueTests(); f.setUp()
        try:
            f.pr(); f.q.enqueue(mq.REPOS[0], 1, f.approved)
            with open(f.env['CARR_GITHUB_READ_BUDGET'] + '.lock', 'a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                f.q.budget.lock_timeout = .02
                start = time.monotonic()
                for _ in range(mq.MAX_ATTEMPTS): f.q.tick()
                self.assertLess(time.monotonic() - start, 1)
                self.assertEqual(f.q.db.execute('SELECT phase FROM entries').fetchone()[0], 'blocked')
                code = ('import importlib.util,sys; from pathlib import Path; '
                        's=importlib.util.spec_from_file_location("queue",sys.argv[1]); '
                        'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
                        'q=m.Queue(Path(sys.argv[2]),Path(sys.argv[3]),gap=0); '
                        'q.enqueue(m.REPOS[0],2,"' + f.approved + '"); '
                        'print("ready",flush=True); q.run(discover=False)')
                child = subprocess.Popen([sys.executable, '-c', code, str(ROOT/'tools/merge_queue/main.py'), str(f.state), str(f.root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    self.assertEqual(child.stdout.readline().strip(), 'ready')
                    time.sleep(.1); child.terminate()
                    out, err = child.communicate(timeout=2)
                    self.assertEqual(child.returncode, 0, err)
                finally:
                    if child.poll() is None: child.kill(); child.communicate(timeout=2)
        finally: f.doCleanups()

    def test_dispatch_faults_have_persisted_deadlines_in_the_real_loop(self):
        for kind in ('no_desk', 'live', 'ps_timeout', 'help_timeout', 'spawn_failure', 'unreapable_child', 'completed_child'):
            with self.subTest(kind=kind):
                f = fixture_module.QueueTests(); f.setUp()
                try:
                    loop = RunLoop(f, EFFECTS[0], 'error'); loop.active = False; loop.seed()
                    with f.q.db:
                        f.q.db.execute("UPDATE entries SET phase='conflict' WHERE repo=? AND pr=1", (mq.REPOS[0],))
                    f.data['comments'][mq.REPOS[0]+'#1'] = []
                    registry = f.root/'desks.json'
                    registry.write_text(json.dumps({'desks': {} if kind == 'no_desk' else {'sol': {'kind':'codex-session','model':'gpt-6.1-sol','effort':'high','sandbox':'workspace-write','last_auth':True,'busy':False}}}))
                    brief=f.root/'conflict.txt'; brief.write_text('repair')
                    phase = 'planned' if kind in ('no_desk', 'help_timeout', 'spawn_failure') else 'issued'
                    identity = f'4242 Thu Oct 8 12:00:00 2026 python {f.root}/tools/room-bridge/dispatch.py --results {brief.with_suffix(".dispatch.jsonl")} send sol'
                    with f.q.db:
                        f.q.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload,phase,desk,dispatcher_pid,dispatcher_identity) VALUES(?,?,?,?,?,?,?,?,?,?)',
                                       ('fault', 'dispatch', mq.REPOS[0], 1, f.approved, str(brief), phase, 'sol' if phase == 'issued' else None, 4242 if phase == 'issued' else None, mq.hashlib.sha256(identity.encode()).hexdigest()))
                    original=loop.request; faults=[]
                    def request(argv,**kw):
                        if argv[0]=='ps':
                            faults.append('ps')
                            if kind=='ps_timeout': raise mq.WaitExpired('process inventory transport deadline expired')
                            if kind in ('spawn_failure','help_timeout','no_desk'): return ''
                            return identity if 'pid=,lstart=,command=' in argv else identity.split('python ',1)[-1]
                        if '--help' in argv:
                            faults.append('help')
                            if kind=='help_timeout': raise mq.WaitExpired('dispatcher preflight deadline expired')
                            return '--family'
                        return original(argv,**kw)
                    loop.request=request
                    class Child:
                        def poll(self): return 1 if kind == 'completed_child' else None
                        def terminate(self): faults.append('terminate')
                        def kill(self): faults.append('kill')
                        def wait(self,timeout):
                            if kind != 'completed_child' and 'kill' not in faults: raise subprocess.TimeoutExpired('dispatcher',timeout)
                    if kind in ('unreapable_child', 'completed_child'): f.q.children['fault']=Child()
                    popen = subprocess.Popen
                    def spawn(argv, **kw):
                        if len(argv)>1 and str(argv[1]).endswith('/room-bridge/dispatch.py'):
                            faults.append('spawn')
                            raise OSError('spawn fault')
                        return popen(argv, **kw)
                    with patch.dict(os.environ,{'CARR_HERMES_DESKS':str(registry)}), patch.object(mq.subprocess,'Popen',side_effect=spawn):
                        for _ in range(4):
                            loop.session(polls=12)
                            if kind not in ('unreapable_child', 'completed_child'): f.restart()
                    row=f.q.db.execute('SELECT * FROM actions WHERE key="fault"').fetchone()
                    self.assertIn(row['phase'],('uncertain','exhausted'))
                    self.assertIsNone(row['desk'])
                    self.assertIn((mq.REPOS[0],2),{(r,n) for r,n,_ in loop.merged})
                    if kind=='unreapable_child': self.assertIn('kill',faults)
                    if kind=='spawn_failure': self.assertIn('spawn',faults)
                    if kind=='help_timeout': self.assertIn('help',faults)
                finally: f.doCleanups()

    def test_git_transport_fault_routes_drive_the_real_loop(self):
        for route in ('conflict', 'git_init', 'git_remote', 'git_remote_read', 'merge_confirmation'):
            with self.subTest(route=route):
                f=fixture_module.QueueTests(); f.setUp()
                try:
                    loop=RunLoop(f,EFFECTS[0],'error'); loop.active=False; entry=loop.seed()
                    p=f.data['prs'][mq.REPOS[0]+'#1']
                    if route=='conflict': p.update(mergeable=False,mergeable_state='dirty')
                    elif route=='merge_confirmation':
                        p.update(merged=True,state='closed',merge_commit_sha=f.merge_sha)
                        with f.q.db: f.q.db.execute("UPDATE entries SET phase='merging',tested=? WHERE id=?",(f.approved,entry))
                    else: p['head']['sha']=f.updated
                    if route in ('git_init','git_remote'):
                        shutil.rmtree(f.state/'repos/carr-system.git')
                    original=subprocess.run; faults=[]
                    def run(argv,**kw):
                        current=f.q._github_entry
                        target=current==entry
                        hit=(route=='conflict' and 'merge-tree' in argv or
                             route=='git_init' and 'init' in argv or route=='git_remote' and 'remote' in argv and 'add' in argv or
                             route=='git_remote_read' and 'get-url' in argv or
                             route=='merge_confirmation' and '--is-ancestor' in argv and f.merge_sha in argv)
                        if target and hit:
                            faults.append(argv)
                            raise subprocess.TimeoutExpired('git',mq.BOUNDS['command']['seconds'])
                        if 'remote' in argv and 'add' in argv and str(argv[-1]).startswith('https://github.com/'):
                            argv=[*argv[:-1],str(f.remote)]
                        return original(argv,**kw)
                    with patch.object(mq.subprocess,'run',side_effect=run):
                        loop.session(polls=12,reconcile=False); f.restart(); loop.session(polls=12,reconcile=False)
                    self.assertEqual(len(faults),1,'transport fault was not exercised exactly once')
                    row=f.q.db.execute('SELECT phase,outcome FROM entries WHERE id=?',(entry,)).fetchone()
                    self.assertEqual(row['phase'],'blocked')
                    self.assertIn((mq.REPOS[0],2),{(r,n) for r,n,_ in loop.merged})
                finally: f.doCleanups()


    def test_installer_transport_timeouts(self):
        import contextlib
        import io
        import plistlib
        import tempfile
        import runpy
        config = runpy.run_path(str(ROOT / 'ops/config-as-code.py'))
        installer = config['install_launchd_plist']
        namespace = installer.__globals__
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'com.carr.test.plist'
            body = plistlib.dumps({'Label': 'com.carr.test'}).decode()
            for route in ('print', 'unload', 'load'):
                calls = []
                def run(argv, **kwargs):
                    calls.append(argv[1])
                    self.assertEqual(kwargs['timeout'], mq.BOUNDS['installer']['seconds'])
                    if argv[1] == route:
                        raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
                    return subprocess.CompletedProcess(argv, 113, '', 'Could not find service "com.carr.test"')
                with patch.dict(namespace, HOME=directory), patch.dict(os.environ, HOME=directory), \
                     patch.object(namespace['subprocess'], 'run', side_effect=run), \
                     patch.object(namespace['launchd_hold'], 'off_reason', return_value=None), \
                     patch.dict(namespace, launchd_off_reason=lambda *args: None), contextlib.redirect_stdout(io.StringIO()):
                    if route == 'load':
                        self.assertEqual(installer(target.name, str(target), body, False, timeout=mq.BOUNDS['installer']['seconds']), 'failed')
                    else:
                        with self.assertRaises(subprocess.TimeoutExpired):
                            installer(target.name, str(target), body, False, timeout=mq.BOUNDS['installer']['seconds'])
                self.assertIn(route, calls, 'installer timeout path was never reached')
                if route == 'print':
                    self.assertFalse(target.exists(), 'inspection timeout changed the plist')
                else:
                    self.assertTrue(Path(str(target) + '.pending-reload').exists(), 'uncertain reload lost its marker')
            f = fixture_module.QueueTests(); f.setUp()
            try:
                f.q.root = ROOT
                from lib import carr_paths, machine_role
                def preflight(argv, **kwargs):
                    if '--show-current' in argv: return 'main'
                    return 'same-sha'
                with patch.object(carr_paths, 'canonical_checkout', return_value=str(ROOT)), \
                     patch.object(machine_role, 'is_primary', return_value=True), \
                     patch.object(mq, 'command', side_effect=preflight), \
                     patch.object(mq.subprocess, 'run', side_effect=subprocess.TimeoutExpired('launchctl', 15)):
                    with self.assertRaises(subprocess.TimeoutExpired):
                        f.q.install_agent(target, apply=True)
                for index in range(1, 4):
                    calls = []
                    def fail_preflight(argv, **kwargs):
                        calls.append(argv)
                        if len(calls) == index: raise mq.WaitExpired('installer git transport deadline')
                        return preflight(argv, **kwargs)
                    with patch.object(carr_paths, 'canonical_checkout', return_value=str(ROOT)), \
                         patch.object(machine_role, 'is_primary', return_value=True), \
                         patch.object(mq, 'command', side_effect=fail_preflight):
                        with self.assertRaises(mq.WaitExpired): f.q.install_agent(target, apply=True)
                    self.assertEqual(len(calls), index)
            finally: f.doCleanups()


if __name__ == '__main__':
    unittest.main()
