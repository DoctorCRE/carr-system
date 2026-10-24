#!/usr/bin/env python3
"""Check deterministic release source identity and the independent DoctorCRE artifact pin."""

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
    out = run("build", *args)
    if out.returncode != 0:
        raise SystemExit(f"release-manifest-selftest: build failed: {out.stderr.strip()}")
    return json.loads(out.stdout)


def git(*args: str) -> str:
    return subprocess.run(("git", "-C", str(REPO), *args),
                          capture_output=True, text=True).stdout


def main() -> int:
    print("release-manifest-selftest: source identity and DoctorCRE artifact pin")
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
            candidate_attempt = run("build", "--sha", sha)
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

    external_product = copy.deepcopy(first)
    external_product["doctorcre_artifact"]["archive_sha256"] = "3" * 64
    out = run("plan-hash", "--manifest", _tmp_json(external_product))
    check("6h. changing the pinned DoctorCRE artifact moves the plan hash",
          out.stdout.strip() and out.stdout.strip() != first["plan_hash"])
    external_product["plan_hash"] = out.stdout.strip()
    out = run("verify", "--manifest", _tmp_json(external_product))
    check("6i. a self-consistent but false DoctorCRE pin fails rebuild verification",
          out.returncode != 0)

    classes = {
        "code": ("git_sha", "artifact_digest", "dependency_lock_digest"),
        "schema": ("schema_highest_migration", "schema_applied_count",
                   "schema_ledger_sha256", "migration_set"),
        "config": ("config_fingerprint", "config_paths"),
        "doctorcre": ("doctorcre_artifact",),
    }
    missing = [f"{name}.{field}"
               for name, fields in classes.items()
               for field in fields
               if first.get(field) in (None, "", [])]
    check("8. the manifest carries code, schema, config and the DoctorCRE artifact",
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
