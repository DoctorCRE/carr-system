#!/usr/bin/env python3
"""Default-off, private same-source setup trials; never a CI verdict cache.

No provider cache is consumed or published. Trial stores are deleted on exit.
A favorable measurement only requests a full-lane proof; it cannot enable reuse.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time


class Refusal(RuntimeError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def command(argv, cwd, env, *, require_output=False, timeout=900):
    # Never propagate captured child text into a report or exception. Installers
    # can print URLs, npm configuration and lifecycle-script output.
    try:
        child = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, start_new_session=True)
    except OSError:
        raise Refusal('child unavailable or deadline exceeded; trial refused') from None
    try:
        stdout, _ = child.communicate(timeout=timeout)
    except BaseException as error:
        if isinstance(error, subprocess.TimeoutExpired):
            raise Refusal('child unavailable or deadline exceeded; trial refused') from None
        raise
    finally:
        # A parent can exit with redirected children still running. Cleanup
        # belongs to the process group on every path, including successful exit.
        dispose_group(child)
    if child.returncode:
        raise Refusal(f'child exited {child.returncode}; trial refused')
    if require_output:
        try:
            parsed = json.loads(stdout)
        except (ValueError, TypeError):
            raise Refusal('missing or partial JSON acknowledgement; trial refused') from None
        if not isinstance(parsed, (dict, list)):
            raise Refusal('invalid JSON acknowledgement; trial refused')
    return stdout


def dispose_group(child):
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.communicate()
    deadline = time.monotonic()+5
    while True:
        try:
            processes = subprocess.run(['ps','-axo','pgid=,stat='],capture_output=True,
                                       text=True,check=True,timeout=5).stdout.splitlines()
            active = any(int(fields[0]) == child.pid and not fields[1].startswith('Z')
                         for line in processes if len(fields := line.split()) == 2)
        except (OSError, ValueError, subprocess.SubprocessError):
            raise Refusal('owned process group disposal could not be verified') from None
        if not active:
            return
        if time.monotonic() >= deadline:
            raise Refusal('owned process group remains active; trial refused')
        time.sleep(.01)


def tree_manifest(root):
    if not root.is_dir() or root.is_symlink():
        raise Refusal('cache tree missing or not a directory')
    entries = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if path.name in ('PG_VERSION', 'postmaster.pid'):
            raise Refusal('mutable PostgreSQL clusters are not cacheable')
        if path.is_symlink():
            if not path.resolve().is_relative_to(root.resolve()) or not path.exists():
                raise Refusal('cache symlink leaves install root or is broken')
            entries[relative] = ['link', os.readlink(path)]
        elif path.is_file():
            entries[relative] = ['file', path.stat().st_mode & 0o777,
                                 hashlib.sha256(path.read_bytes()).hexdigest()]
        elif not path.is_dir():
            raise Refusal('special cache entry refused')
    if not entries:
        raise Refusal('empty cache refused')
    return entries


def save_tree(source, cache, identity):
    entries = tree_manifest(source)
    cache.mkdir(mode=0o700)
    shutil.copytree(source, cache/'tree', symlinks=True)
    if tree_manifest(cache/'tree') != entries:
        raise Refusal('cache save readback differs')
    (cache/'manifest.json').write_text(json.dumps({'identity': identity, 'entries': entries}))


def restore_tree(cache, target, identity):
    # Cache faults are misses, never partial installs. The destination must be
    # disposable and absent. Copies do not share writable files or hardlinks.
    if target.exists() or target.is_symlink():
        raise Refusal('restore destination must be absent')
    try:
        saved = json.loads((cache/'manifest.json').read_text())
        if saved['identity'] != identity or tree_manifest(cache/'tree') != saved['entries']:
            return False
        shutil.copytree(cache/'tree', target, symlinks=True)
        if tree_manifest(target) != saved['entries']:
            raise Refusal('restored bytes differ')
        return True
    except (OSError, ValueError, KeyError, TypeError, Refusal):
        if target.exists():
            shutil.rmtree(target)
        return False


def compare(baseline, candidate):
    """Strict paired acceptance; no missing observations count as improvements."""
    report = {'action': 'remove-candidate-cache', 'enable_reuse': False}
    if len(baseline) != len(candidate) or not baseline:
        return report
    identity_fields = {'os', 'architecture', 'runtime', 'installer', 'lock', 'package_path', 'source_tree'}
    expected_identity = baseline[0].get('identity') if isinstance(baseline[0], dict) else None
    digest_fields = ('dependency_digest', 'check_digest')
    expected_digests = tuple(baseline[0].get(name) for name in digest_fields) if isinstance(baseline[0], dict) else None
    for before, after in zip(baseline, candidate):
        if not isinstance(before, dict) or not isinstance(after, dict):
            return report
        if before.get('cold') != after.get('cold'):
            return report
        for row in (before, after):
            identity = row.get('identity')
            if (row.get('passed') is not True or type(row.get('cold')) is not bool
                    or not isinstance(identity, dict) or set(identity) != identity_fields
                    or any(not isinstance(value, str) or not value for value in identity.values())
                    or identity != expected_identity):
                return report
            for name in ('setup_seconds', 'job_seconds'):
                value = row.get(name)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                    return report
            if row['job_seconds'] < row['setup_seconds']:
                return report
            if (any(not isinstance(row.get(name), str) or not row[name] for name in digest_fields)
                    or tuple(row[name] for name in digest_fields) != expected_digests):
                return report
    metrics = {}
    improved = True
    for cold, label in ((True, 'cold'), (False, 'warm')):
        values = [[row['setup_seconds'] for row in rows if row['cold'] is cold]
                  for rows in (baseline, candidate)]
        if any(len(group) < 10 for group in values):
            return report
        p95 = [sorted(group)[math.ceil(len(group)*.95)-1] for group in values]
        metrics[label+'_setup_p95_seconds'] = dict(zip(('before', 'after'), p95))
        improved &= p95[1] < p95[0]
    totals = [sum(row['job_seconds'] for row in rows) for rows in (baseline,candidate)]
    metrics['total_job_seconds'] = dict(zip(('before', 'after'),totals))
    improved &= totals[1] < totals[0]
    report['metrics'] = metrics
    if improved:
        report['action'] = 'eligible-for-full-lane-proof'
    return report


def class_names(repo):
    return re.search(r'^CLASS_ORDER="([^"]+)"', (repo/'ops/ci.sh').read_text(), re.M).group(1).split()


def inventory(repo):
    # An explicit conservative inventory, NOT an omission selector. The gates
    # class discovers children dynamically; unknown transitive requirements
    # retain current setup until independently measured in the full lane.
    requirements = {
        'pushfloor': ['git', 'python', 'dynamic-paired-check-tools'],
        'unit': ['node', 'python', 'mcp-server', 'practice-plugin', 'chrome-launch-fixtures'],
        'types': ['python-lock', 'mypy'],
        'contract': ['python', 'node'],
        'gates': ['git', 'full-history', 'python-lock', 'node', 'zsh', 'postgres-17', 'dynamic-child-tools'],
        'secret': ['git', 'python'],
        'dependency': ['python-lock', 'node', 'both-node-locks'],
        'migration': ['git', 'full-history', 'historic-schema-blob', 'python-lock', 'node', 'postgres-17', 'role-bootstrap', 'disposable-cluster'],
        'binding': ['python', 'node'],
        'artifact': ['python', 'node', 'mcp-server'],
        'freshness': ['git', 'full-history', 'origin-main-merge-base'],
    }
    names = class_names(repo)
    if set(names) != set(requirements):
        raise Refusal('class inventory changed; review dependencies before another trial')
    sources = ['ops/ci.sh', 'ops/local-pg-ci.py', 'ops/stale-config-check.py',
               'ops/atomic-rule-compat-migration-gate.py', '.github/workflows/ci.yml',
               '.github/workflows/db-acceptance.yml', '.github/workflows/main-canary.yml']
    return {'classes': {name: {'retain': requirements[name], 'narrowing_proven': False} for name in names},
            'source_sha256': {p: hashlib.sha256((repo/p).read_bytes()).hexdigest() for p in sources},
            'removals': [], 'database_reuse': False,
            'schema_template': 'declined: no measured bootstrap saving; retain independent local-pg-ci clusters',
            'browser_store': 'not trialed: CARR has Chrome launch fixtures, no pinned Playwright download setup',
            'preinstalled_image': 'not trialed: require immutable image digest and full-lane parity first'}


def npm_dependencies(output):
    value = json.loads(output)
    # npm ls contains the absolute root path; do not persist that path.
    if not isinstance(value, dict) or not value.get('name') or not value.get('dependencies'):
        raise Refusal('npm dependency acknowledgement empty or incomplete')
    value.pop('path', None)
    return digest(value)


def check_npm(package, env):
    # Node 26 defaults to the spec reporter even with captured stdout. Force
    # TAP in the same npm test command, including its spawned Node children.
    output = command(['npm', 'test'], package, {**env, 'NODE_OPTIONS':'--test-reporter=tap'})
    return tap_digest(output)


def tap_digest(output):
    """Validate each Node TAP stream and its nested plans before hashing evidence."""
    def refuse():
        raise Refusal('test process returned failed or incomplete TAP acknowledgement')

    def scope():
        return {'count': 0, 'plan': None, 'ended': False}

    def complete(frame):
        if frame['plan'] is None or frame['plan'] != frame['count']:
            refuse()

    def complete_batch():
        if pending_subtests:
            refuse()
        for frame in stack:
            complete(frame)
        if (not {'tests','pass','fail','cancelled'} <= totals.keys()
                or totals['tests'] <= 0 or totals['pass'] <= 0
                or totals['fail'] or totals['cancelled']
                or totals['tests'] != assertion_count-suite_count
                or totals['tests'] != sum(totals.get(name,0) for name in ('pass','skipped','todo'))):
            refuse()

    stack = []; totals = {}; pending_subtests = set(); evidence = []
    assertion_count = suite_count = batches = 0
    last_assertion_indent = None
    for line in output.splitlines():
        if re.match(r'^\s*Bail out!',line,re.I) or re.match(r'^\s*not ok\b',line):
            refuse()
        if line == 'TAP version 13':
            if stack:
                complete_batch()
            stack = [scope()]; totals = {}; pending_subtests = set(); assertion_count = suite_count = 0
            last_assertion_indent = None; batches += 1
            continue
        assertion = re.match(r'^( *)(ok) (\d+)(?:\s|$)',line)
        plan = re.fullmatch(r'( *)1\.\.(\d+)(?:\s+#.*)?',line)
        summary = re.fullmatch(r'# (tests|pass|fail|cancelled|skipped|todo) (\d+)\s*',line)
        subtest = re.match(r'^( *)# Subtest:',line)
        if subtest:
            indent = len(subtest[1])
            if not stack or totals or indent % 4 or indent//4 in pending_subtests:
                refuse()
            pending_subtests.add(indent//4)
        if assertion or plan or summary:
            if not stack:
                refuse()
            evidence.append(line)
        if summary:
            name, count = summary.groups()
            if name in totals:
                refuse()
            totals[name] = int(count)
        elif assertion or plan:
            if totals:
                refuse()
            token = assertion or plan
            indent = len(token[1])
            if indent % 4 or indent//4 > len(stack):
                refuse()
            depth = indent//4
            while len(stack) > depth+1:
                complete(stack.pop())
            if depth == len(stack):
                stack.append(scope())
            frame = stack[-1]
            if assertion:
                if frame['ended'] or int(assertion[3]) != frame['count']+1:
                    refuse()
                frame['count'] += 1; assertion_count += 1
                pending_subtests.discard(depth)
                last_assertion_indent = indent
            else:
                if frame['plan'] is not None:
                    refuse()
                frame['plan'] = int(plan[2]); frame['ended'] = bool(frame['count'])
        elif re.fullmatch(r" *type: ['\"]suite['\"]",line):
            if last_assertion_indent is None or len(line)-len(line.lstrip()) != last_assertion_indent+2:
                refuse()
            suite_count += 1
            last_assertion_indent = None
    if not batches:
        refuse()
    complete_batch()
    return digest(sorted(evidence))


def trial_environment(env, trial):
    result = dict(env)
    for key, name in (('HOME', 'home'), ('XDG_CONFIG_HOME', 'config'), ('TMPDIR', 'tmp')):
        directory = trial/name
        directory.mkdir(parents=True, mode=0o700)
        result[key] = str(directory)
    return result


def benchmark(repo, repeats, target):
    env = {'PATH': os.environ.get('PATH',''), 'LANG':'C.UTF-8', 'LC_ALL':'C',
           'CI':'1', 'WRANGLER_SEND_METRICS':'false', 'F03_PARITY_REQUIRE_PYTHON':'1'}
    head = command(['git','rev-parse','HEAD'],repo,env).strip()
    source_tree = command(['git','rev-parse',head+'^{tree}'],repo,env).strip()
    if command(['git','status','--porcelain','--untracked-files=no'],repo,env).strip():
        raise Refusal('tracked source differs from HEAD; commit before measuring')
    with tempfile.TemporaryDirectory(prefix='carr-setup-shadow.') as temp:
        root = Path(temp); env.update(HOME=str(root/'home'), TMPDIR=str(root), XDG_CONFIG_HOME=str(root/'config'))
        (root/'home').mkdir()
        snapshot = root/'source'
        command(['git','clone','--quiet','--shared','--no-checkout',str(repo),str(snapshot)],root,env)
        command(['git','checkout','--quiet','--detach',head],snapshot,env)
        if command(['git','rev-parse','HEAD^{tree}'],snapshot,env).strip() != source_tree:
            raise Refusal('immutable source snapshot differs from reported tree')
        runtime = command(['node','--version'],snapshot,env).strip() if target != 'pip' else platform.python_version()
        installer = command(['npm','--version'],snapshot,env).strip() if target != 'pip' else None
        report = {'schema': 'carr-ci-setup-shadow/v1', 'shadow': True, 'enable_reuse': False,
                  'head': head, 'tree': source_tree, 'target':target, 'inventory':inventory(snapshot),
                  'check_scope': 'package npm test' if target != 'pip' else 'locked-distribution import smoke; full lane still required',
                  'cache_transport': 'private local copies including hash validation; hosted remote overhead NOT measured',
                  'trials': {mode: [] for mode in (('fresh','store','tree') if target != 'pip' else ('fresh','store'))}}
        for repetition in range(repeats):
            # Alternate treatment order; a warmed host must not systematically
            # favor the candidate that happens to run last.
            modes = list(report['trials'])
            if repetition % 2: modes.reverse()
            for mode in modes:
                rows = report['trials'][mode]
                cache = root/f'cache-{repetition}-{mode}'
                for cold in (True, False):
                    started = time.monotonic()
                    phases = {}
                    phase_start = started
                    def mark(name):
                        nonlocal phase_start
                        now = time.monotonic(); phases[name] = now-phase_start; phase_start = now
                    trial = root/f'trial-{repetition}-{mode}-{cold}'
                    command(['git','clone','--quiet','--shared','--no-checkout',str(snapshot),str(trial)],root,env)
                    command(['git','checkout','--quiet','--detach',head],trial,env)
                    check_env = trial_environment(env, trial)
                    store = trial/'store'
                    if target == 'pip':
                        venv = trial/'trial-venv'
                        command([sys.executable,'-m','venv',str(venv)],trial,check_env)
                        python = str(venv/'bin/python')
                        installer = command([python,'-m','pip','--version'],trial,check_env).split()[1]
                        lock = snapshot/'requirements.lock'; install_root=store
                    else:
                        lock = snapshot/target/'package-lock.json'; install_root=trial/target/'node_modules'
                    identity = {'os':platform.platform(), 'architecture':platform.machine(), 'runtime':runtime,
                                'installer':installer, 'lock':hashlib.sha256(lock.read_bytes()).hexdigest(),
                                'package_path':target, 'source_tree':source_tree}
                    mark('checkout_and_environment')
                    hit = False
                    if not cold and mode != 'fresh':
                        hit = restore_tree(cache, store if mode=='store' else install_root, identity)
                    mark('restore_and_validate_cache')
                    if target == 'pip':
                        command([python,'-m','pip','install','--disable-pip-version-check','--cache-dir',str(store),
                                 '-r','requirements.lock'],trial,check_env)
                    elif not hit or mode != 'tree':
                        command(['npm','--prefix',target,'ci','--cache',str(store),'--no-audit','--no-fund'],trial,check_env)
                    mark('locked_install')
                    # Always validate lock resolution after setup, including a
                    # restored installed tree. No restored passing verdict.
                    if target == 'pip':
                        dependency = digest(json.loads(command([python,'-m','pip','list','--format=json','--disable-pip-version-check'],trial,check_env,require_output=True)))
                        command([python,'-m','pip','check'],trial,check_env)
                    else:
                        dependency = npm_dependencies(command(['npm','--prefix',target,'ls','--all','--json'],trial,check_env,require_output=True))
                    mark('validate_dependencies')
                    if cold and mode != 'fresh':
                        save_tree(store if mode=='store' else install_root,cache,identity)
                    mark('save_and_validate_cache')
                    setup_seconds = time.monotonic()-started
                    if target == 'pip':
                        output = command([python,'-c',
                            "import json, psycopg, openpyxl, PIL, lxml.etree, pymupdf; print(json.dumps({'imports':'passed'}))"],trial,check_env,require_output=True)
                        check = digest(json.loads(output))
                    else:
                        check = check_npm(trial/target,check_env)
                    mark('checks')
                    shutil.rmtree(trial)
                    if not cold and cache.exists():
                        shutil.rmtree(cache)
                    mark('disposal')
                    rows.append({'cold':cold,'setup_seconds':setup_seconds,'job_seconds':time.monotonic()-started,
                                 'dependency_digest':dependency,'check_digest':check,'passed':True,'restore_hit':hit,
                                 'identity':identity, 'phase_seconds':phases})
                    print(f'shadow {target} {mode} {repetition+1} {"cold" if cold else "warm"}: complete',file=sys.stderr)
        report['comparisons']={mode:compare(report['trials']['fresh'],rows)
                               for mode,rows in report['trials'].items() if mode!='fresh'}
        report['cache_disposal']='all private stores removed on exit, including losing candidates'
        return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enabled',action='store_true')
    parser.add_argument('--inventory',action='store_true')
    parser.add_argument('--target',choices=['mcp-server','practice-plugin','pip'],default='mcp-server')
    parser.add_argument('--repeats',type=int,default=10)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(); repo=Path(__file__).resolve().parents[1]
    if args.inventory:
        print(json.dumps(inventory(repo),sort_keys=True,indent=2)); return 0
    if not args.enabled:
        print('CI setup experiment disabled (default); no setup or cache effects'); return 0
    if not 1 <= args.repeats <= 20 or args.output is None or args.output.exists() or args.output.is_symlink():
        parser.error('use 1..20 repeats and a new --output file; fewer than ten cannot qualify')
    try:
        report=benchmark(repo,args.repeats,args.target)
        # Exclusive publication refuses a destination created during the trial,
        # including dangling symlinks; it never follows or truncates that sink.
        payload = json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n'
        with args.output.open('x',encoding='utf-8') as output:
            output.write(payload)
    except OSError:
        print('output destination unavailable or occupied; report refused',file=sys.stderr); return 1
    except Refusal as error:
        print(str(error),file=sys.stderr); return 1
    return 0


if __name__ == '__main__': sys.exit(main())
