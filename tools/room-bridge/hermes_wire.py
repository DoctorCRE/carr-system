"""One-shot Nous Portal jobs. Provider diagnostics never enter the result ledger."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
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


class CreditsExhausted(ValueError):
    pass


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
        ledger = nous_budget.Ledger(results_path)
        ledger.reserve(run_id)
        accounting['before_launch'] = lambda: ledger.launch(run_id)
    except nous_budget.BudgetStop as exc:
        return {'status': 'failed', 'code': 'nous_spend_stop', 'detail': str(exc)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {'status': 'failed', 'code': 'nous_spend_stop', 'detail': 'Nous budget ledger unavailable'}
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
        if CREDIT_ERROR.search((proc.stdout or '') + '\n' + (proc.stderr or '')):
            raise CreditsExhausted()
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
            accounting['price'] = nous_budget.portal_price(payload, model)
            accounting['before_launch']()
            accounting['launched'] = True
            proc = invoke(['hermes', '-z', task, '-m', model, '--provider', entry['provider'],
                    '--reasoning', entry['effort'], '--in', entry['cwd'], '--yolo',
                    '--ignore-user-config', '--usage-file', str(usage_path)])
            outcome = {'status': 'completed' if proc.returncode == 0 else 'failed',
                       'exit_code': proc.returncode, 'result': (proc.stdout or '').strip()}
        except nous_budget.BudgetStop as exc:
            outcome = {'status': 'failed', 'code': 'nous_spend_stop', 'detail': str(exc)}
        except CreditsExhausted:
            accounting['exhausted'] = True
            outcome = {'status': 'failed', 'detail': 'nous credits exhausted'}
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
        if CREDIT_ERROR.search(str(raw_usage.get('failure', '')) + str(raw_usage.get('error', ''))):
            accounting['exhausted'] = True
            outcome.update(status='failed', detail='nous credits exhausted')
        if outcome['status'] == 'completed':
            if raw_usage.get('model') != base.get('model') or raw_usage.get('provider') != entry['provider']:
                outcome.update(status='failed', detail='Hermes usage identity mismatch')
            elif (raw_usage.get('completed') is not True or raw_usage.get('failed')
                  or raw_usage.get('partial') or raw_usage.get('interrupted') or not outcome['result']):
                outcome.update(status='failed', detail='Hermes turn incomplete')
        return {**base, **outcome, 'usage': usage}
