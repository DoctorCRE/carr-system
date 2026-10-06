#!/usr/bin/env python3
"""
deploy-release-wiring-selftest.py — the deploy path names an approved release,
or it does not ship.

WHAT THIS GUARDS. P0-1 put the release object in the database and gave migration
0131 the teeth to refuse a production deployment that names no approved release.
None of that reaches a real deploy unless bin/deploy-worker.sh actually asks. So
this proves the wrapper asks, that a refusal happens BEFORE wrangler is invoked,
and that all three outcomes of a deploy reach the ledger.

WHY SOURCE ASSERTIONS AND NOT A FULL RUN. deploy-worker.sh refuses to run at all
unless the tree is exactly origin/main with a clean Worker directory and a real
wrangler binary — by design, and none of it is reproducible inside a test. The
house pattern for that shape is ops/backup-dump-selftest.py: assert the exact
command form in the source, and execute the parts that CAN run hermetically.
Here the executable part is the release preflight's own logic, exercised through
tools/ops-record.py with no database at all.

  1. THE REFUSAL PRECEDES THE DEPLOY. The require check appears before the
     wrangler invocation. A gate that runs after the ship is a report.

  2. THE REFUSAL IS A REFUSAL. Any non-zero result from the require check
     reaches `fail`; a ledger outage cannot become permission to ship.

  3. ALL THREE OUTCOMES REACH THE LEDGER. complete, verifying and failed each
     have a record_deployment call. A read-back belongs only to complete or the
     explicit machine-verified Production identity receipt.

  4. THE FAILED PATH RECORDS BEFORE IT EXITS. Recording after `exit 1` records
     nothing at all.

  5. THE MANIFEST IS BUILT BY THE ONE TOOL. The wrapper shells out to
     tools/release-manifest.py rather than carrying a second copy of the digest
     decision (rule a8c55a47).

  6. A source rehearsal's `release require` DEMANDS ITS SHA and refuses without
     one, so a staging wrapper that loses the variable gets an error rather
     than a pass. Production instead requires provider + immutable version.

  7. THE PRINTED ROLLBACK COMMAND USES THE PRODUCTION PROMOTION FLAG. Production
     no longer rebuilds a SHA; rollback approves and promotes a known immutable
     provider version through `--promote-version`.
"""

import re
import subprocess
import sys
import json
import os
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "deploy-worker.sh"
RECORD = REPO / "tools" / "ops-record.py"
ROLLBACK_RUNBOOK = REPO / "runbooks" / "rollback-worker.md"

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def completion_invocation(source: str) -> tuple[int, list[str]]:
    """Run only the wrapper's completion branch with a fake recorder."""
    start = source.index('  if [ "$rd_state" = "complete" ] && [ -n "$RELEASE_KEY" ]; then')
    end = source.index("\n  return 0\n}", start)
    close_branch = source[start:end]
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        call_log = tmp / "call.json"
        fake_python = tmp / "python"
        fake_python.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "Path(os.environ['CALL_LOG']).write_text(json.dumps(sys.argv[1:]))\n",
            encoding="utf-8")
        fake_python.chmod(0o755)
        script = tmp / "run.sh"
        script.write_text(
            "#!/bin/sh\nset -eu\n"
            + close_branch + "\n",
            encoding="utf-8")
        script.chmod(0o755)
        done = subprocess.run(
            ["sh", str(script)], capture_output=True, text=True,
            env={**os.environ, "PY": str(fake_python), "REPO": str(REPO),
                 "RELEASE_KEY": "approved-release", "TARGET_ENV": "production",
                 "rd_state": "complete", "CALL_LOG": str(call_log)})
        call = json.loads(call_log.read_text()) if call_log.exists() else []
        return done.returncode, call


def main() -> int:
    print("deploy-release-wiring-selftest: the deploy names its release")
    source = SCRIPT.read_text(encoding="utf-8")

    # 1. the gate precedes the ship
    require_at = source.find("release require")
    deploy_at = source.find('"$WRANGLER" deploy')
    check("1. the require check runs BEFORE wrangler deploy",
          require_at != -1 and deploy_at != -1 and require_at < deploy_at,
          f"require at {require_at}, wrangler at {deploy_at}")

    # 2. Provider promotion is strict, while routine source rehearsal preserves
    # main's credential boundary: only the explicit not-approved result blocks.
    check("2. provider promotion refuses an unresolved provider identity",
          re.search(r'\[ "\$REQUIRE_RC" -eq 0 \]\s*\\\s*\n\s*\|\| fail '
                    r'"no unique recorded candidate binds Production', source) is not None
          and 'release ready' in source
          and 'RECONFIRMED_BINDING=' in source,
          "provider-version promotion can proceed without exact release truth")
    check("2b. source rehearsal treats exit 3 as the approval refusal",
          re.search(r'REQUIRE_RC["}]*\s*-eq\s*3', source) is not None
          and re.search(r'-eq 3 \]; then\s*\n\s*fail ', source) is not None,
          "routine staging/rehearsal lost main's scoped credential boundary")

    # 3. all three outcomes are recorded
    for state in ("complete", "verifying", "failed"):
        check(f"3. the {state} outcome records a deployment",
              f"record_deployment {state}" in source)

    check("3b. only complete/verified identity claims a read-back",
          '"$rd_state" = "complete"' in source
          and '"$rd_readback_kind" = "identity-readback"' in source
          and "--read-back-at now" in source,
          "the read-back is not conditioned on complete or verified identity")

    # 3d. THE PRODUCTION READ-BACK RETRIES BEFORE IT CONDEMNS A DEPLOY.
    # It used to read /release once, the instant Wrangler returned, and write
    # whatever it saw into the production ledger as a permanent verdict. A
    # Cloudflare promotion is not globally consistent that instant, so a single
    # early read can observe the PREVIOUS identity — which is exactly what
    # happened to release.eed.prod.v3 on 2026-08-19 at 23:36:47Z, eleven seconds
    # after Joe approved it. The deploy recorded state=failed while the Worker it
    # had just promoted was serving the approved identity correctly.
    #
    # That row also froze ops/last-deployed-verb-count.py, which reads its
    # baseline from the newest COMPLETE production row: it stayed at 130 verbs
    # while production served 140, so a deploy shipping 131 would have passed the
    # verb-loss guard while dropping nine live verbs.
    #
    # A settled mismatch must still fail, so this pins BOTH halves: the loop
    # exists, and the failed record still happens when the loop gives up.
    check("3d. the Production read-back retries before recording a mismatch",
          "LIVE_READBACK_ATTEMPTS" in source
          and "LIVE_RELEASE_OK" in source
          and re.search(r'while \[ "\$LIVE_RELEASE_ATTEMPT" -lt "\$LIVE_READBACK_ATTEMPTS" \]',
                        source) is not None
          and 'if [ "$LIVE_RELEASE_OK" -ne 1 ]; then' in source,
          "a single early read can condemn a healthy deploy, and did on 2026-08-19")

    check("3e. exhausting the retries still records the failure",
          re.search(r'if \[ "\$LIVE_RELEASE_OK" -ne 1 \]; then(?:.|\n)*?'
                    r'record_deployment failed', source) is not None,
          "retrying must not become a way to never fail")

    # 3c. a verified deploy CLOSES its release
    close_at = source.find("release complete --key")
    check("3c. a complete deploy closes the release, and only a complete one",
          close_at != -1 and '"$rd_state" = "complete" ] && [ -n "$RELEASE_KEY"' in source,
          "nothing advances the release past approved, so it and its deployment disagree")
    close_rc, close_call = completion_invocation(source)
    check("3c1. completion preserves the approval-bound verifier pair",
          close_rc == 0
          and close_call == [str(REPO / "tools" / "ops-record.py"), "release", "complete",
                             "--key", "approved-release"],
          f"rc={close_rc} call={close_call!r}")

    # 4. the failed path records before it exits
    failed_at = source.find("record_deployment failed")
    exit_at = source.find("exit 1", failed_at if failed_at != -1 else 0)
    check("4. the failed path records BEFORE exit 1",
          failed_at != -1 and exit_at > failed_at,
          "recording after the exit records nothing")

    # 5. one digest decision, not two
    check("5. the manifest comes from tools/release-manifest.py",
          "tools/release-manifest.py" in source
          # The wrapper may validate the manifest's declared schema_ledger_sha256
          # field, but it must not compute a competing digest itself.
          and "sha256(" not in source
          and "digest(" not in source,
          "the wrapper appears to compute a digest of its own")

    # 6. a source rehearsal still refuses without a SHA, with no DB in sight
    out = subprocess.run(
        [sys.executable, str(RECORD), "release", "require",
         "--environment", "staging"],
        capture_output=True, text=True, cwd=REPO)
    check("6. staging `release require` refuses with no --sha",
          out.returncode == 2 and "needs --sha" in (out.stderr + out.stdout),
          f"rc={out.returncode} err={out.stderr.strip()[:120]}")

    check("6a. one builder binds target and assurance for every deploy rebuild",
          "build_release_manifest()" in source
          and source.count("build_release_manifest ") >= 3
          and 'build --sha "$BUILD_MANIFEST_SHA"' in source
          and '--environment "$BUILD_MANIFEST_ENVIRONMENT"' in source,
          "candidate and deploy can recompute different approval preimages")
    check("6b. standalone staging requires the full assurance preimage",
          "standalone staging release requires performance/recovery assurance" in source,
          "staging can still approve a recovery plan the deploy does not rebuild")
    record_source = RECORD.read_text(encoding="utf-8")
    check("6c. staging refusal prints an explicit target and assurance build",
          "--environment staging --performance-budget-ref <immutable-ref>" in record_source
          and "--performance-budget-ms <milliseconds>" in record_source
          and "--rollback-plan-ref <immutable-ref>" in record_source,
          "the recovery instruction can recreate the production-default manifest defect")

    check("7. rollback instruction uses immutable provider promotion",
          "bin/deploy-worker.sh --promote-version "
          "<approved-prior-version-id>." in source
          and "Rolling back is bin/deploy-worker.sh --release-sha <sha>." not in source,
          "failure output tells Production to rebuild a SHA instead of promoting "
          "an approved provider version")

    rollback_runbook = ROLLBACK_RUNBOOK.read_text(encoding="utf-8")
    recovery_flags = (
        "--release-key",
        "--recovery-attempt-id",
        "--recovery-prior-release-key",
        "--recovery-step current_before",
        "--recovery-step prior",
        "--recovery-step current_after",
        "--staging-receipt-idempotency-key",
    )
    check("8. rollback runbook uses the typed three-step recovery chain",
          all(flag in rollback_runbook for flag in recovery_flags)
          and "The final `current_after` step creates the recovery bundle" in rollback_runbook,
          "the approved runbook can drift back to an unbound staging procedure")
    check("8b. rollback runbook forbids the retired manual receipt path",
          "ops-record.py run --kind check" not in rollback_runbook
          and "do not create or approve separate staging releases" in rollback_runbook,
          "manual or standalone staging receipts cannot satisfy the typed recovery bundle")
    check("8c. rollback runbook pins recovery order and freshness windows",
          "Run `current_before`, `prior`, and `current_after` in that order" in rollback_runbook
          and "finish all\nthree within one hour" in rollback_runbook
          and "within 24\nhours of the completed bundle" in rollback_runbook,
          "the typed bundle and approval both fail closed when their timing windows expire")
    check("8d. only a DB-prepared typed recovery step can temporarily shrink",
          "--allow-shrink" not in source
          and '[ "$RECOVERY_STEP" != "standalone" ] || fail "standalone deploys cannot authorize verb shrink."' in source
          and 'staging-attempt prepare' in source
          and 'staging-restore-only prepare' in source
          and 'current_before|prior|current_after)' in source
          and 'restore_only)' in source
          and "exact prepared typed recovery step" in source
          and "There is no `--allow-shrink` override" in rollback_runbook,
          "a standalone/source deploy or manual flag can still waive verb loss")

    check("9. restore-only is a separate staging writer, not a fourth bundle step",
          '"restore_only"' in source
          and "staging-restore-only" in source
          and "record_staging_restore_only_result" in RECORD.read_text(encoding="utf-8")
          and "structurally outside the three\nreceipt tables" in rollback_runbook,
          "a repair could be routed through current_after and forge a bundle leg")

    check("10. only an exact legacy prior may omit candidate stamps",
          'LEGACY_PRIOR_WITHOUT_CANDIDATE_STAMP=0' in source
          and '[ "$VERSION_MODE" = "ordinary" ]' in source
          and '[ "$TARGET_ENV" = "staging" ]' in source
          and '[ "$RECOVERY_STEP" = "prior" ]' in source
          and '[ -n "$EXACT_SOURCE_ROOT" ]' in source
          and '[ ! -e "$WORKER_DIR/src/build-stamp.js" ]' in source,
          "a non-prior, non-exact, or stamp-aware source could bypass sealing")
    check("10b. legacy prior keeps SHA/tag and omits only candidate stamps",
          'deploy_staging_worker()' in source
          and 'if [ "$LEGACY_PRIOR_WITHOUT_CANDIDATE_STAMP" = "1" ]; then' in source
          and '"$WRANGLER" deploy --env "$TARGET_ENV" --var "GIT_SHA:$HEAD_SHA"' in source
          and '--tag "$DEPLOY_TAG"' in source,
          "the compatibility route can lose deterministic deployment provenance")
    stamp_intro = "ab9678a86f427e8f9e5d1f75597a21b920630995"
    intro_paths = (
        "mcp-server/src/build-stamp.js",
        "mcp-server/bin/seal-candidate-manifest.mjs",
    )
    intro_has_both = all(subprocess.run(
        ["git", "cat-file", "-e", f"{stamp_intro}:{path}"], cwd=REPO,
        capture_output=True, check=False).returncode == 0 for path in intro_paths)
    parent_has_either = any(subprocess.run(
        ["git", "cat-file", "-e", f"{stamp_intro}^:{path}"], cwd=REPO,
        capture_output=True, check=False).returncode == 0 for path in intro_paths)
    check("10c. both pinned boundaries are their canonical introduction",
          f'BUILD_STAMP_INTRODUCTION_SHA="{stamp_intro}"' in source
          and f'CANDIDATE_SEALER_INTRODUCTION_SHA="{stamp_intro}"' in source
          and intro_has_both and not parent_has_either,
          "the pinned provenance boundary does not match canonical Git history")
    provenance_start = source.index("exact_source_predates_candidate_stamps() {")
    provenance_end = source.index("\n}\n", provenance_start) + 2
    provenance_gate = source[provenance_start:provenance_end]
    check("10d. legacy qualification requires strict ancestry to both boundaries",
          provenance_gate.count('git -C "$SOURCE_ROOT" merge-base --is-ancestor') == 2
          and '[ "$HEAD_SHA" != "$BUILD_STAMP_INTRODUCTION_SHA" ]' in provenance_gate
          and '[ "$HEAD_SHA" != "$CANDIDATE_SEALER_INTRODUCTION_SHA" ]' in provenance_gate,
          "file deletion after stamp introduction could impersonate a legacy source")

    upload_start = source.index('if [ "$VERSION_MODE" = "upload" ]; then',
                                source.index("== deploy =="))
    upload_end = source.index("# -- provider-version promotion --", upload_start)
    upload = source[upload_start:upload_end]
    staging_view_at = upload.find(
        'versions view "$FOUNDATION_ASSURANCE_STAGING_PROVIDER" --env staging --json')
    version_upload_at = upload.find("versions upload")
    candidate_at = upload.find('"$REPO/tools/ops-record.py" release candidate')
    sealer_at = upload.find('node "$REPO/mcp-server/bin/seal-foundation-assurance-evidence.mjs"')
    final_view_at = upload.find('versions view "$PROVIDER_VERSION_ID" --json')
    check("11. WR95 accepts only a verified staging UUID and refuses caller test evidence",
          "--foundation-assurance-staging-provider" in source
          and "--foundation-assurance-staging-candidate-operation" in source
          and "--foundation-assurance-staging-replacement-receipt" in source
          and "--foundation-assurance-staging-replacement-source" in source
          and "WR95 derives --test-evidence from live acquisition; caller evidence is refused." in source,
          "the final evidence route can accept caller evidence or an unbound target")
    check("11b. exact staging provider/readback precede final upload",
          0 <= staging_view_at < version_upload_at
          and '(live.get("git_sha") or {}).get("value")!=sha' in upload
          and 'worker_version") or {}).get("id")!=wanted' in upload,
          "evidence could be acquired from a different staging build")
    check("11c. candidate commits before synchronous sealer storage",
          0 <= candidate_at < sealer_at
          and '--staging-candidate-operation-id "$FOUNDATION_ASSURANCE_STAGING_CANDIDATE_OPERATION"' in upload
          and '--staging-replacement-receipt-id "$FOUNDATION_ASSURANCE_STAGING_REPLACEMENT_RECEIPT"' in upload
          and '--staging-replacement-source-sha "$FOUNDATION_ASSURANCE_STAGING_REPLACEMENT_SOURCE"' in upload
          and 'WR95_SEAL_PID' not in upload
          and '> "$WR95_SEAL_OUTPUT" &' not in upload,
          "background acquisition can race the candidate row or survive its failure")
    check("11d. final provider secret binding is authenticated before candidacy",
          0 <= version_upload_at < final_view_at < candidate_at
          and "DATABASE_URL_FOUNDATION_ASSURANCE_WRITER" in upload,
          "the release row can name a provider whose required secret is absent")
    check("11e. WR95 temporary artifacts are owned by the cleanup trap",
          all(name in source[source.index("cleanup_ephemeral() {"):source.index("cleanup_on_signal() {")]
              for name in ("WR95_STAGING_VERSION_JSON", "WR95_STAGING_RELEASE_JSON",
                           "WR95_FINAL_VERSION_JSON", "WR95_SEAL_OUTPUT")),
          "an interrupted upload can strand evidence-bearing temporary files")

    print()
    if FAILURES:
        print(f"deploy-release-wiring-selftest: {len(FAILURES)} FAILED")
        for f in FAILURES:
            print(f"  {f}")
        return 1
    print("deploy-release-wiring-selftest: the deploy path is wired to release truth")
    return 0


if __name__ == "__main__":
    sys.exit(main())
