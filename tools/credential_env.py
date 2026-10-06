#!/usr/bin/env python3
"""Shared loader for long-lived credentials used by UNATTENDED launchers.

WHY THIS EXISTS. Joe minted a year-long Claude Code login with
`claude setup-token`. It lives as CLAUDE_CODE_OAUTH_TOKEN in
~/.config/carr/tokens.env (mode 600, plain NAME=value lines), written by
tools/store-token.py (a separate, not-yet-merged PR — this module does not
import or depend on it). When a launchd-driven process starts a headless
`claude -p` session, it normally relies on the signed-in user's keychain
login; if that keychain entry has expired or is not reachable from a
launchd context, the session fails to start. Handing that one process the
long-lived token, in ITS OWN child environment only, avoids that failure
mode without touching how any interactive session authenticates.

THE CONTRACT, held on every read:
  * only the NAMED keys are returned — nothing else in the file leaks out;
  * values are read by lib/credential_file, the same way the shell reads them;
  * the file is refused outright if its permission bits are looser than
    600 (any group/other bit set) — a credential file the OS will let other
    local accounts read is not trusted, full stop;
  * a missing file, a missing key, or an unreadable path is silently "not
    configured" (an empty result), never an exception — the whole feature is
    optional, additive fallback, and its absence must look exactly like the
    world before this module existed;
  * NOTHING here ever logs, prints, or otherwise surfaces a value. Only key
    NAMES and boolean/"found or not" facts may appear in logs.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.credential_file import read_env_file  # noqa: E402

DEFAULT_TOKENS_PATH = Path.home() / ".config" / "carr" / "tokens.env"

# The one variable every unattended `claude -p` launcher in this repo cares
# about today. Kept as a named constant so call sites don't respell it.
CLAUDE_OAUTH_TOKEN_NAME = "CLAUDE_CODE_OAUTH_TOKEN"


class TokensFilePermissionError(RuntimeError):
    """Raised when tokens.env exists but is not mode 600 or stricter.

    The message names the path and the offending mode; it never includes
    file contents.
    """


def load_carr_tokens(names, *, path: "Path | str | None" = None) -> dict[str, str]:
    """Return the subset of `names` found in tokens.env, as {NAME: value}.

    Absent-safe: a missing file, a missing directory, or a name not present
    in the file simply is not in the returned dict — this never raises for
    that. It DOES raise TokensFilePermissionError when the file exists but
    its mode is looser than 600, because reading a group/other-readable
    credential file is exactly the mistake this loader exists to refuse.

    Reads only the requested names; nothing else in the file is returned,
    and no value is ever logged or printed by this function.
    """
    wanted = set(names)
    token_path = Path(path) if path is not None else DEFAULT_TOKENS_PATH

    try:
        st = token_path.stat()
    except (FileNotFoundError, NotADirectoryError, OSError):
        return {}

    mode = stat.S_IMODE(st.st_mode)
    if mode & 0o077:
        raise TokensFilePermissionError(
            f"{token_path} is mode {oct(mode)}; refusing to read a "
            "credentials file that is not 600 or stricter"
        )

    try:
        parsed = read_env_file(token_path)
    except OSError:
        return {}
    return {name: parsed[name] for name in wanted if name in parsed}


def claude_child_env(base_env=None, *, path=None):
    """Build a child-process env dict with the long-lived Claude login merged in.

    Returns (env, warning):
      * `env` is a NEW dict — a copy of `base_env` (or of the current
        process environment when `base_env` is None) — never the original
        object, and this function never touches os.environ itself. Merge
        the token into a subprocess call's own `env=`, never globally.
      * `warning` is None when CLAUDE_CODE_OAUTH_TOKEN was found and merged
        in, or a short, value-free line to log otherwise (the file is
        absent, the key is absent, or the file's permissions were refused).
        Callers should log this at most once per launch attempt and never
        include `env` or any token value when they do.
    """
    env = dict(base_env if base_env is not None else os.environ)
    try:
        tokens = load_carr_tokens([CLAUDE_OAUTH_TOKEN_NAME], path=path)
    except TokensFilePermissionError as exc:
        return env, f"long-lived Claude login not configured ({exc})"

    token = tokens.get(CLAUDE_OAUTH_TOKEN_NAME)
    if not token:
        return env, "long-lived Claude login not configured"

    env[CLAUDE_OAUTH_TOKEN_NAME] = token
    return env, None
