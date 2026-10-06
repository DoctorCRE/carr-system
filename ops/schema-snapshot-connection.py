#!/usr/bin/env python3
"""Write an owner-only libpq service entry from a snapshot connection URL.

The URL arrives on standard input so a production password never appears in a
client command line or environment.  The generated service file is ephemeral;
schema-snapshot.sh removes it on exit.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit


SAFE_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
RESERVED_KEYS = frozenset({"host", "hostaddr", "port", "dbname", "user", "password", "service", "servicefile", "passfile"})


def decoded(value: str) -> str:
    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        raise ValueError("invalid URL escape")
    result = unquote(value)
    if not result or any(character in result for character in "\x00\r\n"):
        raise ValueError("unsafe connection value")
    return result


def connection_values(raw: str) -> dict[str, str]:
    if not raw or any(character in raw for character in "\x00\r\n"):
        raise ValueError("missing or unsafe connection URL")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise ValueError("unsupported connection URL")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("invalid port") from error
    database = parsed.path.removeprefix("/")
    if parsed.path != f"/{database}" or not database or "/" in database:
        raise ValueError("invalid database")
    values = {
        "host": decoded(parsed.hostname),
        "port": str(port or 5432),
        "dbname": decoded(database),
    }
    if parsed.username is not None:
        values["user"] = decoded(parsed.username)
    if parsed.password is not None:
        values["password"] = decoded(parsed.password)
    seen = set(values)
    for key, value in parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True):
        if key in RESERVED_KEYS or not SAFE_KEY.fullmatch(key) or key in seen:
            raise ValueError("unsafe connection parameter")
        values[key] = decoded(value)
        seen.add(key)
    return values


def service_lines(raw: str) -> list[str]:
    values = connection_values(raw)
    # libpq service files do not strip single quotes around numeric values. Keep
    # this file to syntax-safe connection selectors; the password belongs in the
    # separately parsed .pgpass file below.
    return ["[schema_snapshot]", *(f"{key}={value}" for key, value in values.items() if key != "password")]


def passfile_line(raw: str) -> str:
    values = connection_values(raw)
    password = values.get("password", "")
    def escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace(":", "\\:")
    fields = (values["host"], values["port"], values["dbname"], values.get("user", "*"))
    return ":".join(escape(value) for value in fields) + ":" + escape(password)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--write-service", type=Path, required=True)
    parser.add_argument("--write-passfile", type=Path, required=True)
    args = parser.parse_args()
    try:
        raw = sys.stdin.read()
        lines = service_lines(raw)
        passline = passfile_line(raw)
        # mktemp has already created this path; refuse an accidental redirect.
        if (args.write_service.is_symlink() or not args.write_service.is_file()
                or args.write_passfile.is_symlink() or not args.write_passfile.is_file()):
            raise ValueError("unsafe service path")
        os.chmod(args.write_service, 0o600)
        os.chmod(args.write_passfile, 0o600)
        args.write_service.write_text("\n".join(lines) + "\n", encoding="utf-8")
        args.write_passfile.write_text(passline + "\n", encoding="utf-8")
        os.chmod(args.write_service, 0o600)
        os.chmod(args.write_passfile, 0o600)
    except (OSError, ValueError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
