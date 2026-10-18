#!/usr/bin/env python3
"""Loop watch: a once-a-minute local killer for runaway E2E runners.

WHY. Most staging traffic comes from our own runners on our own Macs, so a
retry storm or respawn loop is cheapest to stop at the source, within a
minute, before Cloudflare's analytics (minutes behind) or its billing data
(a day or more behind) can see it. Joe, 2026-10-08: "something that tracks
dead loops very diligently", and "make sure the loop killer doesn't, itself,
become a dead loop".

WHAT ONE RUN DOES (no network, no model, no sleep; a 10 s self-alarm ends it
whatever happens):
  1. exits at once when loop-watch.off exists (its own breaker tripped);
  2. takes a NON-blocking lock; another run holding it means exit 0 quietly
     (a lock held far past any run's life files a defect instead);
  3. reads `ps`, the E2E RunBudget ledgers, and its own small state file;
  4. KILLS, with SIGTERM now and SIGKILL on the next run if the same process
     is still alive, only a process whose command matches the configured
     allowlist and never an agent session, itself, its parent or launchd:
       over-budget  its RunBudget ledger is exhausted while it is alive
       deadline     its ledger deadline passed (plus a short grace)
       respawn      the same runner started more than N times in M minutes
       time-bound   alive longer than its matching watch rule's bound plus grace
     at most 3 kills per run and 10 per hour; past a cap it alerts once and
     leaves the rest for a human;
  5. marks a killed run KILLED in its ledger (state.json plus the ledger's
     own STOP file, which refuses any new or resumed run until a human
     clears it);
  6. queues one record-layer defect per runner and cause (a repeat raises a
     local count instead of filing again) in a local outbox and starts a
     detached reporter, so a slow or down record layer can never stall it;
  7. writes its heartbeat. The spend guard's 15-minute `fast` poll alerts
     when the heartbeat is more than 5 minutes old.

Five consecutive failed runs write loop-watch.off, alert once and file a
defect; every later run then stops at the first line until a human removes
the file. Thresholds live only in ops/config/cloudflare-spend-guard.v1.json
(loop_watch). The launchd template is in tools/cloudflare-spend-guard/launchd/
and is NOT installed.
"""

import os as _os
import sys as _sys

if (__name__ == '__main__' and _sys.argv[1:2] != ['report']
        and _os.path.exists(_os.path.expanduser('~/.local/state/carr/cloudflare-spend-guard/loop-watch.off'))):
    _sys.exit(0)

import fcntl  # noqa: E402
import glob  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / 'ops' / 'config' / 'cloudflare-spend-guard.v1.json'
DEFAULT_STATE_DIR = '~/.local/state/carr/cloudflare-spend-guard'
OFF_NAME = 'loop-watch.off'
STATE_NAME = 'loop-watch.json'
LOCK_NAME = 'loop-watch.lock'
RECEIPTS_NAME = 'loop-watch-receipts.jsonl'
LEDGER_SCHEMA = 'e2e-run-budget.v1'
DEFECT_NAMESPACE = uuid.UUID('5b7c1f3e-8f0a-4d7e-9a35-0c4f0e6d2b11')
# Never signalled, whatever the allowlist says: agent sessions, the guard and this watcher.
NEVER = re.compile(r'claude|codex|cloudflare[_-]spend[_-]guard|cloudflare[_-]loop[_-]watch|launchd|cursor-agent|gemini|aider|'
                   r'ollama|\bgrok', re.IGNORECASE)
# A watched pattern that matches any of these is refused when the config loads.
AGENT_PROBES = ('claude', 'claude --resume', '/Users/x/.local/bin/claude -p hello', 'codex', 'codex ' + 'exec "fix"',
                'node /opt/homebrew/bin/codex', '/sbin/launchd', 'python3 tools/cloudflare_spend_guard.py fast',
                'python3 tools/cloudflare_loop_watch.py', '/bin/zsh', 'bash', 'node', 'python3', 'ssh studio',
                'git push', 'npm run e2e:staging:stop')
NUMBERS = ('ledger_deadline_grace_seconds', 'respawn_max_starts', 'respawn_window_seconds', 'time_bound_grace_seconds',
           'sigkill_after_seconds', 'self_alarm_seconds', 'max_kills_per_run', 'max_kills_per_hour',
           'breaker_consecutive_errors', 'receipt_rotate_bytes', 'lock_stale_seconds',
           'report_timeout_seconds', 'report_max_per_run', 'outbox_stale_seconds', 'defect_episode_hours')


# ── configuration ────────────────────────────────────────────────────────────

def validate_settings(s: dict) -> dict:
    def need(condition, message):
        if not condition:
            raise ValueError(f'loop_watch config: {message}')

    need(isinstance(s, dict), 'loop_watch must be an object')
    for key in NUMBERS:
        need(isinstance(s.get(key), int) and not isinstance(s.get(key), bool) and s[key] > 0,
             f'{key} must be a positive integer')
    need(s['self_alarm_seconds'] < 60, 'self_alarm_seconds must end a run before the next minute starts')
    globs = s.get('run_budget_globs')
    need(isinstance(globs, list) and all(isinstance(g, str) and g for g in globs), 'run_budget_globs must be strings')
    watched: list = s['watched'] if isinstance(s.get('watched'), list) else []
    need(isinstance(watched, list) and watched, 'watched must list at least one command')
    names = set()
    for entry in watched:
        need(isinstance(entry.get('name'), str) and entry['name'] and entry['name'] not in names,
             'watched names must be unique strings')
        names.add(entry['name'])
        need(isinstance(entry.get('max_seconds'), int) and entry['max_seconds'] > 0,
             f"{entry['name']} needs a positive max_seconds")
        try:
            pattern = re.compile(entry.get('pattern') or '')
        except re.error:
            raise ValueError(f"loop_watch config: {entry['name']} pattern does not compile") from None
        need(bool(entry.get('pattern')), f"{entry['name']} needs a pattern")
        for probe in AGENT_PROBES:
            need(not pattern.search(probe), f"{entry['name']} pattern matches {probe!r}; it may never match an agent "
                                            'session or a general command')
    return s


def load(path: Path = CONFIG_PATH):
    cfg = json.loads(Path(path).read_text())
    return validate_settings(cfg['loop_watch']), Path(cfg['state_dir']).expanduser()


# ── small durable files ──────────────────────────────────────────────────────

def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _blank_state() -> dict:
    return {'schema': 'carr-loop-watch-state.v1', 'heartbeat': None, 'sightings': {}, 'term_sent': {},
            'kill_times': [], 'defects': {}, 'cap_alert_at': None, 'consecutive_errors': 0,
            'last_error': None, 'last_error_repeats': 0, 'breaker_alerted': False}


def read_state(state_dir) -> dict:
    try:
        state = json.loads((Path(state_dir) / STATE_NAME).read_text())
    except (OSError, ValueError):
        return _blank_state()
    if not isinstance(state, dict) or state.get('schema') != 'carr-loop-watch-state.v1':
        return _blank_state()
    return {**_blank_state(), **state}


def _write_state(state_dir: Path, state: dict) -> None:
    _atomic_write(state_dir / STATE_NAME, json.dumps(state, sort_keys=True))


def read_heartbeat(state_dir):
    beat = read_state(state_dir).get('heartbeat')
    return datetime.fromtimestamp(beat, timezone.utc) if isinstance(beat, (int, float)) else None


def _receipt(cfg: dict, state_dir: Path, record: dict) -> None:
    path = state_dir / RECEIPTS_NAME
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        if path.stat().st_size >= cfg['receipt_rotate_bytes']:
            os.replace(path, path.with_name(RECEIPTS_NAME + '.1'))
    except FileNotFoundError:
        pass
    with path.open('a') as handle:
        handle.write(json.dumps(record, sort_keys=True) + '\n')


# ── the outbox and its detached reporter ─────────────────────────────────────

def queue_report(state_dir, dedupe_key: str, fields: dict, *, now: datetime) -> Path:
    """Write one record-defect call to the local outbox. Never touches the network."""
    folder = Path(state_dir) / 'outbox'
    args = {'defect_class': 'loop-watch-report', 'detected_by': 'check', **fields,
            'idempotency_key': str(uuid.uuid5(DEFECT_NAMESPACE, f'{dedupe_key}|{now.timestamp():.0f}')),
            'session_key': f'loop-watch:{dedupe_key}', 'occurred_on': now.strftime('%Y-%m-%d')}
    item = {'queued_at': now.timestamp(), 'dedupe_key': dedupe_key, 'verb': 'record-defect', 'args': args}
    digest = hashlib.sha256(dedupe_key.encode()).hexdigest()[:12]
    path = folder / f'{int(now.timestamp() * 1000):013d}-{digest}.json'
    _atomic_write(path, json.dumps(item, sort_keys=True))
    return path


def _outbox(state_dir) -> list:
    folder = Path(state_dir) / 'outbox'
    return sorted(folder.glob('*.json')) if folder.exists() else []


def spawn_reporter(command=None) -> None:
    """Start the reporter detached; the caller never waits for it."""
    subprocess.Popen(command or [sys.executable, str(Path(__file__).resolve()), 'report'],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, close_fds=True, cwd=str(ROOT))


def call_verb_cli(verb: str, args: dict, timeout: float) -> bool:
    """`./run.sh call <verb> '<json>'`, in its own process group, killed at the timeout."""
    proc = subprocess.Popen([str(ROOT / 'run.sh'), 'call', verb, json.dumps(args)], cwd=str(ROOT),
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=max(0.1, timeout))
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        return False
    try:
        reply = json.loads(out or b'{}')
    except ValueError:
        reply = {}
    return proc.returncode == 0 and isinstance(reply, dict) and 'error' not in reply


def deliver_outbox(cfg: dict, *, state_dir, now: datetime, call_verb=call_verb_cli) -> int:
    """Send at most report_max_per_run outbox items, oldest first; stop at the first failure."""
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(state_dir / 'outbox.lock', os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        started, delivered = time.monotonic(), 0
        for path in _outbox(state_dir)[:cfg['report_max_per_run']]:
            remaining = cfg['report_timeout_seconds'] - (time.monotonic() - started)
            if remaining <= 0:
                break
            try:
                item = json.loads(path.read_text())
            except (OSError, ValueError):
                path.rename(path.with_suffix('.unreadable'))
                continue
            if not call_verb(item['verb'], item['args'], remaining):
                break
            path.unlink(missing_ok=True)
            delivered += 1
            age = now.timestamp() - float(item.get('queued_at') or now.timestamp())
            if age > cfg['outbox_stale_seconds']:
                queue_report(state_dir, f"report-delayed:{item.get('dedupe_key')}", {
                    'defect_class': 'loop-watch-report-delayed',
                    'claimed': 'loop-watch reports reach the record layer within a minute',
                    'actual': f"report {item.get('dedupe_key')} waited {round(age / 60)} min in the local outbox "
                              'before the record layer accepted it',
                    'source_unread': str(state_dir / 'outbox')}, now=now)
        return delivered
    finally:
        os.close(fd)


# ── reading the machine ──────────────────────────────────────────────────────

def _etime(text: str) -> int:
    days, _, clock = text.rpartition('-')
    parts = [int(p) for p in clock.split(':')]
    while len(parts) < 3:
        parts.insert(0, 0)
    return int(days or 0) * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]


def parse_ps(text: str, now: datetime) -> dict:
    procs = {}
    for line in text.splitlines():
        fields = line.split(None, 3)
        if len(fields) < 4 or not fields[0].isdigit():
            continue
        elapsed = _etime(fields[2])
        procs[int(fields[0])] = {'pid': int(fields[0]), 'ppid': int(fields[1]), 'elapsed': elapsed,
                                 'start': now.timestamp() - elapsed, 'command': fields[3]}
    return procs


def read_ps() -> str:
    return subprocess.run(['/bin/ps', '-axo', 'pid=,ppid=,etime=,command='], capture_output=True, text=True,
                          timeout=5, check=True).stdout


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def read_ledgers(cfg: dict) -> dict:
    """pid -> the RunBudget ledger it holds the lock for. doctorcre-app PR 197 owns the format."""
    found = {}
    for pattern in cfg['run_budget_globs']:
        for directory in glob.glob(os.path.expanduser(pattern)):
            try:
                state = json.loads((Path(directory) / 'state.json').read_text())
                pid = int((Path(directory) / 'lock').read_text().strip())
            except (OSError, ValueError):
                continue
            if isinstance(state, dict) and state.get('schema') == LEDGER_SCHEMA and pid > 0:
                found[pid] = {'dir': Path(directory), 'state': state}
    return found


def _ledger_breach(ledger: dict, now: datetime, grace: int):
    state = ledger['state']
    profile, spent = state.get('profile') or {}, state.get('spent') or {}
    over = sorted(k for k, v in spent.items() if isinstance(v, (int, float)) and isinstance(profile.get(k), (int, float))
                  and v > profile[k])
    stopped = state.get('stopped') or {}
    if over or str(stopped.get('reason', '')).endswith('-exhausted'):
        counts = ', '.join(f'{k} {spent[k]}/{profile[k]}' for k in over) or f"stopped {stopped.get('reason')}"
        return 'over-budget', f'RunBudget exhausted ({counts}) and the process is still alive'
    deadline = state.get('deadline_at')
    if isinstance(deadline, (int, float)) and now.timestamp() * 1000 >= deadline + grace * 1000:
        return 'deadline', f'RunBudget deadline passed {round(now.timestamp() - deadline / 1000)}s ago'
    return None


def _ledger_excerpt(ledger) -> dict:
    if not ledger:
        return {}
    state = ledger['state']
    return {'dir': str(ledger['dir']), 'run_id': state.get('run_id'),
            'profile': (state.get('profile') or {}).get('name'), 'created_at': state.get('created_at'),
            'deadline_at': state.get('deadline_at'), 'spent': state.get('spent'), 'stopped': state.get('stopped')}


def mark_ledger_killed(ledger: dict, info: dict, now: datetime) -> None:
    """KILLED in the ledger: its STOP file refuses any new or resumed run until a human clears it."""
    directory = ledger['dir']
    stop = directory / 'STOP'
    try:
        fd = os.open(stop, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(json.dumps({'reason': f"KILLED by loop-watch: {info['rule']}; defect {info['defect']}",
                                     'at': now.strftime('%Y-%m-%dT%H:%M:%SZ')}) + '\n')
    except FileExistsError:
        pass
    state = dict(ledger['state'])
    state['killed'] = {'by': 'loop-watch', 'at': int(now.timestamp() * 1000), **info}
    if not state.get('stopped'):
        state['stopped'] = {'reason': 'killed-by-loop-watch', 'at': int(now.timestamp() * 1000)}
    _atomic_write(directory / 'state.json', json.dumps(state, indent=2) + '\n')


# ── one scan ─────────────────────────────────────────────────────────────────

def _watch_name(cfg: dict, command: str):
    if NEVER.search(command):
        return None
    for entry in cfg['watched']:
        if re.search(entry['pattern'], command):
            return entry
    return None


def _defect(cfg, state, state_dir, now, key, fields, report):
    """One defect per runner and cause per episode; a repeat raises the local count."""
    episode = cfg['defect_episode_hours'] * 3600
    known = state['defects'].get(key)
    if known and now.timestamp() - known['last_at'] < episode:
        known.update(count=known['count'] + 1, last_at=now.timestamp())
        return False
    state['defects'][key] = {'count': 1, 'first_at': now.timestamp(), 'last_at': now.timestamp(),
                             'defect_class': fields['defect_class']}
    queue_report(state_dir, key, fields, now=now)
    report.append(key)
    return True


def scan(cfg: dict, *, state_dir, now: datetime, ps_text: str, kill, is_alive, self_pid: int, parent_pid: int,
         spawn_reporter=spawn_reporter) -> dict:
    state_dir = Path(state_dir)
    state = read_state(state_dir)
    epoch = now.timestamp()
    procs = parse_ps(ps_text, now)
    protected = {self_pid, parent_pid, 1, 0}
    lines: list[str] = []
    errors: list[str] = []
    reported: list[str] = []
    state['kill_times'] = [t for t in state['kill_times'] if epoch - t < 3600]
    kills_this_run, capped = 0, []

    # Escalate earlier SIGTERMs: SIGKILL only the same process (same start time) that still matches.
    escalated = set()
    for pid_text, sent in list(state['term_sent'].items()):
        pid, proc = int(pid_text), procs.get(int(pid_text))
        same = proc is not None and abs(proc['start'] - sent['start']) <= 2 and _watch_name(cfg, proc['command'])
        if not same or not is_alive(pid):
            del state['term_sent'][pid_text]
            continue
        if epoch - sent['at'] >= cfg['sigkill_after_seconds'] and pid not in protected:
            kill(pid, signal.SIGKILL)
            del state['term_sent'][pid_text]
            escalated.add(pid)
            lines.append(f"KILLED pid {pid} {sent['name']} SIGKILL (still alive {round(epoch - sent['at'])}s after SIGTERM)")
            _receipt(cfg, state_dir, {'at': epoch, 'pid': pid, 'name': sent['name'], 'signal': 'SIGKILL',
                                      'command': sent['command']})
            _defect(cfg, state, state_dir, now, f"{sent['name']}:sigkill-needed", {
                'defect_class': 'e2e-dead-loop-killed',
                'claimed': f"E2E runner {sent['name']} exits when sent SIGTERM",
                'actual': f"pid {pid} ({sent['command']}) was still alive {round(epoch - sent['at'])}s after SIGTERM; "
                          'loop-watch sent SIGKILL',
                'cost_note': json.dumps({'command': sent['command'], 'pid': pid, 'signal': 'SIGKILL',
                                         'sigkill_needed': True, 'kill_time': now.isoformat()})}, reported)

    ledgers = read_ledgers(cfg)
    targets = []  # (proc, entry, rule, detail, ledger)
    watched = {pid: (p, _watch_name(cfg, p['command'])) for pid, p in procs.items()}
    eligible = {pid: v for pid, v in watched.items()
                if v[1] is not None and pid not in protected | escalated and str(pid) not in state['term_sent']}
    chosen = set()
    for pid, ledger in ledgers.items():
        if pid in eligible:
            breach = _ledger_breach(ledger, now, cfg['ledger_deadline_grace_seconds'])
            if breach:
                targets.append((eligible[pid][0], eligible[pid][1], *breach, ledger))
                chosen.add(pid)
    window = cfg['respawn_window_seconds']
    for entry in cfg['watched']:
        seen = [s for s in state['sightings'].get(entry['name'], []) if epoch - s[1] < window]
        current = [p for p, e in (watched[pid] for pid in watched) if e is entry]
        for proc in current:
            if not any(s[0] == proc['pid'] and abs(s[1] - proc['start']) <= 2 for s in seen):
                seen.append([proc['pid'], proc['start']])
        state['sightings'][entry['name']] = seen
        if len(seen) > cfg['respawn_max_starts']:
            for proc in current:
                if proc['pid'] in eligible and proc['pid'] not in chosen:
                    targets.append((proc, entry, 'respawn',
                                    f"{len(seen)} starts in {window // 60} min (limit {cfg['respawn_max_starts']})",
                                    ledgers.get(proc['pid'])))
                    chosen.add(proc['pid'])
    for pid, (proc, entry) in sorted(eligible.items()):
        bound = entry['max_seconds'] + cfg['time_bound_grace_seconds']
        if pid not in chosen and proc['elapsed'] > bound:
            targets.append((proc, entry, 'time-bound', f"alive {proc['elapsed']}s, bound {entry['max_seconds']}s "
                                                       f"+ {cfg['time_bound_grace_seconds']}s grace", ledgers.get(pid)))
            chosen.add(pid)

    for proc, entry, rule, detail, ledger in targets:
        if kills_this_run >= cfg['max_kills_per_run'] or len(state['kill_times']) >= cfg['max_kills_per_hour']:
            capped.append(proc['pid'])
            continue
        key = f"{entry['name']}:{rule}"
        evidence = {'command': proc['command'], 'pid': proc['pid'], 'rule': rule, 'detail': detail,
                    'ledger': _ledger_excerpt(ledger), 'kill_time': now.isoformat(), 'signal': 'SIGTERM',
                    'sigkill_needed': 'decided on the next run (SIGKILL if still alive)'}
        if ledger:
            mark_ledger_killed(ledger, {'rule': rule, 'detail': detail, 'pid': proc['pid'], 'defect': key}, now)
        try:
            kill(proc['pid'], signal.SIGTERM)
        except ProcessLookupError:
            continue
        kills_this_run += 1
        state['kill_times'].append(epoch)
        state['term_sent'][str(proc['pid'])] = {'start': proc['start'], 'at': epoch, 'name': entry['name'],
                                                'command': proc['command']}
        lines.append(f"KILLED pid {proc['pid']} {entry['name']} rule={rule} ({detail}); SIGTERM; defect {key}")
        _receipt(cfg, state_dir, {'at': epoch, **evidence})
        _defect(cfg, state, state_dir, now, key, {
            'defect_class': 'e2e-dead-loop-killed',
            'claimed': f"E2E runner {entry['name']} stays inside its bounds and exits on its own",
            'actual': f"loop-watch killed pid {proc['pid']} ({proc['command']}) at {now.isoformat()}: {rule}: {detail}",
            'source_unread': str(ledger['dir']) if ledger else 'ps',
            'cost_note': json.dumps(evidence)[:2000]}, reported)

    if capped:
        if not state['cap_alert_at'] or epoch - state['cap_alert_at'] >= 3600:
            state['cap_alert_at'] = epoch
            line = (f"loop-watch: kill cap reached ({cfg['max_kills_per_run']}/run, {cfg['max_kills_per_hour']}/hour); "
                    f"left {len(capped)} process(es) for a human: pids {', '.join(map(str, capped))}")
            lines.append(line)
            _receipt(cfg, state_dir, {'at': epoch, 'alert': 'kill-cap', 'pids': capped})
            _defect(cfg, state, state_dir, now, 'loop-watch:kill-cap', {
                'defect_class': 'loop-watch-kill-cap-reached',
                'claimed': 'loop-watch stops every runaway E2E runner on its own',
                'actual': line}, reported)

    stale = []
    for path in _outbox(state_dir):
        try:
            item = json.loads(path.read_text())
            if epoch - float(item['queued_at']) > cfg['outbox_stale_seconds']:
                stale.append((epoch - float(item['queued_at']), item.get('dedupe_key')))
        except (OSError, ValueError, KeyError):
            stale.append((0.0, path.name))
    if stale:
        oldest = max(stale, key=lambda s: s[0])
        errors.append(f'loop-watch: {len(stale)} report(s) undelivered to the record layer for up to '
                      f'{round(oldest[0] / 60)} min (oldest {oldest[1]}); outbox {state_dir / "outbox"}')

    state.update(heartbeat=epoch, consecutive_errors=0, last_error=None, last_error_repeats=0, breaker_alerted=False)
    _write_state(state_dir, state)
    if _outbox(state_dir):
        spawn_reporter()
    return {'lines': lines, 'errors': errors, 'kills': kills_this_run, 'reported': reported}


# ── one firing ───────────────────────────────────────────────────────────────

def arm_self_alarm(seconds: int) -> None:
    """The run's hard ceiling: SIGALRM's default action ends the process wherever it is."""
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.alarm(seconds)


def _stderr(line):
    print(line, file=sys.stderr)


def _lock_held(cfg, state_dir: Path, fd: int, now: datetime, spawn, err) -> None:
    try:
        holder_pid, started = (int(x) for x in os.pread(fd, 64, 0).split()[:2])
    except ValueError:
        return
    if now.timestamp() - started <= cfg['lock_stale_seconds']:
        return
    key = f'loop-watch:lock-held:{holder_pid}:{started}'
    marker = state_dir / 'lock-held-alerted'
    try:
        if marker.read_text() == key:
            return
    except OSError:
        pass
    _atomic_write(marker, key)
    line = (f'loop-watch: lock held by pid {holder_pid} for {round(now.timestamp() - started)}s, past the '
            f"{cfg['self_alarm_seconds']}s run ceiling")
    err(line)
    queue_report(state_dir, key, {'defect_class': 'loop-watch-lock-held',
                                  'claimed': f"a loop-watch run ends within {cfg['self_alarm_seconds']}s",
                                  'actual': line, 'source_unread': str(state_dir / LOCK_NAME)}, now=now)
    spawn()


def run_once(cfg: dict, *, state_dir, now: datetime, ps_reader=read_ps, kill=os.kill, is_alive=_alive,
             spawn_reporter=spawn_reporter, alarm=True, err=_stderr, out=print, self_pid=None, parent_pid=None) -> int:
    state_dir = Path(state_dir)
    if (state_dir / OFF_NAME).exists():
        return 0
    if alarm:
        arm_self_alarm(cfg['self_alarm_seconds'])
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(state_dir / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _lock_held(cfg, state_dir, fd, now, spawn_reporter, err)
            return 0
        os.ftruncate(fd, 0)
        os.pwrite(fd, f'{os.getpid()} {int(now.timestamp())}'.encode(), 0)
        try:
            result = scan(cfg, state_dir=state_dir, now=now, ps_text=ps_reader(), kill=kill, is_alive=is_alive,
                          self_pid=os.getpid() if self_pid is None else self_pid,
                          parent_pid=os.getppid() if parent_pid is None else parent_pid,
                          spawn_reporter=spawn_reporter)
        except Exception as exc:  # noqa: BLE001 — every failure counts toward the breaker
            return _record_error(cfg, state_dir, now, exc, spawn_reporter, err)
        for line in result['lines']:
            out(line)
        for line in result['errors']:
            err(line)
        return 0
    finally:
        os.close(fd)


def _record_error(cfg, state_dir: Path, now: datetime, exc: Exception, spawn, err) -> int:
    state = read_state(state_dir)
    line = f'loop-watch error: {type(exc).__name__}: {str(exc)[:200]}'
    if line == state['last_error']:
        state['last_error_repeats'] += 1
    else:
        if state['last_error_repeats']:
            err(f"loop-watch: previous error repeated {state['last_error_repeats']} more time(s)")
        err(line)
        state.update(last_error=line, last_error_repeats=0)
    state['consecutive_errors'] += 1
    if state['consecutive_errors'] >= cfg['breaker_consecutive_errors'] and not state['breaker_alerted']:
        state['breaker_alerted'] = True
        off = state_dir / OFF_NAME
        _atomic_write(off, json.dumps({'at': now.isoformat(), 'last_error': line,
                                       'consecutive_errors': state['consecutive_errors']}) + '\n')
        alert = (f"loop-watch: breaker tripped after {state['consecutive_errors']} failed runs; every run now exits "
                 f'at once until a human removes {off}')
        err(alert)
        _receipt(cfg, state_dir, {'at': now.timestamp(), 'alert': 'breaker', 'last_error': line})
        queue_report(state_dir, 'loop-watch:breaker', {
            'defect_class': 'loop-watch-breaker-tripped', 'claimed': 'loop-watch scans every minute',
            'actual': f'{alert}; last error {line}', 'source_unread': str(off)}, now=now)
        spawn()
    _write_state(state_dir, state)
    return 3


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv else 'scan'
    if command not in ('scan', 'report'):
        print('usage: cloudflare_loop_watch.py [scan|report]', file=sys.stderr)
        return 2
    cfg, state_dir = load()
    now = datetime.now(timezone.utc)
    if command == 'report':
        arm_self_alarm(cfg['report_timeout_seconds'])
        deliver_outbox(cfg, state_dir=state_dir, now=now)
        return 0
    return run_once(cfg, state_dir=state_dir, now=now)


if __name__ == '__main__':
    sys.exit(main())
