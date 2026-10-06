#!/usr/bin/env python3
"""pg-env-exec.py — run a libpq client with a connection URL taken from the
ENVIRONMENT and handed to it as PG* environment variables, never as an argument.

    PGURL_PROD="$PROD_URL" pg-env-exec.py PGURL_PROD psql -v ON_ERROR_STOP=1 -At -c "select 1"

A connection URL on a command line carries its password into every process
listing (`ps`) for as long as the client runs. This reads the URL from the
variable NAMED by the first argument, removes that variable, sets PGHOST,
PGPORT, PGUSER, PGPASSWORD, PGDATABASE and the recognised query parameters,
and exec()s the command, so stdin, stdout, exit status and any PGOPTIONS the
caller already set pass straight through. An unrecognised query parameter is
refused rather than dropped: silently losing sslmode would downgrade the link.
Nothing is printed.
"""
from __future__ import annotations

import os
import sys
import urllib.parse

QUERY_ENV = {
    "sslmode": "PGSSLMODE",
    "channel_binding": "PGCHANNELBINDING",
    "connect_timeout": "PGCONNECT_TIMEOUT",
    "application_name": "PGAPPNAME",
    "sslrootcert": "PGSSLROOTCERT",
    "target_session_attrs": "PGTARGETSESSIONATTRS",
}
CLEARED = ("PGHOST", "PGHOSTADDR", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE", "PGSERVICE",
           "PGSERVICEFILE", "PGPASSFILE", *QUERY_ENV.values())


def env_for(url: str, base: dict[str, str]) -> dict[str, str]:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("postgres", "postgresql") or not parts.hostname:
        raise ValueError("not a postgres:// connection URL")
    env = {k: v for k, v in base.items() if k not in CLEARED}
    env["PGHOST"] = parts.hostname
    if parts.port:
        env["PGPORT"] = str(parts.port)
    if parts.username:
        env["PGUSER"] = urllib.parse.unquote(parts.username)
    if parts.password is not None:
        env["PGPASSWORD"] = urllib.parse.unquote(parts.password)
    database = urllib.parse.unquote(parts.path.lstrip("/"))
    if database:
        env["PGDATABASE"] = database
    for key, values in urllib.parse.parse_qs(parts.query, keep_blank_values=True).items():
        if key == "options":
            env["PGOPTIONS"] = " ".join(filter(None, [base.get("PGOPTIONS", ""), *values]))
        elif key in QUERY_ENV:
            env[QUERY_ENV[key]] = values[-1]
        else:
            raise ValueError(f"unrecognised connection parameter {key!r}; refusing to drop it")
    return env


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: pg-env-exec.py URL_VARIABLE command [args...]", file=sys.stderr)
        return 2
    var, command = argv[1], argv[2:]
    url = os.environ.get(var, "")
    if not url:
        print(f"pg-env-exec: ${var} is empty", file=sys.stderr)
        return 2
    base = {k: v for k, v in os.environ.items() if k != var}
    try:
        env = env_for(url, base)
    except ValueError as exc:
        print(f"pg-env-exec: {exc}", file=sys.stderr)
        return 2
    os.execvpe(command[0], command, env)
    return 127  # unreachable: execvpe replaces this process or raises


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
