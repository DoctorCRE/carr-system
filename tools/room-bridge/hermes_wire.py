"""One-shot Nous Portal jobs. Provider diagnostics never enter the result ledger."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import nous_budget

CATALOG_URL = 'https://portal.nousresearch.com/api/nous/recommended-models'
USAGE_COUNTERS = ('estimated_cost_usd', 'input_tokens', 'output_tokens', 'cache_read_tokens',
                  'cache_write_tokens', 'reasoning_tokens', 'total_tokens', 'api_calls')
CREDIT_ERROR = re.compile(r'out of credits|payment[_ ]required|insufficient[_ ](?:credits|quota|funds|balance)'
                          r'|quota[_ ](?:exhausted|exceeded)|credits?[_ ](?:exhausted|depleted)'
                          r'|(?:HTTP|status)[_ :=]*402|"(?:status(?:_code)?|code)"\s*:\s*402\b', re.I)


def billing_error(payload):
    if not isinstance(payload, dict):
        return False
    if payload.get('status_code') in (402, '402') or payload.get('status') in (402, '402'):
        return True
    error = payload.get('error')
    return isinstance(error, dict) and (error.get('code') in (402, '402') or bool(CREDIT_ERROR.search(
        ' '.join(str(error.get(key, '')) for key in ('code', 'type', 'message')))))


def native_command(raw):
    command = json.loads(raw)
    if (not isinstance(command, list) or len(command) != 4 or command[1:3] != ['-I', '-c']
            or not all(isinstance(arg, str) for arg in command)):
        raise ValueError('Hermes native runtime unavailable')
    runner = Path(__file__).with_name('hermes_native.py')
    command[3] += '; import runpy; runpy.run_path(' + repr(str(runner)) + ", run_name='__main__')"
    return command


def launcher_command():
    launcher = shutil.which('hermes')
    if not launcher:
        raise ValueError('Hermes native runtime unavailable')
    for line in Path(launcher).read_text().splitlines():
        if not line.startswith('exec '):
            continue
        parts = shlex.split(line)
        if len(parts) != 4 or parts[-1] != '$@':
            break
        python, script = Path(parts[1]), Path(parts[2])
        if (python.is_absolute() and python.is_file() and script.is_absolute()
                and (script.parent / 'run_agent.py').is_file()):
            return [str(python), '-I', '-c', 'import sys; sys.path.insert(0, ' + repr(str(script.parent)) + ')']
    raise ValueError('Hermes native runtime unavailable; launcher is not a recognized Python runtime')


def select_model(payload: dict, family: str) -> str:
    candidates = []
    pattern = r'(?:[^/]+/)?' + ('glm-' if family == 'glm' else 'deepseek-v?') + r'(\d+(?:\.\d+)*)(?:[-:]|$)'
    for item in payload.get('paidRecommendedModels', []):
        model = item.get('modelName', '')
        match = re.match(pattern, model, re.I)
        if match:
            version = tuple(map(int, match[1].split('.')))
            candidates.append((version, 'flash' not in model.lower(), model))
    if not candidates:
        raise ValueError('Nous coding family unavailable')
    return max(candidates)[2]


def run_task(entry: dict, task: str, *, env: dict, run, results_path, run_id) -> dict:
    accounting = {'launched': False, 'usage': {}}
    try:
        results_path = Path(results_path).absolute()
        ledger = nous_budget.Ledger(results_path)
        ledger.reserve(run_id)
        accounting['before_launch'] = lambda: ledger.launch(run_id)
        accounting['allowance'] = str(ledger.estimate)
    except nous_budget.BudgetStop as exc:
        return {'status': 'failed', 'code': 'nous_spend_stop', 'detail': str(exc)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {'status': 'failed', 'code': 'nous_spend_stop', 'detail': 'Nous budget ledger unavailable'}
    env = {**env, 'CARR_NOUS_BUDGET_RESULTS': str(results_path)}
    outcome = _run_task(entry, task, env=env, run=run, accounting=accounting)
    try:
        outcome['budget'] = ledger.settle(run_id, accounting['usage'], accounting['launched'],
                                         accounting.get('exhausted', False), accounting.get('price'))
        if outcome['budget']['stop']:
            outcome['code'] = 'nous_spend_stop'
            if outcome['status'] == 'completed':
                outcome.update(status='failed', detail=outcome['budget']['stop'])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        outcome['budget_error'] = 'Nous run cost unavailable'
        outcome['code'] = 'nous_spend_stop'
        if outcome['status'] == 'completed':
            outcome.update(status='failed', detail='Nous run cost unavailable')
    return outcome


def _run_task(entry: dict, task: str, *, env: dict, run, accounting) -> dict:
    base = {'provider': entry['provider'], 'family': entry['family'], 'effort': entry['effort']}
    deadline = time.monotonic() + entry['timeout_s']

    def invoke(argv, ceiling=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired('hermes job', entry['timeout_s'])
        proc = run(argv, cwd=entry['cwd'], env=env,
                   timeout=min(remaining, ceiling) if ceiling else remaining)
        return proc

    with tempfile.TemporaryDirectory(prefix='room-hermes-') as tmp:
        usage_path = Path(tmp) / 'usage.json'
        try:
            status = invoke(['hermes', 'portal', 'status'], 15)
            auth = re.sub(r'\x1b\[[0-9;]*m', '', status.stdout or '')
            if status.returncode:
                return {**base, 'status': 'failed', 'detail': 'Hermes auth status unavailable'}
            if re.search(r'^\s*Auth:\s*(?:not logged in|not signed in)\s*$', auth, re.M):
                return {**base, 'status': 'failed', 'detail': 'Nous Portal not signed in'}
            if not re.search(r'^\s*Auth:\s*✓?\s*logged in\s*$', auth, re.M):
                return {**base, 'status': 'failed', 'detail': 'Hermes auth status unrecognized'}
            fallback = invoke(['hermes', 'fallback', 'list'], 15)
            chain = re.sub(r'\x1b\[[0-9;]*m', '', fallback.stdout or '')
            if fallback.returncode or not re.search(r'^\s*No fallback providers configured\.\s*$', chain, re.M):
                return {**base, 'status': 'failed', 'detail': 'Hermes fallback chain must be empty'}
            catalog = invoke(['curl', '-q', '-fsS', '--max-time', '15', CATALOG_URL], 15)
            if catalog.returncode:
                raise ValueError('Nous model catalog unavailable')
            payload = json.loads(catalog.stdout)
            model = select_model(payload, entry['family'])
            base['model'] = model
            runtime = invoke(['hermes', '--print-runtime-command', '--module', 'hermes_cli.gateway_client'], 15)
            if runtime.returncode and entry.get('posture') == 'record-layer':
                command = native_command(json.dumps(launcher_command()))
            elif runtime.returncode:
                raise ValueError('Hermes native runtime unavailable')
            else:
                command = native_command(runtime.stdout)
            quote = invoke([*command, '--quote-model', model], 15)
            accounting['price'] = json.loads(quote.stdout) if quote.returncode == 0 else None
            if not accounting['price']:
                raise nous_budget.BudgetStop('Nous model price unavailable; cannot bound run spending')
            accounting['before_launch']()
            accounting['launched'] = True
            env = {**env, 'HERMES_IGNORE_USER_CONFIG': '1', 'HERMES_YOLO_MODE': '1',
                   'HERMES_SINGLE_QUERY_SESSION': '1'}
            proc = invoke([*command, '-z', task, '-m', model, '--reasoning', entry['effort'],
                    '--usage-file', str(usage_path), '--allowance-usd', accounting['allowance'],
                    '--price-json', json.dumps(accounting['price']),
                    *(['--posture', 'record-layer'] if entry.get('posture') == 'record-layer' else [])])
            outcome = {'status': 'completed' if proc.returncode == 0 else 'failed',
                       'exit_code': proc.returncode, 'result': (proc.stdout or '').strip()}
            if proc.returncode and entry.get('posture') == 'record-layer':
                outcome['detail'] = 'record-layer runtime refused; CARR tool inventory or confinement unavailable'
        except nous_budget.BudgetStop as exc:
            outcome = {'status': 'failed', 'code': 'nous_spend_stop', 'detail': str(exc)}
        except subprocess.TimeoutExpired as exc:
            outcome = {'status': 'timed_out', 'detail': 'Hermes job deadline exceeded',
                       'termination_confirmed': getattr(exc, 'termination_confirmed', True)}
        except FileNotFoundError:
            outcome = {'status': 'failed', 'detail': 'Hermes CLI or curl unavailable'}
        except (OSError, ValueError, TypeError, AttributeError):
            outcome = {'status': 'failed', 'detail': 'Hermes invocation or model catalog failed'}
        try:
            raw_usage = json.loads(usage_path.read_text())
            usage = {key: value for key in USAGE_COUNTERS
                     if type(value := raw_usage.get(key)) in (int, float)
                     and math.isfinite(value) and value >= 0}
        except (OSError, ValueError, AttributeError):
            raw_usage, usage = {}, {}
        accounting['usage'] = raw_usage
        if raw_usage.get('spend_limit_reached'):
            outcome.update(status='failed', code='nous_spend_stop', detail='Nous run spending limit reached')
        if (usage_path.with_suffix('.credits-exhausted').exists() or raw_usage.get('nous_credits_exhausted')
                or billing_error(raw_usage)):
            accounting['exhausted'] = True
            outcome.update(status='failed', detail='nous credits exhausted')
        if outcome['status'] == 'completed':
            if raw_usage.get('model') != base.get('model') or raw_usage.get('provider') != entry['provider']:
                outcome.update(status='failed', detail='Hermes usage identity mismatch')
            elif (raw_usage.get('completed') is not True or raw_usage.get('failed')
                  or raw_usage.get('partial') or raw_usage.get('interrupted') or not outcome['result']):
                outcome.update(status='failed', detail='Hermes turn incomplete')
        return {**base, **outcome, 'usage': usage}
