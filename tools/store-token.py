#!/usr/bin/env python3
"""store-token.py — save a provider token Joe just created, with no file editing.

Usage:

    .venv/bin/python tools/store-token.py CLOUDFLARE_API_TOKEN

Joe runs this right after minting a token in a provider's dashboard. It reads
the value with getpass (never echoed, never on argv, never in this process's
own logs), VERIFIES it first where a provider probe exists, and only then
writes it into the one env file that token's consumers read. A failed verify
writes nothing and leaves whatever was already in that file untouched. Nothing
but the token's name and outcome ever reaches stdout or stderr.

VERIFY BEFORE WRITE, ALWAYS (2026-09-24 fix). The first version wrote first
and verified after, so a value Cloudflare's own probe rejected (HTTP 400) had
already landed in the file and clobbered a working token. The clipboard is
also cleared ONLY after a save that actually happened — it used to clear
unconditionally, wiping Joe's clipboard on a refused empty paste or a failed
verify, which is exactly when he still needs the value there to try again. A
refusal for a name with a known shape (Cloudflare, GitHub) also gets a
length/character-class hint, e.g. "expected 40 characters of letters, digits,
_ or -; got 37 characters, contains spaces" — never the value itself.

ALLOW-LISTED NAMES ONLY (see CREDENTIALS below). An unknown NAME is refused —
this tool is a narrow door onto a few known destinations, not a generic writer
into ~/.config/carr/.

WHERE EACH TOKEN LANDS. Every entry here writes into
~/.config/carr/tokens.env, mode 600 with the parent directory 700 — the same
env-file shape bin/routine-credential-env.sh reads (see its
carr_load_routine_db_env) and bin/staging-secrets.sh writes
(~/.config/carr/staging-tokens.env). There is no existing per-service file for
these three: grepping bin/deploy-worker.sh and the rest of the tree shows
CLOUDFLARE_API_TOKEN is read out of the ambient process environment (wrangler's
own convention, and how tools/provision-staging-app-writer.py allowlists it
through to a subprocess) rather than sourced from a repo-known file, and no
ops/release-pipeline.py exists in this tree to grep. tokens.env is therefore a
NEW shared file, following the existing db.env / staging-tokens.env
convention, not a rename of something deploy-worker.sh already reads. Joe
still needs to `source ~/.config/carr/tokens.env` (or export its lines) before
a command that expects CLOUDFLARE_API_TOKEN in its environment; this tool only
gets the value into that file safely.

VERIFICATION IS A STATUS CODE, NEVER A VALUE. Cloudflare's token-verify
endpoint and GitHub's /user endpoint are both probed with the token, and only
the HTTP status (plus, for Cloudflare, the `result.status` field) is read back
— the response body is otherwise discarded. CLAUDE_CODE_OAUTH_TOKEN has no
verify probe: presence is all this tool can attest to for it.

NO SEAL. This is a local operator script — no verb, no verb schema/flags, no
worker route, no job definition — so per the SCAC successor seal procedure a
seal is not owed (scripts and server-file edits need none; a seal is owed only
when the runtime would notice). ops/abilities-manifest.py only scans verbs,
run.sh subcommands, scheduled tasks and hooks, so this script needs no
registry entry there either; it is invoked directly, not through run.sh.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from typing import Callable, cast

ENV_DIR = os.path.expanduser("~/.config/carr")
TOKENS_ENV = os.path.join(ENV_DIR, "tokens.env")

HTTP_TIMEOUT_SECONDS = 15


def _http_status(url: str, token: str) -> tuple[int, bytes]:
    """GET url with a Bearer token; return (status, body). Never raises on HTTP
    error status -- only on a genuine network failure, which the caller turns
    into a clear, value-free message."""
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "carr-store-token",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def verify_cloudflare(token: str) -> tuple[bool, str]:
    try:
        status, body = _http_status(
            "https://api.cloudflare.com/client/v4/user/tokens/verify", token
        )
    except urllib.error.URLError as exc:
        return False, f"could not reach Cloudflare to verify: {exc.reason}"
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        parsed = {}
    result_status = None
    if isinstance(parsed, dict):
        result_status = (parsed.get("result") or {}).get("status") if isinstance(
            parsed.get("result"), dict
        ) else None
    if status == 200 and result_status == "active":
        return True, "verified active"
    return False, f"Cloudflare verify failed (HTTP {status}, result.status={result_status!r})"


def verify_github(token: str) -> tuple[bool, str]:
    try:
        status, _body = _http_status("https://api.github.com/user", token)
    except urllib.error.URLError as exc:
        return False, f"could not reach GitHub to verify: {exc.reason}"
    if status == 200:
        return True, "verified active"
    return False, f"GitHub verify failed (HTTP {status})"


# A SHAPE HINT NEVER JUDGES CORRECTNESS -- only a live verify call can do that.
# It exists so a refusal (empty paste, wrong clipboard contents, a stray
# newline) is legible without ever printing the value itself: length and which
# character classes are present are safe to report, the characters are not.
_CLOUDFLARE_ALLOWED = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)


def cloudflare_shape_hint(value: str) -> str:
    issues: list[str] = []
    if any(c.isspace() for c in value):
        issues.append("spaces")
    if "/" in value:
        issues.append("slashes")
    if any(
        (c not in _CLOUDFLARE_ALLOWED) and (not c.isspace()) and c != "/"
        for c in value
    ):
        issues.append("other characters")
    hint = f"expected 40 characters of letters, digits, _ or -; got {len(value)} characters"
    if issues:
        hint += f", contains {'/'.join(issues)}"
    return hint


def github_shape_hint(value: str) -> str:
    issues: list[str] = []
    if any(c.isspace() for c in value):
        issues.append("spaces")
    if value and not value.startswith("github_pat_"):
        issues.append("missing the github_pat_ prefix")
    hint = f"expected a github_pat_-prefixed token; got {len(value)} characters"
    if issues:
        hint += f", {'/'.join(issues)}"
    return hint


# NAME -> {"target": <path under ~/.config/carr/>, "verify": callable | None,
#          "shape_hint": callable | None}
# Room for more: add an entry here, nothing else needs to change to support it.
CREDENTIALS: dict[str, dict[str, object]] = {
    "CLOUDFLARE_API_TOKEN": {
        "target": TOKENS_ENV,
        "verify": verify_cloudflare,
        "shape_hint": cloudflare_shape_hint,
    },
    "GITHUB_FINE_GRAINED_TOKEN": {
        "target": TOKENS_ENV,
        "verify": verify_github,
        "shape_hint": github_shape_hint,
    },
    "CLAUDE_CODE_OAUTH_TOKEN": {
        "target": TOKENS_ENV,
        "verify": None,
        "shape_hint": None,
    },
}

_KEY_LINE_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=")


def _fsync_directory(path: str) -> None:
    fd = os.open(path or ".", os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_env_line(path: str, name: str, value: str) -> None:
    """Write or replace exactly the NAME=... line in path, atomically.

    Every other line is preserved byte-for-byte. The value is never logged,
    printed, or placed anywhere but this file.
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)

    existing_lines: list[str] = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            existing_lines = handle.read().splitlines()

    new_lines: list[str] = []
    replaced = False
    for line in existing_lines:
        m = _KEY_LINE_RE.match(line)
        if m and m.group(1) == name:
            if not replaced:
                new_lines.append(f"{name}={value}")
                replaced = True
            # A stray duplicate key from a hand-edit is dropped, not kept --
            # two lines for the same NAME is ambiguous for every reader of
            # this file (both the Python and the shell parser).
            continue
        new_lines.append(line)
    if not replaced:
        new_lines.append(f"{name}={value}")

    content = "\n".join(new_lines) + "\n"

    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".store-token.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp_path, path)
        _fsync_directory(directory)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def clear_clipboard() -> None:
    """Best-effort: clear the macOS clipboard so a value pasted in to check
    against the terminal (or copied out of some other tool) does not linger.
    Absent on non-macOS test/CI hosts, so a missing binary is not an error."""
    try:
        subprocess.run(
            ["pbcopy"], stdin=subprocess.DEVNULL, check=False, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def store_token(
    name: str,
    *,
    prompt: Callable[[str], str] = getpass.getpass,
    do_clear_clipboard: Callable[[], None] = clear_clipboard,
    out=sys.stdout,
    err=sys.stderr,
) -> int:
    entry = CREDENTIALS.get(name)
    if entry is None:
        allowed = ", ".join(sorted(CREDENTIALS))
        print(f"store-token: unknown NAME {name!r}. Allowed: {allowed}", file=err)
        return 64  # EX_USAGE

    shape_hint = cast("Callable[[str], str] | None", entry.get("shape_hint"))

    def _hint_suffix(value_for_hint: str) -> str:
        return f" ({shape_hint(value_for_hint)})" if shape_hint is not None else ""

    value = prompt(f"Value for {name} (hidden, not echoed): ")
    value = value.strip()
    if not value:
        print(
            f"store-token: refusing an empty value for {name}{_hint_suffix(value)}",
            file=err,
        )
        return 65  # EX_DATAERR

    target = str(entry["target"])
    verify = entry["verify"]

    # VERIFY BEFORE WRITE, ALWAYS. A live-use failure (2026-09-24) saved a
    # value Cloudflare's own verify endpoint rejected with HTTP 400: the write
    # had already happened by the time the probe ran, so a bad paste silently
    # clobbered a working token. Nothing touches the target file until a probe
    # (where one exists) has said the value is good -- on refusal, the
    # existing line is left exactly as it was, and this function exits 1
    # having written nothing.
    verify_message = None
    if verify is not None:
        verify_fn = cast(Callable[[str], tuple[bool, str]], verify)
        ok, verify_message = verify_fn(value)
        if not ok:
            print(
                f"store-token: refusing to save {name}: {verify_message}{_hint_suffix(value)}",
                file=err,
            )
            return 1

    write_env_line(target, name, value)

    if verify_message is None:
        print(f"{name} saved to {target}, no verify probe configured for it", file=out)
    else:
        print(f"{name} saved to {target}, {verify_message}", file=out)

    # The clipboard is cleared ONLY here, on a save that actually happened.
    # It used to run unconditionally, which wiped Joe's clipboard on every
    # refused empty paste and every failed verify too -- exactly the moments
    # he needs the value still sitting there to try again.
    do_clear_clipboard()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="store-token.py",
        description="Save a provider token with hidden input, verify it, clear the clipboard.",
    )
    parser.add_argument(
        "name",
        metavar="NAME",
        help=f"One of: {', '.join(sorted(CREDENTIALS))}",
    )
    args = parser.parse_args(argv)
    return store_token(args.name)


if __name__ == "__main__":
    sys.exit(main())
