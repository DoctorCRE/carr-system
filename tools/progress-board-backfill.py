#!/usr/bin/env python3
"""Propose Work Request parents and card copy without publishing or changing production."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import progress_hierarchy as hierarchy


def read(path):
    return json.loads(Path(path).read_text()) if path else {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', help='a local board copy')
    parser.add_argument('--facts', help='cached PR facts; no GitHub calls')
    parser.add_argument('--catalog', help='Work Request records keyed by human_ref')
    parser.add_argument('--summaries', help='validated Model Room copy proposals keyed by original card ID')
    parser.add_argument('--proposal', required=True, help='write dry-run JSON; with --apply, read this exact proposal')
    parser.add_argument('--apply', action='store_true', help='capture through report-problem, write a separate migrated board; never publish')
    parser.add_argument('--output', help='separate migrated board path, required with --apply')
    args = parser.parse_args()
    state = read(args.source)
    if args.apply:
        if not args.output or Path(args.output).resolve() == Path(args.source).resolve():
            parser.error('--apply requires a separate --output path')
        import importlib.util
        spec = importlib.util.spec_from_file_location('progress_board_backfill', ROOT / 'tools/progress_board.py')
        board = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(board)
        migrated = hierarchy.apply_backfill(state, read(args.proposal), board.call_verb)
        hierarchy.project(migrated)
        Path(args.output).write_text(json.dumps(migrated, indent=2) + '\n')
        print('Captured through record-layer intake; wrote a separate board. Nothing published.')
        return
    proposal = hierarchy.backfill(state, catalog=read(args.catalog), facts=read(args.facts))
    summaries = read(args.summaries)
    for key, values in summaries.items():
        if key not in proposal['proposed_copy']:
            parser.error(f'unknown summary card: {key}')
        hierarchy.validate_copy(**values)
        proposal['proposed_copy'][key] = {'proposed_copy': {
            'generator': 'model-room-import.v1',
            'source_digest': hierarchy.digest({**state.get('legacy_history', {}), **state['tasks']}[key]), 'values': values}}
    proposal['summary_required'] = [key for key in proposal['summary_required'] if key not in summaries]
    identities = {}
    for key, task in state['tasks'].items():
        if task.get('pr'):
            assignment = proposal['assignments'][key]
            identity = (assignment['request_key'], assignment['slice_id'], task.get('repo', 'jbookout/carr-system'), task['pr'])
            identities[identity] = identities.get(identity, False) or hierarchy.merged(task)
    proposal['counts'] = {'work_requests': len(proposal['requests']),
        'slices': sum(max(1, len(row['slices'])) for row in proposal['requests'].values()),
        'active_pr_cards': sum(not done for done in identities.values()),
        'completed_pr_cards': sum(identities.values()), 'legacy_triage_cards': len(proposal['later_triage'])}
    Path(args.proposal).write_text(json.dumps(proposal, indent=2) + '\n')
    print(json.dumps({'dry_run': True, 'counts': proposal['counts'],
        'would_capture': [row['args']['title'] for row in proposal['create_requests']],
        'summary_required': proposal['summary_required'],
        'samples': [row['proposed_copy']['values'] for row in list(proposal['proposed_copy'].values())[:10] if 'proposed_copy' in row]}, indent=2))


if __name__ == '__main__':
    main()
