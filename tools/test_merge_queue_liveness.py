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
EXERCISED: dict[str, set[str]] = {}
EXERCISED_BOUNDS: set[str] = set()

# Fixed test-owned site identities: adding a registry row cannot grant coverage.
FAULT_SITES = {
    'review_handoff': ('main.py:Queue.request_review:call:1',),
    'dot_author_timeout': ('main.py:Queue.approval:call:2',),
    'snapshot_cap': (
        'main.py:bounded_items:for:1',
        'main.py:gh_api_read:for:1',
        'main.py:gh_api_read:comprehension:1',
        'main.py:command:comprehension:1',
        'main.py:Queue.__init__:for:1',
        'main.py:Queue.__init__:comprehension:1',
        'main.py:Queue.__init__:for:2',
        'main.py:Queue.gh:for:1',
        'main.py:Queue.api_pages.validate:for:1',
        'main.py:Queue.validate_pr:comprehension:1',
        'main.py:Queue.held:comprehension:1',
        'main.py:Queue.flush_events:for:1',
        'main.py:Queue.flush_events:for:2',
        'main.py:Queue.green:for:1',
        'main.py:Queue.green:for:2',
        'main.py:Queue.green:comprehension:1',
        'main.py:Queue.green:comprehension:2',
        'main.py:Queue.green.validate:comprehension:1',
        'main.py:Queue.green:comprehension:3',
        'main.py:Queue.dispatcher_identity:comprehension:1',
        'main.py:Queue._dispatch_conflicts:for:1',
        'main.py:Queue._dispatch_conflicts:for:2',
        'main.py:Queue._dispatch_conflicts:comprehension:1',
        'main.py:Queue._dispatch_conflicts:for:3',
        'main.py:Queue._dispatch_conflicts:comprehension:2',
        'main.py:Queue._dispatch_conflicts:comprehension:3',
        'main.py:Queue._dispatch_conflicts:for:4',
        'main.py:Queue._dispatch_conflicts:comprehension:4',
        'main.py:Queue._dispatch_conflicts:comprehension:5',
        'main.py:Queue._dispatch_conflicts:comprehension:6',
        'main.py:Queue.refresh:for:1',
        'main.py:Queue.discover:for:1',
        'main.py:Queue.discover:for:2',
        'main.py:Queue.tick:comprehension:1',
        'main.py:Queue.tick:for:1',
        'main.py:Queue._tick_repo:for:1',
        'main.py:Queue.resume_action_entries:for:1',
        'main.py:Queue.import_legacy:for:1',
        'main.py:Queue.import_legacy:for:2',
        'main.py:Queue.import_legacy:for:3',
        'main.py:Queue.import_legacy:comprehension:1',
        'main.py:Queue.migrate_holds:for:1',
        'main.py:Queue.migrate_holds:comprehension:1',
        'main.py:Queue.migrate_holds:for:2',
        'main.py:Queue.migrate_holds:for:3',
        'main.py:Queue.migrate_holds:comprehension:2',
        'main.py:Queue.migrate_holds:comprehension:3',
        'main.py:Queue.migrate_holds:for:4',
        'main.py:Queue.migrate_holds:comprehension:4',
        'main.py:Queue.migrate_holds:for:5',
        'main.py:Queue.archive_legacy:for:1',
        'main.py:main:for:1',
        'main.py:main:comprehension:1',
    ),
    'transport_timeout': (
        'main.py:command:call:1',
    ),
    'sqlite_contention': (
        'main.py:Queue.__init__:call:1',
    ),
    'runner_contention': (
        'main.py:Queue.runner_lock:call:1',
    ),
    'dispatch_process_faults': (
    ),
    'persistent_effects': (
        'main.py:Queue.gh:call:1',
    ),
    'pacing_contention': (
        'main.py:Queue._gh_request:call:1',
    ),
    'budget_contention': (
        'main.py:Queue._gh_request:call:2',
        'main.py:Queue._gh_request:call:4',
        'main.py:Queue._gh_request.observe:call:1',
    ),
    'spacing_pause': (
        'main.py:Queue._gh_request:while:1',
        'main.py:Queue._gh_request:call:3',
    ),
    'pagination_cap': (
        'main.py:Queue.api_pages:for:1',
    ),
    'provider_merge_deadline': (
        'main.py:Queue._tick_repo:call:2',
    ),
    'mergeability_deadline': (
        'main.py:Queue._tick_repo:call:3',
    ),
    'cancel_service': (
        'main.py:Queue.run:while:1',
        'main.py:Queue.run:while:2',
        'main.py:Queue.run:call:1',
    ),
    'installer_timeout': (
        'main.py:Queue.install_agent:call:4',
        'ops/config-as-code.py:launchd_registration:call:1',
        'ops/config-as-code.py:install_launchd_plist:call:1',
        'lib/launchd_hold.py:activate:call:1',
    ),
}
EFFECT_FAULT_SITES = {
    'pr_read': (
        'main.py:Queue._gh_request:call:5',
        'main.py:Queue.api:call:1',
        'main.py:Queue.pr:call:1',
        'main.py:Queue.archive_legacy:call:1',
    ),
    'approval': (
        'main.py:Queue.api_pages:call:1',
        'main.py:Queue.pages:call:1',
        'main.py:Queue.approval:call:1',
    ),
    'check_runs': (
        'main.py:Queue.green:call:1',
    ),
    'statuses': (
        'main.py:Queue.green:call:2',
    ),
    'required_checks': (
        'main.py:Queue.green:call:3',
    ),
    'update': (
        'main.py:Queue.action:call:1',
    ),
    'retarget': (
        'main.py:Queue.action:call:2',
    ),
    'stamp': (
        'main.py:Queue._tick_repo:call:4',
    ),
    'ready': (
        'main.py:Queue._tick_repo:call:5',
    ),
    'merge': (
        'main.py:Queue._tick_repo:call:6',
    ),
    'merge_intent': (
        'main.py:Queue.merge_pending:call:1',
    ),
    'open_prs': (
        'main.py:Queue.discover:call:1',
        'main.py:Queue.refresh:call:1',
        'main.py:Queue.migrate_holds:call:1',
    ),
    'files': (
        'main.py:Queue.migrate_holds:call:2',
    ),
    'create_label': (
        'main.py:Queue.migrate_holds:call:3',
    ),
    'apply_label': (
        'main.py:Queue.migrate_holds:call:4',
    ),
    'fetch': (
        'main.py:Queue.fetch:call:1',
        'main.py:Queue.fetch:call:2',
    ),
    'board': (
        'main.py:Queue.flush_events:call:1',
        'main.py:Queue.flush_events:call:2',
    ),
}
DISPATCH_FAULT_SITES = {
    'ps_timeout': (
        'main.py:Queue._dispatch_conflicts:call:3',
        'main.py:Queue.dispatcher_identity:call:1',
    ),
    'help_timeout': (
        'main.py:Queue._dispatch_conflicts:call:4',
    ),
    'spawn_failure': (
        'main.py:Queue._dispatch_conflicts:call:5',
    ),
    'unreapable_child': (
        'main.py:Queue.expire_dispatch:call:1',
        'main.py:Queue.expire_dispatch:call:2',
        'main.py:Queue.expire_dispatch:call:3',
        'main.py:Queue.expire_dispatch:call:4',
        'main.py:Queue.expire_dispatch:call:5',
    ),
    'completed_child': (
        'main.py:Queue._dispatch_conflicts:call:1',
        'main.py:Queue._dispatch_conflicts:call:2',
    ),
    'live': (),
}
GIT_FAULT_SITES = {
    'ancestry': (
        'main.py:Queue.behind:call:1',
    ),
    'conflict': (
        'main.py:Queue.conflict:call:1',
    ),
    'patch_base': (
        'main.py:Queue.patch:call:1',
    ),
    'patch_diff': (
        'main.py:Queue.patch:call:2',
    ),
    'patch_id': (
        'main.py:Queue.patch:call:3',
    ),
    'git_init': (
        'main.py:Queue.git:call:1',
    ),
    'git_remote': (
        'main.py:Queue.git:call:3',
    ),
    'git_remote_read': (
        'main.py:Queue.git:call:2',
    ),
    'merge_confirmation': (
        'main.py:Queue.git:call:4',
        'main.py:Queue._tick_repo:call:1',
    ),
}


def record_fault(scenario, site_ids=None):
    for site_id in FAULT_SITES.get(scenario, ()) if site_ids is None else site_ids:
        assert site_id in mq.WAIT_SITES, 'stale fault identity: ' + site_id
        EXERCISED.setdefault(site_id, set()).add(scenario)
    EXERCISED_BOUNDS.add(scenario)


def assert_fault_coverage(site_ids=None, exercised=None):
    exercised = EXERCISED if exercised is None else exercised
    for site_id in mq.WAIT_SITES if site_ids is None else site_ids:
        assert exercised.get(site_id), 'unexercised wait site: ' + site_id



def wait_sites(source, nodes=None):
    """Inventory loops and blocking transports, including comprehensions and child reaping."""
    sites = {}
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ('subprocess', 'time', 'fcntl', 'sqlite3'):
            raise AssertionError('blocking imports must retain their module name for the wait census')
        if isinstance(node, ast.Import) and any(a.asname and a.name in ('subprocess', 'time', 'fcntl', 'sqlite3') for a in node.names):
            raise AssertionError('blocking module aliases bypass the wait census')
    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.context = []
            self.counts = Counter()
        def visit_ClassDef(self, node):
            self.context.append(node.name); self.generic_visit(node); self.context.pop()
        def visit_FunctionDef(self, node):
            self.context.append(node.name); self.generic_visit(node); self.context.pop()
        def add(self, kind, node, policy, shape):
            owner = '.'.join(self.context)
            self.counts[(owner, kind)] += 1
            identity = f'{owner}:{kind}:{self.counts[(owner, kind)]}'
            sites[identity] = {'bound': policy, 'shape': shape}
            if nodes is not None: nodes[identity] = node
        def loop(self, node, kind, iterator):
            if self.context[-1:] == ['bounded_items']:
                policy = 'snapshot'
            else:
                assert isinstance(iterator, ast.Call) and ast.unparse(iterator.func) == 'bounded_items', 'unbounded source loop'
                policy = ast.literal_eval(iterator.args[0])
            self.add(kind, node, policy, ast.unparse(iterator))
        def visit_For(self, node):
            self.loop(node, 'for', node.iter); self.generic_visit(node)
        def visit_comprehension(self, node):
            self.loop(node, 'comprehension', node.iter); self.generic_visit(node)
        def visit_While(self, node):
            owner = '.'.join(self.context)
            if owner == 'Queue._gh_request':
                assert ast.unparse(node.test) == 'time.monotonic() < end', 'spacing loop lost its deadline'
                policy = 'spacing'
            elif owner == 'Queue.run':
                assert ast.unparse(node.test) in ('not self.stopped', 'not self.stopped and time.monotonic() < end'), 'poll loop lost its deadline'
                policy = 'service' if ast.unparse(node.test) == 'not self.stopped' else 'poll'
            else: raise AssertionError('unbounded source while loop: ' + owner)
            self.add('while', node, policy, ast.unparse(node.test)); self.generic_visit(node)
        def visit_Call(self, node):
            name = ast.unparse(node.func)
            policy = None
            if name in ('command', 'subprocess.run', 'self.git', 'self.gh', 'self.api', 'self.pages', 'self.api_pages'):
                policy = 'board' if 'progress_board.py' in ast.unparse(node) else 'command'
            elif name == "config['install_launchd_plist']":
                policy = 'installer'
                assert any(k.arg == 'timeout' and ast.unparse(k.value) == "BOUNDS['installer']['seconds']" for k in node.keywords)
            elif name == 'subprocess.Popen' or name == 'child.poll': policy = 'dispatch_process'
            elif name in ('child.wait', 'child.terminate', 'child.kill'): policy = 'child_reap'
            elif name == 'sqlite3.connect': policy = 'sqlite'
            elif name == 'fcntl.flock': policy = 'runner_lock'
            elif name == 'self.budget.call_slot':
                policy = 'pacing_lock'
                assert any(k.arg == 'timeout' and ast.unparse(k.value) == "BOUNDS['pacing_lock']['seconds']" for k in node.keywords)
            elif name.startswith('self.budget.'): policy = 'budget_lock'
            elif name == 'self.wait': policy = ast.literal_eval(node.args[1])
            elif name == 'self.bounded_operation': policy = 'retry'
            elif name == 'time.sleep':
                assert '.'.join(self.context) in ('Queue._gh_request','Queue.run'), 'sleep has no deadline owner'
                assert ast.unparse(node.args[0]) == 'min(0.2, max(0, end - time.monotonic()))', 'sleep can exceed its deadline'
                policy = 'spacing' if self.context[-1] == '_gh_request' else 'poll'
            elif name in ('os.system', 'os.popen', 'subprocess.call', 'subprocess.check_call', 'subprocess.check_output') or name.endswith(('.wait', '.join', '.acquire', '.sleep', '.flock', '.communicate', '.recv', '.sendall', '.urlopen')):
                # str.join is a pure collection operation, not a thread wait.
                if name == 'shlex.join' or name.endswith('.join') and isinstance(node.func.value, ast.Constant):
                    pass
                else:
                    raise AssertionError('unregistered blocking transport: ' + name)
            if policy:
                self.add('call', node, policy, name)
                if name in ('subprocess.run', 'child.wait'):
                    timeout = next((k.value for k in node.keywords if k.arg == 'timeout'), None)
                    assert timeout is not None, 'transport has no timeout: ' + name
                    expression = ast.unparse(timeout)
                    if '.'.join(self.context) == 'command':
                        assert expression == 'timeout'
                    else:
                        bound = 'child_reap' if name == 'child.wait' else 'command'
                        assert expression == f"BOUNDS['{bound}']['seconds']", 'transport timeout bypasses registry'
                if name == 'fcntl.flock':
                    assert 'LOCK_NB' in ast.unparse(node), 'blocking runner lock'
            self.generic_visit(node)
    Visitor().visit(tree)
    return sites


def validate_wait_registry(directory, sites=None):
    expected = mq.WAIT_SITES if sites is None else sites
    observed = {}
    for path in sorted(directory.rglob('*.py')):
        observed.update({f'{path.relative_to(directory)}:{k}': v for k, v in wait_sites(path.read_text()).items()})
    for relative, owner in (('ops/config-as-code.py', 'launchd_registration'),
                            ('ops/config-as-code.py', 'install_launchd_plist'),
                            ('lib/launchd_hold.py', 'activate')):
        tree = ast.parse((ROOT / relative).read_text())
        method = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == owner)
        if owner == 'install_launchd_plist':
            for call in ast.walk(method):
                if isinstance(call, ast.Call) and ast.unparse(call.func) in ('launchd_registration', 'launchd_hold.activate'):
                    assert any(k.arg == 'timeout' and ast.unparse(k.value) == 'timeout' for k in call.keywords), 'installer helper lost its timeout'
        calls = [n for n in ast.walk(method) if isinstance(n, ast.Call) and ast.unparse(n.func) == 'subprocess.run']
        for index, node in enumerate(calls, 1):
            timeout = next((k.value for k in node.keywords if k.arg == 'timeout'), None)
            assert timeout is not None, 'delegated transport has no timeout: ' + relative
            assert ast.unparse(timeout) == 'timeout', 'installer must propagate its declared timeout'
            defaults = dict(zip((arg.arg for arg in method.args.kwonlyargs), method.args.kw_defaults))
            assert ast.literal_eval(defaults['timeout']) == mq.BOUNDS['installer']['seconds']
            observed[f'{relative}:{owner}:call:{index}'] = {'bound': 'installer', 'shape': 'subprocess.run'}
    declared = {k: {field: row[field] for field in ('bound', 'shape')} for k,row in expected.items()}
    assert observed == declared, f'wait sites differ: missing={set(observed)-set(expected)}, stale={set(expected)-set(observed)}'
    assert all(row['bound'] in mq.BOUNDS for row in expected.values())
    assert all(row.get('scenario') for row in expected.values()), 'wait site has no fault scenario'
    for name,bound in mq.BOUNDS.items():
        if name == 'service':
            assert bound.get('until') == 'cancelled'
        else:
            values = [bound[k] for k in ('attempts','seconds') if k in bound]
            assert len(values) == 1 and type(values[0]) in (int,float) and 0 < values[0] < float('inf'), 'invalid wait bound: ' + name


@dataclass(frozen=True)
class Effect:
    name: str
    sites: tuple
    scenario: str = 'normal'


EFFECTS = (
    Effect('pr_read', (('pr', 'api'),)),
    Effect('approval', (('approval', 'pages'),)),
    Effect('check_runs', (('green', 'api_pages'),)),
    Effect('statuses', (('green', 'pages'),)),
    Effect('required_checks', (('green', 'gh'),)),
    Effect('update', (('action', 'api'),), 'behind'),
    Effect('retarget', (('action', 'gh'),), 'retarget'),
    Effect('stamp', (('_tick_repo', 'api'),)),
    Effect('ready', (('_tick_repo', 'gh'),), 'draft'),
    Effect('merge', (('_tick_repo', 'gh'),)),
    Effect('merge_intent', (('merge_pending', 'api'),), 'rejected'),
    Effect('open_prs', (('discover', 'pages'), ('refresh', 'pages'), ('migrate_holds', 'pages'))),
    Effect('files', (('migrate_holds', 'pages'),), 'migration'),
    Effect('create_label', (('migrate_holds', 'gh'),), 'migration'),
    Effect('apply_label', (('migrate_holds', 'api'),), 'migration'),
    Effect('fetch', (('fetch', 'git'),), 'fetch'),
    Effect('board', (('flush_events', 'command'),)),
)
DOT_AUTHOR_EFFECT = Effect('dot_authors', (('approval', 'pages'),), 'dot_author')


def census(source):
    """Each external call site must belong to an exercised fault-table row."""
    calls = Counter()
    tree = ast.parse(source)
    for method in ast.walk(tree):
        if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(method):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == 'self':
                if func.attr in ('gh', 'api', 'pages', 'api_pages') and method.name not in ('api', 'pages', 'api_pages'):
                    calls[(method.name, func.attr)] += 1
                elif func.attr == 'git' and len(node.args) > 1 and isinstance(node.args[1], ast.Constant) and node.args[1].value == 'fetch':
                    calls[(method.name, 'git')] += 1
            elif isinstance(func, ast.Name) and func.id == 'command' and 'progress_board.py' in ast.unparse(node):
                calls[(method.name, 'command')] += 1
    return calls


def transports(source):
    calls = Counter()
    for method in ast.walk(ast.parse(source)):
        if not isinstance(method, ast.FunctionDef):
            continue
        for node in ast.walk(method):
            if not isinstance(node, ast.Call):
                continue
            name = ast.unparse(node.func)
            if name in ('command', 'subprocess.run', 'subprocess.Popen', "config['install_launchd_plist']"):
                calls[(method.name, name)] += 1
            elif name == 'self.git':
                calls[(method.name, 'git:' + ast.unparse(node.args[1]))] += 1
    return calls


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
    if len(argv) > 1 and argv[1].endswith('/tools/progress_board.py'):
        return 'board', None, None
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
            return '' if op[0] == 'board' else self.commands(argv, **kwargs)
        a = argv[1:]
        self.f.data['calls'].append(a)
        name, repo, n = op
        p = self.f.data['prs'].get(f'{repo}#{n}')
        if name == 'pr_read':
            result = p
        elif name == 'open_prs':
            result = [p for p in self.f.data['prs'].values() if p['repo'] == repo and p['state'] == 'open']
        elif name == 'approval':
            result = [dict(c, id=i+1) for i, c in enumerate(self.f.data['comments'][f'{repo}#{n}'])]
        elif name == 'dot_authors':
            result = [{'author': {'login': 'commit-author'}}]
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
        elif name == 'stamp':
            self.f.data['comments'][f'{repo}#{n}'].append({'body': next(x[5:] for x in a if x.startswith('body=')), 'author_association': 'OWNER'})
            result = {'id': 100}
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
            assert n not in (3, 4, 5, 6), 'unapproved or held head merged'
            assert head in (self.f.approved, self.f.updated), 'unapproved patch merged'
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
        if scenario == 'dot_author':
            body = f'APPROVE\nReviewed-SHA: {f.approved}\nReviewer: ChatGPT Dot'
            f.data['comments'][mq.REPOS[0]+'#1'] = [{'body':body, 'author_association':'OWNER'}]
            f.save()
            f.receipt({'repo':mq.REPOS[0], 'pr':1, 'sha':f.approved}, body,
                      reviewer='dot-user', relay_run_id='author-timeout-fixture')
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
    def test_dot_commit_author_timeout_refuses_approval(self):
        f = fixture_module.QueueTests(); f.setUp()
        try:
            loop = RunLoop(f, DOT_AUTHOR_EFFECT, 'timeout')
            loop.seed()
            loop.session()
            self.assertGreater(loop.failures, 0)
            self.assertNotIn((mq.REPOS[0], 1), {(repo,n) for repo,n,_ in loop.merged})
            self.assertTrue({(repo,2) for repo in mq.REPOS} <= {(repo,n) for repo,n,_ in loop.merged})
            f.restart()
            self.assertEqual(f.q.db.execute('SELECT phase FROM entries WHERE repo=? AND pr=1',
                                           (mq.REPOS[0],)).fetchone()[0], 'review')
            self.assertTrue(f.q.db.execute("SELECT 1 FROM events WHERE repo=? AND pr=1 AND outcome='fresh_review'",
                                          (mq.REPOS[0],)).fetchone())
        finally:
            f.doCleanups()
        record_fault('dot_author_timeout')

    def test_wait_registry_is_complete_and_rejects_a_missing_site(self):
        validate_wait_registry(ROOT / 'tools/merge_queue')
        sites = dict(mq.WAIT_SITES)
        sites.pop(next(iter(sites)))
        with self.assertRaisesRegex(AssertionError, 'wait sites'):
            validate_wait_registry(ROOT / 'tools/merge_queue', sites=sites)
        for source in ('def wait():\n    while True: pass\n',
                       'import time\ndef wait():\n    time.sleep(999999)\n',
                       'from time import sleep\ndef wait():\n    sleep(999999)\n',
                       'def wait():\n    subprocess.run(["slow"])\n'):
            with self.assertRaises(AssertionError):
                wait_sites(source)

    def test_review_handoff_faults_preserve_review_and_allow_recovery(self):
        for fault in (OSError('unavailable'), subprocess.TimeoutExpired('submit', 120), RuntimeError('failed')):
            f = fixture_module.QueueTests()
            f.setUp()
            try:
                router = f.root / 'bin/dot-review.py'
                router.parent.mkdir(exist_ok=True)
                router.write_text('# fixture')
                f.pr()
                entry = f.q.enqueue(mq.REPOS[0], 1, f.approved)
                e = f.q.db.execute('SELECT * FROM entries WHERE id=?', (entry,)).fetchone()
                with patch.object(mq, 'command', side_effect=fault):
                    f.q.report(e, 'fresh_review', 'head changed', 'review')
                self.assertEqual(f.q.db.execute('SELECT phase FROM entries WHERE id=?', (entry,)).fetchone()[0], 'review')
                f.restart()
                with patch.object(mq, 'command', return_value='submitted durable request') as transport:
                    f.q.request_review(mq.REPOS[0], 1)
                self.assertEqual(transport.call_args.kwargs['timeout'], mq.BOUNDS['command']['seconds'])
                self.assertEqual(transport.call_args.args[0][-3:], ['submit', mq.REPOS[0], '1'])
                self.assertEqual(f.q.db.execute('SELECT outcome FROM events ORDER BY id DESC LIMIT 1').fetchone()[0], 'review_handoff')
            finally:
                f.doCleanups()
        record_fault('review_handoff')

    def test_fault_table_covers_external_call_sites(self):
        source = '\n'.join(p.read_text() for p in sorted((ROOT / 'tools/merge_queue').rglob('*.py')))
        expected = Counter(site for effect in (*EFFECTS, DOT_AUTHOR_EFFECT) for site in effect.sites)
        self.assertEqual(census(source), expected,
                         'New external call sites require a fault-table entry and run-loop scenario')
        added = source + '\ndef new_effect(self):\n    self.api("new/external/path")\n'
        self.assertNotEqual(census(added), expected)
        known_transports = Counter({
            ('command', 'subprocess.run'): 1, ('_gh_request', 'command'): 1, ('request_review', 'command'): 1,
            ('flush_events', 'command'): 1, ('git', 'command'): 3, ('git', 'subprocess.run'): 1,
            ('patch', 'command'): 1, ('conflict', 'subprocess.run'): 1,
            ('dispatcher_identity', 'command'): 1, ('_dispatch_conflicts', 'command'): 2,
            ('_dispatch_conflicts', 'subprocess.Popen'): 1, ('behind', 'subprocess.run'): 1,
            ('install_agent', 'command'): 3, ('install_agent', "config['install_launchd_plist']"): 1,
            ('fetch', "git:'fetch'"): 1, ('patch', "git:'diff'"): 1,
            ('patch', "git:'merge-base'"): 1, ('_tick_repo', "git:'merge-base'"): 1,
            ('archive_legacy', "git:'merge-base'"): 1,
        })
        self.assertEqual(transports(source), known_transports,
                         'New subprocess routes must be classified and assigned a fault scenario')
        direct = source + '\ndef bypass(self):\n    command(["gh", "api", "new/path"])\n'
        self.assertNotEqual(transports(direct), known_transports)

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
            record_fault('failed_discovery')
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
                            loop.session()
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
                        if effect.name == 'board':
                            self.assertIn('exhaust', (f.state / 'queue.log').read_text().lower())
                        self.assertGreaterEqual(loop.discoveries, 4)
                        self.assertGreaterEqual(loop.polls, 4)
                        record_fault(effect.name + "_" + fault, EFFECT_FAULT_SITES[effect.name])
                    finally:
                        f.doCleanups()

        record_fault('persistent_effects')

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
                    record_fault(scenario)
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

    def test_bounded_transports_and_registry_caps(self):
        with self.assertRaises(mq.WaitExpired):
            list(mq.bounded_items('snapshot', range(mq.BOUNDS['snapshot']['attempts'] + 1)))
        nodes = {}
        wait_sites((ROOT / 'tools/merge_queue/main.py').read_text(), nodes)
        for site_id in FAULT_SITES['snapshot_cap']:
            with self.subTest(site_id=site_id):
                node = nodes[site_id.removeprefix('main.py:')]
                iterator = node.iter
                # Exercise the literal policy at this source site with an oversized input.
                policy = 'snapshot' if site_id == 'main.py:bounded_items:for:1' else ast.literal_eval(iterator.args[0])
                with self.assertRaises(mq.WaitExpired):
                    list(mq.bounded_items(policy, range(mq.BOUNDS[policy]['attempts'] + 1)))
                record_fault('snapshot_cap', (site_id,))
        with self.assertRaises(mq.WaitExpired):
            mq.command([sys.executable, '-c', 'import time; time.sleep(20)'], timeout=.02)
        record_fault('transport_timeout')
        f = fixture_module.QueueTests(); f.setUp()
        try:
            with (f.state / 'agent.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):
                    with f.q.runner_lock(): pass
            record_fault('runner_contention')
            connection = sqlite3.connect(f.state / 'queue.sqlite3')
            try:
                connection.execute('BEGIN IMMEDIATE')
                f.q.db.execute('PRAGMA busy_timeout=10')
                with self.assertRaises(sqlite3.OperationalError):
                    f.q.enqueue(mq.REPOS[0], 9, f.approved)
            finally: connection.close()
            record_fault('sqlite_contention')
            delay = mq.BOUNDS['spacing']['seconds'] + 1
            with patch.object(f.q.budget, 'reserve', return_value=(f.q.budget.clock() + delay, delay)):
                with self.assertRaises(mq.WaitExpired): f.q.pr(mq.REPOS[0], 1)
            record_fault('spacing_pause')
            pacing = Path(str(f.q.budget.path) + '.call.lock')
            with pacing.open('a') as lock, patch.dict(mq.BOUNDS['pacing_lock'], seconds=.02):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(mq.WaitExpired):
                    f.q.api('repos/example/repo')
            record_fault('pacing_contention')
            page = [{'id': i, 'body': '', 'author_association': 'OWNER'} for i in range(100)]
            def full_page(*args, validate):
                validate(page)
                return page
            with patch.object(f.q, 'api', side_effect=full_page):
                with self.assertRaisesRegex(mq.WaitExpired, 'pagination'):
                    f.q.pages('repos/example/repo/issues/1/comments?per_page=100')
            record_fault('pagination_cap')
            f.pr(); entry = f.q.enqueue(mq.REPOS[0], 1, f.approved)
            with f.q.db:
                f.q.db.execute("UPDATE entries SET phase='review',auto_attempts=? WHERE id=?", (mq.AUTO_ENQUEUE_CAP, entry))
            loop = RunLoop(f, EFFECTS[0], 'error'); loop.active = False
            loop.session()
            self.assertEqual(f.q.db.execute('SELECT phase FROM entries WHERE id=?', (entry,)).fetchone()[0], 'review')
            self.assertIsNotNone(f.q.db.execute("SELECT 1 FROM events WHERE outcome='auto_enqueue_exhausted'").fetchone())
            record_fault('reenqueue_cap')
        finally: f.doCleanups()

    def test_pagination_cap_stops_the_entry_and_releases_next_pr_in_the_real_loop(self):
        f = fixture_module.QueueTests(); f.setUp()
        try:
            loop = RunLoop(f, EFFECTS[0], 'error'); loop.active = False
            entry = loop.seed()
            original_request = loop.request
            calls = []
            page = [{'id': i, 'body': '', 'author_association': 'OWNER'} for i in range(100)]
            def request(argv, **kwargs):
                if operation(argv) == ('approval', mq.REPOS[0], 1):
                    calls.append(argv)
                    return 'HTTP/2.0 200 OK\nLink: <next>; rel="next"\n\n' + json.dumps(page)
                return original_request(argv, **kwargs)
            loop.request = request
            with patch.dict(mq.BOUNDS['pages'], attempts=2):
                loop.session(polls=1, reconcile=False)
                row = f.q.db.execute('SELECT phase,outcome FROM entries WHERE id=?', (entry,)).fetchone()
                self.assertEqual(tuple(row), ('blocked', 'wait_exhausted'))
                self.assertIn((mq.REPOS[0], 2), {(r,n) for r,n,_ in loop.merged})
                f.restart()
                loop.session(polls=1, reconcile=False)
                self.assertEqual(f.q.db.execute('SELECT phase FROM entries WHERE id=?', (entry,)).fetchone()[0], 'blocked')
                self.assertNotIn((mq.REPOS[0], 1), {(r,n) for r,n,_ in loop.merged})
                self.assertLessEqual(len(calls), 4, 'pagination exceeded the entry and discovery caps')
            self.assertIsNotNone(f.q.db.execute("SELECT 1 FROM events WHERE entry_id=? AND detail LIKE '%pagination limit%'", (entry,)).fetchone())
            record_fault('pagination_cap')
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
            record_fault('budget_contention'); record_fault('cancel_service')
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
                    if kind=='no_desk': record_fault('dispatch_no_desk')
                    else: record_fault('dispatch_' + kind, DISPATCH_FAULT_SITES[kind])
                finally: f.doCleanups()
        record_fault('dispatch_process_faults')

    def test_git_transport_fault_routes_drive_the_real_loop(self):
        for route in ('ancestry', 'conflict', 'patch_base', 'patch_diff', 'patch_id', 'git_init', 'git_remote', 'git_remote_read', 'merge_confirmation'):
            with self.subTest(route=route):
                f=fixture_module.QueueTests(); f.setUp()
                try:
                    loop=RunLoop(f,EFFECTS[0],'error'); loop.active=False; entry=loop.seed()
                    p=f.data['prs'][mq.REPOS[0]+'#1']
                    if route=='ancestry': p.update(draft=True,mergeable_state='blocked')
                    elif route=='conflict': p.update(mergeable=False,mergeable_state='dirty')
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
                        hit=(route=='ancestry' and '--is-ancestor' in argv and 'origin/main' in argv and argv[-1]==f.approved or
                             route=='conflict' and 'merge-tree' in argv or
                             route=='patch_base' and 'merge-base' in argv and '--is-ancestor' not in argv or
                             route=='patch_diff' and 'diff' in argv or route=='patch_id' and 'patch-id' in argv or
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
                    record_fault('git_' + route + '_timeout', GIT_FAULT_SITES[route])
                    row=f.q.db.execute('SELECT phase,outcome FROM entries WHERE id=?',(entry,)).fetchone()
                    self.assertEqual(row['phase'],'blocked')
                    self.assertIn((mq.REPOS[0],2),{(r,n) for r,n,_ in loop.merged})
                finally: f.doCleanups()


    def test_ancestry_fault_removal_is_rejected(self):
        import inspect
        import textwrap
        source = textwrap.dedent(inspect.getsource(type(self).test_git_transport_fault_routes_drive_the_real_loop))
        mutated = source.replace("('ancestry', 'conflict',", "('conflict',")
        self.assertNotEqual(source, mutated)
        namespace = dict(globals())
        exec(compile(mutated, __file__, 'exec'), namespace)
        exercised = {}
        with patch.dict(globals(), EXERCISED=exercised):
            namespace['test_git_transport_fault_routes_drive_the_real_loop'](self)
        git_sites = [site for sites in GIT_FAULT_SITES.values() for site in sites]
        with self.assertRaisesRegex(AssertionError, 'unexercised wait site: main.py:Queue.behind:call:1'):
            assert_fault_coverage(git_sites, exercised)
        self.assertNotIn('main.py:Queue.behind:call:1', exercised)

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
            record_fault('installer_timeout', FAULT_SITES['installer_timeout'][1:])
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
                record_fault('installer_timeout', (FAULT_SITES['installer_timeout'][0],))
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
                    record_fault('installer_git_timeout', ('main.py:Queue.install_agent:call:' + str(index),))
            finally: f.doCleanups()

    def test_z_every_registry_bound_has_an_exercised_fault_scenario(self):
        assert_fault_coverage()
        for site_id in mq.WAIT_SITES:
            with self.subTest(site_id=site_id):
                removed = {key: value for key, value in EXERCISED.items() if key != site_id}
                with self.assertRaisesRegex(AssertionError, 'unexercised wait site'):
                    assert_fault_coverage(exercised=removed)
        for name, bound in mq.BOUNDS.items():
            self.assertIn(bound['scenario'], EXERCISED_BOUNDS, 'unexercised bound: ' + name)


if __name__ == '__main__':
    unittest.main()
