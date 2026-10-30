"""Nightly Inbox triage: file the user's already-read Inbox email into Outlook folders.

Code only: no agent session and no model. launchd fires `fire()` every 15
minutes from 19:15 to 21:00, and both `fire()` and `run()` refuse any other time. Once a
day is settled (a run that succeeded, or whatever the 21:00 fire ends with) the
remaining fires exit at once without a run row; a skipped or failed earlier run
leaves the later fires to retry. Otherwise `fire()` hands over to
bin/run-scheduled.sh, which runs `main()` and turns its exit code into the run
row (0 succeeded, 78 skipped, 124 timed out, anything else failed). From 21:15 a
run selects nothing more: the clock is checked right before every row it would
select, so the 21:00 fire gets a quarter hour. The row carries no names.

The Inbox is selected from the folder pane and confirmed by the window title
before anything is read, and every move re-checks that title. Unread email is
never selected, so it stays unread: the unread rows are read off the list first
and must add up to the Inbox unread badge before anything is touched, and after
the moves the unread set must be exactly the same, new arrivals included (a
difference fails the run and a later fire retries). Any failed move fails the
run; two in a row stop it at once. The folder tree and the sender table come
from the configured private folder guide, which must be 0600; the report in
out/outlook-triage/ (0700/0600) carries counts and a 12-character sha256 of each
moved row's label, never a sender, subject or folder.

"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, time as clock, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Callable

from lib.outlook_ui import (EXIT, CuaError, Moved, Outcome, ReadState, Row, UiError, Waits,
                            Window, outlook_pid, prepare_outlook, private_file, usable_window)

from lib.outlook_settings import load_settings
from lib.outlook_runtime import receipt_line
OUT = Path('out/outlook-triage')
FIRST_FIRE, LAST_FIRE, WINDOW_CLOSE = clock(19, 15), clock(21, 0), clock(21, 15)
IDLE_SECONDS = 600
MAP_LINE = re.compile(r'^map:\s*(?P<key>\S.*?)\s*->\s*(?P<path>\S.*?)\s*$')
EMAIL = re.compile(r'[^\s<>@,]+@[^\s<>@,]+\.[^\s<>@,]+')
SERVICE, RUN_KEY = 'outlook-triage', 'outlook.triage'
ENTRY = 'from lib.outlook_triage import main; raise SystemExit(main())'


@dataclass(frozen=True)
class Limits:
    max_moves: int = 150
    failures_in_a_row: int = 2


@dataclass(frozen=True)
class Move:
    path: str
    rule: str


@dataclass(frozen=True)
class Leave:
    reason: str


class FindError(Exception):
    pass


def _same(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return ' '.join(a.split()).casefold() == ' '.join(b.split()).casefold()


def person_name(display: str) -> str:
    """'Last, First' as Outlook lists many senders, turned into 'First Last'."""
    parts = [p.strip() for p in display.split(',')]
    return f'{parts[1]} {parts[0]}' if len(parts) == 2 and all(parts) and '@' not in display else display


@dataclass(frozen=True)
class FolderGuide:
    """The folder tree and sender table parsed from the user's private guide."""
    paths: frozenset[str]
    deal_prefix: str
    vendor_prefix: str
    maps: tuple[tuple[str, str], ...] = ()

    @classmethod
    def parse(cls, text: str, *, deal_prefix: str, vendor_prefix: str) -> FolderGuide:
        paths, maps = set(), []
        for raw in text.splitlines():
            line = re.sub(r'\s{2,}\(.*\)$', '', raw).strip()
            mapped = MAP_LINE.match(line)
            if mapped:
                maps.append((mapped['key'], mapped['path']))
            elif line and not line.startswith(('#', '-', '*')) and not line.endswith(':') and ': ' not in line:
                paths.add(line)
        guide = cls(frozenset(paths), deal_prefix, vendor_prefix, tuple(maps))
        if not any(p.startswith(guide.deal_prefix) for p in guide.paths):
            raise ValueError('no Deals folders')
        if any(path not in guide.paths for _, path in guide.maps):
            raise ValueError('map names an unknown folder')
        return guide

    def leaf(self, path: str) -> str:
        return path.rsplit('/', 1)[-1]

    def leaves_under(self, prefix: str) -> list[str]:
        return [p for p in self.paths if p.startswith(prefix)
                and not any(q.startswith(p + '/') for q in self.paths)]

    def unique_name(self, path: str) -> bool:
        """The Move picker is searched by leaf name, so that name must name one folder."""
        return sum(self.leaf(p) == self.leaf(path) for p in self.paths) == 1


class CarrFinder:
    """Exact-name lookups through the record layer's `find` verb, cached per run."""

    def __init__(self, repo: Path):
        self.repo = repo
        self.cache: dict[str, dict] = {}

    def __call__(self, name: str) -> dict:
        if name not in self.cache:
            try:
                result = subprocess.run([str(self.repo / 'run.sh'), 'call', 'find', json.dumps({'query': name})],
                                        cwd=self.repo, capture_output=True, text=True, timeout=90,
                                        env=load_settings().transport_environment())
                value = json.loads(result.stdout)
            except (OSError, subprocess.TimeoutExpired, ValueError):
                raise FindError from None
            if result.returncode or not isinstance(value, dict):
                raise FindError
            self.cache[name] = value
        return self.cache[name]


def _deal_rule(sender: str, guide: FolderGuide, find) -> list[str] | None:
    name = person_name(sender)
    found = find(name)
    parties = [p for p in found.get('parties') or []
               if _same(p.get('name'), name) and not p.get('merged') and p.get('kind') != 'vendor']
    if len(parties) != 1:
        return None
    party = parties[0]
    names = [party.get('name'), party.get('org_name')]
    if len((party.get('name') or '').split()) >= 2:
        names.append(party['name'].split()[-1])
    names += [d.get('name') for d in found.get('deals') or [] if d.get('client_ref') == party.get('ref')]
    return [p for p in guide.leaves_under(guide.deal_prefix) if any(_same(guide.leaf(p), n) for n in names)]


def _vendor_rule(sender: str, guide: FolderGuide, find) -> list[str]:
    return [p for p in guide.leaves_under(guide.vendor_prefix)
            if _same(guide.leaf(p), sender) or _same(guide.leaf(p), person_name(sender))]


def _map_rule(sender: str, guide: FolderGuide, find) -> list[str]:
    address = (EMAIL.search(sender) or [''])[0].casefold()
    return [path for key, path in guide.maps
            if _same(key, sender) or (address and (_same(key, address)
                                                   or (key.startswith('@') and address.endswith(key.casefold()))))]


RULES = (('deal', _deal_rule), ('vendor', _vendor_rule), ('map', _map_rule))


def decide(row: Row, guide: FolderGuide, find) -> Move | Leave:
    """First rule with exactly one folder wins; an ambiguous rule leaves the email where it is."""
    sender = row.sender
    if row.read_state is not ReadState.READ or sender is None:
        return Leave(row.read_state.value)
    for rule, match in RULES:
        hits = match(sender, guide, find)
        if hits is None or not hits:
            continue
        if len(set(hits)) > 1:
            return Leave(f'{rule}_ambiguous')
        if not guide.unique_name(hits[0]):
            return Leave('folder_name_not_unique')
        return Move(hits[0], rule)
    return Leave('no_rule_matched')


def short_hash(label: str) -> str:
    """Names one row in the report without naming its sender, subject or folder."""
    return hashlib.sha256(label.encode('utf-8')).hexdigest()[:12]


@dataclass
class Report:
    moved: list[tuple[str, str]] = field(default_factory=list)
    left: Counter = field(default_factory=Counter)
    unread_changed: list[str] = field(default_factory=list)

    def text(self, outcome: Outcome) -> str:
        lines = [f'outcome: {outcome.state} {outcome.reason}']
        if outcome.detail:
            lines.append(outcome.detail)
        lines += [f'moved: {len(self.moved)}'] + [f'  {digest} {rule}' for digest, rule in self.moved]
        lines += ['left in Inbox:'] + [f'  {reason}: {n}' for reason, n in sorted(self.left.items())]
        if self.unread_changed:
            lines += ['unread set changed (rows gone, read, or arrived):']
            lines += [f'  {digest}' for digest in self.unread_changed]
        return '\n'.join(lines) + '\n'


def hid_idle_seconds() -> float | None:
    """Seconds since the last keyboard or mouse input, from IOHIDSystem; no permission needed."""
    try:
        out = subprocess.run(['ioreg', '-c', 'IOHIDSystem'], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r'"HIDIdleTime" = (\d+)', out)
    return int(match.group(1)) / 1e9 if match else None


def _inbox(window: Window) -> tuple[int, dict[str | None, list[Row]]]:
    """(Inbox unread badge, rows per Focused/Other tab), leaving the folder pane hidden."""
    window.show_folder_pane(True)
    window.select_inbox()
    badge = window.inbox_badge()
    window.show_folder_pane(False)
    tab, rows = window.rows()
    if tab is None:
        return badge, {None: rows}
    tabs: dict[str | None, list[Row]] = {tab: rows}
    other = 'other' if tab == 'focused' else 'focused'
    window.switch_tab(other)
    tabs[other] = window.rows()[1]
    return badge, tabs


def _unread(tabs: dict[str | None, list[Row]]) -> Counter:
    unique: dict[str, Row] = {}
    for rows in tabs.values():
        seen = set()
        for row in rows:
            if row.selected and row.read_state is not ReadState.READ:
                raise UiError('unread_row_selected')
            if not row.sender or row.subject is None or row.key in seen:
                raise UiError('coverage_identity_ambiguous')
            seen.add(row.key)
            previous = unique.get(row.key)
            if previous is not None and (previous.label, previous.selected) != (row.label, row.selected):
                raise UiError('coverage_identity_ambiguous')
            unique[row.key] = row
    return Counter(r.label for r in unique.values() if r.read_state is ReadState.UNREAD)


def _coverage(window: Window, badge: int, tabs: dict[str | None, list[Row]],
              closed: Callable[[], bool]) -> dict[str | None, list[Row]]:
    deadline = time.monotonic() + 120
    allowed = lambda: time.monotonic() < deadline and not closed()

    def scan() -> dict[str | None, list[Row]]:
        result = {}
        for tab in tabs:
            if not allowed():
                raise UiError('coverage_deadline')
            if tab is not None:
                window.switch_tab(tab)
            result[tab] = window.scan_rows(allowed)
        _unread(result)
        window.show_folder_pane(True)
        try:
            if window.inbox_badge() != badge:
                raise UiError('coverage_changed')
        finally:
            window.show_folder_pane(False)
        return result

    first = scan()
    for tab, rows in tabs.items():
        initial = {r.key: (r.label, r.selected) for r in rows}
        if len(initial) != len(rows):
            raise UiError('coverage_identity_ambiguous')
        observed = {r.key: (r.label, r.selected) for r in first[tab]}
        if any(observed.get(key) != state for key, state in initial.items()):
            raise UiError('coverage_changed')
    second = scan()
    fingerprint = lambda result: {tab: [(r.key, r.read_state, r.selected) for r in rows]
                                  for tab, rows in result.items()}
    if fingerprint(first) != fingerprint(second):
        raise UiError('coverage_changed')
    if len(_unread(second)) != badge:
        raise UiError('coverage_incomplete')
    if not allowed():
        raise UiError('coverage_deadline')
    return second


def file_inbox(window: Window, guide: FolderGuide, find, limits: Limits, report: Report,
               closed: Callable[[], bool]) -> Outcome:
    return preserve_unread(window, report, lambda tabs: (
        _move_all(window, tabs, guide, find, limits, report, closed)), closed)


def preserve_unread(window: Window, report: Report, action: Callable[[dict], Outcome],
                    closed: Callable[[], bool]) -> Outcome:
    """Verify the unread set before and after any read-mail operation."""
    badge, tabs = _inbox(window)
    try:
        unread = _unread(tabs)
    except UiError as exc:
        if str(exc) == 'unread_row_selected':
            return Outcome('failed', 'unread_row_selected')
        return Outcome('skipped', 'unread_set_unverified', f'coverage={exc}')
    scanned = sum(unread.values()) != badge
    if scanned:
        try:
            tabs = _coverage(window, badge, tabs, closed)
            unread = _unread(tabs)
        except UiError as exc:
            if str(exc) == 'unread_row_selected':
                return Outcome('failed', 'unread_row_selected')
            report.left['unread_set_unverified'] += sum(len(rows) for rows in tabs.values())
            return Outcome('skipped', 'unread_set_unverified',
                           f'badge={badge} visible_unread={sum(unread.values())} '
                           f'visible_rows={sum(len(rows) for rows in tabs.values())} coverage={exc}')
    window.show_folder_pane(True)
    try:
        if window.inbox_badge() != badge:
            return Outcome('skipped', 'unread_set_unverified', 'coverage=coverage_changed')
    finally:
        window.show_folder_pane(False)
    if closed():
        return Outcome('skipped', 'unread_set_unverified', 'coverage=coverage_deadline')
    try:
        outcome = action(tabs)
    except UiError as exc:
        outcome = Outcome('failed', str(exc),
                          f'at {exc.step}: {exc}' if exc.step else '')
    except CuaError:
        outcome = Outcome('failed', 'cua_driver_failed')
    end_badge, end_tabs = _inbox(window)
    try:
        end_unread = _unread(end_tabs)
        if scanned:
            end_tabs = _coverage(window, end_badge, end_tabs, closed)
            end_unread = _unread(end_tabs)
    except UiError:
        return Outcome('failed', 'unread_set_changed')
    changed = (unread - end_unread) + (end_unread - unread)
    report.unread_changed = sorted({short_hash(label) for label in changed})
    if changed or end_badge != sum(end_unread.values()):
        return Outcome('failed', 'unread_set_changed')
    return outcome


def _move_all(window: Window, tabs: dict[str | None, list[Row]], guide: FolderGuide, find,
              limits: Limits, report: Report, closed: Callable[[], bool]) -> Outcome:
    failures = in_a_row = 0
    for tab, rows in tabs.items():
        if tab is not None:
            window.switch_tab(tab)
        for row in rows:
            try:
                decision = decide(row, guide, find)
            except FindError:
                return Outcome('failed', 'carr_find_failed')
            if isinstance(decision, Leave):
                report.left[decision.reason] += 1
                continue
            if len(report.moved) >= limits.max_moves:
                report.left['move_cap_reached'] += 1
                continue
            result = window.move(row.label, guide.leaf(decision.path), lambda: not closed())
            if result is Moved.WINDOW_CLOSED:
                return Outcome('timed_out', 'window_closed')
            if result is Moved.MOVED:
                in_a_row = 0
                report.moved.append((short_hash(row.label), decision.rule))
                continue
            report.left[result.value] += 1
            if result is Moved.FAILED:
                failures += 1
                in_a_row += 1
                if in_a_row >= limits.failures_in_a_row:
                    return Outcome('failed', 'move_failed_twice')
    return Outcome('failed', 'move_failed') if failures else Outcome('succeeded', 'filed')


def triage(repo: Path, home: Path, waits: Waits, limits: Limits,
           closed: Callable[[], bool]) -> tuple[Outcome, Report]:
    settings = load_settings(home)
    report = Report()
    try:
        mode = stat.S_IMODE(settings.folder_guide.stat().st_mode)
        if mode != 0o600:
            print(f'outlook-triage: the folder guide names clients, so it must be mode 0600, not {mode:04o}; '
                  f'run chmod 600 {settings.folder_guide}', file=sys.stderr)
            return Outcome('failed', 'folder_guide_not_0600'), report
        guide = FolderGuide.parse(settings.folder_guide.read_text(), deal_prefix=settings.deal_prefix,
                                  vendor_prefix=settings.vendor_prefix)
    except OSError:
        return Outcome('failed', 'folder_guide_missing'), report
    except ValueError:
        return Outcome('failed', 'folder_guide_invalid'), report
    idle = hid_idle_seconds()
    if idle is None:
        return Outcome('failed', 'idle_time_unreadable'), report
    if idle < IDLE_SECONDS:
        return Outcome('skipped', 'user_active'), report
    blocked = prepare_outlook(waits)
    if blocked:
        return blocked, report
    try:
        pid = outlook_pid()
        found = usable_window(pid) if pid else None
    except CuaError:
        found = None
    if not found or pid is None:
        return Outcome('failed', 'no_usable_main_window'), report
    window = Window(pid, found['window_id'], waits)
    try:
        window.front()
        outcome = file_inbox(window, guide, CarrFinder(repo), limits, report, closed)
    except UiError as exc:
        outcome = Outcome('failed', str(exc))
    except CuaError:
        outcome = Outcome('failed', 'cua_driver_failed')
    try:
        window.show_folder_pane(True)
    except (UiError, CuaError):
        if outcome.state != 'failed':
            outcome = Outcome('failed', 'folder_pane_restore_failed')
    return outcome, report


def _settled(repo: Path, day: str) -> Path:
    return repo / OUT / f'{day}.settled'


def _minute(moment: datetime) -> clock:
    return moment.astimezone(load_settings().timezone).time().replace(second=0, microsecond=0)


def run(repo: Path, home: Path, now: datetime, waits: Waits = Waits(), limits: Limits = Limits(),
        now_fn: Callable[[], datetime] = lambda: datetime.now(load_settings().timezone)) -> Outcome:
    """One triage run, only from 19:15 to 21:00; it selects nothing once now_fn() reaches 21:15."""
    load_settings(home).require_repo(repo)
    os.umask(0o077)
    if not FIRST_FIRE <= _minute(now) <= LAST_FIRE:
        print('outlook-triage skipped outside_window moved=0 left=0')
        return Outcome('skipped', 'outside_window')
    close = datetime.combine(now.astimezone(load_settings().timezone).date(), WINDOW_CLOSE, load_settings().timezone)
    if now_fn() >= close:
        print('outlook-triage timed_out window_closed moved=0 left=0')
        return Outcome('timed_out', 'window_closed')
    folder = repo / OUT
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o700)
    day = now.strftime('%Y-%m-%d')
    outcome, report = triage(repo, home, waits, limits, lambda: now_fn() >= close)
    with private_file(folder / f'{day}.report.md') as handle:
        handle.write(f'## {now.isoformat()}\n' + report.text(outcome))
    if outcome.state == 'succeeded' or _minute(now) >= LAST_FIRE:
        with private_file(_settled(repo, day)) as handle:
            handle.write(f'{outcome.state} {outcome.reason}\n')
    print(f'outlook-triage {outcome.state} {outcome.reason} moved={len(report.moved)} '
          f'left={sum(report.left.values())}' + (f' {outcome.detail}' if outcome.detail else ''))
    return outcome


def fire(repo: Path | None = None, now: datetime | None = None) -> int:
    """launchd entry: nothing to do outside 19:15-21:00 or once today is settled, else run the job."""
    repo = repo or Path(__file__).resolve().parents[1]
    settings = load_settings()
    settings.require_repo(repo)
    settings.require_service('triage')
    now = now or datetime.now(load_settings().timezone)
    if not FIRST_FIRE <= _minute(now) <= LAST_FIRE:
        return 0
    if _settled(repo, now.strftime('%Y-%m-%d')).exists():
        return 0
    os.chdir(repo)
    os.execv('/bin/zsh', ['/bin/zsh', str(repo / 'bin/run-scheduled.sh'), SERVICE, RUN_KEY,
                          sys.executable, '-c', ENTRY])


def main() -> int:
    load_settings().require_service('triage')
    outcome = run(Path(__file__).resolve().parents[1], Path.home(), datetime.now(load_settings().timezone))
    print(receipt_line('outlook-triage', outcome.state, outcome.reason, {}, Path(__file__).resolve().parents[1]))
    return EXIT[outcome.state]
