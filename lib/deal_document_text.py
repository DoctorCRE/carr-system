"""Deterministic retained page text shared by extraction and historical backfill."""
import hashlib

FORMAT = 'page-text.v1'
MAX_CHARACTERS = 500000
MAX_PAGES = 1000


def retained_pages(doc, pages):
    if not isinstance(pages, list) or not all(isinstance(p, str) for p in pages):
        raise ValueError('invalid page text')
    if len(pages) > MAX_PAGES or sum(map(len, pages)) > MAX_CHARACTERS:
        return {**{k: doc[k] for k in ('document_ref', 'entity_ref', 'content_hash')},
                'text_format_version': FORMAT, 'status': 'unavailable', 'pages': []}
    return {**{k: doc[k] for k in ('document_ref', 'entity_ref', 'content_hash')},
            'text_format_version': FORMAT,
            'status': 'produced' if any(p.strip() for p in pages) else 'no_text', 'pages': pages}


def derivative(doc, data, text_pages):
    if len(data) != doc['bytes'] or hashlib.sha256(data).hexdigest() != doc['content_hash']:
        raise ValueError('stored document hash mismatch')
    return retained_pages(doc, text_pages(data))


def backfill(*, scan, read_bytes, text_pages, record, max_documents, dry_run=False, after=None):
    if not 1 <= max_documents <= 10000:
        raise ValueError('max_documents must be 1..10000')
    counts = dict(scanned=0, missing=0, skipped=0, produced=0, no_text=0, unavailable=0)
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
            if doc['mime'] != 'application/pdf' or not 1 <= doc['bytes'] <= 25 * 1024 * 1024:
                value = retained_pages(doc, [])
                value['status'] = 'unavailable'
            else:
                value = derivative(doc, read_bytes(doc), text_pages)
            record(value)
            counts[value['status']] += 1
    return {**counts, 'dry_run': dry_run, 'last_document_ref': after}
