"""Replay a finite Codex network-policy matrix without a model or live service."""
import hashlib
import importlib.util
import inspect
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROOM = ROOT / 'tools/room-bridge'
BOOT_TASK = 'synthetic standing-context boot'
BOOT_ACCEPTED = 'SYNTHETIC_BOOT_ACCEPTED'
BOOT_REFUSED = 'SYNTHETIC_BOOT_REFUSED_NO_READONLY_NETWORK'


def wire_module(arm, root, baseline_ref):
    path = ROOM / 'codex_wire.py'
    source = (subprocess.check_output(['git', 'show', baseline_ref + ':tools/room-bridge/codex_wire.py'],
             cwd=ROOT, text=True) if arm == 'baseline' else path.read_text())
    module_path = root / f'{arm}_codex_wire.py'
    module_path.write_text(source)
    spec = importlib.util.spec_from_file_location(f'{arm}_codex_wire', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def observe_wire(module, value):
    """Use the real wire entry point; fake only App Server replies and boot result."""
    sent = []

    class FakeAppServer:
        def __init__(self):
            self.thread_id = None
            self.turn_id = None
            self.deadline = None
            self.turn_requested = False
            self.responses = []
            self.thread_start = None
            self.sock = type('Socket', (), {'close': lambda self: None})()

        def upgrade(self):
            pass

        def send_json(self, message):
            sent.append(message)
            ident = message.get('id')
            if ident == 'initialize':
                self.responses.append({'id': ident, 'result': {}})
            elif ident == 'thread-open':
                if message['method'] == 'thread/start':
                    self.thread_start = message['params']
                self.responses.append({'id': ident, 'result': {'thread': {'id': 'synthetic-thread'}}})
            elif ident == 'turn-start':
                params = message['params']
                boot_requested = any(BOOT_TASK in row.get('text', '') for row in params['input'])
                network_permitted = (self.thread_start is not None
                    and self.thread_start.get('sandbox') == 'read-only'
                    and self.thread_start.get('ephemeral') is True
                    and params.get('sandboxPolicy') == {'type': 'readOnly', 'networkAccess': True})
                allowed = boot_requested and network_permitted
                answer = BOOT_ACCEPTED if allowed else BOOT_REFUSED
                status = 'completed' if allowed else 'failed'
                self.responses.extend([
                    {'id': ident, 'result': {'turn': {'id': 'synthetic-turn'}}},
                    {'method': 'item/completed', 'params': {'threadId': 'synthetic-thread',
                        'turnId': 'synthetic-turn', 'item': {'type': 'agentMessage', 'text': answer}}},
                    {'method': 'turn/completed', 'params': {'threadId': 'synthetic-thread',
                        'turn': {'id': 'synthetic-turn', 'status': status}}},
                ])

        def receive_json(self):
            return self.responses.pop(0)

    network_access = value['network_access']
    thread_id = value['thread_id']
    if network_access and 'network_access' not in inspect.signature(module.run_turn).parameters:
        return {'outcome': 'network-option-unsupported', 'turn_status': None, 'requests': []}
    wire = FakeAppServer()
    module.Wire = lambda *args, **kwargs: wire
    kwargs = {'thread_id': thread_id, 'timeout': 1, 'sandbox': value['sandbox'], 'cwd': value['cwd']}
    if 'network_access' in inspect.signature(module.run_turn).parameters:
        kwargs['network_access'] = network_access
    try:
        result = module.run_turn('/synthetic/socket', BOOT_TASK, **kwargs)
    except Exception as exc:
        if network_access and thread_id is not None:
            return {'outcome': getattr(exc, 'code', type(exc).__name__),
                    'turn_status': None, 'requests': sent}
        raise
    requests = []
    for row in sent:
        if row.get('method') not in ('thread/start', 'thread/resume', 'turn/start'):
            continue
        params = dict(row.get('params') or {})
        if row['method'] == 'turn/start':
            params['input'] = [{'type': 'text', 'text': '<boot task>'}]
        requests.append({'method': row['method'], 'params': params})
    outcomes = {BOOT_ACCEPTED: 'synthetic-boot-accepted',
                BOOT_REFUSED: 'boot-refused-no-readonly-network'}
    return {'outcome': outcomes.get(result['result'], 'unexpected-boot-result'),
            'turn_status': result['status'], 'requests': requests}


def observe(expectations, arm):
    if str(ROOM) not in sys.path:
        sys.path.insert(0, str(ROOM))
    with tempfile.TemporaryDirectory() as tmp:
        wire = wire_module(arm, Path(tmp), expectations['baseline_ref'])
        rows = []
        for cid, case in expectations['cases'].items():
            value = case['input']
            digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
            if case['input_sha256'] != digest:
                raise ValueError(f'{cid} input hash differs from its bound bytes')
            rows.append({'case_id': cid, 'split': case['split'],
                'input_sha256': digest, 'wire': observe_wire(wire, value)})
        return rows


def score(expectations, baseline, candidate):
    cases = expectations['cases']
    train_hashes = {c['input_sha256'] for c in cases.values() if c['split'] == 'train'}
    test_hashes = {c['input_sha256'] for c in cases.values() if c['split'] == 'test'}
    if train_hashes & test_hashes:
        raise ValueError('train and test contain an identical protocol input')
    for arm, rows in (('baseline', baseline), ('candidate', candidate)):
        if rows != observe(expectations, arm):
            raise ValueError('cohort differs from independently replayed wire observations')
    test_cases = [cid for cid, case in cases.items() if case['split'] == 'test']
    def grade(rows):
        indexed = {row['case_id']: row for row in rows}
        return sum(indexed[cid]['wire'] == cases[cid]['expected_wire'] for cid in test_cases) / len(test_cases)
    wb, wc = grade(baseline), grade(candidate)
    oracle = [{**row, 'wire': cases[row['case_id']]['expected_wire']} for row in candidate]
    null = [{**row, 'wire': {'outcome': 'null', 'turn_status': None, 'requests': []}} for row in candidate]
    return {'dimensions': {'fresh-network-boundary': {
        'baseline': {'score': wb, 'ci_low': wb, 'ci_high': wb},
        'candidate': {'score': wc, 'ci_low': wc, 'ci_high': wc},
        'delta': {'value': wc-wb, 'ci_low': wc-wb, 'ci_high': wc-wb}}},
        'controls': {'agreement': 1.0, 'oracle_pass_rate': grade(oracle), 'null_pass_rate': grade(null)}}
