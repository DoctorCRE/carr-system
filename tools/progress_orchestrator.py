"""Display-only engineering facts for the authenticated orchestrator board."""
from datetime import datetime, timezone
import re

SCHEMA = 'doctorcre-orchestrator/v1'
STATES = ('waiting', 'working', 'review', 'fixing', 'merging', 'merged', 'live', 'blocked')
PRIVATE = re.compile(r'@|(?:/Users/|/home/|/private/|/tmp/)|(?:token|password|secret|authorization)\s*[:=]|[a-f0-9]{32,}', re.I)
REPO = re.compile(r'^(DoctorCRE/carr-system|jbookout/(doctorcre-app|software-factory))$')


def text(value, fallback='Not recorded', limit=220):
    if not isinstance(value, str) or not value.strip() or PRIVATE.search(value):
        return fallback
    return value.strip().splitlines()[0][:limit]


def links(card):
    refs = list(card.get('prs') or [])
    if card.get('pr'):
        refs.append({'repo': card.get('repo', 'DoctorCRE/carr-system'), 'number': card['pr']})
    result = []
    for ref in refs:
        if isinstance(ref, dict) and REPO.fullmatch(str(ref.get('repo', ''))) and isinstance(ref.get('number'), int) and ref['number'] > 0:
            href = f"https://github.com/{ref['repo']}/pull/{ref['number']}"
            if href not in result:
                result.append(href)
    return result


def state_of(card):
    stage = card.get('stage')
    if stage == 'live':
        return 'live'
    if stage == 'merged' or card.get('pr_phase') == 'Merged':
        return 'merged'
    if stage == 'blocked' or card.get('status') in ('blocked', 'question-for-orchestrator'):
        return 'blocked'
    if stage == 'fixing' or card.get('pr_phase') == 'Changes requested':
        return 'fixing'
    return {'queued': 'waiting', 'build': 'working', 'building': 'working', 'running': 'working',
            'merging': 'merging', 'review': 'review', 'ci': 'review', 'done': 'review'}.get(stage,
            {'queued': 'waiting', 'running': 'working'}.get(card.get('status'), 'review'))


def observation(sync):
    sync = sync or {}
    return {'state': 'stale' if sync.get('stale') else 'observed' if sync.get('checked_at') else 'unknown',
            'checked_at': sync.get('checked_at'), 'last_success_at': sync.get('last_success_at') or sync.get('last_verified_at') or sync.get('verified_at')}


def project(state, *, portfolio=None, observed_at=None):
    at = observed_at or datetime.now(timezone.utc).isoformat(timespec='seconds')
    tasks = {**(portfolio or {}).get('tasks', {}), **state.get('tasks', {})}
    cards, used = [], set()
    for identity, task in tasks.items():
        refs = links(task)
        if any(ref in used for ref in refs) or task.get('status') in ('superseded', 'failed'):
            continue
        used.update(refs)
        cards.append({'id': 'board:' + text(identity, 'work'), 'title': text(task.get('card_title') or task.get('title'), 'Engineering work'),
            'state': state_of(task), 'owner': text(task.get('executor')),
            'progress': text(task.get('blocked_reason') or task.get('release_wait') or task.get('summary') or task.get('pr_phase'), 'Awaiting a recorded progress update'),
            'changed_at': task.get('updated_at'), 'prs': refs,
            'delivery_evidence': text(task.get('evidence'), '', 440) if state_of(task) == 'live' else '',
            'origin': 'board', 'terminal': False,
            'area': {'DoctorCRE/carr-system': 'Shared backend', 'jbookout/doctorcre-app': 'DoctorCRE app',
                     'jbookout/software-factory': 'Development tools'}.get(task.get('repo'), 'Shared portfolio')})
    history = sorted((c for c in cards if c['state'] == 'live' or c['terminal']), key=lambda c: c['changed_at'] or '', reverse=True)
    active = [c for c in cards if c['state'] != 'live' and not c['terminal']]
    return {'schema': SCHEMA, 'observed_at': at, 'cards': active, 'history': history[:20],
            'omitted_history': max(0, len(history)-20),
            'sources': {'github': observation(state.get('github_sync')),
                        'portfolio': observation((portfolio or {}).get('github_sync'))}}
