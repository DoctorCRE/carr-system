"""Nightly Inbox triage: file Joe's already-read Inbox email into Outlook folders.

Code only: no agent session and no model. launchd fires `fire()` every 15
minutes from 19:15 to 21:00. Once a day is settled (a run that was not
skipped, or the 21:00 fire) the remaining fires exit at once without a run row;
otherwise `fire()` hands over to bin/run-scheduled.sh, which runs `main()` and
turns its exit code into the run row (0 succeeded, 78 skipped, 124 timed out,
anything else failed). The row carries no names.

Unread email is never selected, so it stays unread: the unread rows are read off
the list first and must add up to the Inbox unread badge before anything is
touched, and after the moves the same rows must still be unread. The folder tree
and the sender table come from Joe's private ~/.config/carr folder guide; the
moves report (sender, subject, folder) stays in out/outlook-triage/ (0700/0600).

Before filing, up to 50 emails the intake job lists as missing their full body
are opened so Outlook downloads them: found by searching the sender address and
matching the subject's sha256, selected, given time to render, and, when they
were unread, marked unread again before the selection moves on. The intake job
owns that list; triage only adds opened_at. REFRESH_LIST's path and the hash
are a provisional contract until the intake job publishes its own.
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
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

from lib.outlook_ui import (EXIT, CuaError, Moved, Outcome, ReadState, Refreshed, Row, UiError, Waits,
                            Window, outlook_pid, prepare_outlook, private_file, usable_window)

GUIDE = Path('.config/carr/outlook-nightly-sweep.folders.md')
OUT = Path('out/outlook-triage')
REFRESH_LIST = Path('out/outlook-intake/needs-refresh.json')
REFRESH_FIELDS = ('message_id', 'subject_hash', 'sent_utc', 'sender_email')
ZONE = ZoneInfo('America/Chicago')
LAST_FIRE = clock(21, 0)
IDLE_SECONDS = 600
DEALS = 'Deals/'
VENDORS = 'Networking/Vendors/A.List/'
MAP_LINE = re.compile(r'^map:\s*(?P<key>\S.*?)\s*->\s*(?P<path>\S.*?)\s*$')
EMAIL = re.compile(r'[^\s<>@,]+@[^\s<>@,]+\.[^\s<>@,]+')
SERVICE, RUN_KEY = 'outlook-triage', 'outlook.triage'
ENTRY = 'from lib.outlook_triage import main; raise SystemExit(main())'


@dataclass(frozen=True)
class Limits:
    max_refreshes: int = 50
    max_moves: int = 150
    deadline_seconds: float = 6000
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
    return bool(a and b) and ' '.join(a.split()).casefold() == ' '.join(b.split()).casefold()


def person_name(display: str) -> str:
    """'Last, First' as Outlook lists many senders, turned into 'First Last'."""
    parts = [p.strip() for p in display.split(',')]
    return f'{parts[1]} {parts[0]}' if len(parts) == 2 and all(parts) and '@' not in display else display


@dataclass(frozen=True)
class FolderGuide:
    """The folder tree and sender table parsed from Joe's private guide."""
    paths: frozenset[str]
    maps: tuple[tuple[str, str], ...] = ()

    @classmethod
    def parse(cls, text: str) -> FolderGuide:
        paths, maps = set(), []
        for raw in text.splitlines():
            line = re.sub(r'\s{2,}\(.*\)$', '', raw).strip()
            mapped = MAP_LINE.match(line)
            if mapped:
                maps.append((mapped['key'], mapped['path']))
            elif line and not line.startswith(('#', '-', '*')) and not line.endswith(':') and ': ' not in line:
                paths.add(line)
        guide = cls(frozenset(paths), tuple(maps))
        if not any(p.startswith(DEALS) for p in guide.paths):
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
        self.repo, self.cache = repo, {}

    def __call__(self, name: str) -> dict:
        if name not in self.cache:
            try:
                result = subprocess.run([str(self.repo / 'run.sh'), 'call', 'find', json.dumps({'query': name})],
                                        cwd=self.repo, capture_output=True, text=True, timeout=90)
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
    return [p for p in guide.leaves_under(DEALS) if any(_same(guide.leaf(p), n) for n in names)]


def _vendor_rule(sender: str, guide: FolderGuide, find) -> list[str]:
    return [p for p in guide.leaves_under(VENDORS)
            if _same(guide.leaf(p), sender) or _same(guide.leaf(p), person_name(sender))]


def _map_rule(sender: str, guide: FolderGuide, find) -> list[str]:
    address = (EMAIL.search(sender) or [''])[0].casefold()
    return [path for key, path in guide.maps
            if _same(key, sender) or (address and (_same(key, address)
                                                   or (key.startswith('@') and address.endswith(key.casefold()))))]


RULES = (('deal', _deal_rule), ('vendor', _vendor_rule), ('map', _map_rule))


def decide(row: Row, guide: FolderGuide, find) -> Move | Leave:
    """First rule with exactly one folder wins; an ambiguous rule leaves the email where it is."""
    if row.read_state is not ReadState.READ:
        return Leave(row.read_state.value)
    for rule, match in RULES:
        hits = match(row.sender, guide, find)
        if hits is None or not hits:
            continue
        if len(set(hits)) > 1:
            return Leave(f'{rule}_ambiguous')
        if not guide.unique_name(hits[0]):
            return Leave('folder_name_not_unique')
        return Move(hits[0], rule)
    return Leave('no_rule_matched')


@dataclass
class Report:
    moved: list[tuple[str, str, str]] = field(default_factory=list)
    left: Counter = field(default_factory=Counter)
    refreshed: Counter = field(default_factory=Counter)
    unread_changed: list[str] = field(default_factory=list)

    def text(self, outcome: Outcome) -> str:
        lines = [f'outcome: {outcome.state} {outcome.reason}']
        lines += ['opened for a full download:'] + [f'  {r}: {n}' for r, n in sorted(self.refreshed.items())]
        lines += [f'moved: {len(self.moved)}']
        lines += [f'  {headline} -> {path} ({rule})' for headline, path, rule in self.moved]
        lines += ['left in Inbox:'] + [f'  {reason}: {n}' for reason, n in sorted(self.left.items())]
        if self.unread_changed:
            lines += ['unread email that changed (must stay unread):']
            lines += [f'  {headline}' for headline in self.unread_changed]
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
    badge = window.inbox_badge()
    window.show_folder_pane(False)
    tab, rows = window.rows()
    if tab is None:
        return badge, {None: rows}
    tabs = {tab: rows}
    other = 'other' if tab == 'focused' else 'focused'
    window.switch_tab(other)
    tabs[other] = window.rows()[1]
    return badge, tabs


def _unread(tabs: dict[str | None, list[Row]]) -> Counter:
    return Counter(r.label for rows in tabs.values() for r in rows if r.read_state is ReadState.UNREAD)


def file_inbox(window: Window, guide: FolderGuide, find, limits: Limits, report: Report,
               refresh_list: Path) -> Outcome:
    badge, tabs = _inbox(window)
    unread = _unread(tabs)
    if any(r.selected and r.read_state is not ReadState.READ for rows in tabs.values() for r in rows):
        return Outcome('failed', 'unread_row_selected')
    if sum(unread.values()) != badge:
        report.left['unread_set_unverified'] += sum(len(rows) for rows in tabs.values())
        return Outcome('failed', 'unread_set_unverified')
    try:
        outcome = (refresh_all(window, refresh_list, limits, report)
                   or _move_all(window, tabs, guide, find, limits, report))
    except ValueError:
        outcome = Outcome('failed', 'refresh_list_invalid')
    except UiError as exc:
        outcome = Outcome('failed', str(exc))
    except CuaError:
        outcome = Outcome('failed', 'cua_driver_failed')
    end_badge, end_tabs = _inbox(window)
    end_unread = _unread(end_tabs)
    lost = unread - end_unread
    report.unread_changed = sorted(set(report.unread_changed)
                                   | {Row(label, '', False).headline for label in lost.elements()})
    if lost or end_badge != sum(end_unread.values()):
        return Outcome('failed', 'unread_set_changed')
    return outcome


def _subject_hash(subject: str) -> str:
    return hashlib.sha256(subject.encode('utf-8')).hexdigest()


def read_refresh_list(path: Path) -> list[dict]:
    """The intake job's list of email whose full body is not on disk yet; missing means empty."""
    try:
        entries = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        raise ValueError('refresh list unreadable') from None
    if not isinstance(entries, list) or not all(
            isinstance(e, dict) and all(isinstance(e.get(k), str) and e[k] for k in REFRESH_FIELDS)
            for e in entries):
        raise ValueError('refresh list malformed')
    return entries


def _record_opened(path: Path, opened: dict[str, str]) -> None:
    """Append opened_at to the entries opened; the intake job owns everything else in the file."""
    entries = read_refresh_list(path)
    for entry in entries:
        if entry['message_id'] in opened:
            entry['opened_at'] = opened[entry['message_id']]
    temp = path.with_name(path.name + '.triage-tmp')
    with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as handle:
        handle.write(json.dumps(entries, indent=2) + '\n')
    os.replace(temp, path)


def refresh_all(window: Window, path: Path, limits: Limits, report: Report) -> Outcome | None:
    """Open up to max_refreshes listed emails, least recently opened first; None means no failure."""
    entries = sorted(read_refresh_list(path), key=lambda e: e.get('opened_at') or '')[:limits.max_refreshes]
    opened: dict[str, str] = {}
    outcome = None
    try:
        for entry in entries:
            matches = [r for r in window.search(entry['sender_email'])
                       if r.subject is not None and _subject_hash(r.subject) == entry['subject_hash']]
            if len({r.key for r in matches}) != 1:
                report.refreshed['ambiguous' if matches else Refreshed.NOT_FOUND.value] += 1
                window.end_search()
                continue
            result = window.refresh(matches[0].key)
            window.end_search()
            report.refreshed[result.value] += 1
            if result in (Refreshed.OPENED, Refreshed.RESTORED):
                opened[entry['message_id']] = datetime.now(timezone.utc).isoformat()
            if result is Refreshed.NOT_RESTORED:
                report.unread_changed.append(matches[0].headline)
                outcome = Outcome('failed', 'unread_set_changed')
                break
    finally:
        if opened:
            _record_opened(path, opened)
    return outcome


def _move_all(window: Window, tabs: dict[str | None, list[Row]], guide: FolderGuide, find,
              limits: Limits, report: Report) -> Outcome:
    deadline = time.monotonic() + limits.deadline_seconds
    failures = 0
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
            if time.monotonic() >= deadline:
                return Outcome('timed_out', 'deadline_reached')
            result = window.move(row.label, guide.leaf(decision.path))
            if result is Moved.MOVED:
                failures = 0
                report.moved.append((row.headline, decision.path, decision.rule))
                continue
            report.left[result.value] += 1
            if result is Moved.FAILED:
                failures += 1
                if failures >= limits.failures_in_a_row:
                    return Outcome('failed', 'move_failed_twice')
    return Outcome('succeeded', 'filed')


def triage(repo: Path, home: Path, waits: Waits = Waits(), limits: Limits = Limits()) -> tuple[Outcome, Report]:
    report = Report()
    try:
        guide = FolderGuide.parse((home / GUIDE).read_text())
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
    if not found:
        return Outcome('failed', 'no_usable_main_window'), report
    window = Window(pid, found['window_id'], waits)
    try:
        window.front()
        outcome = file_inbox(window, guide, CarrFinder(repo), limits, report, repo / REFRESH_LIST)
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


def run(repo: Path, home: Path, now: datetime, waits: Waits = Waits(), limits: Limits = Limits()) -> Outcome:
    os.umask(0o077)
    folder = repo / OUT
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o700)
    day = now.strftime('%Y-%m-%d')
    outcome, report = triage(repo, home, waits, limits)
    with private_file(folder / f'{day}.report.md') as handle:
        handle.write(f'## {now.isoformat()}\n' + report.text(outcome))
    if outcome.state != 'skipped' or now.time() >= LAST_FIRE:
        with private_file(_settled(repo, day)) as handle:
            handle.write(f'{outcome.state} {outcome.reason}\n')
    print(f'outlook-triage {outcome.state} {outcome.reason} moved={len(report.moved)} '
          f'left={sum(report.left.values())}')
    return outcome


def fire(repo: Path | None = None, now: datetime | None = None) -> int:
    """launchd entry: nothing to do once today is settled, else run under bin/run-scheduled.sh."""
    repo = repo or Path(__file__).resolve().parents[1]
    now = now or datetime.now(ZONE)
    if _settled(repo, now.strftime('%Y-%m-%d')).exists():
        return 0
    os.chdir(repo)
    os.execv('/bin/zsh', ['/bin/zsh', str(repo / 'bin/run-scheduled.sh'), SERVICE, RUN_KEY,
                          sys.executable, '-c', ENTRY])


def main() -> int:
    outcome = run(Path(__file__).resolve().parents[1], Path.home(), datetime.now(ZONE))
    return EXIT[outcome.state]
