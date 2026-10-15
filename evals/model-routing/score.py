"""Grade the finite registry/catalog matrix, without running a model."""
import importlib.util
import inspect
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROOM = ROOT / 'tools/room-bridge'


def observe_wire(module, network_access, thread_id, sandbox):
    """Exercise the selected revision's real wire code against deterministic RPC replies."""
    sent = []
    class FakeWire:
        def __init__(self):
            self.thread_id = None
            self.turn_id = None
            self.deadline = None
            self.turn_requested = False
            self.responses = []
            self.sock = type('Socket', (), {'close': lambda self: None})()
        def upgrade(self): pass
        def send_json(self, value):
            sent.append(value)
            ident = value.get('id')
            if ident == 'initialize': self.responses.append({'id': ident, 'result': {}})
            elif ident == 'thread-open': self.responses.append({'id': ident, 'result': {'thread': {'id': 'synthetic-thread'}}})
            elif ident == 'turn-start':
                self.responses.append({'id': ident, 'result': {'turn': {'id': 'synthetic-turn'}}})
                self.responses.extend([
                    {'method': 'item/completed', 'params': {'threadId': 'synthetic-thread', 'turnId': 'synthetic-turn', 'item': {'type': 'agentMessage', 'text': 'synthetic'}}},
                    {'method': 'turn/completed', 'params': {'threadId': 'synthetic-thread', 'turn': {'id': 'synthetic-turn', 'status': 'completed'}}},
                ])
        def receive_json(self): return self.responses.pop(0)
    wire = FakeWire()
    if network_access and 'network_access' not in inspect.signature(module.run_turn).parameters:
        return {'outcome': 'network-option-unsupported', 'requests': []}
    if network_access and thread_id:
        try:
            module.run_turn('/synthetic/socket', 'synthetic bounded task', thread_id=thread_id,
                            network_access=True, sandbox=sandbox, timeout=1)
        except Exception as exc:
            return {'outcome': getattr(exc, 'code', type(exc).__name__), 'requests': sent}
        return {'outcome': 'unexpected-resume', 'requests': sent}
    module.Wire = lambda *args, **kwargs: wire
    kwargs = {'thread_id': thread_id, 'timeout': 1, 'sandbox': sandbox}
    if 'network_access' in inspect.signature(module.run_turn).parameters:
        kwargs['network_access'] = network_access
    result = module.run_turn('/synthetic/socket', 'synthetic bounded task', **kwargs)
    requests = []
    for row in sent:
        if row.get('method') not in ('thread/start', 'thread/resume', 'turn/start'):
            continue
        params = dict(row.get('params') or {})
        if row['method'] == 'turn/start' and 'input' in params:
            params['input'] = [{'type': 'text', 'text': '<task>'}]
        requests.append({'method': row['method'], 'params': params})
    return {'outcome': result['status'], 'requests': requests}


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


def observe(expectations, arm):
    sys.path.insert(0, str(ROOM))
    import codex_models
    import desks
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        registry_class = desks.Registry
        if arm == 'baseline':
            source = subprocess.check_output(['git', 'show', expectations['baseline_ref'] +
                ':tools/room-bridge/desks.py'], cwd=ROOT, text=True)
            module_path = root / 'baseline_desks.py'
            module_path.write_text(source)
            spec = importlib.util.spec_from_file_location('routing_baseline_desks', module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            registry_class = module.Registry
        rows = []
        wire = wire_module(arm, root, expectations['baseline_ref'])
        for cid, case in expectations['cases'].items():
            value = case['input']
            if 'wire_case' in value:
                observed = observe_wire(wire, value['network_access'], value.get('thread_id'), value['sandbox'])
                rows.append({'case_id': cid, 'split': case['split'],
                    'input_sha256': case['input_sha256'], 'wire': observed})
                continue
            registry_path = root / 'registry.json'
            registry_path.write_text(json.dumps({'desks': {'sol': value['entry']}}))
            (root / 'models_cache.json').write_text(json.dumps(value['catalog']))
            registry = registry_class(registry_path)
            entry = registry.entries()['sol']
            try:
                if arm == 'baseline':
                    model = registry.resolve('sol').get('model')
                else:
                    model = codex_models.resolve_model(entry.get('family'), {'CODEX_HOME': str(root)})
                result = {'model': model}
            except desks.DeskError as exc:
                result = {'error': exc.code}
            rows.append({'case_id': cid, 'split': case['split'],
                'input_sha256': case['input_sha256'], 'result': result,
                'metadata': {k: entry.get(k) for k in case['expected_metadata']}})
        return rows


def score(expectations, baseline, candidate):
    cases = expectations['cases']
    for arm, rows in (('baseline', baseline), ('candidate', candidate)):
        if rows != observe(expectations, arm):
            raise ValueError('cohort differs from independently replayed registry/catalog observations')
    wire_cases = [cid for cid, case in cases.items() if 'wire_case' in case['input'] and case['split'] == 'test']
    def wire_grade(rows):
        indexed = {row['case_id']: row for row in rows}
        return sum(indexed[cid]['wire'] == cases[cid]['expected_wire'] for cid in wire_cases) / len(wire_cases)
    wb, wc = wire_grade(baseline), wire_grade(candidate)
    dimensions = {'fresh-network-boundary': {'baseline': {'score': wb, 'ci_low': wb, 'ci_high': wb},
        'candidate': {'score': wc, 'ci_low': wc, 'ci_high': wc},
        'delta': {'value': wc-wb, 'ci_low': wc-wb, 'ci_high': wc-wb}}}
    oracle = [{**r, 'wire': cases[r['case_id']]['expected_wire']} for r in candidate]
    null = [{**r, 'wire': {'outcome': 'null', 'requests': []}} for r in candidate]
    return {'dimensions': dimensions, 'controls': {'agreement': 1.0,
        'oracle_pass_rate': wire_grade(oracle), 'null_pass_rate': wire_grade(null)}}
