"""Send an email or save a calendar event from Joe's New Outlook for Mac, by code.

New Outlook's AppleScript model has no Exchange account and HxStore.hxd is a
read cache, so the only write path is the app's own window, driven through the
cua-driver accessibility CLI (lib/outlook_ui.py). One call is one item:

1. Refuse an idempotency key the private ledger already holds, and refuse an
   item the decoded mailbox already shows (same subject and recipient sent in
   the last 24 hours; same subject and start for an event).
2. Open the compose or event form, fill it, and read every field back. Any
   mismatch discards the form.
3. Write the key to the ledger as "pressing", then press Send or Save exactly
   once. The press needs the window in front, so it waits for Joe to have been
   idle for five minutes and gives the front back to the app that had it.
4. Confirm the window closed, then confirm the item in a fresh decode of the
   mailbox. Anything short of that is a distinct result that is never retried.
5. Log the item to the named CARR record with log-activity.

Nothing with an address, a subject or a body reaches stdout, the run row or a
launchd log; the ledger holds keys, states and a fingerprint only.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Callable, Protocol
from urllib.parse import quote
import uuid

from lib import outlook_ui as ui

ACCOUNT = 'sender@example.test'
OUT = Path('out/outlook-send')
LEDGER = 'ledger.jsonl'
IDLE_SECONDS = 300
DUPLICATE_WINDOW = 24 * 3600
CLOCK_SKEW = 5
RECORD_REF = re.compile(r'^[CLV]-\d')
ADDRESS = re.compile(r"^[\w.+'-]+@[\w-]+(?:\.[\w-]+)+$")
BUNDLE = 'com.microsoft.Outlook'
FORM_WALK = 600
MENU_ROLES = ('AXMenuBar', 'AXMenuBarItem', 'AXMenu', 'AXMenuItem')
BODY_ROLE, BODY_LABEL = 'AXTextArea', 'Message body'


class State(Enum):
    DONE = 'done'
    PRESSED_UNCONFIRMED = 'pressed_unconfirmed'
    WINDOW_NOT_CLOSED = 'window_not_closed'
    DUPLICATE_KEY = 'duplicate_key'
    ALREADY_IN_MAILBOX = 'already_in_mailbox'
    FIELD_MISMATCH = 'field_mismatch'
    NOT_IDLE = 'not_idle'
    FAILED = 'failed'

    @property
    def pressed(self) -> bool:
        return self in (State.DONE, State.PRESSED_UNCONFIRMED, State.WINDOW_NOT_CLOSED)


@dataclass(frozen=True)
class Result:
    state: State
    reason: str
    mode: str | None = None
    logged: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.state is State.DONE

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
    def press(self) -> str: ...
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
    close_seconds: float = 30
    confirm_seconds: float = 300
    poll_seconds: float = 20


class UiFailure(Exception):
    """The window did not show the shape this module relies on; the message is a short code."""


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
    return hashlib.sha256(json.dumps(fields(item), sort_keys=True).encode()).hexdigest()


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
        if row.sender.lower() != ACCOUNT or row.sent_unix < since or _norm(row.subject) != _norm(mail.subject):
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


def write(item: Mail | Event, key: str, ports: Ports, record: str | None = None) -> Result:
    """Send one mail or save one event, at most once per key and per mailbox match."""
    out = ports.out
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    with open(out / 'lock', 'a') as lock:
        os.chmod(out / 'lock', 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return Result(State.FAILED, 'another_write_running')
        result = _write(item, key, ports, Ledger(out / LEDGER))
    if result.ok and record:
        result = replace(result, logged=_log(item, key, record, ports))
    return result


def _write(item: Mail | Event, key: str, ports: Ports, ledger: Ledger) -> Result:
    if ledger.states(key):
        return Result(State.DUPLICATE_KEY, 'key_already_pressed')
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
        form.discard()
        return Result(State.FAILED, str(exc))
    if shown != fields(item):
        form.discard()
        wrong = ','.join(sorted(k for k in fields(item) if shown.get(k) != fields(item)[k]))
        return Result(State.FIELD_MISMATCH, f'mismatch:{wrong}')
    idle = ports.idle_seconds()
    if idle is None or idle < IDLE_SECONDS:
        form.discard()
        return Result(State.NOT_IDLE, 'user_active')

    kind, print_ = _kind(item), fingerprint(item)
    ledger.append(key, kind, 'pressing', print_, ports.now())
    pressed_at = ports.now()
    try:
        mode = form.press()
    except UiFailure as exc:
        mode = None
        reason = str(exc)
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
        detail, occurred = item.body, item.start
        if item.location:
            detail = f'Location: {item.location}\n\n{detail}'
    args = {'idempotency_key': str(uuid.uuid5(uuid.NAMESPACE_URL, f'log-activity|{ref}|outlook-{_kind(item)}|{key}')),
            'ref': ref, 'kind': kind, 'summary': summary, 'detail': detail,
            'occurred_at': occurred.isoformat()}
    try:
        reply = ports.carr('log-activity', args)
    except Exception:
        return ('log_failed',)
    return (ref,) if reply.get('ok') is True else ('log_failed',)


# ---- the Outlook window ------------------------------------------------------

def _frontmost() -> str | None:
    done = subprocess.run(['osascript', '-e', 'tell application "System Events" to get bundle identifier '
                           'of first process whose frontmost is true'], capture_output=True, text=True, timeout=30)
    return done.stdout.strip() or None if done.returncode == 0 else None


def _activate(bundle: str) -> None:
    subprocess.run(['osascript', '-e', f'tell application id "{bundle}" to activate'],
                   capture_output=True, timeout=30)


def mailto(mail: Mail) -> str:
    query = '&'.join(f'{k}={quote(v, safe="@")}' for k, v in
                     (('cc', ','.join(mail.cc)), ('bcc', ','.join(mail.bcc)),
                      ('subject', mail.subject), ('body', mail.body)) if v)
    return f'mailto:{quote(",".join(mail.to), safe="@,")}?{query}'


def _open_url(url: str) -> None:
    """Hand a URL to Outlook itself; the system mailto handler is never consulted or changed."""
    done = subprocess.run(['open', '-g', '-a', ui.APP, url], capture_output=True, timeout=60)
    if done.returncode:
        raise UiFailure('open_failed')


class Window:
    """One compose or event window, read and pressed by accessibility only."""

    def __init__(self, pid: int, window_id: int, waits: ui.Waits):
        self.pid, self.window_id, self.waits = pid, window_id, waits

    def elements(self) -> list[dict]:
        state = ui.cua('get_window_state', {'pid': self.pid, 'window_id': self.window_id,
                                            'include_screenshot': False, 'max_elements': FORM_WALK})
        if str(state.get('degraded_reason') or '').startswith('ax_window_unresolved'):
            raise UiFailure('ax_window_unresolved')
        return [e for e in state.get('elements') or [] if e.get('role') not in MENU_ROLES]

    def closed(self) -> bool:
        try:
            return all(w['window_id'] != self.window_id for w in ui.cua('list_windows', {'pid': self.pid})
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
            ui.cua('click', {'pid': self.pid, 'element_token': self._button(self.elements(), 'Discard')['element_token']})
            time.sleep(self.waits.settle_seconds)
            if not self.closed():
                confirm = [e for e in self.elements() if e.get('role') == 'AXButton' and e.get('label') == 'Discard'
                           and e.get('depth', 0) > 1]
                if len(confirm) == 1:
                    ui.cua('click', {'pid': self.pid, 'element_token': confirm[0]['element_token']})
                    time.sleep(self.waits.settle_seconds)
        except (ui.CuaError, UiFailure):
            pass

    def press(self) -> str:
        """Press the form's one commit item with the window in front, then give the front back."""
        previous = _frontmost()
        try:
            ui.cua('bring_to_front', {'pid': self.pid, 'window_id': self.window_id})
            time.sleep(self.waits.settle_seconds)
            ui.cua('invoke_menu', {'pid': self.pid, 'window_id': self.window_id, 'path': self.COMMIT})
        except ui.CuaError as exc:
            raise UiFailure('press_failed') from exc
        finally:
            if previous and previous != BUNDLE:
                _activate(previous)
        return 'front'


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
    COMMIT = ['Draft', 'Send']

    def read(self) -> dict[str, str]:
        elements = self.elements()
        title = next((e.get('label') or '' for e in elements if e.get('role') == 'AXWindow'), '')
        if not title.endswith(f' • {ACCOUNT}'):
            raise UiFailure('wrong_account')
        return {'to': _recipients(elements, 'To Recipients'), 'cc': _recipients(elements, 'Cc Recipients'),
                'bcc': _recipients(elements, 'Bcc Recipients'),
                'subject': _value(elements, 'AXTextField', 'Subject'),
                'body': _value(elements, BODY_ROLE, BODY_LABEL).rstrip('\n')}


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
        fresh = [w for w in ui.cua('list_windows', {'pid': pid}).get('windows') or []
                 if w['window_id'] not in before and w.get('on_current_space')
                 and min((w.get('bounds') or {}).get(k, 0) for k in ('width', 'height')) >= 400]
        if len(fresh) == 1:
            return fresh[0]['window_id']
        if len(fresh) > 1 or time.monotonic() >= deadline:
            raise UiFailure('form_window_not_found')
        time.sleep(waits.poll_seconds)


def open_form(item: Mail | Event, waits: ui.Waits = ui.Waits()) -> Window:
    try:
        pid, main = _main_window(waits)
        before = {w['window_id'] for w in ui.cua('list_windows', {'pid': pid}).get('windows') or []}
        if isinstance(item, Mail):
            _open_url(mailto(item))
            window: Window = ComposeWindow(pid, _new_window(pid, before, waits), waits)
        else:
            ui.cua('invoke_menu', {'pid': pid, 'window_id': main['window_id'], 'path': ['File', 'New', 'Event']})
            window = EventWindow(pid, _new_window(pid, before, waits), waits, item)
            window.fill()
        time.sleep(waits.settle_seconds)
        return window
    except ui.CuaError as exc:
        raise UiFailure('cua_driver_unavailable') from exc


class DecodedMailbox:
    """A fresh hxprobe decode of a private copy of HxStore.hxd, through the intake job's decoder."""

    def __init__(self, repo: Path, home: Path):
        self.repo, self.home = repo, home

    def snapshot(self) -> Snapshot:
        from lib import outlook_intake as intake
        out = self.repo / OUT
        _, mails, events = intake._decode(self.home / intake.STORE, out, intake._decoder(self.home))
        return Snapshot(
            tuple(SentRow(m['sender'] or '', m['recipients'], m['subject'] or '', m['sent_unix']) for m in mails),
            tuple(EventRow(e['subject'] or '', e['start_unix']) for e in events))


def default_ports(repo: Path | None = None) -> Ports:
    from lib import outlook_intake, outlook_triage
    repo = repo or Path(__file__).resolve().parents[1]
    return Ports(open_form=open_form, mailbox=DecodedMailbox(repo, Path.home()),
                 idle_seconds=outlook_triage.hid_idle_seconds, carr=outlook_intake._carr(repo),
                 out=repo / OUT)


# ---- public interface --------------------------------------------------------

def send(to, cc, bcc, subject: str, body: str, *, idempotency_key: str, record: str | None = None,
         ports: Ports | None = None) -> Result:
    """Send one email from Joe's carr.us Outlook. See the module docstring for the guarantees."""
    mail = Mail(_addresses(to), _addresses(cc), _addresses(bcc), subject, body)
    if not mail.to or not subject.strip():
        raise ValueError('an email needs a To address and a subject')
    return write(mail, idempotency_key, ports or default_ports(), record)


def create_event(subject: str, start: datetime, end: datetime, location: str, body: str,
                 attendees=(), *, idempotency_key: str, record: str | None = None,
                 ports: Ports | None = None) -> Result:
    """Save one event on Joe's carr.us calendar; with attendees the save sends invitations."""
    if start.tzinfo is None or end.tzinfo is None or end <= start or not subject.strip():
        raise ValueError('an event needs a subject and a timezone-aware start before its end')
    event = Event(subject, start, end, location, body, _addresses(attendees))
    return write(event, idempotency_key, ports or default_ports(), record)


EXIT = {State.DONE: 0, State.NOT_IDLE: 75, State.PRESSED_UNCONFIRMED: 3, State.WINDOW_NOT_CLOSED: 3}


def main(argv: list[str]) -> int:
    """`run.sh outlook-send|outlook-event --json FILE`: one item from a 0600 JSON file, so no address
    or body sits in argv or shell history. The status line names the state only."""
    import argparse
    parser = argparse.ArgumentParser(prog='run.sh outlook-send')
    parser.add_argument('kind', choices=('send', 'event'))
    parser.add_argument('--json', required=True, type=Path)
    args = parser.parse_args(argv)
    spec = json.loads(args.json.read_text())
    key, record = spec.pop('idempotency_key'), spec.pop('record', None)
    if args.kind == 'send':
        result = send(spec.get('to', ()), spec.get('cc', ()), spec.get('bcc', ()), spec['subject'],
                      spec.get('body', ''), idempotency_key=key, record=record)
    else:
        result = create_event(spec['subject'], datetime.fromisoformat(spec['start']),
                              datetime.fromisoformat(spec['end']), spec.get('location', ''),
                              spec.get('body', ''), spec.get('attendees', ()), idempotency_key=key, record=record)
    print(result.line(args.kind))
    return EXIT.get(result.state, 1)
