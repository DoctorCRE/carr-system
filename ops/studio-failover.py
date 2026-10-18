#!/usr/bin/env python3
"""Manual failover, failback and monthly dry-run rehearsal.

Default is dry-run. --apply must run from canonical main. No credentials are copied.
The restore/ingress evidence file is ~/.config/carr/failover-prerequisites.json:
{host, source_sha, verified_at, state: {label: {paths: {path: sha256}}},
 manual: {role_name: {status: "passed", evidence: "receipt path or reference"}}}.
Directory state requires a hash of its sorted file-name/content-hash pairs.
Power fencing is a manual decision, supplied by --power-fence with a receipt:
{kind: "powered-off", source, target, target_sha, verified_at, keep_off_until_failback: true}.
source_sha is optional provenance; a powered-off source may have an older revision.
SSH failure alone never proves fencing. A rehearsal never invokes apply or copies state.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import plistlib
import shutil
import socket
import subprocess
import sys
import uuid
from types import SimpleNamespace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'ops'))
from git_env import scrubbed_env
from lib.machine_role import write_marker
from lib.studio_failover import Leader, connection, guarded_plist, plan, transfer, write_json as save
from lib.studio_failover_health import contract_hash


def command(argv, timeout=20):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=scrubbed_env())
    except (OSError, subprocess.SubprocessError) as exc:
        return SimpleNamespace(returncode=127, stdout='', stderr=type(exc).__name__)


def recent(value, hours):
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(value.replace('Z', '+00:00'))).total_seconds()
        return 0 <= age <= hours * 3600
    except (ValueError, TypeError, AttributeError): return False


def digest(path):
    if path.is_file(): return hashlib.sha256(path.read_bytes()).hexdigest()
    if path.is_dir():
        return hashlib.sha256(json.dumps([(str(p.relative_to(path)), digest(p))
            for p in sorted(path.rglob('*')) if p.is_file()], separators=(',', ':')).encode()).hexdigest()
    return None


class Host:
    def __init__(self, repo, config, target, power_fence=None):
        self.repo, self.config, self.target, self.power_fence = repo, config, target, power_fence
        self.home = Path.home()
        self.domain = 'gui/' + str(os.getuid())
        self.marker = self.home / '.config/carr/failover-host.json'
        self.sha = command(['git', '-C', str(repo), 'rev-parse', 'HEAD']).stdout.strip()
        self.fence_evidence = None

    def path(self, value):
        if value.startswith('~/carr-system'): return self.repo / value.removeprefix('~/carr-system/').removeprefix('~/carr-system')
        if value.startswith('~/'): return self.home / value[2:]
        return Path(value) if value.startswith('/') else self.repo / value

    def body(self, job, declaration=False):
        source = ROOT / job['source'] if declaration else self.path(job['source'])
        text = source.read_text()
        for token, value in [('{{REPO}}', str(self.repo)), ('{{HOME}}', str(self.home))]: text = text.replace(token, value)
        if '{{' in text: raise RuntimeError('unresolved plist token')
        return guarded_plist(text, self.repo, self.target, {j['label'] for j in self.config['jobs']})

    def snapshot(self):
        paths, errors = {}, []
        for job in self.config['jobs']:
            for value in [job['source'], *job.get('credentials', []), *job.get('state', [])]:
                paths[value] = self.path(value).exists()
            # Inspect the shipped declaration even before canonical deployment so
            # a missing template does not hide its executable prerequisites.
            if (ROOT / job['source']).exists():
                try:
                    p = plistlib.loads(self.body(job, declaration=True).encode())
                    job['executables'] = [a for a in p['ProgramArguments'] if a.startswith('/')]
                    for value in job['executables']: paths[value] = Path(value).exists()
                    cwd = p.get('WorkingDirectory')
                    if cwd and not Path(cwd).is_dir(): errors.append(job['label'] + ': working directory ' + cwd)
                    for value in job.get('credentials', []):
                        if paths[value] and self.path(value).stat().st_mode & 0o077:
                            errors.append(job['label'] + ': credential permissions ' + value)
                except Exception as exc: errors.append(job['label'] + ': plist ' + type(exc).__name__)
        branch = command(['git', '-C', str(self.repo), 'symbolic-ref', '--short', 'HEAD']).stdout.strip()
        common = command(['git', '-C', str(self.repo), 'rev-parse', '--path-format=absolute', '--git-common-dir']).stdout.strip()
        clean = not command(['git', '-C', str(self.repo), 'status', '--porcelain', '--untracked-files=no']).stdout.strip()
        canonical = branch == 'main' and Path(common).parent == self.repo and clean
        gui = command(['launchctl', 'print', self.domain]).returncode == 0
        owner, leader_ready = None, False
        try:
            leader = Leader(connection())
            try: owner = leader.read()[0]; leader_ready = True
            finally: leader.close()
        except Exception as exc: errors.append('leader prerequisite: ' + type(exc).__name__)
        try:
            authority = Leader(connection('authority'))
            try:
                authority.read()
                executable = authority.conn.execute("select has_function_privilege(current_user,"
                    "'ops.transfer_studio_leader(text,text,bigint,jsonb)','execute')").fetchone()[0]
                if not executable: raise RuntimeError('transfer function privilege missing')
            finally: authority.close()
        except Exception as exc:
            leader_ready = False
            errors.append('authority prerequisite: ' + type(exc).__name__)
        prerequisite_path = self.home / '.config/carr/failover-prerequisites.json'
        try: evidence = json.loads(prerequisite_path.read_text())
        except (OSError, ValueError): evidence = {}
        bound = evidence.get('host') == self.target and evidence.get('source_sha') == self.sha and recent(evidence.get('verified_at'), 24)
        state_verified = {}
        for job in self.config['jobs']:
            expected = evidence.get('state', {}).get(job['label'], {}).get('paths', {}) if bound else {}
            state_verified[job['label']] = all(expected.get(p) and expected[p] == digest(self.path(p)) for p in job.get('state', []))
        manual = {r['name']: bool(bound and evidence.get('manual', {}).get(r['name'], {}).get('status') == 'passed'
                                 and evidence['manual'][r['name']].get('evidence')) for r in self.config.get('manual_roles', [])}
        if not (self.home / '.config/carr/failover.env').exists(): errors.append('credential file: ~/.config/carr/failover.env')
        if socket.gethostname() != self.config['hosts'][self.target]['hostname']: errors.append('wrong target host')
        return {'host': self.target, 'paths': paths, 'canonical': canonical, 'gui': gui,
                'leader_ready': leader_ready, 'owner': owner, 'state_verified': state_verified,
                'manual_verified': manual, 'errors': errors}

    def demote(self):
        tasks = self.home / '.claude/scheduled-tasks'
        tracked = {p.stem.removesuffix('.SKILL') for p in (ROOT / 'ops/scheduled-tasks').glob('*.SKILL.md')}
        if any((tasks / name).exists() for name in tracked):
            # Removing a file cannot prove a running client's cached scheduler stopped.
            raise RuntimeError('source_has_native_scheduled_tasks; require manual powered-off fence receipt')
        save(self.marker, {'host': self.target, 'armed': False})
        write_marker('secondary')
        quarantine = self.home / '_to_delete/studio-failover' / uuid.uuid4().hex
        for job in self.config['jobs']:
            label = job['label']
            r = command(['launchctl', 'disable', self.domain + '/' + label])
            if r.returncode: raise RuntimeError('disable failed: ' + label)
            command(['launchctl', 'bootout', self.domain + '/' + label])
            if command(['launchctl', 'print', self.domain + '/' + label]).returncode == 0:
                raise RuntimeError('source job still registered: ' + label)
            path = self.home / 'Library/LaunchAgents' / (label + '.plist')
            if path.exists():
                quarantine.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(quarantine / path.name))
        return {'schema': 'carr-host-fence/v1', 'source': self.target, 'source_sha': self.sha,
                'verified_at': datetime.now(timezone.utc).isoformat(), 'armed': False,
                'unregistered': sorted(j['label'] for j in self.config['jobs'])}

    def abort(self):
        save(self.marker, {'host': self.target, 'armed': False})
        write_marker('secondary')
        failures = []
        for job in self.config['jobs']:
            if command(['launchctl', 'disable', self.domain + '/' + job['label']]).returncode:
                failures.append(job['label'] + ': disable failed')
            command(['launchctl', 'bootout', self.domain + '/' + job['label']])
            if command(['launchctl', 'print', self.domain + '/' + job['label']]).returncode == 0:
                failures.append(job['label'] + ': still registered')
        if failures: raise RuntimeError('abort incomplete; target disarmed; ' + '; '.join(failures))

    def fence(self, source):
        if self.power_fence:
            receipt = json.loads(self.power_fence.read_text())
            valid = (receipt.get('kind') == 'powered-off' and receipt.get('source') == source
                     and receipt.get('target') == self.target and receipt.get('target_sha') == self.sha
                     and receipt.get('keep_off_until_failback') is True and recent(receipt.get('verified_at'), 1))
            if valid: self.fence_evidence = receipt
            return valid
        alias = self.config['hosts'][source]['ssh']
        remote = "cd ~/carr-system && .venv/bin/python ops/studio-failover.py demote --target " + source + " --apply"
        r = command(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', alias, remote], timeout=120)
        if r.returncode: return False
        try: receipt = json.loads(r.stdout)
        except ValueError: return False
        valid = (receipt.get('source') == source and receipt.get('source_sha') == self.sha
                 and receipt.get('armed') is False and recent(receipt.get('verified_at'), 1)
                 and receipt.get('unregistered') == sorted(j['label'] for j in self.config['jobs']))
        if valid: self.fence_evidence = {**receipt, 'kind': 'demoted', 'target': self.target, 'target_sha': self.sha}
        return valid

    def claim(self, source, target):
        leader = Leader(connection('authority'))
        try: leader.claim(source, target, self.fence_evidence)
        finally: leader.close()

    def install(self):
        save(self.marker, {'host': self.target, 'armed': False})
        agents = self.home / 'Library/LaunchAgents'
        agents.mkdir(parents=True, exist_ok=True)
        for job in self.config['jobs']:
            command(['launchctl', 'bootout', self.domain + '/' + job['label']])
            if command(['launchctl', 'print', self.domain + '/' + job['label']]).returncode == 0:
                raise RuntimeError('existing job still registered: ' + job['label'])
            path = agents / (job['label'] + '.plist')
            desired = self.body(job)
            if path.exists() and path.read_text() != desired:
                quarantine = self.home / '_to_delete/studio-failover' / uuid.uuid4().hex
                quarantine.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(quarantine / path.name))
            path.write_text(desired)

    def start(self):
        save(self.marker, {'host': self.target, 'armed': True})
        write_marker('primary')
        for job in self.config['jobs']:
            label = job['label']
            command(['launchctl', 'bootout', self.domain + '/' + label])
            if not job.get('enabled', True):
                if command(['launchctl', 'disable', self.domain + '/' + label]).returncode:
                    raise RuntimeError('preserve disabled job failed: ' + label)
                continue
            for argv in (['launchctl', 'enable', self.domain + '/' + label],
                         ['launchctl', 'bootstrap', self.domain, str(self.home / 'Library/LaunchAgents' / (label + '.plist'))],
                         ['launchctl', 'kickstart', self.domain + '/' + label]):
                if command(argv).returncode: raise RuntimeError('launchd start failed: ' + label)

    def verify(self):
        leader = Leader(connection())
        try:
            if leader.read()[0] != self.target: return False
        finally: leader.close()
        for job in self.config['jobs']:
            path = self.home / 'Library/LaunchAgents' / (job['label'] + '.plist')
            if path.read_text() != self.body(job): return False
            observed = command(['launchctl', 'print', self.domain + '/' + job['label']])
            if not job.get('enabled', True):
                if observed.returncode == 0: return False
                continue
            if observed.returncode: return False
            definition = plistlib.loads(self.body(job).encode())
            if definition.get('KeepAlive') and 'state = running' not in observed.stdout: return False
            import re
            exited = re.search(r'last exit code = (-?\d+)', observed.stdout)
            if exited and int(exited[1]) != 0: return False
        return True


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['takeover', 'demote', 'rehearse', 'install-rehearsal'])
    p.add_argument('--target', choices=['studio', 'macbook'], default='macbook')
    g = p.add_mutually_exclusive_group()
    g.add_argument('--apply', action='store_true')
    g.add_argument('--dry-run', action='store_true')
    p.add_argument('--power-fence', type=Path)
    p.add_argument('--repo', type=Path, default=Path.home() / 'carr-system')
    args = p.parse_args()
    config = json.loads((ROOT / 'ops/config/studio-failover.v1.json').read_text())
    h = Host(args.repo.resolve(), config, args.target, args.power_fence)
    if args.mode == 'install-rehearsal':
        if socket.gethostname() != config['hosts']['macbook']['hostname']:
            raise RuntimeError('rehearsal_requires_macbook')
        source = ROOT / 'ops/launchd/com.carr.studio-failover-rehearsal.plist'
        body = source.read_text().replace('{{REPO}}', str(h.repo))
        path = h.home / 'Library/LaunchAgents' / source.name
        if args.apply:
            snap = h.snapshot()
            if ROOT != h.repo or not snap['canonical']: raise RuntimeError('apply_requires_clean_canonical_main')
            (h.repo / 'out/studio-failover').mkdir(parents=True, exist_ok=True)
            if path.exists() and path.read_text() != body:
                quarantine = h.home / '_to_delete/studio-failover' / uuid.uuid4().hex
                quarantine.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(quarantine / path.name))
            command(['launchctl', 'bootout', h.domain + '/com.carr.studio-failover-rehearsal'])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
            if command(['launchctl', 'enable', h.domain + '/com.carr.studio-failover-rehearsal']).returncode:
                raise RuntimeError('rehearsal_enable_failed')
            if command(['launchctl', 'bootstrap', h.domain, str(path)]).returncode:
                raise RuntimeError('rehearsal_bootstrap_failed')
        print(json.dumps({'action': 'install-rehearsal', 'path': str(path), 'applied': args.apply,
                          'cadence': 'monthly, day 5 at 10:15 local; dry-run only'}))
        return 0
    snap = h.snapshot()
    report = plan(config, snap, args.target)
    report['missing'] += snap['errors']
    report['ready'] = not report['missing']
    if args.apply:
        if args.mode == 'rehearse': raise RuntimeError('rehearsal_is_dry_run_only')
        if ROOT != h.repo or not snap['canonical']: raise RuntimeError('apply_requires_clean_canonical_main')
        if 'wrong target host' in snap['errors'] or not snap['gui']:
            raise RuntimeError('apply_requires_target_host_and_gui')
        if args.mode == 'demote':
            print(json.dumps(h.demote()))
            return 0
        if not report['ready']:
            print(json.dumps(report, indent=2)); return 2
        transfer(h, 'studio' if args.target == 'macbook' else 'macbook', args.target)
        print(json.dumps({'status': 'verified', 'target': args.target}))
        return 0
    report.update({'schema': 'carr-failover-rehearsal/v1', 'source_sha': h.sha,
                   'verified_at': datetime.now(timezone.utc).isoformat(), 'mode': 'dry-run',
                   'contract_hash': contract_hash(ROOT)})
    if args.mode == 'rehearse':
        folder = h.repo / 'out/studio-failover'
        previous = folder / 'latest.json'
        if previous.exists(): json.loads(previous.read_text())  # Read the prior ledger before a recurring run.
        save(folder / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json'), report)
        save(previous, report)
    print(json.dumps(report, indent=2))
    return 0 if report['ready'] else 2


if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception as exc:
        detail = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print('FAIL failover: ' + detail, file=sys.stderr)
        raise SystemExit(2)
