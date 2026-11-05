"""Read-only evening planning. No mover exists until independently verified."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, time as clock
import argparse
import sqlite3
import stat
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable

from lib.outlook_ui import EXIT, Outcome, Waits

from lib.outlook_settings import load_settings
from lib.outlook_runtime import receipt_line
OUT = Path('out/outlook-triage')
FIRST_FIRE, LAST_FIRE, WINDOW_CLOSE = clock(19, 15), clock(21, 0), clock(21, 15)
MAP_LINE = re.compile(r'^map:\s*(?P<key>\S.*?)\s*->\s*(?P<path>\S.*?)\s*$')
EMAIL = re.compile(r'[^\s<>@,]+@[^\s<>@,]+\.[^\s<>@,]+')
SERVICE, RUN_KEY = 'outlook-triage', 'outlook.triage'
ENTRY = 'from lib.outlook_triage import main; raise SystemExit(main())'


@dataclass(frozen=True)
class Limits:
    max_moves: int = 150


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


@dataclass
class Report:
    moved: list[str] = field(default_factory=list)
    left: Counter = field(default_factory=Counter)
    planning: dict = field(default_factory=dict)

    def text(self, outcome: Outcome) -> str:
        lines = [f'outcome: {outcome.state} {outcome.reason}']
        if outcome.detail:
            lines.append(outcome.detail)
        lines += [f'moved: {len(self.moved)}']
        lines += [f'  {rule}: {n}' for rule, n in sorted(Counter(self.moved).items())]
        lines += ['left in Inbox:'] + [f'  {reason}: {n}' for reason, n in sorted(self.left.items())]
        lines += [f'{key}: {value}' for key, value in sorted(self.planning.items())]
        return '\n'.join(lines) + '\n'


def hid_idle_seconds() -> float | None:
    """Seconds since the last keyboard or mouse input, from IOHIDSystem; no permission needed."""
    try:
        out = subprocess.run(['ioreg', '-c', 'IOHIDSystem'], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r'"HIDIdleTime" = (\d+)', out)
    return int(match.group(1)) / 1e9 if match else None


def triage(repo: Path, home: Path, waits: Waits, limits: Limits,
           closed: Callable[[], bool]) -> tuple[Outcome, Report]:
    return _triage(repo, home, limits, closed, None)


def observe_and_triage(repo, home, research_copy, waits, limits, closed):
    """Observe and plan in one invocation; no retained file grants admission."""
    return _triage(repo, home, limits, closed, research_copy)


def _triage(repo, home, limits, closed, research_copy):
    from lib.outlook_triage_snapshot import read_snapshot, SnapshotError
    from lib.outlook_triage_producer import produce
    from lib.outlook_triage_plan import plan_triage
    from lib.outlook_triage_capability import select_candidate

    report = Report()
    capability = 'capability_not_verified'
    snapshot = 'snapshot_producer_unavailable'
    observations = ()
    reason = snapshot
    try:
        settings = load_settings(home)
        try:
            contract = json.loads((repo / 'spec/outlook/triage-executor-contract.v1.json').read_text())
            select_candidate(contract, contract['recommendation']['candidate'])
            capability = 'executor_unavailable'
        except (OSError, ValueError, KeyError, TypeError):
            pass
        path = repo / OUT / 'snapshot.json'
        if research_copy is None:
            try:
                evidence = read_snapshot(path, settings, limits.max_moves, evidence=True)
                if evidence[-1] != 'none':
                    reason = 'reconciliation_required'
            except SnapshotError:
                pass
        else:
            published = produce(research_copy, path, settings, max_intents=limits.max_moves)
            observations, routing, catalog, policy, effect = published.parsed
            snapshot = 'validated'
            reason = capability
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError) as exc:
        if str(exc) == 'reconciliation_required':
            reason = 'reconciliation_required'
        observations = ()
    if snapshot != 'validated':
        report.planning = dict(observed=0, planned=0, held=0, moved=0,
                               snapshot=snapshot, capability=capability)
        detail = ' '.join(f'{key}={value}' for key, value in report.planning.items())
        return Outcome('timed_out' if closed() else 'skipped',
                       'window_closed' if closed() else reason, detail), report
    plan = plan_triage(observations, routing, catalog, policy)
    report.left.update(item.reason for item in plan.leaves)
    report.left[reason] += len(plan.moves)
    report.planning = {'observed': len(observations), 'planned': len(plan.moves),
                       'held': len(plan.moves) + len(plan.leaves), 'moved': 0,
                       'snapshot': snapshot, 'capability': capability}
    detail = ' '.join(f'{key}={value}' for key, value in report.planning.items())
    if closed():
        return Outcome('timed_out', 'window_closed', detail), report
    return Outcome('skipped', reason, detail), report


def _minute(moment: datetime) -> clock:
    return moment.astimezone(load_settings().timezone).time().replace(second=0, microsecond=0)


def run(repo: Path, home: Path, now: datetime, waits: Waits = Waits(), limits: Limits = Limits(),
        now_fn: Callable[[], datetime] = lambda: datetime.now(load_settings().timezone),
        research_copy: Path | None = None) -> Outcome:
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
    day = now.strftime('%Y-%m-%d')
    outcome, report = _triage(repo, home, limits, lambda: now_fn() >= close, research_copy)
    safe_report = not any(p.is_symlink() for p in (folder, *folder.parents))
    if research_copy is not None:
        safe_report = safe_report and not folder.resolve().is_relative_to(Path(research_copy).resolve())
    if safe_report:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            fd = os.open(folder / f'{day}.report.md',
                         os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            with os.fdopen(fd, 'a') as handle:
                info = os.fstat(handle.fileno())
                if (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                        and stat.S_IMODE(info.st_mode) == 0o600):
                    handle.write(f'## {now.isoformat()}\n' + report.text(outcome))
        except OSError:
            pass
    print(f'outlook-triage {outcome.state} {outcome.reason} moved={len(report.moved)} '
          f'left={sum(report.left.values())}' + (f' {outcome.detail}' if outcome.detail else ''))
    return outcome


def fire(repo: Path | None = None, now: datetime | None = None) -> int:
    """launchd entry: nothing to do outside 19:15-21:00 else run the job."""
    repo = repo or Path(__file__).resolve().parents[1]
    settings = load_settings()
    settings.require_repo(repo)
    settings.require_service('triage')
    now = now or datetime.now(load_settings().timezone)
    if not FIRST_FIRE <= _minute(now) <= LAST_FIRE:
        return 0
    os.chdir(repo)
    os.execv('/bin/zsh', ['/bin/zsh', str(repo / 'bin/run-scheduled.sh'), SERVICE, RUN_KEY,
                          sys.executable, '-c', ENTRY])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--copy', type=Path, help='explicit research copy for this observation invocation')
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    try:
        load_settings().require_service('triage')
        outcome = run(repo, Path.home(), datetime.now(load_settings().timezone), research_copy=args.copy)
    except (OSError, ValueError):
        outcome = Outcome('skipped', 'snapshot_producer_unavailable')
        print('outlook-triage skipped snapshot_producer_unavailable observed=0 planned=0 held=0 moved=0')
    print(receipt_line('outlook-triage', outcome.state, outcome.reason, {}, Path(__file__).resolve().parents[1]))
    return EXIT[outcome.state]
