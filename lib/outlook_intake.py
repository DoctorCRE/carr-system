"""Outlook intake: local mail and calendar into the record layer, by code.

Every 30 minutes from 07:00 to 19:00 local (com.carr.outlook-intake) this reads
a private copy of the configured profile's HxStore.hxd and its EFMData body sidecars,
never the live files, decodes it with the vendored hxprobe decoder
(vendor/hxprobe), and writes what it finds
through the named record-layer verbs:

- an email to or from a known address becomes log-activity email_in/email_out
  with the full deduplicated plain-text body as its detail;
- calendar rows remain provisional schedule candidates in a private evidence index;
  unsupported lifecycle rows never establish a reminder or completed contact;
- the next fourteen days of meetings that name the configured account are published
  as one display snapshot (publish-board-snapshot, board "calendar-agenda") that the
  morning brief shows; it is replaced on every decode, carries its known gaps as
  warnings, and a failed publish never fails mail;
- next steps are proposed by the email router for partner review on Home.

A run outside 07:00-19:30 local does nothing. Matching requires one record with
that exact address; every address written in a record's email field counts, and
names alone never establish identity. Every live party address counts, in any market
(email-match-contacts); a participant of an open deal holding no client, lead or vendor
role (a partner-confirmed email sender, for one) logs on that deal itself; any other party
holding no such role is known but has no record to log on, so it is unsupported, never missing. Unmatched participants in mail and calendar
stay unresolved and are retried when mappings change, even outside the rolling
lookback. Records are never created, merged or renamed.

A role address (front desk, info, office...) that shares a record's email field
with other addresses is flagged shared_role, never matched: it may serve many people.
Unresolved mail and calendar candidates expire after 30 days (unresolved_expired,
calendar_unresolved_expired), except sent mail whose recipients are unknown: it is held
and counted (unresolved_recipients_unknown) until it decodes with recipients. Mail
whose only unknown participants are machine senders (no-reply, mailer-daemon,
postmaster, bounces) is settled once as not business, outside the unresolved backlog.
Any other unknown inbound sender's email
gets one model read (lib/unresolved_email.py) proposing what it applies to: an open
deal, a known contact at a new address, a new matter, or not business. Business queues
the address once on the claim card through propose-email-lead with that reasoning, where
a partner confirms or declines it. The email stays unresolved and logs on the record
once the address is on one.
Complete structured delivery-status reports are intercepted before the machine-sender skip.
An exact original Message-ID in the selected mailbox and a complete filing sweep with
one confirmed deal for every source activity permit an
unverified delivery-failure finding. Sent receipts remain unchanged. Unmatched reports
retry at most 32 times within 30 days, then remain unresolved until retention expires.
Sender labels alone establish no failure.
Every write carries uuid5(NAMESPACE_URL, "<verb>|<ref>|<source id>"), so an
action stays pending in the state file until the verb acknowledges it and the
retry replays the same key. A run with any write, model or cleanup failure
reports failed and does not advance its watermark.

Follow-up flags (flag_status, flag_due_unix, flag_completed_unix) become to-dos only on emails
already filed to a deal (the server-confirmed filing email-router-queue reports): flagged files a
"Follow up flagged email" deal item with the flag's due day, completed completes it, and un-flagging
drops it with the source recorded. Due days are the UTC calendar day of the decoder's midnight-UTC
stamp, never local time. An unknown or disagreeing flag is no change; one with no deal is counted
(flags_unmatched, flags_unfiled), not filed. A due-date change after filing is not synced.

Each decoded email carries its observed folder paths (Inbox, Sent Items, user
subfolders such as Active Deals/<deal>) as `folders`; matched client attachments use them to resolve one deal.
Attachment metadata travels as `attachments` with id, name, size, content_type and
nullable content_id. Cache paths and bytes stay in the private decoder output.
Attachment routing failures have client, folder and deal counters, counted per
unfinished attachment. attachments_uncached means a valid cache path has no file;
attachments_path_conflict means decoded revisions disagree on that path. Conflicts,
refused paths and read failures count as attachments_unresolved and stay pending.
Mail enters intake through the configured folder account, including all user
subfolders. A typed current-folder reference in the verified message account also
proves admission when that folder is not catalogued. Decoded recipients provide
context, never admission evidence.

An email whose body is still only Outlook's preview is logged with the preview
and flagged preview_only. A later full body is logged as a note.

The private copy and decoded database live in a 0700 work folder under
out/outlook-intake/ and are deleted before the run ends; a folder that cannot be
deleted fails the run. The report and state are 0600 there too; nothing with a
name, address or body reaches the status line, the launchd log or the run row.
"""
from __future__ import annotations

from contextlib import contextmanager

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
from typing import Callable
import uuid

from lib.calendar_agenda import (agenda, AGENDA_SCHEMA, AGENDA_DAYS, AGENDA_ATTENDEES,
                                 AGENDA_MAX_BYTES)
from lib import email_touch_journal
from lib import email_contact_review
from lib.email_contact_candidates import VERSION as SIGNATURE_POLICY
from lib import email_delivery_failure, email_links, unresolved_email
from lib.email_outcomes import business_day_deadline
from lib.outlook_settings import load_settings, OutlookSettings
from lib.outlook_runtime import stamp
OUT = Path('out/outlook-intake')
STATE = 'state.json'
WORK_PREFIX = 'work-'
WINDOW = (time(7, 0), time(19, 30))
COVERAGE_HELD_RECIPIENTS_LIMIT = 20  # More held Sent items require human attention.
COVERAGE_CHECK_AT = time(18, 0)  # the first successful run from here judges the day's coverage once
WRITE_VERBS = frozenset({'capture-email-touch-journal', 'record-email-touch-journal-outcome', 'log-activity', 'publish-board-snapshot', 'propose-email-lead', 'attach-deal-file',
                         'record-deal-item', 'complete-deal-item', 'drop-action', 'record-finding'})
FLAG_ITEM_TITLE = 'Follow up flagged email'
RECORD_REF = re.compile(r'^[CLV]-')
DEAL_REF = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
EXIT = {'succeeded': 0, 'skipped': 78, 'failed': 1}

BACKFILL = timedelta(hours=48)
LOOKBACK = timedelta(hours=6)
UPCOMING = timedelta(days=7)
REFRESH_DAYS = timedelta(days=7)
UNRESOLVED_DAYS = timedelta(days=30)
DELIVERY_RETRY_LIMIT = 32
REFRESH_INTERVAL = timedelta(minutes=30)
COPY_WINDOW = 86_400
# Fractions and percentage-point drops for decoder health.
DECODER_HEALTH_FLOORS = {'block_verification': 0.95, 'sender': 0.5,
                         'subject': 0.5, 'recipients': 0.5, 'sent_time': 0.5, 'folder': 0.0,
                         'attachment_metadata': 0.0}
DECODER_HEALTH_DROP = 0.20
DECODER_HEALTH_MIN_SAMPLE = 20
AGENDA_BOARD = 'calendar-agenda'
CALENDAR_POLICY = 4


def resolve_store(home: Path) -> Path:
    """Use only the explicitly configured profile."""
    store = load_settings(home).profile / 'HxStore.hxd'
    if not store.is_file():
        raise FileNotFoundError(f'No Outlook store found: {store}')
    return store


CLOSED_ELSEWHERE = frozenset({'action_not_open', 'deal_item_not_open'})
ERROR_CODES = CLOSED_ELSEWHERE | frozenset({'timeout', 'auth_refused', 'key_reuse', 'transport_oversized',
                         'no_acknowledgement', 'reply_unreadable', 'transport_error',
                         'invalid_request', 'subject_unresolved', 'database_refused',
                         'setup_failed', 'contacts_empty', 'contacts_read_failed',
                         'agenda_version_conflict', 'version_conflict', 'email_delivery_filing_changed'})
BLOCKED_ERRORS = frozenset({'key_reuse', 'transport_oversized', 'invalid_request', 'subject_unresolved'})


def _reply_error(reply: dict) -> str:
    error = reply.get('error')
    if error == 'board_version_conflict':
        return 'agenda_version_conflict'
    if error in ('key_reuse', 'version_conflict', 'email_delivery_filing_changed', *CLOSED_ELSEWHERE):
        return error
    if error in ('unauthorized', 'forbidden'):
        return 'auth_refused'
    if error in ('missing_required', 'invalid_field_value', 'missing_idempotency_key', 'unknown_kind',
                 'email_lead_input_invalid', 'email_lead_deal_invalid', 'email_lead_party_invalid'):
        return 'invalid_request'
    if error in ('subject_not_found', 'needs_disambiguation', 'ref_not_found', 'not_found'):
        return 'subject_unresolved'
    if error == 'database_refused_the_statement':
        return 'database_refused'
    return 'no_acknowledgement'


class CarrError(RuntimeError):
    """A safe diagnostic code; never retain request content or raw process errors."""

    def __init__(self, *, code: str = 'transport_error', tool_refusal: str | None = None):
        self.code = code if code in ERROR_CODES else 'transport_error'
        self.tool_refusal = tool_refusal
        super().__init__(self.code)


class DecodeError(RuntimeError):
    """hxprobe could not decode the store copy."""


class FullDiskAccessRequired(RuntimeError):
    """macOS denied the dedicated snapshot helper access to Outlook."""

    def __init__(self, message: str, detail: str = ''):
        super().__init__(message)
        self.detail = detail


class CleanupError(RuntimeError):
    """A private work folder could not be deleted."""


@dataclass(frozen=True)
class Contact:
    email: str
    ref: str
    name: str
    exact: bool = True
    contact_snapshot: dict | None = None


@dataclass
class Ports:
    carr: Callable[[str, dict], dict]
    contacts: Callable[[], list]
    decode: Callable[[Path, Path, Path], None]
    now: Callable[[], datetime]
    read_unresolved: Callable[[dict, date, list, list], dict] | None = None
    put_object: Callable[[str, str, bytes], dict] | None = None
    copy_attachments: Callable[[Path, list[str]], None] | None = None
    journal_identity: Callable[[], dict] | None = None


@dataclass
class Outcome:
    state: str
    reason: str
    counts: dict = field(default_factory=dict)
    detail: str = ''


FOLDER_CLASSES = {
    'sent': ('sent', 'sent items', 'sent mail'), 'inbox': ('inbox',), 'archive': ('archive',),
    'deleted': ('deleted items', 'deleted', 'trash'), 'junk': ('junk email', 'junk', 'spam'),
    'drafts': ('drafts',), 'user': (), 'unknown': (),
}
MAIL_STORE_COUNTERS = ('decoder_rejected', 'decoder_details_rejected', 'mail_outside_selected_account', 'mail_account_ambiguous', 'mail_account_unresolved',
                       'stored', 'admitted', 'stored_multi_class', 'mail_account_unknown_not_stored', 'body_complete',
                       'sender_decoded', 'recipients_decoded',
                       'stored_not_admitted_account_ambiguous',
                       'stored_not_admitted_account_unresolved',
                       *(prefix + kind for prefix in ('stored_', 'admitted_') for kind in FOLDER_CLASSES),
                       'subject_decoded', 'subject_missing',
                       *('subject_missing_' + kind for kind in FOLDER_CLASSES))

# Standing totals recomputed from state or the store every run; every other count is this run's.
BACKLOG_COUNTS = frozenset((
    *MAIL_STORE_COUNTERS, 'mail_rows_reusing_prior_identity',
    'hydration_pending', 'hydration_expired', 'unresolved_messages', 'unresolved_read', 'legacy_unmatched',
    'unresolved_read_abandoned', 'unresolved_expired', 'unresolved_recipients_unknown',
    'unresolved_calendar', 'unresolved_addresses', 'unmatched',
    'requests_blocked', 'calendar_lifecycle_unsupported',
    'calendar_lifecycle_canceled', 'calendar_requests_quarantined', 'calendar_unresolved_expired',
    'calendar_follow_up_reviews', 'calendar_follow_up_trimmed', 'calendar_agenda_items'))
BACKLOG_PREFIXES = ('mapping_', 'error_')
RECEIPT_NAMES = {'calendar_lifecycle_unsupported': 'calendar_copies_in_store',
                 'calendar_lifecycle_canceled': 'calendar_canceled_copies_in_store',
                 'calendar_requests_quarantined': 'calendar_write_requests_held',
                 'calendar_unresolved_expired': 'calendar_expired_total'}
LEAD_OUTCOMES = {'proposed': 'lead_proposed', 'known_party': 'lead_known_party', 'refused': 'lead_refused'}


COVERAGE_SHORTFALLS = ('coverage_messages_shortfall', 'coverage_subjects_shortfall', 'coverage_text_shortfall',
                      'coverage_sender_shortfall', 'coverage_recipients_shortfall')


def coverage_verdict(counts: dict) -> dict:
    """WR-000587's daily check: messages not admitted (ALL-MESSAGES, including messages the decoder
    rejected), admitted without a subject, full text, sender or recipients.
    All zero is a pass."""
    return dict(zip(COVERAGE_SHORTFALLS, (
        counts['stored'] - counts['admitted'] + counts['mail_account_unknown_not_stored']
        + counts.get('decoder_rejected', 0),
        counts['admitted'] - counts['subject_decoded'],
        counts['admitted'] - counts['body_complete'],
        counts['admitted'] - counts['sender_decoded'],
        counts['admitted'] - counts['recipients_decoded'])))


def receipt_counts(counts: dict) -> dict:
    """Split run deltas from standing totals, including the current mail snapshot.

    mail_ids is the retained mailbox-scoped RFC Message-ID association count, not
    a processing delta. It stays in run to match the decoder reconciliation receipt.
    stored/admitted/subject_decoded/body_complete count decoder message rows, never
    folder links. A folder's class is its top-level folder's. The per-class totals count
    a message once in each class of the selected account; stored_multi_class counts the
    messages in more than one class (cached move links). stored_not_admitted_* explains gaps within the selected account;
    mail_account_unknown_not_stored counts rows the decoder placed in no account. The
    first successful run at or after COVERAGE_CHECK_AT each local day adds
    coverage_verdict() to the run counts.
    """
    split: dict[str, dict] = {'run': {}, 'backlog': {}}
    for name, value in counts.items():
        standing = name in BACKLOG_COUNTS or name.startswith(BACKLOG_PREFIXES)
        split['backlog' if standing else 'run'][RECEIPT_NAMES.get(name, name)] = value
    return split


def receipt_line(outcome: Outcome, repo: Path | None = None) -> str:
    """The machine receipt bin/run-scheduled.sh validates and records."""
    reason = 'full_disk_access_required' if outcome.reason.startswith('grant Full Disk Access') else outcome.reason
    receipt = {'state': outcome.state, 'reason': reason, 'counts': receipt_counts(outcome.counts),
               **stamp(repo or Path(__file__).resolve().parents[1])}
    if outcome.detail:
        receipt['detail'] = outcome.detail
    return 'outlook-intake-receipt ' + json.dumps(receipt)


def status_line(outcome: Outcome) -> str:
    counts = ' '.join(f'{k}={v}' for k, v in sorted(outcome.counts.items()))
    return f'outlook-intake {outcome.state} {outcome.reason} {counts}'.rstrip()


def key(verb: str, ref: str, source: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'{verb}|{ref}|{source}'))


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def mail_identity(copy_key: str | None, sender: str, subject: str, sent_unix: int,
                  local_keys: list[str] | None = None) -> tuple[str, str]:
    """Use decoder store-item identity when available, otherwise its content heuristic."""
    identity = ('hxstore-local\x1f' + ','.join(sorted(set(local_keys)))) if local_keys else (
        copy_key or f'{sender.lower()}\x1f{subject.lower()}')
    ck = _hash(identity)[:20]
    return ck, f'{ck}:{sent_unix}'


def _private_fd(path: Path, flags: int) -> int:
    """Open a private file and narrow it to 0600 even when it already existed wider."""
    fd = os.open(path, flags | os.O_CREAT, 0o600)
    os.fchmod(fd, 0o600)
    return fd


def _private_write(path: Path, text: str) -> None:
    temp = path.with_name(path.name + '.tmp')
    with os.fdopen(_private_fd(temp, os.O_WRONLY | os.O_TRUNC), 'w') as handle:
        handle.write(text)
    os.replace(temp, path)


def _iso(unix: int) -> str:
    return datetime.fromtimestamp(unix, timezone.utc).isoformat()


def _domain(address: str) -> str:
    return address.rsplit('@', 1)[-1].lower()


def _internal(address: str, domain: str) -> bool:
    return _domain(address) == domain


def _addresses(text: str | None) -> list[str]:
    return [a.lower() for a in re.findall(r'[\w.+\'-]+@[\w-]+(?:\.[\w-]+)+', text or '')]


def email_context(mail: dict, domain: str) -> dict:
    recipients = []
    for field, kind in (('recipients', 'to'), ('cc', 'cc')):
        for address in dict.fromkeys(_addresses(mail.get(field))):
            recipients.append({'email': address, 'name': None, 'kind': kind,
                               'internal': _internal(address, domain)})
    return {'body_kind': mail.get('body_kind') or 'unknown',
            'body_completeness': mail.get('body_completeness') or 'unknown',
            'body_outcome': mail.get('body_outcome') or 'unknown',
            'body_truncated': bool(mail.get('body_truncated')),
            'subject': mail.get('subject') or '',
            'conversation_id': mail.get('conversation_id') or None,
            'sender': {'email': mail['sender'].lower(), 'name': mail.get('sender_name'),
                       'internal': _internal(mail['sender'], domain)},
            'recipients': recipients}


AUTOMATED_LOCAL = re.compile(r'^(?:no-?reply|do-?not-?reply|donotreply|mailer-daemon|postmaster|bounces?)(?:[-.+].*)?$')
# The same rule propose-email-lead applies; anything else (X.500 paths, non-ASCII) cannot be queued.
PROPOSABLE = re.compile(r"^[a-z0-9._%+'-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+$")


def _local(address: str) -> str:
    return address.lower().rsplit('@', 1)[0].replace('_', '-')


def automated(address: str) -> bool:
    """A machine sender by its local part. Only consulted for an address no record carries."""
    return bool(AUTOMATED_LOCAL.match(_local(address)))


def proposable(address: str) -> bool:
    return bool(PROPOSABLE.match(address.strip().lower()))


def _clean_name(name: str | None, subject: str | None) -> str | None:
    """hxprobe sometimes reads the subject or an attachment name into sender_name."""
    name = (name or '').strip()
    if not name or '@' in name or name == (subject or '').strip() or re.search(r'\.\w{2,4}$', name):
        return None
    return name


def in_window(now: datetime) -> bool:
    return WINDOW[0] <= now.astimezone(load_settings().timezone).time() <= WINDOW[1]


# ---- the decoded store -------------------------------------------------------

def _folder_path_map(con: sqlite3.Connection) -> dict[int, str]:
    """Folder id -> full path ("Inbox/Active Deals/Deal A").

    A folder with an unnamed, unknown or cyclic ancestor has no path. Older decoders export no parents."""
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'folders', 'message_folders'} <= tables:
        return {}
    columns = {r[1] for r in con.execute('PRAGMA table_info(folders)')}
    parents = 'parent_status, parent_id' if 'parent_id' in columns else "'unknown', NULL"
    folders = {r[0]: (r[1], r[2], r[3]) for r in con.execute(f'SELECT id, name, {parents} FROM folders')}

    def path(folder: int) -> str | None:
        names, seen = [], set()
        while folder in folders and folder not in seen:
            seen.add(folder)
            name, status, parent = folders[folder]
            if not name:
                return None
            names.append(name)
            if status == 'top':
                return '/'.join(reversed(names))
            if status != 'folder':
                return None
            folder = parent
        return None

    return {folder: p for folder in folders if (p := path(folder)) is not None}


def _folder_paths(con: sqlite3.Connection, folder_paths: dict[int, str]) -> dict[int, list[str]]:
    """Message row id -> sorted observed folder paths. Cached revisions can still place a
    moved message in its previous folder too."""
    if not folder_paths:
        return {}
    found: dict[int, set[str]] = {}
    for message, folder in con.execute('SELECT message_id, folder_id FROM message_folders'):
        if folder in folder_paths:
            found.setdefault(message, set()).add(folder_paths[folder])
    return {message: sorted(paths) for message, paths in found.items()}


def _attachment_metadata(con: sqlite3.Connection) -> dict[int, list[dict]]:
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'attachments', 'message_keys'} <= tables:
        return {}
    columns = {r[1] for r in con.execute('PRAGMA table_info(attachments)')}
    content_id = 'a.content_id' if 'content_id' in columns else 'NULL AS content_id'
    found: dict[int, list[dict]] = {}
    for row in con.execute(
            'WITH k AS (SELECT local_key, MIN(message_id) AS message_id FROM message_keys '
            '           GROUP BY local_key HAVING COUNT(DISTINCT message_id) = 1) '
            'SELECT k.message_id, a.id, a.name, a.size, a.content_type, '
            f'{content_id} FROM attachments a JOIN k ON k.local_key = a.message_key '
            'ORDER BY k.message_id, a.id'):
        item = dict(row)
        found.setdefault(item.pop('message_id'), []).append(item)
    return found


def _message_object_fields(con: sqlite3.Connection) -> dict[int, dict]:
    """Project agreed object fields when a row names exactly one local key."""
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'message_objects', 'message_keys'} <= tables:
        return {}
    recipients: dict[int, dict[str, list[str]]] = {}
    if 'recipients' in tables:
        for key, kind, address in con.execute(
                "SELECT local_key, kind, address FROM recipients WHERE kind IN ('to', 'cc') "
                'ORDER BY local_key, kind, position'):
            recipients.setdefault(key, {}).setdefault(kind, []).append(address)
    object_columns = {r['name'] for r in con.execute('PRAGMA table_info(message_objects)')}
    sender = 'o.sender' if 'sender' in object_columns else 'NULL AS sender'
    reply_field = 'o.in_reply_to' if 'in_reply_to' in object_columns else 'NULL AS in_reply_to'
    ownership_fields = ', '.join(f'o.{name}' if name in object_columns else f'NULL AS {name}'
                                 for name in ('account_id', 'folder_id'))
    found = {}
    for row in con.execute(
            'WITH k AS (SELECT message_id, MIN(local_key) AS local_key FROM message_keys '
            'GROUP BY message_id HAVING COUNT(DISTINCT local_key) = 1) '
            f'SELECT k.message_id, o.local_key, o.subject, o.conversation_id, o.recipients_status, {sender}, {ownership_fields}, {reply_field} '
            'FROM k '
            'JOIN message_objects o ON o.local_key = k.local_key'):
        fields = {name: row[name] for name in ('subject', 'conversation_id', 'sender', 'in_reply_to')
                  if row[name] is not None and str(row[name]).strip()}
        fields.update(account_id=row['account_id'], folder_id=row['folder_id'])
        lists = recipients.get(row['local_key'])
        # No observed recipient objects does not prove an empty recipient list.
        if row['recipients_status'] == 'listed' and lists:
            fields.update(recipients=', '.join(lists.get('to', [])) or None,
                          cc=', '.join(lists.get('cc', [])) or None)
        found[row['message_id']] = fields
    return found


def _folder_kind(path: str | None) -> str:
    """A folder's class is its top-level folder's: Projects/Archive is user, Inbox/Deals is inbox."""
    top = (path or '').split('/', 1)[0].strip().lower()
    return next((kind for kind, names in FOLDER_CLASSES.items() if top in names), 'user' if top else 'unknown')


def _body_complete(mail: dict) -> bool:
    return (mail.get('body_completeness') == 'complete' and mail.get('body_kind') == 'full'
            and bool((mail.get('body') or '').strip()))


def _coverage_counts(mails: list[dict], classes: dict[int, set[str]]) -> dict:
    """Field coverage over every admitted message; a message counts once per class."""
    counts = dict.fromkeys(('subject_decoded', 'subject_missing',
                            *('subject_missing_' + kind for kind in FOLDER_CLASSES)), 0)
    counts['admitted'] = len(mails)
    counts['body_complete'] = sum(_body_complete(m) for m in mails)
    counts['sender_decoded'] = sum(bool((m['sender'] or '').strip()) for m in mails)
    counts['recipients_decoded'] = sum(bool(_addresses(m['recipients']) or _addresses(m.get('cc'))) for m in mails)
    for mail in mails:
        if (mail['subject'] or '').strip():
            counts['subject_decoded'] += 1
            continue
        counts['subject_missing'] += 1
        kinds = classes.get(mail['id']) or {_folder_kind(path) for path in mail['folders']} or {'unknown'}
        for kind in kinds:
            counts['subject_missing_' + kind] += 1
    return counts


def _load(db: Path, account_id: int | None = None, account_address: str | None = None) -> tuple[dict, list[dict], list[dict]]:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        meta = {r['key']: r['value'] for r in con.execute('SELECT key, value FROM meta')}
        folder_paths = _folder_path_map(con)
        paths = _folder_paths(con, folder_paths)
        attachments = _attachment_metadata(con)
        # Decoders built before WR-000502 export no local_keys column.
        columns = {r['name'] for r in con.execute('PRAGMA table_info(messages)')}
        local_keys = 'local_keys' if 'local_keys' in columns else 'NULL AS local_keys'
        signature_html = 'html AS signature_html' if 'html' in columns else 'NULL AS signature_html'
        context_columns = ', '.join(name if name in columns else f'NULL AS {name}'
                                    for name in ('message_id', 'conversation_id', 'cc', 'flag_status', 'flag_due_unix',
                                                 'flag_completed_unix', 'folder_links_unresolved', 'body_completeness', 'body_outcome'))
        mails = [dict(r) for r in con.execute(
            'SELECT id, sender, sender_name, recipients, subject, body, preview, body_kind, sent_unix, copy_key, '
            f'{local_keys}, {signature_html}, {context_columns} FROM messages')]
        object_fields = _message_object_fields(con)
        for m in mails:
            fields = object_fields.get(m['id'], {})
            if fields.get('sender') and fields['sender'].lower() != (m['sender'] or '').lower():
                m['sender_name'] = None
            m.update(fields)
            for field in ('body', 'preview'):
                if m[field]:
                    m[field] = email_links.unwrap_safe_links(m[field])
        meta['health_store_mails'] = list(mails)
        ownership: dict[int, set[int | None]] = {}
        folder_classes: dict[int, set[str]] = {}
        if account_id is not None:
            for message, folder, owner, name in con.execute(
                    'SELECT mf.message_id, mf.folder_id, f.account_id, f.name FROM message_folders mf '
                    'LEFT JOIN folders f ON f.id = mf.folder_id'):
                ownership.setdefault(message, set()).add(owner if name is not None else None)
                if owner == account_id:
                    folder_classes.setdefault(message, set()).add(_folder_kind(folder_paths.get(folder)))
        if account_address is not None:
            if 'folder_links_unresolved' not in columns:
                raise DecodeError('folder_ownership_evidence_required')
            if not con.execute('SELECT 1 FROM folders WHERE account_id=? LIMIT 1',
                               (account_id,)).fetchone():
                raise DecodeError('folder_account_id_not_found')
            sent_messages = {row[0] for row in con.execute(
                "SELECT mf.message_id FROM message_folders mf JOIN folders f ON f.id=mf.folder_id "
                "WHERE f.account_id=? AND lower(f.name) IN ('sent', 'sent items', 'sent mail')",
                (account_id,)) if ownership.get(row[0]) == {account_id}}
            senders = {m['sender'].strip().lower() for m in mails
                       if m['id'] in sent_messages and m['sender'] and not m['folder_links_unresolved']}
            if account_address.strip().lower() not in senders:
                raise DecodeError('folder_account_mailbox_unverified')
        rejected = int(meta.get('messages_rejected') or 0)
        meta.update(dict.fromkeys(MAIL_STORE_COUNTERS, 0))
        meta['decoder_rejected'] = rejected
        meta['decoder_details_rejected'] = int(meta.get('details_rejected') or 0)
        # Decoders built before folder export have no folders table.
        folder_accounts = (dict(con.execute('SELECT id, account_id FROM folders'))
                           if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='folders'").fetchone()
                           else {})
        admitted = []
        for m in mails:
            owners = ownership.get(m['id'], {None})
            typed_folder = (m.get('account_id') == account_id and m.get('folder_id') is not None
                            and folder_accounts.get(m['folder_id'], account_id) == account_id)
            # A current typed reference retains ownership when the folder catalogue omits the folder,
            # never when the catalogue places that folder under another account.
            reason = ('mail_outside_selected_account' if m.get('account_id') not in (None, account_id) else
                      None if typed_folder and owners <= {None, account_id} else
                      'mail_account_unresolved' if None in owners or m['folder_links_unresolved'] else
                      'mail_account_ambiguous' if len(owners) > 1 else
                      'mail_outside_selected_account' if owners != {account_id} else None)
            kinds = folder_classes.get(m['id'], set())
            if typed_folder and reason is None and not kinds:
                kinds = {'unknown'}
                folder_classes[m['id']] = kinds
            if kinds:
                meta['stored'] += 1
                meta['stored_multi_class'] += len(kinds) > 1
                for kind in kinds:
                    meta['stored_' + kind] += 1
                    if reason is None:
                        meta['admitted_' + kind] += 1
                if reason and reason != 'mail_outside_selected_account':
                    meta['stored_not_admitted_' + reason.removeprefix('mail_')] += 1
            if account_id is not None and reason:
                meta[reason] += 1
                if reason == 'mail_account_unresolved' and not kinds:
                    meta['mail_account_unknown_not_stored'] += 1
            else:
                admitted.append(m)
        mails = admitted
        for m in mails:
            m['folders'] = paths.get(m['id'], [])
        meta.update(_coverage_counts(mails, folder_classes))
        for m in mails:
            message_id = m.pop('id')
            m['row_ids'] = [message_id]
            m['attachments'] = attachments.get(message_id, [])
        meta['health_mails'] = mails
        events = [dict(r) for r in con.execute('SELECT * FROM events')]
        attendees: dict[int, list] = {}
        for r in con.execute('SELECT event_id, name, email FROM event_attendees'):
            attendees.setdefault(r['event_id'], []).append((r['name'], (r['email'] or '').lower()))
        for e in events:
            e['attendees'] = attendees.get(e['id'], [])
        return meta, mails, events
    finally:
        con.close()


def _decoder_health(meta: dict, since: int, now: datetime, baseline: dict) -> dict:
    rows = [m for m in meta['health_mails'] if m['sent_unix'] is not None
            and since <= m['sent_unix'] <= now.timestamp() + 300]
    found = meta.get('blocks_found') or 0
    rates = {'block_verification': (meta.get('blocks_verified') or 0) / found if found else None}
    store_rows = meta['health_store_mails']
    samples = {'block_verification': found}
    for name, field, sample in (('sender', 'sender', rows), ('subject', 'subject', rows),
                                ('recipients', 'recipients', store_rows), ('sent_time', 'sent_unix', store_rows),
                                ('folder', 'folders', rows)):
        samples[name] = len(sample)
        fields = ('recipients', 'cc') if name == 'recipients' else (field,)
        rates[name] = sum(any(bool(m.get(f) and (not isinstance(m[f], str) or m[f].strip()))
                              for f in fields) for m in sample) / len(sample) if sample else None
    attachments = [a for m in rows for a in m['attachments']]
    samples['attachment_metadata'] = len(attachments)
    rates['attachment_metadata'] = (sum(bool(a['name'] and a['size'] is not None) for a in attachments)
                                    / len(attachments) if attachments else None)
    eligible = {name: rate for name, rate in rates.items()
                if rate is not None and samples[name] >= DECODER_HEALTH_MIN_SAMPLE}
    failing = [name for name, rate in eligible.items() if
               rate < DECODER_HEALTH_FLOORS[name] or
               (name in baseline and rate < baseline[name] - DECODER_HEALTH_DROP)]
    if not found:
        failing.append('block_verification')
    # Rejected messages never reach the sampled rows, so this failure follows the counter alone.
    if meta.get('decoder_rejected'):
        failing.append('message_layout')
    if meta.get('decoder_details_rejected'):
        failing.append('detail_layout')
    return {'rates': rates, 'samples': samples, 'eligible': eligible, 'failing': failing, 'messages': len(rows),
            'store_messages': len(store_rows)}


def health_counts(health: dict | None) -> dict:
    """Each measured rate in basis points, so every receipt carries the decoder's health."""
    return {**{f'decoder_{name}_bp': round(rate * 10_000)
               for name, rate in ((health or {}).get('rates') or {}).items() if rate is not None},
            **{f'decoder_{name}_sample': count
               for name, count in ((health or {}).get('samples') or {}).items()}}


def _tool_detail(tool: str, code: int | None, failure: str) -> str:
    return f'{tool} exit={code} code={failure}'


def _snapshot(home: Path, work: Path) -> None:
    settings = load_settings(home)
    settings.verify_helper()
    helper = settings.copy_helper
    try:
        done = subprocess.run([str(helper)], cwd=work, capture_output=True, timeout=900)
        if done.returncode in (errno.EPERM, errno.EACCES):
            raise FullDiskAccessRequired(f'grant Full Disk Access to {helper}',
                                         _tool_detail('outlook-intake-copy', done.returncode, 'exit_nonzero'))
        if done.returncode:
            raise DecodeError(_tool_detail('outlook-intake-copy', done.returncode, 'exit_nonzero'))
    except PermissionError:
        raise FullDiskAccessRequired(f'grant Full Disk Access to {helper}',
                                     _tool_detail('outlook-intake-copy', None,
                                                  'permission_denied')) from None
    except subprocess.TimeoutExpired:
        raise DecodeError(_tool_detail('outlook-intake-copy', None, 'timeout')) from None
    except OSError:
        raise DecodeError(_tool_detail('outlook-intake-copy', None,
                                      'launch_failed')) from None


CENSUS_TIMEOUT = 30
CENSUS_METRICS = frozenset((
    'messages body_complete body_absent body_objects '
    'body_rejected body_inline body_sidecar sidecar_linked '
    'sidecar_missing sidecar_unreadable sidecar_decode_failed recipients_none '
    'recipients_listed recipients_conflict recipient_children recipient_objects '
    'recipient_rejected recipient_to recipient_cc recipient_bcc '
    'recipient_unmapped subject_missing subject_unset_or_conflict subject_empty '
    'unknown_folder_messages folder_uncatalogued folder_unset_or_conflict folder_account_mismatch '
    'folder_unnamed blocks_verified message_layout_rejected_all_accounts body_unlinked_all_accounts '
    'recipient_unlinked_all_accounts body_orphaned_all_accounts recipient_orphaned_all_accounts'
).split())


def _coverage_census(home: Path, out: Path, copy: Path, files: Path, account_id: int) -> None:
    request = out / 'coverage-census.request'
    try:
        info = request.lstat()
    except FileNotFoundError:
        return
    except OSError:
        print('coverage_census_failed=request_stat', file=sys.stderr)
        return
    reason = 'request_consume'
    try:
        request.unlink()
        reason = 'request_invalid'
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid() or info.st_size != 0):
            raise ValueError
        reason = 'setup'
        decoder = load_settings(home).decoder
        with tempfile.TemporaryFile(dir=copy.parent) as output:
            reason = 'launch'
            done = subprocess.run([str(decoder), 'census', '--immutable-copy',
                                   str(copy), str(files), str(account_id)], stdout=output,
                                  stderr=subprocess.DEVNULL, timeout=CENSUS_TIMEOUT)
            reason = 'exit'
            if done.returncode:
                raise ValueError
            reason = 'oversize'
            if output.tell() > 8 * 1024 * 1024:
                raise ValueError
            reason = 'json'
            output.seek(0)
            payload = json.load(output)
            reason = 'totals'
            totals = payload['totals']
        if (not isinstance(totals, dict) or not totals or not totals.keys() <= CENSUS_METRICS
                or any(type(n) is not int or not 0 <= n < 2**64 for n in totals.values())):
            raise ValueError
        reason = 'write'
        _private_write(out / 'coverage-census.json', json.dumps({'totals': totals}, sort_keys=True) + '\n')
    except subprocess.TimeoutExpired:
        print('coverage_census_failed=timeout', file=sys.stderr)
    except Exception:
        print(f'coverage_census_failed={reason}', file=sys.stderr)


def _remove(work: Path) -> None:
    shutil.rmtree(work, onerror=lambda *_: None)
    if work.exists():
        raise CleanupError('private work folder left behind')


@contextmanager
def _decode(home: Path, out: Path, decode: Callable[[Path, Path, Path], None], account_id: int, account_address: str):
    work = Path(tempfile.mkdtemp(prefix=WORK_PREFIX, dir=out))
    try:
        copy = work / 'store.hxd'
        _snapshot(home, work)
        files = work / 'Files'
        _coverage_census(home, out, copy, files, account_id)
        db = work / 'store.db'
        decode(copy, files, db)
        loaded = _load(db, account_id=account_id, account_address=account_address)
        from lib.email_local_search import build_index, published_index_path
        try:
            index = published_index_path(account_id, home=home)
            index.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(db, 0o600)
            with db.open('rb') as reader:
                revision = hashlib.file_digest(reader, 'sha256').hexdigest()
            result = build_index(db.resolve(), index, mailbox=account_address,
                                 account_id=account_id, source_revision=revision, select_account=True)
            counts = dict(email_index_published=1, email_index_failed=0,
                          email_index_rows=result['message_count'], email_index_bytes=index.stat().st_size)
        except Exception:
            counts = dict(email_index_published=0, email_index_failed=1)
        loaded[0]['email_index_receipt'] = counts
        print('email-index-receipt ' + json.dumps(counts, sort_keys=True))
        yield (*loaded, db, files)
    finally:
        _remove(work)


def _flag_of(m: dict) -> tuple:
    return m.get('flag_status'), m.get('flag_due_unix'), m.get('flag_completed_unix')


def _flag_day(unix: int | None) -> str | None:
    """A flag's due or completion day: the UTC calendar day of its midnight-UTC stamp, never local time."""
    if not isinstance(unix, int) or not 946_684_800 <= unix < 4_102_444_800:  # 2000..2100; else no date
        return None
    return datetime.fromtimestamp(unix, timezone.utc).date().isoformat()


def _parties(m: dict, domain: str) -> list[tuple[str, str | None]]:
    """The outside addresses an email is filed against: its recipients if we sent it, else its sender."""
    if _internal(m['sender'], domain):
        addresses = dict.fromkeys(_addresses(m['recipients']) + _addresses(m.get('cc')))
        return [(a, None) for a in addresses if not _internal(a, domain)]
    return [(m['sender'].lower(), _clean_name(m['sender_name'], m['subject']))]


def _same_mail(m: dict, sent: int, at: int) -> bool:
    """Whether state recorded at `sent` under this row's ck is about this row, read at `at`.

    A store key names one message whatever time a decoder reads for it (hxprobe moved send times
    to the Date slot, up to days earlier than the old anchor scan), so keyed rows match on ck alone;
    keyless rows sharing a content ck must also fall within COPY_WINDOW."""
    return bool(m['local_keys']) or abs(sent - at) <= COPY_WINDOW


def _store_keys(local_keys: list[str]) -> list[str]:
    """Real store keys, without the per-decode placeholders given to keyless incomplete rows."""
    return sorted(k for k in local_keys if not k.startswith('decoded-row-'))


def _legacy_unmatched(state: dict) -> int:
    """Held keyless entries that no Message-ID names: kept, never deleted, and counted."""
    named, keys = set(state.get('mail_ids', {}).values()), state.get('mail_keys', {})
    return sum(ident not in named and not _store_keys(keys.get(ident, []))
               for table in ('mail_unresolved', 'pending_full') for ident in state.get(table, {}))


def _best_copies(mails: list[dict], state: dict | None = None) -> list[dict]:
    """Preserve keyed decoder rows; collapse only rows without store-item identity."""
    rows = []
    for m in mails:
        m['metadata_incomplete'] = not m['sender'] or m['sent_unix'] is None
        m['local_keys'] = sorted(set((m['local_keys'] or '').split(',')) - {''})
        if m['metadata_incomplete'] and not m['local_keys']:
            m['local_keys'] = ['decoded-row-' + str(m['row_ids'][0])]
        m['ck'], m['ident'] = mail_identity(m['copy_key'], m['sender'] or '', m['subject'] or '',
                                          m['sent_unix'], m['local_keys'])
        m['signature_ident'] = m['ident']
        m['full'] = _body_complete(m)
        m['text'] = (m['body'] or '').strip() or (m['preview'] or '').strip()
        rows.append(m)
    claimed = set()
    for ident, keys in sorted((state or {}).get('mail_keys', {}).items()):
        candidates = [m for m in rows if set(m['local_keys']) & set(keys) and id(m) not in claimed]
        if candidates:
            ck = ident.rsplit(':', 1)[0]
            for prior in candidates:
                prior['mail_rows_reusing_prior_identity'] = len(keys) > 1 or len(candidates) > 1
                prior['ck'], prior['ident'] = ck, ident
                claimed.add(id(prior))
    rows.sort(key=lambda m: (m['ck'], m['sent_unix'] or 0))
    out: list[dict] = []
    for m in rows:
        if m['local_keys']:
            m['first_sent'] = m['sent_unix']
            out.append(m)
            continue
        last = out[-1] if out and not out[-1]['local_keys'] else None
        if last and last['ck'] == m['ck'] and m['sent_unix'] - last['first_sent'] <= COPY_WINDOW:
            if _flag_of(last) != _flag_of(m):  # copies that disagree on the flag leave it unknown
                m['flag_status'] = last['flag_status'] = None
            m['row_ids'] = last['row_ids'] = sorted(set(last.get('row_ids', [])) | set(m.get('row_ids', [])))
            m['folders'] = last['folders'] = sorted(set(last.get('folders', [])) | set(m.get('folders', [])))
            attachments = {a['id']: a for a in last.get('attachments', []) + m.get('attachments', [])}
            m['attachments'] = last['attachments'] = [attachments[k] for k in sorted(attachments)]
            if (m['full'], len(m['text'])) > (last['full'], len(last['text'])):
                m['first_sent'], m['sent_unix'], m['ident'] = last['first_sent'], last['sent_unix'], last['ident']
                out[-1] = m
            continue
        m['first_sent'] = m['sent_unix']
        out.append(m)
    return sorted(out, key=lambda m: m['sent_unix'] or 0)


# ---- matching ----------------------------------------------------------------

class Matcher:
    def __init__(self, contacts: list, domain: str):
        self.contact_rows: dict[str, list[Contact]] = {}
        self.by_email: dict[str, set] = {}
        self.unsupported: set[str] = set()
        self.shared: set[str] = set()
        self.people = [(c.email.lower(), c.ref, c.name) for c in contacts
                       if c.exact and '@' in c.email and RECORD_REF.match(c.ref) and not _internal(c.email, domain)]
        for c in contacts:
            self.contact_rows.setdefault(c.email.lower(), []).append(c)
            if '@' in c.email and not _internal(c.email, domain):
                if not c.exact:
                    self.shared.add(c.email.lower())
                elif RECORD_REF.match(c.ref) or DEAL_REF.match(c.ref):
                    self.by_email.setdefault(c.email.lower(), set()).add(c.ref)
                else:
                    self.unsupported.add(c.email.lower())
        for address in self.shared:  # a possibly shared inbox is never exact, on any record
            self.by_email.pop(address, None)
            self.unsupported.discard(address)

    def signature_contact(self, address: str) -> Contact | None:
        rows = self.contact_rows.get(address.lower(), [])
        if (not rows or address.lower() in self.shared
                or any(not c.exact or not c.contact_snapshot for c in rows)):
            return None
        snapshots = [c.contact_snapshot for c in rows]
        if any(snapshot != snapshots[0] for snapshot in snapshots):
            return None
        snapshot = snapshots[0]
        if (not isinstance(snapshot, dict) or not snapshot.get('party_id')
                or type(snapshot.get('party_version')) is not int
                or snapshot['party_version'] < 1
                or not isinstance(snapshot.get('values'), dict)
                or not isinstance(snapshot.get('provenance'), dict)
                or snapshot['provenance'].get('party_id') != snapshot['party_id']
                or snapshot['provenance'].get('party_version') != snapshot['party_version']
                or snapshot['provenance'].get('source') != 'party'):
            return None
        return sorted(rows, key=lambda c: c.ref)[0]

    def fingerprint(self) -> str:
        return _hash(json.dumps({'supported': {a: sorted(refs) for a, refs in self.by_email.items()},
                                 'unsupported': sorted(self.unsupported), 'shared': sorted(self.shared)},
                                sort_keys=True))

    def reason(self, address: str) -> str | None:
        refs = self.by_email.get(address.lower(), set())
        if len(refs) > 1:
            return 'ambiguous'
        if not refs:
            if address.lower() in self.unsupported:
                return 'unsupported'
            return 'shared_role' if address.lower() in self.shared else 'missing'
        return None

    def classify(self, address: str) -> str | None:
        """reason(), except an unknown machine sender is 'automated': not business, not unresolved."""
        reason = self.reason(address)
        return 'automated' if reason == 'missing' and automated(address) else reason

    def ref(self, address: str) -> str | None:
        refs = self.by_email.get(address.lower(), set())
        return next(iter(refs)) if len(refs) == 1 else None


# ---- one run -----------------------------------------------------------------

class Run:
    def __init__(self, ports: Ports, state: dict, now: datetime, out: Path, settings: OutlookSettings):
        self.settings = settings
        self.ports, self.state, self.now, self.out = ports, state, now, out
        self.today = now.astimezone(self.settings.timezone).date()
        self.counts = {k: 0 for k in ('emails', 'logged', 'preview_only',
                                      'full_text_notes', 'unmatched',
                                      'model_failed', 'hydration_expired', 'writes_failed',
                                      'writes_deferred', 'requests_blocked', 'skipped_automated',
                                      'lead_proposed', 'lead_already_queued', 'lead_known_party',
                                      'lead_not_business', 'lead_refused', 'lead_existing_deal',
                                      'lead_existing_party', 'lead_new_matter', 'reads', 'reads_deferred',
                                      'reads_abandoned', 'read_context_failed', 'flags_unfiled', 'flags_unmatched',
                                      'flags_unknown', 'flags_untracked', 'flags_reflagged', 'flags_filed',
                                      'flags_completed', 'flags_cleared', 'flags_closed_elsewhere',
                                      'mail_rekeyed', 'mail_inherited', 'message_id_ambiguous', 'mail_ids_reset',
                                      'delivery_failures_recorded', 'delivery_failures_pending',
                                      'signature_recorded', 'signature_held', 'signature_pending')}
        self.deals: dict[str, str] = {}
        self.written: list[tuple[str, str, str, str]] = []
        self.unmatched: set[tuple[str, str, str]] = set()
        self.attempted: set[str] = set()
        self.lead_candidates: list[tuple[dict, dict]] = []
        self.journal_result: bool | None = None

    def owner_key(self, verb: str, ref: str, source: str) -> str:
        legacy = key(verb, ref, source)
        if legacy in self.state.get('requests', {}):
            return legacy
        return key(verb, ref, f'{self.settings.owner}|{source}')

    def activity_key(self, ref: str, source: str) -> str:
        return self.owner_key('log-activity', ref, source)

    def write(self, verb: str, args: dict, who: str, note: str = '') -> bool:
        requests = self.state.setdefault('requests', {})
        request = requests.setdefault(args['idempotency_key'], {
            'verb': verb, 'payload': json.dumps(args), 'acknowledged': False,
            'created_at': int(self.now.timestamp())})
        _save(self.out, self.state)
        if not request['acknowledged']:
            if args['idempotency_key'] in self.attempted:
                return False
            self.attempted.add(args['idempotency_key'])
            if request.get('disposition') == 'blocked' and verb == 'log-activity' and args.get('summary') == 'Links from matched email':
                request.update(acknowledged=True, disposition='refused', retry_at=None)
                _save(self.out, self.state)
                return True
            if request.get('disposition') == 'blocked':
                self.counts['requests_blocked'] += 1
                return False
            if (request.get('retry_at') or 0) > self.now.timestamp():
                self.counts['writes_deferred'] += 1
                return False
            request['attempts'] = request.get('attempts', 0) + 1
            _save(self.out, self.state)
            try:
                reply = self.ports.carr(request['verb'], json.loads(request['payload']))
                if not isinstance(reply, dict) or reply.get('ok') is not True:
                    raise CarrError(code=_reply_error(reply) if isinstance(reply, dict) else 'reply_unreadable')
            except CarrError as error:
                if (verb == 'propose-email-lead' or (verb == 'log-activity' and args.get('summary') == 'Links from matched email')) and error.code in BLOCKED_ERRORS:
                    request.update(acknowledged=True, disposition='refused', outcome='refused',
                                   retry_at=None, error_code=error.code)
                    _save(self.out, self.state)
                    self.written.append((who, args.get('ref', 'claim card'), verb, 'refused'))
                    return True
                if verb in ('complete-deal-item', 'drop-action') and error.code in CLOSED_ELSEWHERE:
                    request.update(acknowledged=True, disposition='closed_elsewhere', retry_at=None,
                                   error_code=None)
                    self.counts['flags_closed_elsewhere'] += 1
                    _save(self.out, self.state)
                    return True
                blocked = (args.get('email_touch') is not None and error.tool_refusal is not None) or error.code in BLOCKED_ERRORS or (
                    verb == 'record-finding' and args.get('kind') == email_contact_review.KIND
                    and error.code in {'auth_refused', 'version_conflict'}) or (
                    verb in ('complete-deal-item', 'drop-action') and error.code == 'version_conflict')
                request.update(error_code=error.code, disposition='blocked' if blocked else 'retry',
                               retry_at=None if blocked else int(self.now.timestamp()) +
                               min(1800, 60 * 2 ** min(request['attempts'] - 1, 5)))
                self.counts['writes_failed'] += 1
                _save(self.out, self.state)
                return False
            if verb == 'record-deal-item':
                request['item_id'] = (reply.get('item') or {}).get('id')
            if verb == 'propose-email-lead':
                request['outcome'] = 'proposed' if reply.get('proposed') is True else str(reply.get('reason'))
            if verb == 'log-activity':
                request['activity_id'] = reply.get('activity_id')
                if args.get('kind') == 'email_in':
                    self.state['last_email_in_filed_at'] = int(self.now.timestamp())
            request.update(acknowledged=True, disposition='acknowledged', retry_at=None, error_code=None)
            _save(self.out, self.state)
        self.written.append((who, args.get('ref', 'claim card'), verb, note))
        return True

    # emails

    def reconcile(self, mails: list[dict]) -> None:
        """Carry mail state across decoder identity changes by RFC Message-ID, when unambiguous.

        Decoders name a message differently (store key, or content hash for keyless rows) but agree
        on its Message-ID. `mail_ids` saves, per verified mailbox, the identity holding each ID's state.
        An ID reconciles only if at most one current keyed row carries it and every saved or current
        identity it names agrees on at most one store key. Distinct current keyless rows also
        make the ID ambiguous. Ambiguity increments `message_id_ambiguous` and each key stays
        its own message. When it reconciles, pending and
        unresolved entries keep their identity (their record-layer writes are keyed by it) and take
        the row's ck and time, and mail already done is done under the row's ck. Either direction of
        decoder change gives the same result. Signature sources reconcile independently
        through their immutable canonical observation and mailbox-scoped alias sets.
        """
        account = _hash(self.settings.account.strip().lower())
        if self.state.get('mail_ids_account') != account:
            self.counts['mail_ids_reset'] += bool(self.state.get('mail_ids'))
            self.state.update(mail_ids={}, mail_ids_account=account)
        done, ids, keys = self.state.setdefault('mail_done', {}), self.state.setdefault('mail_ids', {}), self.state.setdefault('mail_keys', {})
        held = (self.state.setdefault('mail_unresolved', {}), self.state.setdefault('pending_full', {}))
        groups: dict[str, list[dict]] = {}
        for m in mails:
            if mid := (m.get('message_id') or '').strip().strip('<>').lower():
                groups.setdefault(mid, []).append(m)
        for mid, rows in groups.items():
            target = next((m for m in rows if _store_keys(m['local_keys'])), rows[0])
            priors = ({ids[mid]} if mid in ids else set()) | {m['ident'] for m in rows}
            priors.discard(target['ident'])
            store_keys = ({tuple(_store_keys(m['local_keys'])) for m in rows}
                          | {tuple(_store_keys(keys.get(p, []))) for p in priors | {target['ident']}}) - {()}
            if (len(store_keys) > 1
                    or len({m['ident'] for m in rows if not _store_keys(m['local_keys'])}) > 1):
                self.counts['message_id_ambiguous'] += 1
                continue
            holder = target['ident']
            for prior in priors:
                ck, _, sent = prior.rpartition(':')
                for table in held:
                    if prior in table:
                        holder = prior
                        if table[prior]['ck'] != target['ck']:
                            self.counts['mail_inherited'] += 1
                            table[prior].update(ck=target['ck'], sent=target['sent_unix'])
                if (ck != target['ck'] and sent != 'None' and target['sent_unix'] not in done.get(target['ck'], [])
                        and any(abs(t - int(sent)) <= COPY_WINDOW for t in done.get(ck, []))):
                    done.setdefault(target['ck'], []).append(target['sent_unix'])
                    self.counts['mail_rekeyed'] += 1
            ids[mid] = holder

    def signature_source(self, ident: str) -> str:
        return email_contact_review.digest([self.settings.owner, self.settings.account.lower(),
                                            self.settings.folder_account_id, ident])

    def signature_findings(self, mails: list[dict], matcher: Matcher) -> None:
        observations = self.state.setdefault('signature_observations', {})
        mailbox = self.state.setdefault('signature_sources', {}).setdefault(self.signature_source('mailbox'), {})
        sources = mailbox.setdefault('sources', {})
        for source, observation in observations.items():
            if source in sources or not observation['requests']:
                continue
            locator = observation['requests'][0]['value']['source_locator']
            if source != self.signature_source(observation.get('ident', locator['item_id'])):
                continue
            mid = (locator.get('message_id') or '').strip().strip('<>').lower()
            sources[source] = {'aliases': sorted({observation.get('ident', locator['item_id']), locator['item_id']}),
                               'keys': observation.get('binding_keys', locator['local_keys']),
                               'proofs': [], 'message_id': mid}
            competing = mailbox.setdefault('competing', {})
            competing[mid] = sorted(set(competing.get(mid, [])) | {source})
        # Activity retries may rename decoded rows. Signature evidence uses the
        # decoder representation itself and never the activity holder's keys.
        representations = []
        for mail in mails:
            if (mail['metadata_incomplete'] or not mail['full']
                    or _internal(mail['sender'], self.settings.internal_domain)
                    or mail['sent_unix'] > self.now.timestamp() + 300):
                continue
            ident = mail.get('signature_ident', mail['ident'])
            representations.append({**mail, 'ident': ident})
        resolved = email_contact_review.resolve_sources(mailbox, representations, self.signature_source, COPY_WINDOW)
        mids = {(m.get('message_id') or '').strip().strip('<>').lower() for m in representations}
        self.counts['message_id_ambiguous'] = max(self.counts['message_id_ambiguous'],
            sum(len(mailbox.get('competing', {}).get(mid, [])) > 1 for mid in mids if mid))
        self.state.pop('signature_ids', None)
        _save(self.out, self.state)
        for mail in representations:
            contact = matcher.signature_contact(mail['sender'])
            if contact is None:
                self.counts['signature_held'] += 1
                continue
            snapshot = contact.contact_snapshot
            assert snapshot is not None
            ident = mail['ident']
            source = resolved.get(ident)
            if source is None:
                self.counts['signature_held'] += 1
                continue
            observation = observations.get(source)
            binding_keys = observation.get('binding_keys', mail['local_keys']) if observation else mail['local_keys']
            binding = email_contact_review.digest([mail['sender'].lower(), email_contact_review.evidence_input(mail),
                                                    snapshot['party_id'], contact.ref,
                                                    binding_keys, mail.get('message_id'),
                                                    (snapshot['provenance'].get('company') or {}).get('party_id')])
            if observation is None:
                if mail['sent_unix'] < (self.now - BACKFILL).timestamp():
                    continue
                requests, reason = email_contact_review.plan(
                    mail, contact, owner=self.settings.owner, account=self.settings.account,
                    account_id=self.settings.folder_account_id)
                if reason:
                    self.counts['signature_held'] += 1
                    continue
                observation = observations.setdefault(source, {'ident': ident, 'binding_keys': binding_keys,
                                                              'binding': binding, 'requests': requests})
                _save(self.out, self.state)
            observation.setdefault('ident', ident)
            if binding != observation['binding']:
                self.counts['signature_held'] += 1
                continue
            for args in observation['requests']:
                request = self.state.get('requests', {}).get(args['idempotency_key'], {})
                if request.get('acknowledged'):
                    continue
                if request and request['created_at'] < (self.now - timedelta(days=29)).timestamp():
                    request.update(disposition='blocked', retry_at=None)
                    _save(self.out, self.state)
                if self.write('record-finding', args, 'signature observation'):
                    self.counts['signature_recorded'] += 1
                else:
                    self.counts['signature_pending'] += 1

    def delivery_mailbox(self) -> dict:
        account = _hash(f'{self.settings.owner}|{self.settings.account.lower()}|{self.settings.folder_account_id}')
        return self.state.setdefault('delivery_mailboxes', {}).setdefault(account, {'sources': {}, 'reports': {}})

    def observe_delivery_failures(self, mails: list[dict]) -> set[str]:
        mailbox = self.delivery_mailbox()
        handled = set()
        for m in mails:
            if m['metadata_incomplete'] or m['sent_unix'] < (self.now - UNRESOLVED_DAYS).timestamp():
                continue
            mid = email_delivery_failure.message_id(m.get('message_id'))
            if mid and m['sender'].lower() == self.settings.account.lower():
                sources = mailbox['sources'].setdefault(mid, {})
                sources.setdefault(m['ident'], {'recipient_addresses': sorted(set(_addresses(m['recipients']) +
                                                                               _addresses(m.get('cc'))))})
            if (m['sender'].lower().split('@')[0] not in ('postmaster', 'mailer-daemon')
                    or self.settings.account.lower() not in _addresses(m['recipients'])
                    or m['sent_unix'] > self.now.timestamp() + 300):
                continue
            report = email_delivery_failure.parse_report(m['text'], full=m['full'], in_reply_to=m.get('in_reply_to'))
            if report is None:
                continue
            handled.add(m['ident'])
            report_key = mid or m['ident']
            mailbox['reports'].setdefault(report_key, {
                'original_id': report.original_id, 'recipient': report.recipient,
                'status': report.status, 'diagnostic': report.diagnostic,
                'report_id': report_key, 'observed_at': _iso(m['sent_unix']), 'disposition': 'retryable'})
        return handled

    def file_delivery_failures(self) -> bool:
        mailbox = self.delivery_mailbox()
        for report in mailbox['reports'].values():
            if report['disposition'] in ('recorded', 'unresolved'):
                continue
            args = report.get('request')
            if args is None:
                if (report.get('attempts', 0) >= DELIVERY_RETRY_LIMIT
                        or datetime.fromisoformat(report['observed_at']) < self.now - UNRESOLVED_DAYS):
                    report['disposition'] = 'unresolved'
                    continue
                report['attempts'] = report.get('attempts', 0) + 1
                if self.state.get('links_cursor') or not mailbox.get('filing_complete'):
                    continue
                sources = mailbox['sources'].get(report['original_id'], {})
                if len(sources) != 1:
                    continue
                ident, source = next(iter(sources.items()))
                if (ident in self.state.get('mail_unresolved', {}) or ident in self.state.get('pending_full', {})
                        or report['recipient'] not in source['recipient_addresses']):
                    continue
                activities = set()
                for request_id, request in self.state.get('requests', {}).items():
                    if request['verb'] != 'log-activity' or not request.get('acknowledged') or not request.get('activity_id'):
                        continue
                    payload = json.loads(request['payload'])
                    identity = payload.get('email_identity', {})
                    if (payload.get('kind') == 'email_out'
                            and identity.get('mailbox_account') == self.settings.account
                            and identity.get('mailbox_owner') == self.settings.owner
                            and request_id == self.activity_key(payload['ref'], ident)):
                        activities.add(request['activity_id'])
                deals = {mailbox.get('deals', {}).get(activity) for activity in activities}
                if None in deals or len(deals) != 1:
                    continue
                deal = next(iter(deals))
                args = {'idempotency_key': self.owner_key('record-finding', deal,
                        f'delivery-failure|{self.settings.account}|{self.settings.folder_account_id}|' + report['report_id']),
                        'subject': deal, 'kind': 'email_delivery_failure', 'epistemic_status': 'observed',
                        'source': 'outlook-delivery-report/' + _hash(self.settings.account.lower()) + '/' + _hash(report['report_id']),
                        'observed_at': report['observed_at'],
                        'value': {'verification': 'unverified_report', 'mailbox_account': self.settings.account,
                                  'mailbox_owner': self.settings.owner, 'original_message_id': report['original_id'],
                                  'source_activity_ids': sorted(activities),
                                  'source_activity_versions': {a: mailbox.get('versions', {}).get(a) for a in sorted(activities)},
                                  'report_id': report['report_id'],
                                  'recipient': report['recipient'], 'status': report['status'],
                                  'diagnostic': report['diagnostic'], 'parser_version': 1}}
                report['request'] = args
                _save(self.out, self.state)
            if self.write('record-finding', args, 'delivery report'):
                report['disposition'] = 'recorded'
                self.counts['delivery_failures_recorded'] += 1
            elif self.state['requests'][args['idempotency_key']].get('error_code') == 'email_delivery_filing_changed':
                del self.state['requests'][args['idempotency_key']]
                report.pop('request', None)
                mailbox['filing_complete'] = False
                _save(self.out, self.state)
        self.counts['delivery_failures_pending'] = sum(r['disposition'] == 'retryable' for r in mailbox['reports'].values())
        return not any(self.counts[k] for k in ('writes_failed', 'writes_deferred', 'requests_blocked'))

    def emails(self, mails: list[dict], matcher: Matcher, since: int) -> None:
        self.reconcile([m for m in mails if not m['metadata_incomplete']])
        self.signature_findings(mails, matcher)
        delivery_reports = self.observe_delivery_failures(mails)
        done = self.state.setdefault('mail_done', {})
        unresolved = self.state.setdefault('mail_unresolved', {})
        pending = self.state.setdefault('pending_full', {})
        keys = self.state.setdefault('mail_keys', {})
        retries: dict[str, list] = {}
        for ident, item in unresolved.items():
            retries.setdefault(item['ck'], []).append((ident, item['sent']))
        for m in sorted(mails, key=lambda m: (m['sent_unix'] or 0, m['ident'])):
            if m['ident'] in delivery_reports:
                unresolved.pop(m['ident'], None)
                continue
            if m['metadata_incomplete']:
                self.counts['emails'] += int(m['ident'] not in unresolved)
                unresolved[m['ident']] = {'ck': m['ck'], 'sent': m['sent_unix'],
                                           'metadata_incomplete': True, 'participants': {}}
                keys[m['ident']] = m['local_keys']
                continue
            if m['local_keys']:
                for ident, item in list(unresolved.items()):
                    if (ident != m['ident'] and item.get('metadata_incomplete')
                            and set(keys.get(ident, [])) == set(m['local_keys'])):
                        unresolved.pop(ident)
                        keys.pop(ident, None)
            prior = next(((ident, sent) for ident, sent in retries.get(m['ck'], [])
                          if sent is not None and _same_mail(m, sent, m['first_sent'])), None)
            if prior:
                m['ident'], m['sent_unix'] = prior
            if (m['sent_unix'] < since and m['ident'] not in unresolved) or m['sent_unix'] > self.now.timestamp() + 300:
                continue
            outbound = _internal(m['sender'], self.settings.internal_domain)
            recipients_unknown = outbound and not (_addresses(m['recipients']) or _addresses(m.get('cc')))
            if m['ident'] not in unresolved and any(_same_mail(m, t, m['sent_unix']) for t in done.get(m['ck'], [])):
                continue
            if m['local_keys']:
                keys[m['ident']] = sorted(set(keys.get(m['ident'], [])) | set(m['local_keys']))
            parties = _parties(m, self.settings.internal_domain)
            self.counts['emails'] += int(m['ident'] not in unresolved)
            if not parties:
                if recipients_unknown:
                    unresolved[m['ident']] = {'ck': m['ck'], 'sent': m['sent_unix'],
                                               'recipients_unknown': True, 'participants': {}}
                else:
                    unresolved.pop(m['ident'], None)
                    done.setdefault(m['ck'], []).append(m['sent_unix'])
                continue
            refs = []
            for address, name in parties:
                ref = matcher.ref(address)
                if ref is None:
                    reason = matcher.classify(address)
                    assert reason is not None
                    if reason != 'automated':
                        self.unmatched.add((name or '(no name)', _domain(address), reason))
                elif ref not in refs:
                    refs.append(ref)
            progress = unresolved.setdefault(m['ident'], {'ck': m['ck'], 'sent': m['sent_unix']})
            progress['sent'] = m['sent_unix']
            progress.pop('metadata_incomplete', None)
            progress.pop('recipients_unknown', None)
            progress['participants'] = {_hash(address): matcher.classify(address) for address, _ in parties
                                        if matcher.classify(address)}
            if not refs and progress['participants'] and set(progress['participants'].values()) == {'automated'}:
                unresolved.pop(m['ident'], None)
                done.setdefault(m['ck'], []).append(m['sent_unix'])
                self.counts['skipped_automated'] += 1
                continue
            completed = progress.setdefault('completed_refs', [])
            missing = any(reason != 'automated' for reason in progress['participants'].values())
            if not outbound and matcher.classify(m['sender']) == 'missing' and proposable(m['sender']):
                self.lead_candidates.append((m, progress))
            refs = [ref for ref in refs if ref not in completed]
            if not refs:
                if not missing:
                    unresolved.pop(m['ident'], None)
                    done.setdefault(m['ck'], []).append(m['sent_unix'])
                continue
            who = _clean_name(m['sender_name'], m['subject']) or m['sender'].lower()
            kind = 'email_out' if outbound else 'email_in'
            label = f'Email to {", ".join(a for a, _ in parties)}' if outbound else f'Email from {who}'
            if not m['full']:
                pending.setdefault(m['ident'], {'refs': refs, 'ck': m['ck'], 'sent': m['sent_unix'],
                                                'sender_email': m['sender'].lower(),
                                                'subject_hash': _hash(m['subject'] or '')})
                pending[m['ident']]['refs'] = sorted(set(pending[m['ident']]['refs']) | set(refs))
            proposals = [{
                'idempotency_key': self.activity_key(ref, m['ident']), 'ref': ref, 'kind': kind,
                'summary': f'{label}: {m["subject"] or "(no subject)"}', 'detail': m['text'],
                'occurred_at': _iso(m['sent_unix']),
                'email_context': email_context(m, self.settings.internal_domain),
                **({'email_identity': {'mailbox_owner': self.settings.owner, 'mailbox_account': self.settings.account,
                     'item_id': 'hxstore-key:' + ','.join(_store_keys(m['local_keys']))}}
                   if _store_keys(m['local_keys']) else {}),
                **({'email_source': {'sender_email': m['sender'].lower(), 'mailbox_owner': self.settings.owner}}
                   if not outbound else {})} for ref in refs]
            selected = self.journal_email(m, refs, proposals, missing, completed)
            if selected is not None:
                results = selected
            else:
                results = [self.write('log-activity', p, who, '' if m['full'] else 'preview_only') for p in proposals]
            completed.extend(ref for ref, ok in zip(refs, results) if ok)
            if any(results):
                self.counts['logged'] += 1
            if not all(results):
                continue
            if not missing:
                unresolved.pop(m['ident'], None)
                done.setdefault(m['ck'], []).append(m['sent_unix'])
            if not m['full']:
                self.counts['preview_only'] += 1

    def journal_recover(self) -> bool:
        item = self.state.get('email_cohort')
        candidate = self.state.get('pending_email_capture')
        if self.journal_result is not None:
            return self.journal_result
        if not candidate and not item:
            return True
        if item and item['phase'] == 'available':
            try:
                email_touch_journal.verify_receipt(item['receipt'], item['meta'], item['proposals'])
            except (ValueError, KeyError, TypeError):
                email_touch_journal.Cohort(self.state, lambda: _save(self.out, self.state)).fail('integrity', False)
                self.counts['writes_deferred'] += 1
                return False
            if not self.ports.journal_identity:
                return True
            try:
                identity = self.ports.journal_identity()
                if identity['producer_sha'] != item['meta']['producer_sha']:
                    self.state.setdefault('email_cohort_history', []).append(item)
                    self.state.pop('email_cohort')
                    _save(self.out, self.state)
                return True
            except Exception:
                email_touch_journal.Cohort(self.state, lambda: _save(self.out, self.state)).fail('release_unavailable', True)
                self.counts['writes_deferred'] += 1
                return False
        if (item and item['phase'] == 'held') or (candidate and candidate.get('failures', 0) >= 2):
            self.counts['writes_deferred'] += 1
            return False
        cohort = email_touch_journal.Cohort(self.state, lambda: _save(self.out, self.state))
        try:
            identity = self.ports.journal_identity() if self.ports.journal_identity else {}
            if candidate:
                meta = {**candidate['projection'], 'principal_slug': identity['principal_slug'],
                        'producer_sha': identity['producer_sha'], 'execution_id': candidate['execution_id']}
                cohort.select(meta, candidate['proposals'])
                self.state.pop('pending_email_capture')
                _save(self.out, self.state)
                item = self.state['email_cohort']
            assert item is not None
            if identity.get('principal_slug') != item['meta']['principal_slug']:
                return cohort.fail('principal_mismatch', False)
        except Exception:
            if item:
                cohort.fail('transport_error', True)
            elif candidate:
                candidate['failures'] = candidate.get('failures', 0) + 1
                _save(self.out, self.state)
            self.counts['writes_deferred'] += 1
            return False
        self.journal_result = False
        ok = cohort.recover(self.ports.carr, lambda p: self.write('log-activity', p, 'frozen email'), identity.get('release_sha'))
        self.journal_result = ok
        if not ok:
            self.counts['writes_deferred'] += 1
        return ok

    def journal_email(self, mail, refs, proposals, missing, completed):
        item = self.state.get('email_cohort')
        candidate = self.state.get('pending_email_capture')
        message_hash = email_touch_journal.digest(mail['ident'])
        if ((item and item['meta']['message_hash'] == message_hash)
                or (candidate and candidate['projection']['message_hash'] == message_hash)):
            self.journal_recover()
            applied = set(self.state.get('email_cohort', {}).get('applied', []))
            return [p['idempotency_key'] in applied for p in proposals]
        if item or candidate or not self.ports.journal_identity or missing or completed or not mail['full']:
            return None
        if (not _store_keys(mail['local_keys']) or self.state.get('decoder_degraded')
                or self.counts['message_id_ambiguous'] or not 1 <= len(refs) <= 500
                or any(not RECORD_REF.match(ref) for ref in refs)
                or any(p['idempotency_key'] in self.state.get('requests', {}) for p in proposals)):
            return None
        for p in proposals:
            p['occurred_at'] = email_touch_journal.occurrence(mail['sent_unix'])
        self.state['pending_email_capture'] = dict(
            projection=email_touch_journal.projection(self.settings.owner, self.settings.account, mail),
            execution_id=str(uuid.uuid4()), proposals=proposals, failures=0)
        _save(self.out, self.state)
        self.journal_recover()
        applied = set(self.state.get('email_cohort', {}).get('applied', []))
        return [p['idempotency_key'] in applied for p in proposals]

    # follow-up flags

    def observe_flags(self, mails: list[dict], matcher: Matcher) -> None:
        """Note each email's decoded flag against the activities already filed for it.

        Only flagged, complete and none are understood (n=4 labelled samples); any other value, or a
        copy disagreement, is no change. 'none' matters only for an email already tracked."""
        flags = self.state.setdefault('flags', {})
        for m in mails:
            status = m.get('flag_status')
            if status is None:
                continue
            if status not in ('flagged', 'complete', 'none'):
                self.counts['flags_unknown'] += 1
                continue
            if status == 'none' and m['ident'] not in flags:
                continue
            seen = flags.get(m['ident'])
            if seen is None:  # the activity ids are only readable while intake still remembers the logging
                activities = sorted({
                    self.state['requests'][k]['activity_id']
                    for address, _ in _parties(m, self.settings.internal_domain) if (ref := matcher.ref(address))
                    for k in (self.activity_key(ref, m['ident']),)
                    if self.state['requests'].get(k, {}).get('activity_id')})
                if not activities:
                    self.counts['flags_unfiled'] += 1
                    continue
                seen = flags.setdefault(m['ident'], {'applied': {}, 'activities': activities})
            seen.update(status=status, due=_flag_day(m.get('flag_due_unix')),
                        done=_flag_day(m.get('flag_completed_unix', 'folder_links_unresolved')),
                        subject=m['subject'] or '(no subject)', at=int(self.now.timestamp()))

    def apply_flags(self) -> bool:
        """File each observed flag on the deal its email is confirmed against. Idempotent per (email, state)."""
        flags = self.state.setdefault('flags', {})
        ok = True
        for ident, f in sorted(flags.items()):
            deals = sorted(set(f['applied']) | {self.deals[a] for a in f['activities'] if a in self.deals})
            if not deals:
                self.counts['flags_unmatched'] += 1
                continue
            for deal in deals:
                ok = self._flag_on_deal(ident, f, deal) and ok
        horizon = self.now.timestamp() - UNRESOLVED_DAYS.total_seconds()
        for ident in [i for i, f in flags.items() if f['at'] < horizon
                      and all(a['state'] != 'flagged' for a in f['applied'].values())]:
            del flags[ident]
        return ok

    def _flag_on_deal(self, ident: str, f: dict, deal: str) -> bool:
        applied = f['applied'].get(deal)
        who = 'outlook flag'
        if f['status'] == 'flagged':
            if applied:
                if applied['state'] != 'flagged':
                    self.counts['flags_reflagged'] += 1
                return True
            text = re.sub(r'(?<=[.!?])\s+(?=[A-Z])', ' / ', re.sub(r'\s*;\s*|\s+', ' ', f['subject']).strip())
            args = {'idempotency_key': self.owner_key('record-deal-item', deal, f'{ident}|flagged'), 'deal': deal,
                    'title': FLAG_ITEM_TITLE, 'due_on': f['due'], 'source_ref': f'outlook-flag:{self.settings.owner}:{ident}',
                    'bullets': [{'kind': 'promised', 'date': f['due'], 'text': f'Flagged in Outlook: {text}'[:240]}]}
            if not self.write('record-deal-item', args, who, 'flagged'):
                return False
            f['applied'][deal] = {'state': 'flagged', 'item_id': self.state['requests'][args['idempotency_key']].get('item_id')}
            self.counts['flags_filed'] += 1
        elif not applied:
            self.counts['flags_untracked'] += 1  # complete or cleared before we filed it: nothing to close
        elif applied['state'] == 'flagged' and applied.get('item_id'):
            if f['status'] == 'complete':
                args = {'idempotency_key': self.owner_key('complete-deal-item', deal, f'{ident}|complete'),
                        'deal': deal, 'item_id': applied['item_id']}
                verb, state, count = 'complete-deal-item', 'complete', 'flags_completed'
            else:
                args = {'idempotency_key': self.owner_key('drop-action', deal, f'{ident}|cleared'),
                        'action_id': applied['item_id'], 'base_version': 1,
                        'reason': f'Outlook flag cleared without completing (source: outlook-flag:{ident})'}
                verb, state, count = 'drop-action', 'cleared', 'flags_cleared'
            if not self.write(verb, args, who, state):
                return False
            applied['state'] = state
            self.counts[count] += 1
        return True

    def file_links(self) -> bool:
        """Use server-confirmed filing, including matches made after local intake finished."""
        queue = self.ports.carr('email-router-queue', {
            'purpose': 'links', 'limit': 200,
            **({'after': self.state['links_cursor']} if self.state.get('links_cursor') else {})})
        self.counts['links_filed'] = 0
        mailbox = self.delivery_mailbox()
        if not self.state.get('links_cursor'):
            mailbox['deals'] = {}
            mailbox['versions'] = {}
        mailbox['filing_complete'] = False
        source_activities = {r['activity_id'] for r in self.state.get('requests', {}).values()
                             if r.get('activity_id') and r['verb'] == 'log-activity'
                             and json.loads(r['payload']).get('kind') == 'email_out'}
        pending_sources = {self.state.get('requests', {}).get(self.activity_key(ref, ident), {}).get('activity_id')
                           for ident, pending in self.state.get('pending_full', {}).items()
                           for ref in pending['refs']}
        for mail in queue.get('emails', []):
            if mail.get('deal'):
                self.deals[mail['activity_id']] = mail['deal']
                if mail['activity_id'] in source_activities:
                    mailbox.setdefault('deals', {})[mail['activity_id']] = mail['deal']
                    mailbox.setdefault('versions', {})[mail['activity_id']] = mail.get('version')
            if not mail.get('deal') or mail['activity_id'] in pending_sources:
                continue
            links = sorted(email_links.extract_links(mail.get('body') or ''))
            if not links:
                continue
            request_key = key('log-activity', 'email-links', json.dumps([mail['activity_id'], mail['deal']]))
            acknowledged = self.state.get('requests', {}).get(request_key, {}).get('acknowledged')
            if self.write('log-activity', {
                'idempotency_key': request_key,
                'ref': mail['deal'], 'kind': 'note', 'summary': 'Links from matched email',
                'detail': '\n'.join(f'{kind}: {url}' for kind, url in links),
                'occurred_at': mail['occurred_at']}, 'matched email', 'email links') and not acknowledged and self.state['requests'][request_key].get('disposition') == 'acknowledged':
                self.counts['links_filed'] += 1
        if any(self.counts[k] for k in ('writes_failed', 'writes_deferred', 'requests_blocked')):
            return False
        self.state['links_cursor'] = queue.get('next_cursor')
        mailbox['filing_complete'] = not self.state['links_cursor']
        return True

    def prepare_attachments(self, mails: list[dict], matcher: Matcher, db: Path, files: Path) -> list[tuple[str, dict, dict, bytes]]:
        from lib import outlook_attachments as att

        prepared: list[tuple[str, dict, dict, bytes]] = []
        done = self.state.setdefault('attachments_done', {})
        self.state['attachments_pending'] = False
        uncached = self.state.setdefault('attachments_uncached', {})
        cutoff = (self.now - UNRESOLVED_DAYS).timestamp()
        expired = [source for source, sent in uncached.items() if sent < cutoff]
        if expired:
            self.counts['attachments_uncached_refused'] = len(expired)
            for source in expired:
                del uncached[source]
        candidates = []
        for mail in mails:
            if not mail.get('attachments') or not (self.now - UNRESOLVED_DAYS).timestamp() <= mail['sent_unix'] <= self.now.timestamp() + 300:
                continue
            pending = sum(f"{mail['ident']}/{a['id']}" not in done for a in mail['attachments'])
            if not pending:
                continue
            parties = (_addresses(mail['recipients']) if _internal(mail['sender'], self.settings.internal_domain)
                       else [mail['sender']])
            refs = {matcher.ref(a) for a in parties if not _internal(a, self.settings.internal_domain)} - {None}
            names = {parts[i + 1] for path in mail.get('folders', [])
                     for parts in [path.split('/')] for i, part in enumerate(parts[:-1])
                     if part.casefold() == 'active deals'}
            if not refs:
                continue
            reason = ('client_ambiguous' if len(refs) != 1 else
                      'folder_missing' if not names else 'folder_ambiguous' if len(names) != 1 else None)
            if reason:
                counter = 'attachments_' + reason
                self.counts[counter] = self.counts.get(counter, 0) + pending
                continue
            candidates.append((mail, next(iter(refs)), next(iter(names))))
        if not candidates:
            return prepared
        try:
            deals = self.ports.carr('read-deal-reconciliation', {'all': True})['deals']
            selected = {}
            for mail, ref, name in candidates:
                matches = [d for d in deals if d['client_ref'] == ref and d['name'].casefold() == name.casefold()]
                if len(matches) != 1:
                    counter = 'attachments_deal_missing' if not matches else 'attachments_deal_ambiguous'
                    self.counts[counter] = self.counts.get(counter, 0) + sum(
                        f"{mail['ident']}/{a['id']}" not in done for a in mail['attachments'])
                    continue
                for row_id in mail['row_ids']:
                    selected[row_id] = (mail, matches[0])
            pending_ids = {a['id'] for mail, _ in selected.values() for a in mail['attachments']
                           if f"{mail['ident']}/{a['id']}" not in done}
            rows = [a for a in att.rows(db) if a['message'] in selected and a['attachment'] in pending_ids]
            paths = sorted({a['path'] for a in rows if a['path'] and att.CACHE_PATH.fullmatch(a['path'])
                            and not any(ord(c) < 32 or ord(c) == 127 for c in a['path'])})
            if paths:
                if self.ports.copy_attachments:
                    self.ports.copy_attachments(files, paths)
                else:
                    copied = subprocess.run([str(self.settings.copy_helper), '--attachments'],
                        input=''.join(path + '\n' for path in paths), cwd=files.parent,
                        capture_output=True, text=True, timeout=900)
                    if copied.returncode:
                        raise OSError('attachment copy failed')
            for result in att.extract(db, files, files.parent / 'extracted', messages=set(selected), attachments=pending_ids):
                mail, deal = selected[result['message']]
                source = f"{mail['ident']}/{result['attachment']}"
                if source in done:
                    continue
                if result['status'] == 'not_cached':
                    uncached[source] = mail['sent_unix']
                    self.counts['attachments_uncached'] = self.counts.get('attachments_uncached', 0) + 1
                    continue
                uncached.pop(source, None)
                if result['status'] != 'stored':
                    self.counts['attachments_' + result['status']] = self.counts.get('attachments_' + result['status'], 0) + 1
                if result['status'] in ('type_refused', 'too_large', 'empty'):
                    done[source] = int(self.now.timestamp())
                    continue
                if result['status'] != 'stored':
                    if result['status'] in ('path_conflict', 'path_refused', 'unreadable', 'error'):
                        self.counts['attachments_unresolved'] = self.counts.get('attachments_unresolved', 0) + 1
                    self.state['attachments_pending'] = True
                    continue
                prepared.append((mail['ident'], deal, {**result, 'requires_activity': not _internal(mail['sender'], self.settings.internal_domain)},
                                 Path(result['stored']).read_bytes()))
        except (CarrError, OSError, KeyError, sqlite3.Error, subprocess.TimeoutExpired):
            self.state['attachments_pending'] = True
            self.counts['attachments_failed'] = self.counts.get('attachments_failed', 0) + 1
        return prepared

    def file_attachments(self, prepared: list[tuple[str, dict, dict, bytes]]) -> None:
        from lib import outlook_deal_files as filing

        done = self.state.setdefault('attachments_done', {})
        put = self.ports.put_object or filing.r2_put_object()
        for ident, deal, result, data in prepared:
            activity_id = None
            for ref in (deal['id'], deal['client_ref']):
                request = self.state.get('requests', {}).get(self.activity_key(ref, ident), {})
                if request.get('activity_id') and json.loads(request['payload']).get('kind') == 'email_in':
                    activity_id = request['activity_id']
                    break
            if result.get('requires_activity') and not activity_id:
                self.state['attachments_pending'] = True
                self.counts['attachments_source_pending'] = self.counts.get('attachments_source_pending', 0) + 1
                continue
            reply = filing.file_attachment(deal['id'], {'id': result['attachment'], 'name': result['name']},
                data, message_id=ident, client_ref=f"{deal['client_ref']}.{deal['id']}",
                put_object=put, carr=self.ports.carr, activity_id=activity_id)
            status = reply['status']
            self.counts['attachments_' + status] = self.counts.get('attachments_' + status, 0) + 1
            if status in ('stored', 'existing'):
                done[f"{ident}/{result['attachment']}"] = int(self.now.timestamp())
            else:
                self.state['attachments_pending'] = True
        cutoff = int((self.now - UNRESOLVED_DAYS).timestamp())
        self.state['attachments_done'] = {source: at for source, at in done.items() if at >= cutoff}
        _save(self.out, self.state)

    def leads(self, matcher: Matcher) -> None:
        """Read each unresolved email from an unknown sender once; queue business for a partner's review.

        At most unresolved_email.MAX_READS model reads per run, oldest email first; the rest wait
        and reads_due keeps the next run decoding an unchanged store. Any failed read spends one of
        the email's MAX_ATTEMPTS and is not retried before RETRY_AFTER, so an outage costs each
        email at most one attempt; the run moves past it and stops reading after
        MAX_FAILURES_IN_A_ROW. Once a sender is on the queue, that address's later email is not read
        again: the queue holds one row per address. A refused proposal does not count as queued."""
        self.state['reads_due'] = False
        if self.ports.read_unresolved is None:
            return
        queued = {p['sender'] for p in self.state.get('mail_unresolved', {}).values() if p.get('sender')}
        deals, failures = None, 0
        for m, progress in self.lead_candidates:
            address = m['sender'].lower()
            name = _clean_name(m['sender_name'], m['subject'])
            request_key = self.owner_key('propose-email-lead', _hash(address), f'read|{m["ident"]}')
            if 'read' in progress:
                self.count_lead_disposition(progress, request_key)
                continue
            if _hash(address) in queued:
                progress['read'] = 'sender_queued'
                self.counts['lead_already_queued'] += 1
                continue
            if progress.get('read_failed_at', 0) > self.now.timestamp() - unresolved_email.RETRY_AFTER.total_seconds():
                self.state['reads_due'] = True
                continue
            if self.counts['reads'] >= unresolved_email.MAX_READS:
                self.counts['reads_deferred'] += 1
                self.state['reads_due'] = True
                continue
            if deals is None:
                try:
                    deals = unresolved_email.open_deals(
                        self.ports.carr('read-deal-reconciliation', {'all': True})['deals'])
                except (CarrError, KeyError, TypeError):
                    self.counts['read_context_failed'] = 1
                    self.counts['model_failed'] += 1
                    return
            parties = unresolved_email.candidate_parties(matcher.people, address, name)
            self.counts['reads'] += 1
            try:
                read = self.ports.read_unresolved(m, self.today, deals, parties)
            except Exception:
                failures += 1
                progress['read_attempts'] = progress.get('read_attempts', 0) + 1
                progress['read_failed_at'] = int(self.now.timestamp())
                if progress['read_attempts'] >= unresolved_email.MAX_ATTEMPTS:
                    progress['read'] = 'abandoned'
                    self.counts['reads_abandoned'] += 1
                else:
                    self.counts['model_failed'] += 1
                    self.state['reads_due'] = True
                _save(self.out, self.state)
                if failures >= unresolved_email.MAX_FAILURES_IN_A_ROW:
                    return
                continue
            failures = 0
            for stale in ('intent', 'summary', 'read_attempts', 'read_failed_at'):
                progress.pop(stale, None)
            progress['read'] = read['applies_to']
            self.counts['lead_' + read['applies_to']] += 1
            _save(self.out, self.state)
            args = {'idempotency_key': request_key, 'address': address,
                    'display_name': (name or '')[:200] or None, 'summary': read['summary'] or 'Not business.',
                    'first_seen_at': _iso(m['sent_unix']), 'message_ref': m['ident'],
                    'applies_to': read['applies_to'], 'confidence': read['confidence'],
                    **{k: read[k] for k in ('deal', 'party', 'evidence_quote') if read[k] is not None}}
            if self.write('propose-email-lead', args, name or _domain(address)):
                if read['applies_to'] == 'not_business':
                    continue
                outcome = self.state['requests'][request_key].get('outcome')
                self.counts[LEAD_OUTCOMES.get(outcome, 'lead_already_queued')] += 1
                if outcome != 'refused':
                    progress['sender'] = _hash(address)
                    queued.add(progress['sender'])

    def count_lead_disposition(self, progress: dict, request_key: str) -> None:
        """A sender read on an earlier run still has a disposition; report it on every run."""
        if progress['read'] == 'sender_queued':
            self.counts['lead_already_queued'] += 1
            return
        request = self.state.get('requests', {}).get(request_key)
        if request and request['acknowledged']:
            self.counts[LEAD_OUTCOMES.get(request.get('outcome'), 'lead_already_queued')] += 1

    def full_text(self, mails: list[dict]) -> None:
        """Log the full text of emails first logged from their preview, once it decodes."""
        pending = self.state.setdefault('pending_full', {})
        full: dict[str, list[dict]] = {}
        for m in mails:
            if m['full']:
                full.setdefault(m['ck'], []).append(m)
        for ident, p in list(pending.items()):
            match = [m for m in full.get(p['ck'], []) if _same_mail(m, p['sent'], m['first_sent'])]
            if not match:
                if p['sent'] < self.now.timestamp() - REFRESH_DAYS.total_seconds():
                    p['expired'] = True
                    self.counts['hydration_expired'] += 1
                continue
            m = match[0]
            ok = all([self.write('log-activity', {
                'idempotency_key': self.activity_key(ref, f'fullbody|{ident}'), 'ref': ref, 'kind': 'note',
                'summary': f'Full text of email: {m["subject"] or "(no subject)"}', 'detail': m['text'],
                **({'email_context': {**email_context(m, self.settings.internal_domain),
                                      'source_activity_id': source}}
                   if (source := self.state.get('requests', {}).get(self.activity_key(ref, ident), {}).get('activity_id'))
                   else {}),
                'occurred_at': _iso(p['sent'])}, p['sender_email'], 'full text follow-up') for ref in p['refs']])
            if ok:
                self.counts['full_text_notes'] += 1
                del pending[ident]

    # calendar

    def events(self, events: list[dict], matcher: Matcher, since: int) -> None:
        """Keep opaque schedule evidence, never infer attendance or current lifecycle."""
        evidence = {}
        unresolved = {}
        expired = 0
        oldest = self.now.timestamp() - UNRESOLVED_DAYS.total_seconds()
        for e in events:
            candidate = _hash(json.dumps(e, sort_keys=True))
            # Only a lifecycle the decoder proves leaves 'unsupported'; it is reported, not acted on.
            canceled = e.get('lifecycle_status') == 'canceled'
            evidence[candidate] = {'status': 'canceled' if canceled else 'unsupported',
                                   'reason': e['lifecycle_reason'] if canceled else 'hxstore_lifecycle_unverified',
                                   'evidence': 'provisional_schedule',
                                   'start_unix': e['start_unix'], 'end_unix': e['end_unix']}
            people = [(email, name) for name, email in e['attendees'] if email and not _internal(email, self.settings.internal_domain)]
            org = (e['organizer_email'] or '').lower()
            if org and not _internal(org, self.settings.internal_domain) and org not in [p for p, _ in people]:
                people.append((org, e['organizer_name']))
            people = [(email, name) for email, name in people if matcher.classify(email) != 'automated']
            missing = {_hash(email): matcher.reason(email) for email, _ in people if matcher.reason(email)}
            if missing and e['end_unix'] < oldest:
                expired += 1
            elif missing:
                unresolved[candidate] = {'participants': missing}
            if e['end_unix'] >= since and e['start_unix'] <= (self.now + UPCOMING).timestamp():
                for email, name in people:
                    reason = matcher.reason(email)
                    if reason is not None:
                        self.unmatched.add((name or '(no name)', _domain(email), reason))
        self.state.update(calendar_evidence=evidence, calendar_unresolved=unresolved, calendar_policy=CALENDAR_POLICY,
                          calendar_unresolved_expired=expired)
        self.state.pop('calendar_due_at', None)
        self.state.pop('upcoming', None)
        self.counts.update(calendar_counts(self.state))

    def report(self, out: Path, news: bool = False) -> None:
        """Write a report when the run has news, health changed, or the decoder is degraded."""
        health = self.state.get('decoder_health')
        status = (health.get('status', 'degraded' if health['failing'] else 'healthy')
                  if health else 'unavailable')
        for path in out.glob('report-*.md'):
            if path.is_file() and path.stat().st_mtime < (self.now - timedelta(days=14)).timestamp():
                path.unlink()
        if not news and status != 'degraded' and status == self.state.get('decoder_report_status'):
            return
        self.state['decoder_report_status'] = status
        lines = [f'# Outlook intake {self.now.astimezone(self.settings.timezone):%Y-%m-%d %H:%M %Z}', '']
        if health:
            lines += ['## Decoder health', '', f'- status: {health.get("status", "degraded" if health["failing"] else "healthy")}',
                      f'- messages in window: {health["messages"]}',
                      f'- messages in store: {health.get("store_messages", "unavailable")}']
            lines += [f'- {name}: {rate * 100:.1f}%' if rate is not None else f'- {name}: unavailable'
                      for name, rate in health['rates'].items()]
            lines += [f'- failing: {", ".join(health["failing"]) or "none"}',
                      '- conversation_id: available for rows linked to one decoded message object', '']
        lines += ['## Counts', '']
        lines += [f'- {k}: {v}' for k, v in sorted(self.counts.items())]
        lines += ['', '## Writes', '']
        lines += [f'- {who} | {ref} | {verb}' + (f' | {note}' if note else '') for who, ref, verb, note in self.written]
        lines += ['', '## Failed requests', '']
        for ident, request in self.state.get('requests', {}).items():
            if not request['acknowledged']:
                lines.append(f'- {ident} | {request["verb"]} | {request.get("error_code", "pending")} | '
                             f'attempts={request.get("attempts", 0)} | {request.get("disposition", "retry")} | '
                             f'retry_at={request.get("retry_at")}')
        lines += ['', '## Unmatched', '']
        lines += [f'- {name} | {domain} | {reason}' for name, domain, reason in sorted(self.unmatched)]
        _private_write(out / f'report-{self.now.astimezone(self.settings.timezone):%Y%m%d-%H%M%S}.md', '\n'.join(lines) + '\n')


def mapping_counts(state: dict) -> dict:
    """Report the remaining mapping backlog, excluding settled mail."""
    mail = state.get('mail_unresolved', {})
    calendar = state.get('calendar_unresolved', {})
    participants = {}
    for item in [*mail.values(), *calendar.values()]:
        participants.update(item.get('participants', {}))
    participants = {h: r for h, r in participants.items() if r != 'automated'}
    return {'hydration_pending': len(state.get('pending_full', {})),
            'hydration_expired': sum(bool(p.get('expired')) for p in state.get('pending_full', {}).values()),
            'unresolved_messages': len(mail),
            'unresolved_recipients_unknown': sum(bool(item.get('recipients_unknown')) for item in mail.values()),
            'unresolved_read': sum(item.get('read', 'abandoned') != 'abandoned' for item in mail.values()),
            'unresolved_read_abandoned': sum(item.get('read') == 'abandoned' for item in mail.values()),
            'unresolved_expired': state.get('unresolved_expired', 0),
            'calendar_unresolved_expired': state.get('calendar_unresolved_expired', 0),
            'unresolved_calendar': len(calendar), 'unresolved_addresses': len(participants),
            'unmatched': len(participants),
            **{'mapping_' + reason: list(participants.values()).count(reason)
               for reason in ('missing', 'ambiguous', 'unsupported', 'shared_role')}}


def _prune(state: dict, now: datetime) -> None:
    horizon = now.timestamp() - (BACKFILL + UPCOMING).total_seconds() - 2 * COPY_WINDOW
    oldest = now.timestamp() - UNRESOLVED_DAYS.total_seconds()
    unresolved = state.get('mail_unresolved', {})
    expired = [ident for ident, item in unresolved.items()
               if not item.get('metadata_incomplete') and not item.get('recipients_unknown')
               and item['sent'] is not None and item['sent'] < oldest]
    for ident in expired:
        del unresolved[ident]
    state['unresolved_expired'] = state.get('unresolved_expired', 0) + len(expired)
    state['mail_done'] = {ck: ts for ck, ts in ((ck, [t for t in v if t >= horizon])
                                                 for ck, v in state.get('mail_done', {}).items()) if ts}
    signature_sources = {o['ident'] for o in state.get('signature_observations', {}).values() if 'ident' in o}
    held = set(unresolved) | set(state.get('pending_full', {})) | signature_sources
    state['mail_ids'] = {mid: ident for mid, ident in state.get('mail_ids', {}).items() if ident in held
                         or (ident.rsplit(':', 1)[1] != 'None' and int(ident.rsplit(':', 1)[1]) >= horizon)}
    state['mail_keys'] = {ident: k for ident, k in state.get('mail_keys', {}).items()
                          if ident in held or ident in state.get('flags', {})
                          or (ident.rsplit(':', 1)[1] != 'None' and int(ident.rsplit(':', 1)[1]) >= horizon)}
    state['requests'] = {k: r for k, r in state.get('requests', {}).items()
                         if k in {p['idempotency_key'] for p in state.get('email_cohort', {}).get('proposals', [])}
                         or not r['acknowledged'] or r['created_at'] >= horizon
                         or (r['verb'] == 'record-finding' and json.loads(r['payload']).get('kind') == email_contact_review.KIND)
                         or (r['created_at'] >= oldest and r['verb'] == 'log-activity'
                             and json.loads(r['payload']).get('kind') == 'email_out')}
    activity_ids = {r['activity_id'] for r in state['requests'].values() if r.get('activity_id')}
    for mailbox in state.get('delivery_mailboxes', {}).values():
        for report in mailbox['reports'].values():
            if (not report.get('request') and report['disposition'] == 'retryable'
                    and (datetime.fromisoformat(report['observed_at']).timestamp() < oldest
                         or report.get('attempts', 0) >= DELIVERY_RETRY_LIMIT)):
                report['disposition'] = 'unresolved'
        mailbox['reports'] = {k: r for k, r in mailbox['reports'].items()
                              if (r.get('request') and r['disposition'] != 'recorded')
                              or datetime.fromisoformat(r['observed_at']).timestamp() >= oldest - BACKFILL.total_seconds()}
        mailbox['sources'] = {mid: kept for mid, sources in mailbox['sources'].items()
                              if (kept := {ident: source for ident, source in sources.items()
                                           if int(ident.rsplit(':', 1)[1]) >= oldest})}
        mailbox['deals'] = {a: d for a, d in mailbox.get('deals', {}).items() if a in activity_ids}
        mailbox['versions'] = {a: v for a, v in mailbox.get('versions', {}).items() if a in activity_ids}
    for name in ('events_logged', 'events_balled'):
        state[name] = {k: t for k, t in state.get(name, {}).items() if t >= horizon}


def calendar_counts(state: dict) -> dict:
    statuses = [e.get('status') for e in state.get('calendar_evidence', {}).values()]
    return {'calendar_lifecycle_unsupported': statuses.count('unsupported'),
            'calendar_lifecycle_canceled': statuses.count('canceled'),
            'calendar_requests_quarantined': sum(r.get('disposition') == 'calendar_unsupported'
                                                 for r in state.get('requests', {}).values())}


def publish_agenda(carr: Callable[[str, dict], dict], snapshot: dict) -> None:
    """Replace the display snapshot; one re-read covers a concurrent publish."""
    for attempt in (1, 2):
        current = carr('read-progress-board', {'board_id': AGENDA_BOARD}).get('snapshot')
        base = current['version'] if isinstance(current, dict) and isinstance(current.get('version'), int) else 0
        try:
            carr('publish-board-snapshot', {
                'idempotency_key': key('publish-board-snapshot', AGENDA_BOARD,
                                       f'{_hash(json.dumps(snapshot, sort_keys=True))}|{base}'),
                'board_id': AGENDA_BOARD, 'base_version': base, 'snapshot': snapshot})
            return
        except CarrError as error:
            if error.code != 'agenda_version_conflict' or attempt == 2:
                raise


def _retire_email_steps(state: dict) -> bool:
    """Forget unapplied model actions while retaining acknowledged write history."""
    changed = 'steps' in state
    state.pop('steps', None)
    requests = state.get('requests', {})
    for ident, request in list(requests.items()):
        if request['verb'] == 'set-next-action' and not request['acknowledged']:
            del requests[ident]
            changed = True
    return changed


def _quarantine_calendar_requests(state: dict) -> None:
    """Retain unsupported calendar assertions without replaying them."""
    for request in state.get('requests', {}).values():
        if request['acknowledged']:
            continue
        args = json.loads(request['payload'])
        calendar = (request['verb'] == 'log-activity' and args.get('kind') in ('meeting', 'tour', 'call'))
        if calendar:
            request.update(disposition='calendar_unsupported', retry_at=None,
                           error_code='calendar_lifecycle_unsupported')



def _reconcile(out: Path, home: Path, ports: Ports, settings: OutlookSettings, state: dict) -> Outcome:
    """Save old decoder identities under the intake lock before any filing or window gate."""
    if settings.folder_account_id is None:
        return Outcome('failed', 'reconcile_failed', detail='folder_account_id_required')
    try:
        with _decode(home, out, ports.decode, settings.folder_account_id, settings.account) as (_, mails, _, _, _):
            work = Run(ports, state, ports.now(), out, settings)
            work.reconcile([m for m in _best_copies(mails, state) if not m['metadata_incomplete']])
        _save(out, state)
    except (FullDiskAccessRequired, DecodeError, sqlite3.Error, OSError, ValueError) as error:
        return Outcome('failed', 'reconcile_failed', detail=type(error).__name__)
    return Outcome('succeeded', 'reconciled', {**work.counts, 'mail_ids': len(state['mail_ids'])})


def run(repo: Path, home: Path, ports: Ports) -> Outcome:
    settings = load_settings(home)
    settings.require_repo(repo)
    out = repo / OUT
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    fd = _private_fd(out / 'lock', os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return Outcome('skipped', 'previous_run_active')
        try:
            try:
                state = json.loads((out / STATE).read_text())
            except (OSError, ValueError):
                state = {}
            reconcile = (out / STATE).is_file() and 'mail_ids' not in state and os.access(settings.decoder, os.X_OK)
            if not reconcile and not in_window(ports.now()):
                return Outcome('skipped', 'outside_window')
            for stale in out.glob(WORK_PREFIX + '*'):
                _remove(stale)
            if reconcile:
                return _reconcile(out, home, ports, settings, state)
            filing_account = _hash(f'{settings.owner}|{settings.account}|{settings.folder_account_id}')
            if state.get('filing_account') != filing_account:
                for name in ('filing_observed_at', 'last_email_in_filed_at'):
                    state.pop(name, None)
                state['filing_account'] = filing_account
            outcome = _run(out, home, ports, state)
            if outcome.state != 'succeeded':
                if outcome.state == 'degraded':
                    return _filing_outcome(out, state, outcome, ports.now(), settings)
                return outcome
            state = json.loads((out / STATE).read_text())
            work = Run(ports, state, ports.now(), out, settings)
            try:
                linked = work.file_links()
            except CarrError:
                return Outcome('failed', 'email_links_read_failed', outcome.counts)
            delivery = work.file_delivery_failures()
            flagged = work.apply_flags()
            for name, count in work.counts.items():
                outcome.counts[name] = outcome.counts.get(name, 0) + count
            for code in ERROR_CODES:
                outcome.counts['error_' + code] = sum(
                    not r['acknowledged'] and r.get('error_code') == code for r in state.get('requests', {}).values())
            _save(out, state)
            if not linked:
                return Outcome('failed', 'email_links_pending', outcome.counts)
            if not delivery:
                return Outcome('failed', 'delivery_failures_pending', outcome.counts)
            if not flagged:
                return Outcome('failed', 'flags_pending', outcome.counts)
            if work.counts['links_filed']:
                outcome.reason = 'processed'
            local = ports.now().astimezone(settings.timezone)
            today = local.date()
            previous_day = (today - timedelta(days=1)).isoformat()
            last_run = state.get('coverage_run_day') or state.get('coverage_check_day')
            missed = bool(last_run and last_run <= previous_day and state.get('coverage_check_day', '') < previous_day)
            state['coverage_run_day'] = today.isoformat()
            if local.time() >= COVERAGE_CHECK_AT and state.get('coverage_check_day') != today.isoformat():
                state['coverage_check_day'] = today.isoformat()
                shortfalls = coverage_verdict(outcome.counts)
                held = int(outcome.counts.get('unresolved_recipients_unknown', 0) > COVERAGE_HELD_RECIPIENTS_LIMIT)
                failed = any(shortfalls.values()) or held or missed
                outcome.counts.update(shortfalls, coverage_checked=1, coverage_failed=int(failed),
                                      coverage_held_recipients_over_limit=held)
            else:
                failed = missed
            if missed:
                outcome.counts.update(coverage_missed_verdict=1, coverage_failed=1)
            if outcome.counts.get('coverage_checked') or missed:
                state['coverage_failed'] = bool(failed)
            _save(out, state)
            if failed:
                outcome.state, outcome.reason = 'failed', 'coverage_failed'
            return _filing_outcome(out, state, outcome, ports.now(), settings)
        except CleanupError:
            return Outcome('failed', 'cleanup_failed')
    finally:
        os.close(fd)


def _filing_outcome(out: Path, state: dict, outcome: Outcome, now: datetime, settings: OutlookSettings) -> Outcome:
    if outcome.counts.get('stored', 0):
        since = state.setdefault('filing_observed_at', int(now.timestamp()))
        since = max(state.get('last_email_in_filed_at', since), since)
        if outcome.state != 'failed' and now >= business_day_deadline(
                datetime.fromtimestamp(since, timezone.utc), settings.timezone):
            outcome.state, outcome.reason = 'failed', 'no_email_in_business_day'
    else:
        state.pop('filing_observed_at', None)
    _save(out, state)
    return outcome


def _save(out: Path, state: dict) -> None:
    _private_write(out / STATE, json.dumps(state))


def _run(out: Path, home: Path, ports: Ports, state: dict) -> Outcome:
    settings = load_settings(home)
    if not Run(ports, state, ports.now(), out, settings).journal_recover():
        _save(out, state)
        return Outcome('failed', 'email_journal_pending', {'writes_deferred': 1})
    if settings.folder_account_id is None:
        return Outcome('failed', 'folder_account_id_required')
    try:
        store = resolve_store(home)
    except FileNotFoundError:
        return Outcome('failed', 'store_missing')
    if _retire_email_steps(state):
        _save(out, state)
    info = store.stat()
    try:
        decoder_identity = json.loads((settings.vendor / '.carr-build.json').read_text()).get('binary_sha256')
    except (OSError, ValueError, AttributeError):
        decoder_identity = None
    signature = [info.st_mtime_ns, info.st_size, decoder_identity]
    now = ports.now()
    matcher = None
    if state.get('mail_unresolved') or state.get('calendar_unresolved'):
        try:
            matcher = Matcher(ports.contacts(), settings.internal_domain)
        except CarrError as error:
            return Outcome('failed', 'carr_unreachable' if error.code == 'transport_error' else error.code)
    contacts_changed = matcher is not None and state.get('contact_map') != matcher.fingerprint()
    pending_writes = any(not r['acknowledged'] and r.get('disposition') != 'calendar_unsupported'
                         for r in state.get('requests', {}).values())
    hydration_due = bool(state.get('pending_full')) and now.timestamp() >= state.get('hydration_check_at', 0)
    review_due = 'calendar_review_at' not in state or (state['calendar_review_at'] is not None
                                                      and now.timestamp() >= state['calendar_review_at'])
    if state.get('account_binding') == _hash(settings.account.strip().lower()) and not state.get('decoder_degraded') and state.get('folder_account_id') == settings.folder_account_id and all(name in state for name in MAIL_STORE_COUNTERS) and 'email_index_published' in state and state.get('store') == signature and not hydration_due and not review_due and not contacts_changed and state.get('signature_policy') == SIGNATURE_POLICY and state.get('attachment_policy') == 2 and not state.get('attachments_pending') and not state.get('attachments_uncached') and not pending_writes and state.get('calendar_policy') == CALENDAR_POLICY and not state.get('reads_due'):
        if state.get('pending_full'):
            Run(ports, state, now, out, settings).full_text([])
            _save(out, state)
        counts = {**mapping_counts(state), **calendar_counts(state), 'legacy_unmatched': _legacy_unmatched(state),
                  'emails': 0, 'skipped_automated': 0, 'mail_ids': len(state.get('mail_ids', {})),
                  'email_index_failed': int(not state.get('email_index_published')),
                  'mail_rows_reusing_prior_identity': state.get('mail_rows_reusing_prior_identity', 0),
                  **{name: state.get(name, 0) for name in MAIL_STORE_COUNTERS}, **health_counts(state.get('decoder_health'))}
        return Outcome('succeeded', 'store_unchanged', counts)
    try:
        with _decode(home, out, ports.decode, settings.folder_account_id, settings.account) as (meta, mails, events, db, files):
            outcome = _process(out, ports, settings, state, signature, now, matcher, meta, mails, events, db, files)
        publication = meta.get('email_index_receipt', {})
        outcome.counts.update(publication)
        state['email_index_published'] = bool(publication.get('email_index_published'))
        _save(out, state)
        return outcome
    except FullDiskAccessRequired as error:
        return Outcome('failed', str(error), detail=error.detail)
    except DecodeError as error:
        return Outcome('failed', 'decode_failed', detail=str(error))
    except (sqlite3.Error, OSError) as error:
        detail = type(error).__name__
        if isinstance(error, OSError):
            detail += f' errno={error.errno}'
        return Outcome('failed', 'decode_failed', detail=detail)


def _process(out, ports, settings, state, signature, now, matcher, meta, mails, events, db, files):
    high = state.get('high_water')
    since = int((datetime.fromtimestamp(high, timezone.utc) - LOOKBACK if high else now - BACKFILL).timestamp())
    try:
        health = _decoder_health(meta, since, now, state.get('decoder_baseline', {}))
    except Exception as error:
        health = {'status': 'unavailable', 'error_class': (type(error).__name__.lower() if type(error) in
                  (TypeError, ValueError, KeyError, IndexError, AttributeError, RuntimeError) else 'other'), 'rates': {},
                  'eligible': {}, 'failing': [], 'messages': 0}
    state['decoder_health'] = health
    prior_failures = state.get('decoder_failures', [])
    unresolved = [name for name in prior_failures if name not in health['eligible'] and name not in ('message_layout', 'detail_layout')]
    alarm = int(bool(health['failing']) and not state.get('decoder_alarm_delivered', False))
    state['decoder_failures'] = list(dict.fromkeys(health['failing'] + unresolved))
    state['decoder_degraded'] = bool(state['decoder_failures'])
    if not state['decoder_degraded'] and health.get('status') != 'unavailable':
        state.pop('decoder_alarm_delivered', None)
        state['decoder_baseline'] = {**state.get('decoder_baseline', {}), **health['eligible']}
    _save(out, state)
    if 'block_verification' in health['failing'] and 'block_verification' in health['eligible']:
        work = Run(ports, state, now, out, settings)
        work.counts.update(decoder_alarm=alarm, writes_deferred=1, **health_counts(health),
                           **{name: meta[name] for name in MAIL_STORE_COUNTERS})
        work.report(out)
        state['decoder_alarm_delivered'] = True
        _save(out, state)
        return Outcome('degraded', 'writes_deferred', work.counts)
    if (meta.get('event_identities') or 0) > 0 and not meta.get('events_decoded'):
        work = Run(ports, state, now, out, settings)
        work.counts.update(health_counts(health))
        work.counts.update({name: meta[name] for name in MAIL_STORE_COUNTERS})
        if health.get('error_class'):
            work.counts['decoder_health_error_' + health['error_class']] = 1
        work.report(out)
        _save(out, state)
        return Outcome('failed', 'calendar_decode_empty', work.counts)

    mails = _best_copies(mails, state)
    state['hydration_check_at'] = int((now + REFRESH_INTERVAL).timestamp())
    work = Run(ports, state, now, out, settings)
    work.counts['mail_rows_reusing_prior_identity'] = sum(bool(m.get('mail_rows_reusing_prior_identity')) for m in mails)
    work.counts.update({name: meta[name] for name in MAIL_STORE_COUNTERS})
    work.counts['decoder_alarm'] = alarm
    work.counts.update(health_counts(health))
    if health.get('error_class'):
        work.counts['decoder_health_error_' + health['error_class']] = 1
    try:
        matcher = matcher or Matcher(ports.contacts(), settings.internal_domain)
        complete_mails = [m for m in mails if not m['metadata_incomplete']]
        prepared = work.prepare_attachments(complete_mails, matcher, db, files)
        _remove(files.parent)
        _quarantine_calendar_requests(state)
        _save(out, state)
        cohort_keys = {p['idempotency_key'] for p in state.get('email_cohort', {}).get('proposals', [])}
        for request_key, request in list(state.get('requests', {}).items()):
            if request_key in cohort_keys:
                continue
            if request['verb'] in ('record-deal-item', 'complete-deal-item', 'drop-action'):
                continue
            if (request['verb'] == 'record-finding'
                    and json.loads(request['payload']).get('kind') == email_contact_review.KIND):
                continue
            if not request['acknowledged'] and request.get('disposition') != 'calendar_unsupported':
                args = json.loads(request['payload'])
                work.write(request['verb'], args, 'pending write')
        # Outlook can expose a message after its sent time has left the watermark overlap.
        work.emails(mails, matcher, min(since, int((now - BACKFILL).timestamp())))
        work.file_attachments(prepared)
        work.counts['legacy_unmatched'] = _legacy_unmatched(state)
        work.observe_flags(complete_mails, matcher)
        work.leads(matcher)
        work.full_text(complete_mails)
        mail_processed = bool(work.written or any(value for key, value in work.counts.items()
                                                  if key not in (*MAIL_STORE_COUNTERS, 'mail_rows_reusing_prior_identity',
                                                                 'decoder_alarm', 'signature_held')
                                                  and not key.endswith(('_bp', '_sample'))))
        work.events(events, matcher, since)
        snapshot, agenda_counts = agenda(events, meta, settings, now)
        work.counts.update(agenda_counts)
        try:
            publish_agenda(ports.carr, snapshot)
            state['calendar_review_at'] = min((e['end_unix'] for e in events
                if now.timestamp() < e['end_unix']),
                default=None)
            work.counts.update(calendar_agenda_published=1, calendar_agenda_failed=0)
        except CarrError:
            # Display only: mail and its watermark carry on; the next run retries.
            state.pop('calendar_review_at', None)
            work.counts.update(calendar_agenda_published=0, calendar_agenda_failed=1)
    except CarrError as error:
        work.report(out)
        _save(out, state)
        return Outcome('failed', 'carr_unreachable' if error.code == 'transport_error' else error.code, work.counts)
    work.counts.update(mapping_counts(state))
    for code in ERROR_CODES:
        work.counts['error_' + code] = sum(not r['acknowledged'] and r.get('error_code') == code
                                          for r in state.get('requests', {}).values())
    work.report(out, news=bool(mail_processed or work.unmatched or state.get('pending_full')
                                        or work.counts['calendar_requests_quarantined']))
    counts = dict(work.counts)
    failed = next((reason for count, reason in (('writes_failed', 'writes_failed'),
                                               ('requests_blocked', 'request_blocked'),
                                               ('writes_deferred', 'writes_deferred'),
                                               ('model_failed', 'model_failed')) if work.counts[count]),
                  'attachments_pending' if state.get('attachments_pending') else None)
    if failed:
        _save(out, state)
        return Outcome('failed', failed, counts)
    state.update(store=signature, high_water=int(now.timestamp()), contact_map=matcher.fingerprint(), attachment_policy=2, signature_policy=SIGNATURE_POLICY,
                 **{name: meta[name] for name in MAIL_STORE_COUNTERS},
                 folder_account_id=settings.folder_account_id, account_binding=_hash(settings.account.strip().lower()),
                 mail_rows_reusing_prior_identity=work.counts['mail_rows_reusing_prior_identity'])
    _prune(state, now)
    _save(out, state)
    counts.update(mapping_counts(state), mail_ids=len(state['mail_ids']))
    if health['failing']:
        state['decoder_alarm_delivered'] = True
        _save(out, state)
        return Outcome('degraded', 'decoder_degraded', counts)
    return Outcome('succeeded', 'processed' if mail_processed else 'nothing_new', counts)


# ---- production ports --------------------------------------------------------

def _carr(repo: Path) -> Callable[[str, dict], dict]:
    def call(verb: str, args: dict) -> dict:
        try:
            done = subprocess.run([str(repo / 'run.sh'), 'call', verb, '-'], input=json.dumps(args), cwd=repo,
                                  capture_output=True, text=True, timeout=120, env=load_settings().transport_environment())
        except subprocess.TimeoutExpired:
            raise CarrError(code='timeout') from None
        except OSError as error:
            raise CarrError(code='transport_oversized' if error.errno == errno.E2BIG else 'transport_error') from None
        if done.returncode:
            code, refusal = 'setup_failed' if done.returncode == 2 else 'transport_error', None
            for prefix in ('TOOL ERROR ', 'RPC ERROR '):
                if prefix in (done.stderr or ''):
                    try:
                        payload, _ = json.JSONDecoder().raw_decode(done.stderr.split(prefix, 1)[1].lstrip())
                        if isinstance(payload, (dict, str)):
                            code = _reply_error(payload if isinstance(payload, dict) else {'error': payload})
                        if prefix == 'TOOL ERROR ' and isinstance(payload, dict) and isinstance(payload.get('error'), str) \
                                and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', payload['error']):
                            refusal = payload['error']
                    except ValueError:
                        pass
                    break
            raise CarrError(code=code, tool_refusal=refusal)
        try:
            reply = json.loads(done.stdout)
        except ValueError:
            raise CarrError(code='reply_unreadable') from None
        if not isinstance(reply, dict):
            raise CarrError(code='reply_unreadable')
        if verb in WRITE_VERBS and reply.get('ok') is not True:
            raise CarrError(code=_reply_error(reply))
        return reply
    return call


def _contacts(carr: Callable[[str, dict], dict]) -> Callable[[], list]:
    """Every live party address in any market, through the record layer's one contact read."""
    def read() -> list:
        try:
            rows = [Contact(c['address'], c['ref'], c['name'], c['exact'], c.get('contact_snapshot'))
                    for c in carr('email-match-contacts', {})['contacts']]
        except (CarrError, KeyError, TypeError):
            raise CarrError(code='contacts_read_failed') from None
        if not rows:
            raise CarrError(code='contacts_empty')
        return rows
    return read


def _decoder(home: Path) -> Callable[[Path, Path, Path], None]:
    def decode(copy: Path, files: Path, db: Path) -> None:
        try:
            done = subprocess.run([str(load_settings(home).decoder), 'db', str(copy), str(db), str(files)],
                                  capture_output=True, timeout=900)
        except subprocess.TimeoutExpired:
            raise DecodeError(_tool_detail('hxprobe', None, 'timeout')) from None
        except OSError:
            raise DecodeError(_tool_detail('hxprobe', None,
                                          'launch_failed')) from None
        if done.returncode:
            raise DecodeError(_tool_detail('hxprobe', done.returncode, 'exit_nonzero'))
    return decode


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    home = Path.home()
    settings = load_settings(home)
    settings.require_repo(repo)
    settings.require_service('intake')
    carr = _carr(repo)
    from functools import cache
    identity = cache(lambda: email_touch_journal.runtime_identity(repo))
    ports = Ports(carr=carr, contacts=_contacts(carr), journal_identity=identity,
                  decode=_decoder(home), now=lambda: datetime.now(timezone.utc),
                  read_unresolved=lambda mail, today, deals, parties: unresolved_email.ask(
                      mail, today, deals, parties, settings.model))
    outcome = run(repo, home, ports)
    print(status_line(outcome))
    print(receipt_line(outcome, settings.repo))
    if outcome.reason not in ('outside_window', 'previous_run_active', 'reconciled', 'reconcile_failed'):
        try:
            from lib import email_router
            email_router.step(repo, settings)
        except Exception:
            print('email-router-receipt ' + json.dumps({'state': 'failed', 'counts': {'error': 1}}))
    if outcome.state == 'degraded':
        return outcome.counts['decoder_alarm']
    return EXIT[outcome.state]
