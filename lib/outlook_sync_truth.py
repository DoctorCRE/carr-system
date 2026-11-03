"""Compare explicit Osa GetMessage copies with a decoded research database.

Only aggregate counts leave this module. Redacted values never establish
identity or agreement. See spec/outlook/sync-log-truth.md for the evidence limits.
"""
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime
import gzip
from pathlib import Path
import sqlite3
from typing import TypedDict
import xml.etree.ElementTree as ET
import zlib

FIELDS = {'subject': 'Topic', 'preview': 'Preview', 'sender': 'From/Address',
          'sender_name': 'From/Name', 'sent_unix': 'SentTime'}
OUTCOMES = ('agreed', 'disagreed', 'redacted', 'missing_log', 'missing_decoded',
            'invalid_log', 'unjoined', 'ambiguous')
MAX_LOG_BYTES = 16 * 1024 * 1024


class AgreementReport(TypedDict):
    schema: str
    status: str
    files_read: int
    headers_read: int
    duplicate_headers: int
    headers_unjoined: int
    headers_ambiguous: int
    decoded_uncompared: int
    input_failures: dict[str, int]
    fields: dict[str, dict[str, int]]


class LogError(ValueError):
    pass


def _headers(path):
    try:
        with gzip.open(path, 'rb') as stream:
            raw = stream.read(MAX_LOG_BYTES + 1)
    except (OSError, EOFError, zlib.error):
        raise LogError('unreadable_gzip') from None
    if len(raw) > MAX_LOG_BYTES:
        raise LogError('oversized_log')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeError:
        raise LogError('invalid_encoding') from None
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise LogError('unsafe_xml')
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise LogError('invalid_xml') from None
    for node in root.iter():
        node.tag = node.tag.rsplit('}', 1)[-1]
    headers = []
    for header in root.iter('MessageHeader'):
        values = {}
        for field, selector in {'message_id': 'InternetMessageId', **FIELDS}.items():
            nodes = header.findall(selector)
            if len(nodes) > 1:
                raise LogError('duplicate_field')
            values[field] = nodes[0].text.strip() if nodes and nodes[0].text else None
        headers.append(values)
    if not headers:
        raise LogError('no_message_headers')
    return headers


def _redacted(value):
    return isinstance(value, str) and 'pii:' in value.casefold()


def _time(value):
    if value.isdecimal():
        ticks = int(value) - 621355968000000000
        return ticks / 10_000_000
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('timezone_required')
    return stamp.timestamp()


def agreement_report(decoded: Path, logs: list[Path]) -> AgreementReport:
    """Return counts for unique supplied headers, never message content or paths."""
    report: AgreementReport = {'schema': 'outlook-sync-truth/v1', 'status': 'incomplete',
              'files_read': 0, 'headers_read': 0, 'duplicate_headers': 0,
              'headers_unjoined': 0, 'headers_ambiguous': 0, 'decoded_uncompared': 0,
              'input_failures': {},
              'fields': {field: dict.fromkeys(OUTCOMES, 0) for field in FIELDS}}
    failures: Counter[str] = Counter()
    decoded_rows = defaultdict(list)
    try:
        with closing(sqlite3.connect(Path(decoded).resolve().as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            for row in db.execute('SELECT message_id, subject, preview, sender, sender_name, sent_unix FROM messages'):
                decoded_rows[row['message_id']].append(dict(row))
    except (OSError, sqlite3.Error):
        report['status'] = 'failed'
        report['input_failures'] = {'decoded_unreadable_or_schema': 1}
        return report
    unique_headers = {}
    for path in logs:
        try:
            headers = _headers(path)
        except LogError as exc:
            failures[str(exc)] += 1
            continue
        report['files_read'] += 1
        report['headers_read'] += len(headers)
        for header in headers:
            fingerprint = tuple(header.values())
            if fingerprint in unique_headers:
                report['duplicate_headers'] += 1
            unique_headers[fingerprint] = header
    log_ids = Counter(h['message_id'] for h in unique_headers.values()
                      if h['message_id'] and not _redacted(h['message_id']))
    compared_ids = set()
    for header in unique_headers.values():
        identity = header['message_id']
        rows = decoded_rows.get(identity, []) if identity and not _redacted(identity) else []
        join = ('ambiguous' if len(rows) > 1 or log_ids[identity] > 1 else
                'unjoined' if not rows else None)
        if join == 'ambiguous':
            report['headers_ambiguous'] += 1
        elif join == 'unjoined':
            report['headers_unjoined'] += 1
        else:
            compared_ids.add(identity)
        for field in FIELDS:
            value = header[field]
            if not value:
                outcome = 'missing_log'
            elif _redacted(value):
                outcome = 'redacted'
            elif join:
                outcome = join
            elif rows[0][field] is None or rows[0][field] == '':
                outcome = 'missing_decoded'
            else:
                try:
                    normalized = _time(value) if field == 'sent_unix' else value
                    outcome = 'agreed' if normalized == rows[0][field] else 'disagreed'
                except (ValueError, OverflowError):
                    outcome = 'invalid_log'
            report['fields'][field][outcome] += 1
    report['decoded_uncompared'] = sum(len(rows) for key, rows in decoded_rows.items()
                                       if key not in compared_ids)
    report['input_failures'] = dict(failures)
    totals: Counter[str] = Counter()
    for counts in report['fields'].values():
        totals.update(counts)
    if failures:
        report['status'] = 'failed'
    elif totals['disagreed']:
        report['status'] = 'disagreement'
    elif (totals['agreed'] and sum(totals.values()) == totals['agreed']
          and not report['decoded_uncompared']):
        report['status'] = 'complete'
    return report
