"""Synthetic exercise facts shared by board behavior and payload tests."""
import json


def exercise_fields(source="1" * 40, repo="jbookout/carr-system"):
    return {"repo": repo, "pr_phase": "Merged", "merge_sha": source,
            "delivery_source_sha": source,
            "delivery_target": "https://exercise.example.invalid/target",
            "delivery_workflow": "synthetic outcome",
            "consumer_proof": {"schema": "carr-consumer-proof.v1", "outcome": "pass",
                               "repo": repo, "source_sha": source,
                               "target": "https://exercise.example.invalid/target", "workflow": "synthetic outcome",
                               "evidence_ref": "synthetic-receipt", "observed_at": "2026-10-01T12:00:00Z"}}


def seed_legacy_task(board, root, project, task_id, options):
    """Import a synthetic old-format record before exercising legacy CLI updates."""
    path = root / 'boards' / f'{project}.json'
    state = json.loads(path.read_text())
    if task_id not in state['tasks']:
        args = board.parser().parse_args(['task', project, task_id, *options])
        metadata = board.executor_metadata(args.executor)
        state['tasks'][task_id] = {'created_at': board.stamp(), 'title':args.title, 'executor':args.executor,
            **{field:getattr(args,field) or value for field,value in zip(('provider','model','effort'),metadata)}}
        path.write_text(json.dumps(state))


def seed_captured_task(root, project, task_id, executor="deterministic"):
    """Provision a synthetic captured job before an automation reports its status."""
    path = root / 'boards' / f'{project}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    state = json.loads(path.read_text()) if path.exists() else {
        'version': 2, 'project': project, 'title': 'Automation checks', 'kind': 'project',
        'tasks': {}, 'questions': {}, 'deliverables': [], 'notes': []}
    if task_id in state['tasks']:
        return
    copy = {'card_title': 'Automation checks', 'card_description': 'Verify synthetic automation outcomes.'}
    state.setdefault('work_requests', {})['WR-000001'] = {**copy, 'title': 'Automation checks'}
    state['tasks'].setdefault(task_id, {**copy, 'title': task_id, 'executor': executor,
        'work_request': 'WR-000001', 'slice_id': 'implicit'})
    path.write_text(json.dumps(state))
