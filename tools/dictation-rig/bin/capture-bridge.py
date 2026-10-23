#!/usr/bin/env python3
"""
capture-bridge.py — CARR dictation rig, WO-4 capture-bridge rig-side client.

Spec: WO-4 capture bridge (Cloudflare Worker, built and already deployed by
a separate work order — see .claude/worktrees/wo4-capture-bridge/SUMMARY.md
for the wire contract this file was built against). This file is the LOCAL
client for claim, status, sanitized candidate filing, aggregate report filing,
and session polling. Transcript text, recipient email addresses, and draft
bodies never cross this bridge. It never touches vendor/quill, ~/.config/quill,
launchd, or git.

Subcommands:
    claim  <session_dir>          — claim a session with the worker
    status <session_dir> <state> [detail]   — report a state transition
    publish <session_dir>        — file sanitized post-call proposals
    poll   <session_dir>          — check whether a meeting_record landed
    check                         — print config/provisioning status

Config: ~/.config/carr-capture/config.json = {"base_url", "device_id",
"token"}. Verified no-work returns zero; unavailable configuration returns
78 and failed operations return one. This tool sits beside the recording
pipeline. Its failures leave recordings and retry markers intact.

Never logs the device token, the session token, or any Authorization header
value — only "present"/"missing" where that matters (the check subcommand).

Pure standard library, matching transcribe_session.py's convention: no
third-party imports, no venv needed.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from pathlib import Path
from typing import Any

import post_call

# --- constants -------------------------------------------------------------

CONFIG_PATH = Path.home() / ".config" / "carr-capture" / "config.json"
LOG_PATH = Path.home() / "Library" / "Logs" / "capture-bridge.log"

HTTP_TIMEOUT_SECONDS = 10
CALL_CONTEXT_WAIT_SECONDS = 8


class BridgeNetworkError(Exception):
    """Any transport-level failure: DNS, connection refused, timeout, ..."""


class Outcome(IntEnum):
    VERIFIED = 0
    UNAVAILABLE = 1
    UNCONFIGURED = 78


# --- logging -----------------------------------------------------------


def log_line(message: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts} {message}\n"
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def iso_now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def wait_for_call_mode(session_dir: Path, timeout: float = CALL_CONTEXT_WAIT_SECONDS) -> str | None:
    """Let Call Mode attach its session mode before an immutable remote claim."""
    deadline = time.monotonic() + timeout
    while True:
        context = read_json(session_dir / "call-context.json") or {}
        mode = context.get("mode")
        if mode in {"weekly_deal_call", "other_call"}:
            return str(mode)
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.1)


# --- config --------------------------------------------------------------


def load_config_raw() -> dict[str, Any] | None:
    """Whatever parses at CONFIG_PATH, or None. Never raises."""
    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_config() -> dict[str, str] | None:
    """Return usable configuration, or None for an unavailable executor."""
    data = load_config_raw()
    if data is None:
        return None
    base_url = str(data.get("base_url") or "").strip()
    device_id = str(data.get("device_id") or "").strip()
    token = str(data.get("token") or "").strip()
    if not base_url or not device_id or not token:
        return None
    return {"base_url": base_url.rstrip("/"), "device_id": device_id, "token": token}


# --- json marker helpers ------------------------------------------------


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_json_atomic(path: Path, data: dict[str, Any], mode: int | None) -> bool:
    """Write to a temp file in the same directory, chmod if requested, then
    os.replace into place — the closest thing to an atomic write in plain
    Python, so a half-written marker is never mistaken for a finished one.
    Same discipline as consent-watch.sh's marker write."""
    tmp = path.parent / f"{path.name}.tmp.{os.getpid()}"
    try:
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        log_line(f"ERROR could not write {path.name}: {exc}")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def verified(session_dir: Path, operation: str) -> int:
    health_path = session_dir / ".capture-health.json"
    health = read_json(health_path) or {}
    health[f"last_verified_{operation}_at"] = iso_now_utc()
    if not write_json_atomic(health_path, health, mode=0o600):
        return Outcome.UNAVAILABLE
    return Outcome.VERIFIED


# --- http ------------------------------------------------------------------


def http_request(
    method: str,
    url: str,
    body: dict[str, Any] | None,
    headers: dict[str, str] | None,
) -> tuple[int, dict[str, Any]]:
    """Returns (status_code, parsed_json_body). Raises BridgeNetworkError on
    any transport failure (DNS, connection refused, timeout) — an HTTP error
    status (4xx/5xx) is NOT raised, it's a normal return, since callers need
    the status code to distinguish e.g. 401 from 409 from 500."""
    all_headers = dict(headers or {})
    # Identify ourselves. urllib's default User-Agent ("Python-urllib/3.x") is
    # blocked at Cloudflare's EDGE with a 403 before the Worker ever runs —
    # proved live 2026-08-08 against the real endpoint: an unauthenticated
    # request returned 401 (the app answering) with a curl UA and 403 with the
    # urllib default, same URL, same empty body. That 403 is indistinguishable
    # from an auth failure in a log line, so it cost a real debugging detour;
    # a named UA also makes the rig's traffic identifiable in Cloudflare
    # analytics.
    all_headers.setdefault("User-Agent", "carr-quill-dictate/1.0 (capture-bridge)")
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        all_headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=data, headers=all_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            raw = resp.read()
            status = resp.getcode()
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        except Exception:
            raw = b""
        status = exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BridgeNetworkError(str(exc)) from exc

    parsed: dict[str, Any] = {}
    if raw:
        try:
            decoded = json.loads(raw.decode("utf-8"))
            if isinstance(decoded, dict):
                parsed = decoded
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = {}
    return status, parsed


# --- claim -----------------------------------------------------------------


def claim_cmd(cfg: dict[str, str], session_dir: Path) -> int:
    name = session_dir.name
    capture_marker = session_dir / ".capture.json"
    ingested_marker = session_dir / "ingested.json"

    if capture_marker.exists():
        log_line(f"SKIP claim already-claimed session={name}")
        return 0
    if ingested_marker.exists():
        log_line(f"SKIP claim already-ingested session={name}")
        return 0

    announcement = read_json(session_dir / "announcement.json")
    if announcement is None:
        log_line(f"SKIP claim no-announcement session={name} (consent proof missing)")
        return 0
    announced_at = str(announcement.get("announcement_fired_at") or "").strip()
    if not announced_at:
        log_line(f"SKIP claim announcement.json missing announcement_fired_at session={name}")
        return 0

    meta = read_json(session_dir / "meta.json")
    if meta is None:
        log_line(f"SKIP claim no-meta session={name}")
        return 0
    started_at = str(meta.get("started") or "").strip()
    if not started_at:
        log_line(f"SKIP claim meta.json missing started session={name}")
        return 0

    request_path = session_dir / ".capture-request.json"
    body = read_json(request_path)
    if request_path.exists() and (body is None or not body.get("nonce")):
        log_line(f"ERROR claim request marker unreadable session={name}")
        return Outcome.UNAVAILABLE
    if body is None:
        body = {
            "nonce": str(uuid.uuid4()),
            "device_id": cfg["device_id"],
            "mode": "meeting",
            "started_at": started_at,
            "consent": {"announced_at": announced_at},
        }
        # A Call Mode start has six seconds to attach its workflow before
        # this immutable claim. Persist the nonce before any ambiguous effect.
        if wait_for_call_mode(session_dir) == "weekly_deal_call":
            body["workflow"] = "post_call"
        if not write_json_atomic(request_path, body, mode=0o600):
            return Outcome.UNAVAILABLE
    headers = {"Authorization": f"Bearer {cfg['token']}"}
    url = f"{cfg['base_url']}/capture/claim"

    try:
        status, resp = http_request("POST", url, body, headers)
    except BridgeNetworkError as exc:
        log_line(f"ERROR claim network failure session={name}: {exc}")
        return Outcome.UNAVAILABLE

    if status != 200:
        log_line(f"ERROR claim rejected session={name} status={status}")
        return Outcome.UNAVAILABLE

    session_token = str(resp.get("session_token") or "").strip()
    ttl_seconds = resp.get("ttl_seconds")
    if not session_token:
        log_line(f"ERROR claim response missing session_token session={name}")
        return Outcome.UNAVAILABLE

    now = datetime.now(timezone.utc)
    claimed_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    expires_at = ""
    try:
        expires_at = (now + timedelta(seconds=float(ttl_seconds or 0))).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (TypeError, ValueError):
        log_line(f"WARN claim response has no usable ttl_seconds session={name}")

    marker = {
        "session_token": session_token,
        "claimed_at": claimed_at,
        "expires_at": expires_at,
    }
    if not write_json_atomic(capture_marker, marker, mode=0o600):
        log_line(f"ERROR claim: could not persist .capture.json session={name}")
        return Outcome.UNAVAILABLE

    log_line(f"CLAIMED session={name} ttl_seconds={ttl_seconds}")

    # Per spec: a successful claim immediately reports "recording". Routed
    # through status_cmd (re-reading the marker we just wrote) so there is
    # exactly one code path that posts a status update, ever.
    status_rc = status_cmd(cfg, session_dir, "recording", None)
    return status_rc or verified(session_dir, "claim")


# --- status ------------------------------------------------------------


def status_cmd(cfg: dict[str, str], session_dir: Path, state: str, detail: str | None) -> int:
    name = session_dir.name
    marker = read_json(session_dir / ".capture.json")
    if marker is None:
        log_line(f"NOOP status no-capture-marker session={name} state={state}")
        return 0
    session_token = str(marker.get("session_token") or "").strip()
    if not session_token:
        log_line(f"NOOP status capture-marker-missing-token session={name} state={state}")
        return 0

    body: dict[str, Any] = {
        "session_token": session_token,
        "state": state,
        "at": iso_now_utc(),
    }
    if detail:
        body["detail"] = detail
    url = f"{cfg['base_url']}/capture/status"

    try:
        status, _resp = http_request("POST", url, body, None)
    except BridgeNetworkError as exc:
        log_line(f"ERROR status network failure session={name} state={state}: {exc}")
        return Outcome.UNAVAILABLE

    if status == 200:
        log_line(f"STATUS posted session={name} state={state}")
    elif status == 409:
        log_line(
            f"STATUS rejected(409) session={name} state={state} "
            "(backward transition or terminal state already reached)"
        )
    else:
        log_line(f"ERROR status rejected session={name} state={state} status={status}")
        return Outcome.UNAVAILABLE
    return 0


# --- post-call publish ----------------------------------------------------


def capture_token(session_dir: Path) -> str | None:
    marker = read_json(session_dir / ".capture.json")
    if marker is None:
        return None
    token = str(marker.get("session_token") or "").strip()
    return token or None


def post_batch(
    cfg: dict[str, str], session_dir: Path, route: str,
    items: list[dict[str, Any]], bindings: list[dict[str, Any]], lane: str,
) -> bool:
    if not items:
        return True
    token = capture_token(session_dir)
    if token is None:
        return False
    canonical = json.dumps(items, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    key = f"post-call:{session_dir.name}:{lane}:{uuid.uuid5(uuid.NAMESPACE_URL, canonical)}"
    body = {"session_token": token, "idempotency_key": key, "items": items}
    try:
        status, response = http_request("POST", f"{cfg['base_url']}{route}", body, None)
    except BridgeNetworkError as exc:
        log_line(f"ERROR publish network failure session={session_dir.name} lane={lane}: {exc}")
        return False
    ids = response.get("candidate_ids")
    if status != 200 or not isinstance(ids, list):
        log_line(f"ERROR publish rejected session={session_dir.name} lane={lane} status={status}")
        return False
    try:
        post_call.apply_candidate_ids(session_dir, bindings, ids)
    except post_call.ContractError as exc:
        log_line(f"ERROR publish receipt mismatch session={session_dir.name} lane={lane}: {exc}")
        return False
    return True


def publish_cmd(cfg: dict[str, str], session_dir: Path) -> int:
    """Publish validated metadata only; safe to retry after partial failure."""
    if capture_token(session_dir) is None:
        log_line(f"NOOP publish no-capture-marker session={session_dir.name}")
        return 0
    try:
        shaped = post_call.sanitized_candidates(session_dir)
    except post_call.ContractError as exc:
        log_line(f"NOOP publish post-call report unavailable session={session_dir.name}: {exc}")
        return 0

    bindings = shaped.get("bindings") or []
    post_bindings = [item for item in bindings if item.get("remote") == "post_call"]
    legacy_bindings = [item for item in bindings if item.get("remote") == "legacy"]
    status_rc = status_cmd(cfg, session_dir, "distilling", None)
    posted = post_batch(
        cfg, session_dir, "/capture/post-call/candidates",
        shaped.get("post_call_items") or [], post_bindings, "post-call",
    )
    legacy = post_batch(
        cfg, session_dir, "/capture/candidates",
        shaped.get("legacy_items") or [], legacy_bindings, "legacy",
    )
    if not (posted and legacy) or status_rc != 0:
        return 1
    marker = {
        "published_at": iso_now_utc(),
        "post_call_count": len(shaped.get("post_call_items") or []),
        "legacy_count": len(shaped.get("legacy_items") or []),
    }
    if not write_json_atomic(session_dir / post_call.PUSH_FILE, marker, mode=0o600):
        return Outcome.UNAVAILABLE
    log_line(
        f"PUBLISHED session={session_dir.name} "
        f"post_call={marker['post_call_count']} legacy={marker['legacy_count']}"
    )
    return 0


def sync_candidate_statuses(session_dir: Path, response: dict[str, Any]) -> None:
    rows = response.get("candidate_statuses")
    if not isinstance(rows, list):
        return
    statuses: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        candidate_id = row.get("id")
        status = row.get("status")
        if isinstance(candidate_id, str) and status in {"confirmed", "skipped"}:
            statuses[candidate_id] = status
    if not statuses:
        return
    try:
        post_call.apply_candidate_statuses(session_dir, statuses)
    except post_call.ContractError as exc:
        log_line(f"WARN candidate status sync refused session={session_dir.name}: {exc}")


def file_post_call_report(
    cfg: dict[str, str], session_dir: Path, token: str, candidate_count: int,
) -> tuple[bool, str]:
    digest = post_call.report_sha256(session_dir)
    body = {
        "session_token": token,
        "idempotency_key": f"post-call-report:{session_dir.name}:{digest}",
        "report_sha256": digest,
        "candidate_count": candidate_count,
    }
    try:
        status, response = http_request(
            "POST", f"{cfg['base_url']}/capture/post-call/report", body, None,
        )
    except BridgeNetworkError as exc:
        log_line(f"ERROR report filing network failure session={session_dir.name}: {exc}")
        return False, ""
    if status != 200 or response.get("filed") is not True:
        log_line(f"ERROR report filing rejected session={session_dir.name} status={status}")
        return False, ""
    return True, digest


# --- poll ------------------------------------------------------------------


def poll_cmd(cfg: dict[str, str], session_dir: Path) -> int:
    name = session_dir.name
    capture_marker = session_dir / ".capture.json"
    marker = read_json(capture_marker)
    if marker is None:
        log_line(f"NOOP poll no-capture-marker session={name}")
        return 0
    session_token = str(marker.get("session_token") or "").strip()
    if not session_token:
        log_line(f"NOOP poll capture-marker-missing-token session={name}")
        return 0

    # Candidate filing must win this race. Filing a zero-count aggregate report
    # before local proposals have their remote IDs would lock the session and
    # silently discard every proposed task/update/draft.
    if (session_dir / post_call.REPORT_FILE).exists() and publish_cmd(cfg, session_dir) != 0:
        log_line(f"POLL session={name} candidate publish incomplete — holding recordings")
        return Outcome.UNAVAILABLE

    headers = {"Authorization": f"Bearer {session_token}"}
    url = f"{cfg['base_url']}/capture/session"

    try:
        status, resp = http_request("GET", url, None, headers)
    except BridgeNetworkError as exc:
        log_line(f"ERROR poll network failure session={name}: {exc}")
        return Outcome.UNAVAILABLE

    if status == 401:
        log_line(f"ERROR poll unauthorized(401) session={name} (session token invalid/expired?)")
        return Outcome.UNAVAILABLE
    if status != 200:
        log_line(f"ERROR poll failed session={name} status={status}")
        return Outcome.UNAVAILABLE

    if not isinstance(resp.get("state"), str) or "meeting_record" not in resp:
        log_line(f"ERROR poll malformed response session={name}")
        return Outcome.UNAVAILABLE

    meeting_record = resp.get("meeting_record")
    meeting_record_str = str(meeting_record).strip() if meeting_record is not None else ""
    state = resp.get("state")

    is_post_call = resp.get("post_call") is True
    if not is_post_call and not meeting_record_str:
        # HARD GUARD: this is the ordering purge-recordings.sh depends on.
        # Writing ingested.json here without a real meeting_record would
        # make client audio/transcripts eligible for deletion before the
        # distilled, attributed narrative that is supposed to replace them
        # actually exists. Write NOTHING in that case — ever.
        log_line(f"POLL session={name} state={state} meeting_record=null — no ingested.json written")
        return verified(session_dir, "poll")

    receipt: dict[str, Any] | None = None
    if is_post_call:
        if not (session_dir / post_call.REPORT_FILE).exists():
            log_line(f"POLL session={name} post_call local report unavailable — holding recordings")
            return verified(session_dir, "poll")
        sync_candidate_statuses(session_dir, resp)
        raw_candidates = resp.get("candidates")
        post_call_candidates: dict[str, Any] = raw_candidates if isinstance(raw_candidates, dict) else {}
        pending = post_call_candidates.get("pending")
        statuses = resp.get("candidate_statuses")
        candidate_count = len(statuses) if isinstance(statuses, list) else None
        raw_report = resp.get("post_call_report")
        report: dict[str, Any] = raw_report if isinstance(raw_report, dict) else {}
        remote_hash = str(report.get("sha256") or "").strip()
        if report.get("filed") is not True:
            if pending != 0 or candidate_count is None:
                log_line(f"POLL session={name} post_call pending={pending} — holding recordings")
                return verified(session_dir, "poll")
            filed, remote_hash = file_post_call_report(
                cfg, session_dir, session_token, candidate_count,
            )
            if not filed:
                return Outcome.UNAVAILABLE
        if not remote_hash:
            log_line(f"POLL session={name} post_call report missing hash — holding recordings")
            return Outcome.UNAVAILABLE
        receipt = post_call.retention_ready(session_dir, remote_hash)
        if receipt is None:
            log_line(f"POLL session={name} post_call filed remotely — holding recordings pending local dispositions")
            return verified(session_dir, "poll")
        if status_cmd(cfg, session_dir, "done", None) != 0:
            return Outcome.UNAVAILABLE

    # candidates.confirmed may be a count or a list depending on the
    # response shape actually returned; only extract refs if it's a list.
    records: list[str] = []
    candidates = resp.get("candidates")
    if isinstance(candidates, dict):
        confirmed = candidates.get("confirmed")
        if isinstance(confirmed, list):
            for item in confirmed:
                if isinstance(item, dict):
                    ref = item.get("ref") or item.get("id")
                    if ref:
                        records.append(str(ref))
                elif isinstance(item, str) and item.strip():
                    records.append(item.strip())

    ingested = {
        "ingested_at": iso_now_utc(),
        "by": "capture-bridge",
        "meeting_record": meeting_record,
        "records": records,
    }
    if is_post_call:
        assert receipt is not None
        ingested.update({"post_call": True, "aggregate_report_hash": receipt["aggregate_report_hash"], "backend_report_sha256": receipt["backend_report_sha256"], "pending_items": receipt["pending_items"]})
    if not write_json_atomic(session_dir / "ingested.json", ingested, mode=None):
        log_line(f"ERROR poll: could not write ingested.json session={name}")
        return Outcome.UNAVAILABLE

    try:
        capture_marker.unlink()
    except OSError as exc:
        log_line(
            f"WARN poll: ingested.json written but could not remove .capture.json "
            f"session={name}: {exc}"
        )
        return Outcome.UNAVAILABLE

    log_line(f"INGESTED session={name} meeting_record={meeting_record} records={len(records)}")
    return verified(session_dir, "poll")


# --- check -----------------------------------------------------------------


def check_cmd() -> int:
    exists = CONFIG_PATH.exists()
    print(f"config_path: {CONFIG_PATH}")
    print(f"config_file: {'exists' if exists else 'missing'}")

    raw = load_config_raw()
    if raw is None:
        print("base_url: missing")
        print("device_id: missing")
        print("token: missing")
        print("provisioned: no")
        return 0

    base_url = str(raw.get("base_url") or "").strip()
    device_id = str(raw.get("device_id") or "").strip()
    token = str(raw.get("token") or "").strip()
    print(f"base_url: {'set' if base_url else 'missing'}")
    print(f"device_id: {'set' if device_id else 'missing'}")
    print(f"token: {'present' if token else 'missing'}")
    print(f"provisioned: {'yes' if (base_url and device_id and token) else 'no'}")
    return 0


# --- main --------------------------------------------------------------


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        sys.stderr.write(
            "usage: capture-bridge.py <claim|status|publish|poll|check> [args...]\n"
        )
        return 2

    cmd = argv[1]

    if cmd == "check":
        return check_cmd()

    if cmd == "claim":
        if len(argv) < 3:
            sys.stderr.write("usage: capture-bridge.py claim <session_dir>\n")
            return 2
        cfg = load_config()
        if cfg is None:
            log_line("UNAVAILABLE claim: no usable config (missing base_url/device_id/token)")
            return Outcome.UNCONFIGURED
        return claim_cmd(cfg, Path(argv[2]).resolve())

    if cmd == "status":
        if len(argv) < 4:
            sys.stderr.write("usage: capture-bridge.py status <session_dir> <state> [detail]\n")
            return 2
        cfg = load_config()
        if cfg is None:
            log_line("UNAVAILABLE status: no usable config (missing base_url/device_id/token)")
            return Outcome.UNCONFIGURED
        detail = argv[4] if len(argv) > 4 else None
        return status_cmd(cfg, Path(argv[2]).resolve(), argv[3], detail)

    if cmd == "publish":
        if len(argv) < 3:
            sys.stderr.write("usage: capture-bridge.py publish <session_dir>\n")
            return 2
        cfg = load_config()
        if cfg is None:
            log_line("UNAVAILABLE publish: no usable config (missing base_url/device_id/token)")
            return Outcome.UNCONFIGURED
        return publish_cmd(cfg, Path(argv[2]).resolve())

    if cmd == "poll":
        if len(argv) < 3:
            sys.stderr.write("usage: capture-bridge.py poll <session_dir>\n")
            return 2
        cfg = load_config()
        if cfg is None:
            log_line("UNAVAILABLE poll: no usable config (missing base_url/device_id/token)")
            return Outcome.UNCONFIGURED
        return poll_cmd(cfg, Path(argv[2]).resolve())

    sys.stderr.write(f"usage: unknown subcommand {cmd!r}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
