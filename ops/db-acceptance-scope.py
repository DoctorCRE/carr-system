"""Decide whether the hosted disposable-Postgres lane must run in full.

The lane (`local-db-ci --class migration`) takes 34-45 minutes. A PR whose
diff touches no migration, schema, or record-layer database file cannot change
its verdict, so the workflow skips the install and the run and still reports
green. Schedule, manual runs and anything this script cannot decide stay full.

Prints `full=true|false` (and appends it to $GITHUB_OUTPUT when set).
"""
from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from git_env import scrubbed_env  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# Any match runs the full lane. fnmatch `*` crosses `/`, so `dir/*` is a tree.
FULL_PATTERNS = (
    'migrations/*', 'db/*', 'lib/*', 'mcp-server/src/*',
    'mcp-server/test/codex-continuity*',
    'mcp-server/package.json', 'mcp-server/package-lock.json',
    'bin/schema-snapshot.sh', 'run.sh', 'requirements.lock',
    '.python-version', '.nvmrc',
    '.github/workflows/db-acceptance.yml',
    'ops/ci.sh', 'ops/local-pg-ci*', 'ops/db-*', 'ops/db_*',
    'ops/*migration*', 'ops/*schema*', 'ops/*postgres*', 'ops/*-pg-*',
    'ops/*acceptance*', 'ops/*rule-delivery*', 'ops/*canonical-ownership*',
    'ops/config/rule-enforcement-map*', 'ops/config/*migration*',
    'tools/migrat*', 'tools/test_migr*', 'tools/*schema*', 'tools/git_env.py',
    'tools/renumber-at-merge*', 'tools/*f03*', 'ops/*f03*', 'ops/*.sql',
    'mcp-server/test/*pg*', 'mcp-server/test/*f03*',
    'mcp-server/test/find-rule-supersedes*', 'mcp-server/test/catch-me-up-writer-route*',
    'mcp-server/test/one-step-rule-approval*',
)


def needs_full(paths: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(p, pat) for p in paths for pat in FULL_PATTERNS)


def changed_paths(event: dict) -> list[str] | None:
    pr = event.get('pull_request')
    if not pr:
        return None
    base, head = 'origin/' + pr['base']['ref'], pr['head']['sha']
    out = subprocess.run(['git', 'diff', '--name-only', '--no-renames', f'{base}...{head}'], cwd=ROOT, env=scrubbed_env(),
                         capture_output=True, text=True, check=True, timeout=120).stdout
    return [line for line in out.splitlines() if line]


def decide() -> bool:
    if os.environ.get('GITHUB_EVENT_NAME') != 'pull_request':
        return True
    try:
        paths = changed_paths(json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text()))
    except (KeyError, OSError, ValueError, subprocess.SubprocessError):
        return True  # cannot decide: run everything
    return True if paths is None else needs_full(paths)


def main() -> int:
    full = decide()
    line = f'full={"true" if full else "false"}'
    print(line)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as handle:
            handle.write(line + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
