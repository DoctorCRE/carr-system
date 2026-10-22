"""Work Request addresses and the versioned progress-board projection."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter

REPOSITORIES = {'jbookout/carr-system': 'carr', 'jbookout/doctorcre-app': 'app',
                'jbookout/software-factory': 'factory'}


def wr_address(ref):
    match = re.fullmatch(r'WR-0*(\d+)', ref or '')
    if not match:
        raise ValueError(f'Captured Work Request required: {ref}')
    return 'WR' + str(int(match[1]))


def project(state):
    requests, slices, cards = {}, {}, {}
    aliases = state.setdefault('card_aliases', {})
    jobs = state.setdefault('card_jobs', {})
    for ref, request in state.get('work_requests', {}).items():
        propose_copy(request)
        validate_copy(request['card_title'], request['card_description'])
        address = wr_address(ref)
        requests[address] = {'id': address, 'work_request': ref, 'card_title': request['card_title'],
                             'card_description': request['card_description'], 'slices': [],
                             'counts': dict.fromkeys(('done', 'active', 'blocked', 'failed'), 0)}
        registered = request.get('slices') or [{'slice_id': 'implicit', 'title': request['card_title'],
            'card_title': request['card_title'], 'card_description': request['card_description']}]
        next_ordinal = max((row.get('ordinal', 0) for row in registered), default=0)
        for row in registered:
            if 'ordinal' not in row:
                next_ordinal += 1
                row['ordinal'] = next_ordinal
            propose_copy(row)
            validate_copy(row['card_title'], row['card_description'])
            ordinal = row['ordinal']
            if not isinstance(ordinal, int) or ordinal < 1:
                raise ValueError('Slice ordinals must be positive integers')
            sid = f'{address}.S{ordinal}'
            if sid in slices:
                raise ValueError(f'Duplicate slice ordinal: {sid}')
            slices[sid] = {'id': sid, 'parent_id': address, 'slice_id': row['slice_id'],
                           'card_title': row['card_title'], 'card_description': row['card_description'],
                           'cards': [], 'pr_counts': {}, 'status': 'active'}
            requests[address]['slices'].append(sid)
    for key, task in sorted({**state.get('legacy_history', {}), **state.get('tasks', {})}.items(), key=lambda item: (
            0 if (item[1].get('pr') or item[1].get('pr_links')) and re.fullmatch(r'WR\d+\.S\d+\.J\d+', item[1].get('card_id', '')) else 1,
            item[1].get('created_at', ''), item[0])):
        if not task.get('work_request') and state.get('legacy_work_request'):
            task['work_request'] = state['legacy_work_request']
            task['slice_id'] = 'implicit'
            triage = state.setdefault('later_triage', [])
            if key not in triage:
                triage.append(key)
        propose_copy(task)
        placeholder = task.get('summary_required') and not (task.get('card_title') and task.get('card_description'))
        display_copy = {'card_title': 'Older work',
            'card_description': 'Older work from before cards had summaries; open details for the PR link.'} if placeholder else {
            'card_title': task.get('card_title'), 'card_description': task.get('card_description')}
        validate_copy(display_copy['card_title'], display_copy['card_description'])
        ref = task.get('work_request')
        parent = requests[wr_address(ref)]
        candidates = [slices[sid] for sid in parent['slices']]
        target = next((s for s in candidates if s['slice_id'] == task.get('slice_id', 'implicit')), None)
        if target is None:
            raise ValueError(f'Unregistered slice for {key}')
        sid = target['id']
        identity = str(task.get('job_id') or task.get('job_ref') or key)
        allocation = jobs.setdefault(sid, {})
        links = task.get('pr_links') or ([{'repo': task.get('repo', 'jbookout/carr-system'), 'pr': task['pr']}] if task.get('pr') else [{}])
        pr_keys = [f"pr:{row.get('repo')}#{row['pr']}" for row in links if row.get('pr')]
        number = allocation.get(identity)
        if number is None:
            number = next((allocation[k] for k in pr_keys if k in allocation), None)
        else:
            displaced = {allocation[k] for k in pr_keys if k in allocation and allocation[k] != number}
            for allocated, value in allocation.items():
                if value in displaced:
                    allocation[allocated] = number
        if number is None:
            number = max(allocation.values(), default=0) + 1
        allocation[identity] = number
        for pr_key in pr_keys:
            allocation[pr_key] = number
        addresses = []
        for row in links:
            address = f'{sid}.J{number}'
            if row.get('pr'):
                address += f".{REPOSITORIES[row['repo']]}#{row['pr']}"
            addresses.append(address)
            card = {**task, **display_copy, **row, 'id': address, 'task_id': key, 'parent_id': sid,
                    'archived': key in state.get('legacy_history', {}) and key not in state.get('tasks', {})}
            if len(links) > 1:
                for field in ('merge_sha', 'merged_at', 'github_state'):
                    if field not in row:
                        card.pop(field, None)
            if placeholder and (merged(card) or str(card.get('github_state', '')).upper() in ('MERGED', 'CLOSED')):
                card['archived'] = True
            previous = cards.get(address)
            if previous and not previous['archived'] and card['archived']:
                card = previous
            if previous and merged(previous) and not merged(card):
                card.update({field: previous[field] for field in ('merge_sha', 'merged_at')})
            card['done'] = merged(card) or (not card.get('pr') and
                (card.get('status') == 'done' or card.get('stage') == 'live'))
            card['stage'] = card_stage(card)
            cards[address] = card
            if address not in target['cards']:
                target['cards'].append(address)
        old = task.get('card_id')
        if old and old != addresses[0]:
            aliases[old] = addresses[0]
        aliases[key] = addresses[0]
        task['card_id'] = addresses[0]
    for row in slices.values():
        values = [cards[key] for key in row['cards']]
        row['pr_counts'] = dict(Counter('done' if t['done'] else t['stage'] for t in values if t.get('pr') and not t['archived']))
        row['job_counts'] = dict(Counter(t['stage'] for t in values if not t.get('pr') and not t['archived']))
        row['status'] = aggregate([v for v in values if not v['archived']]) if any(not v['archived'] for v in values) or not values else 'archived'
        row['updated_at'] = max((t.get('updated_at', '') for t in values), default='')
        counts = requests[row['parent_id']]['counts']
        counts[row['status']] = counts.get(row['status'], 0) + 1
    for row in requests.values():
        children = [slices[sid] for sid in row['slices']]
        row['status'] = aggregate(children)
        row['updated_at'] = max((s['updated_at'] for s in children), default='')
    return {'schema': 'work-request-hierarchy.v1', 'work_requests': requests, 'slices': slices,
            'cards': cards, 'aliases': aliases,
            'later_triage': [aliases[key] for key in state.get('later_triage', []) if key in aliases]}


def merged(card):
    return bool(card.get('pr') and card.get('merged_at') and
                re.fullmatch(r'[0-9a-f]{40}', str(card.get('merge_sha', ''))))


def aggregate(values):
    if values and all(v.get('status') == 'archived' for v in values):
        return 'archived'
    values = [v for v in values if v.get('status') != 'archived']
    if values and all(v.get('done') or (v.get('status') == 'done' and 'pr_counts' in v) for v in values):
        return 'done'
    if any(v.get('status') in ('blocked', 'question-for-orchestrator') or v.get('health') == 'blocked' for v in values):
        return 'blocked'
    if any(v.get('status') in ('failed', 'superseded') for v in values):
        return 'failed'
    return 'active'


def validate_copy(title, description):
    if not isinstance(title, str) or not 2 <= len(title.split()) <= 3 or not re.fullmatch(r"[A-Za-z][A-Za-z' ]*", title):
        raise ValueError('Card titles require two or three plain words')
    words = title.lower().split()
    if words[0] in {'a', 'an', 'the'} or words[-1] in {'and', 'or', 'that', 'the', 'a', 'an', 'to', 'for', 'with', 'of', 'in', 'on'}:
        raise ValueError('Card titles must name the work, not a sentence fragment')
    if re.search(r'(?i)\b(?:[a-f0-9]{8,}|(?:WR|PR)\s*\d+)', title):
        raise ValueError('Identifiers belong in card details')
    if not isinstance(description, str) or not description.strip() or re.match(r'(?i)\s*(?:(?:WR|PR|J|S)[- .#]?\d|(?:carr|app|factory)#\d|#\d|\d{4,}|[0-9a-f]{8,}\b)', description):
        raise ValueError('Card descriptions must open with human language')
    sentences = re.sub(r'\b(?:Dr|Mr|Mrs|Ms)\.', '', description)
    if len(re.findall(r'[.!?](?:\s|$)', sentences)) > 2:
        raise ValueError('Card descriptions require one or two sentences')


def plain_text(text):
    text = re.sub(r'(?m)^#{1,6}[^\n]*\n', '', str(text or ''))
    text = re.sub(r'\[([^]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'`([^`]*)`', lambda match: match[1].replace('-', ' '), text)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'\*\*|</?[^>]+>', '', text)
    text = re.sub(r'(?i)\b(?:WR[- ]?\d+|PR\s*#?\d+|[a-f0-9]{12,})\b', '', text)
    text = re.sub(r'(?i)^\s*(?:feat|fix|test|chore|refactor)\(([^)]*)\):\s*', r'\1: ', text)
    text = re.sub(r'(?i)^\s*(?:feat|fix|test|chore|refactor):\s*', '', text)
    text = re.sub(r'^\s*(?:V\d+[- ]+[A-Z]\d*[a-z]?:?\s*|[A-Z]\d+[a-z]?:\s*)', '', text)
    return re.sub(r'\s+', ' ', text.strip(' #*\n')).strip()


TOPICS = (
    (r'lease.expiry', 'Lease expiry radar'),
    (r'client.identifying', 'Data privacy'),
    (r'^Bump actions/|github.actions', 'Actions updates'),
    (r'^Bump |^chore\(deps', 'Build dependencies'),
    (r'\bdatabase|\bschema\b', 'Database repair'),
    (r'cadence|activation anchor', 'Cadence checks'),
    (r'provider.*API|billing', 'Provider billing'),
    (r'social.media|social.automation', 'Social drafting'),
    (r'^Dot research: Job queue', 'Research queue'),
    (r'what.?s.new|whats new', 'Recent changes'),
    (r'job watchdog|watchdog.*job', 'Job monitoring'),
    (r'SECURITY DEFINER', 'Database safety'),
    (r'\bQA\b|qa fixes', 'App repairs'),
    (r'slices register|slice registry', 'Slice registration'),
    (r'\bV\d+\b.*delivery', 'Application delivery'),
    (r'site map|sitemap', 'Site navigation'),
    (r'progress.?board|\bboard\b', 'Board cleanup'),
    (r'queue.*spacing|spacing.*queue', 'Queue spacing'),
    (r'merge.queue|\bFIFO\b', 'Merge queue'),
    (r'rule.*(?:admi|approv|activat)|admission.*rule', 'Rule admission'),
    (r'\brule\b|doctrine', 'Rule delivery'),
    (r'\btour\b', 'Tour tools'), (r'\blead\b', 'Lead map'),
    (r'\bcredential|\btoken|oauth', 'Access checks'),
    (r'\bdispatch|\bjob\b|\bcheckout', 'Job dispatch'),
    (r'\bresearch\b|\bstudy\b', 'Research findings'),
    (r'\btest|\bCI\b|browser', 'Test coverage'),
)


def propose_copy(record):
    source = plain_text(record.get('title'))
    title = next((label for pattern, label in TOPICS if re.search(pattern, source, re.I)), None)
    stop = {'a', 'an', 'the', 'this', 'that', 'to', 'for', 'of', 'in', 'on', 'with', 'and', 'or', 'by', 'from', 'it', 'is', 'are', 'as', 'its', 'each'}
    words = [w for w in source.split() if re.fullmatch(r"[A-Za-z][A-Za-z']*", w) and w.lower() not in stop]
    if title is None and len(words) >= 2 and not source.startswith('Dot research:'):
        title = ' '.join(words[:3])
        title = title[:1].upper() + title[1:]
    description = plain_text(record.get('description') or record.get('body') or record.get('summary'))
    if len(description.split()) < 7 or re.match(r'(?i)^(?:absent|natural run|summary)\b', description):
        description = source
    if re.match(r'(?i)^Bump |^chore\(deps', source):
        description = 'Update the selected dependency to keep the build configuration current.'
    elif source.lower().startswith('industry events'):
        description = 'Verify industry events so missing application features can be identified.'
    elif source.lower().startswith('crm capture'):
        description = 'Check that CRM capture saves new information reliably for later use.'
    elif re.search(r'(?i)site map|sitemap', source):
        description = 'Update the site navigation so people can find the right section.'
    if re.match(r'(?i)^This repair round|^Dot research:', description):
        description = ''
    if record.get('title', '').startswith('Dot research:'):
        description = {'Research queue': 'Work through the research queue and record the findings.',
            'Provider billing': 'Check official provider documentation for API billing and reporting support.',
            'Social drafting': 'Research how to automate the weekly social media drafting job.'}.get(title, description)
    if description:
        sentences = re.findall(r'.*?[.!?](?=\s|$)', description)
        description = next((sentence.strip() for sentence in sentences
            if 7 <= len(sentence.split()) and len(sentence) <= 320
            and not re.match(r'(?i)^\s*(?:It pairs|Do not merge|Skills used|[A-Z]\d+\b)', sentence)), '')
    if not description and title and source and len(source) <= 300 and not source.startswith('Dot research:'):
        description = source.rstrip(' .') + '.'

    if title == 'Lease expiry radar':
        description = 'Expose lease expiry data so the app can show upcoming lease deadlines.'
    elif title == 'Data privacy':
        description = 'Remove client identifying data from the public source.'
    if description:
        description = description[:1].upper() + description[1:]
        description = re.sub(r'^Repairs (.+?) failing with', r'Repair \1 when it fails with', description)
        description = re.sub(r'^Repairs\b', 'Repair', description)
    if not title or not description:
        if record.get('card_title') and record.get('card_description'):
            validate_copy(record['card_title'], record['card_description'])
            record.pop('summary_required', None)
            return
        record['summary_required'] = True
        return
    try:
        validate_copy(title, description)
    except ValueError:
        record['summary_required'] = True
        return
    previous = record.get('proposed_copy', {})
    if previous.get('generator', '').startswith('plain-summary.'):
        for field, proposed_field in [('card_title', 'title'), ('card_description', 'description')]:
            if record.get(field) == previous.get('values', {}).get(proposed_field):
                record.pop(field, None)
    record['proposed_copy'] = {'generator': 'plain-summary.v2',
        'source_digest': digest([record.get('title'), record.get('summary'), record.get('description'), record.get('body')]),
        'values': {'title': title, 'description': description}}
    record.setdefault('card_title', title)
    record.setdefault('card_description', description)
    record.pop('summary_required', None)


def apply_copy_proposal(record, proposal):
    previous = record.get('proposed_copy', {})
    for field, name in [('card_title', 'title'), ('card_description', 'description')]:
        if not record.get(field) or (previous.get('generator', '').startswith('plain-summary.')
                and record[field] == previous.get('values', {}).get(name)):
            record[field] = proposal['values'][name]
    record['proposed_copy'] = copy.deepcopy(proposal)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def related_pr_groups(facts):
    # Only combine work with concrete shared evidence, never a generic topic label.
    groups = {key: key for key in facts}
    stop = {'fix', 'fixes', 'repair', 'repairs', 'review', 'findings', 'feat', 'chore',
            'test', 'tests', 'add', 'update', 'the', 'and', 'for', 'with', 'from', 'after', 'round'}
    def signals(row):
        title = plain_text(row.get('title')).lower()
        words = set(re.findall(r'[a-z]{3,}', title)) - stop
        branch = row.get('headRefName') or row.get('branch') or ''
        if branch.startswith('dependabot/github_actions/'):
            prefix = 'github-actions'
        else:
            parts = [w for w in re.split(r'[-_/]', branch.lower()) if w and w not in stop and not w.isdigit()]
            prefix = '-'.join(parts[:2]) if len(parts) >= 2 else ''
        return words, prefix
    keys = sorted(facts)
    for index, key in enumerate(keys):
        words, branch = signals(facts[key])
        for other in keys[:index]:
            if key.split('#')[0] != other.split('#')[0]:
                continue
            theirs, other_branch = signals(facts[other])
            shared = len(words & theirs)
            similar = shared >= 2 and shared / max(len(words), len(theirs)) >= .6
            if (branch and branch == other_branch) or similar:
                old, new = groups[key], groups[other]
                for member in groups:
                    if groups[member] == old:
                        groups[member] = new
    return {key: 'pr:' + group for key, group in groups.items()}


def backfill(state, catalog=None, facts=None, summaries=None):
    import uuid
    catalog = catalog or {}
    facts = facts or {}
    summaries = summaries or {}
    assignments, requests, proposed = {}, {}, {}
    related = related_pr_groups(facts)
    for key, original in {**state.get('legacy_history', {}), **state.get('tasks', {})}.items():
        task = copy.deepcopy(original)
        fact = facts.get(f"{task.get('repo', 'jbookout/carr-system')}#{task.get('pr')}", {})
        if not fact:
            target = re.search(r'(carr-system|doctorcre-app|software-factory)-(\d+)$', key)
            if target:
                fact = facts.get(f'jbookout/{target[1]}#{target[2]}', {})
        if not fact:
            note = str(task.get('note') or '')
            opened = re.search(r'Opened (carr-system|doctorcre-app|software-factory) PR (\d+)', note)
            if opened:
                fact = facts.get(f'jbookout/{opened[1]}#{opened[2]}', {})
        if fact:
            task['title'] = fact.get('title') or task.get('title')
            task['body'] = fact.get('body')
        propose_copy(task)
        if key in summaries:
            validate_copy(**summaries[key])
            apply_copy_proposal(task, {'generator': 'model-room-import.v1',
                'source_digest': digest(original), 'values': summaries[key]})
            task.pop('summary_required', None)
        proposed[key] = {k: task[k] for k in ('proposed_copy', 'summary_required') if k in task}
        ref = original.get('work_request') or original.get('work_request_ref')
        text = str(task.get('title') or '')
        refs = set(re.findall(r'(?i)\bWR-(\d+)\b', text + '\n' + str(task.get('body') or '')))
        if not ref and len(refs) == 1:
            ref = f'WR-{int(next(iter(refs))):06d}'
        series = original.get('plan_id') or original.get('plan_ref') or original.get('milestone')
        if not series:
            match = re.search(r'\b(V\d+)\s+([A-Z]\d+[A-Za-z]?|\d+[A-Z]|[A-Z])\b', text)
            series = match[1] if match else None
        pr_key = f"{original.get('repo', 'jbookout/carr-system')}#{original.get('pr')}"
        standalone = related.get(pr_key) if original.get('pr') and fact else None
        group = ref or ('series:' + str(series) if series else standalone or 'legacy')
        request = requests.setdefault(group, copy.deepcopy(catalog.get(ref) or state.get('work_requests', {}).get(ref) or {
            'title': 'Legacy backlog' if group == 'legacy' else (str(series) + ' delivery' if series and not ref else text),
            'description': (task.get('card_description') or text) if standalone and group == standalone else
                           'Recover the work history and keep related changes together for later triage.'}))
        if (task.get('card_title') and task.get('card_description') and group != 'legacy'
                and not group.startswith('series:') and ref not in catalog
                and ref not in state.get('work_requests', {})):
            if task.get('proposed_copy', {}).get('generator') == 'model-room-import.v1':
                request['card_title'] = task['card_title']
                request['card_description'] = task['card_description']
            else:
                request.setdefault('card_title', task['card_title'])
                request.setdefault('card_description', task['card_description'])
        request.setdefault('slices', [])
        slice_ref = original.get('slice_id') or original.get('slice_ref') or original.get('slice')
        if not slice_ref and series:
            slice_match = re.search(r'\bV\d+\s+([A-Z]\d+[A-Za-z]?|\d+[A-Z]|[A-Z])\b', text)
            slice_ref = slice_match[1] if slice_match else None
        if slice_ref and not any(row['slice_id'] == slice_ref for row in request['slices']):
            request['slices'].append({'slice_id': slice_ref, 'title': task.get('card_title') or 'Work details',
                                      'description': task.get('card_description') or request['description']})
        assignments[key] = {'request_key': group, 'slice_id': slice_ref or 'implicit',
                            'collection': 'tasks' if key in state.get('tasks', {}) else 'legacy_history'}
    for key, row in requests.items():
        if (row['slices'] and not any(s['slice_id'] == 'implicit' for s in row['slices'])
                and any(a['request_key'] == key and a['slice_id'] == 'implicit' for a in assignments.values())):
            row['slices'].append({'slice_id': 'implicit', 'title': 'Other work', 'description': 'Keep unsliced work visible within its Work Request.'})
    creates = []
    for key, row in requests.items():
        if not key.startswith('WR-'):
            row['slices'].sort(key=lambda item: (str(item['slice_id']).upper() == 'E2E',
                [(0, int(w)) if w.isdigit() else (1, w) for w in re.split(r'(\d+)', str(item['slice_id']))]))
        next_ordinal = max((s.get('ordinal', 0) for s in row['slices']), default=0)
        for slice_row in row['slices']:
            if 'ordinal' not in slice_row:
                next_ordinal += 1
                slice_row['ordinal'] = next_ordinal
        propose_copy(row)
        if key.startswith('WR-'):
            continue
        order = ', '.join(str(s['slice_id']) for s in row['slices'])
        outcome = row['description'] + (f' Preserve the slice order: {order}.' if order else '')
        args = {'situation': 'Engineering work tracking and progress board legacy backfill',
                'title': row['title'], 'desired_outcome': outcome,
                'acceptance_criteria': [{'id': 'AC-HISTORY', 'text': 'All assigned legacy cards retain their evidence and ordered slices.'},
                                        {'id': 'AC-TRIAGE', 'text': 'Legacy work without a confident parent is listed for later human triage.'}]}
        args['idempotency_key'] = str(uuid.uuid5(uuid.NAMESPACE_URL, 'progress-board-backfill:' + state['project'] + ':' + key))
        creates.append({'request_key': key, 'verb': 'report-problem', 'args': args})
    return {'schema': 'progress-board-backfill.v1', 'source_digest': digest(state), 'assignments': assignments,
            'requests': requests, 'create_requests': creates, 'proposed_copy': proposed,
            'later_triage': [key for key, row in assignments.items() if row['request_key'] == 'legacy'],
            'summary_required': [key for key, row in proposed.items() if row.get('summary_required')]}


def apply_backfill(state, proposal, call):
    if proposal['source_digest'] != digest(state):
        raise ValueError('Board changed since dry run')
    bindings = {key: key for key in proposal['requests'] if key.startswith('WR-')}
    preview_bindings = dict(bindings)
    next_ref = max((int(wr_address(ref)[2:]) for ref in bindings), default=0)
    for index, row in enumerate(proposal['create_requests'], start=1):
        preview_bindings[row['request_key']] = f'WR-{next_ref + index:06d}'
    project(bind_backfill(state, proposal, preview_bindings))
    for row in proposal['create_requests']:
        receipt = call(row['verb'], row['args'])
        if not receipt.get('ok'):
            raise ValueError('Work Request capture failed')
        wr_address(receipt.get('human_ref'))
        bindings[row['request_key']] = receipt['human_ref']
    return bind_backfill(state, proposal, bindings)


def bind_backfill(state, proposal, bindings):
    result = copy.deepcopy(state)
    result['work_requests'] = {bindings[key]: copy.deepcopy(row) for key, row in proposal['requests'].items()}
    result['legacy_work_request'] = bindings.get('legacy')
    result['later_triage'] = list(proposal['later_triage'])
    for key, assignment in proposal['assignments'].items():
        task = result[assignment.get('collection', 'tasks')][key]
        task['work_request'] = bindings[assignment['request_key']]
        task['slice_id'] = assignment['slice_id']
        proposed = proposal['proposed_copy'][key]
        if proposed.get('proposed_copy'):
            apply_copy_proposal(task, proposed['proposed_copy'])
        if proposed.get('summary_required'):
            task['summary_required'] = True
        for field in ('plan_id', 'plan_ref'):
            task.pop(field, None)
    return result


def request_record(card, passport=None):
    wr_address(card.get('human_ref'))
    row = {'title': card['title'], 'description': card['desired_outcome'], 'slices': []}
    if passport:
        ordered = sorted(passport['slice_plan']['slices'], key=lambda s: s['ordinal'])
        row['slices'] = [{'slice_id': s['slice_ref'], 'title': s['objective'],
                          'description': s['objective'], 'ordinal': s['ordinal']} for s in ordered]
    return row


def load_request(ref, call):
    card = call('work-request-card', {'work_request': ref})
    passport = None
    if card.get('plan'):
        try:
            passport = call('engineering-passport', {'work_request': ref})
        except RuntimeError as error:
            if str(error) != 'engineering-passport refused: engineering_slice_plan_not_registered':
                raise
    return request_record(card, passport)


def card_stage(card):
    if card.get('done'):
        return 'merged'
    if card.get('stage') in ('queued', 'build', 'review', 'ci'):
        return card['stage']
    return {'queued': 'queued', 'running': 'build', 'review': 'review', 'blocked': 'review',
            'failed': 'ci', 'superseded': 'ci'}.get(card.get('status'), 'review')
