#!/usr/bin/env python3
"""Offline filing policy evaluation with authored extraction responses."""
from __future__ import annotations

import argparse
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
CASES = Path(__file__).with_name('cases.jsonl')


def run_case(suite, case):
    p, cov = suite.post_call, suite._REAL_COVERAGE
    test = suite.PostCallTests()
    test.setUp()
    try:
        session = test.session
        transcript = deepcopy(case['transcript'])
        p.store_context(session, test.context)
        p.write_private(session / 'meta.json', {'started': case['call_date'] + 'T18:00:00Z'})
        p.write_private(session / 'transcript.json', transcript)
        task = {'title': 'Call Harbor vendor', 'deal_id': 'deal-a', 'participant_ids': [],
                'evidence': 'I will call Harbor vendor', 'due_on': case['due_on'], 'fact_ids': ['task']}
        if 'timing' in p.distiller_json_schema()['properties']['joe_tasks']['items']['properties']:
            task['timing'] = deepcopy(case['timing'])
        raw = test.output(joe_tasks=[task], deal_updates=[], draft_proposals=[])
        ledger = {'topics': [{'topic_id': 'harbor', 'identity': {'deal_id': 'deal-a', 'participant_ids': []}}],
                  'facts': [{'fact_id': 'task', 'topic_id': 'harbor', 'kind': 'commitment',
                             'statement': ' '.join(row['text'] for row in transcript['segments']),
                             'asserted_value': None,
                             'source_spans': [cov.source_span(transcript, i) for i in range(len(transcript['segments']))],
                             'disposition': None}], 'non_fact_spans': []}

        def distill(request):
            if request.get('stage') == 'coverage':
                return deepcopy(ledger)
            if request.get('stage') == 'coverage_history':
                return {'dispositions': [{'fact_id': 'task', 'disposition': None}]}
            return deepcopy(raw)

        fixture = ROOT / 'tools/dictation-rig/tests/fixtures/rehearsal_jev.py'
        with patch.object(p, 'coverage', cov), patch.object(p, 'LOG_PATH', session / 'post-call.log'), patch.dict(os.environ, {
                'CALL_JEV_DAILY_DOLLARS': '100', 'CARR_POST_CALL_JEV_FIXTURE': str(fixture)}):
            status = p.process_session(session, distiller=distill)
            assert status['state'] == 'ready_review', status
            report = p.read_json(session / p.REPORT_FILE)
            report['joe_tasks'][0].update(candidate_id='task', candidate_status='confirmed')
            p.write_private(session / p.REPORT_FILE, report)
            cov.require_coverage(session, report, p.read_json)
            result = {'eligible': p.review_complete(report), 'due_on': report['joe_tasks'][0]['due_on'],
                      'reminder': bool(report.get('optional_reminders'))}
            assert result['eligible'] == (p.retention_ready(session, 'b' * 64) is not None)
            return result
    finally:
        test.tearDown()
        test.doCleanups()


def load_suite(revision=None):
    spec = importlib.util.spec_from_file_location('deadline_suite', ROOT / 'tools/dictation-rig/tests/test_call_mode.py')
    assert spec and spec.loader
    suite = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(suite)
    if revision:
        path = 'tools/dictation-rig/bin/post_call.py'
        source = subprocess.check_output(['git', 'show', f'{revision}:{path}'], cwd=ROOT, text=True)
        module = types.ModuleType('deadline_baseline')
        module.__file__ = str(ROOT / path)
        sys.modules[module.__name__] = module
        exec(compile(source, module.__file__, 'exec'), module.__dict__)
        suite.post_call = module
    return suite


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', help='Load post_call.py from this git revision; fixtures stay identical.')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    suite = load_suite(args.revision)
    suite.setUpModule()
    try:
        rows = []
        for case in map(json.loads, CASES.read_text().splitlines()):
            actual = run_case(suite, case)
            rows.append({'id': case['id'], 'actual': actual, 'expected': case['expected'],
                         'pass': actual == case['expected']})
        summary = {'revision': args.revision or 'working-tree', 'passed': sum(r['pass'] for r in rows),
                   'total': len(rows), 'unsafe_eligible': sum(r['actual']['eligible'] and not r['expected']['eligible'] for r in rows),
                   'unnecessary_holds': sum(not r['actual']['eligible'] and r['expected']['eligible'] for r in rows),
                   'rows': rows}
        if args.output:
            args.output.write_text(json.dumps(summary, indent=2) + '\n')
        print(json.dumps({k: v for k, v in summary.items() if k != 'rows'}))
        return 0 if summary['passed'] == summary['total'] else 1
    finally:
        suite.tearDownModule()


if __name__ == '__main__':
    raise SystemExit(main())
