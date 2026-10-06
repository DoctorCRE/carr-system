#!/usr/bin/env python3
"""
release-manifest-selftest.py — the P0-1 rebuild clause, proven on every change.

WHY THIS EXISTS SEPARATELY FROM ops/p0-1-release-gate.py. That gate needs a
database and runs against the isolated staging project. This one needs nothing
but git, so ops/ci.sh picks it up automatically (it globs ops/*-selftest.py) and
every proposed change re-proves the rebuild clause. A check that only runs when
somebody remembers to point it at a database is a check that stops running.

WHAT IT PROVES

  1. DETERMINISM. Two builds of the same SHA produce byte-identical digests. If
     this ever fails, the digest has picked up something ambient — a timestamp,
     a path, a machine — and "identical artifact rebuild" becomes unfalsifiable.

  2. THE DIGEST IS OF THE COMMIT, NOT THE CHECKOUT. The digest of HEAD is the
     same whether or not the working tree is dirty. This repo regularly holds
     another session's uncommitted work (rule 308ef1de), so a digest that moved
     with the working tree would be worthless exactly when it matters.

  3. SENSITIVITY. A different SHA whose deployed paths differ produces a
     different artifact digest. A digest that never changes is a constant
     wearing a hash's clothes.

  4. VERIFY ROUND-TRIPS. A freshly built manifest verifies against its own
     recorded SHA.

  5. VERIFY ACTUALLY BITES. A manifest with a tampered digest FAILS verify. This
     is the seeded failure for this clause: without it, assertion 4 could pass
     because verify returns zero unconditionally.

  6. THE PLAN HASH COVERS WHAT AN APPROVER READS, AND ONLY THAT. Changing a
     material field moves it; changing a deploy-time observation does not. The
     database trigger that voids stale approvals is only as good as this hash.

  7. PROVIDER VERSION BINDING IS POST-UPLOAD, IMMUTABLE, AND APPROVAL-BOUND.
     It changes the plan hash without changing source evidence, and a bound
     manifest still verifies its source digests.

  8. THE MANIFEST CARRIES ALL SEVEN CLASSES the acceptance names.
"""

import atexit
import copy
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "release-manifest.py"

_spec = importlib.util.spec_from_file_location("release_manifest", TOOL)
assert _spec and _spec.loader
RELEASE_MANIFEST = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RELEASE_MANIFEST)

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, text=True, cwd=REPO)


def build(*args: str) -> dict:
    out = run("build", "--performance-budget-ref", "runbook:worker-performance-v1",
              "--performance-budget-ms", "1500",
              "--recovery-strategy", "rollback",
              "--rollback-plan-ref", "runbook:rollback-worker-v1", *args)
    if out.returncode != 0:
        raise SystemExit(f"release-manifest-selftest: build failed: {out.stderr.strip()}")
    return json.loads(out.stdout)


def git(*args: str) -> str:
    return subprocess.run(("git", "-C", str(REPO), *args),
                          capture_output=True, text=True).stdout


def main() -> int:
    print("release-manifest-selftest: P0-1 rebuild clause")
    tool_source = TOOL.read_text(encoding="utf-8")
    ledger_start = tool_source.index("def migration_tree_ledger")
    ledger_end = tool_source.index("def ensure_exact_schema_prefix", ledger_start)
    ledger_impl = tool_source[ledger_start:ledger_end]
    check("0g. migration ledger reads one byte-preserving git archive",
          'git_bytes("archive", "--format=tar"' in ledger_impl
          and 'git("show", f"{sha}:migrations/{filename}")' not in ledger_impl)

    tested_sha = "HEAD"
    first = build("--sha", tested_sha)
    second = build("--sha", tested_sha)

    check("0a. manifest declares the full applied schema ledger count",
          isinstance(first.get("schema_applied_count"), int)
          and first["schema_applied_count"] > 0)
    check("0b. manifest binds the full applied schema ledger digest",
          isinstance(first.get("schema_ledger_sha256"), str)
          and first["schema_ledger_sha256"].startswith("sha256:")
          and len(first["schema_ledger_sha256"]) == 71)
    check("0c. highest applied migration is a full ledger filename",
          isinstance(first.get("schema_highest_migration"), str)
          and first["schema_highest_migration"].endswith(".sql"))
    check("0d. manifest derives one exact Program 6 posture from its SHA config",
          first.get("program6_actions") in (
              {"enabled": True, "posture": "enabled"},
              {"enabled": False, "posture": "disabled"}))
    doctorcre = first.get("doctorcre_artifact")
    check("0i. manifest binds the independent DoctorCRE source and archive",
          isinstance(doctorcre, dict)
          and doctorcre.get("source_commit") == "35936628aba4c7189b17f5b195c41c8ba9b782be"
          and doctorcre.get("archive_sha256") == "dca7745d28d6871f988c62c406406d652fce5ba9e61769083edd2ee5541aa6e5"
          and first.get("artifact_paths") == ["mcp-server"])

    # Seed the exact failure with the same numeric prefix but a different
    # filename/content pair.  A numeric-prefix comparison would incorrectly
    # accept this renamed migration; the release-manifest gate must refuse it.
    try:
        RELEASE_MANIFEST.ensure_exact_schema_prefix(
            [("0301a_schema.sql", "a" * 64)],
            [("0301_schema.sql", "b" * 64)],
        )
    except SystemExit as exc:
        check("0e. an exact filename/content ledger mismatch is refused",
              "exact migration ledger" in str(exc))
    else:
        check("0e. an exact filename/content ledger mismatch is refused", False,
              "same-number migration rename was accepted")

    exact_prefix = [("0300_schema.sql", "a" * 64)]
    expected_tree = exact_prefix + [("0301_schema.sql", "b" * 64)]
    try:
        RELEASE_MANIFEST.ensure_exact_schema_prefix(exact_prefix, expected_tree)
    except SystemExit as exc:
        check("0f. an exact pending migration suffix is accepted", False, str(exc))
    else:
        count, highest, digest = RELEASE_MANIFEST.schema_ledger_identity(expected_tree)
        check("0f. an exact pending suffix binds the post-rollout ledger",
              count == 2 and highest == "0301_schema.sql"
              and digest.startswith("sha256:") and len(digest) == 71)

    try:
        RELEASE_MANIFEST.ensure_exact_schema_prefix(
            [("0300_schema.sql", "a" * 64), ("0302_schema.sql", "c" * 64)],
            expected_tree + [("0302_schema.sql", "c" * 64)],
        )
    except SystemExit as exc:
        check("0h. a hole inside the applied ledger is refused",
              "exact migration ledger" in str(exc))
    else:
        check("0h. a hole inside the applied ledger is refused", False,
              "non-prefix snapshot ledger was accepted")

    # 1. determinism
    check("1. two builds of one SHA are identical",
          first == second,
          "the digest picked up something ambient")

    # 2. the digest belongs to the commit, not the checkout
    dirty = bool(git("status", "--porcelain").strip())
    head_sha = git("rev-parse", tested_sha).strip()
    by_sha = build("--sha", head_sha)
    check("2. tested SHA and its explicit SHA digest identically"
          + (" (working tree is dirty, which is the interesting case)" if dirty else ""),
          by_sha["artifact_digest"] == first["artifact_digest"])

    # 3. sensitivity: the comparison commit must be one whose deployed-path tree
    #    GENUINELY differs from HEAD's. `git log -- <path>` is not that test: it
    #    lists a commit that changed the path relative to its own parent, which
    #    includes commits a later one changed straight back. Asking git for the
    #    real difference is the precise question, and it keeps the assertion
    #    exactly as strong: a different deployed tree must digest differently.
    other = None
    # The Worker artifact digests mcp-server only since the DoctorCRE pin (P2):
    # dealroom ships as its own pinned artifact. Searching dealroom too let a
    # dealroom-only branch pick a comparison commit whose Worker tree is
    # identical, so the assertion failed on a true "same digest" (2026-09-25,
    # run 36138851445). Compare only the paths the digest actually covers.
    for sha in git("log", "-40", "--format=%H", "--", "mcp-server").split():
        if sha == head_sha:
            continue
        differs = subprocess.run(
            ("git", "-C", str(REPO), "diff", "--quiet", sha, head_sha,
             "--", "mcp-server",
             ":(exclude)mcp-server/.last-deployed-verb-count"))
        if differs.returncode != 0:
            candidate_attempt = run("build", "--sha", sha,
                                    "--performance-budget-ref", "runbook:worker-performance-v1",
                                    "--performance-budget-ms", "1500",
                                    "--recovery-strategy", "rollback",
                                    "--rollback-plan-ref", "runbook:rollback-worker-v1")
            if candidate_attempt.returncode == 0:
                other = sha
                break
    if other:
        older = build("--sha", other)
        check("3. a commit whose deployed tree differs digests differently",
              older["artifact_digest"] != first["artifact_digest"],
              f"{other[:12]} has a different tree but the SAME digest")
    else:
        check("3. a commit whose deployed tree differs digests differently",
              False, "no differing commit in the last 40 — cannot prove sensitivity")

    # 4 + 5. verify round-trips, and verify bites
    with tempfile.TemporaryDirectory() as tmp:
        good = Path(tmp) / "good.json"
        good.write_text(json.dumps(first))
        out = run("verify", "--manifest", str(good))
        check("4. a fresh manifest verifies against its own SHA",
              out.returncode == 0, out.stdout.strip()[-300:])

        tampered = dict(first)
        tampered["artifact_digest"] = "sha256:" + "0" * 64
        bad = Path(tmp) / "bad.json"
        bad.write_text(json.dumps(tampered))
        out = run("verify", "--manifest", str(bad))
        check("5. a tampered digest FAILS verify",
              out.returncode != 0,
              "verify accepted a manifest whose digest does not match its SHA")

        stale_count = dict(first)
        stale_count["schema_applied_count"] += 1
        stale_count["plan_hash"] = _plan_hash(stale_count)
        bad.write_text(json.dumps(stale_count))
        out = run("verify", "--manifest", str(bad))
        check("5b. a valid-shaped stale applied-ledger count FAILS verify",
              out.returncode != 0)

        stale_ledger = dict(first)
        stale_ledger["schema_ledger_sha256"] = "sha256:" + "1" * 64
        stale_ledger["plan_hash"] = _plan_hash(stale_ledger)
        bad.write_text(json.dumps(stale_ledger))
        out = run("verify", "--manifest", str(bad))
        check("5c. a valid-shaped stale applied-ledger digest FAILS verify",
              out.returncode != 0)

    # 6. the plan hash covers material fields only
    material = dict(first)
    material["config_fingerprint"] = "sha256:" + "1" * 64
    out = run("plan-hash", "--manifest", _tmp_json(material))
    moved = out.stdout.strip()
    check("6a. changing configuration moves the plan hash",
          moved and moved != first["plan_hash"])

    posture = dict(first)
    posture["program6_actions"] = {"enabled": not first["program6_actions"]["enabled"],
                                   "posture": "disabled" if first["program6_actions"]["enabled"] else "enabled"}
    out = run("plan-hash", "--manifest", _tmp_json(posture))
    check("6aa. derived Program 6 posture preserves the established plan-hash contract",
          out.stdout.strip() == first["plan_hash"])
    out = run("program6-posture", "--manifest", _tmp_json(first))
    check("6ab. manifest posture reader returns the reviewed typed value",
          out.returncode == 0 and out.stdout.strip() == first["program6_actions"]["posture"])

    observed = dict(first)
    observed["commit_subject"] = "a different subject line entirely"
    out = run("plan-hash", "--manifest", _tmp_json(observed))
    check("6b. a non-material field does NOT move the plan hash",
          out.stdout.strip() == first["plan_hash"])

    performance = dict(first)
    performance["performance_budget_ms"] = 1501
    out = run("plan-hash", "--manifest", _tmp_json(performance))
    check("6c. changing the approved performance budget moves the plan hash",
          out.stdout.strip() and out.stdout.strip() != first["plan_hash"])
    partial = run("build", "--sha", tested_sha, "--performance-budget-ref",
                  "runbook:worker-performance-v1")
    check("6d. partial performance assurance input is refused", partial.returncode != 0)
    recovery_plan = dict(first)
    recovery_plan["rollback_plan_ref"] = "runbook:rollback-worker-v2"
    out = run("plan-hash", "--manifest", _tmp_json(recovery_plan))
    check("6e. changing the recovery plan moves the plan hash",
          out.stdout.strip() and out.stdout.strip() != first["plan_hash"])

    schema_count = dict(first)
    schema_count["schema_applied_count"] += 1
    out = run("plan-hash", "--manifest", _tmp_json(schema_count))
    check("6f. changing the full applied-ledger count moves the plan hash",
          out.stdout.strip() and out.stdout.strip() != first["plan_hash"])

    schema_digest = dict(first)
    schema_digest["schema_ledger_sha256"] = "sha256:" + "2" * 64
    out = run("plan-hash", "--manifest", _tmp_json(schema_digest))
    check("6g. changing the full applied-ledger digest moves the plan hash",
          out.stdout.strip() and out.stdout.strip() != first["plan_hash"])

    external_product = copy.deepcopy(first)
    external_product["doctorcre_artifact"]["archive_sha256"] = "3" * 64
    out = run("plan-hash", "--manifest", _tmp_json(external_product))
    check("6h. changing the pinned DoctorCRE artifact moves the plan hash",
          out.stdout.strip() and out.stdout.strip() != first["plan_hash"])
    external_product["plan_hash"] = out.stdout.strip()
    out = run("verify", "--manifest", _tmp_json(external_product))
    check("6i. a self-consistent but false DoctorCRE pin fails rebuild verification",
          out.returncode != 0)

    legacy_out = run("build", "--sha", tested_sha)
    legacy = json.loads(legacy_out.stdout) if legacy_out.returncode == 0 else {}
    legacy_path = _tmp_json(legacy)
    legacy_verify = run("verify", "--manifest", legacy_path)
    check("6j. an all-absent historical assurance group still round-trips",
          legacy_out.returncode == 0 and legacy_verify.returncode == 0)

    # Find the pre-P2 commit deterministically rather than walking a fixed
    # number of commits back from HEAD. A commit-count window rots: every
    # merge to main pushes the P2-adding commit further back, and once it
    # falls outside the window this assertion silently stops finding a
    # historical (pre-P2) commit at all. Instead, locate the commit that
    # ADDED ops/config/doctorcre-artifact.v1.json and use its first parent —
    # the file is added exactly once, and everything before that add is, by
    # definition, pre-P2.
    add_commits = git("log", "--diff-filter=A", "--format=%H",
                      "--", "ops/config/doctorcre-artifact.v1.json").strip()
    if add_commits == "":
        raise SystemExit(
            "release-manifest-selftest: could not find the commit that added "
            "ops/config/doctorcre-artifact.v1.json — history is unavailable "
            "(shallow clone?). This job's checkout needs fetch-depth: 0.")
    artifact_added_sha = add_commits.splitlines()[-1]  # oldest, if ever re-added

    parent = subprocess.run(
        ("git", "-C", str(REPO), "rev-parse", f"{artifact_added_sha}^"),
        capture_output=True, text=True)
    if parent.returncode != 0:
        raise SystemExit(
            "release-manifest-selftest: could not resolve the parent of the "
            f"commit that added the P2 artifact ({artifact_added_sha}) — "
            "history is unavailable (shallow clone?). This job's checkout "
            "needs fetch-depth: 0.")
    historical_sha = parent.stdout.strip()

    present = subprocess.run(
        ("git", "-C", str(REPO), "cat-file", "-e",
         f"{historical_sha}:ops/config/doctorcre-artifact.v1.json"),
        capture_output=True)
    if present.returncode == 0:
        raise SystemExit(
            "release-manifest-selftest: expected the P2 artifact to be "
            f"absent at {historical_sha} (parent of the commit that added "
            "it), but it is present. The add-commit detection is wrong.")

    historical = build("--sha", historical_sha) if historical_sha else {}
    historical_verify = run("verify", "--manifest", _tmp_json(historical)) if historical else None
    check("6k. pre-P2 manifests retain the legacy joint-source recipe",
          bool(historical_sha and historical.get("artifact_paths") == ["mcp-server", "dealroom"]
               and "doctorcre_artifact" not in historical
               and historical_verify and historical_verify.returncode == 0))

    # 7. Provider versions do not exist until Cloudflare has uploaded the
    # source. Binding that returned identity must preserve source evidence and
    # produce the exact plan hash an approver sees.
    with tempfile.TemporaryDirectory() as tmp:
        source_path = Path(tmp) / "source.json"
        source_path.write_text(json.dumps(first))
        out = run("bind-provider", "--manifest", str(source_path),
                  "--provider", "cloudflare-workers",
                  "--provider-version-id", "cf-version-test-001")
        check("7a. a post-upload provider version binds successfully",
              out.returncode == 0, out.stderr.strip())
        bound = json.loads(out.stdout) if out.returncode == 0 else {}
        source_fields = ("git_sha", "artifact_digest", "dependency_lock_digest",
                         "config_fingerprint", "migration_set",
                         "schema_highest_migration", "schema_applied_count",
                         "schema_ledger_sha256", "doctorcre_artifact")
        check("7b. binding preserves the SHA and every source digest",
              all(bound.get(k) == first.get(k) for k in source_fields))
        check("7c. provider/version binding changes the approval plan hash",
              bool(bound.get("plan_hash")
                   and bound.get("plan_hash") != first["plan_hash"]))

        bound_path = Path(tmp) / "bound.json"
        bound_path.write_text(json.dumps(bound))
        out = run("verify", "--manifest", str(bound_path))
        check("7d. a bound manifest verifies its recorded source evidence",
              out.returncode == 0, out.stdout.strip()[-300:])

        out = run("bind-provider", "--manifest", str(source_path),
                  "--provider", "cloudflare-workers", "--provider-version-id", "")
        check("7e. an empty provider version ID is refused", out.returncode != 0)

        out = run("bind-provider", "--manifest", str(bound_path),
                  "--provider", "cloudflare-workers",
                  "--provider-version-id", "cf-version-test-002")
        check("7f. a changed provider version is refused", out.returncode != 0)

        out = run("bind-provider", "--manifest", str(bound_path),
                  "--provider", "different-provider",
                  "--provider-version-id", "cf-version-test-001")
        check("7g. a changed provider is refused", out.returncode != 0)

        hash_tampered = dict(bound)
        hash_tampered["plan_hash"] = "plan:" + "0" * 32
        tampered_path = Path(tmp) / "plan-hash-tampered.json"
        tampered_path.write_text(json.dumps(hash_tampered))
        out = run("verify", "--manifest", str(tampered_path))
        check("7h. a tampered bound plan hash FAILS verify", out.returncode != 0)

        invalid_contract = dict(bound)
        invalid_contract["recovery_strategy"] = "make-up-a-recovery-receipt"
        invalid_contract["plan_hash"] = "plan:" + "0" * 32
        invalid_path = Path(tmp) / "invalid-contract.json"
        invalid_path.write_text(json.dumps(invalid_contract))
        out = run("verify", "--manifest", str(invalid_path))
        check("7i. invalid recovery strategy FAILS verify", out.returncode != 0)

    # 8. all seven acceptance classes present
    classes = {
        "code": ("git_sha", "artifact_digest", "dependency_lock_digest"),
        "schema": ("schema_highest_migration", "schema_applied_count",
                   "schema_ledger_sha256", "migration_set"),
        "config": ("config_fingerprint", "config_paths"),
        "plan": ("plan_hash",),
        "doctorcre": ("doctorcre_artifact",),
    }
    missing = [f"{name}.{field}"
               for name, fields in classes.items()
               for field in fields
               if first.get(field) in (None, "", [])]
    check("8. the manifest carries code, schema, config and plan",
          not missing, "missing: " + ", ".join(missing) if missing else "")

    print()
    if FAILURES:
        print(f"release-manifest-selftest: {len(FAILURES)} FAILED")
        for f in FAILURES:
            print(f"  {f}")
        return 1
    print("release-manifest-selftest: the rebuild clause holds")
    return 0


_TMPDIR = tempfile.mkdtemp(prefix="release-manifest-selftest-")
atexit.register(shutil.rmtree, _TMPDIR, ignore_errors=True)


def _tmp_json(obj: dict) -> str:
    path = Path(_TMPDIR) / f"m{abs(hash(json.dumps(obj, sort_keys=True)))}.json"
    path.write_text(json.dumps(obj))
    return str(path)


def _plan_hash(obj: dict) -> str:
    out = run("plan-hash", "--manifest", _tmp_json(obj))
    if out.returncode != 0:
        raise SystemExit(f"release-manifest-selftest: plan-hash failed: {out.stderr.strip()}")
    return out.stdout.strip()


if __name__ == "__main__":
    sys.exit(main())
