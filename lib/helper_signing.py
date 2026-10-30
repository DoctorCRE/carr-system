"""Sign a Full Disk Access helper with a stable local identity when this Mac has one.

macOS ties an ad-hoc signed helper's Full Disk Access grant to its cdhash, so every
rebuild drops the grant. A helper signed by a keychain identity with a designated
requirement on its identifier and certificate keeps the grant across rebuilds.
This module only reads the keychain (find-identity, find-certificate); it never
creates, imports or changes keychain items. Stdlib only: the research installer
runs it under /usr/bin/python3.
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
    """The identity exists but the helper could not be signed to a stable requirement."""


def _run(*command: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        raise SigningError(f'{" ".join(command[:2])} failed: {(error.stderr or error.stdout).strip()}') from error


def certificate_sha1(name: str = IDENTITY) -> Optional[str]:
    """The identity's certificate SHA-1 when find-identity lists it as valid, else None."""
    listed = _run('security', 'find-identity', '-p', 'codesigning', '-v').stdout
    hashes = {match.group(1).upper() for match in re.finditer(r'^\s*\d+\)\s+([0-9A-Fa-f]{40})\s+"(.*)"\s*$', listed, re.M)
              if match.group(2) == name}
    if not hashes:
        return None
    found = re.search(r'^SHA-1 hash:\s*([0-9A-Fa-f]{40})\s*$', _run('security', 'find-certificate', '-c', name, '-Z').stdout, re.M)
    if not found or found.group(1).upper() not in hashes:
        raise SigningError(f'the certificate named {name!r} is not the valid code-signing identity of that name')
    return found.group(1).upper()


def requirement(identifier: str, sha1: str) -> str:
    """Pin the grant to the identifier and certificate, never the cdhash."""
    return f'designated => identifier "{identifier}" and certificate leaf = H"{sha1.lower()}"'


def sign(path: Path, identifier: str, sha1: str) -> str:
    """Sign with the explicit requirement, verify it, print and return the designated line."""
    wanted = requirement(identifier, sha1)
    _run('codesign', '--force', '-s', sha1, '--identifier', identifier, f'-r={wanted}', str(path))
    _run('codesign', '--verify', '--strict', str(path))
    shown = _run('codesign', '-d', '-r-', str(path))
    text = (shown.stdout + shown.stderr).strip()
    print(text, flush=True)
    designated = [line for line in text.splitlines() if line.startswith('designated =>')]
    if (len(designated) != 1 or 'cdhash' in designated[0] or f'identifier "{identifier}"' not in designated[0]
            or f'h"{sha1.lower()}"' not in designated[0].lower()):
        raise SigningError(f'{path} must carry the designated requirement {wanted!r}, got {designated!r}')
    return designated[0]
