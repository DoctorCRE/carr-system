"""Mail intake's side of the engine's doors 19 (email in) and 20 (attachments).

The doors are fixed in the engine repository's docs/new-database.md, "Doors the old
system calls". Intake calls them only when outlook.json sets engine_mail; otherwise
nothing here runs and intake writes the record layer as before.

An email is sent once per Message-ID: when its full text decodes, or with its preview
once FULL_TEXT_WAIT has passed since intake first saw it. Each request stays in the
private intake state until the engine answers it, and a retry derives the same key from
the mailbox, the Message-ID and the step, so a crashed run converges. The other partner's
mailbox derives another key and the engine answers its stored activity. An attachment is
uploaded by hash, then tied to its email and deal in a separate call; a tie the engine
refuses with email_not_stored waits for a later run.
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
from typing import Callable
import urllib.error
import urllib.request
import uuid

from lib.credential_file import read_env_file

FULL_TEXT_WAIT = timedelta(hours=2)
KEEP = timedelta(days=30)
TIMEOUT = 60
JOB = 'mail_capture'
CREDENTIALS = ('ENGINE_URL', 'ENGINE_ACCESS_CLIENT_ID', 'ENGINE_ACCESS_CLIENT_SECRET')
# Refusals a retry cannot change: the email or file is settled and counted, never retried.
SETTLED = frozenset({'about_nothing', 'message_id_invalid', 'missing_required', 'invalid_field_value',
                     'not_found', 'database_refused_the_statement', 'filed_to_another_deal',
                     'sha256_mismatch', 'unsupported_file', 'too_large'})
SLUG = re.compile(r'[a-z][a-z0-9_]{0,63}')


@dataclass(frozen=True)
class Reply:
    status: int
    body: dict

    @property
    def error(self) -> str:
        error = self.body.get('error')
        return error if isinstance(error, str) and SLUG.fullmatch(error) else 'unreadable'


Door = Callable[[str, str, bytes], Reply]


def verdict(reply: Reply) -> str:
    """done, settled, later, upload (the file must be sent again), retry or blocked."""
    if 200 <= reply.status < 300:
        return 'done'
    if reply.status == 0 or reply.status >= 500:
        return 'retry'
    if reply.error == 'email_not_stored':
        return 'later'
    if reply.error == 'file_not_uploaded':
        return 'upload'
    return 'settled' if reply.error in SETTLED else 'blocked'


def connect(credentials: Path, user: str) -> Door:
    """The /jobs entrance, behind the Studio's Access service token, stamped mail_capture for user."""
    info = os.stat(credentials)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise PermissionError('engine credentials must be a 0600 file owned by this user')
    values = read_env_file(credentials, names=CREDENTIALS)
    url, client, secret = (values.get(name) or '' for name in CREDENTIALS)
    if not (url.startswith('https://') and client and secret):
        raise PermissionError('engine credentials need ENGINE_URL (https), ENGINE_ACCESS_CLIENT_ID and SECRET')
    headers = {'CF-Access-Client-Id': client, 'CF-Access-Client-Secret': secret,
               'Engine-Job': JOB, 'Engine-For-User': user}

    def call(method: str, path: str, body: bytes) -> Reply:
        kind = 'application/octet-stream' if method == 'PUT' else 'application/json'
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
        self.offers: dict[str, tuple] = {}

    def key(self, *parts: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, '|'.join(('engine', self.mailbox, *parts))))

    def count(self, name: str) -> None:
        self.counts[name] = self.counts.get(name, 0) + 1

    def holds(self, message_id: str) -> bool:
        return message_id in self.emails

    def offer(self, message_id: str, sent: int, ready: bool, rank: tuple, inbound: bool, activity: dict) -> None:
        """One decoded copy of an email; the most complete copy of each Message-ID is the one sent."""
        held = self.offers.get(message_id)
        if held is None or (ready, rank) > held[:2]:
            self.offers[message_id] = (ready, rank, sent, inbound, activity)

    def send_emails(self) -> None:
        for message_id, (ready, _, sent, inbound, activity) in self.offers.items():
            entry = self.emails.setdefault(message_id, {'sent': sent, 'seen': self.at})
            if 'request' in entry:
                continue
            if not ready and self.at - entry['seen'] < FULL_TEXT_WAIT.total_seconds():
                self.count('engine_emails_waiting')
                continue
            entry.update(inbound=inbound, request={'idempotency_key': self.key(message_id, 'activity'), **activity})
        self.save()
        for entry in self.emails.values():
            if 'request' in entry and 'outcome' not in entry:
                self.answer(entry, self.post('/jobs/activities', entry['request']), 'emails')
        self.save()

    def upload(self, source: str, data: bytes, name: str, message_id: str | None, deal_id: str) -> bool:
        """Store the bytes; False when they are still needed by a later run."""
        sha256 = hashlib.sha256(data).hexdigest()
        reply = self.door('PUT', f'/jobs/files/{sha256}', data)
        if verdict(reply) == 'settled':
            self.count('engine_files_' + reply.error)
            return True
        if not self.answered(reply):
            return False
        self.files[source] = {'sent': self.at, 'sha256': sha256, 'name': name,
                              'message_id': message_id, 'deal_id': deal_id}
        self.save()
        return True

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
            message_id = None if email.get('outcome') in SETTLED else entry['message_id']
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

    def post(self, path: str, request: dict) -> Reply:
        return self.door('POST', path, json.dumps(request).encode())

    def answered(self, reply: Reply) -> bool:
        judged = verdict(reply)
        if judged in ('retry', 'blocked', 'later', 'upload'):
            self.count('writes_failed' if judged == 'retry' else 'requests_blocked')
            return False
        return True

    def answer(self, entry: dict, reply: Reply, what: str) -> None:
        if not self.answered(reply):
            entry['attempts'] = entry.get('attempts', 0) + 1
            return
        if verdict(reply) == 'settled':
            entry['outcome'] = reply.error
            self.count(f'engine_{what}_{reply.error}')
            return
        entry['outcome'] = 'existing' if reply.body.get('existing') is True else 'stored'
        self.count(f'engine_{what}_{entry["outcome"]}')
        if what == 'emails' and entry.get('inbound'):
            self.state['last_email_in_filed_at'] = self.at

    def prune(self) -> None:
        oldest = self.at - KEEP.total_seconds()
        for table in (self.emails, self.files):
            for name in [name for name, entry in table.items() if entry['sent'] < oldest]:
                del table[name]
