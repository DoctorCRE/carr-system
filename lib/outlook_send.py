"""Prepare one Outlook mail or calendar draft, or dispatch it when Joe asked.

A default call leaves a field-verified draft open. Explicit send intent requires
native confirmation after suppression, the ten-item daily limit, duplicate checks
and idle checks. A local delivery latch prevents ambiguous effects from being
repeated. Successful delivery is confirmed against Sent Items or the calendar
before logging an activity. Diagnostics contain no addresses, subjects or bodies.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import fcntl
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import stat
import sqlite3
import tempfile
import time
from typing import Callable, Protocol
from urllib.parse import quote
import uuid

from lib import outlook_ui as ui
from zoneinfo import ZoneInfo

ACCOUNT_CONFIG = Path.home() / '.config/carr/outlook-account'
OUT = Path('out/outlook-send')
LEDGER = 'ledger.jsonl'
IDLE_SECONDS = 300
DUPLICATE_WINDOW = 24 * 3600
CLOCK_SKEW = 5
RECORD_REF = re.compile(r'^(?:[CL]-\d+|V-[A-Z0-9]+(?:-[A-Z0-9]+)*)$')
ADDRESS = re.compile(r"^[\w.+'-]+@[\w-]+(?:\.[\w-]+)+$")
BUNDLE = 'com.microsoft.Outlook'
FORM_WALK = 600
MENU_ROLES = ('AXMenuBar', 'AXMenuBarItem', 'AXMenu', 'AXMenuItem')
BODY_ROLE, BODY_LABEL = 'AXTextArea', 'Message body'


class State(Enum):
    DRAFT = 'draft'
    DONE = 'done'
    ACTIVITY_LOG_FAILED = 'activity_log_failed'
    PRESSED_UNCONFIRMED = 'pressed_unconfirmed'
    WINDOW_NOT_CLOSED = 'window_not_closed'
    DUPLICATE_KEY = 'duplicate_key'
    ALREADY_IN_MAILBOX = 'already_in_mailbox'
    FIELD_MISMATCH = 'field_mismatch'
    NOT_IDLE = 'not_idle'
    FAILED = 'failed'
    REFUSED = 'refused'

    @property
    def pressed(self) -> bool:
        return self in (State.DONE, State.ACTIVITY_LOG_FAILED, State.PRESSED_UNCONFIRMED, State.WINDOW_NOT_CLOSED)


@dataclass(frozen=True)
class Result:
    state: State
    reason: str
    mode: str | None = None
    logged: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.state in (State.DONE, State.DRAFT)

    def line(self, kind: str) -> str:
        logged = f' logged={",".join(self.logged)}' if self.logged else ''
        return f'outlook-{kind} {self.state.value} {self.reason} mode={self.mode or "-"}{logged}'


@dataclass(frozen=True)
class Mail:
    to: tuple[str, ...]
    cc: tuple[str, ...]
    bcc: tuple[str, ...]
    subject: str
    body: str

    @property
    def recipients(self) -> tuple[str, ...]:
        return self.to + self.cc + self.bcc


@dataclass(frozen=True)
class Event:
    subject: str
    start: datetime
    end: datetime
    location: str
    body: str
    attendees: tuple[str, ...]


@dataclass(frozen=True)
class SentRow:
    sender: str
    recipients: str | None
    subject: str
    sent_unix: int


@dataclass(frozen=True)
class EventRow:
    subject: str
    start_unix: int


@dataclass(frozen=True)
class Snapshot:
    sent: tuple[SentRow, ...]
    events: tuple[EventRow, ...]


class Mailbox(Protocol):
    def snapshot(self) -> Snapshot: ...


class Form(Protocol):
    """One open compose or event window in Outlook."""

    def read(self) -> dict[str, str]: ...
    def discard(self) -> None: ...
    def press(self, *, approved_at: float | None = None) -> str: ...
    def closed(self) -> bool: ...


@dataclass
class Ports:
    open_form: Callable[[Mail | Event], Form]
    mailbox: Mailbox
    idle_seconds: Callable[[], float | None]
    carr: Callable[[str, dict], dict]
    now: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    out: Path = OUT
    contacts: Callable[[], list[tuple[str, str, str]]] = lambda: []
    close_seconds: float = 30
    confirm_seconds: float = 300
    poll_seconds: float = 20


class UiFailure(Exception):
    """The window did not show the shape this module relies on; the message is a short code."""


class UserActive(UiFailure):
    """Foreground fallback stopped before dispatch because input resumed."""


def _transport_failure(exc: Exception) -> UiFailure:
    digest = hashlib.sha256(str(exc).encode()).hexdigest()[:12]
    return UiFailure(f'transport_{type(exc).__name__}_{digest}')


def _run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, check=True, **kwargs)
    except (OSError, subprocess.SubprocessError) as exc:
        raise _transport_failure(exc) from None


def account(path: Path = ACCOUNT_CONFIG) -> str:
    """Read the local account identity; never put a real address in source."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
        raise ValueError('account config must be an owned 0600 regular file')
    value = path.read_text().strip().lower()
    if not ADDRESS.fullmatch(value):
        raise ValueError('account config must contain one address')
    return value


def _norm(text: str) -> str:
    return re.sub(r'\s+', ' ', text or '').strip().casefold()


def _addresses(values) -> tuple[str, ...]:
    if isinstance(values, str):
        values = [values]
    found = tuple(v.strip().lower() for v in values or () if v and v.strip())
    bad = [a for a in found if not ADDRESS.match(a)]
    if bad:
        raise ValueError('invalid address')
    return found


def fields(item: Mail | Event) -> dict[str, str]:
    """The exact text each form field must read back."""
    if isinstance(item, Mail):
        return {'to': '; '.join(item.to), 'cc': '; '.join(item.cc), 'bcc': '; '.join(item.bcc),
                'subject': item.subject, 'body': item.body}
    return {'subject': item.subject, 'start': item.start.isoformat(), 'end': item.end.isoformat(),
            'location': item.location, 'body': item.body, 'attendees': '; '.join(item.attendees)}


def fingerprint(item: Mail | Event) -> str:
    identity = (['send', account(), _norm(item.subject), sorted(item.recipients)] if isinstance(item, Mail)
                else ['event', account(), _norm(item.subject), int(item.start.timestamp())])
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


# ---- ledger ------------------------------------------------------------------

class Ledger:
    """Append-only 0600 JSONL of keys that reached the press. A key on it is never pressed again."""

    def __init__(self, path: Path):
        self.path = path

    def states(self, key: str) -> list[str]:
        try:
            lines = self.path.read_text().splitlines()
        except FileNotFoundError:
            return []
        return [row['state'] for row in map(json.loads, lines) if row.get('key') == key]

    def contains_item(self, print_: str, since: float) -> bool:
        if not self.path.exists():
            return False
        return any(row.get('fingerprint') == print_ and row['at'] >= since
                   for row in map(json.loads, self.path.read_text().splitlines()))

    def append(self, key: str, kind: str, state: str, print_: str, at: float) -> None:
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, (json.dumps({'key': key, 'kind': kind, 'state': state, 'fingerprint': print_,
                                      'at': int(at)}) + '\n').encode())
            os.fsync(fd)
        finally:
            os.close(fd)


# ---- mailbox matching --------------------------------------------------------

def mail_in_mailbox(snapshot: Snapshot, mail: Mail, since: float) -> bool:
    """A message Joe sent since `since` with this subject to any of these recipients.

    hxprobe sometimes leaves recipients empty or misreads them, so an unreadable
    recipient list counts as a match: a refused send is safe, a duplicate is not.
    A draft decodes with its first recipient as sender, so it never matches.
    """
    wanted = set(mail.recipients)
    for row in snapshot.sent:
        if row.sender.lower() != account() or row.sent_unix < since or _norm(row.subject) != _norm(mail.subject):
            continue
        listed = set(re.findall(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+", (row.recipients or '').lower()))
        if not listed or listed & wanted:
            return True
    return False


def event_in_mailbox(snapshot: Snapshot, event: Event) -> bool:
    start = int(event.start.timestamp())
    return any(_norm(row.subject) == _norm(event.subject) and row.start_unix == start for row in snapshot.events)


def _in_mailbox(snapshot: Snapshot, item: Mail | Event, since: float) -> bool:
    return mail_in_mailbox(snapshot, item, since) if isinstance(item, Mail) else event_in_mailbox(snapshot, item)


# ---- one write ---------------------------------------------------------------

def _kind(item: Mail | Event) -> str:
    return 'send' if isinstance(item, Mail) else 'event'


def _confirm_dispatch(item: Mail | Event) -> bool:
    try:
        shown = fields(item)
        text = (f'Account: {account()}\nTo: {shown.get("to", shown.get("attendees", ""))}\n'
                f'Cc: {shown.get("cc", "")}\nBcc: {shown.get("bcc", "")}\nSubject: {item.subject}')
        if isinstance(item, Event):
            text += f'\nStart: {shown["start"]}\nEnd: {shown["end"]}\nLocation: {item.location}'
        script = (f'set answer to display dialog {json.dumps(text, ensure_ascii=False)} '
                  'with title "Confirm Outlook send" buttons {"Cancel", "Send"} '
                  'default button "Cancel" cancel button "Cancel" giving up after 120\n'
                  'return (not (gave up of answer)) and (button returned of answer is "Send")')
        reply = _run(['osascript', '-'], input=script, capture_output=True, text=True, timeout=125)
        return reply.stdout.strip() == 'true'
    except (UiFailure, OSError, ValueError):
        return False


def _write_transport(item: Mail | Event, key: str, ports: Ports, record: str | None = None,
                     *, send_requested: bool = False) -> Result:
    """Send one mail or save one event, at most once per key and per mailbox match."""
    if not isinstance(key, str) or not key.strip():
        raise ValueError('idempotency_key is required')
    out = ports.out
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    with open(out / 'lock', 'a') as lock:
        os.chmod(out / 'lock', 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return Result(State.FAILED, 'another_write_running')
        result = _write(item, key, ports, Ledger(out / LEDGER), send_requested)
    if result.state is State.DONE:
        result = replace(result, logged=_log_matches(item, key, record, ports))
        if 'log_failed' in result.logged:
            result = replace(result, state=State.ACTIVITY_LOG_FAILED, reason='confirmed_in_mailbox:log_failed')
    return result


def _write(item: Mail | Event, key: str, ports: Ports, ledger: Ledger, send_requested: bool) -> Result:
    if send_requested and (ledger.states(key) or ledger.contains_item(fingerprint(item),
            ports.now() - DUPLICATE_WINDOW if isinstance(item, Mail) else 0)):
        return Result(State.DUPLICATE_KEY, 'key_already_pressed')
    if send_requested:
        refused = _send_refusal(item, ports, ledger)
        if refused:
            return Result(State.REFUSED, refused)
        try:
            before = ports.mailbox.snapshot()
        except Exception:
            return Result(State.FAILED, 'mailbox_unreadable')
        if _in_mailbox(before, item, ports.now() - DUPLICATE_WINDOW):
            return Result(State.ALREADY_IN_MAILBOX, 'matching_item_found')
    idle = ports.idle_seconds()
    if idle is None:
        return Result(State.FAILED, 'idle_time_unreadable')
    if idle < IDLE_SECONDS:
        return Result(State.NOT_IDLE, 'user_active')
    try:
        form = ports.open_form(item)
    except UiFailure as exc:
        return Result(State.FAILED, str(exc))
    try:
        shown = form.read()
    except UiFailure as exc:
        return _discard(form, Result(State.FAILED, str(exc)))
    if shown != fields(item):
        wrong = ','.join(sorted(k for k in fields(item) if shown.get(k) != fields(item)[k]))
        return _discard(form, Result(State.FIELD_MISMATCH, f'mismatch:{wrong}'))
    if not send_requested:
        return Result(State.DRAFT, 'draft_ready')
    idle = ports.idle_seconds()
    if idle is None or idle < IDLE_SECONDS:
        return _discard(form, Result(State.NOT_IDLE, 'user_active'))
    dialog_started = time.monotonic()
    if not _confirm_dispatch(item):
        return Result(State.DRAFT, 'send_not_confirmed')
    confirmed_at = time.monotonic()
    idle = _idle_seconds()
    approved_at = confirmed_at if idle is not None and idle <= confirmed_at - dialog_started else None
    try:
        if form.read() != shown:
            return Result(State.DRAFT, 'approval_fields_changed')
    except UiFailure:
        return Result(State.DRAFT, 'approval_fields_unreadable')

    kind, print_ = _kind(item), fingerprint(item)
    ledger.append(key, kind, 'pressing', print_, ports.now())
    pressed_at = ports.now()
    try:
        mode = form.press(approved_at=approved_at)
    except UserActive:
        return _discard(form, Result(State.NOT_IDLE, 'user_active'))
    except (UiFailure, ui.CuaError, OSError, subprocess.SubprocessError) as exc:
        mode = None
        reason = str(exc) if isinstance(exc, UiFailure) else str(_transport_failure(exc))
    else:
        reason = 'pressed'
    if not _wait(form.closed, ports.close_seconds, ports):
        ledger.append(key, kind, State.WINDOW_NOT_CLOSED.value, print_, ports.now())
        return Result(State.WINDOW_NOT_CLOSED, reason if mode is None else 'window_still_open', mode)

    def confirmed() -> bool:
        try:
            return _in_mailbox(ports.mailbox.snapshot(), item, pressed_at - CLOCK_SKEW)
        except Exception:
            return False

    if not _wait(confirmed, ports.confirm_seconds, ports):
        ledger.append(key, kind, State.PRESSED_UNCONFIRMED.value, print_, ports.now())
        return Result(State.PRESSED_UNCONFIRMED, 'not_in_mailbox_yet', mode)
    ledger.append(key, kind, State.DONE.value, print_, ports.now())
    return Result(State.DONE, 'confirmed_in_mailbox', mode)


def _send_refusal(item: Mail | Event, ports: Ports, ledger: Ledger) -> str | None:
    recipients = sorted(set(item.recipients if isinstance(item, Mail) else item.attendees))
    if sum(address != account() and address.rsplit('@', 1)[-1] != 'carr.us' for address in recipients) > 1:
        return 'one_to_one_required'
    if recipients:
        try:
            rows = ports.carr('mail-contact-policy', {'emails': recipients}).get('recipients')
        except Exception:
            return 'contact_policy_unreadable'
        if (not isinstance(rows, list) or len(rows) != len(recipients)
                or any(not isinstance(row, dict) or not isinstance(row.get('email'), str)
                       or type(row.get('blocked')) is not bool for row in rows)
                or {row['email'] for row in rows} != set(recipients)):
            return 'contact_policy_unreadable'
        if any(row['blocked'] for row in rows):
            return 'do_not_contact'
    zone = ZoneInfo('America/Chicago')
    today = datetime.fromtimestamp(ports.now(), zone).date()
    if ledger.path.exists():
        attempts = [json.loads(line) for line in ledger.path.read_text().splitlines()]
        if sum(row['state'] == 'pressing' and datetime.fromtimestamp(row['at'], zone).date() == today
               for row in attempts) >= 10:
            return 'daily_cap'
    return None


def _discard(form: Form, result: Result) -> Result:
    try:
        form.discard()
        if form.closed():
            return result
    except (UiFailure, ui.CuaError):
        pass
    return Result(State.FAILED, 'discard_failed:' + result.reason)


def _wait(check: Callable[[], bool], seconds: float, ports: Ports) -> bool:
    deadline = ports.now() + seconds
    while True:
        if check():
            return True
        if ports.now() >= deadline:
            return False
        ports.sleep(ports.poll_seconds if seconds > ports.poll_seconds else 1)


# ---- record layer ------------------------------------------------------------

def resolve_record(record: str, carr: Callable[[str, dict], dict]) -> str | None:
    """An L-/C-/V- ref as given, else the one live record whose name is exactly `record`."""
    if RECORD_REF.match(record):
        return record
    hits = {p['ref'] for p in carr('find', {'query': record}).get('parties') or []
            if p.get('name') == record and not p.get('merged') and RECORD_REF.match(p.get('ref') or '')}
    return hits.pop() if len(hits) == 1 else None


def _log_matches(item: Mail | Event, key: str, record: str | None, ports: Ports) -> tuple[str, ...]:
    if record:
        return _log(item, key, record, ports)
    addresses = set(item.recipients if isinstance(item, Mail) else item.attendees)
    try:
        matched: dict[str, set[tuple[str, str]]] = {}
        for email, name, ref in ports.contacts():
            if email in addresses:
                matched.setdefault(email, set()).add((name, ref))
        records = set()
        for matches in matched.values():
            if len(matches) == 1:
                name, expected_ref = next(iter(matches))
                if resolve_record(name, ports.carr) == expected_ref:
                    records.add(expected_ref)
    except (Exception, SystemExit):
        return ('record_lookup_failed',)
    if not records:
        return ('record_not_matched',)
    return tuple(value for ref in sorted(records) for value in _log(item, key, ref, ports))


def _log(item: Mail | Event, key: str, record: str, ports: Ports) -> tuple[str, ...]:
    try:
        ref = resolve_record(record, ports.carr)
    except Exception:
        return ('record_lookup_failed',)
    if ref is None:
        return ('record_not_matched',)
    if isinstance(item, Mail):
        kind, summary = 'email_out', f'Email to {", ".join(item.recipients)}: {item.subject}'
        detail, occurred = item.body, datetime.fromtimestamp(ports.now(), timezone.utc)
    else:
        kind, summary = 'meeting', f'Calendar event: {item.subject}'
        detail, occurred = (f'Start: {item.start.isoformat()}\nEnd: {item.end.isoformat()}\n'
                            f'Location: {item.location}\nAttendees: {", ".join(item.attendees)}\n\n{item.body}'), item.start
    args = {'idempotency_key': str(uuid.uuid5(uuid.NAMESPACE_URL, f'log-activity|{ref}|outlook-{_kind(item)}|{key}')),
            'ref': ref, 'kind': kind, 'summary': summary, 'detail': detail,
            'occurred_at': occurred.isoformat()}
    try:
        reply = ports.carr('log-activity', args)
    except Exception:
        return ('log_failed',)
    return (ref,) if reply.get('ok') is True else ('log_failed',)


# ---- the Outlook window ------------------------------------------------------

def _cua(tool: str, args: dict) -> dict:
    try:
        result = ui.cua(tool, args)
    except (ui.CuaError, OSError, subprocess.SubprocessError) as exc:
        raise _transport_failure(exc) from None
    if result.get('status') in ('refused', 'failed', 'error') or result.get('refusal') or result.get('error'):
        raise UiFailure('cua_action_refused')
    return result


def _frontmost() -> str | None:
    done = _run(['osascript', '-e', 'tell application "System Events" to get bundle identifier '
                           'of first process whose frontmost is true'], capture_output=True, text=True, timeout=30)
    return done.stdout.strip() or None


def _activate(bundle: str) -> None:
    _run(['osascript', '-e', f'tell application id "{bundle}" to activate'],
                   capture_output=True, timeout=30)


def mailto(mail: Mail) -> str:
    query = '&'.join(f'{k}={quote(v, safe="@")}' for k, v in
                     (('cc', ','.join(mail.cc)), ('bcc', ','.join(mail.bcc)),
                      ('subject', mail.subject), ('body', mail.body)) if v)
    return f'mailto:{quote(",".join(mail.to), safe="@,")}?{query}'


def _mailto_handler() -> str:
    """Read LaunchServices without changing the registered handler."""
    core = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    services = ctypes.CDLL('/System/Library/Frameworks/CoreServices.framework/CoreServices')
    core.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    core.CFStringCreateWithCString.restype = ctypes.c_void_p
    core.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    core.CFRelease.argtypes = [ctypes.c_void_p]
    services.LSCopyDefaultHandlerForURLScheme.argtypes = [ctypes.c_void_p]
    services.LSCopyDefaultHandlerForURLScheme.restype = ctypes.c_void_p
    scheme = core.CFStringCreateWithCString(None, b'mailto', 0x08000100)
    handler = None
    try:
        handler = services.LSCopyDefaultHandlerForURLScheme(scheme)
        buffer = ctypes.create_string_buffer(1024)
        return buffer.value.decode().lower() if handler and core.CFStringGetCString(handler, buffer, 1024, 0x08000100) else ''
    finally:
        if handler:
            core.CFRelease(handler)
        core.CFRelease(scheme)


def _open_url(url: str) -> None:
    _run(['open', '-g', url], capture_output=True, timeout=60)


class Window:
    """One compose or event window, read and pressed by accessibility only."""

    commit_label: str

    def __init__(self, pid: int, window_id: int, waits: ui.Waits):
        self.pid, self.window_id, self.waits = pid, window_id, waits

    def elements(self) -> list[dict]:
        state = _cua('get_window_state', {'pid': self.pid, 'window_id': self.window_id,
                                            'include_screenshot': False, 'max_elements': FORM_WALK})
        if str(state.get('degraded_reason') or '').startswith('ax_window_unresolved'):
            raise UiFailure('ax_window_unresolved')
        return [e for e in state.get('elements') or [] if e.get('role') not in MENU_ROLES]

    def closed(self) -> bool:
        try:
            return all(w['window_id'] != self.window_id for w in _cua('list_windows', {'pid': self.pid})
                       .get('windows') or [])
        except ui.CuaError:
            return False

    def _button(self, elements: list[dict], label: str) -> dict:
        hits = [e for e in elements if e.get('role') == 'AXButton' and e.get('label') == label and e.get('depth') == 1]
        if len(hits) != 1:
            raise UiFailure(f'{label.lower().replace(" ", "_")}_button_not_found')
        return hits[0]

    def discard(self) -> None:
        """Press Discard and confirm the window went; a draft left behind is a failure to report."""
        try:
            _cua('click', {'pid': self.pid, 'element_token': self._button(self.elements(), 'Discard')['element_token']})
            time.sleep(self.waits.settle_seconds)
            if not self.closed():
                confirm = [e for e in self.elements() if e.get('role') == 'AXButton' and e.get('label') == 'Discard'
                           and e.get('depth', 0) > 1]
                if len(confirm) == 1:
                    _cua('click', {'pid': self.pid, 'element_token': confirm[0]['element_token']})
                    time.sleep(self.waits.settle_seconds)
        except (ui.CuaError, UiFailure):
            pass

    def press(self, *, approved_at: float | None = None) -> str:
        """One accepted press. Only an explicit pre-dispatch refusal allows front fallback."""
        target = self._button(self.elements(), self.commit_label)
        try:
            result = ui.cua('click', {'pid': self.pid, 'element_token': target['element_token']})
        except (ui.CuaError, OSError, subprocess.SubprocessError) as exc:
            raise _transport_failure(exc) from None
        refusal = result.get('refusal') or {}
        if not refusal and result.get('status') not in ('refused', 'failed', 'error') and not result.get('error'):
            return 'background'
        if refusal.get('code') != 'background_unavailable':
            raise UiFailure('press_refused')
        required = IDLE_SECONDS if approved_at is None else min(IDLE_SECONDS, time.monotonic() - approved_at)
        idle = _idle_seconds()
        if idle is None or idle < required:
            raise UserActive('user_active')
        previous = _frontmost()
        if previous is None:
            raise UiFailure('frontmost_unreadable')
        required = IDLE_SECONDS if approved_at is None else min(IDLE_SECONDS, time.monotonic() - approved_at)
        idle = _idle_seconds()
        if idle is None or idle < required:
            raise UserActive('user_active')
        try:
            _cua('bring_to_front', {'pid': self.pid, 'window_id': self.window_id})
            time.sleep(self.waits.settle_seconds)
            if not isinstance(self, ComposeWindow):
                target = self._button(self.elements(), self.commit_label)
            required = IDLE_SECONDS if approved_at is None else min(IDLE_SECONDS, time.monotonic() - approved_at)
            idle = _idle_seconds()
            if idle is None or idle < required:
                raise UserActive('user_active')
            if isinstance(self, ComposeWindow):
                _cua('invoke_menu', {'pid': self.pid, 'window_id': self.window_id, 'path': ['Draft', 'Send']})
            else:
                _cua('click', {'pid': self.pid, 'element_token': target['element_token']})
        finally:
            if previous and previous != BUNDLE:
                _activate(previous)
        return 'front'


def _idle_seconds() -> float | None:
    from lib.outlook_triage import hid_idle_seconds
    return hid_idle_seconds()


def _recipients(elements: list[dict], label: str) -> str:
    field = [e for e in elements if e.get('role') == 'AXTextField' and e.get('label') == label]
    if len(field) != 1:
        return ''
    chips = [e.get('label') or '' for e in elements
             if e.get('parent_index') == field[0]['element_index'] and e.get('role') == 'AXStaticText']
    return '; '.join(c.split(',', 1)[0].strip().lower() for c in chips)


def _value(elements: list[dict], role: str, label: str) -> str:
    hits = [e for e in elements if e.get('role') == role and e.get('label') == label]
    return (hits[0].get('value') or '') if len(hits) == 1 else ''


class ComposeWindow(Window):
    commit_label = 'Send'

    def fill(self, item: Mail) -> None:
        for key, value in fields(item).items():
            label = {'to': 'To Recipients', 'cc': 'Cc Recipients', 'bcc': 'Bcc Recipients',
                     'subject': 'Subject', 'body': BODY_LABEL}[key]
            hits = [e for e in self.elements() if e.get('label') == label
                    and e.get('role') in ('AXTextField', 'AXTextArea')]
            if not hits and key in ('cc', 'bcc'):
                toggles = [e for e in self.elements() if e.get('label') == key.capitalize()
                           and e.get('role') == 'AXButton']
                if len(toggles) == 1:
                    _cua('click', {'pid': self.pid, 'element_token': toggles[0]['element_token']})
                    hits = [e for e in self.elements() if e.get('label') == label and e.get('role') == 'AXTextField']
            if len(hits) != 1:
                raise UiFailure('compose_field_not_found:' + key)
            _cua('set_value', {'pid': self.pid, 'element_token': hits[0]['element_token'], 'value': value})
            if key in ('to', 'cc', 'bcc') and value:
                _cua('press_key', {'pid': self.pid, 'window_id': self.window_id, 'key': 'tab'})

    def read(self) -> dict[str, str]:
        elements = self.elements()
        title = next((e.get('label') or '' for e in elements if e.get('role') == 'AXWindow'), '')
        if not title.endswith(f' • {account()}'):
            raise UiFailure('wrong_account')
        return {'to': _recipients(elements, 'To Recipients'), 'cc': _recipients(elements, 'Cc Recipients'),
                'bcc': _recipients(elements, 'Bcc Recipients'),
                'subject': _value(elements, 'AXTextField', 'Subject'),
                'body': _value(elements, BODY_ROLE, BODY_LABEL)}


class EventWindow(Window):
    """AX event form. Unknown/ambiguous fields fail closed before Save or invitations.

    This field contract still requires a discard-only live verification on the
    installed Outlook version; offline fixtures do not establish native labels.
    """
    LABELS = {'subject': 'Subject', 'start': 'Start', 'end': 'End', 'location': 'Location',
              'body': 'Message body', 'attendees': 'Required attendees'}

    def __init__(self, pid: int, window_id: int, waits: ui.Waits, item: Event):
        super().__init__(pid, window_id, waits)
        self.item = item
        self.commit_label = 'Send' if item.attendees else 'Save'

    def _field(self, elements: list[dict], key: str) -> dict:
        hits = [e for e in elements if e.get('label') == self.LABELS[key]
                and e.get('role') in ('AXTextField', 'AXTextArea', 'AXDateTimeArea')]
        if len(hits) != 1:
            raise UiFailure('event_field_not_found:' + key)
        return hits[0]

    def fill(self) -> None:
        for key, value in fields(self.item).items():
            target = self._field(self.elements(), key)
            _cua('set_value', {'pid': self.pid, 'element_token': target['element_token'], 'value': value})
            if self._field(self.elements(), key).get('value', '') != value:
                raise UiFailure('event_field_mismatch:' + key)

    def read(self) -> dict[str, str]:
        elements = self.elements()
        title = next((e.get('label', '') for e in elements if e.get('role') == 'AXWindow'), '')
        if not title.endswith(f' • {account()}'):
            raise UiFailure('wrong_account')
        return {key: self._field(elements, key).get('value') or '' for key in self.LABELS}


def _main_window(waits: ui.Waits) -> tuple[int, dict]:
    pid = ui.outlook_pid()
    found = ui.usable_window(pid) if pid else None
    if not found:
        blocked = ui.prepare_outlook(waits)
        if blocked:
            raise UiFailure(blocked.reason)
        pid = ui.outlook_pid()
        found = ui.usable_window(pid) if pid else None
    if not found or pid is None:
        raise UiFailure('no_usable_main_window')
    return pid, found


def _new_window(pid: int, before: set[int], waits: ui.Waits) -> int:
    deadline = time.monotonic() + waits.new_window_seconds
    while True:
        fresh = [w for w in _cua('list_windows', {'pid': pid}).get('windows') or []
                 if w['window_id'] not in before and w.get('on_current_space')
                 and min((w.get('bounds') or {}).get(k, 0) for k in ('width', 'height')) >= 400]
        if len(fresh) == 1:
            return fresh[0]['window_id']
        if len(fresh) > 1 or time.monotonic() >= deadline:
            raise UiFailure('form_window_not_found')
        time.sleep(waits.poll_seconds)


def open_form(item: Mail | Event, waits: ui.Waits = ui.Waits()) -> ComposeWindow | EventWindow:
    try:
        pid, main = _main_window(waits)
        windows = _cua('list_windows', {'pid': pid}).get('windows') or []
        if any(w['window_id'] != main['window_id']
               and min((w.get('bounds') or {}).get(k, 0) for k in ('width', 'height')) >= 400
               for w in windows):
            raise UiFailure('compose_window_open')
        before = {w['window_id'] for w in windows}
        window: ComposeWindow | EventWindow
        if isinstance(item, Mail):
            handler = _mailto_handler()
            if handler == BUNDLE.lower():
                _open_url(mailto(item))
                window = ComposeWindow(pid, _new_window(pid, before, waits), waits)
            else:
                main_form = Window(pid, main['window_id'], waits)
                target = main_form._button(main_form.elements(), 'New Mail')
                _cua('click', {'pid': pid, 'element_token': target['element_token']})
                window = ComposeWindow(pid, _new_window(pid, before, waits), waits)
                try:
                    window.fill(item)
                except UiFailure as exc:
                    failure = _discard(window, Result(State.FAILED, str(exc)))
                    raise UiFailure(failure.reason) from None
        else:
            _cua('invoke_menu', {'pid': pid, 'window_id': main['window_id'], 'path': ['File', 'New', 'Event']})
            window = EventWindow(pid, _new_window(pid, before, waits), waits, item)
            try:
                window.fill()
            except UiFailure as exc:
                failure = _discard(window, Result(State.FAILED, str(exc)))
                raise UiFailure(failure.reason) from None
        time.sleep(waits.settle_seconds)
        return window
    except (ui.CuaError, OSError, subprocess.SubprocessError) as exc:
        raise _transport_failure(exc) from None


class DecodedMailbox:
    """Read confirmation metadata through intake's existing private snapshot helper."""

    def __init__(self, home: Path, repo: Path | None = None):
        self.home = home
        self.repo = repo or Path(__file__).resolve().parents[1]

    def snapshot(self) -> Snapshot:
        from lib.outlook_intake import OUT, WORK_PREFIX, _snapshot, _decoder

        out = (self.repo / OUT).resolve()
        out.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(out, 0o700)
        with tempfile.TemporaryDirectory(prefix=WORK_PREFIX, dir=out) as folder:
            work = Path(folder)
            copy, db = work / 'store.hxd', work / 'store.db'
            _snapshot(self.home, work)
            fd = os.open(db, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            _decoder(self.home)(copy, work / 'Files', db)
            os.chmod(db, 0o600)
            con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
            try:
                sent = tuple(SentRow(*row) for row in con.execute(
                    'SELECT DISTINCT m.sender, m.recipients, m.subject, m.sent_unix FROM messages m '
                    'JOIN message_folders mf ON mf.message_id=m.id JOIN folders f ON f.id=mf.folder_id '
                    "WHERE lower(f.name) IN ('sent', 'sent items') AND m.sender IS NOT NULL "
                    'AND m.subject IS NOT NULL AND m.sent_unix IS NOT NULL'))
                events = tuple(EventRow(*row) for row in con.execute(
                    'SELECT subject, start_unix FROM events WHERE subject IS NOT NULL'))
                return Snapshot(sent, events)
            finally:
                con.close()


def default_ports(repo: Path | None = None) -> Ports:
    from lib import outlook_triage
    repo = repo or Path(__file__).resolve().parents[1]

    def carr(verb: str, args: dict) -> dict:
        # stdin keeps message text and addresses out of argv/process listings.
        done = _run([str(repo / 'run.sh'), 'call', verb, '-'], input=json.dumps(args),
                              cwd=repo, capture_output=True, text=True, timeout=120)
        return json.loads(done.stdout)

    def contacts() -> list[tuple[str, str, str]]:
        from lib.loadpy import load_module_from_path
        matcher = load_module_from_path('outlook_send_contacts', str(repo / 'tools/calendar-touch-matcher.py'))
        rows = []
        for view, id_col, name_col, org_col in matcher.RECORD_VIEWS:
            _, found = matcher.read_view(view)
            for row in found:
                if not row.get('_out_of_market'):
                    rows.append((str(row.get('Email') or '').strip().lower(),
                                 str(row.get(name_col) or row.get(org_col) or '').strip(),
                                 str(row.get(id_col) or '').strip()))
        return rows

    return Ports(open_form=open_form, mailbox=DecodedMailbox(Path.home(), repo),
                 idle_seconds=outlook_triage.hid_idle_seconds, carr=carr, contacts=contacts,
                 out=Path.home() / '.local/state/carr/outlook-send')


# ---- public interface --------------------------------------------------------

def send(to, cc, bcc, subject: str, body: str, *, idempotency_key: str, record: str | None = None,
         ports: Ports | None = None, send_requested: bool = False) -> Result:
    """Prepare one email; send it only when Joe requested this item."""
    mail = Mail(_addresses(to), _addresses(cc), _addresses(bcc), subject, body)
    if not mail.to or not subject.strip():
        raise ValueError('an email needs a To address and a subject')
    if type(send_requested) is not bool:
        raise ValueError('send_requested must be a boolean')
    return _write_transport(mail, idempotency_key, ports or default_ports(), record,
                            send_requested=send_requested)


def create_event(subject: str, start: datetime, end: datetime, location: str, body: str,
                 attendees=(), *, idempotency_key: str, record: str | None = None,
                 ports: Ports | None = None, send_requested: bool = False) -> Result:
    """Prepare one event; an explicitly requested save sends its invitations."""
    if start.tzinfo is None or end.tzinfo is None or end <= start or not subject.strip():
        raise ValueError('an event needs a subject and a timezone-aware start before its end')
    event = Event(subject, start, end, location, body, _addresses(attendees))
    if type(send_requested) is not bool:
        raise ValueError('send_requested must be a boolean')
    return _write_transport(event, idempotency_key, ports or default_ports(), record,
                            send_requested=send_requested)


EXIT = {State.DRAFT: 0, State.DONE: 0, State.ACTIVITY_LOG_FAILED: 4, State.NOT_IDLE: 75, State.PRESSED_UNCONFIRMED: 3, State.WINDOW_NOT_CLOSED: 3}


def main(argv: list[str]) -> int:
    """`run.sh outlook-send|outlook-event --json FILE`: one item from a 0600 JSON file, so no address
    or body sits in argv or shell history. The status line names the state only."""
    import argparse
    parser = argparse.ArgumentParser(prog='run.sh outlook-send')
    parser.add_argument('kind', choices=('send', 'event'))
    parser.add_argument('--json', required=True, type=Path)
    parser.add_argument('--send', action='store_true', help="send or save only when Joe asked for this item; default is draft")
    args = parser.parse_args(argv)
    info = args.json.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
        print('outlook-' + args.kind + ' failed input_must_be_owned_0600_file')
        return 1
    spec = json.loads(args.json.read_text())
    key, record = spec.pop('idempotency_key'), spec.pop('record', None)
    if args.kind == 'send':
        result = send(spec.get('to', ()), spec.get('cc', ()), spec.get('bcc', ()), spec['subject'],
                      spec.get('body', ''), idempotency_key=key, record=record, send_requested=args.send)
    else:
        result = create_event(spec['subject'], datetime.fromisoformat(spec['start']),
                              datetime.fromisoformat(spec['end']), spec.get('location', ''),
                              spec.get('body', ''), spec.get('attendees', ()), idempotency_key=key, record=record,
                              send_requested=args.send)
    print(result.line(args.kind))
    return EXIT.get(result.state, 1)
