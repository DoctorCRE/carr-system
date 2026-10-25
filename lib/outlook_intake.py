"""Outlook intake: Joe's mail and calendar into the record layer, by code.

Every 30 minutes from 07:00 to 19:00 local (com.carr.outlook-intake) this reads
a private copy of Main Identity's HxStore.hxd and its EFMData body sidecars,
never the live files, decodes it with the pinned hxprobe fork
(vendor-patches/hxprobe-calendar-and-bodies.patch), and writes what it finds
through the named record-layer verbs:

- an email to or from a known address becomes log-activity email_in/email_out
  with the full deduplicated plain-text body as its detail;
- a past meeting becomes log-activity meeting, tour or call by its title;
- a meeting in the next seven days becomes a dated ball (set-next-action);
- one isolated haiku call per matched full email proposes a next step, which
  becomes a ball when the record has none or only an older one.

A run outside 07:00-19:30 local does nothing. Matching requires one record with
that exact address; names alone never establish identity. Unmatched participants
in mail and calendar stay unresolved and are retried when mappings change, even
outside the rolling lookback. Records are never created, merged or renamed.
Every write carries uuid5(NAMESPACE_URL, "<verb>|<ref>|<source id>"), so an
action stays pending in the state file until the verb acknowledges it and the
retry replays the same key. A run with any write, model or cleanup failure
reports failed and does not advance its watermark.

An email whose body is still only Outlook's preview is logged with the preview,
flagged preview_only, and listed in out/outlook-intake/needs-refresh.json for the
same-host refresh worker to open. The file is a JSON array, mode 0600, one object per
email:

    {"message_id": "<opaque intake id>", "subject_hash": "<sha256 hex of the subject>",
     "sent_utc": "<ISO 8601>", "sender_email": "<address>", "opened_at": "<ISO 8601, refresh worker adds it>"}

The refresh worker finds the email by sender_email and subject_hash and adds opened_at; this
intake job owns every other field and keeps opened_at when it rewrites the file. Once a
listed email decodes with its full body, the next run logs that text as a
log-activity note (activities cannot be updated in place: the verb refuses a
reused key with new arguments) and drops the entry.

The private copy and decoded database live in a 0700 work folder under
out/outlook-intake/ and are deleted before the run ends; a folder that cannot be
deleted fails the run. The report and state are 0600 there too; nothing with a
name, address or body reaches the status line, the launchd log or the run row.
"""
from __future__ import annotations

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
import subprocess
import tempfile
from typing import Callable
import uuid
import zoneinfo

HXPROBE = Path('carr-local/hxstore-reverse-engineering/hxprobe/target/release/hxprobe')
COPY_HELPER = Path('carr-local/bin/outlook-intake-copy')
PROFILE = Path('Library/Group Containers/UBF8T346G9.Office/Outlook/Outlook 15 Profiles/Main Identity')
OUT = Path('out/outlook-intake')
REFRESH_LIST = 'needs-refresh.json'
STATE = 'state.json'
WORK_PREFIX = 'work-'
LOCAL = zoneinfo.ZoneInfo('America/Chicago')
WINDOW = (time(7, 0), time(19, 30))
INTERNAL_DOMAIN = 'carr.us'
WRITE_VERBS = frozenset({'log-activity', 'set-next-action'})
RECORD_REF = re.compile(r'^[CLV]-')
EXIT = {'succeeded': 0, 'skipped': 78, 'failed': 1}

BACKFILL = timedelta(hours=48)
LOOKBACK = timedelta(hours=6)
UPCOMING = timedelta(days=7)
REFRESH_DAYS = timedelta(days=7)
REFRESH_INTERVAL = timedelta(minutes=30)
COPY_WINDOW = 86_400
VERIFIED_MIN = 0.95
MODEL_BODY_CHARS = 12_000
BALL_OWNER = 'joe'


def resolve_store(home: Path) -> Path:
    """The Studio intake and its copy helper use only Main Identity."""
    store = home / PROFILE / 'HxStore.hxd'
    if not store.is_file():
        raise FileNotFoundError(f'No Outlook store found: {store}')
    return store


ERROR_CODES = frozenset({'timeout', 'auth_refused', 'key_reuse', 'transport_oversized',
                         'no_acknowledgement', 'reply_unreadable', 'transport_error',
                         'invalid_request', 'subject_unresolved', 'database_refused',
                         'setup_failed', 'contacts_empty', 'contacts_read_failed', 'next_action_conflict',
                         'legacy_action_guard_required'})
BLOCKED_ERRORS = frozenset({'key_reuse', 'transport_oversized', 'invalid_request', 'subject_unresolved'})


def _reply_error(reply: dict) -> str:
    error = reply.get('error')
    if error in ('key_reuse', 'next_action_conflict'):
        return error
    if error in ('unauthorized', 'forbidden'):
        return 'auth_refused'
    if error in ('missing_required', 'invalid_field_value', 'missing_idempotency_key', 'unknown_kind'):
        return 'invalid_request'
    if error in ('subject_not_found', 'needs_disambiguation', 'ref_not_found', 'not_found'):
        return 'subject_unresolved'
    if error == 'database_refused_the_statement':
        return 'database_refused'
    return 'no_acknowledgement'


class CarrError(RuntimeError):
    """A safe diagnostic code; never retain request content or raw process errors."""

    def __init__(self, *, code: str = 'transport_error'):
        self.code = code if code in ERROR_CODES else 'transport_error'
        super().__init__(self.code)


class DecodeError(RuntimeError):
    """hxprobe could not decode the store copy."""


class FullDiskAccessRequired(RuntimeError):
    """macOS denied the dedicated snapshot helper access to Outlook."""


class CleanupError(RuntimeError):
    """A private work folder could not be deleted."""


@dataclass(frozen=True)
class Contact:
    email: str
    ref: str
    name: str


@dataclass
class Ports:
    carr: Callable[[str, dict], dict]
    contacts: Callable[[], list]
    next_step: Callable[[dict, date], dict]
    decode: Callable[[Path, Path, Path], None]
    now: Callable[[], datetime]


@dataclass
class Outcome:
    state: str
    reason: str
    counts: dict = field(default_factory=dict)


def status_line(outcome: Outcome) -> str:
    counts = ' '.join(f'{k}={v}' for k, v in sorted(outcome.counts.items()))
    return f'outlook-intake {outcome.state} {outcome.reason} {counts}'.rstrip()


def key(verb: str, ref: str, source: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'{verb}|{ref}|{source}'))


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def mail_identity(copy_key: str | None, sender: str, subject: str, sent_unix: int) -> tuple[str, str]:
    """(copy hash, intake id). hxprobe's copy_key is the identity all copies of one email share."""
    ck = _hash(copy_key or f'{sender.lower()}\x1f{subject.lower()}')[:20]
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


def _internal(address: str) -> bool:
    return _domain(address) == INTERNAL_DOMAIN


def _addresses(text: str | None) -> list[str]:
    return [a.lower() for a in re.findall(r'[\w.+\'-]+@[\w-]+(?:\.[\w-]+)+', text or '')]


def _clean_name(name: str | None, subject: str | None) -> str | None:
    """hxprobe sometimes reads the subject or an attachment name into sender_name."""
    name = (name or '').strip()
    if not name or '@' in name or name == (subject or '').strip() or re.search(r'\.\w{2,4}$', name):
        return None
    return name


def in_window(now: datetime) -> bool:
    return WINDOW[0] <= now.astimezone(LOCAL).time() <= WINDOW[1]


# ---- the decoded store -------------------------------------------------------

def _load(db: Path) -> tuple[dict, list[dict], list[dict]]:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        meta = {r['key']: r['value'] for r in con.execute('SELECT key, value FROM meta')}
        mails = [dict(r) for r in con.execute(
            'SELECT sender, sender_name, recipients, subject, body, preview, body_kind, sent_unix, copy_key '
            'FROM messages WHERE sent_unix IS NOT NULL AND sender IS NOT NULL')]
        events = [dict(r) for r in con.execute('SELECT * FROM events')]
        attendees: dict[int, list] = {}
        for r in con.execute('SELECT event_id, name, email FROM event_attendees'):
            attendees.setdefault(r['event_id'], []).append((r['name'], (r['email'] or '').lower()))
        for e in events:
            e['attendees'] = attendees.get(e['id'], [])
        return meta, mails, events
    finally:
        con.close()


def _snapshot(home: Path, work: Path) -> None:
    helper = home / COPY_HELPER
    try:
        done = subprocess.run([str(helper)], cwd=work, capture_output=True, timeout=900)
        if done.returncode in (errno.EPERM, errno.EACCES):
            raise PermissionError(done.returncode, 'Outlook copy denied')
        if done.returncode:
            raise DecodeError(f'Outlook copy helper exit {done.returncode}')
    except PermissionError:
        raise FullDiskAccessRequired(f'grant Full Disk Access to {helper}') from None
    except subprocess.TimeoutExpired:
        raise DecodeError('Outlook copy helper timed out') from None


def _remove(work: Path) -> None:
    shutil.rmtree(work, onerror=lambda *_: None)
    if work.exists():
        raise CleanupError('private work folder left behind')


def _decode(home: Path, out: Path, decode: Callable[[Path, Path, Path], None]):
    work = Path(tempfile.mkdtemp(prefix=WORK_PREFIX, dir=out))
    try:
        copy = work / 'store.hxd'
        _snapshot(home, work)
        files = work / 'Files'
        db = work / 'store.db'
        decode(copy, files, db)
        return _load(db)
    finally:
        _remove(work)


def _best_copies(mails: list[dict]) -> list[dict]:
    """One row per email: hxprobe collapses copies; any it left are joined here, full body first."""
    rows = []
    for m in mails:
        m['ck'], m['ident'] = mail_identity(m['copy_key'], m['sender'], m['subject'] or '', m['sent_unix'])
        m['full'] = m['body_kind'] == 'full' and bool((m['body'] or '').strip())
        m['text'] = (m['body'] or '').strip() or (m['preview'] or '').strip()
        rows.append(m)
    rows.sort(key=lambda m: (m['ck'], m['sent_unix']))
    out: list[dict] = []
    for m in rows:
        last = out[-1] if out else None
        if last and last['ck'] == m['ck'] and m['sent_unix'] - last['first_sent'] <= COPY_WINDOW:
            if (m['full'], len(m['text'])) > (last['full'], len(last['text'])):
                m['first_sent'], m['sent_unix'], m['ident'] = last['first_sent'], last['sent_unix'], last['ident']
                out[-1] = m
            continue
        m['first_sent'] = m['sent_unix']
        out.append(m)
    return sorted(out, key=lambda m: m['sent_unix'])


# ---- matching ----------------------------------------------------------------

class Matcher:
    def __init__(self, contacts: list):
        self.by_email: dict[str, set] = {}
        self.unsupported: set[str] = set()
        for c in contacts:
            if '@' in c.email and not _internal(c.email):
                if RECORD_REF.match(c.ref):
                    self.by_email.setdefault(c.email.lower(), set()).add(c.ref)
                else:
                    self.unsupported.add(c.email.lower())

    def fingerprint(self) -> str:
        return _hash(json.dumps({'supported': {a: sorted(refs) for a, refs in self.by_email.items()},
                                 'unsupported': sorted(self.unsupported)}, sort_keys=True))

    def reason(self, address: str) -> str | None:
        refs = self.by_email.get(address.lower(), set())
        if len(refs) > 1:
            return 'ambiguous'
        if not refs:
            return 'unsupported' if address.lower() in self.unsupported else 'missing'
        return None

    def ref(self, address: str) -> str | None:
        refs = self.by_email.get(address.lower(), set())
        return next(iter(refs)) if len(refs) == 1 else None


# ---- the model ---------------------------------------------------------------

REPLY_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['next_step', 'due_on'],
    'properties': {'next_step': {'type': ['string', 'null']},
                   'due_on': {'type': ['string', 'null'], 'pattern': r'^\d{4}-\d{2}-\d{2}$'}},
}
SYSTEM_PROMPT = (
    'You read one email from the inbox of a healthcare commercial real estate broker. Return the one '
    'concrete next step the broker owes because of it, as a short imperative sentence, and the date '
    'it is due (YYYY-MM-DD) when the email implies one. Return null for both when the email needs '
    'nothing from the broker. Never invent facts that are not in the email.')


def model_command() -> list[str]:
    return ['claude', '-p', '--model', 'haiku', '--no-session-persistence', '--tools', '',
            '--setting-sources', '', '--strict-mcp-config', '--disable-slash-commands',
            '--exclude-dynamic-system-prompt-sections', '--output-format', 'json',
            '--json-schema', json.dumps(REPLY_SCHEMA), '--system-prompt', SYSTEM_PROMPT]


def parse_reply(reply: dict) -> dict:
    out = reply.get('structured_output') if isinstance(reply, dict) else None
    if reply.get('is_error') or not isinstance(out, dict):
        raise ValueError('model reply carries no result')
    step, due = out.get('next_step'), out.get('due_on')
    if step is not None and not isinstance(step, str):
        raise ValueError('next_step is not text')
    if due is not None:
        if not isinstance(due, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', due):
            raise ValueError('due_on is not a date')
        date.fromisoformat(due)
    return {'next_step': (step or '').strip() or None, 'due_on': due}


def ask_model(mail: dict, today: date) -> dict:
    direction = 'sent by the broker' if mail['outbound'] else 'received by the broker'
    prompt = (f'Today is {today.isoformat()}. This email was {direction}.\n'
              f'Subject: {mail["subject"] or ""}\n\n{mail["text"][:MODEL_BODY_CHARS]}')
    env = {k: v for k, v in os.environ.items() if k not in ('ANTHROPIC_API_KEY', 'CLAUDECODE')}
    with tempfile.TemporaryDirectory() as empty:
        done = subprocess.run(model_command(), input=prompt, capture_output=True, text=True,
                              cwd=empty, env=env, timeout=180)
    if done.returncode:
        raise RuntimeError(f'model exit {done.returncode}')
    return parse_reply(json.loads(done.stdout))


# ---- one run -----------------------------------------------------------------

class Run:
    def __init__(self, ports: Ports, state: dict, now: datetime, out: Path):
        self.ports, self.state, self.now, self.out = ports, state, now, out
        self.today = now.astimezone(LOCAL).date()
        self.counts = {k: 0 for k in ('emails', 'events_past', 'events_upcoming', 'logged', 'preview_only',
                                      'full_text_notes', 'balls_set', 'balls_kept', 'unmatched',
                                      'model_failed', 'steps_pending', 'hydration_expired', 'writes_failed',
                                      'writes_deferred', 'requests_blocked')}
        self.written: list[tuple[str, str, str, str]] = []
        self.unmatched: set[tuple[str, str, str]] = set()
        self.balls: dict[str, tuple[str, int, int]] = {}
        self.attempted: set[str] = set()

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
                if verb == 'set-next-action' and error.code == 'next_action_conflict':
                    request.update(acknowledged=True, disposition='superseded', retry_at=None,
                                   error_code=None)
                    self.counts['balls_kept'] += 1
                    _save(self.out, self.state)
                    return True
                blocked = error.code in BLOCKED_ERRORS
                request.update(error_code=error.code, disposition='blocked' if blocked else 'retry',
                               retry_at=None if blocked else int(self.now.timestamp()) +
                               min(1800, 60 * 2 ** min(request['attempts'] - 1, 5)))
                self.counts['writes_failed'] += 1
                _save(self.out, self.state)
                return False
            if verb == 'set-next-action':
                request['next_action_id'] = reply.get('next_action_id')
            request.update(acknowledged=True, disposition='acknowledged', retry_at=None, error_code=None)
            _save(self.out, self.state)
        self.written.append((who, args['ref'], verb, note))
        return True

    def open_ball(self, ref: str) -> dict | None:
        """Read Joe's canonical ordinary action, including its replacement version."""
        reply = self.ports.carr('get-next-action', {'ref': ref, 'owner': BALL_OWNER})
        if reply.get('ok') is not True or reply.get('owner') != BALL_OWNER or 'action' not in reply:
            raise CarrError(code='reply_unreadable')
        action = reply['action']
        if action is not None:
            try:
                if not isinstance(action['id'], str) or not isinstance(action['version'], int):
                    raise ValueError('invalid action identity')
                datetime.fromisoformat(action['updated_at'])
            except (KeyError, TypeError, ValueError):
                raise CarrError(code='reply_unreadable') from None
        return action

    def ball(self, ref: str, who: str, description: str, due_on: str, item_at: int, source: str,
             only_if_none: bool = False) -> bool:
        """Set the ball only while the canonical action read still holds."""
        action = self.open_ball(ref)
        existing = int(datetime.fromisoformat(action['updated_at']).timestamp()) if action else None
        if action and ref in self.balls and self.balls[ref][:2] == (action['id'], action['version']):
            existing = self.balls[ref][2]
        if existing is not None and (only_if_none or existing >= item_at):
            self.counts['balls_kept'] += 1
            self.written.append((who, ref, 'set-next-action', 'kept newer ball'))
            return True
        note = 'no prior ball' if existing is None else 'replaced ball'
        args = {'idempotency_key': key('set-next-action', ref, source), 'ref': ref,
                'description': description, 'due_on': due_on, 'owner': BALL_OWNER,
                'expected_action': {k: action[k] for k in ('id', 'version')} if action else None}
        if not self.write('set-next-action', args, who, note):
            return False
        request = self.state['requests'][args['idempotency_key']]
        if request.get('disposition') != 'superseded':
            self.counts['balls_set'] += 1
            self.balls[ref] = (request.get('next_action_id'), 1, item_at)
        return True

    # emails

    def emails(self, mails: list[dict], matcher: Matcher, since: int) -> None:
        done = self.state.setdefault('mail_done', {})
        unresolved = self.state.setdefault('mail_unresolved', {})
        pending = self.state.setdefault('pending_full', {})
        steps = self.state.setdefault('steps', {})
        retries: dict[str, list] = {}
        for ident, item in unresolved.items():
            retries.setdefault(item['ck'], []).append((ident, item['sent']))
        for m in mails:
            prior = next(((ident, sent) for ident, sent in retries.get(m['ck'], [])
                          if abs(sent - m['first_sent']) <= COPY_WINDOW), None)
            if prior:
                m['ident'], m['sent_unix'] = prior
            if (m['sent_unix'] < since and m['ident'] not in unresolved) or m['sent_unix'] > self.now.timestamp() + 300:
                continue
            if m['ident'] not in unresolved and any(abs(t - m['sent_unix']) <= COPY_WINDOW for t in done.get(m['ck'], [])):
                continue
            outbound = _internal(m['sender'])
            parties = ([(a, None) for a in _addresses(m['recipients']) if not _internal(a)] if outbound
                       else [(m['sender'].lower(), _clean_name(m['sender_name'], m['subject']))])
            if not parties:
                done.setdefault(m['ck'], []).append(m['sent_unix'])
                continue
            self.counts['emails'] += 1
            refs = []
            for address, name in parties:
                ref = matcher.ref(address)
                if ref is None:
                    reason = matcher.reason(address)
                    assert reason is not None
                    self.unmatched.add((name or '(no name)', _domain(address), reason))
                elif ref not in refs:
                    refs.append(ref)
            progress = unresolved.setdefault(m['ident'], {'ck': m['ck'], 'sent': m['sent_unix']})
            progress['participants'] = {_hash(address): matcher.reason(address) for address, _ in parties
                                        if matcher.reason(address)}
            completed = progress.setdefault('completed_refs', [])
            missing = bool(progress['participants'])
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
                                                'subject_hash': _hash(m['subject'] or ''),
                                                'who': who, 'outbound': outbound})
                pending[m['ident']]['refs'] = sorted(set(pending[m['ident']]['refs']) | set(refs))
            if m['full'] and m['ident'] not in pending:
                steps.setdefault(m['ident'], {'ck': m['ck'], 'sent': m['sent_unix'], 'ref': refs[0],
                                           'who': who, 'outbound': outbound,
                                           'activity_keys': [key('log-activity', ref, m['ident']) for ref in refs]})
            results = [self.write('log-activity', {
                'idempotency_key': key('log-activity', ref, m['ident']), 'ref': ref, 'kind': kind,
                'summary': f'{label}: {m["subject"] or "(no subject)"}', 'detail': m['text'],
                'occurred_at': _iso(m['sent_unix'])}, who, '' if m['full'] else 'preview_only') for ref in refs]
            completed.extend(ref for ref, ok in zip(refs, results) if ok)
            if not all(results):
                continue
            self.counts['logged'] += 1
            if not missing:
                unresolved.pop(m['ident'], None)
                done.setdefault(m['ck'], []).append(m['sent_unix'])
            if not m['full']:
                self.counts['preview_only'] += 1

    def next_steps(self, mails: list[dict]) -> None:
        """Ask for each pending email's next step and set its ball; a step stays pending until both succeed."""
        steps = self.state.setdefault('steps', {})
        by_ck: dict[str, list[dict]] = {}
        for m in mails:
            by_ck.setdefault(m['ck'], []).append(m)
        for ident, s in sorted(steps.items(), key=lambda kv: kv[1]['sent']):
            if ident in self.state.get('pending_full', {}):
                del steps[ident]
                continue
            if not all(self.state['requests'].get(k, {}).get('acknowledged') for k in s.get('activity_keys', [])):
                self.counts['steps_pending'] += 1
                continue
            if 'reply' not in s:
                found = [m for m in by_ck.get(s['ck'], []) if abs(m['first_sent'] - s['sent']) <= COPY_WINDOW]
                if found and not found[0]['full']:
                    continue
                if not found:
                    self.counts['steps_pending'] += 1
                    continue
                m = {**found[0], 'outbound': s['outbound']}
                try:
                    reply = parse_reply({'is_error': False, 'structured_output': self.ports.next_step(m, self.today)})
                except Exception:
                    self.counts['model_failed'] += 1
                    continue
                s['reply'] = {**reply, 'due_on': reply['due_on'] or self.today.isoformat()}
                _save(self.out, self.state)
            reply = s['reply']
            if not reply['next_step'] or self.ball(
                    s['ref'], s['who'], reply['next_step'], reply['due_on'] or self.today.isoformat(),
                    s['sent'], f'next|{ident}'):
                del steps[ident]

    def full_text(self, mails: list[dict]) -> None:
        """Log the full text of emails first logged from their preview, once it decodes."""
        pending = self.state.setdefault('pending_full', {})
        full: dict[str, list[dict]] = {}
        for m in mails:
            if m['full']:
                full.setdefault(m['ck'], []).append(m)
        for ident, p in list(pending.items()):
            match = [m for m in full.get(p['ck'], []) if abs(m['first_sent'] - p['sent']) <= COPY_WINDOW]
            if not match:
                if p['sent'] < self.now.timestamp() - REFRESH_DAYS.total_seconds():
                    p['expired'] = True
                    self.counts['hydration_expired'] += 1
                continue
            m = match[0]
            ok = all([self.write('log-activity', {
                'idempotency_key': key('log-activity', ref, f'fullbody|{ident}'), 'ref': ref, 'kind': 'note',
                'summary': f'Full text of email: {m["subject"] or "(no subject)"}', 'detail': m['text'],
                'occurred_at': _iso(p['sent'])}, p['sender_email'], 'full text follow-up') for ref in p['refs']])
            if ok:
                self.counts['full_text_notes'] += 1
                steps = self.state.setdefault('steps', {})
                steps.pop(ident, None)
                steps.setdefault(f'fullbody|{ident}', {
                    'ck': p['ck'], 'sent': p['sent'], 'ref': p['refs'][0],
                    'who': p.get('who', p['sender_email']),
                    'outbound': p.get('outbound', _internal(p['sender_email'])),
                    'activity_keys': [key('log-activity', ref, ident) for ref in p['refs']
                                      if key('log-activity', ref, ident) in self.state['requests']] +
                                     [key('log-activity', ref, f'fullbody|{ident}') for ref in p['refs']]})
                del pending[ident]

    # calendar

    def events(self, events: list[dict], matcher: Matcher, since: int) -> None:
        logged = self.state.setdefault('events_logged', {})
        balled = self.state.setdefault('events_balled', {})
        unresolved = self.state.setdefault('calendar_unresolved', {})
        transitions: list[int] = []
        now = int(self.now.timestamp())
        for e in sorted(events, key=lambda e: e['start_unix']):
            source = f'{e["goid"]}|{e["start_unix"]}'
            if e['end_unix'] > now:
                transitions.append(e['end_unix'])
            enters = e['start_unix'] - int(UPCOMING.total_seconds())
            if enters > now:
                transitions.append(enters)
            past = e['end_unix'] <= now and (since <= e['end_unix'] or source in unresolved)
            soon = now < e['start_unix'] <= now + UPCOMING.total_seconds()
            if not past and not soon:
                continue
            if source not in unresolved and not (past and source not in logged) and not (soon and source not in balled):
                continue
            people = [(email, name) for name, email in e['attendees'] if email and not _internal(email)]
            org = (e['organizer_email'] or '').lower()
            if org and not _internal(org) and org not in [p for p, _ in people]:
                people.append((org, e['organizer_name']))
            refs: list[tuple[str, str]] = []
            for email, name in people:
                ref = matcher.ref(email)
                if ref is None:
                    reason = matcher.reason(email)
                    assert reason is not None
                    self.unmatched.add((name or '(no name)', _domain(email), reason))
                elif ref not in [r for r, _ in refs]:
                    refs.append((ref, name or email))
            progress = unresolved.setdefault(source, {})
            progress['participants'] = {_hash(email): matcher.reason(email) for email, _ in people
                                        if matcher.reason(email)}
            missing = not refs or bool(progress['participants'])
            completed = progress.setdefault('logged_refs' if past else 'balled_refs', [])
            refs = [(ref, name) for ref, name in refs if ref not in completed]
            subject = e['subject'] or 'Meeting'
            if past:
                self.counts['events_past'] += 1
                kind = 'tour' if re.search(r'\btour', subject, re.I) else 'call' if re.search(r'\bcall\b', subject, re.I) else 'meeting'
                results = [self.write('log-activity', {
                        'idempotency_key': key('log-activity', ref, source), 'ref': ref, 'kind': kind,
                        'summary': f'{kind.capitalize()}: {subject}', 'detail': e['body'] or '',
                        'occurred_at': _iso(e['start_unix'])}, name) for ref, name in refs]
                completed.extend(ref for (ref, _), ok in zip(refs, results) if ok)
                if not missing and all(results):
                    logged[source] = e['end_unix']
                    unresolved.pop(source, None)
            else:
                self.counts['events_upcoming'] += 1
                start = datetime.fromtimestamp(e['start_unix'], LOCAL)
                results = [self.ball(ref, name, f'{subject} with {name}, {start:%a %b %-d %-I:%M %p}',
                                  start.date().isoformat(), now, f'event|{source}', only_if_none=True)
                        for ref, name in refs]
                completed.extend(ref for (ref, _), ok in zip(refs, results) if ok)
                if not missing and all(results):
                    balled[source] = e['start_unix']
                    unresolved.pop(source, None)
        self.state['calendar_due_at'] = min(transitions, default=None)
        self.state.pop('upcoming', None)

    def report(self, out: Path, refresh_count: int) -> None:
        lines = [f'# Outlook intake {self.now.astimezone(LOCAL):%Y-%m-%d %H:%M %Z}', '', '## Counts', '']
        lines += [f'- {k}: {v}' for k, v in sorted(self.counts.items())]
        lines += [f'- needs_refresh_listed: {refresh_count}', '', '## Writes', '']
        lines += [f'- {who} | {ref} | {verb}' + (f' | {note}' if note else '') for who, ref, verb, note in self.written]
        lines += ['', '## Failed requests', '']
        for ident, request in self.state.get('requests', {}).items():
            if not request['acknowledged']:
                lines.append(f'- {ident} | {request["verb"]} | {request.get("error_code", "pending")} | '
                             f'attempts={request.get("attempts", 0)} | {request.get("disposition", "retry")} | '
                             f'retry_at={request.get("retry_at")}')
        lines += ['', '## Unmatched', '']
        lines += [f'- {name} | {domain} | {reason}' for name, domain, reason in sorted(self.unmatched)]
        _private_write(out / f'report-{self.now.astimezone(LOCAL):%Y%m%d-%H%M%S}.md', '\n'.join(lines) + '\n')


def _refresh_list(out: Path, pending: dict) -> int:
    path = out / REFRESH_LIST
    try:
        opened = {e['message_id']: e['opened_at'] for e in json.loads(path.read_text())
                  if isinstance(e, dict) and e.get('opened_at')}
    except (OSError, ValueError, TypeError, KeyError):
        opened = {}
    entries = []
    for ident, p in sorted(pending.items(), key=lambda kv: kv[1]['sent']):
        entry = {'message_id': ident, 'subject_hash': p['subject_hash'], 'sent_utc': _iso(p['sent']),
                 'sender_email': p['sender_email']}
        if p.get('expired'):
            entry['expired'] = True
        if ident in opened:
            entry['opened_at'] = opened[ident]
        entries.append(entry)
    _private_write(path, json.dumps(entries, indent=2) + '\n')
    return len(entries)


def mapping_counts(state: dict) -> dict:
    mail = state.get('mail_unresolved', {})
    calendar = state.get('calendar_unresolved', {})
    participants = {}
    for item in [*mail.values(), *calendar.values()]:
        participants.update(item.get('participants', {}))
    return {'hydration_pending': len(state.get('pending_full', {})),
            'hydration_expired': sum(bool(p.get('expired')) for p in state.get('pending_full', {}).values()),
            'unresolved_messages': len(mail),
            'unresolved_calendar': len(calendar), 'unresolved_addresses': len(participants),
            'unmatched': len(participants),
            **{'mapping_' + reason: list(participants.values()).count(reason)
               for reason in ('missing', 'ambiguous', 'unsupported')}}


def _prune(state: dict, now: datetime) -> None:
    horizon = now.timestamp() - (BACKFILL + UPCOMING).total_seconds() - 2 * COPY_WINDOW
    state['mail_done'] = {ck: ts for ck, ts in ((ck, [t for t in v if t >= horizon])
                                                 for ck, v in state.get('mail_done', {}).items()) if ts}
    state['requests'] = {k: r for k, r in state.get('requests', {}).items()
                         if not r['acknowledged'] or r['created_at'] >= horizon}
    for name in ('events_logged', 'events_balled'):
        state[name] = {k: t for k, t in state.get(name, {}).items() if t >= horizon}


def _calendar_due(state: dict, now: datetime) -> bool:
    """Recheck when a meeting enters the horizon or ends; seed older state once."""
    if 'calendar_due_at' not in state:
        return True
    due = state['calendar_due_at']
    return due is not None and due <= now.timestamp()


def run(repo: Path, home: Path, ports: Ports) -> Outcome:
    if not in_window(ports.now()):
        return Outcome('skipped', 'outside_window')
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
            for stale in out.glob(WORK_PREFIX + '*'):
                _remove(stale)
            return _run(out, home, ports)
        except CleanupError:
            return Outcome('failed', 'cleanup_failed')
    finally:
        os.close(fd)


def _save(out: Path, state: dict) -> None:
    _private_write(out / STATE, json.dumps(state))


def _run(out: Path, home: Path, ports: Ports) -> Outcome:
    try:
        store = resolve_store(home)
    except FileNotFoundError:
        return Outcome('failed', 'store_missing')
    try:
        state = json.loads((out / STATE).read_text())
    except (OSError, ValueError):
        state = {}
    info = store.stat()
    signature = [info.st_mtime_ns, info.st_size]
    now = ports.now()
    matcher = None
    if state.get('mail_unresolved') or state.get('calendar_unresolved'):
        try:
            matcher = Matcher(ports.contacts())
        except CarrError as error:
            return Outcome('failed', 'carr_unreachable' if error.code == 'transport_error' else error.code)
    contacts_changed = matcher is not None and state.get('contact_map') != matcher.fingerprint()
    pending_writes = any(not r['acknowledged'] for r in state.get('requests', {}).values())
    hydration_due = bool(state.get('pending_full')) and now.timestamp() >= state.get('hydration_check_at', 0)
    if state.get('store') == signature and not hydration_due and not contacts_changed and not state.get('steps') and not pending_writes and not _calendar_due(state, now):
        if state.get('pending_full'):
            Run(ports, state, now, out).full_text([])
            _refresh_list(out, state['pending_full'])
            _save(out, state)
        return Outcome('succeeded', 'store_unchanged', mapping_counts(state))
    try:
        meta, mails, events = _decode(home, out, ports.decode)
    except FullDiskAccessRequired as error:
        return Outcome('failed', str(error))
    except (DecodeError, sqlite3.Error, OSError):
        return Outcome('failed', 'decode_failed')
    found = meta.get('blocks_found') or 0
    if not found or (meta.get('blocks_verified') or 0) / found < VERIFIED_MIN:
        return Outcome('failed', 'store_verification_low')
    if (meta.get('event_identities') or 0) > 0 and not meta.get('events_decoded'):
        return Outcome('failed', 'calendar_decode_empty')

    high = state.get('high_water')
    since = int((datetime.fromtimestamp(high, timezone.utc) - LOOKBACK if high else now - BACKFILL).timestamp())
    mails = _best_copies(mails)
    state['hydration_check_at'] = int((now + REFRESH_INTERVAL).timestamp())
    work = Run(ports, state, now, out)
    try:
        for request in list(state.get('requests', {}).values()):
            if not request['acknowledged']:
                args = json.loads(request['payload'])
                if request['verb'] == 'set-next-action' and (
                        args.get('owner') != BALL_OWNER or 'expected_action' not in args):
                    request.update(disposition='blocked', retry_at=None,
                                   error_code='legacy_action_guard_required')
                work.write(request['verb'], args, 'pending write')
        matcher = matcher or Matcher(ports.contacts())
        work.emails(mails, matcher, since)
        work.full_text(mails)
        work.next_steps(mails)
        work.events(events, matcher, since)
    except CarrError as error:
        _save(out, state)
        return Outcome('failed', 'carr_unreachable' if error.code == 'transport_error' else error.code, work.counts)
    work.counts.update(mapping_counts(state))
    for code in ERROR_CODES:
        work.counts['error_' + code] = sum(not r['acknowledged'] and r.get('error_code') == code
                                          for r in state.get('requests', {}).values())
    refresh = _refresh_list(out, state.get('pending_full', {}))
    if any(work.counts.values()) or work.written:
        work.report(out, refresh)
    counts = {**work.counts, 'needs_refresh_listed': refresh}
    failed = next((reason for count, reason in (('writes_failed', 'writes_failed'),
                                               ('requests_blocked', 'request_blocked'),
                                               ('writes_deferred', 'writes_deferred'),
                                               ('model_failed', 'model_failed')) if work.counts[count]),
                  'steps_pending' if state.get('steps') else None)
    if failed:
        _save(out, state)
        return Outcome('failed', failed, counts)
    state.update(store=signature, high_water=int(now.timestamp()), contact_map=matcher.fingerprint())
    _prune(state, now)
    _save(out, state)
    return Outcome('succeeded', 'processed' if work.written or any(work.counts.values()) else 'nothing_new', counts)


# ---- production ports --------------------------------------------------------

def _carr(repo: Path) -> Callable[[str, dict], dict]:
    def call(verb: str, args: dict) -> dict:
        try:
            done = subprocess.run([str(repo / 'run.sh'), 'call', verb, '-'], input=json.dumps(args), cwd=repo,
                                  capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            raise CarrError(code='timeout') from None
        except OSError as error:
            raise CarrError(code='transport_oversized' if error.errno == errno.E2BIG else 'transport_error') from None
        if done.returncode:
            code = 'setup_failed' if done.returncode == 2 else 'transport_error'
            for prefix in ('TOOL ERROR ', 'RPC ERROR '):
                if prefix in (done.stderr or ''):
                    try:
                        payload, _ = json.JSONDecoder().raw_decode(done.stderr.split(prefix, 1)[1].lstrip())
                        if isinstance(payload, (dict, str)):
                            code = _reply_error(payload if isinstance(payload, dict) else {'error': payload})
                    except ValueError:
                        pass
                    break
            raise CarrError(code=code)
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


def _contacts(repo: Path) -> Callable[[], list]:
    def read() -> list:
        from lib.loadpy import load_module_from_path
        matcher = load_module_from_path('calendar_touch_matcher', str(repo / 'tools/calendar-touch-matcher.py'))
        rows = []
        try:
            for view, id_col, name_col, org_col in matcher.RECORD_VIEWS:
                _, found = matcher.read_view(view)
                for r in found:
                    email = str(r.get('Email') or '').strip().lower()
                    if '@' in email and not r.get('_out_of_market'):
                        rows.append(Contact(email, str(r.get(id_col) or '').strip(),
                                            str(r.get(name_col) or r.get(org_col) or '').strip()))
        except (Exception, SystemExit):
            raise CarrError(code='contacts_read_failed') from None
        if not rows:
            raise CarrError(code='contacts_empty')
        return rows
    return read


def _decoder(home: Path) -> Callable[[Path, Path, Path], None]:
    def decode(copy: Path, files: Path, db: Path) -> None:
        try:
            done = subprocess.run([str(home / HXPROBE), 'db', str(copy), str(db), str(files)],
                                  capture_output=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired):
            raise DecodeError('hxprobe did not run') from None
        if done.returncode:
            raise DecodeError(f'hxprobe exit {done.returncode}')
    return decode


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    home = Path.home()
    ports = Ports(carr=_carr(repo), contacts=_contacts(repo), next_step=ask_model,
                  decode=_decoder(home), now=lambda: datetime.now(timezone.utc))
    outcome = run(repo, home, ports)
    print(status_line(outcome))
    reason = 'full_disk_access_required' if outcome.reason.startswith('grant Full Disk Access') else outcome.reason
    print('outlook-intake-receipt ' + json.dumps({'state': outcome.state, 'reason': reason, 'counts': outcome.counts}))
    return EXIT[outcome.state]
