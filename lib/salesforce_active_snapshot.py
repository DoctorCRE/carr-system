"""Reconcile an explicitly authoritative Salesforce list through deployed verbs.

Bindings contain reviewed identity/account matches, never guessed Opportunity IDs.
Reports, bindings, plans and receipts belong outside the repository.
"""

import copy
import csv
import datetime as dt
import io
import re
from collections import Counter

from lib.salesforce_deal_sync import OWNERS, STAGES, fingerprint, norm, sfid

HEADERS = 'sf_id_tail|name|type|close|commission|phase|transaction|owner|company'.split('|')


def build_plan(source, board, *, expected_count, bindings):
    board = copy.deepcopy(board)
    reader = csv.DictReader(io.StringIO(source), delimiter='|', strict=True)
    if reader.fieldnames != HEADERS:
        raise ValueError('snapshot_headers_invalid')
    rows = list(reader)
    if len(rows) != expected_count or not rows:
        raise ValueError('report_count_mismatch')
    items = []
    for row in rows:
        if None in row or any(v is None for v in row.values()) or not row['name'].strip():
            raise ValueError('snapshot_row_invalid')
        key = row['sf_id_tail']
        if not re.fullmatch(r'[A-Za-z0-9]{8}', key):
            raise ValueError('snapshot_identity_invalid')
        binding = bindings.get(key, {})
        sid = binding.get('salesforce_id')
        if sid and (not re.fullmatch(r'006[A-Za-z0-9]{15}', sid) or not sid.endswith(key)):
            raise ValueError('binding_identity_invalid')
        item = {'source_key': key, 'name': row['name'], 'kind': 'held'}
        items.append(item)
        stage = norm(row['phase'])
        status = 'lost' if stage == 'closed lost' else 'closed' if stage in ('fully closed out', 'closed won') else 'active'
        desired = {'operating_state': 'active'}
        item.update(status=status, desired=desired)
        if status != 'active':
            desired['outcome'] = 'lost' if status == 'lost' else 'won'
            if status == 'closed':
                desired['phase'] = 'closed'
            item['reason'] = 'terminal_status_requires_separate_reconciliation'
            continue
        if stage not in STAGES or STAGES[stage] == 'closed':
            item['reason'] = 'unmapped_stage'
            continue
        desired['phase'] = STAGES[stage]
        if sid:
            desired['salesforce_id'] = sid
        matches = [d for d in board['deals'] if (sid and sfid(d.get('salesforce_id')) == sfid(sid))
                   or (not sid and (d.get('salesforce_id') or '').endswith(key))]
        if not matches and binding.get('deal_id'):
            matches = [d for d in board['deals'] if d['id'] == binding['deal_id']]
            if not matches:
                item['reason'] = 'bound_deal_missing'
                continue
        if not matches:
            matches = [d for d in board['deals'] if norm(d['name']) == norm(row['name'])]
        if len(matches) > 1 or (matches and matches[0].get('salesforce_id') and
                               (sfid(matches[0]['salesforce_id']) != sfid(sid) if sid else not matches[0]['salesforce_id'].endswith(key))):
            item['reason'] = 'ambiguous_or_conflicting_identity'
            continue
        prior = matches[0] if matches else None
        item['prior'] = prior
        owner = binding.get('owner') or OWNERS.get(norm(row['owner'])) or (prior or {}).get('owner')
        if owner not in ('joe', 'dell'):
            item['reason'] = 'partner_owner_unmapped'
            continue
        desired['owner'] = owner
        agent = row['owner'].strip() if not OWNERS.get(norm(row['owner'])) else None
        if agent:
            item['source_owner'] = agent
            if binding.get('account_client_id') or (prior or {}).get('account_client_id'):
                desired['market_agent'] = agent
        if prior:
            item.update(kind='update', changes=[k for k, v in desired.items()
                        if (sfid(prior.get(k)) != sfid(v) if k == 'salesforce_id' else prior.get(k) != v)])
            if not item['changes']:
                item['kind'] = 'unchanged'
            continue
        if not sid:
            item['reason'] = 'full_opportunity_id_required_for_create'
            continue
        date = dt.datetime.strptime(row['close'], '%m/%d/%Y').date().isoformat() if row['close'] else None
        if binding.get('account_client_id') and binding.get('client_name') and binding.get('market'):
            args = {k: binding[k] for k in ('account_client_id', 'client_name', 'market')}
            args.update(deal_name=row['name'], phase=desired['phase'])
            if binding.get('research_evidence'):
                args['research_evidence'] = binding['research_evidence']
            if agent:
                args['agent_name'] = agent
            create = {'verb': 'create-national-market-deal', 'args': args}
        elif binding.get('client_ref') or binding.get('intake'):
            args = {'name': row['name'], 'deal_type': 'other', 'phase': desired['phase'], 'salesforce_id': sid,
                    'reason': 'Reconcile the partner-authorized Salesforce active snapshot'}
            if date:
                args['sf_close_date_placeholder'] = date
            if binding.get('client_ref'):
                args['client'] = binding['client_ref']
            if binding.get('lane'):
                args['lane'] = binding['lane']
            create = {'verb': 'new-deal', 'args': args}
            if binding.get('intake'):
                item['intake'] = binding['intake']
        else:
            item['reason'] = 'client_binding_required'
            continue
        item.update(kind='create', create=create, changes=list(desired))
    keys = Counter(i['source_key'] for i in items)
    targets = Counter(i['prior']['id'] for i in items if i.get('prior'))
    names = Counter(norm(i['name']) for i in items if i['kind'] == 'create')
    for item in items:
        if keys[item['source_key']] > 1 or targets[(item.get('prior') or {}).get('id')] > 1 or (item['kind'] == 'create' and names[norm(item['name'])] > 1):
            item.update(kind='held', reason='duplicate_source_or_target')
    result = {'schema': 'salesforce-active-snapshot.v1', 'source_digest': fingerprint(source),
              'board_digest': fingerprint(board), 'items': items,
              'counts': {'source': len(rows), **{kind: sum(i['kind'] == kind for i in items)
                         for kind in ('create', 'update', 'unchanged', 'held')},
                         'unpark': sum(i['kind'] == 'update' and i['prior'].get('operating_state') == 'parked' for i in items)}}
    result['plan_digest'] = fingerprint(result)
    return result


def read_snapshot(call_verb):
    board = call_verb('deal-room-board', {'workspace': 'all'})
    if not isinstance(board.get('deals'), list):
        raise ValueError('board_read_failed')
    for deal in board['deals']:
        detail = call_verb('read-deal-reconciliation', {'deal': deal['id']})
        if detail.get('id') != deal['id']:
            raise ValueError('reconciliation_read_failed')
        deal.update(salesforce_id=detail.get('salesforce_id'), base_version=detail['base_version'])
    return board


def apply_plan(plan, call_verb, journal_path):
    import json
    import os
    import uuid

    if plan.get('schema') != 'salesforce-active-snapshot.v1' or plan.get('plan_digest') != fingerprint({k: v for k, v in plan.items() if k != 'plan_digest'}):
        raise ValueError('plan_changed_run_dry_run_again')
    if journal_path.exists():
        raise ValueError('attempt_already_recorded_reconcile_receipts')
    current = read_snapshot(call_verb)
    if fingerprint(current) != plan['board_digest']:
        raise ValueError('records_changed_run_dry_run_again')
    fd = os.open(journal_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as journal:
        def note(entry):
            journal.write(json.dumps(entry, sort_keys=True) + '\n')
            journal.flush()
            os.fsync(journal.fileno())

        class IntakeConflict(Exception):
            pass

        def write(verb, args):
            key = str(uuid.uuid5(uuid.NAMESPACE_URL, 'carr:salesforce-active:' + fingerprint({'verb': verb, 'args': args})))
            note({'status': 'pending', 'verb': verb, 'idempotency_key': key, 'request_digest': fingerprint(args)})
            result = call_verb(verb, {**args, 'idempotency_key': key})
            note({'status': 'accepted' if result.get('ok') is True else 'refused', 'verb': verb,
                  'idempotency_key': key, 'response': result})
            if verb == 'add-party' and result.get('needs_confirm') is True:
                raise IntakeConflict()
            if result.get('ok') is not True:
                raise ValueError('record_verb_refused_check_receipts')
            return result

        note({'status': 'started', 'plan_digest': plan['plan_digest']})
        verified = 0
        held = plan['counts']['held']
        for item in plan['items']:
            if item['kind'] == 'held':
                continue
            prior = item.get('prior')
            if item['kind'] == 'create':
                create = item['create']
                args = dict(create['args'])
                if item.get('intake'):
                    intake = item['intake']
                    party_id = intake.get('party_id')
                    if not party_id:
                        try:
                            party_id = write('add-party', intake['party'])['party_id']
                        except IntakeConflict:
                            held += 1
                            note({'status': 'held', 'source_key': item['source_key'], 'reason': 'party_identity_ambiguous'})
                            continue
                    client = write('new-client', {**intake['client'], 'party_id': party_id})
                    args['client'] = client['ref']
                if create['verb'] not in ('new-deal', 'create-national-market-deal'):
                    raise ValueError('create_verb_invalid')
                deal_id = write(create['verb'], args)['deal_id']
            else:
                deal_id = prior['id']
            row = call_verb('get-deal-room', {'deal': deal_id})
            if row.get('id') != deal_id:
                raise ValueError('deal_readback_failed')
            if prior and any(row.get(k) != prior.get(k) for k in ('phase', 'owner', 'operating_state', 'salesforce_id', 'base_version')):
                raise ValueError('deal_changed_during_apply_check_receipts')
            fields = {k: v for k, v in item['desired'].items() if k in ('phase', 'salesforce_id')
                      and (sfid(row.get(k)) != sfid(v) if k == 'salesforce_id' else row.get(k) != v)}
            if fields:
                write('update-deal', {'deal': deal_id, 'base_version': row['base_version'], 'fields': fields})
            for field in ('owner', 'operating_state'):
                value = item['desired'][field]
                if row.get(field) != value:
                    write('patch-deal-field', {'deal': deal_id, 'field': field,
                          'value': {'state': value} if field == 'operating_state' else value,
                          'base_event_id': (prior or {}).get('field_base', {}).get(field, {}).get('id'),
                          'change_reason': 'Reconcile the partner-authorized Salesforce active snapshot'})
            agent = item['desired'].get('market_agent')
            if agent and row.get('market_agent') != agent:
                write('set-market-agent', {'deal': deal_id, 'agent_name': agent,
                      'source': 'Salesforce active list: stated Opportunity owner, partner owner preserved'})
            after = call_verb('get-deal-room', {'deal': deal_id})
            if any(sfid(after.get(k)) != sfid(v) if k == 'salesforce_id' else after.get(k) != v
                   for k, v in item['desired'].items()):
                raise ValueError('final_readback_failed_check_receipts')
            verified += 1
            note({'status': 'verified', 'source_key': item['source_key'], 'deal_id': deal_id})
        result = {'verified': verified, 'held': held}
        note({'status': 'complete', **result})
        return result
