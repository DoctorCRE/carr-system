#!/usr/bin/env python3
"""Hermetic contract checks for Program 5's Cloudflare version promotion path.

This test never calls Cloudflare or Postgres.  It proves the wrapper keeps the
two provider operations separate and exercises ops-record's fail-closed input
boundary before either command can open a database connection.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
DEPLOY = REPO / "bin" / "deploy-worker.sh"
RECORD = REPO / "tools" / "ops-record.py"
VERIFY_RELEASE = REPO / "ops" / "verify-worker-release.py"
PROVIDER = "cloudflare-workers"
VERSION = "11111111-2222-4333-8444-555555555555"
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
        return
    FAILURES.append(name)
    print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def run_record(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RECORD), *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )


def run_deploy(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(DEPLOY), *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )


def run_manifest(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REPO / "tools" / "release-manifest.py"), *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )


def run_release_verify(payload: str, *, version: str = VERSION,
                       posture: str = "enabled",
                       schema_highest: str = "0300_operational_hermes_bot_profiles.sql",
                       schema_count: int = 236) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERIFY_RELEASE), "--environment", "production",
         "--sha", "a" * 40, "--provider", PROVIDER,
         "--provider-version-id", version,
         "--expected-program6-actions", posture,
         "--expected-schema-highest-migration", schema_highest,
         "--expected-schema-applied-count", str(schema_count)],
        cwd=REPO,
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> int:
    print("cloudflare-version-promotion-selftest: upload is not promotion")
    deploy = DEPLOY.read_text(encoding="utf-8")
    record = RECORD.read_text(encoding="utf-8")
    verifier = VERIFY_RELEASE.read_text(encoding="utf-8") if VERIFY_RELEASE.exists() else ""

    check("1. wrapper exposes upload and exact-version promotion flags",
          "--upload-version" in deploy and "--promote-version" in deploy)
    check("1b. the two modes are explicitly mutually exclusive",
          "--upload-version and --promote-version are mutually exclusive" in deploy)
    check("1c. both provider-version modes are Production-only",
          "provider-version operations are Production-only" in deploy)
    both_modes = run_deploy("--upload-version", "--promote-version", VERSION)
    check("1d. mutual exclusion executes before any provider call",
          both_modes.returncode == 64
          and "mutually exclusive" in (both_modes.stdout + both_modes.stderr),
          f"rc={both_modes.returncode}")
    staging_upload = run_deploy("--env", "staging", "--upload-version")
    check("1e. staging provider upload is refused before any provider call",
          staging_upload.returncode == 1
          and "Production-only" in (staging_upload.stdout + staging_upload.stderr),
          f"rc={staging_upload.returncode}")
    mutable_alias = run_deploy("--promote-version", "latest")
    check("1f. promotion refuses a mutable alias before any provider call",
          mutable_alias.returncode == 1
          and "exact immutable Cloudflare UUID" in
          (mutable_alias.stdout + mutable_alias.stderr),
          f"rc={mutable_alias.returncode}")

    upload = re.search(
        r"# -- provider-version upload --(?P<body>.*?)# -- provider-version promotion --",
        deploy,
        re.DOTALL,
    )
    promote = re.search(
        r"# -- provider-version promotion --(?P<body>.*?)# -- ordinary source deploy --",
        deploy,
        re.DOTALL,
    )
    upload_body = upload.group("body") if upload else ""
    promote_body = promote.group("body") if promote else ""

    check("2. upload invokes `wrangler versions upload`",
          '"$WRANGLER" versions upload' in upload_body,
          "provider upload block missing")
    check("2b. upload never activates traffic",
          "versions deploy" not in upload_body
          and re.search(r'"\$WRANGLER"\s+deploy(?:\s|$)', upload_body) is None,
          "upload block contains a traffic/source deploy command")
    check("2c. upload binds the returned version into the approval plan",
          "release-manifest.py\" bind-provider" in upload_body
          and "BOUND_RELEASE_MANIFEST" in upload_body
          and "RELEASE_PLAN_HASH" in upload_body,
          "provider identity is not part of the post-upload approval hash")
    check("2d. upload reads only Wrangler's named Worker Version ID",
          "Worker Version ID:" in upload_body,
          "an unrelated UUID in provider output could be promoted")
    check("3. promotion deploys the exact supplied version at 100 percent",
          '"$WRANGLER" versions deploy "${PROVIDER_VERSION_ID}@100" --yes' in promote_body,
          "exact id@100 command missing")
    check("3b. promotion neither uploads nor source-deploys",
          "versions upload" not in promote_body
          and re.search(r'"\$WRANGLER"\s+deploy(?:\s|$)', promote_body) is None,
          "promotion block rebuilds or re-uploads source")
    check("3c. the ordinary source deploy remains staging-only",
          'if [ "$TARGET_ENV" != "production" ]; then' in deploy
          and '"$WRANGLER" deploy --env "$TARGET_ENV"' in deploy)

    missing_candidate = run_record(
        "release", "candidate", "--key", "selftest", "--environment", "production")
    check("4. Production candidates fail closed without provider identity",
          missing_candidate.returncode == 2
          and "requires --provider and --provider-version-id" in
          (missing_candidate.stdout + missing_candidate.stderr),
          f"rc={missing_candidate.returncode}")

    # ── the maker is not a caller field (standing-rule amendment 9) ──────
    #
    # The Gate Zero producer reads its SUBJECT MAKER out of the
    # release-candidate row, so a --maker somebody typed would be
    # caller-asserted metadata inside a receipt that claims provenance. Both
    # halves of the pair are refused, before any credential is opened, and the
    # refusal names where the identity comes from instead: migration 0504's
    # record of the login role that filed the row.
    for flag, value in (("--maker", "joe"), ("--maker-verification", "ref:anything")):
        asserted = run_record(
            "release", "candidate", "--key", "selftest", "--environment", "production",
            "--provider", PROVIDER, "--provider-version-id", VERSION, flag, value)
        check(f"4aa. a candidate asserting {flag} is refused",
              asserted.returncode == 2
              and "not a caller field" in (asserted.stdout + asserted.stderr)
              and "records the filing login role from session_user"
                  in (asserted.stdout + asserted.stderr),
              f"rc={asserted.returncode} err={(asserted.stderr or asserted.stdout)[:160]}")
    filed_at = deploy.find('ops-record.py" release candidate')
    filed_command = deploy[filed_at:deploy.find("|| fail", filed_at)] if filed_at != -1 else ""
    check("4ab. the wrapper files the candidate itself instead of printing a command",
          filed_at != -1
          and "Record this exact candidate" not in deploy
          and "--provider-version-id" in filed_command
          and "--manifest" in filed_command,
          "the upload path still hands the record to a human to run")
    check("4ac. and it passes no maker of its own",
          "--maker" not in filed_command,
          "the wrapper names a maker the database is supposed to derive")

    missing_require = run_record("release", "require", "--environment", "production")
    check("4b. Production approval lookup fails closed without provider identity",
          missing_require.returncode == 2
          and "requires --provider and --provider-version-id" in
          (missing_require.stdout + missing_require.stderr),
          f"rc={missing_require.returncode}")

    missing_deployment = run_record(
        "deployment", "--service", "carr-mcp", "--environment", "production",
        "--state", "verifying")
    check("4c. Production deployment evidence fails closed without provider identity",
          missing_deployment.returncode == 2
          and "requires --provider and --provider-version-id" in
          (missing_deployment.stdout + missing_deployment.stderr),
          f"rc={missing_deployment.returncode}")

    staging_claim = run_record(
        "release", "candidate", "--key", "selftest", "--environment", "staging",
        "--provider", PROVIDER, "--provider-version-id", VERSION)
    check("5. staging cannot claim the immutable Production provider version",
          staging_claim.returncode == 2
          and "only valid for Production" in
          (staging_claim.stdout + staging_claim.stderr),
          f"rc={staging_claim.returncode}")

    malformed_version = run_record(
        "release", "candidate", "--key", "selftest", "--environment", "production",
        "--provider", PROVIDER, "--provider-version-id", "latest")
    check("5b. a mutable alias cannot masquerade as Cloudflare's immutable id",
          malformed_version.returncode == 2
          and "must be an exact UUID" in
          (malformed_version.stdout + malformed_version.stderr),
          f"rc={malformed_version.returncode}")

    with tempfile.TemporaryDirectory(prefix="promotion-selftest-") as td:
        manifest_path = Path(td) / "bound.json"
        manifest_path.write_text(json.dumps({
            "service": "carr-mcp",
            "environment": "production",
            "provider": PROVIDER,
            "provider_version_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        }), encoding="utf-8")
        mismatched_manifest = run_record(
            "release", "candidate", "--key", "selftest",
            "--environment", "production", "--provider", PROVIDER,
            "--provider-version-id", VERSION, "--manifest", str(manifest_path))
    check("5c. candidate refuses a provider version different from its plan manifest",
          mismatched_manifest.returncode == 2
          and "must exactly match the bound release manifest" in
          (mismatched_manifest.stdout + mismatched_manifest.stderr),
          f"rc={mismatched_manifest.returncode}")

    with tempfile.TemporaryDirectory(prefix="promotion-verify-") as td:
        source_path = Path(td) / "source.json"
        bound_path = Path(td) / "bound.json"
        tampered_path = Path(td) / "tampered.json"
        built = run_manifest(
            "build", "--sha", "HEAD", "--environment", "production",
            "--performance-budget-ref", "runbook:worker-performance-v1",
            "--performance-budget-ms", "1500",
            "--recovery-strategy", "rollback",
            "--rollback-plan-ref", "runbook:rollback-worker-v1")
        if built.returncode == 0:
            source_path.write_text(built.stdout, encoding="utf-8")
            bound = run_manifest(
                "bind-provider", "--manifest", str(source_path),
                "--provider", PROVIDER, "--provider-version-id", VERSION)
        else:
            bound = built
        if bound.returncode == 0:
            bound_path.write_text(bound.stdout, encoding="utf-8")
            tampered = json.loads(bound.stdout)
            tampered["plan_hash"] = "plan:" + "0" * 32
            tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
            refused_tamper = run_record(
                "release", "candidate", "--key", "selftest-tampered",
                "--environment", "production", "--provider", PROVIDER,
                "--provider-version-id", VERSION.upper(),
                "--manifest", str(tampered_path))
            wrong_target_path = Path(td) / "wrong-target.json"
            wrong_target = json.loads(bound.stdout)
            wrong_target["environment"] = "staging"
            wrong_target_path.write_text(json.dumps(wrong_target), encoding="utf-8")
            refused_target = run_record(
                "release", "candidate", "--key", "selftest-wrong-target",
                "--environment", "production", "--service", "carr-mcp",
                "--provider", PROVIDER, "--provider-version-id", VERSION,
                "--manifest", str(wrong_target_path))
        else:
            refused_tamper = bound
            refused_target = bound
    check("5d. candidate verifies source evidence and plan hash before DB intake",
          refused_tamper.returncode == 2
          and "manifest verification failed" in
          (refused_tamper.stdout + refused_tamper.stderr),
          f"rc={refused_tamper.returncode} err={refused_tamper.stderr[:120]}")
    check("5da. candidate refuses a verified manifest for another target",
          refused_target.returncode == 2
          and "service/environment must exactly match" in
          (refused_target.stdout + refused_target.stderr),
          f"rc={refused_target.returncode}")

    # A service uploads the candidate and receives its own database-derived
    # maker identity. Manifest verification still precedes the connection.
    candidate_verify_at = record.find("release-manifest.py")
    candidate_connect_at = record.find('connection_kind = ("authority"', candidate_verify_at)
    check("5e. candidate verification runs before the connection opens",
          candidate_verify_at != -1 and candidate_connect_at > candidate_verify_at)
    check("5e-i. candidate and readiness use the scoped service connection",
          'else "routine" if args.action in ("candidate", "ready", "reopen")' in record)

    check("5f. Cloudflare UUIDs normalize to lowercase at both boundaries",
          "args.provider_version_id = version_id.lower()" in record
          and "tr 'A-F' 'a-f'" in deploy)

    check("6. release candidate persists both provider identity fields",
          re.search(r"CANDIDATE_INSERT,.*?args\.provider, "
                    r"args\.provider_version_id", record, re.DOTALL) is not None)
    check("6a. Production candidate verifies the manifest carries the exact pair",
          "manifest_identity != requested_identity" in record
          and "approval plan hash" in record)
    check("6b. release require matches both fields exactly",
          re.search(r"where .*?provider = %s.*?provider_version_id = %s",
                    record, re.DOTALL) is not None)
    check("6c. deployment evidence persists both provider identity fields",
          re.search(r"insert into ops\.deployment.*?provider.*?provider_version_id",
                    record, re.DOTALL) is not None)

    resolve_at = deploy.find("RELEASE_BINDING=")
    rebuild_at = deploy.find('build_release_manifest "$HEAD_SHA" production', resolve_at)
    bind_at = deploy.find('release-manifest.py" bind-provider', rebuild_at)
    recheck_at = deploy.find("RECONFIRMED_BINDING=", bind_at)
    promote_at = deploy.find('"$WRANGLER" versions deploy', recheck_at)
    check("7. promotion resolves SHA, recomputes evidence, then rechecks readiness",
          -1 not in (resolve_at, rebuild_at, bind_at, recheck_at, promote_at)
          and resolve_at < rebuild_at < bind_at < recheck_at < promote_at,
          f"positions={resolve_at,rebuild_at,bind_at,recheck_at,promote_at}")
    check("7b. final readiness check binds SHA, provider UUID, and plan hash",
          '--sha "$HEAD_SHA" --environment production' in deploy
          and '--provider-version-id "$PROVIDER_VERSION_ID"' in deploy
          and '--plan-hash "$RELEASE_PLAN_HASH"' in deploy)

    deploy_at = deploy.find('"$WRANGLER" versions deploy')
    readback_at = deploy.find('https://api.doctorcre.com/release', deploy_at)
    smoke_at = deploy.find('"$REPO/bin/smoke-and-record.sh"', readback_at)
    complete_at = deploy.find("record_deployment complete", smoke_at)
    check("8. machine release identity read-back gates golden completion",
          -1 not in (deploy_at, readback_at, smoke_at, complete_at)
          and deploy_at < readback_at < smoke_at < complete_at)
    for label, expected, haystack in (
        ("ok=true", 'payload.get("ok") is not True', verifier),
        ("Production environment", "--environment production", deploy),
        ("cloudflare provider", 'PROVIDER="cloudflare-workers"', deploy),
        ("worker version id", 'worker_version.get("id")', verifier),
    ):
        check(f"8a. read-back checks {label}", expected in haystack)
    check("8b. unavailable read-back records verifying and exits nonzero",
          "production_readback_unavailable" in deploy
          and "record_deployment verifying" in deploy)
    check("8c. malformed or mismatched read-back records failed and exits nonzero",
          "production_readback_mismatch" in deploy
          and "record_deployment failed" in deploy)
    check("8c1. read-back threads exact manifest schema highest/count",
          "--expected-schema-highest-migration" in deploy
          and "--expected-schema-applied-count" in deploy
          and 'schema.get("highest_applied_migration")' in verifier
          and 'schema.get("applied_count")' in verifier)
    good_release = json.dumps({
        "ok": True,
        "env": {"value": "production"},
        "git_sha": {"value": "a" * 40},
        "provider": PROVIDER,
        "worker_version": {"id": VERSION},
        "schema": {
            "highest_applied_migration": "0300_operational_hermes_bot_profiles.sql",
            "applied_count": 236,
        },
        "program6_actions": {"enabled": True, "posture": "enabled", "reason": None},
    })
    good = run_release_verify(good_release, version=VERSION.upper())
    check("8d. executable read-back verifier accepts the exact identity",
          good.returncode == 0, f"rc={good.returncode} err={good.stderr[:120]}")
    schema_wrong = json.loads(good_release)
    schema_wrong["schema"]["applied_count"] = 235
    schema_mismatch = run_release_verify(json.dumps(schema_wrong))
    check("8d1. executable read-back verifier refuses a schema count mismatch",
          schema_mismatch.returncode == 1 and "schema.applied_count" in schema_mismatch.stderr,
          f"rc={schema_mismatch.returncode}")
    wrong = json.loads(good_release)
    wrong["worker_version"]["id"] = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    mismatch = run_release_verify(json.dumps(wrong))
    check("8e. executable read-back verifier refuses an identity mismatch",
          mismatch.returncode == 1 and "mismatch" in mismatch.stderr,
          f"rc={mismatch.returncode}")
    malformed = run_release_verify("{not-json")
    check("8f. executable read-back verifier refuses malformed JSON",
          malformed.returncode == 2 and "malformed" in malformed.stderr,
          f"rc={malformed.returncode}")
    for observed, label in (
        ({"enabled": False, "posture": "disabled", "reason": None}, "disabled"),
        ({"enabled": False, "posture": "misconfigured", "reason": "bad"}, "misconfigured"),
        (None, "missing"),
    ):
        changed = json.loads(good_release)
        if observed is None:
            changed.pop("program6_actions")
        else:
            changed["program6_actions"] = observed
        rejected = run_release_verify(json.dumps(changed))
        check(f"8f.{label}. executable verifier refuses {label} Program 6 posture",
              rejected.returncode == 1 and "program6_actions" in rejected.stderr,
              f"rc={rejected.returncode} err={rejected.stderr[:120]}")
    check("8g. wrapper delegates parsing to the tested verifier",
          "ops/verify-worker-release.py" in deploy)

    print()
    if FAILURES:
        print(f"cloudflare-version-promotion-selftest: {len(FAILURES)} FAILED")
        return 1
    print("cloudflare-version-promotion-selftest: immutable promotion path is separated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
