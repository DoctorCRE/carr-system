"""Outlook intake: Joe's mail and calendar into the record layer, by code.

Every 30 minutes from 07:00 to 19:00 (com.carr.outlook-intake) this reads a
private copy of the new Outlook's HxStore.hxd, never the live file, decodes it
with the pinned hxprobe fork (vendor-patches/hxprobe-calendar-and-bodies.patch),
and writes what it finds through the named record-layer verbs:

- an email to or from a known address becomes log-activity email_in/email_out
  with the full deduplicated plain-text body as its detail;
- a past meeting becomes log-activity meeting, tour or call by its title;
- a meeting in the next seven days becomes a dated ball (set-next-action);
- one isolated haiku call per new matched email proposes a next step, which
  becomes a ball when the record has none or only an older one.

Matching is deterministic: one record with that exact address, else one exact,
unmerged record name; anything else is reported as unmatched. Records are never
created, merged or renamed. Every write carries
uuid5(NAMESPACE_URL, "<verb>|<ref>|<source id>") so a retry replays.

An email whose body is still only Outlook's preview is logged with the preview,
flagged preview_only, and listed in out/outlook-intake/needs-refresh.json for the
evening triage job to open. The file is a JSON array, mode 0600, one object per
email:

    {"message_id": "<opaque intake id>", "subject_hash": "<sha256 hex of the subject>",
     "sent_utc": "<ISO 8601>", "sender_email": "<address>", "opened_at": "<ISO 8601, triage adds it>"}

Triage finds the email by sender_email and subject_hash and adds opened_at; this
job owns every other field and keeps opened_at when it rewrites the file. Once a
listed email decodes with its full body, the next run logs that text as a
log-activity note (activities cannot be updated in place: the verb refuses a
reused key with new arguments) and drops the entry.

The store copy and decoded database live in a 0700 temporary folder under
out/outlook-intake/ and are deleted before the run ends. The report and state
are 0600 there too; nothing with a name, address or body reaches the status
line, the launchd log or the run row.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
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

STORE = Path('Library/Group Containers/UBF8T346G9.Office/Outlook/Outlook 15 Profiles/Main Profile/HxStore.hxd')
HXPROBE = Path('carr-local/hxstore-reverse-engineering/hxprobe/target/release/hxprobe')
OUT = Path('out/outlook-intake')
REFRESH_LIST = 'needs-refresh.json'
STATE = 'state.json'
LOCAL = zoneinfo.ZoneInfo('America/Chicago')
INTERNAL_DOMAIN = 'carr.us'
WRITE_VERBS = frozenset({'log-activity', 'set-next-action'})
RECORD_REF = re.compile(r'^[CLV]-')
EXIT = {'succeeded': 0, 'skipped': 78, 'failed': 1}

BACKFILL = timedelta(hours=48)
LOOKBACK = timedelta(hours=6)
UPCOMING = timedelta(days=7)
REFRESH_DAYS = timedelta(days=7)
COPY_WINDOW = 86_400
VERIFIED_MIN = 0.95
MODEL_BODY_CHARS = 12_000
BALL_OWNERS = re.compile(r'^ball → (joe|joe-local):')


class CarrError(RuntimeError):
    """The record layer refused or could not be reached."""


class DecodeError(RuntimeError):
    """hxprobe could not decode the store copy."""


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
    decode: Callable[[Path, Path], None]
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


def _private_write(path: Path, text: str) -> None:
    temp = path.with_name(path.name + '.tmp')
    with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as handle:
        handle.write(text)
    os.chmod(temp, 0o600)
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


def _decode(store: Path, out: Path, decode: Callable[[Path, Path], None]):
    work = Path(tempfile.mkdtemp(dir=out))
    try:
        copy = work / 'store.hxd'
        with open(store, 'rb') as src, os.fdopen(os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as dst:
            shutil.copyfileobj(src, dst)
        db = work / 'store.db'
        decode(copy, db)
        return _load(db)
    finally:
        shutil.rmtree(work, ignore_errors=True)


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
    def __init__(self, contacts: list, carr: Callable[[str, dict], dict]):
        self.by_email: dict[str, set] = {}
        for c in contacts:
            if '@' in c.email and not _internal(c.email) and RECORD_REF.match(c.ref):
                self.by_email.setdefault(c.email.lower(), set()).add(c.ref)
        self.carr = carr
        self.names: dict[str, str | None] = {}

    def ref(self, address: str, name: str | None) -> str | None:
        refs = self.by_email.get(address, set())
        if len(refs) == 1:
            return next(iter(refs))
        if refs or not name:
            return None
        if name not in self.names:
            hits = [p for p in self.carr('find', {'query': name}).get('parties') or []
                    if p.get('name') == name and not p.get('merged') and RECORD_REF.match(p.get('ref') or '')]
            self.names[name] = hits[0]['ref'] if len({h['ref'] for h in hits}) == 1 else None
        return self.names[name]


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
    def __init__(self, ports: Ports, state: dict, now: datetime):
        self.ports, self.state, self.now = ports, state, now
        self.today = now.astimezone(LOCAL).date()
        self.counts = {k: 0 for k in ('emails', 'events_past', 'events_upcoming', 'logged', 'preview_only',
                                      'full_text_notes', 'balls_set', 'balls_kept', 'unmatched',
                                      'model_failed', 'writes_failed')}
        self.written: list[tuple[str, str, str, str]] = []
        self.unmatched: set[tuple[str, str]] = set()
        self.balls: dict[str, int] = {}

    def write(self, verb: str, args: dict, who: str, note: str = '') -> bool:
        try:
            self.ports.carr(verb, args)
        except CarrError:
            self.counts['writes_failed'] += 1
            return False
        self.written.append((who, args['ref'], verb, note))
        return True

    def open_ball(self, ref: str) -> int | None:
        """When the newest open ball on ref was set (unix), or None."""
        if ref in self.balls:
            return self.balls[ref]
        closed_after = None
        for row in sorted(self.ports.carr('catch-me-up', {'ref': ref, 'limit': 30}).get('timeline') or [],
                          key=lambda r: r.get('occurred_at') or '', reverse=True):
            at = int(datetime.fromisoformat(row['occurred_at']).timestamp())
            if row.get('verb') in ('complete-action', 'log-outreach'):
                closed_after = closed_after or at
            elif row.get('verb') == 'set-next-action' and BALL_OWNERS.match(row.get('summary') or ''):
                return None if closed_after and closed_after >= at else at
        return None

    def ball(self, ref: str, who: str, description: str, due_on: str, item_at: int, source: str,
             only_if_none: bool = False) -> None:
        existing = self.open_ball(ref)
        if existing is not None and (only_if_none or existing >= item_at):
            self.counts['balls_kept'] += 1
            self.written.append((who, ref, 'set-next-action', 'kept newer ball'))
            return
        note = 'no prior ball' if existing is None else 'replaced ball'
        args = {'idempotency_key': key('set-next-action', ref, source), 'ref': ref,
                'description': description, 'due_on': due_on}
        if self.write('set-next-action', args, who, note):
            self.counts['balls_set'] += 1
            self.balls[ref] = item_at

    # emails

    def emails(self, mails: list[dict], matcher: Matcher, since: int) -> None:
        done = self.state.setdefault('mail_done', {})
        pending = self.state.setdefault('pending_full', {})
        for m in mails:
            if m['sent_unix'] < since or m['sent_unix'] > self.now.timestamp() + 300:
                continue
            if any(abs(t - m['sent_unix']) <= COPY_WINDOW for t in done.get(m['ck'], [])):
                continue
            m['outbound'] = _internal(m['sender'])
            parties = ([(a, None) for a in _addresses(m['recipients']) if not _internal(a)] if m['outbound']
                       else [(m['sender'].lower(), _clean_name(m['sender_name'], m['subject']))])
            if not parties:
                done.setdefault(m['ck'], []).append(m['sent_unix'])
                continue
            self.counts['emails'] += 1
            refs = []
            for address, name in parties:
                ref = matcher.ref(address, name)
                if ref is None:
                    self.unmatched.add((name or '(no name)', _domain(address)))
                elif ref not in refs:
                    refs.append(ref)
            self.counts['unmatched'] += len(parties) - len(refs)
            if not refs:
                done.setdefault(m['ck'], []).append(m['sent_unix'])
                continue
            who = _clean_name(m['sender_name'], m['subject']) or m['sender'].lower()
            kind = 'email_out' if m['outbound'] else 'email_in'
            label = f'Email to {", ".join(a for a, _ in parties)}' if m['outbound'] else f'Email from {who}'
            ok = all([self.write('log-activity', {
                'idempotency_key': key('log-activity', ref, m['ident']), 'ref': ref, 'kind': kind,
                'summary': f'{label}: {m["subject"] or "(no subject)"}', 'detail': m['text'],
                'occurred_at': _iso(m['sent_unix'])}, who, '' if m['full'] else 'preview_only') for ref in refs])
            if not ok:
                continue
            self.counts['logged'] += 1
            done.setdefault(m['ck'], []).append(m['sent_unix'])
            if not m['full']:
                self.counts['preview_only'] += 1
                pending[m['ident']] = {'refs': refs, 'ck': m['ck'], 'sent': m['sent_unix'],
                                       'sender_email': m['sender'].lower(),
                                       'subject_hash': _hash(m['subject'] or '')}
            self.propose(m, refs[0], who)

    def propose(self, m: dict, ref: str, who: str) -> None:
        try:
            reply = parse_reply({'is_error': False, 'structured_output': self.ports.next_step(m, self.today)})
        except Exception:
            self.counts['model_failed'] += 1
            return
        if reply['next_step']:
            self.ball(ref, who, reply['next_step'], reply['due_on'] or self.today.isoformat(),
                      m['sent_unix'], f'next|{m["ident"]}')

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
                    del pending[ident]
                continue
            m = match[0]
            ok = all([self.write('log-activity', {
                'idempotency_key': key('log-activity', ref, f'fullbody|{ident}'), 'ref': ref, 'kind': 'note',
                'summary': f'Full text of email: {m["subject"] or "(no subject)"}', 'detail': m['text'],
                'occurred_at': _iso(p['sent'])}, p['sender_email'], 'full text follow-up') for ref in p['refs']])
            if ok:
                self.counts['full_text_notes'] += 1
                del pending[ident]

    # calendar

    def events(self, events: list[dict], matcher: Matcher, since: int) -> None:
        logged = self.state.setdefault('events_logged', {})
        balled = self.state.setdefault('events_balled', {})
        now = int(self.now.timestamp())
        for e in sorted(events, key=lambda e: e['start_unix']):
            source = f'{e["goid"]}|{e["start_unix"]}'
            past = since <= e['end_unix'] <= now
            upcoming = now < e['start_unix'] <= now + UPCOMING.total_seconds()
            if not (past and source not in logged) and not (upcoming and source not in balled):
                continue
            people = [(email, name) for name, email in e['attendees'] if email and not _internal(email)]
            org = (e['organizer_email'] or '').lower()
            if org and not _internal(org) and org not in [p for p, _ in people]:
                people.append((org, e['organizer_name']))
            refs: list[tuple[str, str]] = []
            for email, name in people:
                ref = matcher.ref(email, _clean_name(name, e['subject']))
                if ref is None:
                    self.unmatched.add((name or '(no name)', _domain(email)))
                elif ref not in [r for r, _ in refs]:
                    refs.append((ref, name or email))
            subject = e['subject'] or 'Meeting'
            ok = True
            if past:
                self.counts['events_past'] += 1
                kind = 'tour' if re.search(r'\btour', subject, re.I) else 'call' if re.search(r'\bcall\b', subject, re.I) else 'meeting'
                for ref, name in refs:
                    ok &= self.write('log-activity', {
                        'idempotency_key': key('log-activity', ref, source), 'ref': ref, 'kind': kind,
                        'summary': f'{kind.capitalize()}: {subject}', 'detail': e['body'] or '',
                        'occurred_at': _iso(e['start_unix'])}, name)
                if ok:
                    logged[source] = e['end_unix']
            else:
                self.counts['events_upcoming'] += 1
                start = datetime.fromtimestamp(e['start_unix'], LOCAL)
                for ref, name in refs:
                    self.ball(ref, name, f'{subject} with {name}, {start:%a %b %-d %-I:%M %p}',
                              start.date().isoformat(), now, f'event|{source}', only_if_none=True)
                balled[source] = e['start_unix']

    def report(self, out: Path, refresh_count: int) -> None:
        lines = [f'# Outlook intake {self.now.astimezone(LOCAL):%Y-%m-%d %H:%M %Z}', '', '## Counts', '']
        lines += [f'- {k}: {v}' for k, v in sorted(self.counts.items())]
        lines += [f'- needs_refresh_listed: {refresh_count}', '', '## Writes', '']
        lines += [f'- {who} | {ref} | {verb}' + (f' | {note}' if note else '') for who, ref, verb, note in self.written]
        lines += ['', '## Unmatched', '']
        lines += [f'- {name} | {domain}' for name, domain in sorted(self.unmatched)]
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
        if ident in opened:
            entry['opened_at'] = opened[ident]
        entries.append(entry)
    _private_write(path, json.dumps(entries, indent=2) + '\n')
    return len(entries)


def _prune(state: dict, now: datetime) -> None:
    horizon = now.timestamp() - (BACKFILL + UPCOMING).total_seconds() - 2 * COPY_WINDOW
    state['mail_done'] = {ck: ts for ck, ts in ((ck, [t for t in v if t >= horizon])
                                                 for ck, v in state.get('mail_done', {}).items()) if ts}
    for name in ('events_logged', 'events_balled'):
        state[name] = {k: t for k, t in state.get(name, {}).items() if t >= horizon}


def run(repo: Path, home: Path, ports: Ports) -> Outcome:
    out = repo / OUT
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    lock = open(out / 'lock', 'a')
    os.chmod(out / 'lock', 0o600)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return Outcome('skipped', 'previous_run_active')
        return _run(out, home, ports)
    finally:
        lock.close()


def _run(out: Path, home: Path, ports: Ports) -> Outcome:
    store = home / STORE
    if not store.is_file():
        return Outcome('failed', 'store_missing')
    try:
        state = json.loads((out / STATE).read_text())
    except (OSError, ValueError):
        state = {}
    info = store.stat()
    signature = [info.st_mtime_ns, info.st_size]
    if state.get('store') == signature:
        return Outcome('succeeded', 'store_unchanged')
    now = ports.now()
    try:
        meta, mails, events = _decode(store, out, ports.decode)
    except (DecodeError, sqlite3.Error, OSError):
        return Outcome('failed', 'decode_failed')
    found = meta.get('blocks_found') or 0
    if not found or (meta.get('blocks_verified') or 0) / found < VERIFIED_MIN:
        return Outcome('failed', 'store_verification_low')

    high = state.get('high_water')
    since = int((datetime.fromtimestamp(high, timezone.utc) - LOOKBACK if high else now - BACKFILL).timestamp())
    mails = _best_copies(mails)
    work = Run(ports, state, now)
    done = state.get('mail_done', {})
    nowu = now.timestamp()
    new_mail = any(since <= m['sent_unix'] <= nowu + 300
                   and not any(abs(t - m['sent_unix']) <= COPY_WINDOW for t in done.get(m['ck'], []))
                   for m in mails)
    new_event = any((since <= e['end_unix'] <= nowu and f'{e["goid"]}|{e["start_unix"]}' not in state.get('events_logged', {}))
                    or (nowu < e['start_unix'] <= nowu + UPCOMING.total_seconds()
                        and f'{e["goid"]}|{e["start_unix"]}' not in state.get('events_balled', {}))
                    for e in events)
    pending = state.get('pending_full', {})
    full_cks = {m['ck'] for m in mails if m['full']}
    follow_up = any(p['ck'] in full_cks for p in pending.values())
    if not (new_mail or new_event or follow_up):
        state.update(store=signature, high_water=int(nowu))
        _prune(state, now)
        refresh = _refresh_list(out, state.get('pending_full', {}))
        _private_write(out / STATE, json.dumps(state))
        return Outcome('succeeded', 'nothing_new', {'needs_refresh_listed': refresh})

    try:
        matcher = Matcher(ports.contacts(), ports.carr)
        work.full_text(mails)
        work.emails(mails, matcher, since)
        work.events(events, matcher, since)
    except CarrError:
        return Outcome('failed', 'carr_unreachable', work.counts)
    refresh = _refresh_list(out, state.get('pending_full', {}))
    work.report(out, refresh)
    counts = {**work.counts, 'needs_refresh_listed': refresh}
    if work.counts['writes_failed']:
        _private_write(out / STATE, json.dumps(state))
        return Outcome('failed', 'writes_failed', counts)
    state.update(store=signature, high_water=int(nowu))
    _prune(state, now)
    _private_write(out / STATE, json.dumps(state))
    return Outcome('succeeded', 'processed', counts)


# ---- production ports --------------------------------------------------------

def _carr(repo: Path) -> Callable[[str, dict], dict]:
    def call(verb: str, args: dict) -> dict:
        try:
            done = subprocess.run([str(repo / 'run.sh'), 'call', verb, json.dumps(args)], cwd=repo,
                                  capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            raise CarrError(f'{verb} unreachable') from None
        if done.returncode:
            raise CarrError(f'{verb} exit {done.returncode}')
        try:
            reply = json.loads(done.stdout)
        except ValueError:
            raise CarrError(f'{verb} reply unreadable') from None
        if verb in WRITE_VERBS and reply.get('ok') is not True:
            raise CarrError(f'{verb} not acknowledged')
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
            raise CarrError('record contacts unreachable') from None
        if not rows:
            raise CarrError('record contacts empty')
        return rows
    return read


def _decoder(home: Path) -> Callable[[Path, Path], None]:
    def decode(copy: Path, db: Path) -> None:
        files = (home / STORE).parent / 'Files'
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
    return EXIT[outcome.state]
