"""Read-only validation of evidence-held outlook-task-candidate/v1 projections."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from uuid import UUID

SCHEMA = 'outlook-task-candidate/v1'
FIELDS = ('revision', 'title', 'due', 'completion', 'deletion', 'reminder')
KINDS = {'mail_flag', 'standalone_task', 'calendar_reminder'}


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
        if not _u64(source.get('object_key')) or candidate['kind'] != 'mail_flag' or source.get('format_id') != 'mail-detail-060f-00c9-layout3':
            raise ValueError('candidate_identity')
    elif identity != 'unresolved' or source.get('object_key') is not None:
        raise ValueError('candidate_identity')
    for name in FIELDS:
        field = candidate.get(name)
        if not isinstance(field, dict) or field.get('state') not in {'known', 'absent', 'unknown', 'conflict'}:
            raise ValueError('candidate_field')
        state = field['state']
        if set(field) != ({'state', 'value'} if state in {'known', 'unknown'} else {'state'}):
            raise ValueError('candidate_field')
        if state == 'unknown' and (not isinstance(field['value'], str) or not field['value']):
            raise ValueError('candidate_field')
        if candidate['kind'] != 'mail_flag' and state != 'unknown':
            raise ValueError('unverified_format')
        if name in {'title', 'deletion', 'reminder'} and state != 'unknown':
            raise ValueError('unverified_format')
        if state == 'known':
            value = field['value']
            if not isinstance(value, dict):
                raise ValueError('candidate_field')
            if name == 'revision' and (value.get('scheme') != 'mail_detail_u32' or
                    not isinstance(value.get('value'), str) or not value['value'].isdigit() or int(value['value']) >= 2**32):
                raise ValueError('candidate_revision')
            if name == 'due':
                from datetime import date
                if value.get('kind') != 'date_only' or date.fromisoformat(value.get('date', '')).isoformat() != value['date']:
                    raise ValueError('candidate_due')
            if name == 'completion' and value.get('kind') not in {'open', 'completed'}:
                raise ValueError('candidate_completion')
    if candidate.get('decode_status') not in {'partial', 'unsupported', 'conflict'}:
        raise ValueError('candidate_status')
    reasons = candidate.get('reason_codes')
    if not isinstance(reasons, list) or 'admission_disabled' not in reasons or not all(isinstance(r, str) for r in reasons):
        raise ValueError('candidate_hold')
    provenance = candidate.get('provenance')
    if not isinstance(provenance, list):
        raise ValueError('candidate_provenance')
    for span in provenance:
        if not isinstance(span, dict) or not all(type(span.get(k)) is int and span[k] >= 0
                                                for k in ('tag', 'class', 'object_offset', 'length')):
            raise ValueError('candidate_provenance')
        if not all(isinstance(span.get(k), str) and span[k] for k in ('field', 'encoding', 'evidence_id')):
            raise ValueError('candidate_provenance')
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
            if not isinstance(counts, dict) or not all(type(n) is int and n >= 0 for n in counts.values()):
                raise ValueError('evidence_receipt')
            if counts.get('candidates_admitted') != 0 or counts.get('candidates_supported') != 0:
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
