#!/usr/bin/env python3
"""ops/credential-health.py — the daily credential-health lane.

WHY THIS EXISTS (2026-09-24, Joe's unattended-CARR push). Joe is replacing
every interactive login CARR depends on with a long-lived scoped token, so
nothing here should ever again stop unattended because a human forgot to
re-authenticate. That only works if something tells him BEFORE a credential
lapses, not after a nightly chain goes dark. This is that something: one lane,
wired into tools/health-check.py the same way rules-live and forgetting are
(see that file, just above the "schedule drift" section), driven by the data
file ops/config/credential-inventory.v1.json so a new credential is a config
edit, never a code change.

WHAT IT DOES, in order:
  1. Reads the inventory (name, kind, machine(s), where it lives, a liveness
     probe, a replacement plan, a doc pointer).
  2. Runs each probe with a timeout. A probe never inspects or stores what a
     command prints or what an HTTP response body carries, beyond the one
     named field (Cloudflare's expires_on) this file's docstring on
     `_probe_cloudflare` explains and justifies. Every other probe reads an
     exit status or an HTTP status code and nothing else.
  3. Buckets each credential ok / expiring_soon / failed / unknown.
  4. Appends {name, status, checked_at[, expires_at]} — NEVER anything else —
     to out/credential-health.jsonl.
  5. On failed or expiring_soon, files exactly one CARR loop per credential
     name through `./run.sh call add-loop` (the allowlisted Bash door;
     CLAUDE.md — carries no credential), deduplicated across ticks in
     out/credential-health-loop-dedup.json so the same lapse does not refile
     every day it stays lapsed.
  6. Never fails silently: a probe that cannot run reports `unknown`, and an
     unreadable inventory is reported and exits nonzero rather than reporting
     a clean sweep of nothing.

INJECTABLE I/O, for the selftest (ops/credential-health-selftest.py). Nothing
below calls subprocess.run / urllib directly except through the four module
level names SUBPROCESS_RUN, HTTP_GET, HTTP_POST — a selftest replaces those
with fake probe runners and gets full control over every probe's outcome,
including one that tries to leak a fake secret through stdout, with no
network or subprocess access of its own.

Run by hand:  ./.venv/bin/python ops/credential-health.py
Verification-only (no loops filed): add --no-file-loops
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import request as _urllib_request
from urllib.error import HTTPError, URLError

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from lib.credential_file import read_env_file  # noqa: E402
from lib.record_call import call_verb  # noqa: E402

INVENTORY_PATH = REPO_ROOT / "ops" / "config" / "credential-inventory.v1.json"
OUT_JSONL = REPO_ROOT / "out" / "credential-health.jsonl"
DEDUP_PATH = REPO_ROOT / "out" / "credential-health-loop-dedup.json"
MINT_STATE_PATH = REPO_ROOT / "out" / "credential-health-mint-state.json"

BUCKETS = ("ok", "expiring_soon", "failed", "unknown")
DEFAULT_TIMEOUT_S = 10
DEFAULT_EXPIRING_SOON_DAYS = 3
JSONL_ALLOWED_KEYS = {"name", "status", "checked_at", "expires_at"}

# ── injectable I/O ────────────────────────────────────────────────────────
# Every probe below goes through exactly these names. A selftest that
# monkeypatches them controls every probe result with no real subprocess,
# network, or `claude` CLI access, and can prove a probe that tries to print
# a fake secret never reaches out/credential-health.jsonl.
SUBPROCESS_RUN = subprocess.run
WHICH = shutil.which

# The claude-cli-token probe's persisted first-seen-mint-date bookkeeping
# (see `_probe_claude_cli_token_age` below). A plain module-level dict, same
# injectable-name pattern as SUBPROCESS_RUN/HTTP_GET/HTTP_POST above: main()
# loads it from disk into this name before running probes and saves it back
# after, and a selftest can swap in its own dict with no disk access at all.
# It holds a token FINGERPRINT (sha256) and a date, never the token itself.
MINT_STATE: dict = {}

# Set explicitly because Python's urllib default ("Python-urllib/3.x") is a
# known Cloudflare bot-fight-mode signature: the deployed Worker's edge
# blocked EVERY urllib request with a bare 403 (Cloudflare error 1010)
# regardless of an otherwise-perfectly-formed, correctly-authenticated
# JSON-RPC call — reproduced live 2026-09-24 (PR #1218 review, round 2) and
# fixed by nothing except this header; curl (which smoke-reads.sh and this
# lane's own selftests-by-precedent use) was never affected because its
# default User-Agent isn't on that list.
_HTTP_USER_AGENT = "carr-credential-health/1 (+ops/credential-health.py)"


def _default_http_get(url, headers, timeout_s):
    """GET -> (status_code, parsed_json_or_None). Body capped at 64KiB and
    parsed once here; callers below pull out at most one or two named fields
    and the rest of the parsed object is discarded by them, never logged."""
    hdrs = dict(headers)
    hdrs.setdefault("user-agent", _HTTP_USER_AGENT)
    req = _urllib_request.Request(url, headers=hdrs, method="GET")
    try:
        with _urllib_request.urlopen(req, timeout=timeout_s) as resp:
            status = resp.status
            raw = resp.read(65536)
    except HTTPError as e:
        return e.code, None
    except (URLError, TimeoutError, OSError, ValueError):
        return None, None
    try:
        return status, json.loads(raw.decode("utf-8", "replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return status, None


_HTTP_POST_BODY_CAP = 1_048_576  # 1 MiB — see docstring below


def _default_http_post(url, headers, body, timeout_s):
    """POST JSON -> (status_code, parsed_json_or_None) — same shape as
    _default_http_get, added 2026-09-24 (PR #1218 review, round 2) because a
    bearer probe needs more than a status code to call a credential 'ok':
    an HTTP 200 that carries a JSON-RPC `error` instead of a `result` is
    NOT a passing call (see _probe_worker_bearer), and status alone cannot
    tell the two apart.

    Capped at 1 MiB, not the GET helper's 64KiB: this lane's one POST caller
    calls `tools/list` (the same call smoke-reads.sh's own preflight makes),
    whose response is the FULL published tool catalog — ~93KB against the
    live Worker as of 2026-09-24 — not a small status object like
    Cloudflare's or Neon's GET responses. The 64KiB cap silently truncated
    that body mid-object, so json.loads raised and every worker_bearer probe
    read a fabricated 'malformed body' failure even when the call had fully
    succeeded (PR #1218 review, round 2) — the response was never too big to
    trust, only too big for the wrong cap."""
    data = json.dumps(body).encode("utf-8")
    hdrs = dict(headers)
    hdrs["content-type"] = "application/json"
    hdrs.setdefault("user-agent", _HTTP_USER_AGENT)
    req = _urllib_request.Request(url, headers=hdrs, data=data, method="POST")
    try:
        with _urllib_request.urlopen(req, timeout=timeout_s) as resp:
            status = resp.status
            raw = resp.read(_HTTP_POST_BODY_CAP)
    except HTTPError as e:
        return e.code, None
    except (URLError, TimeoutError, OSError, ValueError):
        return None, None
    try:
        return status, json.loads(raw.decode("utf-8", "replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return status, None


HTTP_GET = _default_http_get
HTTP_POST = _default_http_post


class ProbeResult:
    __slots__ = ("bucket", "detail", "expires_at")

    def __init__(self, bucket, detail, expires_at=None):
        assert bucket in BUCKETS, f"unknown bucket {bucket!r}"
        self.bucket = bucket
        self.detail = detail
        self.expires_at = expires_at


# ── probe handlers ───────────────────────────────────────────────────────
# Every handler returns a ProbeResult built from a status code / exit code,
# never from raw probe output. `detail` is always one of this module's own
# short fixed strings, never text taken from a subprocess or an HTTP body.

def _probe_shell_exit(cred, timeout_s):
    spec = cred["probe"]
    cmd = spec["command"]
    cwd = str(REPO_ROOT / spec["cwd"]) if spec.get("cwd") else str(REPO_ROOT)
    try:
        p = SUBPROCESS_RUN(cmd, cwd=cwd, timeout=timeout_s,
                            capture_output=True)
    except FileNotFoundError:
        return ProbeResult("unknown", "tool_not_installed")
    except subprocess.TimeoutExpired:
        return ProbeResult("unknown", "timeout")
    except OSError:
        return ProbeResult("unknown", "probe_os_error")
    # p.stdout / p.stderr are deliberately never read past this line.
    if p.returncode == 0:
        return ProbeResult("ok", "exit_status_ok")
    return ProbeResult("failed", "exit_status_nonzero")


def _probe_cloudflare(cred, timeout_s):
    """CLOUDFLARE_API_TOKEN, once it exists, is checked against Cloudflare's
    own token-verify endpoint. The ONLY field ever pulled from that response
    is result.expires_on — a status field of the token, not the token itself
    or any account data — which is exactly what bucket rule 3 (task spec)
    calls for: an expiry window, read without the token body. Until that
    token exists this falls back to the interactive `wrangler whoami` exit
    status, same as every other CLI probe in this file."""
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    if not token:
        return _probe_shell_exit(cred, timeout_s)
    status, body = HTTP_GET(
        "https://api.cloudflare.com/client/v4/user/tokens/verify",
        {"Authorization": f"Bearer {token}"}, timeout_s)
    if status is None:
        return ProbeResult("unknown", "timeout")
    if status == 200:
        expires_at = None
        if isinstance(body, dict):
            expires_at = ((body.get("result") or {}).get("expires_on"))
        return ProbeResult("ok", "http_ok", expires_at)
    if status in (401, 403):
        return ProbeResult("failed", "http_unauthorized")
    return ProbeResult("failed", "http_error")


def _read_token_from_env_file(path, key):
    """Return (value_or_None, mode_or_None, error_detail_or_None) for one
    NAME=value line in a dotenv-shaped file. `value` is the ONLY thing in this
    module that ever holds the live credential, and it is used for exactly one
    thing — the Authorization header of the one HTTP call right after this
    returns — never printed, never logged, never put in a ProbeResult.detail
    or anywhere else. `mode` is the file's permission bits, read whether or
    not the key was found, so a caller can flag a loose mode even when the
    token itself is fine."""
    try:
        st = os.stat(path)
    except OSError:
        return None, None, "token_file_missing"
    mode = stat.S_IMODE(st.st_mode)
    try:
        values = read_env_file(path)
    except OSError:
        return None, mode, "token_file_unreadable"
    if key in values:
        return values[key], mode, None
    return None, mode, "token_key_missing"


def _probe_cloudflare_token_file(cred, timeout_s):
    """CLOUDFLARE_API_TOKEN, read from its own dotenv-shaped file (Joe's own
    NAME=value paste, per bin/staging-secrets.sh's convention for every other
    machine token in this repo — never a repo file, never printed). Verified
    the same way the Cloudflare dashboard verifies it: GET .../tokens/verify,
    reading only the HTTP status and the two response fields this function
    names — result.status ('active' is the only passing value Cloudflare
    documents) and result.expires_on, read only if the response carries it.
    Nothing else in the response body is ever touched.

    A LOOSE FILE MODE IS ITS OWN FINDING, checked before the token is even
    used and regardless of whether the token itself still verifies — a
    600-mode file that started leaking to the group or to everyone is a
    problem independent of whether Cloudflare still honours the token inside
    it, and this lane's whole job is to say so before something worse reads
    it."""
    spec = cred["probe"]
    path = os.path.expanduser(spec.get("path", "~/.config/carr/tokens.env"))
    key = spec.get("token_key", "CLOUDFLARE_API_TOKEN")
    token, mode, err = _read_token_from_env_file(path, key)
    if mode is not None and (mode & 0o077):
        # Anything beyond owner read/write (0o600) is looser than required —
        # group or other can read a live deploy credential off disk.
        return ProbeResult("failed", "insecure_file_permissions")
    if err:
        return ProbeResult("failed", err)

    url = spec.get("url", "https://api.cloudflare.com/client/v4/user/tokens/verify")
    status, body = HTTP_GET(url, {"Authorization": f"Bearer {token}"}, timeout_s)
    token = None  # the only local name that ever held it; done with it now
    if status is None:
        return ProbeResult("unknown", "timeout")
    if status in (401, 403):
        return ProbeResult("failed", "token_unauthorized")
    if status != 200:
        return ProbeResult("failed", "http_error")
    result = (body or {}).get("result") if isinstance(body, dict) else None
    token_status = (result or {}).get("status")
    expires_at = (result or {}).get("expires_on")
    if token_status != "active":
        return ProbeResult("failed", "token_status_not_active", expires_at)
    return ProbeResult("ok", "active", expires_at)


CLAUDE_TOKEN_PREFIX = "sk-ant-oat"
CLAUDE_TOKEN_LENGTH = 108


def _sha256_hex(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _record_mint_date_if_new(cred_name, token_sha256, state, today_iso):
    """Returns the minted_at date this credential should be aged from: the
    one already on file, if the token's FINGERPRINT (sha256, never the token
    itself) still matches what was recorded — or today, freshly recorded,
    the first time this lane has ever seen this credential, or whenever the
    fingerprint changes (a rotation: a new token was pasted in, so its clock
    restarts). Mutates `state` in place; the caller persists it."""
    entry = state.get(cred_name)
    if entry and entry.get("token_sha256") == token_sha256 and entry.get("minted_at"):
        return entry["minted_at"]
    state[cred_name] = {"token_sha256": token_sha256, "minted_at": today_iso}
    return today_iso


def _probe_claude_cli_token_age(cred, timeout_s):
    """CLAUDE_CODE_OAUTH_TOKEN, the long-lived token `claude setup-token`
    mints to replace an interactive Claude Code CLI login. Three checks, per
    Joe's spec:

    1. PRESENT AND CORRECTLY SHAPED — read from its dotenv file (never
       printed), checked only for length and prefix, never inspected further.
    2. A CHEAP AUTH READBACK — `claude -p "Reply with exactly: PONG"
       --max-turns 1`, run from a throwaway temp cwd with ONLY
       CLAUDE_CODE_OAUTH_TOKEN in its environment (no ambient PATH, no
       inherited keychain login) so a PONG reply proves the TOKEN authenticated,
       not some other credential already sitting on the machine. Only the
       three-letter word PONG is ever looked for in the reply; the rest of
       whatever the CLI printed is discarded exactly like every other probe's
       stdout in this file.
    3. AN EXPIRY WARNING FROM A RECORDED MINT DATE — this token carries no
       expiry Cloudflare-style; `claude setup-token` documents roughly a
       year of validity from when it was minted, and nothing this lane can
       query tells it that date after the fact. So the lane records ITS OWN
       first-seen date the first time it observes this exact token (by
       fingerprint, never the value) and ages from there. The inventory sets
       expiring_soon_days to 35, i.e. the 330-day warning Joe asked for out
       of a ~365-day token life.
    """
    spec = cred["probe"]
    path = os.path.expanduser(spec.get("path", "~/.config/carr/tokens.env"))
    key = spec.get("token_key", "CLAUDE_CODE_OAUTH_TOKEN")
    token, _mode, err = _read_token_from_env_file(path, key)
    if err:
        return ProbeResult("unknown" if err == "token_file_missing" else "failed", err)

    expected_prefix = spec.get("expected_prefix", CLAUDE_TOKEN_PREFIX)
    expected_length = spec.get("expected_length", CLAUDE_TOKEN_LENGTH)
    if len(token) != expected_length or not token.startswith(expected_prefix):
        token = None
        return ProbeResult("failed", "token_malformed")

    claude_bin = spec.get("claude_bin") or WHICH("claude")
    if not claude_bin:
        token = None
        return ProbeResult("unknown", "claude_cli_not_found")

    tmpdir = tempfile.mkdtemp(prefix="credential-health-claude-probe-")
    try:
        p = SUBPROCESS_RUN(
            [claude_bin, "-p", "Reply with exactly: PONG", "--max-turns", "1"],
            cwd=tmpdir, env={"CLAUDE_CODE_OAUTH_TOKEN": token},
            capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        token = None
        return ProbeResult("unknown", "timeout")
    except OSError:
        token = None
        return ProbeResult("unknown", "probe_os_error")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    token_sha256 = _sha256_hex(token)
    token = None
    # Scanned only for the fixed word this probe itself asked for — never
    # stored, never printed beyond this boolean.
    if "PONG" not in (p.stdout or ""):
        return ProbeResult("failed", "readback_failed")

    minted_at = _record_mint_date_if_new(
        cred["name"], token_sha256, MINT_STATE,
        datetime.now(timezone.utc).date().isoformat())
    try:
        minted_dt = datetime.strptime(minted_at, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        expires_at = (minted_dt + timedelta(days=365)).date().isoformat()
    except ValueError:
        expires_at = None
    return ProbeResult("ok", "active", expires_at)


def _read_token_file_first(spec, default_token_key):
    """Shared file-first, env-fallback-only-if-absent policy for a bearer-style
    credential. If `spec["path"]` is configured, that FILE is authoritative
    whenever it exists: a missing key or an unreadable file is a genuine
    finding (returned as `hard_fail_detail`) and is never silently papered
    over by falling back to the environment underneath it. Only when the
    file itself does not exist does this fall back to `os.environ`, which is
    the legacy behaviour for a credential that has not moved to a file yet
    on this machine.

    This is the same file-vs-environment posture as
    `_probe_cloudflare_token_file` and `_probe_claude_cli_token_age`; it
    exists so `_probe_neon` and `_probe_worker_bearer` stop being the two
    probes in this module that only ever look at `os.environ` — the one
    place `bin/routine-credential-env.sh`'s `carr_routine_exec` strips bare,
    which is exactly why those two read 'unknown' every night regardless of
    whether the credential is actually fine.

    Returns (value_or_None, hard_fail_detail_or_None)."""
    token_key = spec.get("token_key", default_token_key)
    path = spec.get("path")
    if path:
        expanded = os.path.expanduser(path)
        if os.path.exists(expanded):
            value, _mode, err = _read_token_from_env_file(expanded, token_key)
            if err:
                return None, err
            return value, None
        # A path is configured but nothing is there yet on THIS machine —
        # fall through to the environment, deliberately, rather than firing
        # a false "file missing" finding for a credential that simply still
        # lives ambiently here.
    return (os.environ.get(token_key, "") or None), None


def _probe_neon(cred, timeout_s):
    """NEON_API_KEY against Neon's own control-plane API.

    GET /api_keys — the exact path tools/rotate-credential.py's neon()
    helper already calls to verify a freshly-rotated key before writing it
    (see that file's `neon()`/`_rotate_api_key_locked` around line 730) —
    rather than /projects, which this probe used to call and which needs an
    organisation scope this key may not carry: it returned a bare 400 for a
    perfectly good key (PR #1218 review, round 2), a probe bug, not a
    finding about the credential.

    401/403 is treated as the only unambiguous "this key does not
    authenticate" shape Neon uses. Anything else non-2xx is reported as
    `unknown` with the status code in the detail — an ambiguous response,
    never guessed at as either a pass or a fail."""
    spec = cred["probe"]
    key, hard_fail = _read_token_file_first(spec, "NEON_API_KEY")
    if hard_fail:
        return ProbeResult("failed", hard_fail)
    if not key:
        return ProbeResult("unknown", "env_var_missing")
    url = spec.get("url", "https://console.neon.tech/api/v2/api_keys")
    status, _body = HTTP_GET(
        url, {"Authorization": f"Bearer {key}", "Accept": "application/json"},
        timeout_s)
    key = None  # the only local name that ever held it; done with it now
    if status is None:
        return ProbeResult("unknown", "timeout")
    if 200 <= status < 300:
        return ProbeResult("ok", "http_ok")
    if status in (401, 403):
        return ProbeResult("failed", "http_unauthorized")
    return ProbeResult("unknown", f"http_status_{status}")


def _probe_worker_bearer(cred, timeout_s):
    """A machine-actor bearer (CARR_MCP_PROBE_TOKEN / CARR_MCP_LOCAL_TOKEN)
    against the deployed Worker's authenticated /mcp read (`tools/list`).

    OK REQUIRES TWO THINGS, NOT ONE (PR #1218 review, round 2): an HTTP 200
    AND a JSON-RPC `result` key in the body. Status alone was the original
    design (RFC 6750 puts an invalid bearer at HTTP 401, confirmed live
    against the real Worker — a bad token returns 401 with
    `{"error":"invalid_token",...}` at the TRANSPORT layer, never a 200
    dressed up as a failure), which still holds for classifying a bad
    token — but a 200 whose JSON-RPC body carries an `error` instead of a
    `result` is not proof of a working call either, so the response body's
    top-level SHAPE (does `result` or `error` appear) is read as the second
    half of the check. Nothing inside either key is ever read, stored, or
    printed — only which one is present.

    The token itself is read file-first via `_read_token_file_first` (see
    that function for the exact policy), falling back to `spec["token_env"]`
    in the environment only when no file is configured or present."""
    spec = cred["probe"]
    token, hard_fail = _read_token_file_first(spec, spec.get("token_env", ""))
    if hard_fail:
        return ProbeResult("failed", hard_fail)
    if not token:
        return ProbeResult("unknown", "env_var_missing")
    url = os.environ.get(spec.get("url_env", ""), "") or spec.get("url", "")
    if not url:
        token = None
        return ProbeResult("unknown", "config_incomplete")
    status, body = HTTP_POST(
        url, {"Authorization": f"Bearer {token}"},
        {"jsonrpc": "2.0", "id": 0, "method": "tools/list"}, timeout_s)
    token = None
    if status is None:
        return ProbeResult("unknown", "timeout")
    if status in (401, 403):
        return ProbeResult("failed", "http_unauthorized")
    if status != 200:
        return ProbeResult("failed", "http_error")
    if isinstance(body, dict) and "result" in body:
        return ProbeResult("ok", "http_ok")
    return ProbeResult("failed", "jsonrpc_error_or_malformed_body")


def _probe_file_presence_age(cred, timeout_s):
    """Presence + mtime age only — the file's contents are never opened."""
    path = os.path.expanduser(cred["probe"]["path"])
    try:
        st = os.stat(path)
    except OSError:
        return ProbeResult("unknown", "not_found_on_machine")
    age_days = (time.time() - st.st_mtime) / 86400.0
    warn_after = cred["probe"].get("warn_after_days")
    if warn_after is not None and age_days > warn_after:
        return ProbeResult("expiring_soon", "stale_credential_file")
    return ProbeResult("ok", "present_on_machine")


def _probe_config_only(cred, timeout_s):
    """No live probe is possible (Google's OAuth client — Joe holds it, no
    agent ever has, per secrets-inventory.md). The real bucket comes entirely
    from `_apply_expiry_window` below, driven by the `expiry` block Joe edits
    by hand in the inventory."""
    return ProbeResult("ok", "config_only_no_probe")


PROBE_HANDLERS = {
    "shell_exit_status": _probe_shell_exit,
    "cloudflare_auth": _probe_cloudflare,
    "cloudflare_token_file": _probe_cloudflare_token_file,
    "claude_cli_token_age": _probe_claude_cli_token_age,
    "neon_api": _probe_neon,
    "worker_bearer_health": _probe_worker_bearer,
    "file_presence_age": _probe_file_presence_age,
    "config_only": _probe_config_only,
}


def _apply_expiry_window(cred, result, now):
    """Upgrade an `ok` probe result to expiring_soon/failed when a known
    expiry window says so. Never downgrades a failed/unknown probe — those
    already carry the worse news. Three sources of a window, per bucket rule
    3: a Cloudflare expires_on the probe itself returned, a configured PAT
    rotation date Joe sets in the inventory, or the Google testing-mode
    7-day window computed from the two fields Joe sets there."""
    expiry_cfg = cred.get("expiry") or {}
    expires_at = result.expires_at
    is_config_only = result.detail == "config_only_no_probe"
    testing_mode = expiry_cfg.get("google_testing_mode")

    if testing_mode is True:
        last_verified = expiry_cfg.get("consent_screen_last_verified")
        if not last_verified:
            return ProbeResult("unknown", "config_incomplete")
        try:
            lv = datetime.strptime(last_verified, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return ProbeResult("unknown", "config_incomplete")
        expires_at = (lv + timedelta(days=7)).date().isoformat()
        result = ProbeResult("ok", "google_testing_mode_active", expires_at)
    elif testing_mode is False:
        # Consent screen is published; Google does not expire the refresh
        # token on a 7-day cycle. Nothing to compute, but it IS a positive
        # answer Joe gave us, not silence — record it as such.
        result = ProbeResult("ok", "consent_screen_published", expires_at)
    elif is_config_only:
        # No live probe is possible for this credential AND Joe has not set
        # the testing-mode flag yet. That is genuinely unknown, not a clean
        # bill of health by default — an unset config field must never read
        # as "ok" (rule 6: never fail silently).
        return ProbeResult("unknown", "not_configured")
    elif expiry_cfg.get("configured_rotation_date"):
        expires_at = expires_at or expiry_cfg["configured_rotation_date"]

    if not expires_at:
        return result
    try:
        exp_dt = datetime.strptime(str(expires_at)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return ProbeResult(result.bucket, result.detail, expires_at)

    if result.bucket != "ok":
        return ProbeResult(result.bucket, result.detail, expires_at)

    days_left = (exp_dt - now).total_seconds() / 86400.0
    soon = cred.get("expiring_soon_days")
    if soon is None:
        soon = DEFAULT_EXPIRING_SOON_DAYS
    if days_left < 0:
        return ProbeResult("failed", "past_expiry", expires_at)
    if days_left <= soon:
        return ProbeResult("expiring_soon", "within_expiry_window", expires_at)
    return ProbeResult("ok", result.detail, expires_at)


def evaluate_credential(cred, default_timeout_s=DEFAULT_TIMEOUT_S):
    ptype = (cred.get("probe") or {}).get("type")
    handler = PROBE_HANDLERS.get(ptype)
    if handler is None:
        return ProbeResult("unknown", "unknown_probe_type")
    timeout_s = (cred.get("probe") or {}).get("timeout_s", default_timeout_s)
    try:
        result = handler(cred, timeout_s)
    except Exception:
        # A probe handler that raises is exactly as "cannot verify" as one
        # that times out. Never let an unexpected exception propagate and
        # abort the whole run over one bad credential.
        return ProbeResult("unknown", "probe_exception")
    return _apply_expiry_window(cred, result, datetime.now(timezone.utc))


# ── inventory / output ───────────────────────────────────────────────────

def load_inventory(path=INVENTORY_PATH):
    with open(path) as fh:
        data = json.load(fh)
    return data.get("credentials", [])


def run_all(credentials, default_timeout_s=DEFAULT_TIMEOUT_S):
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for cred in credentials:
        result = evaluate_credential(cred, default_timeout_s)
        row = {"name": cred["name"], "status": result.bucket, "checked_at": now_iso}
        if result.expires_at:
            row["expires_at"] = result.expires_at
        rows.append(row)
    return rows


def write_jsonl(rows, path=OUT_JSONL):
    """Raises on a disallowed-key row (that assertion is the audit-trail
    invariant itself and must propagate — see JSONL_ALLOWED_KEYS) but wraps
    the actual disk write the same way every other stateful write in this
    module does, so a full disk or a permissions problem on `out/` surfaces
    as a clear, callable-checkable failure rather than an unhandled
    traceback. The caller (main()) treats a write failure here as fatal: it
    is this lane's entire audit trail for the night, not a degradable
    extra."""
    path = Path(path)
    lines = []
    for row in rows:
        # Names-and-statuses-only, enforced here rather than trusted: a row
        # carrying any other key is a defect in THIS file, not in the
        # caller, and it must not reach disk.
        extra = set(row.keys()) - JSONL_ALLOWED_KEYS
        if extra:
            raise AssertionError(f"credential-health: refusing to write disallowed "
                                  f"jsonl keys {sorted(extra)}")
        lines.append(json.dumps(row, sort_keys=True) + "\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fh.writelines(lines)


# ── mint-date state (claude-cli-token-age probe) ─────────────────────────
# Same load/save shape as the loop dedup store just below, kept as its own
# file and its own function pair because the two stores answer different
# questions and must not share a schema by accident: dedup tracks "have I
# already filed a loop for this bucket", mint state tracks "when did I first
# see this exact token". Holds {cred_name: {token_sha256, minted_at}} only —
# a fingerprint and a date, never a credential value.

def _load_mint_state(path=MINT_STATE_PATH):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_mint_state(state, path=MINT_STATE_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as fh:
        json.dump(state, fh, sort_keys=True, indent=2)
    tmp.replace(path)


# ── loop filing, deduplicated ────────────────────────────────────────────

def _load_dedup(path=DEDUP_PATH):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_dedup(state, path=DEDUP_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as fh:
        json.dump(state, fh, sort_keys=True, indent=2)
    tmp.replace(path)


def _credential_file_exists(cred):
    """True only when this credential's probe names a `path` AND that path
    exists on this machine right now. Used to decide whether a nightly
    `unknown` is real news (a credential Joe has provisioned here that this
    lane still could not verify — worth a loop) or ordinary silence (nothing
    is configured here yet, e.g. Codex on a Claude-only launch machine)."""
    path = (cred.get("probe") or {}).get("path")
    if not path:
        return False
    return os.path.exists(os.path.expanduser(path))


def _loop_body(cred, bucket):
    """Plain words: what to create, and where. No secret ever appears here —
    only the fields the inventory itself carries (never a value)."""
    if bucket == "failed":
        verb = "has FAILED its daily liveness probe"
    elif bucket == "expiring_soon":
        verb = "is EXPIRING SOON"
    else:
        verb = ("could NOT be verified even though its credential file is present "
                 "on this machine — the probe itself may be broken or unreachable, "
                 "not necessarily the credential")
    where = cred.get("location", "location not recorded")
    plan = cred.get("replacement_plan", "no replacement plan recorded")
    doc = cred.get("doc_pointer", "")
    parts = [
        f"Credential '{cred.get('display_name', cred['name'])}' ({cred['name']}) {verb}.",
        f"Where it lives: {where}.",
        f"What to create: {plan}",
    ]
    if doc:
        parts.append(f"Doc pointer: {doc}.")
    return " ".join(parts)


def file_loop_if_needed(cred, bucket, dedup_state, dry_run=False, timeout_s=30):
    """Returns 'deduped' | 'dry_run' | 'filed' | 'file_failed'. Files at most
    one loop per (credential, bucket) — a bucket CHANGE (e.g. expiring_soon
    -> failed) is allowed to refile, since that is new news; the same bucket
    repeating every tick is not."""
    name = cred["name"]
    prior = dedup_state.get(name)
    if prior and prior.get("bucket") == bucket:
        return "deduped"
    if dry_run:
        dedup_state[name] = {
            "bucket": bucket,
            "filed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dry_run": True,
        }
        return "dry_run"
    payload = {
        "idempotency_key": str(uuid.uuid4()),
        "kind": "open_loop",
        "owner": "Joe",
        "domain": "system",
        "blocker": "capability",
        "blocker_detail": f"{name} credential-health probe",
        "body": _loop_body(cred, bucket),
    }
    filed_ok = call_verb("add-loop", payload, timeout=timeout_s, runner=SUBPROCESS_RUN).ok
    dedup_state[name] = {
        "bucket": bucket,
        "filed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "filed_ok": filed_ok,
    }
    return "filed" if filed_ok else "file_failed"


# ── CLI ───────────────────────────────────────────────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(description="Daily credential-health lane")
    ap.add_argument("--inventory", default=str(INVENTORY_PATH))
    ap.add_argument("--out", default=str(OUT_JSONL))
    ap.add_argument("--dedup-store", default=str(DEDUP_PATH))
    ap.add_argument("--mint-state", default=str(MINT_STATE_PATH))
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--no-file-loops", action="store_true",
                     help="report statuses but never call add-loop (verification runs)")
    ap.add_argument("--nightly", action="store_true",
                     help="unattended-chain exit code: 0 whenever the lane itself ran to "
                          "completion, even with failed/expiring_soon findings — those "
                          "already have their own alerting channel (the loop this run "
                          "files). Matches the repo's other 'reports, never mutates' "
                          "nightly steps (e.g. ops/rule-admission-drift.py), which return "
                          "0 on a finding and reserve nonzero for the check ITSELF being "
                          "unable to run. Without this flag (the default, used by `run.sh "
                          "health` and CI) a finding still returns nonzero, because a human "
                          "reading that surface wants to see it turn amber.")
    args = ap.parse_args(argv)

    try:
        credentials = load_inventory(args.inventory)
    except (OSError, json.JSONDecodeError) as exc:
        # Rule 6: an unreadable inventory is a check that could not run, and
        # must be reported as exactly that — never as a clean sweep because
        # the loop below simply had nothing to iterate.
        print(f"⚠︎ credential-health — inventory UNREADABLE ({type(exc).__name__}); "
              f"nothing was probed. Fix: {args.inventory}")
        return 1
    if not credentials:
        print("⚠︎ credential-health — inventory has zero credentials; that is a "
              "configuration defect, not a clean sweep")
        return 1

    global MINT_STATE
    MINT_STATE = _load_mint_state(args.mint_state)
    rows = run_all(credentials, default_timeout_s=args.timeout)

    try:
        _save_mint_state(MINT_STATE, path=args.mint_state)
    except OSError as exc:
        # Not fatal on its own: the worst case of losing this write is that
        # the Claude token's age gets recomputed from today on the NEXT run
        # (see _record_mint_date_if_new), which can only push an expiry
        # warning LATER, never mask a real one. That asymmetry is why this
        # is a loud warning and not a `return 1` — unlike write_jsonl just
        # below, losing this state can never turn a real finding silent.
        print(f"⚠︎ credential-health — could not persist mint-state to "
              f"{args.mint_state} ({type(exc).__name__}: {exc}); token age "
              f"will be recomputed from today on the next run")

    try:
        write_jsonl(rows, path=args.out)
    except (OSError, AssertionError) as exc:
        # Fatal, unlike the mint-state write above: this jsonl IS the lane's
        # entire audit trail for tonight (rule 6, "never fail silently") —
        # losing it silently would be indistinguishable from nothing having
        # run at all, so it is reported and the run stops here rather than
        # going on to file loops off results nobody can ever read back.
        print(f"⚠︎ credential-health — could not write {args.out} "
              f"({type(exc).__name__}: {exc}); this run's results were never recorded")
        return 1

    dedup_state = _load_dedup(args.dedup_store)
    filed = []
    by_name = {c["name"]: c for c in credentials}
    # A finding files a loop the moment it's real news: failed/expiring_soon
    # always qualify, and so does an `unknown` for a credential whose
    # declared file IS present on this machine — that is not "not yet
    # configured", it is "configured and still couldn't be verified", which
    # is exactly the silent-forever gap `carr_routine_exec`'s environment
    # stripping used to leave for the file-based probes above before they
    # moved to reading their own dotenv files directly off disk. A credential
    # with no path configured at all (or whose file genuinely never landed
    # on this machine) stays a quiet `unknown` — that one really is "not yet
    # set up here", not a finding to page Joe over every night.
    file_backed_unknown_names = set()
    for row in rows:
        cred = by_name[row["name"]]
        is_file_backed_unknown = (row["status"] == "unknown"
                                   and _credential_file_exists(cred))
        if is_file_backed_unknown:
            file_backed_unknown_names.add(row["name"])
        if row["status"] in ("failed", "expiring_soon") or is_file_backed_unknown:
            outcome = file_loop_if_needed(cred, row["status"], dedup_state,
                                           dry_run=args.no_file_loops)
            if outcome in ("filed", "dry_run"):
                filed.append((row["name"], row["status"], outcome))
    try:
        _save_dedup(dedup_state, path=args.dedup_store)
    except OSError as exc:
        # Also not fatal: the worst case is a loop getting refiled once on
        # the next run rather than deduplicated — noisy, never silent.
        print(f"⚠︎ credential-health — could not persist dedup-state to "
              f"{args.dedup_store} ({type(exc).__name__}: {exc}); a finding "
              f"already filed above may be refiled once on the next run")

    counts = {b: 0 for b in BUCKETS}
    for row in rows:
        counts[row["status"]] += 1
    needs_attention = counts["failed"] + counts["expiring_soon"] + len(file_backed_unknown_names)
    unconfigured_unknown = counts["unknown"] - len(file_backed_unknown_names)

    if needs_attention:
        print(f"⚠︎ credential-health — {needs_attention} of {len(rows)} credential(s) need "
              f"attention (failed={counts['failed']} expiring_soon={counts['expiring_soon']} "
              f"unverifiable={len(file_backed_unknown_names)} unconfigured={unconfigured_unknown} "
              f"ok={counts['ok']})")
    else:
        print(f"OK credential-health — {len(rows)} credential(s) checked, all clear "
              f"(unknown={counts['unknown']} ok={counts['ok']})")
    for row in rows:
        extra = f" (expires {row['expires_at']})" if "expires_at" in row else ""
        print(f"  {row['status']:<14} {row['name']}{extra}")
    for name, bucket, outcome in filed:
        print(f"  loop {outcome:<10} {name} ({bucket})")

    if args.nightly:
        # The lane ran to completion and did its job (jsonl written, any
        # finding already loop-filed and deduplicated) — that IS success for
        # an unattended chain step. Turning the whole night red for the same
        # thing the loop already says would be the exact "an alarm that fires
        # every day trains people to stop reading alarms" failure this file's
        # own module docstring and bin/nightly.sh's GATES section both name.
        return 0
    return 1 if needs_attention else 0


if __name__ == "__main__":
    sys.exit(main())
