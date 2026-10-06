#!/usr/bin/env python3
"""Hermetic contract tests for the signed renewal-source ingress adapter."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.machine_prerequisites import openssl_executable  # noqa: E402

OPENSSL = openssl_executable()


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "renewal_source_adapter", ROOT / "tools" / "renewal-source-adapter.py"
)
assert SPEC and SPEC.loader
adapter = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = adapter
SPEC.loader.exec_module(adapter)
CONTROL_SPEC = importlib.util.spec_from_file_location("control_plane", ROOT / "tools" / "control-plane.py")
assert CONTROL_SPEC and CONTROL_SPEC.loader
control_plane = importlib.util.module_from_spec(CONTROL_SPEC)
sys.modules[CONTROL_SPEC.name] = control_plane
CONTROL_SPEC.loader.exec_module(control_plane)

def signed(rows: list[dict[str, object]], *, key: str, fingerprint: str) -> dict[str, object]:
    unsigned = {
        "schema_version": 1,
        "provider": "fixture-provider",
        "key_fingerprint": fingerprint,
        "snapshot_id": "00000000-0000-4000-8000-000000000001",
        "observed_at": "2026-08-21T12:00:00Z",
        "rows": rows,
    }
    unsigned["payload_sha256"] = hashlib.sha256(adapter.canonical_payload(unsigned)).hexdigest()
    signed_payload = adapter.signing_payload(unsigned)
    # Ed25519 is a one-shot operation.  OpenSSL on Linux therefore requires a
    # seekable input rather than /dev/stdin, even though macOS accepts the pipe.
    # Keep the payload anonymous while exercising the same portable FD shape as
    # the runtime verifier.
    with tempfile.TemporaryFile() as payload_file:
        payload_file.write(signed_payload)
        payload_file.flush()
        payload_file.seek(0)
        payload_fd = payload_file.fileno()
        proc = subprocess.run(
            [OPENSSL, "pkeyutl", "-sign", "-inkey", key, "-rawin", "-in", f"/dev/fd/{payload_fd}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(payload_fd,),
            check=True,
        )
    unsigned["signature"] = base64.b64encode(proc.stdout).decode("ascii")
    return unsigned


ROW: dict[str, object] = {
    "source_key": "fixture-practice|100-main-st",
    "name": "Fixture Practice",
    "org_name": "Fixture Practice",
    "vertical": "Physician practice",
    "address": "100 Main St",
    "city": "Pensacola",
    "county": "Escambia",
    "state": "FL",
    "email": "fixture@example.test",
    "phone": "555-0100",
    "segment": "LEASE EVENT — decision window",
    "source_row": {"tier": "T1 (window <12mo)", "flag": ""},
    "est_lease_event": "2027-04-01",
    "est_basis": "fixture estimate",
}


def refuses(value: object, profile: object) -> bool:
    try:
        adapter.validate_snapshot(value, profile)
    except adapter.SourceContractError:
        return True
    return False


def main() -> int:
    # EXIT 78 IS "NOT CONFIGURED HERE", NOT A FAILURE — the same narrow guard
    # ops/calendar-prebrief-collector-selftest.py installed (66144bf0). Apple
    # ships LibreSSL, which has no Ed25519, and a bare CalledProcessError read
    # as a red gate no local fix could clear. Minting a throwaway key IS the
    # question: on any machine with OpenSSL 3 every assertion still runs.
    with tempfile.TemporaryDirectory() as probe_dir:
        probe = subprocess.run(
            [OPENSSL, "genpkey", "-algorithm", "ED25519",
             "-out", str(Path(probe_dir) / "probe.pem")],
            capture_output=True, text=True)
        if probe.returncode != 0:
            print("NOT CONFIGURED: openssl here cannot mint an Ed25519 key "
                  "(LibreSSL); this proof needs OpenSSL 3")
            return 78
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp)
        private, public = tmp / "private.pem", tmp / "public.pem"
        subprocess.run([OPENSSL, "genpkey", "-algorithm", "Ed25519", "-out", str(private)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([OPENSSL, "pkey", "-in", str(private), "-pubout", "-out", str(public)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        fingerprint = hashlib.sha256(public.read_bytes()).hexdigest()
        profile = adapter.SourceProfile("fixture-provider", fingerprint, public, "postgresql://carr_renewal_source_attestor:fixture@db.example/carr")  # ci-secret-scan: allow -- inert fixture
        good = signed([ROW], key=str(private), fingerprint=fingerprint)
        checked = adapter.validate_snapshot(good, profile)
        assert checked.provider == "fixture-provider"
        assert checked.row_count == 1
        assert checked.observed_at == datetime(2026, 8, 21, 12, tzinfo=timezone.utc)
        tampered = dict(good)
        tampered["provider"] = "forged-provider"
        assert refuses(tampered, profile)
        assert refuses({**good, "signature": "AAAA"}, profile)
        assert refuses({key: value for key, value in good.items() if key != "signature"}, profile)
        assert refuses(signed([ROW, dict(ROW)], key=str(private), fingerprint=fingerprint), profile)
        assert refuses(signed([{**ROW, "source_key": "", "name": ""}], key=str(private), fingerprint=fingerprint), profile)
        assert refuses(signed([{**ROW, "est_lease_event": "not-a-date"}], key=str(private), fingerprint=fingerprint), profile)
        profile_path = tmp / "profile.env"
        profile_path.write_text(
            "CARR_RENEWAL_SOURCE_PROVIDER=fixture-provider\n"
            f"CARR_RENEWAL_SOURCE_KEY_FINGERPRINT={fingerprint}\n"
            f"CARR_RENEWAL_SOURCE_PUBLIC_KEY={public}\n"
            "CARR_DB_RENEWAL_SOURCE_ATTESTOR_URL=postgresql://carr_renewal_source_attestor:fixture@db.example/carr\n",  # ci-secret-scan: allow -- inert fixture
            encoding="utf-8",
        )
        profile_path.chmod(0o600)
        assert adapter._read_profile(profile_path).attestor_dsn.startswith("postgresql://carr_renewal_source_attestor:")
        profile_path.write_text(profile_path.read_text(encoding="utf-8").replace("carr_renewal_source_attestor", "carr_jobs"), encoding="utf-8")
        try:
            adapter._read_profile(profile_path)
        except adapter.SourceContractError:
            pass
        else:
            raise AssertionError("jobs identity was accepted for a renewal source profile")

    # The adapter is stdin-only by construction.  No normal path may retain a
    # hidden Drive/vault source while this credential has not been provisioned.
    source = (ROOT / "tools" / "renewal-source-adapter.py").read_text(encoding="utf-8")
    assert "--snapshot" not in source and "CARR_VAULT" not in source and "HMAC" not in source
    assert "CARR_RENEWAL_SOURCE_PROFILE" in source and "sys.stdin.buffer.read" in source
    assert "ops.ingest_renewal_signed_snapshot" in source
    migration = (ROOT / "migrations" / "0249_renewal_signed_source_ingress.sql").read_text(encoding="utf-8")
    assert "source_snapshot_id" in migration and "v_snapshot.row_count" in migration
    assert "sealed_member_count<>(select count(*) from candidate_pool" not in migration
    assert "octet_length(p_rows::text)>8388608" in migration
    assert "carr_renewal_source_attestors nologin" in migration
    assert "session_user<>'carr_renewal_source_attestor'" in migration
    assert "to carr_jobs;" not in migration

    disabled = subprocess.run(
        [str(ROOT / "tools" / "renewal-source-adapter.py"), "--disabled-contract"],
        capture_output=True, text=True, env={"PATH": os.environ.get("PATH", "")},
    )
    assert disabled.returncode == 78
    assert "provider and dedicated claim runner are not installed" in disabled.stderr
    assert not disabled.stdout

    receipt = {
        "contract": "renewal-source-ingress.v1", "schema_version": 1,
        "provider": "fixture-provider", "key_fingerprint": "a" * 64,
        "snapshot_id": "00000000-0000-4000-8000-000000000001", "payload_sha256": "b" * 64,
        "source_observed_at": "2026-08-21T12:00:00Z", "row_count": 1,
        "source_run_id": "00000000-0000-4000-8000-000000000002",
    }
    evidence = {"stdout_tail": "renewal-source: result " + json.dumps(receipt, sort_keys=True, separators=(",", ":"))}
    assert control_plane._renewal_source_aggregate(evidence) == receipt
    try:
        control_plane._renewal_source_aggregate({"stdout_tail": "renewal-source: result {}"})
    except RuntimeError:
        pass
    else:
        raise AssertionError("untyped renewal source receipt was accepted")

    print("renewal source adapter selftest: signed/versioned input and lease-bound seal contract pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
