"""Read migration slots held by open PRs. Lower PR numbers own collisions."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import time
from urllib.parse import urlencode, parse_qsl, urlsplit
from collections.abc import Callable
from migration_number_contract import SLOT_RE

REPO = 'DoctorCRE/carr-system'


def github(path: str) -> list:
    from lib import release_reader

    token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if not token:
        raise ValueError('GitHub token required to verify migration claims')
    parts = urlsplit(path)
    if parts.scheme or parts.netloc or not parts.path.startswith(f'repos/{REPO}/pulls'):
        raise ValueError('invalid migration claims endpoint')
    query = dict(parse_qsl(parts.query))
    pages = []
    deadline = time.monotonic() + 120
    for page in range(1, 101):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('migration claims read deadline exceeded')
        query.update(per_page='100', page=str(page))
        reply = release_reader.read(
            'https://api.github.com/' + parts.path + '?' + urlencode(query),
            headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                     'X-GitHub-Api-Version': '2022-11-28'},
            timeout=min(15, remaining), max_bytes=8 * 1024 * 1024)
        if reply.status != 200 or len(reply.body) > 8 * 1024 * 1024:
            raise ValueError(f'cannot read migration claims: HTTP {reply.status} or oversized response')
        data = json.loads(reply.body)
        if not isinstance(data, list):
            raise ValueError('migration claims response is not a page list')
        pages.append(data)
        link = next((value for key, value in reply.headers.items() if key.lower() == 'link'), '')
        if 'rel="next"' not in link and len(data) < 100:
            return pages
    raise ValueError('migration claims pagination limit exceeded; no partial result returned')


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
