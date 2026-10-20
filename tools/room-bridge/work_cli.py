"""Work queue commands used by dispatch.py. JSON is the board/daemon interface."""
import json
from pathlib import Path
import re
import uuid

import codex_models
from work_queue import WorkQueue, STAGES, EXECUTABLE, default_path
import work_runner

COMMANDS = ('queue', 'show', 'history', 'export', 'merge-order', 'prioritize', 'requeue',
            'stage', 'start-merge', 'details', 'complete', 'import-results', 'run')


def add_commands(sub):
    for name in COMMANDS:
        p = sub.add_parser(name, help=f'work queue {name}')
        if name in ('show', 'prioritize', 'requeue', 'stage', 'start-merge', 'details', 'complete'):
            p.add_argument('card_id')
        if name == 'history':
            p.add_argument('card_id', nargs='?')
        if name == 'prioritize':
            order = p.add_mutually_exclusive_group(required=True)
            order.add_argument('--before')
            order.add_argument('--top', action='store_true')
        if name in ('requeue', 'stage'):
            p.add_argument('--stage', required=True, choices=EXECUTABLE if name == 'requeue' else STAGES)
        if name == 'details':
            p.add_argument('json', help='metadata object; findings include id, level, summary and resolved')
        if name == 'complete':
            p.add_argument('--claim', required=True)
            p.add_argument('--result', required=True, help='verified executor receipt as JSON')
            p.add_argument('--evidence', required=True, help='receipt/termination evidence; reconcile effects first')
        if name == 'import-results':
            p.add_argument('path', type=Path)
        p.add_argument('--actor', default='orchestrator')
        if name in ('prioritize', 'requeue', 'stage', 'start-merge', 'details'):
            p.add_argument('--reason', required=True)


def send(args, task, registry, results):
    entry = registry.resolve(args.name)
    codex = entry['kind'] in codex_models.CODEX_KINDS
    options = dict(fresh=args.fresh, stream_output=args.stream_output, retrieval=args.retrieve,
                   family=args.family, effort=args.effort, writes=args.writes, checkout=args.checkout,
                   codex=codex, allow_phrasing=args.allow_phrasing, result_path=str(results.resolve()), registry_path=str(registry.path.resolve()))
    card_id = args.card_id or f'job:{uuid.uuid4()}'
    path = default_path()
    with WorkQueue(path) as queue:
        if args.stage:
            card = queue.requeue(card_id, stage=args.stage, task=task, options=options, desk=args.name,
                                 actor=args.actor, reason='dispatch stage requested')
        else:
            match = re.match(r'^(WR[^.]+)\.(S[^.]+)\.', card_id)
            card = queue.enqueue(card_id, desk=args.name, task=task, options=options, actor=args.actor,
                                 wr=args.wr or (match[1] if match else ''),
                                 slice=args.slice or (match[2] if match else ''),
                                 title=args.title or '', description=args.description or '',
                                 family=args.family or entry.get('family', ''),
                                 effort=args.effort or entry.get('effort', ''))
    work_runner.kick(path, registry.path, results)
    return {'status': 'queued', 'card_id': card_id, 'stage': card['stage'], 'position': card['position'], 'db': str(path)}


def handle(args, registry, results):
    if args.cmd not in COMMANDS:
        return None
    path = default_path()
    mutate = args.cmd in ('prioritize', 'requeue', 'stage', 'start-merge', 'details', 'complete', 'import-results')
    if args.cmd == 'run':
        return work_runner.main(['--db', str(path), '--registry', str(registry.path), '--results', str(results)])
    with WorkQueue(path, read_only=not mutate) as queue:
        if args.cmd in ('queue', 'export'):
            out = queue.export()
        elif args.cmd == 'show':
            out = queue.get(args.card_id)
        elif args.cmd == 'history':
            out = [e for e in queue.export()['history'] if not args.card_id or e['card_id'] == args.card_id]
        elif args.cmd == 'merge-order':
            out = queue.merge_order()
        elif args.cmd == 'prioritize':
            out = queue.prioritize(args.card_id, before=args.before, actor=args.actor, reason=args.reason)
        elif args.cmd == 'requeue':
            out = queue.requeue(args.card_id, stage=args.stage, actor=args.actor, reason=args.reason)
        elif args.cmd == 'stage':
            out = queue.transition(args.card_id, args.stage, actor=args.actor, reason=args.reason)
        elif args.cmd == 'start-merge':
            out = queue.begin_merge(args.card_id, actor=args.actor, reason=args.reason)
        elif args.cmd == 'details':
            fields = json.loads(args.json)
            if not isinstance(fields, dict):
                raise ValueError('details must be a JSON object')
            out = queue.update(args.card_id, actor=args.actor, reason=args.reason, **fields)
        elif args.cmd == 'complete':
            result = json.loads(args.result)
            result['evidence'] = args.evidence
            queue.finish(args.card_id, args.claim, result, actor=args.actor)
            out = queue.get(args.card_id)
        elif args.cmd == 'import-results':
            out = queue.import_results(args.path, actor=args.actor)
    if mutate and args.cmd != 'import-results':
        work_runner.kick(path, registry.path, results)
    print(json.dumps(out, indent=2))
    return 0
