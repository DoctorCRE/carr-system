#!/usr/bin/env python3
"""Read host metadata. Credential files and launch arguments are never exported."""
import argparse
import json
import os
import plistlib
import socket
import subprocess
import re
from datetime import datetime, timezone
from pathlib import Path


def collect(home, check_paths=()):
    home = Path(home)
    def portable(path):
        return str(path).replace(str(home), '~', 1)
    errors, jobs = [], []
    try:
        disabled_output = subprocess.run(['launchctl', 'print-disabled', 'gui/' + str(os.getuid())],
            capture_output=True, text=True, timeout=5)
        disabled = set(re.findall(r'"([^"]+)"\s*=>\s*true', disabled_output.stdout))
        if disabled_output.returncode: errors.append('launchd disabled-state: collection unavailable')
    except (OSError, subprocess.SubprocessError):
        disabled = set()
        errors.append('launchd disabled-state: collection unavailable')
    for directory in (home / 'Library/LaunchAgents', Path('/Library/LaunchDaemons')):
        for path in sorted(directory.glob('*.plist')):
            try:
                p = plistlib.loads(path.read_bytes())
                args = p.get('ProgramArguments', [p.get('Program', '')])
                # Only executable and existing absolute file paths. Never serialize argv/env values.
                paths = [a for a in args if isinstance(a, str) and a.startswith('/')
                         and Path(a).exists() and not '://' in a]
                jobs.append({'label': p.get('Label', path.stem), 'path': portable(path),
                             'executables': [portable(args[0])] if args and args[0].startswith('/') else [],
                             'dependencies': [portable(a) for a in paths[1:]],
                             'schedule': {k: p[k] for k in ('StartInterval', 'StartCalendarInterval',
                                                           'KeepAlive', 'RunAtLoad') if k in p},
                             'disabled': p.get('Disabled', False) or p.get('Label', path.stem) in disabled,
                             'environment_names': sorted(p.get('EnvironmentVariables', {}))})
            except Exception:
                errors.append(portable(path) + ': unreadable plist')
    # Files in these credential containers are inventory entries, never source inputs.
    containers = ['.config/carr', '.ssh', '.cli-proxy-api', '.config/cliproxyapi']
    credentials = sorted({portable(p) for d in containers for p in (home / d).rglob('*') if p.is_file()})
    state_roots = ['.hermes', '.local/share/carr', 'carr-system/out',
                   'Library/Application Support/Tailscale']
    states = []
    for d in state_roots:
        for p in (home / d).rglob('*'):
            if p.is_file() and p.suffix in ('.db', '.sqlite', '.sqlite3'):
                states.append(portable(p))
    listeners = []
    try:
        r = subprocess.run(['/usr/sbin/lsof', '-nP', '-iTCP', '-sTCP:LISTEN', '-Fpcn'],
                           capture_output=True, text=True, timeout=10)
        pid, command = '', ''
        for line in r.stdout.splitlines():
            if line.startswith('p'): pid = line[1:]
            elif line.startswith('c'): command = line[1:]
            elif line.startswith('n'): listeners.append({'pid': pid, 'command': command, 'listen': line[1:]})
    except (OSError, subprocess.SubprocessError):
        errors.append('listeners: collection unavailable')
    try:
        r = subprocess.run(['crontab', '-l'], capture_output=True, text=True, timeout=5)
        # Commands can embed secrets. Only count entries; any entry blocks transfer until declared.
        cron_count = len([s for s in r.stdout.splitlines() if s.strip() and not s.lstrip().startswith('#')])
        if r.returncode and 'no crontab' not in r.stderr:
            errors.append('cron: collection unavailable')
    except (OSError, subprocess.SubprocessError):
        cron_count = None
        errors.append('cron: collection unavailable')
    requested = set(check_paths) | {p for j in jobs for p in j['executables'] + j['dependencies']}
    presence = {p: (home / p[2:] if p.startswith('~/') else Path(p)).exists() for p in sorted(requested)}
    try: memory = subprocess.run(['sysctl', '-n', 'hw.memsize'], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError): memory = ''
    return {'schema': 'carr-host-inventory/v1', 'host': socket.gethostname(),
            'path_presence': presence, 'memory_bytes': int(memory) if memory.isdigit() else None,
            'captured_at': datetime.now(timezone.utc).isoformat(), 'launchd': jobs,
            'credential_paths': credentials, 'local_databases': sorted(states),
            'listeners': listeners, 'cron_entries': cron_count, 'errors': errors,
            'state_search_roots': ['~/' + d for d in state_roots],
            'scheduled_tasks': sorted(p.name for p in (home / '.claude/scheduled-tasks').glob('*') if p.is_dir()),
            'ssh_aliases': ssh_aliases(home),
            'tailscale_app': Path('/Applications/Tailscale.app').exists()}


def ssh_aliases(home):
    try:
        return [word for line in (home / '.ssh/config').read_text().splitlines()
                if line.lstrip().lower().startswith('host ') for word in line.split()[1:]
                if not any(c in word for c in '*?!')]
    except OSError:
        return []


def capture_pair(root):
    config = json.loads((root / 'ops/config/studio-failover.v1.json').read_text())
    if socket.gethostname() != config['hosts']['studio']['hostname']:
        raise RuntimeError('paired inventory must run on Studio')
    studio = collect(Path.home())
    paths = sorted({p for j in studio['launchd'] for p in j['executables'] + j['dependencies']} |
                   {p for j in config['jobs'] for p in j['credentials'] + j['state']})
    script = 'import sys\nsys.argv=' + repr(['inventory', '--paths', json.dumps(paths)]) + '\n' + Path(__file__).read_text()
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                             config['hosts']['macbook']['ssh'], '~/carr-system/.venv/bin/python -'],
                            input=script, capture_output=True, text=True, timeout=60)
    if result.returncode: raise RuntimeError('MacBook metadata collection unavailable')
    macbook = json.loads(result.stdout)
    folder = root / 'out/studio-failover'
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in [('studio', studio), ('macbook', macbook)]:
        (folder / (name + '.json')).write_text(json.dumps(data, indent=2) + '\n')
    compare(root)


def compare(root):
    """Generate path-only source inventory and the transfer declaration from host readbacks."""
    import sys
    sys.path.insert(0, str(root))
    from lib.launchd_scope import PRIMARY_ONLY
    folder = root / 'out/studio-failover'
    s, m = [json.loads((folder / (h + '.json')).read_text()) for h in ('studio', 'macbook')]
    labels = {p.removesuffix('.plist') for p in PRIMARY_ONLY}
    captured = {'com.carr.cliproxyapi', 'local.carr-progress-board', 'com.carr.fix-train',
                'local.ds4-flash-next', 'local.flash-desk'}
    labels.update(captured)
    credentials = {
        'room-bridge': ['mcp-tokens.env', 'engineering-controller.env'],
        'nightly-record-layer': ['db.env', 'tokens.env', 'healthchecks.env', 'age-key.txt'],
        'nightly-exports-daytime-retry': ['db.env', 'tokens.env'],
        'partner-ping': ['db.env'], 'release-pipeline': ['db.env', 'tokens.env'],
        'control-plane-tick': ['db.env', 'engineering-controller.env'], 'rules-refresh': ['db.env'],
        'job-watchdog': ['db.env', 'mcp-tokens.env'], 'cutover-watch': ['mcp-tokens.env'],
        'delivery-cadence-a05-sweep': ['mcp-tokens.env']}
    states = {'room-bridge': ['~/.config/carr/room-bridge-state.json', '~/.config/carr/hermes-dispatch-results.jsonl'],
              'partner-ping': ['~/.config/carr/partner-ping.json'],
              'release-pipeline': ['~/carr-system/out/release-pipeline'],
              'local.carr-progress-board': ['~/carr-system/out/boards'],
              'nightly-record-layer': ['~/carr-system/out/backups']}
    jobs = []
    for label in sorted(labels):
        if label not in {j['label'] for j in s['launchd']}: continue
        source = 'ops/launchd/' + label + '.plist'
        if label in captured:
            installed = Path.home() / 'Library/LaunchAgents' / (label + '.plist')
            p = plistlib.loads(installed.read_bytes())
            if any(word in ' '.join(p.get('ProgramArguments', [])).lower()
                   for word in ('--api-key', '--token', '--password', '://')):
                raise ValueError('credential-shaped launch arguments cannot be captured')
            p['EnvironmentVariables'] = {k: v for k, v in p.get('EnvironmentVariables', {}).items()
                                         if k in ('PATH', 'PROGRESS_BOARD_ROOT')}
            if not p['EnvironmentVariables']: p.pop('EnvironmentVariables')
            text = plistlib.dumps(p).decode().replace(str(Path.home() / 'carr-system'), '{{REPO}}')
            if 'StartInterval' in p:
                from lib.launchd_calendar import rewrite_template
                text, _ = rewrite_template(text)
            text = text.replace('\n', '\n<!-- doctrine: studio-macbook-failover -->\n', 1)
            (root / source).write_text(text.replace(str(Path.home()), '{{HOME}}'))
        if (root / source).exists():
            key = label.removeprefix('com.carr.')
            deps = ['~/.config/carr/' + p for p in credentials.get(key, [])]
            if key == 'cliproxyapi': deps += ['~/.cli-proxy-api/' + p for p in ('config.yaml', 'client-api-key', 'management-key')]
            installed_role = next(j for j in s['launchd'] if j['label'] == label)
            jobs.append({'label': label, 'source': source, 'credentials': deps, 'state': states.get(key, []),
                         'enabled': not installed_role['disabled']})
    manual = [
        ('guarded host startup', 'verify guarded or safely disabled startup on both hosts; bootstrap Studio with failback-to-studio.sh while MacBook is fenced; keep an unprepared returning host off until its old startup jobs are disabled'),
        ('generated fix-train executable', 'provision and verify out/orch/fix-train.sh from its owning orchestrator'),
        ('local model runtimes', 'provision model binaries and weights; verify their local server endpoints'),
        ('local PostgreSQL instances', 'classify observed test clusters; restore and verify any persistent cluster'),
        ('Model Room profile state', 'restore Hermes profiles and verify non-interactive desk authentication'),
        ('Tailscale and SSH ingress', 'verify target tailnet and SSH ingress; repoint Studio-bound endpoints'),
        ('Claude scheduled definitions', 'demote source scheduler and reconcile target primary definitions without client settings edits')]
    manifest_ref = 'origin/main' if subprocess.run(['git', 'cat-file', '-e',
        'origin/main:ops/config/scheduled-jobs.v1.json'], capture_output=True).returncode == 0 else 'origin/codex/job-manifest-drift'
    scheduled = json.loads(subprocess.check_output(['git', 'show', manifest_ref + ':ops/config/scheduled-jobs.v1.json'], text=True))
    manifest = {'repository': 'jbookout/carr-system', 'ref': manifest_ref,
                'captured_machine_role': scheduled.get('captured_machine_role'),
                'source_sha': subprocess.check_output(['git', 'rev-parse', manifest_ref], text=True).strip(),
                'path': 'ops/config/scheduled-jobs.v1.json'}
    config = {'schema': 'carr-studio-failover/v1',
              'hosts': {h: {'hostname': d['host'], 'ssh': h} for h, d in [('studio', s), ('macbook', m)]},
              'scheduled_manifest': manifest, 'jobs': jobs,
              'manual_roles': [{'name': n, 'prerequisite': p} for n, p in manual], 'rehearsal_max_age_days': 35}
    config['hosts']['studio']['ssh'] = 'booko@mac-studio.tailc8cc93.ts.net'
    ml = {j['label']: j for j in m['launchd']}
    roles = [{**{k: v for k, v in j.items() if k != 'schedule'}, 'schedule_source': j['path'],
              'classification': 'leader-job' if j['label'] in labels else
              'manual-runtime' if j['label'].startswith(('com.carr.', 'local.')) else 'device-vendor',
              'macbook_installed': j['label'] in ml,
              'macbook_missing': [p for p in j['executables'] + j['dependencies'] if not m['path_presence'].get(p)],
              'schedule_expectation': next(({k: v[k] for k in ('scheduler', 'expected_enabled', 'expected_installed')}
                  for v in scheduled['jobs'] if v['label'] == j['label']), None)}
             for j in s['launchd'] if j['label'] not in ml or j['label'] in labels]
    creds = [p for p in s['credential_paths'] if (p.endswith(('.env', '.pem', '.connection', '.key')) or
              p.endswith(('age-key.txt', 'client-api-key', 'management-key')) or p.startswith('~/.ssh/'))
             and '/._' not in p and 'backup' not in p]
    report = {'schema': 'carr-studio-failover-inventory/v1', 'captured_at': s['captured_at'],
              'source_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
              'manifest_source': manifest, 'roles': roles,
              'credential_files': [{'path': p, 'macbook_present': p in m['credential_paths']} for p in creds],
              'listeners': [{**v, 'macbook_same_endpoint': any(v['command'] == b['command'] and v['listen'] == b['listen']
                  for b in m['listeners']), 'required_action': 'classify dependency and verify replacement endpoint before takeover'}
                  for v in s['listeners']],
              'local_databases': [{'path': p, 'macbook_present': p in m['local_databases']} for p in s['local_databases'] if '/.mypy_cache/' not in p],
              'state_search_roots': s['state_search_roots'],
              'hardware': {'studio_memory_bytes': s['memory_bytes'], 'macbook_memory_bytes': m['memory_bytes'],
                           'required_action': 'verify model weights and context fit target memory; do not assume equivalent capacity'},
              'ssh_roles': {'studio_aliases': s['ssh_aliases'], 'macbook_aliases': m['ssh_aliases'],
                            'tailscale_app_studio': s['tailscale_app'], 'tailscale_app_macbook': m['tailscale_app'],
                            'required_action': 'verify target ingress and repoint callers pinned to Studio'},
              'collections': {'launchd': 'all user agents and readable /Library/LaunchDaemons',
                              'cron': {'studio_entries': s['cron_entries'], 'macbook_entries': m['cron_entries']},
                              'claude': {'studio_definitions': s['scheduled_tasks'], 'macbook_definitions': m['scheduled_tasks']},
                              'errors': s['errors'] + m['errors']}}
    for name, data in [('studio-failover.v1', config), ('studio-failover-inventory.v1', report)]:
        (root / ('ops/config/' + name + '.json')).write_text(json.dumps(data, indent=2) + '\n')
    print('Generated path-only inventory and transfer declaration')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', default=str(Path.home()))
    parser.add_argument('--compare', action='store_true')
    parser.add_argument('--capture-pair', action='store_true', help='read both hosts and rebuild declarations from Studio')
    parser.add_argument('--paths', default='[]', help='JSON array of PATH-only dependencies to stat on this host')
    args = parser.parse_args()
    if args.capture_pair: capture_pair(Path(__file__).resolve().parents[1])
    elif args.compare: compare(Path(__file__).resolve().parents[1])
    else: print(json.dumps(collect(args.home, json.loads(args.paths)), indent=2))
