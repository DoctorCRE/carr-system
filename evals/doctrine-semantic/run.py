#!/usr/bin/env python3
"""Measure the unchanged twenty K8 questions against the deployed Worker."""

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'retrieval-real-misses'))
from run_eval import ReadError, call_read


def measure(cases):
    rows = []
    for case in cases:
        row = {'id': case['id'], 'group': case['group'], 'q': case['q'],
               'gold_ids': case['gold_ids'], 'ranks': None, 'error': None}
        try:
            result = call_read('search-doctrine', {'q': case['q'], 'limit': 10})
            ids = [hit['section_id'] for hit in result['hits']]
            row.update(ranks=[ids.index(gold) + 1 if gold in ids else None
                              for gold in case['gold_ids']],
                       returned_ids=ids, fallback=result.get('fallback'),
                       semantic=result.get('semantic'))
        except ReadError as exc:
            row['error'] = str(exc)
        except (KeyError, TypeError, ValueError):
            row['error'] = 'invalid_result_contract'
        rows.append(row)
        print(case['id'] + ': ' + (row['error'] or str(row['ranks'])), file=sys.stderr)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, help='Compare with a prior run of these exact questions')
    parser.add_argument('--require-embeddings', action='store_true',
                        help='Fail unless every response reports complete semantic retrieval')
    args = parser.parse_args()
    fixture_path = HERE / 'questions.json'
    fixture_bytes = fixture_path.read_bytes()
    fixture = json.loads(fixture_bytes)
    digest = hashlib.sha256(fixture_bytes).hexdigest()
    baseline = json.loads(args.baseline.read_text()) if args.baseline else None
    if baseline and baseline['fixture_sha256'] != digest:
        parser.error('baseline must use the same frozen questions and gold IDs')
    rows = measure(fixture['cases'])
    result = {
        'version': 'doctrine-semantic-live/v1',
        'measured_at': datetime.now(ZoneInfo('America/Chicago')).isoformat(),
        'fixture_sha256': digest,
        'runner_source_revision': subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=HERE, text=True).strip(),
        'source': 'deployed Worker through run.sh call; no local model or reranking',
        'cases': rows,
    }
    result['gold_first'] = {group: sum(1 in (r['ranks'] or []) for r in rows if r['group'] == group)
                            for group in ['frozen', 'paraphrase']}
    if baseline:
        before = {r['id']: r['ranks'] for r in baseline['cases']}
        result['comparison'] = [{'id': r['id'], 'before': before[r['id']], 'after': r['ranks']}
                                for r in rows]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'gold_first': result['gold_first'], 'errors': sum(bool(r['error']) for r in rows)}))
    return int(any(r['error'] for r in rows) or
               (args.require_embeddings and any(
                   (r.get('semantic') or {}).get('status') != 'ready' for r in rows)))


if __name__ == '__main__':
    raise SystemExit(main())
