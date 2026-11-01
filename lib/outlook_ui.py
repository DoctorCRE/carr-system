"""Drive the New Outlook for Mac window through the cua-driver CLI (accessibility).

The preflight half restarts Outlook into one ordinary, readable mailbox window;
it was lifted from the cancelled agent-based sweep (commit 5f5d83ac5). The
message-list half reads Inbox rows and moves one already-read row to a folder.

Shapes confirmed against live Outlook on 2026-10-20 (read-only probe):
the folder pane is an AXOutline of every folder, and with it showing a walk deep
enough to reach the message list exceeds cua-driver's 20 s AX timeout, so the
pane is hidden (View > Sidebar) while rows are read. invoke_menu reports
"unverifiable", so every view change is confirmed by a fresh walk. The message
list is an AXTable labelled "Message List"; each message is an AXRow whose
AXCell label reads "Sender: NAME, Subject: SUBJECT, ..., DATE, Message preview:
TEXT". The window's own title names the open folder ("Inbox • account"), and
every read the moves rely on checks it. Not yet seen live: an unread row's label, the Inbox unread badge, and the
Move folder picker; those paths fail closed.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import time
from typing import Callable
from xml.parsers.expat import ExpatError

APP = 'Microsoft Outlook'
MAILBOX_TITLE = re.compile(r'. • [^\s@]+@[^\s@]+\.[^\s@]+$')
INBOX_TITLE = re.compile(r'^Inbox • ')
from lib.outlook_settings import load_settings
EXIT = {'succeeded': 0, 'skipped': 78, 'timed_out': 124, 'failed': 1}
SIDEBAR = ['View', 'Sidebar']
QUIT = ['Outlook', 'Quit Outlook']
# Outlook names the selected tab first: "Other Inbox Selected;  Focused Inbox Unselected".
TABS = re.compile(r'(Focused|Other) Inbox Selected;\s*(?:Focused|Other) Inbox Unselected')
SWITCH = {'focused': ['View', 'Switch to Focused'], 'other': ['View', 'Switch to Other']}
LIST_WALK = 1500
PANE_WALK = {'max_elements': 1500, 'max_depth': 3}
BADGE_WALK = 120


@dataclass(frozen=True)
class Outcome:
    state: str
    reason: str
    detail: str = ''


@dataclass(frozen=True)
class Waits:
    quit_seconds: float = 60
    sync_seconds: float = 90
    new_window_seconds: float = 15
    poll_seconds: float = 1
    settle_seconds: float = 2
    move_seconds: float = 10
    render_seconds: float = 8


class CuaError(Exception):
    pass


class ComposeWindowOpen(Exception):
    pass


class UiError(Exception):
    """The window did not show the expected shape; labeled details must be non-secret codes."""

    def __init__(self, detail: str, *, step: str | None = None):
        super().__init__(detail)
        self.step = step


class Moved(Enum):
    MOVED = 'moved'
    FAILED = 'failed'
    GONE = 'gone'
    NEXT_TO_UNREAD = 'next_to_unread'
    WINDOW_CLOSED = 'window_closed'


class Refreshed(Enum):
    OPENED = 'opened'
    NOT_OPENED = 'not_opened'
    UNREAD_SKIPPED = 'unread_skipped'
    WINDOW_CLOSED = 'window_closed'
    NOT_FOUND = 'not_found'
    UNCERTAIN = 'uncertain'


class ReadState(Enum):
    READ = 'read'
    UNREAD = 'unread'
    UNCERTAIN = 'uncertain'


@dataclass(frozen=True)
class Row:
    label: str
    token: str
    selected: bool

    @property
    def sender(self) -> str | None:
        match = re.search(r'Sender: (.*?), (?:Subject: |No subject)', self.label)
        return match.group(1).strip() if match else None

    @property
    def read_state(self) -> ReadState:
        if re.search(r'\bunread\b', self.label, re.I):
            return ReadState.UNREAD
        return ReadState.READ if self.sender else ReadState.UNCERTAIN

    @property
    def key(self) -> str:
        """The label with any unread marker removed: the same email read or unread."""
        return re.sub(r'\s+', ' ', re.sub(r'\bunread\b,?', '', self.label, flags=re.I)).strip()

    @property
    def subject(self) -> str | None:
        """The subject as listed; fields after it are separated by a comma and a run of spaces."""
        if re.search(r'Sender: .*?, No subject,', self.label):
            return ''
        match = re.search(r'Subject: (.*?),\s{2,}', self.label)
        return match.group(1) if match else None

    @property
    def headline(self) -> str:
        """Sender, subject and time: the label without its message preview."""
        return re.sub(r'\s+', ' ', re.split(r',\s*Message preview:', self.label)[0]).strip()


def is_main_window(window: dict) -> bool:
    return bool(MAILBOX_TITLE.search(window.get('title') or ''))


def is_compose_window(window: dict) -> bool:
    """Identify titled drafts, retaining the observed unassigned-window exemption."""
    bounds = window.get('bounds') or {}
    if (window.get('title') == '' and bounds.get('width') == 500
            and bounds.get('height') == 500 and window.get('is_on_screen') is False
            and 'on_current_space' in window and window['on_current_space'] is None):
        return False
    title = window.get('title') or ''
    return bool(re.match(r'^(?:Draft|Compose|New (?:Mail|Message))(?: • |$)', title, re.I)
                or (title and not is_main_window(window) and bounds.get('width', 0) >= 400
                    and bounds.get('height', 0) >= 400))


def is_ordinary_main_window(window: dict) -> bool:
    bounds = window.get('bounds') or {}
    return (is_main_window(window) and window.get('is_on_screen') is True
            and bounds.get('width', 0) >= 400 and bounds.get('height', 0) >= 400)


def _call(tool: str, arguments: dict) -> str:
    try:
        driver = str(load_settings().driver)
        result = subprocess.run([driver, 'call', tool, json.dumps(arguments)],
                                capture_output=True, text=True, timeout=120)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise CuaError(f'{tool}: {type(exc).__name__}') from None
    if result.returncode:
        diagnostic = ''
        try:
            error = json.loads(result.stdout).get('error')
            code = error.get('code') if isinstance(error, dict) else None
            if isinstance(code, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{1,119}', code):
                diagnostic = code
        except (ValueError, AttributeError):
            pass
        if not diagnostic:
            match = re.match(r'^([A-Za-z][A-Za-z0-9_]{0,114}Error)(?::|\s|$)', result.stderr)
            diagnostic = match.group(1) if match else ''
        suffix = f': {diagnostic}' if diagnostic else ''
        raise CuaError(f'{tool}: exit {result.returncode}{suffix}')
    return result.stdout


def cua(tool: str, arguments: dict) -> dict:
    stdout = _call(tool, arguments)
    try:
        value = json.loads(stdout)
    except ValueError as exc:
        raise CuaError(f"{tool}: {type(exc).__name__} {'text' if stdout.strip() else 'empty'}") from None
    if not isinstance(value, dict):
        raise CuaError(f'{tool}: exit 0')
    return value


def act(tool: str, arguments: dict) -> None:
    """One UI action. A zero exit is done whatever it prints; the caller's next walk proves the effect."""
    try:
        value = json.loads(_call(tool, arguments))
    except ValueError:
        return
    if isinstance(value, dict) and (value.get('error') or value.get('refusal')
                                    or value.get('status') in ('refused', 'failed', 'error')):
        raise CuaError(f'{tool}: refused')


def outlook_pid() -> int | None:
    found = subprocess.run(['pgrep', '-x', APP], capture_output=True, text=True)
    pids = found.stdout.split()
    return int(pids[0]) if found.returncode == 0 and pids else None


def _windows(pid: int) -> list[dict]:
    return cua('list_windows', {'pid': pid}).get('windows') or []


def button_matches(element: dict, name: str) -> bool:
    """Match an AX button name before Outlook's comma-separated state suffixes."""
    label = element.get('label') or ''
    if element.get('role') != 'AXButton' or not label or not name:
        return False
    base, separator, state = str(label).partition(', ')
    return base == name and (not separator or all(part in {
        'pressed', 'selected', 'expanded', 'collapsed', 'checked', 'unchecked',
    } for part in state.split(', ')))


def _readable_probe(pid: int, window: dict) -> dict:
    state = cua('get_window_state', {'pid': pid, 'window_id': window['window_id'],
                                     'include_screenshot': False, 'max_elements': LIST_WALK})
    elements = state.get('elements') or []
    return {'unresolved': str(state.get('degraded_reason') or '').startswith('ax_window_unresolved'),
            'new_mail_found': any(button_matches(e, 'New Mail') for e in elements),
            'message_list_found': any(e.get('role') == 'AXTable' and e.get('label') == 'Message List'
                                      for e in elements),
            'element_count': len(elements)}


def _probe_readable(probe: dict) -> bool:
    return not probe['unresolved'] and probe['new_mail_found'] and probe['message_list_found']


def _readable(pid: int, window: dict) -> bool:
    return _probe_readable(_readable_probe(pid, window))


def usable_window(pid: int) -> dict | None:
    return next((w for w in _windows(pid) if is_ordinary_main_window(w) and _readable(pid, w)), None)


def compose_window_open(pid: int, windows: list[dict]) -> bool:
    """True on positive draft evidence: a draft title, or readable Send/Subject controls."""
    for window in windows:
        if is_compose_window(window):
            return True
        bounds = window.get('bounds') or {}
        if (window.get('title') == '' and window.get('is_on_screen') is False
                and bounds.get('height') in (30, 32)):
            continue
        state = cua('get_window_state', {'pid': pid, 'window_id': window['window_id'],
                                         'include_screenshot': False, 'max_elements': LIST_WALK})
        controls = {(element.get('role'), element.get('label'))
                    for element in state.get('elements') or []}
        if (not str(state.get('degraded_reason') or '').startswith('ax_window_unresolved')
                and (any(button_matches(e, 'Send') for e in state.get('elements') or [])
                     or ('AXTextField', 'Subject') in controls)):
            return True
    return False


def _quit(pid: int, windows: list[dict], waits: Waits) -> bool:
    if compose_window_open(pid, windows):
        raise ComposeWindowOpen
    window = next((w for w in windows if is_ordinary_main_window(w)), None)
    fallback_at = time.monotonic()
    if window is not None:
        try:
            act('invoke_menu', {'pid': pid, 'window_id': window['window_id'], 'path': QUIT})
            fallback_at = time.monotonic() + min(2, waits.quit_seconds / 2)
        except (CuaError, KeyError):
            pass
    deadline = time.monotonic() + waits.quit_seconds
    fallback_at = min(fallback_at, deadline)
    while outlook_pid() is not None:
        now = time.monotonic()
        if now >= deadline:
            return False
        if now >= fallback_at:
            if compose_window_open(pid, _windows(pid)):
                raise ComposeWindowOpen
            now = time.monotonic()
            if now >= deadline:
                return False
            try:
                subprocess.run(['osascript', '-e', f'tell application "{APP}" to quit'],
                               capture_output=True, timeout=min(30, deadline - now))
            except subprocess.TimeoutExpired:
                return False
            fallback_at = float('inf')
            continue
        time.sleep(min(waits.poll_seconds, deadline - now, fallback_at - now))
    return True


def window_detail(pid: int | None) -> str:
    if pid is None:
        return 'process_missing'
    try:
        windows = _windows(pid)
    except CuaError:
        return 'windows_unavailable'
    snapshot = []
    for window in windows:
        role = window.get('role')
        subrole = window.get('subrole')
        if role == 'AXSheet' or subrole == 'AXSheet':
            kind = 'sheet'
        elif subrole in ('AXDialog', 'AXSystemDialog') or role == 'AXDialog':
            kind = 'dialog'
        elif is_main_window(window):
            kind = 'main'
        elif is_compose_window(window):
            kind = 'compose'
        else:
            kind = 'dialog'
        bounds = window.get('bounds') or {}
        size = {key: bounds.get(key) if type(bounds.get(key)) in (int, float) else None
                for key in ('width', 'height')}
        visible = window.get('is_on_screen')
        snapshot.append({'kind': kind, **size,
                         'is_on_screen': visible if type(visible) is bool else None})
    return json.dumps({'windows': snapshot}, separators=(',', ':'))


def screen_locked() -> bool:
    """Return positive console lock evidence; an unavailable probe leaves preflight unchanged."""
    try:
        result = subprocess.run(['ioreg', '-a', '-d1', '-k', 'IOConsoleUsers', '-r'],
                                capture_output=True, timeout=5)
        if result.returncode:
            return False
        roots = plistlib.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError, ExpatError):
        return False
    if not isinstance(roots, list):
        return False
    return any(session.get('CGSSessionScreenIsLocked') is True
               for root in roots if isinstance(root, dict)
               if isinstance(root.get('IOConsoleUsers'), list)
               for session in root['IOConsoleUsers']
               if isinstance(session, dict) and session.get('kCGSSessionUserIDKey') == os.getuid())


def prepare_outlook(waits: Waits) -> Outcome | None:
    """Restart Outlook into one ordinary readable main window; None means ready."""
    if screen_locked():
        return Outcome('skipped', 'screen_locked')
    try:
        pid = outlook_pid()
        if pid is not None:
            windows = _windows(pid)
            if any(is_compose_window(w) for w in windows):
                return Outcome('skipped', 'compose_window_open')
            candidates = [w for w in windows if is_ordinary_main_window(w)]
            probes = []
            for candidate in candidates:
                probe = _readable_probe(pid, candidate)
                probes.append(probe)
                if _probe_readable(probe):
                    break
            if candidates and not _probe_readable(probes[-1]):
                return Outcome('skipped', 'no_usable_main_window',
                               'phase=before_quit ordinary_candidates_unreadable '
                               + window_detail(pid) + ' readable_probes='
                               + json.dumps(probes, separators=(',', ':')))
            if not _quit(pid, windows, waits):
                return Outcome('failed', 'outlook_quit_timeout', window_detail(pid))
        launched = subprocess.run(['open', '-g', '-a', APP, '--args',
                                   '-ApplePersistenceIgnoreState', 'YES'],
                                  capture_output=True, timeout=60)
        if launched.returncode:
            return Outcome('failed', 'outlook_launch_failed')
        time.sleep(waits.sync_seconds)
        pid = outlook_pid()
        if pid is None:
            return Outcome('failed', 'outlook_not_running_after_launch')
        if usable_window(pid):
            return None
        windows = _windows(pid)
        if windows:
            try:
                act('invoke_menu', {'pid': pid, 'window_id': windows[0]['window_id'],
                                    'path': ['File', 'New', 'Main Window']})
            except CuaError:
                pass
            time.sleep(waits.new_window_seconds)
        if usable_window(pid):
            return None
        return Outcome('failed', 'no_usable_main_window',
                       'phase=after_launch_and_new_window ' + window_detail(pid))
    except ComposeWindowOpen:
        return Outcome('skipped', 'compose_window_open')
    except CuaError:
        return Outcome('failed', 'cua_driver_unavailable')
    except subprocess.TimeoutExpired:
        return Outcome('failed', 'preflight_command_timeout')


def private_file(path: Path):
    """Append to path, making it 0600 even when it already existed with a wider mode."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, 'a')


class Window:
    """One ordinary Outlook mailbox window; every read is a fresh accessibility walk."""

    def __init__(self, pid: int, window_id: int, waits: Waits):
        self.pid, self.window_id, self.waits = pid, window_id, waits

    def _walk(self, **bounds) -> list[dict]:
        state = cua('get_window_state', {'pid': self.pid, 'window_id': self.window_id,
                                         'include_screenshot': False, **bounds})
        if str(state.get('degraded_reason') or '').startswith('ax_window_unresolved'):
            raise UiError('ax_window_unresolved', step='_walk')
        return state.get('elements') or []

    def _menu(self, path: list[str]) -> None:
        act('invoke_menu', {'pid': self.pid, 'window_id': self.window_id, 'path': path})
        time.sleep(self.waits.settle_seconds)

    def front(self) -> None:
        act('bring_to_front', {'pid': self.pid, 'window_id': self.window_id})

    def select_all_accounts(self) -> bool:
        buttons = [e for e in self._walk(**PANE_WALK)
                   if button_matches(e, 'All Accounts')]
        if len(buttons) != 1:
            return False
        act('click', {'pid': self.pid, 'element_token': buttons[0]['element_token'],
                      'action': 'press'})
        time.sleep(self.waits.settle_seconds)
        return True

    def _folder_pane_visible(self) -> bool:
        return any(e.get('role') == 'AXOutline' for e in self._walk(**PANE_WALK))

    def show_folder_pane(self, visible: bool) -> None:
        for _ in range(2):
            if self._folder_pane_visible() == visible:
                return
            self._menu(SIDEBAR)
        if self._folder_pane_visible() != visible:
            raise UiError('folder_pane_toggle_failed', step='show_folder_pane')

    def _inbox_rows(self) -> tuple[list[dict], list[tuple[dict, list[str]]]]:
        """(walk, the folder pane's Inbox rows as (row, child texts)); needs the folder pane showing."""
        elements = self._walk(max_elements=BADGE_WALK)
        children: dict[int | None, list[dict]] = {}
        for e in elements:
            children.setdefault(e.get('parent_index'), []).append(e)
        found: list[tuple[dict, list[str]]] = []
        for row in (e for e in elements if e.get('role') == 'AXRow'):
            texts = [(c.get('label') or c.get('value') or '').strip()
                     for c in children.get(row['element_index'], [])]
            if 'Inbox' in texts:
                found.append((row, texts))
        if not found:
            raise UiError('inbox_row_not_found', step='_inbox_rows')
        return elements, found

    def select_inbox(self) -> None:
        """Open the Inbox from the folder pane and confirm it by its selected row and the window title."""
        elements, found = self._inbox_rows()
        if not (any(row.get('selected') for row, _ in found) and _in_inbox(elements)):
            act('click', {'pid': self.pid, 'element_token': found[0][0]['element_token']})
            time.sleep(self.waits.settle_seconds)
            elements, found = self._inbox_rows()
        if not (any(row.get('selected') for row, _ in found) and _in_inbox(elements)):
            raise UiError('inbox_not_selected', step='select_inbox')

    def inbox_badge(self) -> int:
        """Unread count on the Inbox folder row; needs the folder pane showing."""
        texts = self._inbox_rows()[1][0][1]
        counts = [int(m.group(1)) for t in texts if (m := re.fullmatch(r'(\d+)(?: unread)?', t))]
        if len(counts) > 1:
            raise UiError('inbox_badge_ambiguous', step='inbox_badge')
        return counts[0] if counts else 0

    def rows(self, require_inbox: bool = True) -> tuple[str | None, list[Row]]:
        """(current Focused/Other tab or None, every message row the list exposes).

        Unless require_inbox is off (search results), a window whose title does not
        name the Inbox raises not_inbox, so a Sent Items or Archive list never passes.
        """
        elements = self._walk(max_elements=LIST_WALK)
        if require_inbox and not _in_inbox(elements):
            raise UiError('not_inbox', step='rows')
        tables = [e for e in elements if e.get('role') == 'AXTable' and e.get('label') == 'Message List']
        if len(tables) != 1:
            raise UiError('message_list_not_found', step='rows')
        table = tables[0]['element_index']
        cells: dict[int, dict] = {}
        for e in elements:
            if e.get('role') == 'AXCell' and e.get('parent_index') is not None:
                cells.setdefault(e['parent_index'], e)
        rows = []
        for e in elements:
            if e.get('role') == 'AXRow' and e.get('parent_index') == table:
                cell = cells.get(e['element_index'], {})
                rows.append(Row(label=cell.get('label') or '', token=cell.get('element_token') or '',
                                selected=bool(cell.get('selected') or e.get('selected'))))
        return _tab(elements), rows

    def _scroll_list(self, direction: str) -> None:
        elements = self._walk(max_elements=LIST_WALK)
        tables = [e for e in elements if e.get('role') == 'AXTable' and e.get('label') == 'Message List']
        if len(tables) != 1 or not tables[0].get('element_token'):
            raise UiError('message_list_not_found', step='scan_rows')
        act('scroll', {'pid': self.pid, 'window_id': self.window_id,
                       'element_token': tables[0]['element_token'],
                       'direction': direction, 'by': 'line', 'amount': 50 if direction == 'up' else 1})
        time.sleep(self.waits.settle_seconds)

    def scan_rows(self, allowed: Callable[[], bool]) -> list[Row]:
        """Collect overlapping viewports using wheel events, with bounded top restoration."""
        observed: dict[str, Row] = {}

        def page(rows: list[Row] | None = None) -> list[Row]:
            if rows is None:
                rows = self.rows()[1]
            if any(not r.sender or r.subject is None for r in rows) or len({r.key for r in rows}) != len(rows):
                raise UiError('coverage_identity_ambiguous', step='scan_rows')
            if any(r.selected and r.read_state is not ReadState.READ for r in rows):
                raise UiError('unread_row_selected', step='scan_rows')
            for row in rows:
                previous = observed.get(row.key)
                if previous is not None and (previous.label, previous.selected) != (row.label, row.selected):
                    raise UiError('coverage_changed', step='scan_rows')
                observed[row.key] = row
            return rows

        def signature(rows: list[Row]) -> list[tuple[str, bool]]:
            return [(r.label, r.selected) for r in rows]

        def top() -> list[Row]:
            deadline = time.monotonic() + 30
            error = None

            def snapshot() -> list[Row]:
                nonlocal error
                rows = self.rows()[1]
                try:
                    page(rows)
                except UiError as exc:
                    error = error or exc
                return rows

            previous = snapshot()
            for _ in range(50):
                if time.monotonic() >= deadline:
                    break
                self._scroll_list('up')
                current = snapshot()
                if signature(previous) == signature(current):
                    if error is not None:
                        raise error
                    return current
                previous = current
            raise UiError('coverage_restore_failed', step='scan_rows')

        first = None
        collected: dict[str, Row] = {}
        try:
            first = top()
            previous = page()
            collected = {r.key: r for r in previous}
            for _ in range(50):
                if not allowed():
                    raise UiError('coverage_deadline', step='scan_rows')
                self._scroll_list('down')
                current = page()
                if signature(current) == signature(previous):
                    if observed.keys() != collected.keys():
                        raise UiError('coverage_changed', step='scan_rows')
                    return list(collected.values())
                old_keys, keys = [r.key for r in previous], [r.key for r in current]
                overlap = next((n for n in range(min(len(old_keys), len(keys)), 0, -1)
                                if old_keys[-n:] == keys[:n]), 0)
                if not overlap or any(r.key in collected for r in current[overlap:]):
                    raise UiError('coverage_identity_ambiguous', step='scan_rows')
                if any(signature([collected[r.key]]) != signature([r]) for r in current[:overlap]):
                    raise UiError('coverage_changed', step='scan_rows')
                collected.update((r.key, r) for r in current)
                previous = current
            raise UiError('coverage_page_limit', step='scan_rows')
        finally:
            restored = top()
            if first is not None and (signature(first) != signature(restored)
                                      or observed.keys() != collected.keys()):
                raise UiError('coverage_changed', step='scan_rows')

    def switch_tab(self, tab: str) -> None:
        before = self.rows()[0]
        if before == tab:
            return
        self._menu(SWITCH[tab])
        after = self.rows()[0]
        if after != tab:
            raise UiError(f'tab_switch_failed requested={tab} before={before} observed={after}',
                          step='switch_tab')

    def move(self, label: str, leaf: str, allowed: Callable[[], bool]) -> Moved:
        """Move the read row with this label to the folder named leaf, then confirm it left the list.

        The caller guarantees the leaf names exactly one folder. Outlook may open
        the next or previous message after a move, so a row is only selected when
        both its neighbours are read too, and only while allowed() still holds.
        The only toolbar button pressed is Move.
        """
        rows = self.rows()[1]
        before = Counter(r.label for r in rows)
        index = next((i for i, r in enumerate(rows)
                      if r.label == label and r.read_state is ReadState.READ), None)
        if index is None:
            return Moved.GONE
        if any(r.read_state is not ReadState.READ for r in rows[max(0, index - 1):index + 2]):
            return Moved.NEXT_TO_UNREAD
        if not allowed():
            return Moved.WINDOW_CLOSED
        act('click', {'pid': self.pid, 'element_token': rows[index].token})
        time.sleep(self.waits.settle_seconds)
        selected = [r for r in self.rows()[1] if r.selected]
        if [r.label for r in selected] != [label]:
            return Moved.FAILED
        elements = self._walk(max_elements=LIST_WALK)
        moves = [e for e in elements if button_matches(e, 'Move')
                 and e.get('depth') == 1]
        if len(moves) != 1:
            return Moved.FAILED
        fields_before = {(e.get('role'), e.get('label')) for e in elements if e.get('role') in FIELDS}
        act('click', {'pid': self.pid, 'element_token': moves[0]['element_token']})
        time.sleep(self.waits.settle_seconds)
        picker = self._walk(max_elements=LIST_WALK)
        fields = [e for e in picker if e.get('role') in FIELDS
                  and (e.get('role'), e.get('label')) not in fields_before]
        if len(fields) != 1:
            return self._abandon()
        act('type_text', {'pid': self.pid, 'element_token': fields[0]['element_token'], 'text': leaf})
        time.sleep(self.waits.settle_seconds)
        entries = [e for e in self._walk(max_elements=LIST_WALK)
                   if e.get('role') in ENTRIES and (e.get('label') or '').strip() == leaf]
        if len(entries) != 1:
            return self._abandon()
        if not allowed():
            return self._abandon(Moved.WINDOW_CLOSED)
        act('click', {'pid': self.pid, 'element_token': entries[0]['element_token']})
        deadline = time.monotonic() + self.waits.move_seconds
        while True:
            time.sleep(self.waits.settle_seconds)
            if Counter(r.label for r in self.rows()[1])[label] < before[label]:
                return Moved.MOVED
            if time.monotonic() >= deadline:
                return self._abandon()

    def search(self, query: str) -> list[Row]:
        """Type into Outlook's search box and return the result rows."""
        fields = sorted((e for e in self._walk(max_elements=LIST_WALK)
                         if e.get('role') == 'AXTextField' and e.get('label') == 'Search'),
                        key=lambda e: e.get('depth', 0))
        if not fields:
            raise UiError('search_field_not_found', step='search')
        act('type_text', {'pid': self.pid, 'element_token': fields[0]['element_token'], 'text': query})
        act('press_key', {'pid': self.pid, 'window_id': self.window_id, 'key': 'return'})
        time.sleep(self.waits.settle_seconds)
        return self.rows(require_inbox=False)[1]

    def end_search(self) -> None:
        act('press_key', {'pid': self.pid, 'window_id': self.window_id, 'key': 'escape'})
        time.sleep(self.waits.settle_seconds)

    def refresh(self, key: str, allowed: Callable[[], bool]) -> Refreshed:
        """Open one listed, already-read email so Outlook downloads its full body.

        An unread email is never selected: it is skipped and refreshed on a later
        night, once the user has read it. The row is selected only while allowed() still
        holds, and counts as opened only when a fresh read shows it alone selected.
        """
        rows = self.rows(require_inbox=False)[1]
        index = next((i for i, r in enumerate(rows) if r.key == key), None)
        if index is None:
            return Refreshed.NOT_FOUND
        if rows[index].read_state is ReadState.UNREAD:
            return Refreshed.UNREAD_SKIPPED
        if rows[index].read_state is ReadState.UNCERTAIN:
            return Refreshed.UNCERTAIN
        if not allowed():
            return Refreshed.WINDOW_CLOSED
        act('click', {'pid': self.pid, 'element_token': rows[index].token})
        time.sleep(self.waits.render_seconds)
        selected = [r.label for r in self.rows(require_inbox=False)[1] if r.selected]
        return Refreshed.OPENED if selected == [rows[index].label] else Refreshed.NOT_OPENED

    def _abandon(self, result: Moved = Moved.FAILED) -> Moved:
        act('press_key', {'pid': self.pid, 'window_id': self.window_id, 'key': 'escape'})
        time.sleep(self.waits.settle_seconds)
        return result


FIELDS = ('AXTextField', 'AXSearchField')
ENTRIES = ('AXRow', 'AXCell', 'AXMenuItem', 'AXButton', 'AXStaticText')


def _in_inbox(elements: list[dict]) -> bool:
    return any(e.get('role') == 'AXWindow' and INBOX_TITLE.match(e.get('label') or '') for e in elements)


def _tab(elements: list[dict]) -> str | None:
    for e in elements:
        match = TABS.search(e.get('label') or '')
        if e.get('role') == 'AXCheckBox' and match:
            return match.group(1).lower()
    return None
