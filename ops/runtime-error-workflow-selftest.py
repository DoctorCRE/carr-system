#!/usr/bin/env python3
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('runtime_error_workflow_test', ROOT / 'tools/control-plane.py')
assert spec and spec.loader
control_plane = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control_plane)
manifest = json.loads((ROOT / 'ops/config/control-plane-workflows.v1.json').read_text())
workflow = next(w for w in manifest['workflows'] if w['key'] == 'runtime-error-watch')
payload = {'scheduled_for': '2026-10-06T05:00:00Z'}
collector = control_plane.RuntimeWorkflowFactCollector(workflow, payload)
assert collector._pre_value('runtime_errors.consumer_registered') is True
for mode in ('shadow', 'live'):
    evidence = {'entrypoint': 'ops/runtime-error-watch.mjs', 'mode': mode,
                'args': ['--shadow'] if mode == 'shadow' else [], 'exit_code': 0,
                'stdout_tail': f'runtime-error-watch: mode={mode} result=WARN ' + ('writes=0' if mode == 'shadow' else 'receipt=written')}
    facts = control_plane.RuntimeWorkflowFactCollector(workflow, payload, mode=mode, execution=evidence)
    assert facts._command_evidence_valid(evidence)
    assert not facts._command_evidence_valid({**evidence, 'stdout_tail': 'OK naked signal'})
    assert not facts._command_evidence_valid({**evidence, 'exit_code': 1})
print('runtime-error-workflow-selftest: ledger routing and receipt validation passed')
