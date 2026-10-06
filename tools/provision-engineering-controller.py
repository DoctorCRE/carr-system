#!/usr/bin/env python3
"""provision-engineering-controller.py — give the Engineering Passport controller its Worker credential, without
the value ever being displayed, logged, or placed on a command line.

WHY. The Model Room bridge runs bin/run-engineering-dispatch.sh after every cycle, and on 2026-09-25 it had failed
on every cycle (954 logged failures) with "isolated Worker controller configuration is required": the controller's
bearer had never been provisioned, on either Mac, and the Worker had no ENGINEERING_CONTROLLER_TOKENS secret. So
admitted engineering jobs were never claimed unattended (loop #646).

WHAT IT DOES, in this order:
  1. generates a 48-character URL-safe token in-process;
  2. sets the Worker secret ENGINEERING_CONTROLLER_TOKENS = {"codex": "<token>"} through `wrangler secret put`,
     with the value on stdin (mcp-server/wrangler.toml documents the map; the Worker resolves it to the codex actor
     and opens only the four canonical-ownership operations);
  3. writes ~/.config/carr/engineering-controller.env (mode 0600, atomic replace) with the Worker's /mcp address
     and the token, in the literal KEY=value form bin/run-engineering-dispatch.sh reads without sourcing;
  4. unless --no-verify, runs bin/run-engineering-dispatch.sh once and reports only its outcome.

Running it again rotates: the new file is staged first, then the Worker secret is replaced (the old token stops
working at once), then the staged file is swapped in. A Mac carrying the not-this-host marker refuses outright,
because rotating from the wrong Mac takes the controller host down.

    .venv/bin/python tools/provision-engineering-controller.py            # provision or rotate, then verify
    .venv/bin/python tools/provision-engineering-controller.py --check    # report state only; changes nothing

Needs Cloudflare access for the carr-mcp Worker (`wrangler whoami`). Exit 0 done, 1 a step failed, 2 usage.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP = os.path.join(REPO, "mcp-server")
WRANGLER = os.path.join(MCP, "node_modules", ".bin", "wrangler")
ENV_FILE = os.environ.get("CARR_ENGINEERING_CONTROLLER_ENV_FILE",
                          os.path.expanduser("~/.config/carr/engineering-controller.env"))
WORKER_URL = "https://api.doctorcre.com/mcp"
SECRET_NAME = "ENGINEERING_CONTROLLER_TOKENS"
ACTOR = "codex"
DISPATCH = os.path.join(REPO, "bin", "run-engineering-dispatch.sh")
# The same marker bin/run-engineering-dispatch.sh honours: a Mac carrying it is deliberately not the controller host
# (the Worker accepts one token, so one Mac holds it). Provisioning there would rotate the live host's token away.
NOT_HOST_MARKER = os.environ.get("CARR_ENGINEERING_CONTROLLER_NOT_HOST_MARKER",
                                 os.path.expanduser("~/.config/carr/engineering-controller.not-this-host"))
RUN = subprocess.run  # replaced by the tests


def new_token() -> str:
    return secrets.token_urlsafe(36)  # 48 characters


def secret_names(run=None) -> set[str]:
    p = (run or RUN)([WRANGLER, "secret", "list", "--format", "json"], cwd=MCP, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError("wrangler secret list failed (is Cloudflare signed in? `wrangler whoami`)")
    return {row.get("name") for row in json.loads(p.stdout)}


def put_secret(token: str, run=None) -> None:
    value = json.dumps({ACTOR: token}, separators=(",", ":"))
    p = (run or RUN)([WRANGLER, "secret", "put", SECRET_NAME], cwd=MCP, input=value, capture_output=True, text=True,
            timeout=180)
    if p.returncode != 0:
        # wrangler's own error text never contains the stdin value; still, report only the first line
        raise RuntimeError("wrangler secret put failed: " + ((p.stderr or p.stdout).strip().splitlines() or [""])[0][:300])


def stage_env(token: str, path: str = ENV_FILE) -> str:
    """Write the env file's new contents to a private temporary file beside it and return that path. Staging comes
    BEFORE the Worker secret changes, so a disk failure cannot leave the Worker holding a token no Mac has."""
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    body = f"CARR_ENGINEERING_WORKER_URL={WORKER_URL}\nCARR_ENGINEERING_CONTROLLER_TOKEN={token}\n"
    fd, tmp = tempfile.mkstemp(prefix=".engineering-controller.", dir=os.path.dirname(path))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        discard(tmp)
        raise
    return tmp


def discard(tmp: str) -> None:
    try:
        os.unlink(tmp)
    except OSError:
        pass


def write_env(token: str, path: str = ENV_FILE) -> None:
    os.replace(stage_env(token, path), path)


def env_state(path: str = ENV_FILE) -> str:
    if not os.path.exists(path):
        return "missing"
    mode = os.stat(path).st_mode & 0o777
    keys = set()
    with open(path) as fh:
        for line in fh:
            if "=" in line and not line.startswith("#"):
                keys.add(line.split("=", 1)[0].strip())
    ok = {"CARR_ENGINEERING_WORKER_URL", "CARR_ENGINEERING_CONTROLLER_TOKEN"} <= keys
    return f"present, mode {mode:o}" + ("" if ok and not mode & 0o077 else " (INVALID: needs both keys and mode 600)")


def verify(run=None) -> tuple[bool, str]:
    """Run the controller once. It prints one JSON readback on success; its refusals are typed one-liners."""
    p = (run or RUN)(["/bin/zsh", DISPATCH], capture_output=True, text=True, timeout=1900, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        return False, f"exit {p.returncode}: " + ((p.stderr or "").strip().splitlines() or [""])[-1][:300]
    try:
        value = json.loads(p.stdout)
    except ValueError:
        return False, "controller returned unreadable output"
    if value.get("ok") is not True:
        return False, "controller readback was not ok"
    return True, f"controller ok, claimed {value.get('claimed')} job(s)"


def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="report state only")
    ap.add_argument("--no-verify", action="store_true", help="skip the controller run afterwards")
    a = ap.parse_args(argv)
    marked = os.path.exists(NOT_HOST_MARKER)
    try:
        on_worker = SECRET_NAME in secret_names()
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"stopped: could not read the Worker's secrets ({type(exc).__name__}: {exc})"[:400], file=sys.stderr)
        return 1
    print(f"Worker secret {SECRET_NAME}: {'set' if on_worker else 'not set'}")
    print(f"Mac file {ENV_FILE}: {env_state()}")
    print(f"This Mac: {'marked not-this-host (' + NOT_HOST_MARKER + ')' if marked else 'not marked; may host'}")
    if a.check:
        return 0
    if marked:
        print(f"stopped: this Mac is marked not-this-host ({NOT_HOST_MARKER}). Provisioning here would rotate the "
              "controller host's token away and take the controller down. Run this on the controller host.",
              file=sys.stderr)
        return 1
    token = new_token()
    try:
        tmp = stage_env(token)
    except OSError as exc:
        print(f"stopped before touching the Worker: could not write {ENV_FILE}: {exc}", file=sys.stderr)
        return 1
    try:
        put_secret(token)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        discard(tmp)
        state = "state UNKNOWN (timed out); run --check" if isinstance(exc, subprocess.TimeoutExpired) else "unchanged"
        print(f"stopped: {type(exc).__name__}: {exc}"[:400] + f". Worker secret {state}; Mac file unchanged.",
              file=sys.stderr)
        return 1
    finally:
        del token  # drops this name only; the string lives until collected
    print(f"Worker secret {SECRET_NAME}: {'replaced' if on_worker else 'set'}")
    try:
        os.replace(tmp, ENV_FILE)
    except OSError as exc:
        discard(tmp)
        print(f"stopped: the Worker holds a NEW token but {ENV_FILE} could not be updated ({exc}). The controller "
              "is down until this is re-run on the controller host.", file=sys.stderr)
        return 1
    print(f"Mac file written (mode 600): {ENV_FILE}")
    if a.no_verify:
        return 0
    ok, detail = verify()
    print(("verified: " if ok else "verify FAILED: ") + detail)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
