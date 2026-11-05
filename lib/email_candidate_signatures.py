"""Deterministic candidate evidence, independent of business-intent model reads."""
from lib.email_contact_candidates import VERSION, evidence_input, extract_candidates, signature_text
from lib.email_contact_review import digest, resolve_sources


def observations(mails, mailbox, source_key, copy_window):
    retained = set(mailbox.get('observed', []))
    resolved = resolve_sources(mailbox, mails, source_key, copy_window)
    result = {}
    for mail in mails:
        source = resolved.get(mail['ident'])
        if source is None:
            continue
        raw = evidence_input(mail)
        body = signature_text(mail['signature_html']) if mail.get('signature_html') else raw['body']
        extraction = extract_candidates(body, mail['sender'], source, complete=mail['full'])
        if extraction['ambiguity'] and source not in retained:
            continue
        retained.add(source)
        record = {'source_id': source, 'item_ids': mailbox['sources'][source]['aliases'][:50],
                  'raw_digest': digest(raw), 'extraction_version': VERSION, 'extraction': extraction,
                  'observed_at': mail['occurred_at']}
        result.setdefault(mail['sender'].lower(), {})[(source, record['raw_digest'])] = record
    mailbox['observed'] = sorted(retained)
    return {address: list(records.values()) for address, records in result.items()}
