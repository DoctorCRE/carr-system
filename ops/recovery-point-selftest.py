#!/usr/bin/env python3
"""recovery-point-selftest.py — the checks that make ops/recovery-point.py worth
having.

THE ONE THING THIS MUST PROVE. The module exists because a two-state answer
(fresh / stale) produced a four-day false alarm while a twelve-hour-old backup
sat in GitHub. The fix is a THIRD state, and a third state is only worth
anything if it never silently collapses into one of the other two. Both
collapses are failures, in opposite directions:

  · unknown read as FRESH hides a genuinely dead workflow — the worst outcome,
    because it is the one nobody investigates.
  · unknown read as a GAP rebuilds the false alarm one layer up, and trains
    people to ignore the line.

So both directions get their own case, and neither is inferred from the other.

The cases drive assess() and the path readers with fabricated inputs rather
than the real filesystem or the real GitHub API: a check that needs the network
to run is a check that goes amber on a plane, and a check that reads the real
backups/ directory would pass or fail depending on the day it is run.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE / "recovery-point.py"

# NEVER LET BYTECODE ANSWER FOR SOURCE. Found the hard way on 2026-08-21 while
# mutation-testing this very file: changing `min` to `max` in assess() leaves
# the source the SAME NUMBER OF BYTES, and restoring it with `cp` in the same
# second leaves the mtime unchanged too. Python validates its cache on exactly
# (mtime, size), so the mutated .pyc was still being executed against restored
# source — the checks read red while the file on disk was correct, and the
# obvious conclusion, that the restore had failed, was wrong.
#
# A check that can be answered by a stale artifact is not checking the artifact
# (rule a9ecd5b4). So: drop any cached bytecode before loading, and never write
# any from this process.
sys.dont_write_bytecode = True
importlib.invalidate_caches()
_cached = MODULE_PATH.parent / "__pycache__"
if _cached.is_dir():
    for stale in _cached.glob("recovery-point.*.pyc"):
        stale.unlink()

spec = importlib.util.spec_from_file_location("recovery_point", MODULE_PATH)
if spec is None or spec.loader is None:      # mypy: both are Optional by signature
    raise SystemExit(f"recovery-point-selftest: cannot load {MODULE_PATH}")
rp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rp)

PASSED: list[str] = []
FAILED: list[str] = []


def check(label: str, ok: bool) -> None:
    (PASSED if ok else FAILED).append(label)
    print(f"{'PASS' if ok else 'FAIL'}  {label}")


def present(path: str, age_hours: float) -> dict:
    when = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    return {"path": path, "state": "present", "configured": True,
            "at": when.isoformat().replace("+00:00", "Z"),
            "age_hours": age_hours, "detail": "fixture"}


def unknown(path: str) -> dict:
    return {"path": path, "state": "unknown", "detail": "fixture: could not ask"}


def none(path: str, configured: bool = True) -> dict:
    return {"path": path, "state": "none", "configured": configured,
            "detail": "fixture: nothing on record"}


def fake_gh(path: Path) -> None:
    """Install one provider-boundary fake; production code still runs unchanged."""
    path.write_text(
        f"#!{sys.executable}\n" + """
import json, os, sys
fixture = json.load(open(os.environ['CARR_TEST_GH_FIXTURE'], encoding='utf-8'))
with open(os.environ['CARR_TEST_GH_LOG'], 'a', encoding='utf-8') as log:
    log.write(json.dumps(sys.argv[1:]) + '\\n')
args = ' '.join(sys.argv[1:])
if fixture.get('api_error'):
    print(fixture['api_error'], file=sys.stderr)
    raise SystemExit(1)
if len(sys.argv) > 2 and sys.argv[1:3] == ['run', 'list']:
    value = fixture.get('runs_cli', fixture.get('workflow_runs', []))
elif 'check-runs' in args:
    rows = fixture.get('check_runs', [])
    if fixture.get('check_requires_filter_all') and 'filter=all' not in sys.argv:
        rows = []
    value = {'total_count': len(rows), 'check_runs': rows}
elif '/artifacts' in args or 'actions/artifacts' in args:
    value = {'total_count': len(fixture.get('artifacts', [])),
             'artifacts': fixture.get('artifacts', [])}
elif 'actions/runs' in args:
    value = {'total_count': len(fixture.get('workflow_runs', [])),
             'workflow_runs': fixture.get('workflow_runs', [])}
else:
    value = fixture.get('default', {})
print(json.dumps(value))
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def cloud_fixture(fixture: dict) -> tuple[dict, list[list[str]]]:
    with tempfile.TemporaryDirectory(prefix="carr-rpo-provider-") as raw:
        root = Path(raw)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        fake_gh(bin_dir / "gh")
        fixture_path = root / "fixture.json"
        log_path = root / "calls.jsonl"
        fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
        saved = {name: os.environ.get(name) for name in (
            "PATH", "CARR_TEST_GH_FIXTURE", "CARR_TEST_GH_LOG", "GITHUB_REPOSITORY",
        )}
        os.environ.update({
            "PATH": str(bin_dir),
            "CARR_TEST_GH_FIXTURE": str(fixture_path),
            "CARR_TEST_GH_LOG": str(log_path),
            "GITHUB_REPOSITORY": "jbookout/carr-system",
        })
        try:
            try:
                result = rp.cloud_path(repo=str(root))
            except Exception as exc:  # a malformed provider response is unknown, never a crashed chain
                result = {"path": "cloud", "state": "crashed", "detail": repr(exc)}
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        calls = ([json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
                 if log_path.exists() else [])
        return result, calls


def provider_fixture(*, artifact_hours: float = 2.0, run_attempt: int = 2,
                     artifact_attempt: int = 2, include_check: bool = True,
                     include_artifact: bool = True, run_id: int = 101,
                     artifact_id: int = 7001, run_created_hours: float = 5 / 60) -> dict:
    now = datetime.now(timezone.utc)
    head = "a" * 40
    created = (now - timedelta(hours=artifact_hours)).isoformat().replace("+00:00", "Z")
    run_created = (now - timedelta(hours=run_created_hours)).isoformat().replace("+00:00", "Z")
    identity = {
        "repository": "jbookout/carr-system", "run_id": run_id,
        "run_attempt": run_attempt, "head_sha": head,
    }
    artifact = {
        "id": artifact_id,
        "name": f"carr-20260906-run-{run_id}-attempt-{artifact_attempt}.sql.age",
        "size_in_bytes": 2_097_152,
        "digest": "sha256:" + "b" * 64,
        "created_at": created,
        "expires_at": (now + timedelta(days=30)).isoformat().replace("+00:00", "Z"),
        "expired": False,
        # GitHub's artifact response omits run_attempt. The name supplies the
        # attempt locator used for the exact run-attempt API readback.
        "workflow_run": {"id": run_id, "head_sha": head},
    }
    check_output = {
        "artifact_id": artifact_id, "artifact_name": artifact["name"],
        "artifact_digest": artifact["digest"], "artifact_bytes": artifact["size_in_bytes"],
        "artifact_created_at": artifact["created_at"], "artifact_expires_at": artifact["expires_at"],
        "required_steps": ["dump", "encrypt", "upload", "readback"],
    }
    run = {
        "id": run_id, "databaseId": run_id, "run_attempt": run_attempt,
        "head_sha": head, "headSha": head, "created_at": run_created,
        "createdAt": run_created, "status": "completed", "conclusion": "success",
        "path": ".github/workflows/backup-nightly.yml",
    }
    return {
        "runs_cli": [run], "workflow_runs": [run],
        "check_runs": ([{
            "id": artifact_id + 1000, "name": "Backup artifact", "status": "completed",
            "conclusion": "success", "head_sha": head,
            "external_id": json.dumps(identity, sort_keys=True, separators=(",", ":")),
            "output": {"title": "Backup artifact", "summary": json.dumps(check_output, sort_keys=True)},
        }] if include_check else []),
        "artifacts": [artifact] if include_artifact else [],
    }


def main() -> int:
    # ── the two collapses, each in its own direction ─────────────────────────
    r = rp.assess([none("local", configured=True), unknown("cloud")])
    check("unknown is NOT reported as fresh (a dead workflow stays visible)",
          r["verdict"] != "ok" and r["age_hours"] is None)
    check("unknown is NOT reported as a gap (the false alarm is not rebuilt)",
          r["verdict"] == "unknown")

    r = rp.assess([unknown("local"), unknown("cloud")])
    check("no path readable at all is unknown, not a gap", r["verdict"] == "unknown")

    r = rp.assess([none("local"), none("cloud")])
    check("every path answering 'nothing on record' IS a real gap",
          r["verdict"] == "gap")

    # ── the newest path wins, whichever one it is ────────────────────────────
    r = rp.assess([present("local", 104.0), present("cloud", 12.0)])
    check("newest across paths wins when cloud is fresher",
          r["verdict"] == "ok" and r["newest_path"] == "cloud" and r["age_hours"] == 12.0)

    r = rp.assess([present("local", 3.0), present("cloud", 30.0)])
    check("newest across paths wins when local is fresher",
          r["verdict"] == "ok" and r["newest_path"] == "local")

    # THE EXACT SHAPE OF THE 2026-08-21 FALSE ALARM, pinned as a regression:
    # a stale local dump beside a fresh cloud run must read OK, not out of
    # contract. This is the case the old one-line shell test got wrong.
    r = rp.assess([present("local", 109.9), present("cloud", 17.5)])
    check("the 2026-08-21 case reads OK, not out of contract",
          r["verdict"] == "ok" and r["age_hours"] == 17.5)

    # ── a real gap is still a real gap ───────────────────────────────────────
    r = rp.assess([present("local", 50.0), present("cloud", 40.0)])
    check("both paths past the objective IS out of contract",
          r["verdict"] == "out_of_contract" and r["age_hours"] == 40.0)

    r = rp.assess([present("cloud", 25.0)], rpo_hours=24)
    check("one hour past the objective still trips it",
          r["verdict"] == "out_of_contract")

    # A conclusive stale path cannot turn provider uncertainty into a proven
    # gap. The older aggregate selected stale whenever *any* present path
    # existed, even if another required path could not be read.
    r = rp.assess([present("local", 50.0), unknown("cloud")])
    check("stale local plus unknown cloud remains unknown",
          r["verdict"] == "unknown" and r["age_hours"] is None)
    r = rp.assess([present("local", 2.0), unknown("cloud")])
    check("one verified fresh path still yields ok despite another unknown",
          r["verdict"] == "ok" and r["newest_path"] == "local")

    # The Check and artifact are the successful recovery-point evidence. A
    # workflow conclusion alone must never become a recovery point.
    src = MODULE_PATH.read_text(encoding="utf-8")

    # ── exact Check -> artifact provider provenance ─────────────────────────
    no_artifact, _ = cloud_fixture(provider_fixture(include_check=False, include_artifact=False))
    check("workflow success without its named Check and artifact is not a recovery point",
          no_artifact["state"] != "present")

    filter_fixture = provider_fixture()
    filter_fixture["check_requires_filter_all"] = True
    valid, valid_calls = cloud_fixture(filter_fixture)
    check("exact successful Check and artifact metadata yield a cloud recovery point",
          valid["state"] == "present"
          and (valid.get("artifact_id") == 7001 or "7001" in valid.get("detail", "")))
    check("cloud reader queries exact attempt, all Checks, and artifact surfaces",
          any("check-runs" in " ".join(call) for call in valid_calls)
          and any("filter=all" in call for call in valid_calls)
          and any("/actions/runs/101/attempts/2" in " ".join(call)
                  for call in valid_calls)
          and any("artifact" in " ".join(call) for call in valid_calls))

    mismatch, _ = cloud_fixture(provider_fixture(run_attempt=2, artifact_attempt=1))
    check("artifact from another run attempt is rejected",
          mismatch["state"] != "present")

    embedded_attempt = provider_fixture()
    embedded_attempt["artifacts"][0]["name"] = "carr-20260906-run-101-attempt-20.sql.age"
    embedded_summary = json.loads(
        embedded_attempt["check_runs"][0]["output"]["summary"]
    )
    embedded_summary["artifact_name"] = embedded_attempt["artifacts"][0]["name"]
    embedded_attempt["check_runs"][0]["output"]["summary"] = json.dumps(
        embedded_summary, sort_keys=True
    )
    embedded, _ = cloud_fixture(embedded_attempt)
    check("attempt 2 never matches an artifact whose exact name says attempt 20",
          embedded["state"] != "present")

    prior_attempt = provider_fixture(run_attempt=1, artifact_attempt=1)
    prior_attempt["workflow_runs"].append({
        **prior_attempt["workflow_runs"][0],
        "run_attempt": 2,
        "conclusion": "failure",
    })
    prior_attempt["runs_cli"] = prior_attempt["workflow_runs"]
    prior, prior_calls = cloud_fixture(prior_attempt)
    check("a valid prior attempt survives a later failed rerun of the same run ID",
          prior["state"] == "present"
          and any("/actions/runs/101/attempts/1" in " ".join(call)
                  for call in prior_calls))

    missing, _ = cloud_fixture(provider_fixture(include_artifact=False))
    check("successful named Check with a missing artifact is not present",
          missing["state"] != "present")

    zero_fixture = provider_fixture()
    zero_fixture["artifacts"][0]["size_in_bytes"] = 0
    zero, _ = cloud_fixture(zero_fixture)
    check("zero-byte artifact is rejected", zero["state"] != "present")

    expired_fixture = provider_fixture()
    expired_fixture["artifacts"][0]["expired"] = True
    expired, _ = cloud_fixture(expired_fixture)
    check("provider-expired artifact is rejected", expired["state"] != "present")

    digest_fixture = provider_fixture()
    digest_fixture["artifacts"][0]["digest"] = "sha256:" + "c" * 64
    wrong_digest, _ = cloud_fixture(digest_fixture)
    check("artifact digest must match the successful Check metadata",
          wrong_digest["state"] != "present")

    head_fixture = provider_fixture()
    head_fixture["artifacts"][0]["workflow_run"]["head_sha"] = "d" * 40
    wrong_head, _ = cloud_fixture(head_fixture)
    check("artifact head must match run and Check provenance",
          wrong_head["state"] != "present")

    stale, _ = cloud_fixture(provider_fixture(artifact_hours=49.0))
    check("rerun/check time cannot refresh an old artifact",
          stale["state"] == "present" and stale.get("age_hours", 0) >= 48.0)

    older = provider_fixture(
        artifact_hours=20.0, run_id=101, artifact_id=7001, run_created_hours=0.1
    )
    newer = provider_fixture(
        artifact_hours=2.0, run_id=102, artifact_id=7002, run_created_hours=72.0
    )
    artifact_order = {
        "artifacts": older["artifacts"] + newer["artifacts"],
        "workflow_runs": older["workflow_runs"] + newer["workflow_runs"],
        "runs_cli": older["workflow_runs"] + newer["workflow_runs"],
        "check_runs": older["check_runs"] + newer["check_runs"],
    }
    newest, _ = cloud_fixture(artifact_order)
    check("artifact creation time wins even when its workflow run was created earlier",
          newest["state"] == "present" and newest.get("artifact_id") == 7002)

    legacy = provider_fixture(include_check=False)
    legacy["artifacts"][0]["name"] = "carr-legacy.sql.age"
    unverified, _ = cloud_fixture(legacy)
    check("a legacy backup-like artifact without complete provenance makes absence unknown",
          unverified["state"] == "unknown")

    for failure in ("exit", "timeout"):
        runner = mock.Mock(return_value=subprocess.CompletedProcess(
            ["gh"], 1, "", "synthetic provider unavailable"))
        if failure == "timeout":
            runner.side_effect = subprocess.TimeoutExpired(["gh"], 30)
        delays: list[float] = []
        reader = rp.GitHubReader(gh="gh", runner=runner, sleep=delays.append)
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "jbookout/carr-system"}), \
             mock.patch.object(rp.shutil, "which", return_value="gh"), \
             mock.patch.object(rp, "GitHubReader", return_value=reader):
            failed_api = rp.cloud_path(repo="/fixture")
        check(f"provider {failure} failure is unknown", failed_api["state"] == "unknown")
        check(f"a transient provider {failure} is retried before it reads unknown",
              runner.call_count == 3 and delays == [5, 15])

    cap_fixtures = [
        provider_fixture(
            run_id=1000 + offset,
            artifact_id=8000 + offset,
            include_check=False,
        )
        for offset in range(rp.MAX_CANDIDATES + 1)
    ]
    horizon = {
        "artifacts": [item["artifacts"][0] for item in cap_fixtures],
        "workflow_runs": [item["workflow_runs"][0] for item in cap_fixtures],
        "runs_cli": [item["workflow_runs"][0] for item in cap_fixtures],
        "check_runs": [],
    }
    exhausted, exhausted_calls = cloud_fixture(horizon)
    check("artifact candidate cap exhaustion before the RPO horizon is unknown",
          exhausted["state"] == "unknown"
          and len(exhausted_calls) > rp.MAX_CANDIDATES
          and not any(len(call) > 1 and call[1].endswith("/actions/runs")
                      for call in exhausted_calls))

    # ── degradation is real, not claimed ─────────────────────────────────────
    # Point the reader at a PATH with no gh on it. It must return unknown
    # rather than raising, because an unattended 2am chain cannot handle a
    # traceback and must not be stopped by a missing optional tool.
    with tempfile.TemporaryDirectory() as empty:
        saved = os.environ.get("PATH")
        try:
            os.environ["PATH"] = empty
            out = rp.cloud_path()
        finally:
            os.environ["PATH"] = saved if saved is not None else ""
    check("cloud reader degrades to unknown when gh is absent, without raising",
          out["state"] == "unknown" and "gh" in out["detail"])

    # ── local: absent credential is not a failure on this machine ────────────
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "backups"), exist_ok=True)
        saved = os.environ.pop("CARR_DB_BACKUP_URL", None)
        try:
            out = rp.local_path(tmp)
        finally:
            if saved is not None:
                os.environ["CARR_DB_BACKUP_URL"] = saved
    check("local with no dumps and no credential reports state+configured, not a crash",
          out["state"] == "none" and out["configured"] is False)

    # ── exit codes are the contract the chain branches on ────────────────────
    codes = {"ok": 0, "out_of_contract": 1, "gap": 1, "unknown": 2}
    check("exit-code map covers every verdict assess() can return",
          set(codes) == {"ok", "out_of_contract", "gap", "unknown"}
          and all(f'"{k}"' in src for k in codes))

    # ── the module actually runs end to end ──────────────────────────────────
    proc = subprocess.run([sys.executable, str(MODULE_PATH), "--hours"],
                          capture_output=True, text=True, timeout=60)
    printed = (proc.stdout or "").strip()
    check("--hours prints a whole number or the word 'unknown'",
          printed == "unknown" or printed.isdigit())
    check("--hours exits with one of the three contract codes",
          proc.returncode in (0, 1, 2))

    print(f"\nrecovery-point-selftest: {len(PASSED)}/{len(PASSED) + len(FAILED)} passed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
