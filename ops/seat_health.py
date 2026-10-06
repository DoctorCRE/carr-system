from datetime import datetime, timezone
import json
import math
import re
import time
from pathlib import Path

EXPECTED = '323'
PROMPT = ('Compute 17 * 19. Reply with only the decimal integer. '
          'Do not read credentials, change files, call record tools, or delegate.')
SEATS = {'opus-studio': 'opus-5.5', 'sol-studio': 'gpt-6.1-sol',
         'sol-macbook': 'gpt-6.1-sol', 'grok': 'grok-4.7',
         'jev': 'jev-1.13.0', 'e2e-auth': 'gpt-6-luna'}
FAILURE_LAYERS = frozenset(('auth', 'network', 'runner_wrapper', 'model'))


def action(seat, layer):
    wrappers = {'opus-studio': 'out/orch/opus-run.sh on Studio',
                'sol-studio': 'out/orch/sol-run.sh on Studio',
                'sol-macbook': 'ssh macbook and out/orch/mac-sol-run.sh on MacBook',
                'grok': 'bin/grok-run.sh on Studio', 'jev': 'the admitted ask-jev Worker path',
                'e2e-auth': 'doctorcre-app/node_modules/.bin/e2e on Studio'}
    logins = {'opus-studio': 'the Claude headless login on Studio',
              'sol-studio': 'the Codex login on Studio',
              'sol-macbook': 'SSH access and the Codex login on MacBook',
              'grok': 'grok login on Studio',
              'jev': 'the shared Jev Worker admission credential',
              'e2e-auth': 'e2e login openai on Studio'}
    remedy = (f'Restore {logins[seat]}.' if layer == 'auth' else
              f'Repair {wrappers[seat]} and its completion contract.' if layer == 'runner_wrapper' else
              f'Restore network reachability from {wrappers[seat]}.' if layer == 'network' else
              f'Repair model selection or the response contract in {wrappers[seat]}.' if layer == 'model' else
              f'Rerun {wrappers[seat]} through the daily probe if its evidence expires.')
    verification = ('e2e models openai must list gpt-6-luna' if seat == 'e2e-auth'
                    else 'this seat must return exactly 323 for 17 * 19')
    return (f'on breach: open/update one deduplicated loop seat-health:{seat}; '
            f'owner orchestrator; {remedy} Verify: {verification}; '
            'auto-clear on the next passing daily probe.')


def assess(seat, answer, diagnostics, code, latency, *, reason=None, usage=None):
    passed = code == 0 and answer.strip() == EXPECTED
    layer = None
    if not passed:
        text = diagnostics.lower()
        if code == 0:
            layer = 'model'
        elif re.search(r'authentication (?:failed|required)|auth_failed|sign.in|not.logged.in|login required|token.*expired|expired.*token|admission_secret_missing|permission denied|unauthorized|401|403', text):
            layer = 'auth'
        elif re.search(r'network|fetch failed|enotfound|econn|dns|connection|unreachable|could not resolve hostname|no route to host', text):
            layer = 'network'
        elif code == 5:
            layer = 'model'
        elif code != 0:
            layer = 'runner_wrapper'
        else:
            layer = 'model'
    return {'seat': seat, 'model': SEATS[seat], 'passed': passed,
            'dispatchable': passed, 'latency_seconds': round(latency, 3),
            'failing_layer': layer, 'reason': reason or ('exact_match' if passed else
            'authentication_failed' if layer == 'auth' else
            'network_unavailable' if layer == 'network' else
            'runner_timeout' if code == 124 else 'runner_failed' if code else
            'empty_answer' if not answer.strip() else 'answer_mismatch'),
            'usage': usage, 'action': action(seat, layer)}


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temp.replace(path)


def reconcile(row, state_path, verb):
    """Save mutation intent before calling so an interrupted run reuses its key."""
    import uuid
    path = Path(state_path)
    state = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(state, dict) or any(key in state and not isinstance(state[key], str) for key in ('loop_id', 'open_key', 'mutation_key')):
        return 'error'
    if row['passed'] and not state.get('loop_id') and not state.get('open_key'):
        return 'none'
    for attempt in range(2):
        if not state.get('loop_id'):
            state.setdefault('open_key', 'seat-health:' + row['seat'] + ':' + str(uuid.uuid4()))
            atomic_json(path, state)
            result = verb('add-loop', {'idempotency_key': state['open_key'],
                'kind': 'open_loop', 'owner': 'claude', 'domain': 'system',
                'body': row['action'], 'source_note': 'Daily AI seat probe: ' + row['seat'],
                'blocker': 'other_lane', 'blocker_detail': 'Orchestrator runner remediation for ' + row['seat']})
            if not isinstance(result, dict) or result.get('ok') is not True or not isinstance(result.get('loop_id'), str) or not result['loop_id']:
                return 'error'
            state['loop_id'] = result['loop_id']
            atomic_json(path, state)
        current = verb('read-loop', {'loop_id': state['loop_id']})
        if not isinstance(current, dict) or current.get('error') or not isinstance(current.get('loop'), dict):
            return 'error'
        loop = current['loop']
        if loop.get('status') not in ('done', 'dropped'):
            break
        atomic_json(path, {})
        state = {}
        if row['passed']:
            return 'cleared'
    else:
        return 'error'
    version = loop.get('version', loop.get('current_version'))
    if type(version) is not int or version < 1:
        return 'error'
    intent = {'verb': 'close-loop' if row['passed'] else 'update-loop',
              'base_version': version, 'body': row['action']}
    if state.get('intent') != intent:
        state.update(intent=intent, mutation_key='seat-health:' + str(uuid.uuid4()))
        atomic_json(path, state)
    payload = {'idempotency_key': state['mutation_key'], 'loop_id': state['loop_id'],
               'base_version': version}
    if row['passed']:
        payload.update(resolution='done', outcome='Daily exact-value probe passed for ' + row['seat'])
    else:
        payload['body'] = row['action']
    result = verb(intent['verb'], payload)
    if not isinstance(result, dict) or result.get('ok') is not True:
        return 'error'
    atomic_json(path, {} if row['passed'] else {'loop_id': state['loop_id'], 'open_key': state['open_key']})
    return 'cleared' if row['passed'] else 'open'


def dispatchable(report, seat, *, now=None):
    """Unknown, stale, future or failed evidence never admits a dispatch."""
    try:
        now = now or datetime.now(timezone.utc)
        at = datetime.fromisoformat(report['seats'][seat]['observed_at'].replace('Z', '+00:00'))
        age = (now - at).total_seconds()
        return (report['schema'] == 'carr-seat-health/v1' and 0 <= age <= 86400
                and report['seats'][seat]['passed'] is True
                and report['seats'][seat]['dispatchable'] is True)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def numeric_usage(value):
    """Only named numeric telemetry crosses the report surface."""
    if not isinstance(value, dict):
        return None
    fields = ('input_tokens', 'output_tokens', 'total_tokens', 'cost_usd',
              'used_percent', 'window_minutes', 'resets_at', 'five_hour_pct', 'weekly_pct')
    result = {key: value[key] for key in fields if type(value.get(key)) in (int, float)
              and math.isfinite(value[key]) and value[key] >= 0}
    for key in ('primary', 'secondary', 'usage', 'windows'):
        child = numeric_usage(value.get(key))
        if child:
            result[key] = child
    return result or None


def codex_usage(sessions):
    latest = None
    for path in Path(sessions).expanduser().rglob('*.jsonl'):
        try:
            with path.open() as source:
                for line in source:
                    if '"rate_limits"' not in line:
                        continue
                    try:
                        row = json.loads(line)
                        usage = numeric_usage(row.get('payload', {}).get('rate_limits'))
                        at = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00'))
                        if usage and at.tzinfo and (latest is None or at > latest[0]):
                            latest = (at, usage)
                    except (ValueError, KeyError, TypeError, AttributeError):
                        continue
        except OSError:
            continue
    return {'observed_at': latest[0].isoformat(), **latest[1]} if latest else None


def execute(argv, cwd, timeout, env=None):
    import os
    import signal
    import subprocess
    import time
    started = time.monotonic()
    process = None
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        stdout, stderr = process.communicate(timeout=timeout)
        return stdout, stderr, process.returncode, time.monotonic() - started
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        return '', 'runner timed out', 124, time.monotonic() - started
    except OSError:
        return '', 'runner unavailable', 127, time.monotonic() - started


def verb_call(repo, name, payload):
    repo = Path(repo).resolve()
    output, _, code, _ = execute([str(repo / 'run.sh'), 'call', name,
                                 json.dumps(payload)], repo, 35)
    if code:
        return {'ok': False}
    try:
        value = json.loads(output[output.index('{'):])
        return value if isinstance(value, dict) else {'ok': False}
    except (ValueError, TypeError):
        return {'ok': False}


def probe_jev(run_id):
    """One paid Worker invocation through the same shared admission as builders."""
    import typesafe_client as ts
    import jev_semantic
    try:
        value = jev_semantic.ask({'task': PROMPT, 'run_id': run_id},
            {'answer': ts.choice('Choose the result of 17 * 19.',
                               {'323': 'Select if this is the product.',
                                '324': 'Select if this is the product.'})},
            caller='seat_health', version='1', client=ts, facets=[], purpose='call',
            session_id=run_id, timeout=20, retries=0, cache_ttl_seconds=0)
        if value.get('cache_hit') is True or value.get('model') != SEATS['jev']:
            raise ValueError('probe requires a fresh pinned model observation')
        print(json.dumps({'ok': True, 'answer': value['answers']['answer'].get('choice'),
                          'model': value.get('model'), 'usage': numeric_usage(value.get('usage'))}))
        return 0
    except (ts.TypeSafeError, ValueError, KeyError, OSError, TimeoutError):
        print(json.dumps({'ok': False, 'error': 'Jev probe unavailable'}))
        return 1


def bounded_codex_usage(sessions, timeout):
    """Telemetry shares the probe deadline; terminate scans that exceed it."""
    import multiprocessing
    context = multiprocessing.get_context('fork')
    receive, send = context.Pipe(duplex=False)
    def collect():
        try:
            send.send(codex_usage(sessions))
        except (OSError, ValueError, TypeError, AttributeError):
            send.send(None)
        finally:
            send.close()
    process = context.Process(target=collect)
    process.start()
    send.close()
    try:
        if receive.poll(max(0, timeout)):
            try:
                return receive.recv(), False
            except EOFError:
                return None, False
        return None, True
    finally:
        if process.is_alive():
            process.terminate()
        process.join(.05)
        if process.is_alive():
            process.kill()
            process.join()
        receive.close()


def probe(seat, runtime, run_dir, timeout, e2e_cli):
    import os
    import shlex
    import sys
    started = time.monotonic()
    runtime, run_dir = Path(runtime).resolve(), Path(run_dir).resolve()
    prompt = run_dir / (seat + '.prompt')
    log = run_dir / (seat + '.log')
    prompt.write_text(PROMPT + '\n')
    env = dict(os.environ)
    for key in ('GROK_RUN_FAKE_NDJSON', 'GROK_RUN_RECEIPT'):
        env.pop(key, None)
    usage = None
    if seat in ('opus-studio', 'sol-studio'):
        wrapper = 'opus-run.sh' if seat == 'opus-studio' else 'sol-run.sh'
        argv = ['/bin/zsh', str(runtime / 'out/orch' / wrapper), str(run_dir), str(log), str(prompt)]
    elif seat == 'sol-macbook':
        remote = ('umask 077; repo="$HOME/carr-system"; '
                  'probe="$repo/out/seat-health-runs/' + run_dir.name + '"; '
                  'mkdir -p "$probe"; printf %s ' + shlex.quote(PROMPT) + ' > "$probe/prompt"; '
                  '/bin/zsh "$repo/out/orch/mac-sol-run.sh" "$probe" "$probe/log" "$probe/prompt"; '
                  'rc=$?; cat "$probe/log"; exit "$rc"')
        argv = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', 'macbook', remote]
    elif seat == 'grok':
        env['GROK_RUN_RECEIPT'] = str(run_dir / 'grok-receipt.json')
        argv = ['bash', str(Path(__file__).resolve().parents[1] / 'bin/grok-run.sh'),
                '--prompt-file', str(prompt), '--no-sign-in-alert', '--max-turns', '2', '--timeout-seconds', str(min(timeout, 1800))]
    elif seat == 'jev':
        argv = [sys.executable, str(Path(__file__).with_name('seat-health.py')), '--jev-task', run_dir.name]
    else:
        argv = [str(e2e_cli), 'models', 'openai']
    output, diagnostics, code, latency = execute(argv, runtime, timeout, env)
    answer = output
    if seat in ('opus-studio', 'sol-studio', 'sol-macbook'):
        raw = log.read_text() if seat != 'sol-macbook' and log.exists() else output
        terminal = 'OPUS' if seat == 'opus-studio' else 'CODEX'
        exits = re.findall(r'(?m)^' + terminal + r'_EXIT (\d+)\s*$', raw)
        completed = re.search(r'(?m)^' + terminal + r'_EXIT (\d+)\s*\Z', raw)
        code = code or (int(completed[1]) if completed and len(exits) == 1 else 4)
        raw = re.sub(r'(?m)^' + terminal + r'_EXIT \d+\s*$', '', raw).strip()
        if seat.startswith('sol-'):
            models = re.findall(r'(?m)^model:\s*(\S+)\s*$', raw)
            if models != [SEATS[seat]]:
                code = code or 5
            footer = re.search(r'(?s)\ntokens used\n[0-9,]+\n(.*)$', raw)
            answer = footer[1] if footer else (raw.rsplit('\ncodex\n', 1)[-1] if '\ncodex\n' in raw else '')
        else:
            try:
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError('invalid result')
                answer = result.get('result', '')
                if result.get('type') != 'result' or result.get('subtype') != 'success' or result.get('is_error') is not False:
                    code = code or 4
                models = result.get('modelUsage')
                if not isinstance(models, dict) or set(models) != {SEATS[seat]}:
                    code = code or 5
                usage = numeric_usage(result)
            except (ValueError, TypeError):
                answer, code = '', code or 4
    elif seat == 'jev':
        try:
            result = json.loads(output)
            if not isinstance(result, dict):
                raise ValueError('invalid result')
            answer = result.get('answer') or ''
            usage = numeric_usage(result.get('usage'))
            error = result.get('error')
            if isinstance(error, str):
                diagnostics += error
            if result.get('model') != SEATS['jev'] or result.get('ok') is not True:
                code = code or 5
        except (ValueError, TypeError):
            answer, code = '', code or 4
    elif seat == 'e2e-auth':
        diagnostics += output
        ids = [line.split()[0] for line in output.splitlines() if line.strip()]
        answer = EXPECTED if SEATS['e2e-auth'] in ids else ''
    elif seat == 'grok':
        receipt = run_dir / 'grok-receipt.json'
        if receipt.exists():
            try:
                usage = numeric_usage(json.loads(receipt.read_text()))
            except ValueError:
                pass
    if seat == 'sol-studio':
        usage, timed_out = bounded_codex_usage(Path.home() / '.codex/sessions', max(0, timeout - (time.monotonic() - started)))
        if timed_out:
            code, diagnostics = 124, 'runner timed out collecting telemetry'
    elif seat == 'sol-macbook':
        import inspect
        script = ('from pathlib import Path\nfrom datetime import datetime\nimport json,math\n'
                  + inspect.getsource(numeric_usage) + '\n' + inspect.getsource(codex_usage)
                  + '\nprint(json.dumps(codex_usage(Path.home()/".codex/sessions")))')
        raw, _, rc, _ = execute(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                                'macbook', 'python3 -c ' + shlex.quote(script)], runtime, max(.001, timeout - (time.monotonic() - started)))
        if rc == 124:
            code, diagnostics = 124, 'runner timed out collecting telemetry'
        if not rc:
            try:
                value = json.loads(raw)
                usage = numeric_usage(value)
                if usage and value.get('observed_at'):
                    usage['observed_at'] = datetime.fromisoformat(value['observed_at']).isoformat()
            except (ValueError, TypeError, AttributeError):
                pass
    elif seat == 'opus-studio' and usage is None:
        path = runtime / 'out/orch/budget/claude-usage.json'
        if path.exists():
            try:
                value = json.loads(path.read_text())
                usage = numeric_usage(value)
                if usage:
                    at = datetime.fromisoformat(value['observed_at'].replace('Z', '+00:00'))
                    usage['observed_at'] = at.isoformat()
                    usage['stale'] = (datetime.now(timezone.utc) - at).total_seconds() > 1800
            except (ValueError, KeyError, TypeError, AttributeError):
                usage = None
    if not isinstance(answer, str):
        answer, code = '', code or 4
    latency = time.monotonic() - started
    if latency > timeout:
        code, diagnostics = 124, 'runner timed out'
    return assess(seat, answer, diagnostics, code, latency, usage=usage)


def health_rows(report):
    report = report if isinstance(report, dict) else {}
    rows = []
    for seat in SEATS:
        passed = dispatchable(report, seat)
        seats = report.get('seats', {})
        row = seats.get(seat, {}) if isinstance(seats, dict) else {}
        row = row if isinstance(row, dict) else {}
        latency = row.get('latency_seconds')
        if type(latency) not in (int, float) or not math.isfinite(latency):
            latency = 'unknown'
        layer = row.get('failing_layer')
        if layer not in FAILURE_LAYERS:
            layer = None if passed else 'runner_wrapper'
        rows.append(f"{'PASS' if passed else 'FAIL'} {seat} latency={latency}s "
                    f"layer={layer or 'none'} · {action(seat, layer)}")
    return rows
