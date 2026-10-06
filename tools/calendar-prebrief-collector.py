#!/usr/bin/env python3
"""Capture one DB-bound EventKit snapshot and sign its in-memory envelope.

The collector accepts an opaque capture contract only from stdin, and writes one
signed envelope only to stdout.  It deliberately has no file, argv, or logging
surface for raw attendee addresses.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.machine_prerequisites import openssl_executable  # noqa: E402

MAX_PIPE = 1_048_576
HEX64 = re.compile(r"^[0-9a-f]{64}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SPONSORS = {"joe", "dell"}


class Refusal(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _read_pipe() -> dict[str, Any]:
    mode = os.fstat(sys.stdin.fileno()).st_mode
    if sys.stdin.isatty() or stat.S_ISREG(mode):
        raise Refusal("capture contract must arrive through a process pipe")
    raw = sys.stdin.read(MAX_PIPE + 1)
    if len(raw) > MAX_PIPE:
        raise Refusal("capture contract exceeds its bound")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Refusal("capture contract is malformed") from exc
    if not isinstance(value, dict):
        raise Refusal("capture contract must be an object")
    return value


def _timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise Refusal(f"capture contract {label} must be RFC3339")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Refusal(f"capture contract {label} must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise Refusal(f"capture contract {label} must include a timezone")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def capture_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the precise challenge returned by the resolver DB identity."""
    required = {
        "challenge_id", "sponsor", "job_id", "attempt", "lease_token", "scheduled_for",
        "window_starts_at", "window_ends_at", "mode", "destination", "allowlist_revision_id",
        "allowlist_digest", "calendar_keys",
    }
    if set(value) != required:
        raise Refusal("capture contract has an unsupported shape")
    sponsor, job_id, lease, mode, destination, revision, digest, challenge = (
        value.get("sponsor"), value.get("job_id"), value.get("lease_token"), value.get("mode"),
        value.get("destination"), value.get("allowlist_revision_id"), value.get("allowlist_digest"), value.get("challenge_id"),
    )
    if (sponsor not in SPONSORS or not isinstance(job_id, str) or not UUID.fullmatch(job_id)
            or not isinstance(lease, str) or not UUID.fullmatch(lease)
            or type(value.get("attempt")) is not int or value["attempt"] < 1
            or mode not in {"live", "canary"} or not isinstance(destination, str)
            or not isinstance(revision, str) or not UUID.fullmatch(revision)
            or not isinstance(digest, str) or not HEX64.fullmatch(digest)
            or not isinstance(challenge, str) or not UUID.fullmatch(challenge)):
        raise Refusal("capture contract identity is invalid")
    expected_destination = "live" if mode == "live" else f"calendar-prebrief-canary-{sponsor}"
    if destination != expected_destination:
        raise Refusal("capture contract destination is not sponsor/mode bound")
    scheduled_for = _timestamp(value.get("scheduled_for"), "scheduled_for")
    starts_at, ends_at = _timestamp(value.get("window_starts_at"), "window start"), _timestamp(value.get("window_ends_at"), "window end")
    scheduled = datetime.fromisoformat(scheduled_for.replace("Z", "+00:00"))
    starts, ends = (datetime.fromisoformat(starts_at.replace("Z", "+00:00")),
                    datetime.fromisoformat(ends_at.replace("Z", "+00:00")))
    if starts != scheduled - timedelta(days=7) or ends != scheduled + timedelta(days=45):
        raise Refusal("capture contract window is not the exact DB scheduled window")
    keys = value.get("calendar_keys")
    if not isinstance(keys, list) or not keys or any(not isinstance(key, str) or not HEX64.fullmatch(key) for key in keys) or keys != sorted(set(keys)):
        raise Refusal("capture contract allowlist keys are invalid")
    return {
        "challenge_id": challenge, "sponsor": sponsor, "job_id": job_id, "attempt": value["attempt"],
        "lease_token": lease, "scheduled_for": scheduled_for, "window_starts_at": starts_at,
        "window_ends_at": ends_at, "mode": mode, "destination": destination,
        "allowlist_revision_id": revision, "allowlist_digest": digest, "calendar_keys": keys,
    }


def _secure_private_key(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise Refusal("collector private key is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise Refusal("collector private key must be a 0600 regular non-symlink")


def _openssl_with_key(path: Path, args: list[str], payload: bytes, *, key_flag: str = "-inkey") -> bytes:
    _secure_private_key(path)
    fd = os.open(path, os.O_RDONLY)
    payload_fd = -1
    try:
        input_data: bytes | None = payload
        portable_args = list(args)
        # OpenSSL's Linux Ed25519 provider requires a seekable one-shot input;
        # macOS accepts the existing stdin pipe.  memfd keeps the raw capture
        # RAM-only while giving Linux the seekable descriptor it requires.
        if "/dev/stdin" in portable_args and hasattr(os, "memfd_create"):
            payload_fd = os.memfd_create("carr-calendar-envelope")
            remaining = memoryview(payload)
            while remaining:
                written = os.write(payload_fd, remaining)
                if written < 1:
                    raise Refusal("collector signing input could not be buffered")
                remaining = remaining[written:]
            os.lseek(payload_fd, 0, os.SEEK_SET)
            portable_args = [f"/dev/fd/{payload_fd}" if item == "/dev/stdin" else item for item in portable_args]
            input_data = None
        result = subprocess.run(
            [openssl_executable(), *portable_args, key_flag, f"/dev/fd/{fd}"], input=input_data,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            pass_fds=tuple(item for item in (fd, payload_fd) if item >= 0), timeout=10, check=False,
        )
    finally:
        os.close(fd)
        if payload_fd >= 0:
            os.close(payload_fd)
    if result.returncode != 0:
        raise Refusal("collector signing key cannot perform the requested operation")
    return result.stdout


def key_fingerprint(path: Path) -> str:
    public = _openssl_with_key(path, ["pkey", "-pubout"], b"", key_flag="-in")
    if not public.startswith(b"-----BEGIN PUBLIC KEY-----"):
        raise Refusal("collector public key derivation failed")
    return hashlib.sha256(public).hexdigest()


def sign(path: Path, payload: bytes) -> str:
    signature = _openssl_with_key(path, ["pkeyutl", "-sign", "-rawin", "-in", "/dev/stdin"], payload)
    if not signature or len(signature) > 4096:
        raise Refusal("collector signature is invalid")
    return base64.b64encode(signature).decode("ascii")


def _eventkit() -> Any:
    source = Path(__file__).with_name("calendar-prebrief-eventkit.py")
    spec = importlib.util.spec_from_file_location("calendar_prebrief_eventkit", source)
    if not spec or not spec.loader:
        raise Refusal("EventKit projection helper is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _eventkit_capture(contract: Mapping[str, Any], allowlist_path: Path) -> dict[str, Any]:
    eventkit = _eventkit()
    allowlist = eventkit.load_allowlist(allowlist_path, contract["sponsor"])
    keys = sorted(eventkit.opaque_key("calendar", entry["identifier"]) for entry in allowlist)
    if keys != contract["calendar_keys"]:
        raise Refusal("local allowlist does not match DB-issued capture contract")
    try:
        from EventKit import EKEventStore
        from Foundation import NSDate
    except Exception as exc:
        raise Refusal("EventKit runtime is unavailable") from exc
    start = datetime.fromisoformat(contract["window_starts_at"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(contract["window_ends_at"].replace("Z", "+00:00"))
    snapshot = eventkit.capture_raw_snapshot(
        EKEventStore.alloc().init(), allowlist, starts_at=start, ends_at=end,
        predicate_date=lambda value: NSDate.dateWithTimeIntervalSince1970_(value.timestamp()),
    )
    if snapshot.get("window") != {"starts_at": contract["window_starts_at"], "ends_at": contract["window_ends_at"]}:
        raise Refusal("EventKit did not preserve the DB-issued capture window")
    observed = snapshot.get("observed_calendars")
    observed_keys: list[str] = []
    if isinstance(observed, list):
        for item in observed:
            key = item.get("calendar_key") if isinstance(item, dict) else None
            if not isinstance(key, str):
                raise Refusal("EventKit returned invalid calendar coverage")
            observed_keys.append(key)
    if not isinstance(observed, list) or sorted(observed_keys) != contract["calendar_keys"]:
        raise Refusal("EventKit did not preserve the DB-issued calendar coverage")
    return snapshot


def collect(contract: Mapping[str, Any], *, allowlist: Path, private_key: Path, version: str) -> dict[str, Any]:
    contract = capture_contract(contract)
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise Refusal("collector version is invalid")
    raw_payload = _eventkit_capture(contract, allowlist)
    digest = hashlib.sha256(canonical(raw_payload)).hexdigest()
    count = len(raw_payload["events"])
    envelope: dict[str, Any] = dict(contract)
    envelope.update({
        "raw_payload": raw_payload, "raw_payload_digest": digest, "raw_payload_count": count,
        "collector_version": version, "key_fingerprint": key_fingerprint(private_key),
    })
    envelope["signature"] = sign(private_key, canonical(envelope))
    return envelope


def main() -> int:
    try:
        if any(os.environ.get(name) for name in os.environ if name.startswith("CARR_DB_") or name.startswith("PG")):
            raise Refusal("collector must not receive a database credential")
        allowlist = Path(os.environ.get("CARR_CALENDAR_PREBRIEF_ALLOWLIST", ""))
        private_key = Path(os.environ.get("CARR_CALENDAR_PREBRIEF_COLLECTOR_PRIVATE_KEY", ""))
        version = os.environ.get("CARR_CALENDAR_PREBRIEF_COLLECTOR_VERSION", "eventkit-collector-v1")
        print(json.dumps(collect(_read_pipe(), allowlist=allowlist, private_key=private_key, version=version), sort_keys=True, separators=(",", ":")))
        return 0
    except Refusal:
        print("calendar prebrief collector: REFUSE", file=sys.stderr)
        return 78
    except (OSError, subprocess.SubprocessError):
        print("calendar prebrief collector: FAIL", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
