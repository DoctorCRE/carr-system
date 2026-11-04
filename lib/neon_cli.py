"""The only Neon CLI launcher. Browser authentication is never permitted."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

from lib.credential_file import read_env_file

ROOT = Path(__file__).resolve().parents[1]
MISSING_KEY = (
    'neonctl refused: NEON_API_KEY is not set. Browser login is disabled.\n'
    'Store a Neon API key in ~/.config/carr/db.env as NEON_API_KEY=... '
    '(chmod 600), as described in RECOVERY.md, then retry.'
)


def api_key(env: dict[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    key = (source.get('NEON_API_KEY') or '').strip()
    if key:
        return key
    if not source.get('HOME'):
        return ''
    path = Path(source['HOME']) / '.config/carr/db.env'
    try:
        return read_env_file(path, syntax='prefix', duplicates='first',
                             names=('NEON_API_KEY',)).get('NEON_API_KEY', '').strip()
    except (OSError, ValueError):
        return ''


def run(args: list[str], *, env: dict[str, str] | None = None,
        executable: str | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
    """Capture provider output with a stored API key and closed stdin."""
    child_env = dict(os.environ if env is None else env)
    key = api_key(child_env)
    if not key:
        raise SystemExit(MISSING_KEY)
    if not args or args[0] not in {'connection-string', 'projects', 'branches', 'api'}:
        raise SystemExit('neonctl refused: only non-interactive CARR commands are permitted')
    child_env['NEON_API_KEY'] = key
    if executable is None:
        pinned = ROOT / 'mcp-server/node_modules/.bin/neonctl'
        executable = str(pinned) if pinned.is_file() else 'neonctl'
    return subprocess.run([executable, *args], env=child_env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=timeout)
