#!/usr/bin/env python3
"""Count explicit retained intake observations without exposing private input."""
import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.outlook_intake import BACKFILL, UPCOMING, COPY_WINDOW, MAIL_STORE_COUNTERS

MAX_FILES = 1000
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_REQUESTS = 100_000
MAX_ROWS = 100_000
RUN_COUNTERS = frozenset(('emails', 'logged', 'preview_only', 'full_text_notes',
                          'mail_rekeyed', 'mail_inherited', 'mail_ids_reset', 'message_id_ambiguous'))
BACKLOG_COUNTERS = frozenset(MAIL_STORE_COUNTERS) | frozenset(('hydration_pending', 'unresolved_messages'))


def timestamp(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('offset_required')
    return stamp


@dataclass(frozen=True)
class Window:
    since: datetime
    until: datetime
    as_of: datetime

    def contains(self, value):
        return self.since <= value < self.until


class PrivateReads:
    def __init__(self):
        self.bytes = 0
        self.digests = []

    def read(self, path):
        if path.is_symlink() or not path.is_file():
            raise ValueError('input_unavailable')
        with path.open('rb') as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        self.bytes += len(raw)
        if len(raw) > MAX_FILE_BYTES or self.bytes > MAX_TOTAL_BYTES:
            raise ValueError('input_limit')
        self.digests.append(hashlib.sha256(raw).hexdigest())
        return raw.decode('utf-8')


def census(receipt_rows, state, account, window, now):
    if not window.since < window.until <= window.as_of <= now():
        raise ValueError('invalid_window')
    requests = state.get('requests', {})
    if not isinstance(requests, dict) or len(requests) > MAX_REQUESTS:
        raise ValueError('invalid_journal')
    receipts = Counter(selected=0, malformed=0, legacy=0, excluded=0)
    run = Counter()
    backlog = {}
    latest = None
    for row in receipt_rows:
        try:
            if row.get('service') != 'outlook-intake':
                receipts['excluded'] += 1
                continue
            at = timestamp(row['started_at'])
            if not window.contains(at):
                receipts['excluded'] += 1
                continue
            receipt = row['detail']
            if isinstance(receipt, str):
                receipt = json.loads(receipt)
            counts = receipt['counts']
            if not isinstance(counts, dict):
                raise ValueError('invalid_counts')
            if 'run' not in counts and 'backlog' not in counts:
                receipts['legacy'] += 1
                continue
            if set(counts) != {'run', 'backlog'} or not all(isinstance(g, dict) for g in counts.values()):
                raise ValueError('invalid_counts')
            for group in counts.values():
                if any(type(v) is not int or not 0 <= v <= 1_000_000_000 for v in group.values()):
                    raise ValueError('invalid_counts')
            receipts['selected'] += 1
            run.update({k: v for k, v in counts['run'].items() if k in RUN_COUNTERS})
            if latest is None or at > latest:
                latest = at
                backlog = {k: v for k, v in counts['backlog'].items() if k in BACKLOG_COUNTERS}
        except (AttributeError, KeyError, TypeError, ValueError):
            receipts['malformed'] += 1
    retained = Counter(dict.fromkeys(('requests', 'acknowledged_contact_effects', 'refused', 'blocked',
                                     'pending', 'missing_source_identity', 'account_unresolved',
                                     'excluded', 'malformed', 'source_identities', 'partial_source_identities'), 0))
    sources = defaultdict(set)
    account_matches = state.get('mail_ids_account') == hashlib.sha256(account.encode()).hexdigest()
    for request in requests.values():
        try:
            if request['verb'] != 'log-activity':
                retained['excluded'] += 1
                continue
            payload = json.loads(request['payload'])
            if payload.get('kind') not in ('email_in', 'email_out'):
                retained['excluded'] += 1
                continue
            if not window.contains(timestamp(payload['occurred_at'])):
                retained['excluded'] += 1
                continue
            created = request['created_at']
            if type(created) not in (int, float) or created > window.as_of.timestamp():
                retained['excluded'] += 1
                continue
            identity = payload.get('email_identity')
            if identity is not None and not isinstance(identity, dict):
                raise ValueError('invalid_identity')
            identity = identity or {}
            scoped = identity.get('mailbox_account')
            if scoped and scoped.strip().lower() != account:
                retained['excluded'] += 1
                continue
            if not scoped and not account_matches:
                retained['account_unresolved'] += 1
                continue
            if type(request['acknowledged']) is not bool:
                raise ValueError('invalid_acknowledgement')
            retained['requests'] += 1
            disposition = request.get('disposition')
            population = ('refused' if disposition == 'refused' else
                          'blocked' if disposition in ('blocked', 'calendar_unsupported') else
                          'acknowledged_contact_effects' if request['acknowledged'] else 'pending')
            retained[population] += 1
            source = identity.get('item_id')
            if isinstance(source, str) and source:
                sources[source].add(population)
            else:
                retained['missing_source_identity'] += 1
        except (KeyError, TypeError, ValueError, AttributeError):
            retained['malformed'] += 1
    retained['source_identities'] = len(sources)
    retained['partial_source_identities'] = sum(
        'acknowledged_contact_effects' in populations and len(populations) > 1
        for populations in sources.values())
    horizon = window.as_of.timestamp() - (BACKFILL + UPCOMING).total_seconds() - 2 * COPY_WINDOW
    return dict(schema='email-application-census/v1',
                window=dict(since=window.since.isoformat(), until=window.until.isoformat(), as_of=window.as_of.isoformat()),
                counts=dict(run=dict(run), backlog=backlog, receipts=dict(receipts), retained=dict(retained)),
                completeness=dict(unique_message_census='incomplete', retained_history='partial',
                                  receipt_account_scope='caller_supplied', acknowledgement_time='unavailable',
                                  rekeys_unresolved=True, retention_window_crossed=window.since.timestamp() < horizon))


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('invalid_arguments')


def main(argv=None, *, now=lambda: datetime.now(timezone.utc), reads=None):
    try:
        parser = Parser(description=__doc__)
        for name in ('receipts-dir', 'journal-dir', 'account', 'since', 'until', 'as-of'):
            parser.add_argument('--' + name, required=True)
        args = parser.parse_args(argv)
        window = Window(timestamp(args.since), timestamp(args.until), timestamp(args.as_of))
        account = args.account.strip().lower()
        if not account or not window.since < window.until <= window.as_of <= now():
            raise ValueError('invalid_scope')
        reads = reads or PrivateReads()
        state = json.loads(reads.read(Path(args.journal_dir) / 'state.json'))
        if not isinstance(state, dict):
            raise ValueError('invalid_journal')
        files = []
        for entry_count, path in enumerate(Path(args.receipts_dir).iterdir(), 1):
            if entry_count > MAX_FILES:
                raise ValueError('input_limit')
            if path.suffix in ('.json', '.jsonl'):
                files.append(path)
        def rows():
            row_count = 0
            for path in sorted(files):
                text = reads.read(path)
                parts = text.splitlines() if path.suffix == '.jsonl' else [text]
                for part in parts:
                    if not part.strip():
                        continue
                    row_count += 1
                    if row_count > MAX_ROWS:
                        raise ValueError('input_limit')
                    try:
                        row = json.loads(part)
                    except ValueError:
                        row = None
                    yield row
        report = census(rows(), state, account, window, now)
        report['inputs'] = dict(files=len(reads.digests), bytes=reads.bytes,
                                artifact_sha256=sorted(reads.digests),
                                sha256=hashlib.sha256(''.join(sorted(reads.digests)).encode()).hexdigest())
        print(json.dumps(report, sort_keys=True))
        return 0
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError, RecursionError):
        print('{"error":"census_input_invalid","schema":"email-application-census/v1"}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
