"""Display-only engineering facts for the authenticated orchestrator board."""
from datetime import datetime, timezone
import re

SCHEMA = 'doctorcre-orchestrator/v1'
STATES = ('waiting', 'working', 'review', 'fixing', 'merging', 'merged', 'live', 'blocked')
PRIVATE = re.compile(r'@|(?:/Users/|/home/|/private/|/tmp/)|(?:token|password|secret|authorization)\s*[:=]|[a-f0-9]{32,}', re.I)
REPO = re.compile(r'^jbookout/(carr-system|doctorcre-app|software-factory)$')


def text(value, fallback='Not recorded', limit=220):
    if not isinstance(value, str) or not value.strip() or PRIVATE.search(value):
        return fallback
    return value.strip().splitlines()[0][:limit]


def links(card):
    refs = list(card.get('prs') or [])
    if card.get('pr'):
        refs.append({'repo': card.get('repo', 'jbookout/carr-system'), 'number': card['pr']})
    result = []
    for ref in refs:
        if isinstance(ref, dict) and REPO.fullmatch(str(ref.get('repo', ''))) and isinstance(ref.get('number'), int) and ref['number'] > 0:
            href = f"https://github.com/{ref['repo']}/pull/{ref['number']}"
            if href not in result:
                result.append(href)
    return result


def state_of(card, queued=False):
    stage = card.get('stage')
    if not queued and stage == 'live':
        return 'live'
    if not queued and (stage == 'merged' or card.get('pr_phase') == 'Merged'):
        return 'merged'
    if stage == 'blocked' or card.get('status') in ('blocked', 'question-for-orchestrator'):
        return 'blocked'
    if queued and stage == 'done' and card.get('merge_complete'):
        return 'merged'
    if stage == 'fixing' or card.get('pr_phase') == 'Changes requested':
        return 'fixing'
    return {'queued': 'waiting', 'build': 'working', 'building': 'working', 'running': 'working',
            'merging': 'merging', 'review': 'review', 'ci': 'review', 'done': 'review'}.get(stage,
            {'queued': 'waiting', 'running': 'working'}.get(card.get('status'), 'review'))


def observation(sync):
    sync = sync or {}
    return {'state': 'stale' if sync.get('stale') else 'observed' if sync.get('checked_at') else 'unknown',
            'checked_at': sync.get('checked_at'), 'last_success_at': sync.get('last_success_at') or sync.get('last_verified_at') or sync.get('verified_at')}


def project(state, queue, *, portfolio=None, observed_at=None):
    at = observed_at or datetime.now(timezone.utc).isoformat(timespec='seconds')
    tasks = {**(portfolio or {}).get('tasks', {}), **state.get('tasks', {})}
    indexed = {href: card for card in tasks.values() for href in links(card)}
    cards, used = [], set()
    for item in (queue or {}).get('items', []):
        refs = links(item)
        pins = {f"https://github.com/{ref['repo']}/pull/{ref['number']}": ref.get('head')
                for ref in item.get('prs', []) if isinstance(ref, dict) and 'repo' in ref and 'number' in ref}
        facts = [indexed[href] for href in refs if href in indexed
                 and (not pins.get(href) or indexed[href].get('pr_head') == pins[href])]
        used.update(refs)
        status = state_of(item, True)
        if facts and len(facts) == len(refs):
            states = [state_of(fact) for fact in facts]
            if all(s == 'live' for s in states):
                status = 'live'
            elif all(s in ('merged', 'live') for s in states):
                status = 'merged'
        event = item.get('last_event') or {}
        cards.append({'id': 'queue:' + text(item.get('card_id'), 'work'), 'title': text(item.get('title'), 'Engineering work'),
            'state': status, 'owner': text(item.get('agent') or item.get('desk')),
            'progress': text(event.get('reason'), 'Awaiting a recorded progress update'),
            'changed_at': event.get('at'), 'prs': refs,
            'origin': 'queue', 'terminal': item.get('stage') in ('failed', 'parked') or (item.get('stage') == 'done' and status not in ('merged', 'live')),
            'area': ', '.join(sorted({ref.split('/')[4] for ref in refs})) or 'Shared portfolio'})
    for identity, task in tasks.items():
        refs = links(task)
        if any(ref in used for ref in refs) or task.get('status') in ('superseded', 'failed'):
            continue
        used.update(refs)
        cards.append({'id': 'board:' + text(identity, 'work'), 'title': text(task.get('card_title') or task.get('title'), 'Engineering work'),
            'state': state_of(task), 'owner': text(task.get('executor')),
            'progress': text(task.get('blocked_reason') or task.get('release_wait') or task.get('summary') or task.get('pr_phase'), 'Awaiting a recorded progress update'),
            'changed_at': task.get('updated_at'), 'prs': refs,
            'origin': 'board', 'terminal': task.get('activity_status') == 'stale',
            'area': {'jbookout/carr-system': 'Shared backend', 'jbookout/doctorcre-app': 'DoctorCRE app',
                     'jbookout/software-factory': 'Development tools'}.get(task.get('repo'), 'Shared portfolio')})
    history = sorted((c for c in cards if c['state'] == 'live' or c['terminal']), key=lambda c: c['changed_at'] or '', reverse=True)
    active = [c for c in cards if c['state'] != 'live' and not c['terminal']]
    return {'schema': SCHEMA, 'observed_at': at, 'cards': active, 'history': history[:20],
            'omitted_history': max(0, len(history)-20),
            'sources': {'queue': {'state': 'observed' if queue is not None else 'unavailable',
                                  'revision': (queue or {}).get('revision'), 'last_success_at': at if queue is not None else None, 'checked_at': at if queue is not None else None},
                        'github': observation(state.get('github_sync')),
                        'portfolio': observation((portfolio or {}).get('github_sync'))}}


def retain_unavailable_queue(current, previous):
    if current['sources']['queue']['state'] != 'unavailable' or not previous:
        return
    saved = [card for section in ('cards','history') for card in previous.get(section, []) if card.get('origin') == 'queue']
    refs = {ref for card in saved for ref in card.get('prs', [])}
    for section in ('cards','history'):
        current[section] = [card for card in current[section] if not any(ref in refs for ref in card.get('prs', []))]
        current[section].extend(card for card in previous.get(section, []) if card in saved)
    source = previous.get('sources', {}).get('queue', {})
    current['sources']['queue']['last_success_at'] = source.get('last_success_at') or source.get('checked_at')
