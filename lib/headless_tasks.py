"""Subscription Claude jobs with local schedule windows and private receipts.

The desktop task prompt remains authoritative. This module owns execution,
timeouts and the local ledger, and never creates a desktop or Model Room session.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import itertools
import json
import os
from pathlib import Path
import plistlib
import queue
import re
import signal
import subprocess
import sys
import threading
import tempfile
import time
import uuid
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
PREFIX = 'com.carr.headless.'
DEFAULT_TOOLS = 'Read,Write,Glob,Grep,Bash(./run.sh *),Bash(./bin/*),Bash(./ops/*),WebFetch,WebSearch'
API_ENV = {'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL',
           'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY',
           'CLAUDE_CODE_API_KEY_HELPER_TTL_MS'}


def log_text(text: str) -> str:
    """Project protocol diagnostics only; never retain arbitrary stream text.

    A whitelist excludes business text and credentials, including multiline
    secrets split across events or stderr lines and unknown environment values.
    """
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return 'STREAM non_protocol_text_omitted\n'
    if not isinstance(value, dict):
        return 'STREAM non_object_omitted\n'
    projected = {}
    for key, allowed in {'type': {'system', 'assistant', 'user', 'result'},
                         'subtype': {'init', 'success', 'permission_denied', 'error_during_execution',
                                     'error_max_turns', 'error_max_budget_usd'}}.items():
        if isinstance(value.get(key), str) and value[key] in allowed:
            projected[key] = value[key]
    if type(value.get('is_error')) is bool:
        projected['is_error'] = value['is_error']
    if isinstance(value.get('permission_denials'), list):
        projected['permission_denial_count'] = len(value['permission_denials'])
    return json.dumps(projected) + '\n'


def stamp(instant: datetime) -> str:
    return instant.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def fields(expression: str) -> list[list[int]]:
    specs = expression.split()
    if len(specs) != 5:
        raise ValueError('cron must have five fields')
    expanded = []
    for spec, (low, high) in zip(specs, [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]):
        values: set[int] = set()
        for part in spec.split(','):
            base, sep, step = part.partition('/')
            stride = int(step) if sep else 1
            if stride <= 0:
                raise ValueError('cron step must be positive')
            if base == '*':
                lo, hi = low, high
            elif '-' in base:
                lo, hi = map(int, base.split('-'))
            else:
                lo = hi = int(base)
            if not low <= lo <= hi <= high:
                raise ValueError('cron field out of range')
            values.update(range(lo, hi+1, stride))
        expanded.append(sorted(values))
    expanded[4] = sorted({v % 7 for v in expanded[4]})
    # Native cron uses OR when both day fields are restricted. The launchd
    # calendars below implement that union too, instead of turning it into AND.
    return expanded


def calendar_entries(expression: str) -> list[dict]:
    specs = expression.split()
    expanded = fields(expression)
    day_sets = [set(range(5))]
    if specs[2] != '*' and specs[4] != '*':
        day_sets = [set(range(4)), {0, 1, 3, 4}]
    rows = []
    names = ['Minute', 'Hour', 'Day', 'Month', 'Weekday']
    for day_set in day_sets:
        indices = [i for i in sorted(day_set) if specs[i] != '*']
        for values in itertools.product(*(expanded[i] for i in indices)):
            row = dict(zip((names[i] for i in indices), values))
            if row not in rows:
                rows.append(row)
    return rows


def _slots(expression: str, instant: datetime, zone: str, forward: bool):
    minute, hour, days, months, weekdays = fields(expression)
    specs = expression.split()
    tz = ZoneInfo(zone)
    local_date = instant.astimezone(tz).date()
    # A bounded calendar search covers leap-day schedules too.
    for offset in range(8*366):
        day = local_date + timedelta(days=offset if forward else -offset)
        dom = day.day in days
        dow = (day.weekday()+1) % 7 in weekdays
        day_ok = dom or dow if specs[2] != '*' and specs[4] != '*' else dom and dow
        if day.month not in months or not day_ok:
            continue
        slots = set()
        for h, m, fold in itertools.product(hour, minute, (0, 1)):
            local = datetime(day.year, day.month, day.day, h, m, tzinfo=tz, fold=fold)
            utc = local.astimezone(UTC)
            if utc.astimezone(tz).replace(fold=fold) == local:
                slots.add(utc)
        for slot in sorted(slots, reverse=not forward):
            if (forward and slot > instant) or (not forward and slot <= instant):
                yield slot


def schedule_window(expression: str, instant: datetime, zone: str) -> datetime:
    return next(_slots(expression, instant, zone, False))


def schedule_interval(expression: str, instant: datetime, zone: str) -> float:
    previous = schedule_window(expression, instant, zone)
    return (next(_slots(expression, previous, zone, True)) - previous).total_seconds()


def config(repo: Path) -> dict:
    return json.loads((repo / 'ops/headless-tasks/tasks.json').read_text())


def ledger_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError('invalid ledger row')
            rows.append(row)
    return rows


def append_ledger(path: Path, row: dict) -> None:
    with path.open('ab+') as handle:
        if handle.seek(0, os.SEEK_END):
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b'\n':
                # A crash can lose only the delimiter of an otherwise intact
                # row. Preserve that row before appending the next record.
                handle.write(b'\n')
        handle.write((json.dumps(row, sort_keys=True) + '\n').encode())
        handle.flush()
        os.fsync(handle.fileno())


def private_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def write_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name+'.tmp')
    with temporary.open('w') as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(value, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def recover_ledger(path: Path) -> tuple[list[dict], bool]:
    """Under run.lock, salvage only an incomplete final append.

    Preserve the complete original bytes. Interior corruption stays a hard
    failure; it must not erase a prior success and cause effects to repeat.
    """
    try:
        return ledger_rows(path), False
    except (ValueError, UnicodeError):
        raw = path.read_bytes()
        backup = path.with_name('ledger.corrupt.'+uuid.uuid4().hex)
        with backup.open('xb') as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if raw.endswith(b'\n'):
            raise ValueError('ledger interior corruption') from None
        prefix = raw[:raw.rfind(b'\n')+1]
        rows = [json.loads(line) for line in prefix.splitlines() if line.strip()]
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError('ledger interior corruption')
        temp = path.with_name(path.name+'.repair')
        with temp.open('wb') as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(prefix)
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(path)
        return rows, True


class InterruptedRun(BaseException):
    def __init__(self, signum):
        self.code = 128+signum


def process_birth(pid: int) -> str:
    result = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='],
                            capture_output=True, text=True, timeout=5)
    return result.stdout.strip() if result.returncode == 0 else ''


def reconcile_runs(ledger: Path, prior: list[dict]) -> None:
    ended = {r.get('run_id') for r in prior if r.get('end')}
    latest = {r['run_id']: r for r in prior if r.get('run_id') and not r.get('end')}
    for run_id, row in latest.items():
        if run_id in ended:
            continue
        pid, birth = row.get('child_pid'), row.get('child_birth')
        if pid and birth and process_birth(pid) == birth and os.getpgid(pid) == pid:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        append_ledger(ledger, {**row, 'end': stamp(datetime.now(UTC)), 'status':'failed',
                               'exit_code': 130, 'reason': 'interrupted'})


def verify_completion(path: Path, task_id: str, run_id: str, task_instructions: str,
                      *, deadline: float | None = None) -> str:
    """Read the task receipt AND every claimed artifact, bound to this run.

    The prompt's done-condition remains authoritative; the receipt must cite
    its retained output or record readback, never just conversation termination.
    No-op receipts are kept distinct from work completion and freshness.
    """
    value = json.loads(path.read_text())
    if (value.get('schema') != 'carr-headless-completion/v1' or
            value.get('task_id') != task_id or value.get('run_id') != run_id or
            value.get('outcome') not in ('completed', 'noop')):
        raise ValueError('completion binding')
    artifacts = value.get('artifacts')
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError('completion evidence missing')
    from lib.acceptance_checks import contract, evaluate, read_regular
    remaining = 96000 - len(task_instructions.encode('utf-8'))
    for item in artifacts:
        artifact = Path(item['path'])
        if artifact.resolve() == path.resolve():
            raise ValueError('completion artifact missing')
        # Validate every claimed artifact, including ones not used by a criterion.
        try:
            raw = read_regular(artifact, max(0, remaining), deadline=deadline)
        except OSError:
            raise ValueError('completion artifact missing') from None
        if not raw or len(raw) > remaining:
            raise ValueError('completion evidence empty or exceeds verification budget')
        remaining -= len(raw)
        if hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('completion artifact digest')
    # Only criteria in the pre-launch contract can authorize acceptance. The
    # worker's receipt names artifacts; any claim it makes about checks it ran
    # is not read, because nothing independent observed that execution.
    if deadline is not None and time.monotonic() >= deadline:
        raise ValueError('completion deadline expired')
    acceptance = contract(task_instructions)
    criteria = acceptance.get('noop_criteria' if value['outcome'] == 'noop' else 'criteria')
    # A pre-launch criterion may bind the output next to this run's receipt.
    # The worker cannot select or rewrite this template through its evidence.
    if isinstance(criteria, list):
        criteria = [{**item, 'path':item['path'].replace('{receipt}', str(path.resolve()))}
                    if isinstance(item, dict) and isinstance(item.get('path'), str) else item
                    for item in criteria]
    result = evaluate(criteria, root='/', receipts=artifacts, deadline=deadline)
    if deadline is not None and time.monotonic() >= deadline:
        raise ValueError('completion deadline expired')
    if result['status'] == 'needs_review':
        raise ValueError('task acceptance needs review: typed criteria required')
    if result['status'] != 'passed':
        raise ValueError('task done-condition not established')
    return value['outcome']


def record_run(repo: Path, task_id: str, row: dict) -> bool:
    from lib.scheduled_run import build_run_args
    argv = build_run_args(task_id, 'succeeded' if row['status'] == 'success' else 'failed',
        None if row['status'] == 'success' else 'tool_error', row['start'], row['end'],
        'headless verified work completion' if row['status'] == 'success' else 'headless execution failed',
        row['run_id'], 'bin/headless-task')
    python = repo/'.venv/bin/python'
    result = subprocess.run([str(python) if python.exists() else sys.executable,
        str(repo/'tools/ops-record.py'), *argv], cwd=repo, capture_output=True, text=True, timeout=30)
    if result.returncode:
        return False
    # cmd_run's first line acknowledges the correlation and canonical run;
    # later lines report incident recovery. They are diagnostic text, not part
    # of the acknowledgement contract.
    lines = result.stdout.splitlines()
    parts = lines[0].split() if lines else []
    try:
        return len(parts) == 2 and str(uuid.UUID(parts[0])) == row['run_id'] and bool(uuid.UUID(parts[1]))
    except ValueError:
        return False


def _terminate(child: subprocess.Popen) -> None:
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        # Darwin can return EPERM after the last group member has exited.
        # A live group remains a failure; verify absence instead of swallowing.
        child.poll()  # Reap an exited leader before asking ps about its group.
        probe = subprocess.run(['ps', '-axo', 'pgid='], capture_output=True, text=True, timeout=5)
        if probe.returncode or str(child.pid) in probe.stdout.split():
            raise
    child.wait(timeout=5)


def _claude(prompt: Path, repo: Path, model: str, tools: str, timeout: float,
            log, env: dict, started_child=None) -> tuple[int, str]:
    command = ['claude', '-p', '--model', model, '--permission-mode', 'dontAsk',
               '--permission-prompts', 'none', '--allowedTools', tools,
               '--no-session-persistence', '--output-format', 'stream-json', '--verbose']
    # Prevent API-billed credentials/provider overrides from taking precedence
    # over the existing subscription login. Never print the environment.
    child_env = {k: v for k, v in env.items() if k not in API_ENV and k != 'CLAUDECODE'}
    started = time.monotonic()
    for source in (Path.home()/'.claude/settings.json', repo/'.claude/settings.json',
                   repo/'.claude/settings.local.json'):
        if source.exists():
            setting = json.loads(source.read_text())
            if setting.get('apiKeyHelper') or set(setting.get('env', {})) & API_ENV:
                return 78, 'api_configuration_refused'
    try:
        auth = subprocess.run(['claude', 'auth', 'status', '--json'], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              cwd=repo, env=child_env, timeout=min(30, timeout))
        status = json.loads(auth.stdout)
    except subprocess.TimeoutExpired:
        return 124, 'timeout'
    except (ValueError, OSError):
        return 78, 'subscription_login_unavailable'
    subscription = status.get('authMethod') == 'claude.ai' or (
        status.get('authMethod') == 'oauth_token' and
        status.get('apiProvider') == 'firstParty' and bool(child_env.get('CLAUDE_CODE_OAUTH_TOKEN')))
    if auth.returncode or status.get('loggedIn') is not True or not subscription:
        return 78, 'subscription_login_required'
    # Auth readback may include account identifiers; none are written to logs.
    if time.monotonic()-started >= timeout:
        return 124, 'timeout'
    messages: queue.Queue[tuple[str, str | None]] = queue.Queue()
    def pump(stream, channel):
        try:
            for line in iter(stream.readline, ''):
                messages.put((channel, line))
        finally:
            stream.close()
            messages.put((channel, None))
    task_id = env['CARR_HEADLESS_TASK_ID']
    run_id = env['CARR_HEADLESS_RUN_ID']
    receipt = env['CARR_HEADLESS_RECEIPT']
    task_instructions = prompt.read_text()
    instruction = (f'<scheduled-task name="{task_id}">\n' + task_instructions +
        f'\n</scheduled-task>\nHEADLESS COMPLETION CONTRACT: run_id={run_id}. '
        'Only after verifying the task done-condition, write a private JSON receipt at '+receipt+'. '
        'Use schema carr-headless-completion/v1, task_id '+task_id+', run_id '+run_id+', '
        'outcome completed or noop, and artifacts [{"path":"absolute retained artifact or '
        'record readback path","sha256":"sha256 of its bytes"}]. '
        'Evidence must be UTF-8 output or readback that establishes every task done-condition. '
        'Natural-language SOP acceptance needs review; do not self-certify criteria. The verifier compares '
        'the artifact bytes against the pre-launch acceptance_contract JSON criteria. '
        'An assertion that the conversation ended is insufficient. '
        'A stopped, blocked, partial or failed task must not write a completion receipt. '
        'Keep business content out of stdout and stderr.\n')
    with tempfile.TemporaryFile(mode='w+') as input_handle:
        input_handle.write(instruction)
        input_handle.seek(0)
        # The child cannot execute model work until its identity is fsynced.
        # If the wrapper dies before acknowledging that append, closing the
        # pipe makes the launcher exit without ever invoking the CLI.
        ready_write: int | None
        ready_read, ready_write = os.pipe()
        launcher = ('import os,sys; fd=int(sys.argv[1]); '
                    'ready=os.read(fd,1); os.close(fd); '
                    'sys.exit(130) if ready != b"1" else None; '
                    'os.execvpe(sys.argv[2],sys.argv[2:],os.environ)')
        try:
            child = subprocess.Popen([sys.executable, '-c', launcher,
                                      str(ready_read), *command],
                                     stdin=input_handle, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, errors='replace',
                                     cwd=repo, env=child_env, start_new_session=True,
                                     pass_fds=(ready_read,))
        except BaseException:
            os.close(ready_write)
            raise
        finally:
            os.close(ready_read)
    threads = [threading.Thread(target=pump, args=(stream, name), daemon=True)
               for name, stream in [('stdout', child.stdout), ('stderr', child.stderr)]]
    for thread in threads:
        thread.start()
    done = 0
    result = None
    forced = None
    reason = ''
    try:
        if started_child:
            started_child(child)
        os.write(ready_write, b'1')
        os.close(ready_write)
        ready_write = None
        while done < 2 or child.poll() is None:
            if time.monotonic()-started >= timeout:
                forced, reason = 124, 'timeout'
                break
            try:
                channel, line = messages.get(timeout=min(0.05, timeout))
            except queue.Empty:
                continue
            if line is None:
                done += 1
                continue
            log.write(log_text(line))
            log.flush()
            if channel != 'stdout':
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get('subtype') == 'permission_denied' or event.get('permission_denials'):
                forced, reason = 77, 'permission_denied'
                break
            if event.get('type') == 'result':
                result = event
        if forced is not None:
            return forced, reason
        rc = child.wait()
        if rc:
            return rc if rc > 0 else 128-rc, 'claude_nonzero'
        if (not result or result.get('is_error') is not False or
                result.get('subtype') != 'success' or
                not isinstance(result.get('result'), str) or not result['result'].strip()):
            return 65, 'missing_or_failed_result'
        try:
            outcome = verify_completion(Path(receipt), task_id, run_id, task_instructions,
                                        deadline=started+timeout)
        except (OSError, ValueError, KeyError, TypeError):
            if time.monotonic()-started >= timeout:
                return 124, 'timeout'
            return 65, 'invalid_completion_receipt'
        if time.monotonic()-started >= timeout:
            return 124, 'timeout'
        return 0, outcome
    finally:
        if ready_write is not None:
            os.close(ready_write)
        # Kill surviving children even when the parent has already exited.
        _terminate(child)
        for thread in threads:
            thread.join(timeout=1)
        while not messages.empty():
            _, line = messages.get_nowait()
            if line is not None:
                log.write(log_text(line))


def record_failure(repo: Path, task_id: str, code: int, reason: str, log_path: Path,
                   episode: str = 'initial') -> bool:
    # The live add-loop taxonomy has no defect kind. Use its supported
    # open_loop kind, explicitly titled as a defect. Success starts a new
    # failure episode; failures within one episode deduplicate by task.
    args = {'idempotency_key': f'headless-defect:{task_id}:{episode}', 'kind': 'open_loop',
            'domain': 'system', 'owner': 'Claude',
            'title': f'Defect: headless task {task_id} failed',
            'body': f'Defect: headless task {task_id} failed. '
                    f'Inspect out/headless/{task_id}/ledger.jsonl and its latest failure log; '
                    f'repair the failure, run bin/headless-task {task_id}, '
                    'and verify a successful ledger row plus run.sh health.',
            'source_note': f'headless-task:{task_id}', 'marker': 'none',
            'blocker': 'capability',
            'blocker_detail': f'The unattended {task_id} executor failed; an attended session must inspect its local ledger and repair the named tool, runtime or login dependency.'}
    # A stable task key makes repeated failures one record-layer write. A
    # failed record write remains visible in the log and never becomes success.
    result = subprocess.run([str(repo/'run.sh'), 'call', 'add-loop', json.dumps(args)],
                            cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=30)
    if result.returncode:
        return False
    # Verify the record acknowledgement; zero wrapper exit alone is insufficient.
    try:
        return json.loads(result.stdout[result.stdout.index('{'):]).get('ok') is True
    except (ValueError, json.JSONDecodeError):
        return False


def queue_alert(folder: Path, task_id: str, reason: str, episode: str) -> None:
    path = folder/'alerts.json'
    alerts = json.loads(path.read_text()) if path.exists() else []
    key = f'{task_id}:{episode}:{reason}'
    if not any(a['key'] == key for a in alerts):
        alerts.append({'key':key, 'episode':episode+':'+reason, 'reason':reason, 'delivered':False})
        write_json(path, alerts)


def replay_alerts(repo: Path, task_id: str, folder: Path, log_path: Path) -> None:
    path = folder/'alerts.json'
    if not path.exists():
        return
    alerts = json.loads(path.read_text())
    for alert in alerts:
        if alert['delivered']:
            continue
        try:
            delivered = record_failure(repo, task_id, 70, alert['reason'], log_path, alert['episode'])
        except Exception:
            delivered = False
        if delivered:
            alert['delivered'] = True
            write_json(path, alerts)


def replay_runs(repo: Path, task_id: str, folder: Path) -> None:
    # A terminal append is the durable publication intent. Recover from that
    # source even if the wrapper died before writing the outbox projection.
    # Acknowledgements append a successor with the same stable correlation.
    path = folder/'pending-runs.json'
    terminal = {row['run_id']:row for row in ledger_rows(folder/'ledger.jsonl')
                if row.get('end') and row.get('status') in ('success','failed')}
    pending = [row for row in terminal.values() if not row.get('canonical_recorded')]
    write_json(path, pending)
    for row in pending:
        try:
            row['canonical_recorded'] = record_run(repo, task_id, row)
        except Exception:
            row['canonical_recorded'] = False
        if row['canonical_recorded']:
            append_ledger(folder/'ledger.jsonl', row)
    write_json(path, [row for row in pending if not row.get('canonical_recorded')])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task_id')
    parser.add_argument('--repo', type=Path, default=Path.home()/'carr-system')
    parser.add_argument('--timeout-seconds', type=float)
    parser.add_argument('--model', default='sonnet')
    parser.add_argument('--allowed-tools', default=DEFAULT_TOOLS)
    parser.add_argument('--check-fresh-since')
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', args.task_id):
        parser.error('task id must contain lowercase letters, digits and hyphens')
    repo = args.repo.resolve()
    settings = config(repo)
    task = settings['tasks'][args.task_id]
    timeout = args.timeout_seconds if args.timeout_seconds is not None else task.get('timeout_seconds', 5400)
    if timeout <= 0 or timeout != timeout or timeout == float('inf'):
        parser.error('timeout must be finite and positive')
    os.umask(0o077)
    folder = repo/'out/headless'/args.task_id
    private_folder(repo/'out/headless')
    private_folder(folder)
    launch_log = folder/'launchd.log'
    launch_log.touch(exist_ok=True)
    launch_log.chmod(0o600)
    if args.check_fresh_since:
        since = datetime.fromisoformat(args.check_fresh_since.replace('Z', '+00:00'))
        fresh = any(r.get('status') == 'success' and r.get('canonical_recorded') is True and
                    datetime.fromisoformat(r['end'].replace('Z', '+00:00')) >= since
                    for r in ledger_rows(folder/'ledger.jsonl'))
        print('FRESH verified canonical headless completion' if fresh else 'STALE no verified completion')
        return 0 if fresh else 1
    start = datetime.now(UTC)
    window = stamp(schedule_window(task['cron'], start, settings['timezone']))
    log_path = folder/(start.strftime('%Y%m%dT%H%M%S.%fZ')+'.log')
    ledger = folder/'ledger.jsonl'
    with (folder/'run.lock').open('a') as lock, log_path.open('x') as log:
        row: dict[str, Any] = {'run_id':str(uuid.uuid4()), 'start': stamp(start), 'end': None, 'exit_code': None,
               'log_path': str(log_path), 'window': window, 'status': 'running'}
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            row.update(end=stamp(datetime.now(UTC)), exit_code=0, status='skipped', reason='already_running')
            log.write('SKIP already_running\n')
            print(f'SKIP {args.task_id}: already_running')
            return 0
        code, reason = 70, 'runner_error'
        episode = 'initial'
        ledger_readable = False
        old_handlers = {sig:signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
        def interrupted(signum, frame):
            raise InterruptedRun(signum)
        for sig in old_handlers:
            signal.signal(sig, interrupted)
        try:
            try:
                prior, repaired = recover_ledger(ledger)
            except (ValueError, UnicodeError):
                queue_alert(folder, args.task_id, 'ledger_corrupt', 'initial')
                raise
            if repaired:
                queue_alert(folder, args.task_id, 'ledger_torn_append', 'initial')
            ledger_readable = True
            reconcile_runs(ledger, prior)
            prior = ledger_rows(ledger)
            for prior_row in prior:
                if prior_row.get('reason') == 'interrupted':
                    queue_alert(folder, args.task_id, 'interrupted', prior_row['run_id'])
            replay_alerts(repo, args.task_id, folder, log_path)
            replay_runs(repo, args.task_id, folder)
            episode = max((r['window'] for r in prior if r.get('status') == 'success'), default='initial')
            if any(r.get('window') == window and r.get('status') == 'success' and r.get('exit_code') == 0 for r in prior):
                row.update(end=stamp(datetime.now(UTC)), exit_code=0, status='skipped', reason='window_succeeded')
                append_ledger(ledger, row)
                log.write('SKIP window_succeeded\n')
                print(f'SKIP {args.task_id}: window_succeeded')
                return 0
            if task.get('monthly'):
                gate = subprocess.run([sys.executable, str(repo/'bin/monthly-gate.py'),
                    args.task_id, '--quiet'], cwd=repo, capture_output=True, timeout=30)
                if gate.returncode == 1:
                    row.update(end=stamp(datetime.now(UTC)), exit_code=0, status='skipped', reason='monthly_completed')
                    append_ledger(ledger, row)
                    print(f'SKIP {args.task_id}: monthly_completed')
                    return 0
                if gate.returncode != 0:
                    raise ValueError('monthly predicate failed')
            row['wrapper_pid'] = os.getpid()
            row['wrapper_birth'] = process_birth(os.getpid())
            append_ledger(ledger, row)
            log.write(f'START {args.task_id} {stamp(start)} window={window}\n')
            log.flush()
            prompt = Path.home()/'.claude/scheduled-tasks'/args.task_id/'SKILL.md'
            env = {**os.environ, 'CARR_HEADLESS_TASK_ID':args.task_id,
                   'CARR_HEADLESS_RUN_ID':row['run_id'],
                   'CARR_HEADLESS_RECEIPT':str(folder/(row['run_id']+'.completion.json'))}
            def started_child(child):
                birth = process_birth(child.pid)
                if not birth:
                    raise ValueError('child identity unavailable')
                row.update(child_pid=child.pid, child_birth=birth)
                append_ledger(ledger, row)
            code, reason = _claude(prompt, repo, args.model, args.allowed_tools, timeout,
                                   log, env, started_child)
        except InterruptedRun as exc:
            code, reason = exc.code, 'interrupted'
        except Exception as exc:
            # The exception type is diagnostic; exception text can quote a
            # secret, prompt, configuration, or raw provider response.
            log.write(f'RUNNER_ERROR {type(exc).__name__}\n')
        finally:
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
        row.update(end=stamp(datetime.now(UTC)), exit_code=code,
                   status=('noop' if reason == 'noop' else 'success') if code == 0 else 'failed', reason=reason)
        append_ledger(ledger, row)
        log.write(f'END exit={code} reason={reason}\n')
        if code:
            queue_alert(folder, args.task_id, reason, episode)
            replay_alerts(repo, args.task_id, folder, log_path)
            pending_alerts = any(not a['delivered'] for a in json.loads((folder/'alerts.json').read_text()))
            log.write('ERROR pending_alert_delivery\n' if pending_alerts else 'DEFECT_RECORDED\n')
        # Alert delivery uses its own durable outbox. Corrupt history must
        # remain a hard execution failure without blocking those alerts or
        # rebuilding canonical publication intent from unauthenticated rows.
        if ledger_readable and row['status'] in ('success', 'failed'):
            replay_runs(repo, args.task_id, folder)
        print(f'{"OK" if code == 0 else "FAIL"} {args.task_id}: {reason}; exit={code}; log={log_path}')
        return code


def health_rows(repo: Path, home: Path, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(UTC)
    try:
        settings = config(repo)
        if not isinstance(settings['tasks'], dict):
            raise ValueError('manifest tasks')
        ZoneInfo(settings['timezone'])
    except Exception as exc:
        return [{'task_id':'manifest', 'status':'WARN', 'reason':'manifest_unreadable',
            'hard_error':True, 'time_rolling':False,
            'line':f'WARN headless/manifest — unreadable manifest ({type(exc).__name__}) · owner: Claude · '
                   'on breach: repair ops/headless-tasks/tasks.json · verify: ./run.sh health · auto-clear: readable valid manifest'}]
    rows = []
    for installed in sorted((home/'Library/LaunchAgents').glob(PREFIX+'*.plist')):
        task_id = installed.name[len(PREFIX):-len('.plist')]
        response = (f'owner: Claude · on breach: inspect out/headless/{task_id}/ and launchd state; '
                    f'repair then run bin/headless-task {task_id} · verify: ./run.sh health and '
                    'successful ledger row · auto-clear: next success within schedule interval + grace')
        try:
            task = settings['tasks'][task_id]
            successes = [r for r in ledger_rows(repo/'out/headless'/task_id/'ledger.jsonl')
                         if r.get('status') == 'success' and r.get('exit_code') == 0]
            # Installation is due at its first slot. A completed monthly
            # window stays fresh until the next month's first slot.
            last = max((datetime.fromisoformat(r['end'].replace('Z', '+00:00')) for r in successes),
                       default=datetime.fromtimestamp(installed.stat().st_mtime, UTC))
            slots = _slots(task['cron'], last-timedelta(microseconds=1) if not successes else last,
                           settings['timezone'], True)
            due = next(slots)
            if successes and task.get('monthly'):
                tz = ZoneInfo(settings['timezone'])
                completed_month = last.astimezone(tz).strftime('%Y-%m')
                while due.astimezone(tz).strftime('%Y-%m') == completed_month:
                    due = next(slots)
            deadline = due+timedelta(seconds=task.get('grace_seconds', 3600))
            status = 'WARN' if now > deadline or last > now+timedelta(seconds=60) else 'OK'
            detail = f'last success {stamp(last)}' if successes else 'awaiting first success (installation grace)'
            reason, hard, rolling = 'missed_run', False, True
            folder = repo/'out/headless'/task_id
            alerts = json.loads((folder/'alerts.json').read_text()) if (folder/'alerts.json').exists() else []
            pending = json.loads((folder/'pending-runs.json').read_text()) if (folder/'pending-runs.json').exists() else []
            if any(not a['delivered'] for a in alerts) or pending:
                status, detail = 'WARN', 'pending canonical run or defect delivery'
                reason, hard, rolling = 'receipt_delivery_pending', True, False
        except Exception as exc:
            status, detail = 'WARN', f'unreadable task/ledger ({type(exc).__name__})'
            reason, hard, rolling = 'state_unreadable', True, False
        rows.append({'task_id': task_id, 'status': status, 'reason':reason,
                     'hard_error':hard, 'time_rolling':rolling,
                     'line': f'{status} headless/{task_id} — {detail} · {response}'})
    return rows


def install_main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Install reviewed headless launchd jobs; no desktop task changes.')
    parser.add_argument('action', choices=['install', 'uninstall'])
    parser.add_argument('task_ids', nargs='*')
    args = parser.parse_args(argv)
    home = Path.home()
    repo = home/'carr-system'
    settings = config(repo)
    selected = args.task_ids or sorted(settings['tasks'])
    if set(selected)-set(settings['tasks']):
        parser.error('unknown task id')
    from lib.claude_scheduler_native import system_timezone
    if args.action == 'install' and system_timezone() != settings['timezone']:
        parser.error('host timezone differs from reviewed desktop schedules')
    agents = home/'Library/LaunchAgents'
    agents.mkdir(parents=True, exist_ok=True)
    domain = f'gui/{os.getuid()}'
    private_folder(repo/'out/headless')
    failures = []
    # Serialize the entire read/bootout/write/bootstrap transition, including
    # separate installer processes. A retry reconciles each selected service;
    # successful batch members remain installed and are idempotent on retry.
    with (repo/'out/headless/install.lock').open('a') as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        for task_id in selected:
            try:
                _install_task(args.action, task_id, home, repo, agents, domain, settings)
                print(f'{args.action} {task_id}')
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                failures.append(exc)
                print(f'FAIL {args.action} {task_id}: {type(exc).__name__}; retry selected batch to reconcile')
    if failures:
        raise failures[0]
    return 0


def _install_task(action: str, task_id: str, home: Path, repo: Path,
                  agents: Path, domain: str, settings: dict) -> None:
    label = PREFIX+task_id
    target = agents/(label+'.plist')
    probe = subprocess.run(['launchctl', 'print', domain+'/'+label], capture_output=True)
    if probe.returncode not in (0, 3, 113):
        raise subprocess.CalledProcessError(probe.returncode, probe.args)
    loaded = probe.returncode == 0
    if action == 'uninstall':
        if loaded:
            subprocess.run(['launchctl', 'bootout', domain+'/'+label], check=True)
        if target.exists():
            quarantine = repo/'out/headless/uninstalled'
            private_folder(quarantine)
            target.replace(quarantine/(uuid.uuid4().hex+'-'+target.name))
        return
    if not (home/'.claude/scheduled-tasks'/task_id/'SKILL.md').is_file():
        raise ValueError('task prompt missing')
    folder = repo/'out/headless'/task_id
    private_folder(folder)
    launch_log = folder/'launchd.log'
    launch_log.touch(mode=0o600, exist_ok=True)
    launch_log.chmod(0o600)
    source = repo/'ops/headless-tasks'/(label+'.plist')
    content = source.read_text().replace('{{HOME}}', str(home)).replace('{{REPO}}', str(repo))
    plist = plistlib.loads(content.encode())
    if plist['StartCalendarInterval'] != calendar_entries(settings['tasks'][task_id]['cron']):
        raise ValueError('template schedule differs from reviewed manifest')
    plist['Umask'] = 0o077
    expected = plistlib.dumps(plist)
    if loaded and target.exists() and target.read_bytes() == expected:
        return
    if loaded:
        subprocess.run(['launchctl', 'bootout', domain+'/'+label], check=True)
    temporary = target.with_suffix('.tmp')
    with temporary.open('wb') as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(expected)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(target)
    subprocess.run(['launchctl', 'bootstrap', domain, str(target)], check=True)
