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


def resolve_sources(mailbox, mails, source_key, copy_window):
    """Resolve representations without moving frozen observations or borrowing keys.

    A Message-ID with competing sources is never proof by itself. Content proof
    can extend one compatible source, but cannot collapse known distinct keys.
    None means that the new representation must wait for unique evidence.
    """
    sources = mailbox.setdefault('sources', {})
    competing = mailbox.setdefault('competing', {})
    evidence = mailbox.setdefault('evidence', {})
    resolved = {}
    groups = {}
    for mail in mails:
        mid = (mail.get('message_id') or '').strip().strip('<>').lower()
        groups.setdefault(mid, []).append(mail)
    def content_proof(mail):
        return digest([mail['sender'].lower(), mail.get('subject'), mail['sent_unix'],
                       mail.get('body', mail['text']), mail.get('copy_key')])

    for mid, rows in groups.items():
        proofs = evidence.setdefault(mid, {})
        # Collect before resolving any row. Held rows constrain later scans even
        # when the decoder no longer returns the other competing representation.
        for mail in rows:
            proof = content_proof(mail)
            proofs.setdefault(proof, {}).setdefault(mail['ident'], sorted(mail['local_keys']))
        known_mid = any(mid and r['message_id'] == mid for r in sources.values())
        batch_keys = {tuple(m['local_keys']) for m in rows if m['local_keys']}
        keyless = {m['ident'] for m in rows if not m['local_keys']}
        batch_competes = len(batch_keys) > 1 or len(keyless) > 1
        for mail in sorted(rows, key=lambda m: (bool(m['local_keys']), m['ident'])):
            ident, keys = mail['ident'], set(mail['local_keys'])
            proof = content_proof(mail)
            candidates = {s: r for s, r in sources.items() if mid and r['message_id'] == mid
                          and (r.get('sent_unix') is None or abs(r['sent_unix'] - mail['sent_unix']) <= copy_window)}
            exact = [s for s, r in sources.items() if ident in r['aliases']]
            compatible = {s: r for s, r in candidates.items()
                          if not keys or not r['keys'] or keys == set(r['keys'])}
            assigned = {}
            for source, record in sources.items():
                for alias in record['aliases']:
                    assigned.setdefault(alias, set()).add(source)
            proven = []
            for source, record in compatible.items():
                same_content = {alias: ks for alias, ks in proofs[proof].items()
                                if (alias not in assigned or assigned[alias] == {source})
                                and (not ks or not record['keys'] or set(ks) == set(record['keys']))}
                proof_competes = (len({tuple(ks) for ks in same_content.values() if ks}) > 1
                                  or len({alias for alias, ks in same_content.items() if not ks}) > 1)
                if proof in record['proofs'] and not proof_competes:
                    proven.append(source)
            selected = None
            if len(exact) == 1:
                selected = exact[0]
            elif len(exact) > 1:
                resolved[ident] = None
                continue
            elif len(proven) == 1:
                selected = proven[0]
            elif candidates and compatible and (known_mid or not batch_competes):
                resolved[ident] = None
                continue
            if selected is None:
                selected = source_key(ident)
                sources[selected] = {'aliases': [], 'keys': [], 'proofs': [], 'message_id': mid, 'sent_unix': mail['sent_unix']}
            record = sources[selected]
            record['aliases'] = sorted(set(record['aliases']) | {ident})
            record['keys'] = sorted(set(record['keys']) | keys)
            # Proofs freeze source content. A changed body must not become proof
            # for another alias just because its store key was already known.
            if not record['proofs']:
                record['proofs'] = [proof]
            if mid:
                competing[mid] = sorted(set(competing.get(mid, [])) | {selected})
            resolved[ident] = selected
    return resolved
