"""Deterministic retained page text shared by extraction and historical backfill."""
import hashlib

FORMAT = 'page-text.v1'
MAX_CHARACTERS = 500000
MAX_PAGES = 1000
# page-text.v1 whitespace is fixed across Python and PostgreSQL locales.
WHITESPACE = '\u0009\u000a\u000b\u000c\u000d\u001c\u001d\u001e\u001f\u0020\u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000'


def retained_pages(doc, pages):
    if not isinstance(pages, list) or not all(isinstance(p, str) for p in pages):
        raise ValueError('invalid page text')
    if len(pages) > MAX_PAGES or sum(map(len, pages)) > MAX_CHARACTERS:
        return {**{k: doc[k] for k in ('document_ref', 'entity_ref', 'content_hash')},
                'text_format_version': FORMAT, 'status': 'unavailable', 'pages': []}
    return {**{k: doc[k] for k in ('document_ref', 'entity_ref', 'content_hash')},
            'text_format_version': FORMAT,
            'status': 'produced' if any(p.strip(WHITESPACE) for p in pages) else 'no_text', 'pages': pages}


def derivative(doc, data, text_pages):
    if len(data) != doc['bytes'] or hashlib.sha256(data).hexdigest() != doc['content_hash']:
        raise ValueError('stored document hash mismatch')
    return retained_pages(doc, text_pages(data))


def backfill(*, scan, read_bytes, text_pages, record, max_documents, dry_run=False, after=None):
    if not 1 <= max_documents <= 10000:
        raise ValueError('max_documents must be 1..10000')
    counts = dict(scanned=0, missing=0, skipped=0, produced=0, no_text=0, unavailable=0)
    errors = []
    while counts['scanned'] < max_documents:
        docs = scan(after, min(100, max_documents - counts['scanned']))
        if not docs:
            break
        for doc in docs:
            counts['scanned'] += 1
            after = doc['document_ref']
            if doc['retained']:
                counts['skipped'] += 1
                continue
            counts['missing'] += 1
            if dry_run:
                continue
            try:
                if doc['mime'] != 'application/pdf' or not 1 <= doc['bytes'] <= 25 * 1024 * 1024:
                    value = retained_pages(doc, [])
                    value['status'] = 'unavailable'
                else:
                    value = derivative(doc, read_bytes(doc), text_pages)
                record(value)
                counts[value['status']] += 1
            except Exception:  # A failed document must not prevent later documents.
                errors.append({'document_ref': after, 'code': 'document_processing_failed'})
    return {**counts, 'dry_run': dry_run, 'last_document_ref': after, 'errors': errors}
