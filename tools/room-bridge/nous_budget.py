"""Durable Nous spend reservations shared by desks using one results ledger."""
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_UP
import fcntl
import json
import os
from pathlib import Path
import re
from write_ownership import process_owner, process_start

CONFIG = Path(__file__).resolve().parents[2] / 'ops/config/nous-spend.v1.json'


class BudgetStop(ValueError):
    pass


def dollars(value):
    if isinstance(value, bool):
        raise ValueError('invalid cost')
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('invalid cost') from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError('invalid cost')
    return amount


def money(amount):
    return str(amount.quantize(Decimal('0.01'), rounding=ROUND_UP))


def portal_price(payload, model):
    for item in payload.get('paidRecommendedModels', []):
        if item.get('modelName') != model:
            continue
        pricing = item.get('pricing') or {}
        if pricing.get('prompt') is not None and pricing.get('completion') is not None:
            prompt, completion = dollars(pricing['prompt']), dollars(pricing['completion'])
        else:
            match = re.fullmatch(r'in \$([\d.]+) / out \$([\d.]+) per 1M', item.get('tokenPrice') or '')
            if not match:
                return None
            prompt, completion = (dollars(value) / 1000000 for value in match.groups())
        return {'model': model, 'input_usd_per_token': str(prompt),
                'output_usd_per_token': str(completion), 'source': 'nous_recommended_models'}
    return None


def run_cost(usage, price):
    total = usage.get('total_including_auxiliary') or {}
    if total.get('estimated_cost_usd') is not None:
        return dollars(total['estimated_cost_usd']), 'hermes_usage_usd'
    auxiliary = usage.get('auxiliary') or {}
    if usage.get('estimated_cost_usd') is not None:
        if auxiliary.get('api_calls') and auxiliary.get('estimated_cost_usd') is None:
            raise ValueError('auxiliary cost unknown')
        return dollars(usage['estimated_cost_usd']) + dollars(auxiliary.get('estimated_cost_usd', 0)), 'hermes_usage_usd'
    if not price or auxiliary.get('api_calls'):
        raise ValueError('Portal token pricing unavailable')
    if any(type(usage.get(key)) is not int or usage[key] < 0 for key in ('input_tokens', 'output_tokens')):
        raise ValueError('token counts unavailable')
    inputs = sum(dollars(usage.get(key, 0) or 0) for key in ('input_tokens', 'cache_read_tokens', 'cache_write_tokens'))
    outputs = sum(dollars(usage.get(key, 0) or 0) for key in ('output_tokens', 'reasoning_tokens'))
    return (inputs * dollars(price['input_usd_per_token'])
            + outputs * dollars(price['output_usd_per_token'])), 'portal_tokens'


class Ledger:
    def __init__(self, results_path):
        results = Path(results_path)
        self.path = results.with_name(results.name + '.nous-spend.json')
        config = json.loads(CONFIG.read_text())
        self.budget = dollars(config['budget_usd'])
        self.estimate = dollars(config['per_run_estimate_usd'])
        if not self.estimate or self.estimate > self.budget:
            raise ValueError('invalid Nous budget configuration')

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                state = json.loads(self.path.read_text()) if self.path.exists() else {'runs': {}, 'stop': None}
                if not isinstance(state['runs'], dict) or 'stop' not in state:
                    raise ValueError('invalid Nous ledger')
                yield state
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def save(self, state):
        temporary = self.path.with_suffix('.tmp')
        with temporary.open('w') as output:
            json.dump(state, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(self.path)
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def totals(self, state):
        spent = sum((dollars(row['cost_usd']) for row in state['runs'].values()
                     if row.get('cost_usd') is not None), Decimal(0))
        reserved = sum((dollars(row['estimate_usd']) for row in state['runs'].values()
                        if row.get('cost_usd') is None), Decimal(0))
        return {'spent_usd': money(spent), 'reserved_usd': money(reserved),
                'budget_usd': money(self.budget), 'stop': state['stop'], 'ledger': str(self.path)}

    def read(self):
        with self.locked() as state:
            return self.totals(state)

    def reserve(self, run_id):
        with self.locked() as state:
            if state['stop']:
                raise BudgetStop(state['stop'])
            owner = process_owner()
            for row in state['runs'].values():
                if row.get('cost_usd') is not None:
                    continue
                previous = row.get('owner', {})
                if (previous.get('host') != owner['host'] or not previous.get('start_time')
                        or process_start(previous.get('pid')) != previous['start_time']):
                    state['stop'] = 'Nous interrupted run cost unknown; reconcile ledger'
                    self.save(state)
                    raise BudgetStop(state['stop'])
            totals = self.totals(state)
            if dollars(totals['spent_usd']) + dollars(totals['reserved_usd']) + self.estimate > self.budget:
                raise BudgetStop('Nous credit budget spent')
            if run_id in state['runs']:
                raise BudgetStop('Nous run already reserved; do not retry')
            state['runs'][run_id] = {'estimate_usd': money(self.estimate), 'cost_usd': None, 'owner': owner}
            self.save(state)

    def launch(self, run_id):
        with self.locked() as state:
            if state['stop']:
                raise BudgetStop(state['stop'])
            totals = self.totals(state)
            if dollars(totals['spent_usd']) + dollars(totals['reserved_usd']) > self.budget:
                raise BudgetStop('Nous credit budget spent')
            row = state['runs'][run_id]
            if row.get('launched') or row.get('cost_usd') is not None:
                raise BudgetStop('Nous run already launched; do not retry')
            row['launched'] = True
            self.save(state)

    def settle(self, run_id, usage, launched, exhausted=False, price=None):
        with self.locked() as state:
            row = state['runs'][run_id]
            if exhausted:
                state['stop'] = 'nous credits exhausted'
            if row.get('cost_usd') is None:
                try:
                    cost, source = run_cost(usage, price) if launched else (Decimal(0), 'not_launched')
                    row.update(cost_usd=money(cost), calculated_cost_usd=str(cost), cost_source=source)
                    if price:
                        row['price'] = price
                except (ValueError, InvalidOperation, KeyError, TypeError, AttributeError):
                    row['cost_source'] = 'unknown'
                    state['stop'] = state['stop'] or 'Nous run cost unavailable; reconcile ledger'
            self.save(state)
            return self.totals(state)
