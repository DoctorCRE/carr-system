"""Read migration slots held by open PRs. Lower PR numbers own collisions."""
from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
from collections.abc import Callable
from migration_number_contract import SLOT_RE

REPO = 'jbookout/carr-system'


def github(path: str) -> list:
    result = subprocess.run(['gh', 'api', '--paginate', '--slurp', path],
                            capture_output=True, text=True, check=True, timeout=120)
    return json.loads(result.stdout)


@dataclass
class Claims:
    earlier: set[int]
    occupied: set[int]
    owners: dict[int, int]


def rows(pages):
    if not isinstance(pages, list) or not pages or any(not isinstance(p, list) for p in pages):
        raise ValueError('cannot read complete paginated migration claims')
    for page in pages:
        for row in page:
            if not isinstance(row, dict):
                raise ValueError('invalid migration claim row')
            yield row


def claims(pr: int | None, read: Callable = github, *, branch: str | None = None) -> Claims:
    result = Claims(set(), set(), {})
    peers = list(rows(read(f'repos/{REPO}/pulls?state=open&per_page=100')))
    if pr is None and branch:
        pr = next((peer['number'] for peer in peers if peer.get('head', {}).get('ref') == branch), None)
    for peer in peers:
        number = peer.get('number')
        if type(number) is not int or number < 1:
            raise ValueError('invalid open PR number')
        if number == pr:
            continue
        files = list(rows(read(f'repos/{REPO}/pulls/{number}/files?per_page=100')))
        if len(files) >= 3000:
            raise ValueError(f'PR #{number} file list may exceed GitHub limits')
        for file in files:
            name, status = file.get('filename'), file.get('status')
            if not isinstance(name, str) or not isinstance(status, str):
                raise ValueError('incomplete open PR file list')
            match = SLOT_RE.fullmatch(name.removeprefix('migrations/')) if name.startswith('migrations/') else None
            if match and status in ('added', 'renamed'):
                slot = int(match[1])
                result.occupied.add(slot)
                result.owners[slot] = min(number, result.owners.get(slot, number))
                if pr is None or number < pr:
                    result.earlier.add(slot)
    return result
