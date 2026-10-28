"""Decide whether the hosted disposable-Postgres lane must run in full.

One path inventory covers the canonical class, its disposable runner and all
acceptance programs. A PR outside this inventory records not applicable. Schedule, manual runs and anything this script cannot decide stay full.

Prints `full=true|false` (and appends it to $GITHUB_OUTPUT when set).
"""
from __future__ import annotations

import argparse
import functools
import fnmatch
import json
import os
import re
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from git_env import scrubbed_env  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT

# Runtime-discovered inputs remain broad. Static JS dependencies outside these
# trees come from the canonical proof commands, not a second path list.
FULL_PATTERNS = (
    '*.sql', 'migrations/*', 'db/*', 'lib/*', 'ops/*', 'tools/*', 'bin/*',
    'control-room/contracts/*', 'pipelines/*',
    'audits/guidance-migration-manifest.v1.tsv', 'runbooks/rollback-worker.md',
    'mcp-server/src/*', 'mcp-server/test/*',
    'mcp-server/package.json', 'mcp-server/package-lock.json',
    'run.sh', 'requirements*', '.python-version', '.nvmrc',
    '.github/workflows/ci.yml',
)


def needs_fixtures(paths):
    return any(fnmatch.fnmatchcase(path, pattern) for path in paths
               for pattern in ('migrations/*', 'db/*', 'tools/migrate.py', 'ops/ci-migration*'))


def relative_imports(roots: list[Path], root: Path) -> frozenset[str]:
    """Follow literal local imports without executing proof code."""
    pending, seen = list(roots), set()
    while pending:
        path = pending.pop().resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError('proof dependency escapes repository')
        name = path.relative_to(root.resolve()).as_posix()
        if name in seen:
            continue
        seen.add(name)
        # Include static import/export, literal import() and require(). Bare
        # packages are covered by package.json/lock. Unknown local paths fail
        # scope resolution instead of silently dropping a dependency.
        text = path.read_text()
        for imported in re.findall(r"(?:from\s*|import\s*\(?\s*|require\s*\(\s*)[\"'](\.[^\"']+)[\"']", text):
            target = path.parent / imported
            if target.suffix in ('.js', '.mjs', '.cjs'):
                pending.append(target)
            else:
                seen.add(target.resolve().relative_to(root.resolve()).as_posix())
    return frozenset(seen)


@functools.lru_cache(maxsize=1)
def proof_inputs() -> frozenset[str]:
    text = (SOURCE_ROOT / 'ops/ci-migration.py').read_text()
    text += (SOURCE_ROOT / 'ops/db_acceptance_programs.py').read_text()
    roots = set(re.findall(r'mcp-server/test/[A-Za-z0-9_.-]+\.mjs', text))
    return relative_imports([SOURCE_ROOT / path for path in sorted(roots) if (SOURCE_ROOT / path).is_file()], SOURCE_ROOT)


def needs_full(paths: list[str]) -> bool:
    inputs = proof_inputs()
    return any(p in inputs or any(fnmatch.fnmatchcase(p, pat) for pat in FULL_PATTERNS)
               for p in paths)


def changed_paths(event: dict) -> list[str] | None:
    pr = event.get('pull_request')
    group = event.get('merge_group')
    if group:
        base, head = group['base_sha'], group['head_sha']
    elif pr:
        base, head = pr['base'].get('sha', 'origin/' + pr['base']['ref']), pr['head']['sha']
    else:
        return None
    out = subprocess.run(['git', 'diff', '--name-only', '--no-renames', '-z', f'{base}...{head}'], cwd=ROOT, env=scrubbed_env(),
                         capture_output=True, text=True, check=True, timeout=120).stdout
    return [path for path in out.split('\0') if path]


def classify() -> tuple[bool, list[str] | None, str]:
    if os.environ.get('GITHUB_EVENT_NAME') not in ('pull_request', 'merge_group'):
        return True, None, 'non-diff event requires full proof'
    try:
        paths = changed_paths(json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text()))
        full = paths is None or os.environ.get('GITHUB_EVENT_NAME') == 'merge_group' or needs_full(paths)
    except (KeyError, TypeError, AttributeError, OSError, ValueError, subprocess.SubprocessError):
        return True, None, 'diff unavailable; run full proof'
    if paths is None:
        return True, None, 'PR identity unavailable; run full proof'
    return full, paths, 'database input changed' if full else 'no database input changed'


def decide() -> bool:
    return classify()[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    full, paths, reason = classify()
    status = 'required' if full else 'not applicable'
    try:
        inputs = sorted(proof_inputs())
    except (OSError, ValueError):
        inputs = None
    receipt = {'schema': 'carr-db-scope/v1', 'full': full,
               'status': status,
               'paths': paths, 'reason': reason,
               'event': os.environ.get('GITHUB_EVENT_NAME'),
               'tested_sha': os.environ.get('GITHUB_SHA'),
               'patterns': FULL_PATTERNS, 'inputs': inputs}
    if args.receipt:
        args.receipt.write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write('Database migration proof: ' + status + '\n')
    fixtures = paths is None or needs_fixtures(paths)
    fixture_line = f'fixtures={str(fixtures).lower()}'
    print(fixture_line)
    line = f'full={"true" if full else "false"}'
    print(line)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as handle:
            handle.write(line + '\n' + fixture_line + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
