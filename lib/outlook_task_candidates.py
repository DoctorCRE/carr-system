"""Read-only validation of evidence-held outlook-task-candidate/v1 projections."""
from contextlib import closing
from dataclasses import dataclass, fields
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from uuid import UUID

SCHEMA = 'outlook-task-candidate/v1'
FIELDS = ('revision', 'title', 'due', 'completion', 'deletion', 'reminder')
KINDS = {'mail_flag', 'standalone_task', 'calendar_reminder'}

MAIL_FORMAT = 'mail-detail-060f-00c9-layout3'
OBSERVED_FIELDS = {
    'revision': (1418, 4, 'u32le'),
    'completion': (1474, 1, 'flag_byte'),
    'completion_date': (286, 8, 'dotnet_ticks_or_no_date'),
}


@dataclass(frozen=True)
class Receipt:
    candidates_seen: int
    candidates_supported: int
    candidates_held: int
    candidates_admitted: int
    reviews_pending: int
    writes_acknowledged: int
    candidates_deferred: int
    account_unknown: int
    foreign_account_excluded: int
    unreadable_candidates: int
    unknown_tag_class: int
    mail_classifiers: int
    task_classifier_hypotheses: int
    calendar_objects: int
    blocks_verified: int
    block_failures: int
    revisions_conflicted: int
    decoder_incomplete: int
    selected_account_objects: int


def _u64(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r'[1-9][0-9]{0,19}', value)) and int(value) < 2**64


def _validate(candidate: dict, account: str, generation: str) -> None:
    if not isinstance(candidate, dict) or candidate.get('schema') != SCHEMA or candidate.get('kind') not in KINDS:
        raise ValueError('candidate_schema')
    source = candidate.get('source', {})
    if not isinstance(source, dict) or source.get('account_key') != account or source.get('store_generation') != generation:
        raise ValueError('candidate_context')
    if not re.fullmatch(r'[0-9a-fA-F]{40}', source.get('decoder_revision', '')):
        raise ValueError('decoder_revision')
    if not isinstance(source.get('outlook_build'), str) or not source['outlook_build']:
        raise ValueError('outlook_build')
    identity = source.get('identity_basis')
    if identity == 'verified_local_object':
        if not _u64(source.get('object_key')) or candidate['kind'] != 'mail_flag' or source.get('format_id') != MAIL_FORMAT:
            raise ValueError('candidate_identity')
    elif identity != 'unresolved' or source.get('object_key') is not None or source.get('format_id') is not None:
        raise ValueError('candidate_identity')
    verified = identity == 'verified_local_object'
    provenance = candidate.get('provenance')
    if not isinstance(provenance, list):
        raise ValueError('candidate_provenance')
    observed = set()
    for span in provenance:
        if not isinstance(span, dict) or not all(type(span.get(k)) is int and span[k] >= 0
                                                for k in ('tag', 'class', 'object_offset', 'length')):
            raise ValueError('candidate_provenance')
        name = span.get('field')
        if not isinstance(name, str):
            raise ValueError('candidate_provenance')
        expected = OBSERVED_FIELDS.get(name)
        if expected is not None and verified:
            valid = ((span['object_offset'], span['length'], span.get('encoding')) == expected and
                     span.get('evidence_id') == 'hxstore-atlas-mail-detail')
            observed.add(name)
        else:
            valid = (not verified and name == 'classifier' and candidate['kind'] == 'standalone_task' and
                     (span['object_offset'], span['length'], span.get('encoding'), span.get('evidence_id')) ==
                     (1226, 8, 'heap_descriptor_utf16le', 'message-item-class-not-task-format-proof'))
        if not valid or (span['tag'], span['class']) != (0x060f, 0x00c9):
            raise ValueError('candidate_provenance')
    for name in FIELDS:
        field = candidate.get(name)
        if not isinstance(field, dict) or field.get('state') not in {'known', 'absent', 'unknown', 'conflict'}:
            raise ValueError('candidate_field')
        state = field['state']
        if set(field) != ({'state', 'value'} if state in {'known', 'unknown'} else {'state'}):
            raise ValueError('candidate_field')
        if state == 'unknown' and (not isinstance(field['value'], str) or not field['value']):
            raise ValueError('candidate_field')
        if state != 'unknown' and (not verified or name not in observed):
            raise ValueError('unverified_format')
        if name in {'title', 'due', 'deletion', 'reminder'} and state != 'unknown':
            raise ValueError('unverified_format')
        if state == 'known':
            value = field['value']
            if not isinstance(value, dict):
                raise ValueError('candidate_field')
            if name == 'revision' and (value.get('scheme') != 'mail_detail_u32' or
                    not isinstance(value.get('value'), str) or not value['value'].isdigit() or int(value['value']) >= 2**32):
                raise ValueError('candidate_revision')
            if name == 'completion':
                kind = value.get('kind')
                if kind == 'open':
                    if set(value) != {'kind'}:
                        raise ValueError('candidate_completion')
                elif kind == 'completed':
                    if (set(value) != {'kind', 'source_completed_on', 'source_completed_at'} or
                            value['source_completed_at'] is not None):
                        raise ValueError('candidate_completion')
                    if value['source_completed_on'] is not None:
                        from datetime import date
                        if ('completion_date' not in observed or
                                date.fromisoformat(value['source_completed_on']).isoformat() != value['source_completed_on']):
                            raise ValueError('candidate_provenance')
                else:
                    raise ValueError('candidate_completion')
    if candidate.get('decode_status') not in {'partial', 'unsupported', 'conflict'}:
        raise ValueError('candidate_status')
    reasons = candidate.get('reason_codes')
    if not isinstance(reasons, list) or 'admission_disabled' not in reasons or not all(isinstance(r, str) for r in reasons):
        raise ValueError('candidate_hold')
    if verified and candidate['decode_status'] == 'unsupported':
        raise ValueError('unverified_format')
    spans = candidate.get('unresolved_spans')
    if not isinstance(spans, list) or len(spans) != (2 if verified else 0):
        raise ValueError('candidate_raw_spans')
    for offset, span in zip((294, 302), spans):
        if (not isinstance(span, dict) or set(span) !=
                {'object_offset', 'length', 'encoding', 'raw_value', 'reason_code'} or
                type(span['object_offset']) is not int or span['object_offset'] != offset or
                type(span['length']) is not int or span['length'] != 8 or
                span['encoding'] != 'u64le_decimal' or span['reason_code'] != 'date_semantics_unverified'):
            raise ValueError('candidate_raw_spans')
        raw = span['raw_value']
        if not isinstance(raw, dict):
            raise ValueError('candidate_raw_spans')
        if raw.get('state') == 'known':
            value = raw.get('value')
            if (set(raw) != {'state', 'value'} or not isinstance(value, str) or
                    not re.fullmatch(r'0|[1-9][0-9]{0,19}', value) or int(value) >= 2**64):
                raise ValueError('candidate_raw_spans')
        elif raw != {'state': 'conflict'}:
            raise ValueError('candidate_raw_spans')
    payload = {k: v for k, v in candidate.items() if k != 'candidate_digest'}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    if candidate.get('candidate_digest') != digest:
        raise ValueError('candidate_digest')


def intake_evidence(path: Path, account: str, generation: str) -> dict[str, int]:
    if not _u64(account) or str(UUID(generation)) != generation:
        raise ValueError('evidence_context')
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            contexts = db.execute('SELECT account_key,store_generation FROM task_context').fetchmany(2)
            if contexts != [(account, generation)]:
                raise ValueError('evidence_context')
            receipts = db.execute('SELECT receipt_json FROM task_census').fetchmany(2)
            if len(receipts) != 1:
                raise ValueError('evidence_receipt')
            counts = json.loads(receipts[0][0])
            if (not isinstance(counts, dict) or set(counts) != {f.name for f in fields(Receipt)} or
                    not all(type(n) is int and n >= 0 for n in counts.values())):
                raise ValueError('evidence_receipt')
            counts = vars(Receipt(**counts))
            if any(counts.get(key) != 0 for key in ('candidates_admitted', 'candidates_supported',
                                                   'reviews_pending', 'writes_acknowledged')):
                raise ValueError('admission_disabled')
            total = 0
            for row in db.execute('SELECT candidate_json FROM task_candidates'):
                total += 1
                if total > 10000 or len(row[0]) > 32768:
                    raise ValueError('evidence_budget')
                _validate(json.loads(row[0]), account, generation)
            if counts.get('candidates_held') != total:
                raise ValueError('evidence_count')
            return counts
    except (sqlite3.Error, TypeError, KeyError, AttributeError) as error:
        raise ValueError('evidence_unreadable') from error
