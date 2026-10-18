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

WHERE EACH TOKEN LANDS is lib/token_store.py's one map, shared with the nightly
mint lane (lib/credential_mint.py), and the write is that module's one 0600
atomic writer: a value Joe pastes here and a value the system mints for the
same NAME land in the same line of the same file. Most names land in
~/.config/carr/tokens.env; NEON_API_KEY lands in db.env, which every shell job
that derives a Neon connection already sources. A command that expects
CLOUDFLARE_API_TOKEN in its environment still has to load tokens.env itself
(ops/release-pipeline.py does, for its wrangler steps only).

ROOT GRANTS. The names in ops/config/credential-root-grants.v1.json
(CLOUDFLARE_TOKEN_ROOT, NEON_ORG_ROOT_KEY, OPENAI_ADMIN_KEY) are accepted too.
Each is the ONE credential Joe places per provider (decision cbe91287,
2026-10-12); the nightly 'credential mint' step mints and rotates every child
credential from it, so after this one paste he never mints those by hand again.
A root grant is verified with the provider's read-only call from
lib/credential_mint.py before it is saved, exactly like every other name here.

VERIFICATION IS A STATUS CODE, NEVER A VALUE. Cloudflare's token-verify
endpoint and GitHub's /user endpoint are both probed with the token, and only
the HTTP status (plus, for Cloudflare, the `result.status` field) is read back
— the response body is otherwise discarded. CLAUDE_CODE_OAUTH_TOKEN has no
live verify probe: only the inventory-owned token syntax is checked here.

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
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import credential_mint, token_store
from lib.credential_shape import valid_claude_token, claude_token_hint

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


def verify_claude_shape(token: str) -> tuple[bool, str]:
    # `claude setup-token` wraps the token across two Terminal lines, so a
    # one-line copy saves a short value that only fails later with HTTP 401.
    if valid_claude_token(token):
        return True, "shape checked (no live probe)"
    return False, "not a complete setup-token value; copy both wrapped lines as one"


def claude_shape_hint(value: str) -> str:
    return claude_token_hint(value)


def _verify_root(grant: str) -> Callable[[str], tuple[bool, str]]:
    return lambda value: credential_mint.verify_root_grant(grant, value)


# NAME -> {"target": <file from lib/token_store.py>, "verify": callable | None,
#          "shape_hint": callable | None}
# A new NAME needs a token_store destination first; nothing else changes.
_VERIFY: dict[str, dict[str, object]] = {
    "CLOUDFLARE_API_TOKEN": {"verify": verify_cloudflare, "shape_hint": cloudflare_shape_hint},
    "GITHUB_FINE_GRAINED_TOKEN": {"verify": verify_github, "shape_hint": github_shape_hint},
    "CLAUDE_CODE_OAUTH_TOKEN": {"verify": verify_claude_shape, "shape_hint": claude_shape_hint},
    "CLOUDFLARE_TOKEN_ROOT": {"verify": _verify_root("cloudflare"), "shape_hint": cloudflare_shape_hint},
    "NEON_ORG_ROOT_KEY": {"verify": _verify_root("neon"), "shape_hint": None},
    "OPENAI_ADMIN_KEY": {"verify": _verify_root("openai"), "shape_hint": None},
}
CREDENTIALS: dict[str, dict[str, object]] = {
    name: {"target": str(token_store.destination(name).path), **entry}
    for name, entry in _VERIFY.items()
}


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

    token_store.write_env_line(target, token_store.destination(name).key, value)

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
