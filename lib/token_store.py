"""Where every stored credential NAME lands, and the one writer that puts it there.

Two writers used to each know this. tools/store-token.py (the human paste door)
kept its own NAME -> file table and its own atomic writer; the mint path
(lib/credential_mint.py) needs the same answer for the children it mints and
the root grants it reads. One table here means a credential moved to another
file is moved for both, and a value the system mints lands exactly where the
value Joe pastes would have.

THE MAP IS AN ALLOW-LIST. destination() refuses a NAME it does not know, so
neither door can write an arbitrary key into ~/.config/carr/.

THE WRITER. write_env_line() replaces exactly the NAME=... line of a dotenv
file and leaves every other line byte-for-byte, through a 0600 temporary,
fsync, atomic rename and directory fsync, with the parent directory forced to
0700. The value is shell-quoted because the shell jobs `set -a; . file` these
files (lib/credential_file.py states that contract). A stray duplicate line for
the same NAME is dropped: two lines for one key is ambiguous to every reader.

THE LOCK. Every write holds an exclusive flock on `<file>.rotate-credential.lock`,
the same 0600 lock path tools/rotate-credential.py takes for db.env, so the mint
path and that tool can never interleave two read-modify-write cycles on one
file. A held lock is waited for, not refused: the nightly mint must not lose a
freshly minted credential because a human rotation happened to be running.

Nothing here prints or logs a value. Paths resolve against HOME at call time,
which is how the selftests point the whole store at a scratch directory.
"""
from __future__ import annotations

import fcntl
import os
import re
import shlex
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from lib.credential_file import carr_config, read_env_file

LOCK_SUFFIX = ".rotate-credential.lock"


@dataclass(frozen=True)
class Destination:
    filename: str  # under ~/.config/carr/
    key: str

    @property
    def path(self) -> Path:
        return carr_config(self.filename)


def _tokens(name: str) -> Destination:
    return Destination("tokens.env", name)


# NAME -> where it lands. Children first, then the root grants
# ops/config/credential-root-grants.v1.json names. NEON_API_KEY stays in db.env
# because every shell job that derives a connection through neonctl sources
# that file, not tokens.env.
DESTINATIONS: dict[str, Destination] = {
    "CLOUDFLARE_API_TOKEN": _tokens("CLOUDFLARE_API_TOKEN"),
    "GITHUB_FINE_GRAINED_TOKEN": _tokens("GITHUB_FINE_GRAINED_TOKEN"),
    "CLAUDE_CODE_OAUTH_TOKEN": _tokens("CLAUDE_CODE_OAUTH_TOKEN"),
    "NEON_API_KEY": Destination("db.env", "NEON_API_KEY"),
    "OPENAI_API_KEY": _tokens("OPENAI_API_KEY"),
    # Root grants. OPENAI_ADMIN_KEY is the same name tools/system_costs.py
    # already reads its billing figures with, so one placed key serves both.
    "CLOUDFLARE_TOKEN_ROOT": _tokens("CLOUDFLARE_TOKEN_ROOT"),
    "NEON_ORG_ROOT_KEY": _tokens("NEON_ORG_ROOT_KEY"),
    "OPENAI_ADMIN_KEY": _tokens("OPENAI_ADMIN_KEY"),
}


def destination(name: str) -> Destination:
    """Where NAME lands. KeyError for a NAME outside the allow-list."""
    return DESTINATIONS[name]


def read(name: str) -> str | None:
    """NAME's stored value, or None when its file or line is absent."""
    dest = destination(name)
    try:
        return read_env_file(dest.path).get(dest.key) or None
    except OSError:
        return None


def store(name: str, value: str) -> Path:
    """Write NAME's value to its destination; returns the file written."""
    dest = destination(name)
    write_env_line(dest.path, dest.key, value)
    return dest.path


_KEY_LINE_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=")


def _fsync_directory(path: str) -> None:
    fd = os.open(path or ".", os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _writer_lock(path: str) -> Iterator[None]:
    lock_path = path + LOCK_SUFFIX
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                 stat.S_IRUSR | stat.S_IWUSR)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise OSError(f"credential lock {lock_path} is not a private regular file")
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def write_env_line(path: str | os.PathLike[str], key: str, value: str) -> None:
    """Write or replace exactly the KEY=... line in path, atomically, 0600."""
    path = os.fspath(path)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    line = f"{key}={shlex.quote(value)}"

    with _writer_lock(path):
        existing: list[str] = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                existing = handle.read().splitlines()
        lines: list[str] = []
        replaced = False
        for current in existing:
            match = _KEY_LINE_RE.match(current)
            if match and match.group(1) == key:
                if not replaced:
                    lines.append(line)
                    replaced = True
                continue
            lines.append(current)
        if not replaced:
            lines.append(line)

        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".token-store.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(tmp_path, path)
            _fsync_directory(directory)
        except BaseException:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
