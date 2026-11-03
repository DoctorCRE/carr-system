"""Plan unverified signature findings without changing contact records."""
import hashlib
import json
import uuid

from lib.email_contact_candidates import extract_candidates

KIND = 'email_signature_observation'
FIELDS = {'phone': 'phone', 'mobile': 'cell', 'email': 'email', 'title': 'title',
          'name': 'name', 'company': 'company'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def plan(mail, contact, *, owner, account, account_id):
    snapshot = contact.contact_snapshot
    source = 'outlook-signature/' + digest([owner, account.lower(), account_id]) + '/' + digest(mail['ident'])
    extracted = extract_candidates(mail.get('body', mail['text']), mail['sender'], source, complete=mail['full'])
    if extracted['ambiguity']:
        return [], extracted['ambiguity']
    requests = []
    for item in extracted['contact_facts'] + extracted['identity_observations']:
        field = FIELDS[item['field']]
        if field in snapshot.get('unread_fields', []) or field not in snapshot['values']:
            continue
        prior = snapshot['values'][field]
        if prior == item['value']:
            continue
        provenance = (snapshot['provenance'].get('company') if field == 'company'
                      else {k: snapshot['provenance'][k] for k in ('source', 'party_id', 'party_version')})
        identity = [source, snapshot['party_id'], field, snapshot['party_version'],
                    provenance, item['extraction_version']]
        requests.append({
            'idempotency_key': str(uuid.uuid5(uuid.NAMESPACE_URL, 'record-finding|' + digest(identity))), 'subject': contact.ref,
            'subject_kind': 'auto' if contact.ref.startswith('P-') else 'party',
            'kind': KIND, 'epistemic_status': 'proposed', 'source': source,
            'value': {'verification': 'unverified_signature_observation', 'verified': False,
                      'party_id': snapshot['party_id'], 'party_version': snapshot['party_version'],
                      'prior_provenance': provenance, 'field': field,
                      'prior': prior, 'proposed': item['value'], 'evidence': item['evidence'],
                      'extraction_version': item['extraction_version'],
                      'mailbox_owner': owner, 'mailbox_account': account,
                      'source_locator': {'item_id': mail['ident'], 'local_keys': mail['local_keys'],
                                         'message_id': mail.get('message_id')}},
            'proposes_correction': {'field': field, 'current': prior, 'proposed': item['value']}})
    return requests, None
