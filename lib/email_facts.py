"""Adapt captured email extraction to existing review-only fact observations.

The adapter does not establish identity, source independence or field authority.
Its output may enter record-fact-observations, never bypass promote-fact.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from uuid import UUID, NAMESPACE_URL, uuid5

from lib.deal_extraction import CONTRACTS, VERSION as EXTRACTION_VERSION, digest, validate_fields

VERSION = 'email-facts.v1+' + EXTRACTION_VERSION


@dataclass(frozen=True)
class EmailFactSource:
    activity_id: UUID
    entity_ref: UUID
    source_version: str
    body: str
    complete: bool

    def __post_init__(self):
        if not isinstance(self.activity_id, UUID) or not isinstance(self.entity_ref, UUID):
            raise ValueError('source identifiers must be UUIDs')
        if not isinstance(self.source_version, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', self.source_version):
            raise ValueError('invalid source version')
        if not isinstance(self.body, str) or not self.body.strip() or len(self.body) > 500000:
            raise ValueError('email body exceeds extraction limits or is empty')
        if type(self.complete) is not bool:
            raise ValueError('source completeness must be explicit')


def adapt_email_facts(source: EmailFactSource, output: dict) -> dict:
    """Return immutable evidence and field dispositions, with no write or promotion."""
    fields = validate_fields(output, [source.body])
    observations = []
    dispositions = {'rent_schedule': 'unsupported_field'}
    content_hash = digest(source.body.encode())
    for key in CONTRACTS:
        field = fields[key]
        if field['status'] != 'observed':
            dispositions[key] = field['reason'] if source.complete else 'incomplete_source'
            continue
        quote = field['cited_text']
        reason = ('unsupported_' + field['region'] if field['region'] != 'scalar' else
                  'ungrounded' if quote not in source.body or field['value'] not in
                  re.findall(r'[0-9]+(?:\.[0-9]+)?', quote.replace(',', '')) else None)
        if reason:
            dispositions[key] = reason
            continue
        observation = {name: field[name] for name in ('fact_key', 'value', 'unit', 'expense_basis') if name in field}
        observation.update(id=key, method='text_layer', extractor_version=VERSION,
            confidence_tier='human_review', source={
                'document_ref': f'email:{source.activity_id}:{source.source_version}',
                'content_hash': content_hash, 'document_family_id': f'email:{source.activity_id}',
                'page_start': 1, 'page_end': 1, 'cited_text': quote})
        observations.append(observation)
        dispositions[key] = 'review_required' if source.complete else 'incomplete_source'
    identity = digest({'activity_id': str(source.activity_id), 'source_version': source.source_version,
        'entity_ref': str(source.entity_ref), 'content_hash': content_hash,
        'complete': source.complete, 'extractor_version': VERSION})
    return {'idempotency_key': str(uuid5(NAMESPACE_URL, 'record-email-facts|' + identity)),
        'entity_ref': str(source.entity_ref), 'observations': observations,
        'dispositions': dispositions, 'canonical': False}
