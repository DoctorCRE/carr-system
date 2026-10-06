#!/usr/bin/env python3
"""Hermetic crash/race tests for isolated-staging database credentials."""

from __future__ import annotations

import importlib.util
import os
import pathlib
import stat
import sys
import tempfile


REPO = pathlib.Path(__file__).resolve().parents[1]
MODULE = REPO / "tools" / "staging_database_credential.py"


def load_module():
    spec = importlib.util.spec_from_file_location("staging_database_credential", MODULE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {MODULE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    credential = load_module()
    checked = 0

    def check(label: str, condition: bool) -> None:
        nonlocal checked
        checked += 1
        if not condition:
            raise AssertionError(label)
        print(f"  ok  {label}")

    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        final = root / "staging-writer.env"
        paths = credential.CredentialPaths(final=final, pending=pathlib.Path(str(final) + ".pending"))
        owner = (
            "postgresql://neondb_owner:owner-fixture@staging.example:5432/neondb"  # ci-secret-scan: allow — hermetic fixture
            "?sslmode=require"
        )  # ci-secret-scan: allow — RFC 2606 fixture host

        prepared = credential.prepare_pending(
            paths,
            key="CARR_DB_STAGING_WRITER_URL",
            role_name="app_writer",
            owner_uri=owner,
            expected_endpoint="staging.example",
            expected_port=5432,
            expected_database="neondb",
            password_factory=lambda: "w" * 64,
        )
        check("new credential is written only to the same-directory pending path",
              prepared.path == paths.pending and paths.pending.exists() and not paths.final.exists())
        check("pending credential is mode 0600",
              stat.S_IMODE(paths.pending.stat().st_mode) == 0o600)
        first_bytes = paths.pending.read_bytes()
        check("credential file contains exactly the one profile key",
              first_bytes.startswith(b"CARR_DB_STAGING_WRITER_URL=postgresql://app_writer:")
              and first_bytes.count(b"\n") == 1)

        reused = credential.prepare_pending(
            paths,
            key="CARR_DB_STAGING_WRITER_URL",
            role_name="app_writer",
            owner_uri=owner,
            expected_endpoint="staging.example",
            expected_port=5432,
            expected_database="neondb",
            password_factory=lambda: (_ for _ in ()).throw(AssertionError("regenerated")),
        )
        check("rerun reuses pending bytes and never regenerates the secret",
              reused.value == prepared.value and paths.pending.read_bytes() == first_bytes)

        credential.promote_pending(
            paths, key="CARR_DB_STAGING_WRITER_URL", expected_value=prepared.value
        )
        check("verified pending credential promotes atomically without changing bytes",
              paths.final.read_bytes() == first_bytes and not paths.pending.exists()
              and stat.S_IMODE(paths.final.stat().st_mode) == 0o600)
        final_reused = credential.load_existing(
            paths,
            key="CARR_DB_STAGING_WRITER_URL",
            role_name="app_writer",
            expected_endpoint="staging.example",
            expected_port=5432,
            expected_database="neondb",
        )
        check("final credential is independently reusable", final_reused.state == "final")

        paths.pending.write_bytes(first_bytes)
        os.chmod(paths.pending, 0o600)
        try:
            credential.load_existing(
                paths,
                key="CARR_DB_STAGING_WRITER_URL",
                role_name="app_writer",
                expected_endpoint="staging.example",
                expected_port=5432,
                expected_database="neondb",
            )
        except credential.CredentialRefusal:
            check("simultaneous pending and final credentials refuse", True)
        else:
            raise AssertionError("ambiguous credential state was accepted")

        paths.pending.unlink()
        os.chmod(paths.final, 0o644)
        try:
            credential.load_existing(
                paths,
                key="CARR_DB_STAGING_WRITER_URL",
                role_name="app_writer",
                expected_endpoint="staging.example",
                expected_port=5432,
                expected_database="neondb",
            )
        except credential.CredentialRefusal:
            check("credential mode wider than 0600 refuses", True)
        else:
            raise AssertionError("insecure credential mode was accepted")

    bad_values = (
        "http://app_writer:secret@staging.example/neondb?sslmode=require",
        "postgresql://wrong:secret@staging.example/neondb?sslmode=require",  # ci-secret-scan: allow — hermetic fixture
        "postgresql://app_writer:secret@other.example/neondb?sslmode=require",  # ci-secret-scan: allow — hermetic fixture
        "postgresql://app_writer:secret@staging.example:6432/neondb?sslmode=require",  # ci-secret-scan: allow — hermetic fixture
        "postgresql://app_writer:secret@staging.example/other?sslmode=require",  # ci-secret-scan: allow — hermetic fixture
        "postgresql://app_writer:secret@staging.example/neondb?sslmode=disable",  # ci-secret-scan: allow — hermetic fixture
        "postgresql://app_writer:secret@staging.example/neondb?sslmode=require&options=-csearch_path%3Dpublic",  # ci-secret-scan: allow — hermetic fixture
    )
    for value in bad_values:
        try:
            credential.validate_uri(
                value, role_name="app_writer", expected_endpoint="staging.example",
                expected_port=5432, expected_database="neondb",
            )
        except credential.CredentialRefusal:
            pass
        else:
            raise AssertionError(f"unsafe URI accepted: {value}")
    check("wrong scheme/user/host/port/database/query and shell-capable options refuse", True)

    owner = (
        "postgresql://neondb_owner:owner-fixture@staging.example:5432/neondb"  # ci-secret-scan: allow — hermetic fixture
        "?sslmode=require"
    )  # ci-secret-scan: allow — RFC 2606 fixture host
    for crash_boundary in ("after_open", "after_write", "after_fsync", "after_publish"):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            final = root / "staging-writer.env"
            paths = credential.CredentialPaths(
                final=final, pending=pathlib.Path(str(final) + ".pending")
            )

            def crash(boundary: str) -> None:
                if boundary == crash_boundary:
                    raise SystemExit("simulated hard stop")

            try:
                credential.prepare_pending(
                    paths, key="CARR_DB_STAGING_WRITER_URL", role_name="app_writer",
                    owner_uri=owner, expected_endpoint="staging.example",
                    expected_port=5432, expected_database="neondb",
                    password_factory=lambda: "c" * 64, boundary=crash,
                )
            except SystemExit:
                pass
            else:
                raise AssertionError(f"{crash_boundary} injection did not stop")
            resumed = credential.prepare_pending(
                paths, key="CARR_DB_STAGING_WRITER_URL", role_name="app_writer",
                owner_uri=owner, expected_endpoint="staging.example",
                expected_port=5432, expected_database="neondb",
                password_factory=lambda: "r" * 64,
            )
            expected_password = "r" * 64 if crash_boundary == "after_open" else "c" * 64
            check(f"{crash_boundary} crash resumes without malformed canonical pending state",
                  resumed.state == "pending" and resumed.password == expected_password
                  and paths.pending.exists()
                  and not pathlib.Path(str(paths.pending) + ".preparing").exists())

    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        target = root / "credential.env"
        bad_paths = credential.CredentialPaths(target, pathlib.Path(str(target) + ".pending"))
        os.mkfifo(target)
        try:
            credential.load_existing(
                bad_paths, key="CARR_DB_STAGING_WRITER_URL", role_name="app_writer",
                expected_endpoint="staging.example", expected_port=5432,
                expected_database="neondb",
            )
        except credential.CredentialRefusal:
            check("FIFO credential path refuses without opening", True)
        else:
            raise AssertionError("FIFO credential path was accepted")
        target.unlink()
        target.mkdir()
        try:
            credential.load_existing(
                bad_paths, key="CARR_DB_STAGING_WRITER_URL", role_name="app_writer",
                expected_endpoint="staging.example", expected_port=5432,
                expected_database="neondb",
            )
        except credential.CredentialRefusal:
            check("directory credential path refuses", True)
        else:
            raise AssertionError("directory credential path was accepted")
        target.rmdir()
        target.write_bytes(b"X" * 5000)
        os.chmod(target, 0o600)
        try:
            credential.load_existing(
                bad_paths, key="CARR_DB_STAGING_WRITER_URL", role_name="app_writer",
                expected_endpoint="staging.example", expected_port=5432,
                expected_database="neondb",
            )
        except credential.CredentialRefusal:
            check("oversize credential file refuses", True)
        else:
            raise AssertionError("oversize credential file was accepted")

    check("writer and reader profiles use separate files and keys",
          credential.profile("writer").key != credential.profile("reader").key
          and credential.profile("writer").paths.final != credential.profile("reader").paths.final)
    # THE SEAT ROLE NAME IS CLUSTER-WIDE AND SAYS NOTHING ABOUT ENVIRONMENT, so
    # the key and the filename are what keep a staging DSN out of a production
    # Worker. Both must name staging, and this module must offer NO production
    # counterpart to reach for: the production credential is Joe's own act.
    staging_seat = credential.profile("gate_zero_producer")
    check("the Gate Zero seat credential names staging in both its key and its file",
          staging_seat.role_name == "carr_gate_zero_producer"
          and "STAGING" in staging_seat.key
          and staging_seat.paths.final.name.startswith("staging-")
          and staging_seat.key != credential.profile("writer").key
          and staging_seat.paths.final != credential.profile("writer").paths.final)
    foundation_seat = credential.profile("foundation_assurance_oracle")
    check("the Foundation assurance seat credential is a fourth isolated staging profile",
          foundation_seat.role_name == "carr_foundation_assurance_oracle"
          and foundation_seat.bundle_role == "carr_foundation_assurance_oracle"
          and "STAGING" in foundation_seat.key
          and foundation_seat.paths.final.name.startswith("staging-")
          and foundation_seat.key not in {
              credential.profile("writer").key,
              credential.profile("reader").key,
              staging_seat.key,
          }
          and len({
              profile.paths.final for profile in (
                  credential.profile("writer"), credential.profile("reader"),
                  staging_seat, foundation_seat)
          }) == 4)
    for absent in ("production_gate_zero_producer", "production", "gate_zero"):
        try:
            credential.profile(absent)
        except credential.CredentialRefusal:
            pass
        else:
            raise AssertionError(f"a credential profile exists for {absent!r}")
    check("this module offers no production credential profile to reach for", True)
    print(f"PASS: staging database credential self-test ({checked} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
