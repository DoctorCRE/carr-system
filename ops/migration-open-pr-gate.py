"""Refuse a later PR's migration collision before any database work."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from git_env import scrubbed_env
from migration_claims import Claims, claims
from migration_number_contract import SLOT_RE, allocate_integration_successors
from migration_source import migration_names


def findings(main: set[str], pending: set[str], peer: Claims) -> list[str]:
    ceiling = max((int(m[1]) for n in main if (m := SLOT_RE.fullmatch(n))), default=0)
    bad = {name for name in pending if int(name[:4]) <= ceiling or int(name[:4]) in peer.earlier}
    if not bad:
        return []
    targets = allocate_integration_successors(main, pending, occupied_slots=peer.occupied)
    return [f'{name}: slot {name[:4]} belongs to '
            + (f'PR #{peer.owners[int(name[:4])]}' if int(name[:4]) > ceiling else 'main')
            + f'; renumber to {targets[name]} with ./run.sh regen --pr <number>' for name in sorted(bad)]


def main() -> int:
    if os.environ.get('GITHUB_EVENT_NAME') != 'pull_request':
        print('migration-open-pr-gate: open PR read runs on pull_request; local regen also checks claims')
        return 0
    try:
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        base = 'origin/' + event['pull_request']['base']['ref']
        head = event['pull_request']['head']['sha']
        fork = subprocess.run(['git', 'merge-base', base, head], cwd=ROOT,
                              env=scrubbed_env(), capture_output=True, text=True, check=True, timeout=60).stdout.strip()
        base_names = migration_names(ROOT, base) | migration_names(ROOT, 'origin/main')
        added = migration_names(ROOT, head) - migration_names(ROOT, fork) - base_names
        if not added:
            print('migration-open-pr-gate: no added migrations')
            return 0
        errors = findings(base_names, added, claims(int(event['number'])))
        if errors:
            print('\n'.join(errors), file=sys.stderr)
            return 1
        print('migration-open-pr-gate: added slots have no earlier open PR owner')
        return 0
    except (OSError, KeyError, ValueError, subprocess.SubprocessError) as exc:
        print(f'migration-open-pr-gate: cannot verify claims: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
