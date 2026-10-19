"""Claude API coding desk, with retained checkouts and a monthly spend guard.

Install its optional runtime with Python 3.10 or newer:
    python3 -m pip install -r tools/room-bridge/requirements.txt
Register and send using dispatch.py:
    register api-build --kind claude-api --effort medium --timeout 3600
    register api-hard --kind claude-api --model claude-opus-5-5 --effort high
    send api-build "Build the requested change; test, commit, push, open a PR" --checkout new:api-job
    claude-api-spend
    claude-api-spend --clear-stop

The last command is human recovery after fixing billing or reconciling an
interrupted job. It preserves spent dollars and pending reservations. Never
replay a timed-out write job without checking its checkout and remote receipts.
Policy lives in claude_api_config.py, including the model and USD 200 monthly
budget. Months use America/Chicago. A run reserves USD 10 atomically before
launch and passes that amount as the SDK maximum budget. The SDK may overshoot
by one request. Provider credit exhaustion remains the final account limit.
Reported terminal cost replaces the reservation. Unknown cost retains it and
stops spending. Outstanding reservations carry across months. A run crossing
a month boundary counts its full reported cost in both months conservatively.
The ledger and its independent two-slot pool live under Library/Application
Support/CARR, outside provider config. Results use dispatch's existing NDJSON.
Each reserved and settled run records its requested and reported model. Older
balances remain intact without inventing model attribution for historical spend.

Security read of published claude-agent-sdk 0.2.168:
https://github.com/anthropics/claude-agent-sdk-python/tree/v0.2.168
The macOS arm64 wheel SHA-256 is
2ef1e7fc442c5abdc1e698027bf1b5faa8ea2130134a14364e2ae3c53bf31fdd.
It contains CLI 2.1.301 and no .pth payload. SubprocessCLITransport starts that
CLI with anyio.open_process, inherits environment, and otherwise searches PATH
when the bundle is missing. Our worker pins the SDK version and bundle path,
refuses a missing bundle, disables filesystem settings and ambient MCP, uses
an empty temporary Claude config, strips alternate auth and proxies, and
requires auth status to report the environment API key on firstParty. It never
uses the Max token loader. The keychain secret travels only in process memory
and the child environment. Worker stderr is discarded and receipts redact the
key. The parent kills the whole process group on completion and timeout.

Runtime reference: https://code.claude.com/docs/en/agent-sdk/python
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import uuid

import claude_api_config as config
from desks import DeskError
import write_ownership
from claude_api_worker import billing_error
from ops.git_env import scrubbed_env


def month_now():
    return datetime.now(ZoneInfo('America/Chicago')).strftime('%Y-%m')


def usd(value):
    try:
        cost = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return cost if cost.is_finite() and cost >= 0 else None


def pending(data):
    return {run: amount for month in data['months'].values()
            for run, amount in month['reservations'].items()}


class Ledger:
    def __init__(self, path=None):
        self.path = Path(path or config.LEDGER_PATH)

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with Path(str(self.path) + '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                data = json.loads(self.path.read_text()) if self.path.exists() else {'months': {}}
                for account in data['months'].values():
                    if usd(account['spent_usd']) is None or any(usd(x) is None for x in account['reservations'].values()):
                        raise ValueError('invalid ledger amount')
            except (ValueError, KeyError, TypeError, AttributeError):
                raise DeskError('claude_api_ledger_invalid', 'Claude API ledger unreadable; spending refused') from None
            yield data
            tmp = self.path.with_suffix('.tmp')
            with tmp.open('w') as output:
                json.dump(data, output)
                output.flush()
                os.fsync(output.fileno())
            tmp.replace(self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)

    def read(self):
        with self.locked() as data:
            month = month_now()
            account = data['months'].get(month, {'spent_usd': '0', 'reservations': {}})
            return {'month': month, 'spent_usd': account['spent_usd'],
                    'reserved_usd': str(sum((Decimal(x) for x in pending(data).values()), Decimal(0))),
                    'budget_usd': str(config.MONTHLY_BUDGET_USD), 'stop': data.get('stop'),
                    'pending_runs': pending(data), 'runs': account.get('runs', {})}

    def reserve(self, model):
        with self.locked() as data:
            if data.get('stop'):
                raise DeskError('claude_api_stopped', 'Claude API spending stopped until a human clears it')
            month = month_now()
            account = data['months'].setdefault(month, {'spent_usd': '0', 'reservations': {}})
            committed = Decimal(account['spent_usd']) + sum(Decimal(x) for x in pending(data).values())
            if committed + config.RUN_ESTIMATE_USD > config.MONTHLY_BUDGET_USD:
                raise DeskError('monthly_budget_exceeded', 'Claude API monthly budget exceeded')
            run_id = str(uuid.uuid4())
            account['reservations'][run_id] = str(config.RUN_ESTIMATE_USD)
            account.setdefault('runs', {})[run_id] = {
                'model': model, 'actual_model': None, 'cost_usd': None, 'status': 'reserved'}
            return month, run_id

    def settle(self, reservation, cost, *, actual_model=None, status='settled', billing_error=False):
        with self.locked() as data:
            if billing_error:
                data['stop'] = 'billing_error'
            account = data['months'][reservation[0]]
            cost = usd(cost)
            run = account['runs'][reservation[1]]
            run.update(actual_model=actual_model, cost_usd=str(cost) if cost is not None else None,
                       status=status if cost is not None else 'accounting_unknown')
            if cost is None:
                data.setdefault('stop', 'accounting_unknown')
                return
            for month in {reservation[0], month_now()}:
                charged = data['months'].setdefault(month, {'spent_usd': '0', 'reservations': {}})
                charged['spent_usd'] = str(Decimal(charged['spent_usd']) + cost)
                charged.setdefault('runs', {})[reservation[1]] = dict(run)
            del account['reservations'][reservation[1]]

    def clear_stop(self):
        with self.locked() as data:
            data.pop('stop', None)



def api_env(env, key, config_dir):
    child = {k: v for k, v in scrubbed_env(env).items()
             if not k.startswith(('ANTHROPIC_', 'CLAUDE', 'AWS_', 'GOOGLE_', 'OTEL_', 'API_'))}
    for proxy in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        child.pop(proxy, None)
    child.update(ANTHROPIC_API_KEY=key, CLAUDE_CONFIG_DIR=config_dir,
                 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1', CLAUDE_CODE_MAX_RETRIES='0')
    return child


@contextmanager
def api_slot(directory, deadline):
    directory.mkdir(parents=True, exist_ok=True)
    while time.monotonic() < deadline:
        for index in range(config.MAX_CONCURRENT):
            handle = (directory / f'slot-{index}.lock').open('a')
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                continue
            try:
                yield
            finally:
                handle.close()
            return
        time.sleep(0.05)
    raise DeskError('api_slot_timeout', 'Claude API slots busy until deadline')


def run_task(entry, task, *, env=None, ledger=None, keychain_run=None, on_executor=None):
    ledger = ledger or Ledger()
    deadline = time.monotonic() + entry['timeout_s']
    base = {'model': entry['model'], 'requested_model': entry['model'],
            'actual_model': None, 'effort': entry['effort'], 'usage': {},
            'cost_usd': None, 'result': ''}
    try:
        with api_slot(ledger.path.parent / 'claude-api-slots', deadline):
            outcome = _run_task(entry, task, env=env, ledger=ledger, keychain_run=keychain_run,
                                on_executor=on_executor, deadline=deadline)
    except DeskError as exc:
        outcome = {'status': 'refused', 'detail': exc.code}
    return {**base, **outcome}


def _run_task(entry, task, *, env, ledger, keychain_run, on_executor, deadline):
    keychain_run = keychain_run or subprocess.run
    try:
        key_result = keychain_run(['/usr/bin/security', 'find-generic-password',
                                  '-a', config.KEYCHAIN_ACCOUNT, '-s', config.KEYCHAIN_SERVICE, '-w'],
                                 capture_output=True, text=True, timeout=max(0.001, min(5, deadline - time.monotonic())))
        key = key_result.stdout.strip() if key_result.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        key = ''
    if not key:
        return {'status': 'failed', 'detail': 'Claude API key not set up',
                'setup_command': config.KEY_SETUP}
    reservation = ledger.reserve(entry['model'])
    request = {'task': task, 'cwd': entry['checkout_path'], 'model': entry['model'],
               'effort': entry['effort'], 'estimate_usd': float(config.RUN_ESTIMATE_USD)}
    with tempfile.TemporaryDirectory(prefix='room-claude-api-') as isolated:
        child = api_env(env, key, isolated)
        try:
            proc = subprocess.Popen([sys.executable, str(Path(__file__).with_name('claude_api_worker.py'))],
                                    env=child, cwd=request['cwd'], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    start_new_session=True,
                                    pass_fds=(() if write_ownership.CHECKOUT_FD is None
                                              else (write_ownership.CHECKOUT_FD,)))
        except OSError:
            ledger.settle(reservation, 0, status='failed')
            return {'status': 'failed', 'detail': 'sdk_worker_unavailable', 'cost_usd': 0}

        identity = {**write_ownership.process_owner(), 'pid': proc.pid, 'pgid': proc.pid,
                    'start_time': write_ownership.process_start(proc.pid), 'kind': 'process_group'}
        try:
            if on_executor:
                on_executor(identity)
            stdout, stderr = proc.communicate(json.dumps(request), timeout=max(0.001, deadline - time.monotonic()))
            try:
                result = json.loads(stdout.replace(key, '[redacted]'))
                if not isinstance(result, dict) or result.get('status') not in ('completed', 'failed'):
                    raise ValueError('invalid worker receipt')
            except ValueError:
                result = {'status': 'failed', 'detail': 'sdk_worker_receipt_invalid',
                          'billing_error': billing_error(stderr)}
            if proc.returncode:
                result['status'] = 'failed'
                result['detail'] = 'sdk_worker_failed'
                result['billing_error'] = result.get('billing_error') or billing_error(stderr)
        except subprocess.TimeoutExpired:
            result = {'status': 'timed_out', 'detail': 'claude_api_deadline; do not replay'}
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=2)
            proc.stdin.close()
            proc.stdout.close()
            proc.stderr.close()
        for _ in range(20):
            if write_ownership.process_terminated(identity, group=True):
                break
            time.sleep(0.01)
        result['termination_confirmed'] = write_ownership.process_terminated(identity, group=True)
    if usd(result.get('cost_usd')) is None:
        result['cost_usd'] = None
        if result.get('status') == 'completed':
            result.update(status='failed', detail='sdk_cost_missing')
    result['spend_reservation'] = {'month': reservation[0], 'run_id': reservation[1]}
    ledger.settle(reservation, result.get('cost_usd'), actual_model=result.get('actual_model'),
                  status=result['status'], billing_error=result.get('billing_error', False))
    return result
