#!/usr/bin/env python3
"""Compare context bytes with a baseline revision using synthetic bootstrap/rules."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO), str(REPO / 'hooks')]


def load_hook(source):
    spec = importlib.util.spec_from_file_location('measured_hook', REPO / 'hooks/rule-pack-preuse-reselection.py')
    module = importlib.util.module_from_spec(spec)
    exec(compile(source, str(spec.origin), 'exec'), module.__dict__)
    return module


def rule_bytes(source, state):
    hook = load_hook(source)
    catalog = {'ok': True, 'schema': 'rule-scope-catalog/v1', 'corpus_digest': 'a' * 64,
               'identity': {'agent_principal_id': 'joe-local', 'runtime_principal': 'joe-local',
                            'sponsoring_human_id': 'joe'},
               'rules': [{'id': '24e10ee8-0000-0000-0000-000000000000',
                          'statement': 'Synthetic covered rule.', 'scope': {'tools': ['Bash']},
                          'personal_to': None}]}
    payload = {'session_id': 'measurement', 'hook_event_name': 'PreToolUse',
               'tool_name': 'Bash', 'tool_input': {'command': 'git diff'},
               'tool_use_id': 'measure', 'prompt': 'Review this change'}
    runner = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, json.dumps(catalog), '')
    with patch.dict(os.environ, {'CARR_RULE_ROUTE_DEDUPE_DIR': str(state)}), \
            patch.object(hook.rule_routes, '_context_epoch', return_value='measured-context'):
        outputs = [hook.process(payload, runner=runner, cache=False) for _ in range(2)]
    first = json.loads(outputs[0]['hookSpecificOutput']['additionalContext'])
    assert [c['id'] for c in first['cards']] == ['A01'], first
    return [len(o['hookSpecificOutput']['additionalContext'].encode()) if o else 0 for o in outputs]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', help='Git revision before the change')
    args = parser.parse_args()
    baseline = subprocess.run(['git', 'show', f'{args.baseline}:hooks/rule-pack-preuse-reselection.py'],
                              cwd=REPO, check=True, capture_output=True).stdout
    head = (REPO / 'hooks/rule-pack-preuse-reselection.py').read_bytes()
    data = ('🩺' * 12000).encode()
    runs = []
    for _ in range(5):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            result = subprocess.run([sys.executable, REPO / 'hooks/orchestrator-rotation.py',
                                     '--bootstrap-capture', str(root / 'bootstrap')],
                                    input=data, capture_output=True, check=True)
            receipt = json.loads(result.stdout)
            assert Path(receipt['path']).read_bytes() == data
            before = rule_bytes(baseline, root / 'before')
            after = rule_bytes(head, root / 'after')
            runs.append({'bootstrap_before': len(data), 'bootstrap_after': len(result.stdout),
                         'rule_first_before': before[0], 'rule_first_after': after[0],
                         'rule_repeat_before': before[1], 'rule_repeat_after': after[1],
                         'total_before': len(data) + sum(before),
                         'total_after': len(result.stdout) + sum(after)})
    print(json.dumps({'unit': 'UTF-8 bytes entering context', 'runs': runs,
                      'errors': 0, 'scope': 'Synthetic 48 KB bootstrap and two identical PreToolUse events; not a token or latency forecast.'}, indent=2))


if __name__ == '__main__':
    main()
