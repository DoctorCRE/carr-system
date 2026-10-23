#!/usr/bin/python3 -I
"""Privilege-separated Outlook approvals. Install this standalone file as root.

Only the fixed consume command is delegated to the unprivileged sender. Issuance
and instruction registration belong to Joe's separate administrative context.
No imported repository code or caller-selected store participates in that context.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import uuid

HELPER = Path('/Library/PrivilegedHelperTools/com.carr.outlook-approval')
STORE = Path('/private/var/db/carr-outlook-approval')
MAX_LIFETIME = 900
MAX_INPUT = 1024 * 1024
MAIL_FIELDS = {'to', 'cc', 'bcc', 'subject', 'body'}
EVENT_FIELDS = {'subject', 'start', 'end', 'location', 'body', 'attendees'}


class Refused(Exception):
    """Approval is unavailable, invalid, expired, or already used."""


def _identifier(value) -> str:
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise Refused()
    return value


def _digest(binding: dict) -> str:
    if not isinstance(binding, dict) or set(binding) != {'account', 'kind', 'fields', 'operation_key'}:
        raise Refused()
    account, kind, fields = binding['account'], binding['kind'], binding['fields']
    if (not isinstance(account, str) or not re.fullmatch(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+", account)
            or account != account.lower() or kind not in ('send', 'event')
            or not isinstance(binding['operation_key'], str) or not binding['operation_key'].strip()
            or not isinstance(fields, dict) or set(fields) != (MAIL_FIELDS if kind == 'send' else EVENT_FIELDS)
            or any(not isinstance(value, str) for value in fields.values())):
        raise Refused()
    return hashlib.sha256(json.dumps(binding, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def _deadline(expires, now: float, *, bounded: bool = True) -> float:
    if (type(expires) not in (int, float) or not math.isfinite(expires) or expires <= now
            or (bounded and expires > now + MAX_LIFETIME)):
        raise Refused()
    return expires


def _scope_digest(binding: dict) -> str:
    return _digest({**binding, 'operation_key': 'standing-instruction-scope'})


def _trusted(path: Path, *, directory: bool = False) -> None:
    for current in (path, *path.parents):
        info = current.lstat()
        expected = stat.S_ISDIR if current != path or directory else stat.S_ISREG
        if info.st_uid != 0 or info.st_mode & 0o022 or not expected(info.st_mode):
            raise Refused()


class Authority:
    """Root-only issue/consume store. Spent receipts survive sender state resets."""

    def __init__(self, root: Path = STORE):
        if os.geteuid() != 0:
            raise Refused()
        self.root = root
        _trusted(root.parent, directory=True)
        root.mkdir(mode=0o700, exist_ok=True)
        _trusted(root, directory=True)

    def _read(self, name: str) -> dict:
        path = self.root / name
        _trusted(path)
        with path.open() as stream:
            return json.load(stream)

    def _save(self, name: str, value: dict) -> None:
        fd = os.open(self.root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        self._sync()

    def _sync(self) -> None:
        fd = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def register(self, binding: dict, reference: str, name: str, expires: float) -> str:
        now = time.time()
        if (not isinstance(reference, str) or not reference.startswith('decision:')
                or not isinstance(name, str) or not name.strip() or len(name) > 200):
            raise Refused()
        _identifier(reference.removeprefix('decision:'))
        instruction = str(uuid.uuid4())
        self._save('instruction-' + instruction, {'digest': _scope_digest(binding), 'reference': reference,
                   'name': name, 'issued_at': now, 'expires_at': _deadline(expires, now, bounded=False)})
        return instruction

    def _instruction(self, instruction: str, binding: dict, now: float) -> dict:
        row = self._read('instruction-' + _identifier(instruction))
        if row['digest'] != _scope_digest(binding) or not row['issued_at'] <= now < row['expires_at']:
            raise Refused()
        return row

    def issue(self, binding: dict, expires: float, instruction: str | None = None) -> str:
        now, digest = time.time(), _digest(binding)
        basis = {'kind': 'joe_yes'}
        if instruction is not None:
            row = self._instruction(instruction, binding, now)
            expires = min(expires, row['expires_at'])
            basis = {'kind': 'standing_instruction', 'instruction_id': instruction,
                     'reference': row['reference'], 'name': row['name']}
        receipt = str(uuid.uuid4())
        self._save('approval-' + receipt, {'digest': digest, 'issued_at': now,
                   'expires_at': _deadline(expires, now), 'basis': basis})
        return receipt

    def consume(self, receipt: str, binding: dict) -> float:
        receipt, digest = _identifier(receipt), _digest(binding)
        lock_fd = os.open(self.root / 'lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(lock_fd, 'w') as lock:
            _trusted(self.root / 'lock')
            fcntl.flock(lock, fcntl.LOCK_EX)
            row, now = self._read('approval-' + receipt), time.time()
            if row['digest'] != digest or not row['issued_at'] <= now < row['expires_at']:
                raise Refused()
            if row['basis']['kind'] == 'standing_instruction':
                self._instruction(row['basis']['instruction_id'], binding, now)
            elif row['basis'] != {'kind': 'joe_yes'}:
                raise Refused()
            os.rename(self.root / ('approval-' + receipt), self.root / ('spent-' + receipt))
            self._sync()
            return row['expires_at']


def consume(receipt: str | None, binding: dict) -> float:
    """Ask the installed authority, never a sender-supplied verifier or receipt file."""
    try:
        receipt = _identifier(receipt)
        _digest(binding)
        _trusted(HELPER)
        done = subprocess.run(['/usr/bin/sudo', '-n', '--', str(HELPER), 'consume'],
                              input=json.dumps({'approval_id': receipt, 'binding': binding}),
                              capture_output=True, text=True, timeout=10, check=True, cwd='/')
        reply = json.loads(done.stdout)
        if set(reply) != {'expires_at'}:
            raise Refused()
        return _deadline(reply['expires_at'], time.time())
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        raise Refused() from None


def main(argv: list[str]) -> int:
    try:
        if len(argv) != 1 or argv[0] not in ('approve', 'register-instruction', 'issue-standing', 'consume'):
            raise Refused()
        authority = Authority()
        raw = sys.stdin.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise Refused()
        request = json.loads(raw)
        command = argv[0]
        reply: dict[str, str | float]
        if command == 'consume':
            if set(request) != {'approval_id', 'binding'}:
                raise Refused()
            reply = {'expires_at': authority.consume(request['approval_id'], request['binding'])}
        else:
            binding, expires = request['binding'], request['expires_at']
            _digest(binding)
            _deadline(expires, time.time(), bounded=command != 'register-instruction')
            if command != 'issue-standing':
                with open('/dev/tty', 'r+') as terminal:
                    terminal.write(json.dumps(request, indent=2, ensure_ascii=True) + '\n'
                                   'Joe: review every field and expiry above. Type yes to '
                                   + ('record this named standing instruction you gave' if command == 'register-instruction'
                                      else 'approve this exact dispatch') + ': ')
                    terminal.flush()
                    if terminal.readline().strip() != 'yes':
                        raise Refused()
            if command == 'register-instruction':
                reply = {'instruction_id': authority.register(binding, request['reference'], request['name'], expires)}
            else:
                reply = {'approval_id': authority.issue(binding, expires,
                         request['instruction_id'] if command == 'issue-standing' else None)}
        print(json.dumps(reply))
        return 0
    except (Refused, OSError, ValueError, TypeError, KeyError):
        print('approval_refused', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
