"""The one Python reader of ~/.config/carr/*.env credential files.

The default syntax reads literal shell assignments for callers that share files
with shell jobs. Explicit historical profiles preserve the existing syntax,
quote handling, and duplicate selection of older Python consumers. They do not
execute or expand shell expressions.

Nothing here prints, logs or returns a value to anything but its caller.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

SOURCE_BYTES = Path(__file__).read_bytes()

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


def read_env_file(path: str | os.PathLike[str], *, syntax: str = "shell",
                  duplicates: str = "last", names: tuple[str, ...] = ()) -> dict[str, str]:
    """Read literal NAME=value assignments, without executing or expanding shell
    expressions. Raises OSError when the file cannot be read.

    Historical readers retain their literal assignment syntax through explicit
    profiles: prefix, prefix-double-single, trimmed, and paired. Duplicate
    selection and quote handling remain part of each caller's contract.
    Selected first-prefix reads stop as soon as all requested names are found,
    preserving the original readers' decoding errors and early return behavior.
    """
    if syntax not in ("shell", "prefix", "prefix-double-single", "trimmed", "paired"):
        raise ValueError(f"unknown credential syntax: {syntax}")
    if duplicates not in ("first", "last"):
        raise ValueError(f"unknown duplicate policy: {duplicates}")
    values: dict[str, str] = {}
    with Path(path).open(encoding="utf-8") as source:
        lines = source if syntax.startswith("prefix") else source.read().splitlines()
        for line in lines:
            if syntax != "shell":
                if syntax in ("trimmed", "paired"):
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                if "=" not in line:
                    continue
                name, value = line.split("=", 1)
                if syntax in ("trimmed", "paired"):
                    name = name.strip()
                if names and name not in names:
                    continue
                value = value.strip()
                if syntax == "paired":
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                        value = value[1:-1]
                elif syntax == "prefix-double-single":
                    value = value.strip('"').strip("'")
                else:
                    value = value.strip("\"'")
                if duplicates == "last" or name not in values:
                    values[name] = value
                if duplicates == "first" and names and all(name in values for name in names):
                    break
                continue
            match = _ASSIGNMENT.match(line)
            if not match:
                continue
            name, raw = match.groups()
            if names and name not in names:
                continue
            try:
                value = _literal_value(raw)
            except ValueError:
                continue
            if duplicates == "last" or name not in values:
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
