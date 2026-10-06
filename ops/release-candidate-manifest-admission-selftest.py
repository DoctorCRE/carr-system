#!/usr/bin/env python3
"""Hermetic contract for exact release-candidate manifest admission."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO = Path(__file__).resolve().parents[1]
RECORD = REPO / "tools" / "ops-record.py"
MANIFEST = REPO / "tools" / "release-manifest.py"
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def run_manifest(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(MANIFEST), *args], cwd=REPO,
        capture_output=True, text=True, timeout=300,
    )


def build(environment: str, path: Path) -> dict[str, Any]:
    built = run_manifest(
        "build", "--sha", "HEAD", "--environment", environment,
        "--performance-budget-ref", "runbook:staging-performance-v1",
        "--performance-budget-ms", "1000",
        "--recovery-strategy", "rollback",
        "--rollback-plan-ref", "runbook:staging-rollback-v1",
    )
    if built.returncode != 0:
        raise RuntimeError((built.stderr or built.stdout).strip())
    path.write_text(built.stdout, encoding="utf-8")
    return json.loads(built.stdout)


def args_for(environment: str, manifest: Path, **changes: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "action": "candidate", "key": f"candidate-{environment}",
        "service": "carr-mcp", "environment": environment,
        "manifest": str(manifest), "sha": None,
        "provider": None, "provider_version_id": None,
        "rollback_ready": False, "rollback_plan": None,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def main() -> int:
    print("release-candidate-manifest-admission-selftest: exact target and assurance")
    spec = importlib.util.spec_from_file_location("ops_record_candidate_test", RECORD)
    assert spec and spec.loader
    module: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    manifest_spec = importlib.util.spec_from_file_location(
        "release_manifest_candidate_test", MANIFEST
    )
    assert manifest_spec and manifest_spec.loader
    manifest_module: Any = importlib.util.module_from_spec(manifest_spec)
    manifest_spec.loader.exec_module(manifest_module)

    validator = getattr(module, "release_candidate_manifest_refusal", None)
    check("1. candidate intake exposes one shared fail-closed validator",
          callable(validator))

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        staging_path = tmp / "staging.json"
        production_path = tmp / "production.json"
        staging = build("staging", staging_path)
        production = build("production", production_path)

        if callable(validator):
            check("2. exact staging target with complete assurance verifies",
                  validator(args_for("staging", staging_path), staging) is None)

            # Production has an additional immutable provider binding. Bind it
            # through the canonical tool before asking admission to verify it.
            bound = run_manifest(
                "bind-provider", "--manifest", str(production_path),
                "--provider", "cloudflare-workers",
                "--provider-version-id", "11111111-2222-4333-8444-555555555555",
            )
            check("3. production source manifest binds for the admission fixture",
                  bound.returncode == 0, (bound.stderr or bound.stdout)[:160])
            if bound.returncode == 0:
                production_path.write_text(bound.stdout, encoding="utf-8")
                production = json.loads(bound.stdout)
                production_args = args_for(
                    "production", production_path,
                    provider="cloudflare-workers",
                    provider_version_id="11111111-2222-4333-8444-555555555555",
                )
                check("3a. exact production target/provider/assurance verifies",
                      validator(production_args, production) is None)

            # Every CLI environment must bind to the manifest environment before
            # verification or any connection. Local/rehearsal are non-serving,
            # but they must not become an unchecked bypass.
            for environment in ("local", "rehearsal", "staging", "production"):
                wrong_path, wrong_manifest = ((staging_path, staging)
                                               if environment == "production"
                                               else (production_path, production))
                refusal = validator(args_for(environment, wrong_path), wrong_manifest)
                check(f"4. {environment} refuses a different manifest target",
                      isinstance(refusal, str) and "service/environment" in refusal)

            for environment in ("local", "rehearsal"):
                nonserving = dict(staging)
                nonserving["environment"] = environment
                nonserving_path = tmp / f"{environment}.json"
                nonserving_path.write_text(json.dumps(nonserving), encoding="utf-8")
                refusal = validator(
                    args_for(environment, nonserving_path), nonserving
                )
                check(f"4a. exact {environment} target still runs manifest verification",
                      isinstance(refusal, str)
                      and "manifest verification failed" in refusal)

            missing_assurance = dict(staging)
            for field in (
                "performance_budget_ref", "performance_budget_ms",
                "recovery_strategy", "rollback_plan_ref",
            ):
                missing_assurance[field] = None
            missing_assurance["rollback_ready"] = False
            missing_path = tmp / "missing-assurance.json"
            missing_path.write_text(json.dumps(missing_assurance), encoding="utf-8")
            refusal = validator(args_for("staging", missing_path), missing_assurance)
            check("5. staging candidate assurance is mandatory and approval-bound",
                  isinstance(refusal, str) and "assurance" in refusal.lower())

            refusal = validator(
                args_for("staging", staging_path,
                         rollback_plan="runbook:caller-supplied-different-plan"),
                staging,
            )
            check("5a. caller rollback fields cannot replace the hashed manifest plan",
                  isinstance(refusal, str) and "differs" in refusal)

        # Reproduce the real defect: a production-default manifest passed to a
        # staging candidate must refuse before the database connection opens.
        connections: list[str] = []

        def forbidden_connect(kind: str):
            connections.append(kind)
            raise AssertionError("malformed candidate reached database connection")

        module.connect = forbidden_connect
        malformed_args = args_for("staging", production_path)
        # cmd_release's candidate write fields are intentionally absent: an exact
        # target refusal must occur before any of them or a credential are read.
        malformed_rc = module.cmd_release(malformed_args)
        check("6. production-default manifest for staging refuses before DB write",
              malformed_rc == 2 and not connections,
              f"rc={malformed_rc} connections={connections}")

        # The canonical manifest builder resolves refs, but candidate admission
        # must persist only one exact immutable commit identity.  Recompute each
        # plan hash so these refusals prove the source identity check, and invoke
        # the real command path to prove no database credential is opened.
        immutable_sha = staging["git_sha"]
        for label, mutable_ref in (
            ("HEAD", "HEAD"),
            ("tag", "refs/tags/candidate-test"),
            ("abbreviated SHA", immutable_sha[:12]),
        ):
            malformed_ref = dict(staging)
            malformed_ref["git_sha"] = mutable_ref
            malformed_ref["plan_hash"] = manifest_module.plan_hash(malformed_ref)
            malformed_ref_path = tmp / f"mutable-{label.replace(' ', '-')}.json"
            malformed_ref_path.write_text(
                json.dumps(malformed_ref), encoding="utf-8"
            )
            connections.clear()
            malformed_ref_rc = module.cmd_release(
                args_for("staging", malformed_ref_path)
            )
            check(f"6a. {label} git ref refuses before DB write",
                  malformed_ref_rc == 2 and not connections,
                  f"rc={malformed_ref_rc} connections={connections}")

    source = (REPO / "bin" / "deploy-worker.sh").read_text(encoding="utf-8")
    check("7. deploys use one manifest builder for exact target/assurance recomputation",
          "build_release_manifest()" in source
          and source.count("build_release_manifest ") >= 3)
    check("8. standalone staging refuses without the full assurance preimage",
          "standalone staging release requires performance/recovery assurance" in source)

    record_source = RECORD.read_text(encoding="utf-8")
    check("9. named successor is locked and same-target before old-row terminalization",
          "with eligible_successor as" in record_source
          and "for share" in record_source
          and "successor.service_id = target.service_id" in record_source
          and "successor.environment = target.environment" in record_source)
    candidate_call = record_source[record_source.index("cur.execute(CANDIDATE_INSERT,"):]
    candidate_call = candidate_call[:candidate_call.index("row = cur.fetchone()")]
    check("10. persisted rollback evidence comes from the verified manifest",
          'manifest.get("rollback_ready")' in candidate_call
          and 'manifest.get("rollback_plan_ref")' in candidate_call
          and "else args.rollback_ready" not in candidate_call
          and "else args.rollback_plan" not in candidate_call)

    # ── the eighth review round: the row carries no maker this tool typed ───
    #
    # The sixth round derived the maker over the authority connection, CLOSED it,
    # and inserted the row over the generic ledger writer: the derivation was
    # honest and the row's provenance was not, because any role holding INSERT on
    # ops.release could write the same two text columns. The seventh round answered
    # with a SECURITY DEFINER door and could not grant EXECUTE on it under the
    # moratorium, so `release candidate` stopped filing anything at all. The eighth
    # moves the fact into the database instead of into the permissions: migration
    # 0504 records the filing login from session_user, derives the maker from it,
    # and makes the column the Gate Zero seam store keys on GENERATED, so no role
    # can write it. What is checked here is the wrapper's half — that it asserts
    # neither half of the maker and reads the provenance back out of the row.
    check("11. the recorder asserts no maker and reads the recorded one back",
          "ops.record_release_candidate(" not in record_source
          and "CANDIDATE_INSERT" in record_source
          and "maker_actor, maker_session_user" in record_source
          and "args.maker" not in record_source.split("def main(")[0]
          and "returning id, release_key, maker_actor" in record_source)

    opened: list[str] = []

    class FakeCursor:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def __enter__(self) -> "FakeCursor":
            return self

        def __exit__(self, *_exc: Any) -> None:
            return None

        def execute(self, statement: str, params: Any = None) -> None:
            self.statements.append(statement)

        def fetchone(self) -> tuple[Any, ...]:
            # service_id() resolves first on this connection, then the insert
            # returns the row's own recorded provenance.
            if len(self.statements) == 1:
                return ("22222222-2222-4222-8222-222222222222",)
            return ("11111111-1111-4111-8111-111111111111",
                    "candidate-production", "app_writer", "app_writer", False)

    class FakeConnection:
        def __init__(self, kind: str) -> None:
            self.kind = kind
            self.cursor_object = FakeCursor()

        def __enter__(self) -> "FakeConnection":
            return self

        def __exit__(self, *_exc: Any) -> None:
            return None

        def cursor(self) -> FakeCursor:
            return self.cursor_object

    connection_objects: list[FakeConnection] = []

    def recording_connect(kind: str) -> FakeConnection:
        opened.append(kind)
        conn = FakeConnection(kind)
        connection_objects.append(conn)
        return conn

    provider_version = "11111111-2222-4333-8444-555555555555"
    with tempfile.TemporaryDirectory() as raw_authority:
        authority_tmp = Path(raw_authority)
        exact_path = authority_tmp / "production.json"
        build("production", exact_path)
        bound_exact = run_manifest(
            "bind-provider", "--manifest", str(exact_path),
            "--provider", "cloudflare-workers",
            "--provider-version-id", provider_version,
        )
        if bound_exact.returncode != 0:
            raise RuntimeError((bound_exact.stderr or bound_exact.stdout).strip())
        exact_path.write_text(bound_exact.stdout, encoding="utf-8")
        module.connect = recording_connect
        candidate_rc = module.cmd_release(args_for(
            "production", exact_path,
            key="candidate-production",
            provider="cloudflare-workers",
            provider_version_id=provider_version,
            correlation=None, verifier=None, verifier_evidence=None,
            test_evidence="evidence:tests", security_evidence="evidence:security",
            work_request=None, expires_at=None, actor=None, plan_hash=None,
            idempotency_key=None,
        ))

    # The service files its own candidate. PostgreSQL derives the maker from
    # session_user; an autonomous upload must not acquire Joe attribution.
    check("12. an exact candidate is filed on the scoped service connection",
          candidate_rc == 0 and opened == ["routine"],
          f"rc={candidate_rc} connections={opened}")
    filed = [statement for conn in connection_objects
             for statement in conn.cursor_object.statements]
    candidate_statements = [statement for statement in filed
                            if "ops.release" in statement]
    check("13. the one release statement names no maker column of its own",
          len(candidate_statements) == 1
          and "insert into ops.release" in candidate_statements[0]
          and "maker_actor," not in candidate_statements[0].split("returning")[0]
          and "maker_verification_ref" not in candidate_statements[0].split("returning")[0]
          and "maker_session_user" not in candidate_statements[0].split("returning")[0],
          f"statements={candidate_statements}")

    if FAILURES:
        print(f"release-candidate-manifest-admission-selftest: {len(FAILURES)} FAILED")
        return 1
    print("release-candidate-manifest-admission-selftest: contract holds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
