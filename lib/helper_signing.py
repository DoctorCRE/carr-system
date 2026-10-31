"""Sign a Full Disk Access helper with the stable local identity when this Mac has one.

An ad-hoc helper's grant is tied to its cdhash, so every rebuild drops it. A helper
signed by the CARR Local Helpers certificate with a requirement on its identifier
and certificate keeps the grant. Reads the keychain only; stdlib only, because the
research installer runs it under /usr/bin/python3.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
from typing import Optional

IDENTITY = 'CARR Local Helpers'
FALLBACK = (f'{IDENTITY} signing identity not found; keeping the pinned ad-hoc helper, '
            'so a rebuild will need Full Disk Access re-granted')


class SigningError(RuntimeError):
    """The helper could not be signed to the stable requirement."""


def _run(*command: str) -> str:
    try:
        done = subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        raise SigningError(f'{" ".join(command[:2])} failed: {(error.stderr or error.stdout).strip()}') from error
    return done.stdout + done.stderr


def certificate_sha1() -> Optional[str]:
    """The identity's certificate SHA-1 when find-identity lists it as valid, else None."""
    found = re.search(rf'^\s*\d+\)\s+([0-9A-Fa-f]{{40}})\s+"{re.escape(IDENTITY)}"\s*$',
                      _run('security', 'find-identity', '-p', 'codesigning', '-v'), re.M)
    return found.group(1).upper() if found else None


def _requirement(identifier: str, sha1: str) -> str:
    return f'designated => identifier "{identifier}" and certificate leaf = H"{sha1.lower()}"'


def has_identity(path: Path, identifier: str, sha1: str) -> bool:
    """Whether the installed helper carries the same stable designated requirement."""
    shown = _run('codesign', '-d', '-r-', str(path))
    return [line for line in shown.splitlines() if line.startswith('designated =>')] == [_requirement(identifier, sha1)]


def sign(path: Path, identifier: str, sha1: str) -> None:
    """Sign with an explicit identifier-and-certificate requirement, verify it and print it."""
    wanted = _requirement(identifier, sha1)
    _run('codesign', '--force', '-s', sha1, '--identifier', identifier, f'-r={wanted}', str(path))
    _run('codesign', '--verify', '--strict', str(path))
    shown = _run('codesign', '-d', '-r-', str(path)).strip()
    print(shown, flush=True)
    designated = [line for line in shown.splitlines() if line.startswith('designated =>')]
    if designated != [wanted]:
        raise SigningError(f'{path} must carry the designated requirement {wanted!r}, got {designated!r}')
