#!/usr/bin/env python3
"""Verify one signed renewal snapshot from stdin, import it, and seal its run.

This is deliberately a provider-neutral ingress boundary.  It has no provider
URL, browser session, Drive path, file fallback, or signing private key.  A
credentialed provider bridge streams one bounded JSON envelope to stdin; this
adapter verifies it with a fixed Ed25519 public key named by a private profile
before it opens the jobs database.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.machine_prerequisites import openssl_executable  # noqa: E402


class SourceContractError(ValueError):
    pass


MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
ROW_KEYS = frozenset({
    "source_key", "name", "org_name", "vertical", "address", "city", "county", "state",
    "email", "phone", "segment", "source_row", "est_lease_event", "est_basis",
})
DATA_KEYS = ("schema_version", "provider", "key_fingerprint", "snapshot_id", "observed_at", "rows")
SNAPSHOT_KEYS = frozenset((*DATA_KEYS, "payload_sha256", "signature"))
PROFILE_KEYS = frozenset({"CARR_RENEWAL_SOURCE_PROVIDER", "CARR_RENEWAL_SOURCE_KEY_FINGERPRINT",
                          "CARR_RENEWAL_SOURCE_PUBLIC_KEY", "CARR_DB_RENEWAL_SOURCE_ATTESTOR_URL"})
HEX = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class SourceProfile:
    provider: str
    key_fingerprint: str
    public_key: Path
    attestor_dsn: str


@dataclass(frozen=True)
class RenewalSnapshot:
    provider: str
    key_fingerprint: str
    snapshot_id: uuid.UUID
    observed_at: datetime
    rows: tuple[dict[str, Any], ...]
    payload_sha256: str
    signature_sha256: str

    @property
    def row_count(self) -> int:
        return len(self.rows)


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_payload(value: Mapping[str, Any]) -> bytes:
    """The data digest preimage; signature metadata cannot change its content."""
    return _canonical({key: value[key] for key in DATA_KEYS})


def signing_payload(value: Mapping[str, Any]) -> bytes:
    """The Ed25519 preimage includes the declared digest but never the signature."""
    return _canonical({key: value[key] for key in (*DATA_KEYS, "payload_sha256")})


def _text(value: Any, field: str, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SourceContractError(f"{field} must be a nonblank string")
    return value.strip()


def _secure_public_key(path: Path) -> bytes:
    try:
        info, key = path.lstat(), path.read_bytes()
    except OSError as exc:
        raise SourceContractError("renewal source public key is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or not key or len(key) > 32768:
        raise SourceContractError("renewal source public key is unsafe")
    return key


def _verify_ed25519(payload: bytes, signature_text: object, public_key: Path) -> str:
    if not isinstance(signature_text, str):
        raise SourceContractError("snapshot signature is malformed")
    try:
        signature = base64.b64decode(signature_text, validate=True)
    except ValueError as exc:
        raise SourceContractError("snapshot signature is malformed") from exc
    if not signature or len(signature) > 4096:
        raise SourceContractError("snapshot signature is malformed")
    _secure_public_key(public_key)
    read_fd, write_fd = os.pipe()
    payload_fd = signature_fd = -1
    try:
        input_path = "/dev/stdin"
        input_data: bytes | None = payload
        signature_path = f"/dev/fd/{read_fd}"
        if hasattr(os, "memfd_create"):
            payload_fd, signature_fd = os.memfd_create("carr-renewal-source"), os.memfd_create("carr-renewal-signature")
            os.write(payload_fd, payload)
            os.write(signature_fd, signature)
            os.lseek(payload_fd, 0, os.SEEK_SET)
            os.lseek(signature_fd, 0, os.SEEK_SET)
            input_path, input_data, signature_path = f"/dev/fd/{payload_fd}", None, f"/dev/fd/{signature_fd}"
        else:
            os.write(write_fd, signature)
        os.close(write_fd)
        result = subprocess.run(
            [openssl_executable(), "pkeyutl", "-verify", "-pubin", "-inkey", str(public_key), "-rawin",
             "-in", input_path, "-sigfile", signature_path], input=input_data,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            pass_fds=tuple(fd for fd in (read_fd, payload_fd, signature_fd) if fd >= 0), timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SourceContractError("renewal source signature verifier is unavailable") from exc
    finally:
        for fd in (read_fd, write_fd, payload_fd, signature_fd):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
    if result.returncode != 0:
        raise SourceContractError("snapshot signature is invalid")
    return hashlib.sha256(signature).hexdigest()


def _read_profile(path: Path) -> SourceProfile:
    try:
        info, lines = path.lstat(), path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SourceContractError("renewal source profile is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise SourceContractError("renewal source profile must be a 0600 regular non-symlink")
    values: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or name not in PROFILE_KEYS or name in values:
            raise SourceContractError("renewal source profile has unknown or duplicate content")
        values[name] = value.strip().strip("'\"")
    if set(values) != PROFILE_KEYS or not values["CARR_RENEWAL_SOURCE_PROVIDER"].strip() \
            or not HEX.fullmatch(values["CARR_RENEWAL_SOURCE_KEY_FINGERPRINT"]):
        raise SourceContractError("renewal source profile is incomplete")
    parsed = urlsplit(values["CARR_DB_RENEWAL_SOURCE_ATTESTOR_URL"])
    if parsed.scheme not in {"postgresql", "postgres"} or parsed.username != "carr_renewal_source_attestor" or not parsed.hostname:
        raise SourceContractError("renewal source profile does not hold the exact attestor database identity")
    public_key = Path(values["CARR_RENEWAL_SOURCE_PUBLIC_KEY"])
    if hashlib.sha256(_secure_public_key(public_key)).hexdigest() != values["CARR_RENEWAL_SOURCE_KEY_FINGERPRINT"]:
        raise SourceContractError("renewal source profile key fingerprint does not match its public key")
    return SourceProfile(values["CARR_RENEWAL_SOURCE_PROVIDER"], values["CARR_RENEWAL_SOURCE_KEY_FINGERPRINT"],
                         public_key, values["CARR_DB_RENEWAL_SOURCE_ATTESTOR_URL"])


def validate_snapshot(value: object, profile: SourceProfile) -> RenewalSnapshot:
    if not isinstance(value, dict) or set(value) != SNAPSHOT_KEYS:
        raise SourceContractError("snapshot has an unregistered shape")
    if value.get("schema_version") != 1:
        raise SourceContractError("snapshot schema_version must equal 1")
    provider = _text(value.get("provider"), "provider", required=True)
    fingerprint = _text(value.get("key_fingerprint"), "key_fingerprint", required=True)
    if provider != profile.provider or fingerprint != profile.key_fingerprint:
        raise SourceContractError("snapshot provider or signing key is not the profile-bound authority")
    try:
        snapshot_id = uuid.UUID(str(value.get("snapshot_id")))
    except (ValueError, TypeError, AttributeError) as exc:
        raise SourceContractError("snapshot_id must be a UUID") from exc
    observed_raw = _text(value.get("observed_at"), "observed_at", required=True)
    assert observed_raw is not None
    try:
        observed_at = datetime.fromisoformat(observed_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceContractError("observed_at must be ISO-8601") from exc
    if observed_at.tzinfo is None:
        raise SourceContractError("observed_at must carry a timezone")
    rows = value.get("rows")
    if not isinstance(rows, list):
        raise SourceContractError("rows must be an array")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or set(row) != ROW_KEYS:
            raise SourceContractError(f"rows[{index}] has an unregistered shape")
        source_key = _text(row.get("source_key"), f"rows[{index}].source_key", required=True)
        name = _text(row.get("name"), f"rows[{index}].name", required=True)
        assert source_key is not None and name is not None
        if source_key in seen:
            raise SourceContractError("source_key is duplicated in one source snapshot")
        seen.add(source_key)
        if not isinstance(row.get("source_row"), dict):
            raise SourceContractError(f"rows[{index}].source_row must be an object")
        lease_event = row.get("est_lease_event")
        if lease_event is not None:
            try:
                lease_event_text = _text(lease_event, f"rows[{index}].est_lease_event", required=True)
                assert lease_event_text is not None
                datetime.strptime(lease_event_text, "%Y-%m-%d")
            except ValueError as exc:
                raise SourceContractError("est_lease_event must be YYYY-MM-DD or null") from exc
        clean = dict(row)
        clean["source_key"], clean["name"] = source_key, name
        for field in ROW_KEYS - {"source_key", "name", "source_row", "est_lease_event"}:
            clean[field] = _text(row[field], f"rows[{index}].{field}")
        normalized.append(clean)
    digest = hashlib.sha256(canonical_payload(value)).hexdigest()
    if value.get("payload_sha256") != digest:
        raise SourceContractError("payload_sha256 does not match canonical snapshot payload")
    signature_sha256 = _verify_ed25519(signing_payload(value), value.get("signature"), profile.public_key)
    return RenewalSnapshot(provider, fingerprint, snapshot_id, observed_at.astimezone(timezone.utc), tuple(normalized),
                           digest, signature_sha256)


def import_and_seal(conn: Any, snapshot: RenewalSnapshot, *, job_id: str, lease: str) -> dict[str, object]:
    """Invoke the sole jobs capability; it owns import, membership, and seal."""
    try:
        with conn.cursor() as cur:
            cur.execute("""select source_run_id,row_count from ops.ingest_renewal_signed_snapshot
                           (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                        (job_id, lease, str(snapshot.snapshot_id), snapshot.provider, snapshot.key_fingerprint,
                         snapshot.observed_at, snapshot.payload_sha256, snapshot.signature_sha256,
                         json.dumps(snapshot.rows, sort_keys=True)))
            sealed = cur.fetchone()
            if sealed is None or sealed[1] != snapshot.row_count:
                raise SourceContractError("lease-bound renewal source seal returned no receipt")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"contract": "renewal-source-ingress.v1", "schema_version": 1,
            "provider": snapshot.provider, "key_fingerprint": snapshot.key_fingerprint,
            "snapshot_id": str(snapshot.snapshot_id), "payload_sha256": snapshot.payload_sha256,
            "source_observed_at": snapshot.observed_at.isoformat().replace("+00:00", "Z"),
            "row_count": snapshot.row_count, "source_run_id": str(sealed[0])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disabled-contract", action="store_true")
    parser.add_argument("--job-id")
    parser.add_argument("--lease")
    args = parser.parse_args()
    if args.disabled_contract:
        print("renewal-source: refused: provider and dedicated claim runner are not installed", file=sys.stderr)
        return 78
    if not args.job_id or not args.lease:
        parser.error("--job-id and --lease are required outside the disabled contract")
    profile_path = os.environ.get("CARR_RENEWAL_SOURCE_PROFILE")
    if not profile_path:
        raise SystemExit("renewal-source: credentialed verification profile is required")
    raw = sys.stdin.buffer.read(MAX_SNAPSHOT_BYTES + 1)
    if not raw or len(raw) > MAX_SNAPSHOT_BYTES:
        raise SystemExit("renewal-source: stdin must contain one bounded signed snapshot")
    try:
        profile = _read_profile(Path(profile_path))
        snapshot = validate_snapshot(json.loads(raw), profile)
        import psycopg
        with psycopg.connect(profile.attestor_dsn) as conn:
            result = import_and_seal(conn, snapshot, job_id=args.job_id, lease=args.lease)
    except (json.JSONDecodeError, SourceContractError) as exc:
        print(f"renewal-source: refused: {exc}", file=sys.stderr)
        return 2
    print("renewal-source: result " + json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
