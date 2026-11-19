"""Mail intake's Engine client for emails, attachments, lead suggestions and agendas.

The doors are fixed in the engine repository's docs/new-database.md, "Doors the old
system calls". Intake calls them only when outlook.json sets engine_mail; otherwise
nothing here runs and intake writes the record layer as before.

An email is sent once per Message-ID: when its full text decodes, or with its preview
once FULL_TEXT_WAIT has passed since intake first saw it. Each request stays in the
private intake state until the engine answers it, and a retry derives the same key from
the mailbox, the Message-ID and the step, so a crashed run converges. The other partner's
mailbox derives another key and the engine answers its stored activity. An attachment is
uploaded by hash, then tied to its email and deal in a separate call; a tie the engine
refuses with email_not_stored waits for a later run, and the file counts as done only
once its tie settles. Each run also reads one page of the emails filed to a deal and
writes a links note about each deal for an email with links.

As the door doc says, a 5xx or a lost connection is retried with the same key and any
other 4xx is final: counted and settled, except email_not_stored and a file to upload
again. A refusal of the caller itself (401, 403, an unknown job or unprovisioned actor)
would refuse every request alike, so it fails the run and is tried again next run
instead of settling a mailbox's mail.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Callable, Optional
import urllib.error
from urllib.parse import quote
import urllib.request
import uuid

from lib import email_links
from lib.credential_file import read_env_file

FULL_TEXT_WAIT = timedelta(hours=2)
KEEP = timedelta(days=30)
TIMEOUT = 60
JOB = 'mail_capture'
CREDENTIALS = ('ENGINE_URL', 'ENGINE_ACCESS_CLIENT_ID', 'ENGINE_ACCESS_CLIENT_SECRET')
CALLER = frozenset({'unauthorized', 'unknown_job', 'actor_not_provisioned'})
SLUG = re.compile(r'[a-z][a-z0-9_]{0,63}')


@dataclass(frozen=True)
class Reply:
    status: int
    body: dict

    @property
    def error(self) -> str:
        error = self.body.get('error')
        return error if isinstance(error, str) and SLUG.fullmatch(error) else 'unreadable'


Door = Callable[[str, str, Optional[bytes]], Reply]


def verdict(reply: Reply) -> str:
    """done, settled, later, upload (the file must be sent again), retry, or caller (fix the setup)."""
    if 200 <= reply.status < 300:
        return 'done'
    if reply.status == 0 or reply.status >= 500:
        return 'retry'
    if reply.error == 'email_not_stored':
        return 'later'
    if reply.error == 'file_not_uploaded':
        return 'upload'
    return 'caller' if reply.status in (401, 403) or reply.error in CALLER else 'settled'


def connect(credentials: Path, user: str | None = None, *, job: str = JOB) -> Door:
    """The /jobs entrance behind the Studio's Access service token."""
    info = os.stat(credentials)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise PermissionError('engine credentials must be a 0600 file owned by this user')
    values = read_env_file(credentials, names=CREDENTIALS)
    url, client, secret = (values.get(name) or '' for name in CREDENTIALS)
    if not (url.startswith('https://') and client and secret):
        raise PermissionError('engine credentials need ENGINE_URL (https), ENGINE_ACCESS_CLIENT_ID and SECRET')
    config = Path(__file__).resolve().parents[1] / 'mcp-server/src/engine-client.json'
    headers = {
        'CF-Access-Client-Id': client,
        'CF-Access-Client-Secret': secret,
        'Engine-Job': job,
        'User-Agent': json.loads(config.read_text())['userAgent'],
    }
    if user is not None:
        headers['Engine-For-User'] = user

    def call(method: str, path: str, body: bytes | None) -> Reply:
        kind = 'application/octet-stream' if method == 'PUT' and path.startswith('/jobs/files/') else 'application/json'
        request = urllib.request.Request(url.rstrip('/') + path, data=body, method=method,
                                         headers={**headers, 'Content-Type': kind})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return Reply(response.status, _json(response.read()))
        except urllib.error.HTTPError as error:
            return Reply(error.code, _json(error.read()))
        except OSError:
            return Reply(0, {})
    return call


def _json(raw: bytes) -> dict:
    try:
        body = json.loads(raw)
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def due(state: dict, now: datetime) -> bool:
    """Whether a request is unanswered or a preview's wait has run out, so an unchanged store still runs."""
    book = state.get('engine_mail')
    if not book:
        return False
    at = now.timestamp()
    return (any('outcome' not in e and ('request' in e or at - e['seen'] >= FULL_TEXT_WAIT.total_seconds())
                for e in book['emails'].values())
            or any('outcome' not in f for f in book['files'].values()))


class Mailbox:
    """One mailbox's engine requests, kept in the private intake state under engine_mail."""

    def __init__(self, door: Door, state: dict, now: datetime, owner: str, account: str,
                 counts: dict, save: Callable[[], None]):
        self.door, self.state, self.counts, self.save = door, state, counts, save
        self.at, self.mailbox = int(now.timestamp()), f'{owner}|{account}'
        book = state.setdefault('engine_mail', {'emails': {}, 'files': {}})
        self.emails: dict[str, dict] = book['emails']
        self.files: dict[str, dict] = book['files']
        self.notes: dict[str, dict] = book.setdefault('notes', {})
        self.offers: dict[str, tuple] = {}
        self.filed: list[dict] = []

    def key(self, *parts: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, '|'.join(('engine', self.mailbox, *parts))))

    def count(self, name: str) -> None:
        self.counts[name] = self.counts.get(name, 0) + 1

    def holds(self, message_id: str) -> bool:
        return message_id in self.emails

    def activity_id(self, message_id: str) -> str | None:
        return self.emails.get(message_id, {}).get('activity_id')

    def offer(self, message_id: str, sent: int, ready: bool, rank: tuple, inbound: bool, activity: dict) -> None:
        """One decoded copy of an email; the most complete copy of each Message-ID is the one sent."""
        held = self.offers.get(message_id)
        if held is None or (ready, rank) > held[:2]:
            self.offers[message_id] = (ready, rank, sent, inbound, activity)

    def _stage_emails(self) -> None:
        for message_id, (ready, _, sent, inbound, activity) in self.offers.items():
            entry = self.emails.setdefault(message_id, {'sent': sent, 'seen': self.at})
            if 'request' in entry:
                continue
            if not ready and self.at - entry['seen'] < FULL_TEXT_WAIT.total_seconds():
                self.count('engine_emails_waiting')
                continue
            entry.update(inbound=inbound, request={'idempotency_key': self.key(message_id, 'activity'), **activity})

    def preview_emails(self) -> dict[str, str]:
        self._stage_emails()
        return {mid: entry['request']['happened_at'] for mid, entry in self.emails.items()
                if mid in self.offers and 'request' in entry and 'outcome' not in entry}

    def send_emails(self) -> None:
        self._stage_emails()
        self.save()
        for entry in self.emails.values():
            if 'request' in entry and 'outcome' not in entry:
                self.answer(entry, self.post('/jobs/activities', entry['request']), 'emails')
        self.save()

    def upload(self, source: str, data: bytes, name: str, message_id: str | None, deal_id: str) -> str:
        """Store the bytes: kept (a tie follows), settled (refused for good) or again (read them next run)."""
        sha256 = hashlib.sha256(data).hexdigest()
        reply = self.door('PUT', f'/jobs/files/{sha256}', data)
        if not self.answered(reply):
            return 'again'
        if verdict(reply) == 'settled':
            self.count('engine_files_' + reply.error)
            return 'settled'
        self.files.setdefault(source, {'sent': self.at, 'sha256': sha256, 'name': name,
                                       'message_id': message_id, 'deal_id': deal_id})
        self.save()
        return 'kept'

    def tie_files(self) -> list[str]:
        """Tie each uploaded file to its email and deal. Returns sources whose bytes must be read again."""
        again = []
        for source, entry in self.files.items():
            if 'outcome' in entry:
                continue
            email = self.emails.get(entry['message_id'] or '', {})
            if 'seen' in email and 'outcome' not in email:
                self.count('engine_attachments_waiting')
                continue
            message_id = entry['message_id'] if email.get('outcome') in (None, 'stored', 'existing') else None
            request = {'sha256': entry['sha256'], 'name': entry['name'], 'kind': 'other',
                       'deal_id': entry['deal_id'], **({'message_id': message_id} if message_id else {})}
            request['idempotency_key'] = self.key('attachment', message_id or '', entry['deal_id'], entry['sha256'])
            reply = self.post('/jobs/attachments', request)
            if verdict(reply) == 'upload':
                again.append(source)
            elif verdict(reply) == 'later':
                self.count('engine_attachments_waiting')
            else:
                self.answer(entry, reply, 'attachments')
        for source in again:
            del self.files[source]
        self.save()
        return again

    def file_links(self) -> bool:
        """One page of this mailbox's filed emails, kept as filed. False until every links note is answered.

        The engine pages by activity id, and copied activities keep their old random ids, so a late
        filing can sort before any cursor: the scan restarts after its last page, and a note is
        written only for an email of the last KEEP days, the notes kept for that long."""
        after = self.state.get('engine_links_cursor')
        reply = self.door('GET', '/jobs/filed-emails?limit=200' + (f'&after={quote(after)}' if after else ''), None)
        if not self.answered(reply):
            return False
        self.filed = [mail for mail in reply.body.get('emails') or []
                      if isinstance(mail, dict) and mail.get('activity_id') and mail.get('happened_at')]
        for mail in self.filed:
            happened = int(datetime.fromisoformat(mail['happened_at']).timestamp())
            links = sorted(email_links.extract_links(mail.get('body') or ''))
            if not links or happened < self.at - KEEP.total_seconds():
                continue
            for deal_id in mail.get('deal_ids') or []:
                self.notes.setdefault(f"{mail['activity_id']}|{deal_id}", {'sent': happened, 'request': {
                    'idempotency_key': self.key('links', mail['activity_id'], deal_id), 'kind': 'note',
                    'happened_at': mail['happened_at'], 'about': [{'deal_id': deal_id}],
                    'summary': '\n'.join(['Links from matched email', *(f'{kind}: {url}' for kind, url in links)])}})
        self.save()
        for note in self.notes.values():
            if 'outcome' not in note:
                self.answer(note, self.post('/jobs/activities', note['request']), 'notes')
        self.save()
        if any('outcome' not in note for note in self.notes.values()):
            return False
        self.state['engine_links_cursor'] = reply.body.get('next_cursor')
        return True

    def post(self, path: str, request: dict) -> Reply:
        return self.door('POST', path, json.dumps(request).encode())

    def publish_agenda(self, snapshot: dict) -> str:
        reply = self.door('PUT', '/jobs/calendar/agenda', json.dumps(snapshot).encode())
        result = verdict(reply)
        if result == 'done':
            return 'published'
        if reply.status == 409 and reply.error == 'agenda_superseded':
            self.count('engine_agenda_superseded')
            return 'superseded'
        self.count('engine_agenda_' + reply.error)
        return 'retry' if result in ('retry', 'caller') else 'refused'

    def propose_lead(self, request: dict) -> str | None:
        reply = self.post('/jobs/proposals/email-lead', request)
        if not self.answered(reply):
            return None
        if verdict(reply) == 'settled':
            self.count('engine_leads_' + reply.error)
            return 'refused'
        if reply.body.get('status') == 'known_party':
            return 'known_party'
        if reply.body.get('existing') is True or reply.body.get('status') == 'already_queued':
            return 'already_queued'
        return 'proposed'

    def answered(self, reply: Reply) -> bool:
        """Whether the engine answered for good; a retry or a caller refusal is counted as the run's failure."""
        judged = verdict(reply)
        if judged in ('retry', 'caller'):
            self.count('writes_failed' if judged == 'retry' else 'requests_blocked')
        return judged in ('done', 'settled')

    def answer(self, entry: dict, reply: Reply, what: str) -> None:
        if not self.answered(reply):
            entry['attempts'] = entry.get('attempts', 0) + 1
            return
        if verdict(reply) == 'settled':
            entry['outcome'] = reply.error
            self.count(f'engine_{what}_{reply.error}')
            return
        entry['outcome'] = 'existing' if reply.body.get('existing') is True else 'stored'
        if isinstance(reply.body.get('activity_id'), str):
            entry['activity_id'] = reply.body['activity_id']
        self.count(f'engine_{what}_{entry["outcome"]}')
        if what == 'emails' and entry.get('inbound'):
            self.state['last_email_in_filed_at'] = self.at

    def prune(self) -> None:
        oldest = self.at - KEEP.total_seconds()
        for table in (self.emails, self.files, self.notes):
            for name in [name for name, entry in table.items()
                         if max(entry['sent'], entry.get('seen', 0)) < oldest
                         and (table is not self.notes or 'outcome' in entry)]:
                del table[name]
