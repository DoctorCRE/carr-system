"""The one Python reader of ~/.config/carr/*.env credential files.

These files have two consumers with one contract. Shell jobs load them with
`set -a; . db.env` (bin/nightly.sh, bin/migrate-prod.sh and the rest), so a
DSN carrying `&` must be single-quoted. Python jobs used to parse them with
their own `split("=", 1)` lines, at least nine of them, each slightly
different. On 2026-08-02 quoting a value for the shell broke four of those
readers at once and blinded the monitor that would have reported it.

read_env_file() reads a line the way the shell does: optional `export`,
single quotes literal (including rotate-credential's `'\\''` escape), double
quotes with backslash escapes, a trailing `# comment` dropped, the last
assignment winning. A line the shell could not parse (an unterminated quote)
sets nothing here either, but unlike the shell it does not take the rest of
the file down with it; tools/health-check.py's `zsh -n` row reports that.

Nothing here prints, logs or returns a value to anything but its caller.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def carr_config(name: str = "") -> Path:
    """~/.config/carr (or a file in it), resolved against HOME when called."""
    return Path.home() / ".config" / "carr" / name


def _literal_value(raw: str) -> str:
    """Decode the assignment word's quotes and escapes without shell expansion."""
    value: list[str] = []
    quote = ""
    index = 0
    while index < len(raw):
        char = raw[index]
        if quote == "'":
            if char == quote:
                quote = ""
            else:
                value.append(char)
        elif char == "\\":
            index += 1
            if index == len(raw):
                raise ValueError("unterminated escape")
            escaped = raw[index]
            if quote == '"' and escaped not in '$`"\\':
                value.append("\\")
            value.append(escaped)
        elif quote:
            if char == quote:
                quote = ""
            else:
                value.append(char)
        elif char in "'\"":
            quote = char
        elif char.isspace():
            break
        else:
            value.append(char)
        index += 1
    if quote:
        raise ValueError("unterminated quote")
    return "".join(value)


def read_env_file(path: str | os.PathLike[str]) -> dict[str, str]:
    """Read literal NAME=value assignments, without executing or expanding shell
    expressions. Raises OSError when the file cannot be read."""
    values: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        match = _ASSIGNMENT.match(line)
        if not match:
            continue
        name, raw = match.groups()
        try:
            value = _literal_value(raw)
        except ValueError:
            continue
        values[name] = value
    return values


def credential(name: str, *, path: str | os.PathLike[str] | None = None,
               environ: Mapping[str, str] | None = None) -> str | None:
    """A job's named credential: the job's environment first, else the file
    (default ~/.config/carr/db.env). None when neither holds a non-empty value
    or the file cannot be read."""
    value = (os.environ if environ is None else environ).get(name)
    if value:
        return value
    try:
        return read_env_file(path or carr_config("db.env")).get(name) or None
    except OSError:
        return None
