#!/usr/bin/env python3
"""Offline recovery of lost staging rollback credentials and inventory."""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from typing import Any
import unittest
from unittest import mock
from urllib.parse import urlsplit, urlunsplit

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("staging_recovery_provision", REPO / "tools/provision-staging-app-writer.py")
assert SPEC is not None and SPEC.loader is not None
provision: Any = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = provision
SPEC.loader.exec_module(provision)
OPERATION = "021cf377-d2fc-4b26-9146-d1ddc1556df1"


class ProcessDeath(BaseException):
    pass


def binding():
    return SimpleNamespace(
        target=SimpleNamespace(candidate_operation_id=OPERATION),
        old=provision.replacement.ProviderScope("old-project", "carr-staging", "old-branch", "ep-old", "ep-old.example.invalid"),
        candidate=SimpleNamespace(project_id="candidate-project"),
    )


class Recovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="staging-recovery-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.canonical = self.root / "canonical"
        self.operation = self.root / "operation"
        self.operation.mkdir(mode=0o700)
        self.binding = binding()
        self.profile = next(p for p in provision.PROFILES if p.label == "authority_joe")
        self.owner_uri = urlunsplit((
            "postgresql", "neondb_owner:synthetic-owner@ep-old.example.invalid:5432",
            "/neondb", "sslmode=require", ""))
        self.passwords = {p.login_role: "o" * 48 for p in provision.PROFILES}
        self.live = {p.secret_name: "unrecoverable-fixture-value" for p in provision.PROFILES}
        self.changes = []
        self.bulk_calls = []
        self.fail_commit = False
        real_profile = provision.credential.profile
        self.file_profile = lambda label, **kw: real_profile(label, config_root=kw.get("config_root") or self.canonical)
        self.patch("replacement_credential_root", lambda _operation: self.operation)
        self.patch("read_worker_database_secret_names", lambda **_kwargs: set(self.live))
        self.patch("bulk_worker_database_secrets", self.bulk)
        self.patch("apply_login_profile", self.apply)
        self.patch("validate_profile_login", self.validate)
        self.patch("profile", self.file_profile, provision.credential)
        self.patch("derive_dsn", lambda *_a, **_kw: SimpleNamespace(value=self.owner_uri), provision.replacement)
        self.patch("connect", lambda _dsn: self.Owner(self), provision.psycopg)
        for profile in provision.PROFILES:
            if profile.label == "authority_joe":
                continue
            file_profile = self.file_profile(profile.label)
            stored = provision.credential.prepare_pending(
                file_profile.paths, key=file_profile.key, role_name=profile.login_role,
                owner_uri=self.owner_uri, expected_endpoint=self.binding.old.endpoint_host,
                expected_port=5432, expected_database="neondb", password_factory=lambda: "o" * 48)
            provision.credential.promote_pending(file_profile.paths, key=file_profile.key, expected_value=stored.value)
            self.live[profile.secret_name] = stored.value

    def patch(self, name, value, module=provision):
        patch = mock.patch.object(module, name, value)
        patch.start()
        self.addCleanup(patch.stop)

    class Owner:
        def __init__(self, fixture):
            self.fixture = fixture
            self.pending = {}
            self.row = None

        def cursor(self):
            return self

        def execute(self, statement, params=None):
            if "session_user,current_user" in str(statement):
                self.row = ("neondb_owner", "neondb_owner", False, True)
            elif "exists" in str(statement):
                self.row = (params[0] in self.fixture.passwords,)
            elif "pg_authid" in str(statement):
                self.row = (False,) if params[0] in self.fixture.passwords else None

        def fetchone(self):
            return self.row

        def fetchall(self):
            return [self.row] if self.row else []

        def commit(self):
            if self.pending and self.fixture.fail_commit:
                self.fixture.fail_commit = False
                raise ProcessDeath()
            self.fixture.passwords.update(self.pending)
            self.pending.clear()

        def rollback(self):
            self.pending.clear()

        def close(self):
            self.rollback()

    def apply(self, owner, profile, _grants, password, *, rotate=False, commit=True, **_kwargs):
        self.assertFalse(commit)
        if not rotate and profile.login_role in self.passwords:
            self.assertEqual(password, self.passwords[profile.login_role])
            return False
        if rotate:
            self.assertIn(profile.login_role, self.passwords)
        owner.pending[profile.login_role] = password
        self.changes.append((profile.login_role, password))
        return not rotate

    def validate(self, dsn, profile, _grants, **_kwargs):
        if urlsplit(dsn).password != self.passwords[profile.login_role]:
            raise provision.ProvisioningRefusal("synthetic login did not authenticate")

    def bulk(self, values):
        self.assertTrue(all(value is not None for value in values.values()))
        self.bulk_calls.append(dict(values))
        self.live.update(values)

    def prepare(self):
        rotations = []
        with provision.credential.exclusive_lock(self.root / ".worker.lock"):
            values = provision.prepare_worker_rollback(
                self.binding, capture=True,
                plans={p.label: () for p in provision.PROFILES}, rotations=rotations)
        return values, rotations

    def test_blocked_live_authority_rotates_rebinds_and_retains_canonical_final(self):
        values, rotations = self.prepare()
        authority = values["CARR_DB_AUTHORITY_JOE_URL"]
        self.assertEqual(self.live["CARR_DB_AUTHORITY_JOE_URL"], authority)
        self.assertEqual(urlsplit(authority).password, self.passwords["carr_authority_joe"])
        self.assertTrue(self.file_profile("authority_joe").paths.final.is_file())
        self.assertFalse(self.file_profile("authority_joe").paths.pending.exists())
        self.assertEqual(rotations[0]["outcome"], "rotated")
        self.assertEqual(rotations[0]["profile"], "authority_joe")
        receipt = (self.operation / "prior-worker-credential-recovery.json").read_text()
        self.assertNotIn(authority, receipt)
        self.assertNotIn(urlsplit(authority).password, receipt)
        self.assertEqual(self.prepare()[0], values)
        self.assertEqual(len(self.changes), 1)

    def test_pending_value_resumes_after_worker_publication_before_commit(self):
        self.fail_commit = True
        with self.assertRaises(ProcessDeath):
            self.prepare()
        published = self.live["CARR_DB_AUTHORITY_JOE_URL"]
        self.assertTrue(self.file_profile("authority_joe").paths.pending.is_file())
        values, _rotations = self.prepare()
        self.assertEqual(values["CARR_DB_AUTHORITY_JOE_URL"], published)
        self.assertEqual({password for _role, password in self.changes}, {urlsplit(published).password})

    def test_pending_value_resumes_after_commit_before_final_publication(self):
        original = provision.credential.promote_pending
        def die(paths, **kwargs):
            if paths == self.file_profile("authority_joe").paths:
                raise ProcessDeath()
            original(paths, **kwargs)
        with mock.patch.object(provision.credential, "promote_pending", side_effect=die):
            with self.assertRaises(ProcessDeath):
                self.prepare()
        published = self.live["CARR_DB_AUTHORITY_JOE_URL"]
        values, _rotations = self.prepare()
        self.assertEqual(values["CARR_DB_AUTHORITY_JOE_URL"], published)

    def test_final_value_resumes_after_promotion_before_receipt(self):
        original = provision.save_private_json
        def die(path, value):
            if path.name == "prior-worker-credential-recovery.json" and any(
                    p["outcome"] == "rotated" for p in value["profiles"].values()):
                raise ProcessDeath()
            original(path, value)
        with mock.patch.object(provision, "save_private_json", side_effect=die):
            with self.assertRaises(ProcessDeath):
                self.prepare()
        published = self.live["CARR_DB_AUTHORITY_JOE_URL"]
        values, rotations = self.prepare()
        self.assertEqual(values["CARR_DB_AUTHORITY_JOE_URL"], published)
        self.assertEqual(rotations[0]["outcome"], "rotated")
        self.assertEqual(len(self.changes), 1)

    def test_current_live_binding_overrides_retained_absence_for_deletion_safety(self):
        provision.save_private_json(self.operation / "prior-worker-secret-names.json", {
            **provision.prior_worker_identity(self.binding),
            "secret_names": sorted(set(self.live) - {"CARR_DB_AUTHORITY_JOE_URL"})})
        values, rotations = self.prepare()
        self.assertIsNotNone(values["CARR_DB_AUTHORITY_JOE_URL"])
        self.assertEqual(rotations[0]["outcome"], "rotated")

    def test_a_binding_observed_later_stays_protected_after_its_name_and_file_disappear(self):
        file_profile = self.file_profile("authority_joe")
        stored = provision.credential.prepare_pending(
            file_profile.paths, key=file_profile.key, role_name="carr_authority_joe",
            owner_uri=self.owner_uri, expected_endpoint=self.binding.old.endpoint_host,
            expected_port=5432, expected_database="neondb", password_factory=lambda: "o" * 48)
        provision.credential.promote_pending(file_profile.paths, key=file_profile.key, expected_value=stored.value)
        provision.save_private_json(self.operation / "prior-worker-secret-names.json", {
            **provision.prior_worker_identity(self.binding),
            "secret_names": sorted(set(self.live) - {"CARR_DB_AUTHORITY_JOE_URL"})})
        self.prepare()
        file_profile.paths.final.unlink()
        del self.live["CARR_DB_AUTHORITY_JOE_URL"]
        values, rotations = self.prepare()
        self.assertIsNotNone(values["CARR_DB_AUTHORITY_JOE_URL"])
        self.assertEqual(rotations[0]["outcome"], "rotated")

    def test_all_live_profiles_can_recover_without_deleting_bindings(self):
        for profile in provision.PROFILES:
            self.file_profile(profile.label).paths.final.unlink(missing_ok=True)
        values, rotations = self.prepare()
        self.assertEqual(len(rotations), 5)
        self.assertTrue(all(values.values()))
        self.assertEqual(self.live, values)
        self.assertEqual(self.prepare()[0], values)
        self.assertEqual(len(self.changes), 5)

    def test_a_later_loss_of_the_final_file_can_recover_the_live_binding_again(self):
        first, _rotations = self.prepare()
        self.file_profile("authority_joe").paths.final.unlink()
        second, rotations = self.prepare()
        self.assertNotEqual(first["CARR_DB_AUTHORITY_JOE_URL"], second["CARR_DB_AUTHORITY_JOE_URL"])
        self.assertEqual(rotations[0]["outcome"], "rotated")
        self.assertEqual(self.live["CARR_DB_AUTHORITY_JOE_URL"], second["CARR_DB_AUTHORITY_JOE_URL"])
        self.assertEqual(len(self.changes), 2)

    def test_production_scope_refuses_before_rotation_or_publication(self):
        self.binding.old = dataclasses.replace(self.binding.old, project_id=provision.PRODUCTION_PROJECT_ID)
        with self.assertRaises(provision.ProvisioningRefusal):
            self.prepare()
        self.assertEqual(self.changes, [])
        self.assertEqual(self.bulk_calls, [])

    def test_unregistered_role_refuses_before_rotation_or_publication(self):
        profiles = tuple(dataclasses.replace(p, login_role="neondb_owner") if p.label == "authority_joe" else p
                         for p in provision.PROFILES)
        with mock.patch.object(provision, "PROFILES", profiles):
            with self.assertRaises(provision.ProvisioningRefusal):
                self.prepare()
        self.assertEqual(self.changes, [])
        self.assertEqual(self.bulk_calls, [])

    def test_live_authority_binding_with_missing_role_creates_registered_login(self):
        del self.passwords["carr_authority_joe"]
        values, outcomes = self.prepare()
        authority = values["CARR_DB_AUTHORITY_JOE_URL"]
        self.assertEqual(self.live["CARR_DB_AUTHORITY_JOE_URL"], authority)
        self.assertEqual(urlsplit(authority).password, self.passwords["carr_authority_joe"])
        self.assertEqual(outcomes[0]["outcome"], "created")
        self.assertTrue(self.file_profile("authority_joe").paths.final.is_file())
        self.assertEqual(self.prepare()[0], values)
        self.assertEqual(len(self.changes), 1)

    def test_missing_migration_owned_seat_refuses_without_creating_or_publishing(self):
        self.prepare()
        self.changes.clear()
        self.bulk_calls.clear()
        profile = next(p for p in provision.PROFILES if p.created_by_migration)
        self.file_profile(profile.label).paths.final.unlink()
        del self.passwords[profile.login_role]
        with self.assertRaisesRegex(provision.ProvisioningRefusal, "migration has not applied"):
            self.prepare()
        self.assertEqual(self.changes, [])
        self.assertEqual(self.bulk_calls, [])

    def test_creation_resumes_same_password_after_publication_before_commit(self):
        del self.passwords["carr_authority_joe"]
        self.fail_commit = True
        with self.assertRaises(ProcessDeath):
            self.prepare()
        published = self.live["CARR_DB_AUTHORITY_JOE_URL"]
        self.assertNotIn("carr_authority_joe", self.passwords)
        values, outcomes = self.prepare()
        self.assertEqual(values["CARR_DB_AUTHORITY_JOE_URL"], published)
        self.assertEqual(outcomes[0]["outcome"], "created")
        self.assertEqual({password for _role, password in self.changes}, {urlsplit(published).password})

    def test_creation_resumes_after_commit_before_final_publication(self):
        del self.passwords["carr_authority_joe"]
        with mock.patch.object(provision.credential, "promote_pending", side_effect=ProcessDeath):
            with self.assertRaises(ProcessDeath):
                self.prepare()
        published = self.live["CARR_DB_AUTHORITY_JOE_URL"]
        values, outcomes = self.prepare()
        self.assertEqual(values["CARR_DB_AUTHORITY_JOE_URL"], published)
        self.assertEqual(outcomes[0]["outcome"], "created")
        self.assertEqual(len(self.changes), 1)

    def test_creation_resumes_after_final_publication_before_receipt(self):
        del self.passwords["carr_authority_joe"]
        original = provision.save_private_json
        def die(path, value):
            if path.name == "prior-worker-credential-recovery.json" and any(
                    p["outcome"] == "created" for p in value["profiles"].values()):
                raise ProcessDeath()
            original(path, value)
        with mock.patch.object(provision, "save_private_json", side_effect=die):
            with self.assertRaises(ProcessDeath):
                self.prepare()
        published = self.live["CARR_DB_AUTHORITY_JOE_URL"]
        values, outcomes = self.prepare()
        self.assertEqual(values["CARR_DB_AUTHORITY_JOE_URL"], published)
        self.assertEqual(outcomes[0]["outcome"], "created")
        self.assertEqual(len(self.changes), 1)


class InventoryCrash(unittest.TestCase):
    def test_inventory_publication_survives_process_death(self):
        with tempfile.TemporaryDirectory(prefix="inventory-crash-") as raw:
            root = Path(raw)
            result = subprocess.run([sys.executable, __file__, "--inventory-crash", raw],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 73, result.stderr)
            proof = root / "prior-worker-secret-names.json"
            original = proof.read_bytes()
            with mock.patch.object(provision, "replacement_credential_root", return_value=root), \
                 mock.patch.object(provision, "load_rollback_worker_values", return_value={"fixture": "value"}):
                with provision.credential.exclusive_lock(root / ".worker.lock"):
                    provision.prepare_worker_rollback(binding(), capture=True)
            self.assertEqual(proof.read_bytes(), original)
            self.assertEqual(proof.stat().st_nlink, 1)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--inventory-crash":
        root = Path(sys.argv[2])
        provision.replacement_credential_root = lambda _operation: root
        provision.read_worker_database_secret_names = lambda: {"DATABASE_URL_READER", "DATABASE_URL_WRITER"}
        provision.load_rollback_worker_values = lambda *_args: {"fixture": "value"}
        for name in ("link", "replace"):
            original = getattr(os, name)
            def publish(source, target, original=original):
                original(source, target)
                if Path(target).name == "prior-worker-secret-names.json":
                    os._exit(73)
            setattr(os, name, publish)
        with provision.credential.exclusive_lock(root / ".worker.lock"):
            provision.prepare_worker_rollback(binding(), capture=True)
        raise AssertionError("crash boundary was not reached")
    unittest.main(verbosity=2)
