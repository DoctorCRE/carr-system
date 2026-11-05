"""Deterministic candidate evidence, independent of business-intent model reads."""
from lib.email_contact_candidates import VERSION, evidence_input, extract_candidates, signature_text
from lib.email_contact_review import digest, resolve_sources


def observations(mails, mailbox, source_key, copy_window):
    retained = set(mailbox.get('observed', []))
    resolved = resolve_sources(mailbox, mails, source_key, copy_window)
    senders = mailbox.setdefault('senders', {})
    result = {}
    for mail in mails:
        source = resolved.get(mail['ident'])
        raw = evidence_input(mail)
        body = signature_text(mail['signature_html']) if mail.get('signature_html') else raw['body']
        if source is None:
            mid = (mail.get('message_id') or '').strip().strip('<>').lower()
            affected = [key for key, value in mailbox['sources'].items()
                        if mid and value['message_id'] == mid and senders.get(key) == mail['sender'].lower()]
        else:
            affected = [source]
        for selected in affected:
            extraction = extract_candidates(body, mail['sender'], selected, complete=mail['full'])
            if source is None:
                extraction = {'version': VERSION, 'ambiguity': 'competing_source_representation',
                              'contact_facts': [], 'identity_observations': []}
            has_signature = isinstance(body, str) and any(line.strip() == '--' for line in body.splitlines())
            if extraction['ambiguity'] and selected not in retained and not has_signature:
                continue
            retained.add(selected)
            senders[selected] = mail['sender'].lower()
            record = {'source_id': selected, 'item_ids': mailbox['sources'][selected]['aliases'][:50],
                      'raw_digest': digest(raw), 'extraction_version': VERSION, 'extraction': extraction,
                      'observed_at': mail['occurred_at']}
            result.setdefault(mail['sender'].lower(), {})[(selected, record['raw_digest'])] = record
    mailbox['observed'] = sorted(retained)
    return {address: list(records.values()) for address, records in result.items()}
