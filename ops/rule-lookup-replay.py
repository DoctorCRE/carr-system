#!/usr/bin/env python3
"""Compare injected UTF-8 bytes on 200 synthetic mixed-tool hook calls.

Run with --before-ref naming the unchanged base revision. Catalog transport,
logs and route state are local fixtures; this never calls the rule store.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
HOOK = 'hooks/rule-pack-preuse-reselection.py'


def load(name, source):
    spec = importlib.util.spec_from_file_location(name, REPO / HOOK)
    module = importlib.util.module_from_spec(spec)
    exec(compile(source, str(REPO / HOOK), 'exec'), module.__dict__)
    return module


def samples():
    defects = [dict(id=f'{i:08x}-0000-0000-0000-000000000000',
                    statement='Synthetic missing scope.', scope={}, personal_to=None)
               for i in range(9)]
    for i in range(200):
        tool = ('Bash', 'Edit', 'Write', 'mcp__fixture__act')[i % 4]
        kind = 'empty' if i % 100 < 77 else 'rule' if i % 100 < 87 else 'card' if i % 100 < 95 else 'overflow'
        rows = list(defects)
        if kind != 'empty':
            rows.append(dict(id='24e10ee8-0000-0000-0000-000000000000' if kind == 'card' else '11111111',
                             statement='Read the synthetic diff.' if kind != 'overflow' else 'Long binding rule. ' * 2000,
                             scope={'tools': [tool]}, personal_to=None))
        payload = dict(hook_event_name='PreToolUse', session_id='synthetic-session', turn_id='turn',
                       tool_use_id=f'call-{i}', tool_name=tool, prompt='Inspect the synthetic fixture.',
                       tool_input={'command': 'git diff', 'file_path': '/tmp/synthetic-fixture'})
        catalog = dict(ok=True, schema='rule-scope-catalog/v1', corpus_digest='a' * 64,
                       identity=dict(agent_principal_id='joe-local', runtime_principal='joe-local',
                                     sponsoring_human_id='joe'), rules=rows)
        yield payload, catalog


def replay(before_ref):
    before_source = subprocess.check_output(['git', 'show', f'{before_ref}:{HOOK}'], cwd=REPO, text=True)
    modules = [load('before', before_source), load('after', (REPO / HOOK).read_text())]
    totals = [dict(calls=0, injected_bytes=0, empty_output=0, denies=0) for _ in modules]
    with tempfile.TemporaryDirectory() as raw:
        for side, module in enumerate(modules):
            module.GAP_LOG = Path(raw) / f'gaps-{side}.jsonl'
            module.RECEIPT_LOG = Path(raw) / f'receipts-{side}.jsonl'
        for payload, catalog in samples():
            content = []
            for module, total in zip(modules, totals):
                runner = lambda command, **kwargs: subprocess.CompletedProcess(command, 0, json.dumps(catalog), '')
                output = module.process(payload, runner=runner, dedupe=False, cache=False)
                with patch.object(module, 'process', return_value=output):
                    verdict = module.decide(payload)
                assert verdict.code == 0
                total['calls'] += 1
                total['empty_output'] += not verdict.stdout
                specific = (output or {}).get('hookSpecificOutput', {})
                total['denies'] += specific.get('permissionDecision') == 'deny'
                text = specific.get('additionalContext', '') if verdict.stdout else ''
                total['injected_bytes'] += len(text.encode('utf-8'))
                row = json.loads(text) if text else {}
                content.append({key: row.get(key, []) for key in ('rules', 'cards', 'overflow')})
            assert content[0] == content[1], payload
        assert all(total['calls'] == 200 and total['denies'] == 0 for total in totals)
        assert 'missing_scope' in modules[1].GAP_LOG.read_text()
    return dict(before_ref=before_ref, sample='200 calls; four tools; nine missing_scope defects per call; cache/dedupe disabled equally',
                before=totals[0], after=totals[1], content_mismatches=0, errors=0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before-ref', required=True)
    print(json.dumps(replay(parser.parse_args().before_ref), indent=2))
