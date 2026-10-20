"""Choose the first available record-layer desk and retain admission evidence."""
from decimal import Decimal
import json
import os
from pathlib import Path

import claude_api_wire
import flashlib
import nous_budget
import record_layer
import seat_gate
from desks import DeskError

ORDER = ('hermes-nous', 'grok-cli', 'codex-session', 'claude-desktop')
LIMITED = {'quota_exhausted', 'rate_limited', 'capacity', 'over_budget', 'provider_capped'}


def provider(kind):
    if kind.startswith('claude-'):
        return 'claude'
    return 'codex' if kind.startswith('codex-') else kind


def limit_states(results):
    states = {}
    path = Path(results)
    if not path.exists():
        return states
    for line in path.read_text().splitlines():
        row = json.loads(line)
        kind = row.get('kind', '')
        if not kind:
            continue
        if row.get('status') in LIMITED:
            states[provider(kind)] = row['status']
        elif row.get('status') == 'completed':
            states.pop(provider(kind), None)
    return states


def unavailable(entry, results, limits):
    blocked = seat_gate.check(entry)
    if blocked:
        return ': '.join(blocked)
    kind = entry['kind']
    if provider(kind) in limits:
        return limits[provider(kind)]
    if kind in ('hermes-nous', 'claude-desktop'):
        ledger = nous_budget.Ledger(results) if kind == 'hermes-nous' else claude_api_wire.Ledger()
        state = ledger.read()
        if state['stop']:
            return str(state['stop'])
        estimate = ledger.estimate if kind == 'hermes-nous' else Decimal(0)
        total = Decimal(state['spent_usd']) + Decimal(state['reserved_usd'])
        budget = Decimal(state['budget_usd'])
        if total >= budget or total + estimate > budget:
            return 'provider budget spent or reserved'
    try:
        record_layer.enforce(entry)
    except DeskError as exc:
        return f'{exc.code}: {exc}'
    return None


def select(registry, results):
    order = list(ORDER)
    if os.environ.get('CARR_BUSINESS_FLASH_ENABLED', '0') == '1' and not flashlib.is_switched_off():
        order.append('flash-local')
    route = {'chosen': None, 'skips': []}
    entries = registry.entries()
    try:
        limits = limit_states(results)
    except (OSError, ValueError, TypeError, AttributeError):
        limits = {provider(kind): 'provider limit ledger unreadable' for kind in order}
    for kind in order:
        names = sorted(name for name, entry in entries.items()
                       if entry.get('kind') == kind and entry.get('posture') == 'record-layer')
        if not names:
            route['skips'].append({'desk': kind, 'reason': 'no registered record-layer desk'})
        for name in names:
            try:
                entry = registry.resolve(name)
                reason = unavailable(entry, results, limits)
            except (DeskError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                reason = f'admission unavailable: {type(exc).__name__}'
            if reason:
                route['skips'].append({'desk': name, 'reason': reason})
            else:
                route['chosen'] = name
                return route
    return route
