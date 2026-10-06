#!/usr/bin/env python3
"""Hermetic contracts for the second Drive-reader repoint slice."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PY = ROOT / ".venv/bin/python"
if not PY.exists():
    PY = Path(sys.executable)

passed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    passed += 1
    print(f"ok {passed:02d} - {label}")


def run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True,
                          env=env, timeout=90)


with tempfile.TemporaryDirectory(prefix="drive-reader-slice2-") as td:
    tmp = Path(td)
    now = datetime.now(timezone.utc).isoformat()
    health_fixture = tmp / "health.json"
    health_fixture.write_text(json.dumps({
        "observed_at": now,
        "exports": {"registered": ["leads"], "rows": [
            {"target": "leads", "last_ok": now, "latest_status": "ok"}
        ]},
        "job_definitions": [{"key": "nightly", "version": 1,
                             "recurrence": {"cron": None}, "registered_at": now}],
        "jobs": [{"definition_key": "nightly", "state": "succeeded",
                  "definition_version": 1, "id": "job-ok", "mode": "live",
                  "attempt": 1, "max_attempts": 3, "created_at": now,
                  "completion_receipt_count": 1}],
        "errors": [],
    }))
    poisoned = dict(os.environ)
    poisoned["CARR_VAULT"] = "/DO-NOT-READ-THIS-DRIVE"

    p = run(str(PY), "tools/health-check.py", "--section", "exports",
            "--fixture", str(health_fixture), env=poisoned)
    check("normal health reads canonical export receipts", p.returncode == 0 and
          "canonical export_run receipts" in p.stdout, p.stdout + p.stderr)
    check("normal health discards ambient CARR_VAULT",
          "DO-NOT-READ" not in p.stdout + p.stderr, p.stdout + p.stderr)

    p = run(str(PY), "tools/health-check.py", "--section", "jobs",
            "--fixture", str(health_fixture), env=poisoned)
    check("normal health reads durable Control Plane jobs", p.returncode == 0 and
          "durable Control Plane job state" in p.stdout and "every due window present" in p.stdout,
          p.stdout + p.stderr)

    failed_export = tmp / "failed-export.json"
    failed_export.write_text(json.dumps({
        "observed_at": now,
        "exports": {"registered": ["leads"], "rows": [
            {"target": "leads", "last_ok": now, "latest_status": "failed"}
        ]}, "errors": [],
    }))
    p = run(str(PY), "tools/health-check.py", "--section", "exports",
            "--fixture", str(failed_export))
    check("latest export failure is red even after a recent success",
          p.returncode == 1 and "LATEST FAILED leads" in p.stdout and
          "CANONICAL_FINDING export_receipt" in p.stdout, p.stdout + p.stderr)

    fixed_now = "2026-08-17T12:10:00+00:00"
    missing_due = tmp / "missing-due.json"
    missing_due.write_text(json.dumps({
        "observed_at": fixed_now, "exports": None, "errors": [],
        "job_definitions": [{"key": "daily", "version": 1,
                             "recurrence": {"cron": "0 7 * * *",
                                            "timezone": "America/Chicago",
                                            "source": "tracked definition"},
                             "registered_at": "2026-08-16T00:00:00+00:00"}],
        "jobs": [],
    }))
    p = run(str(PY), "tools/health-check.py", "--section", "jobs",
            "--fixture", str(missing_due))
    check("a passed cron window with no live job is MISSING DUE",
          p.returncode == 1 and "MISSING DUE execution" in p.stdout and
          "CANONICAL_FINDING job_missing_due" in p.stdout, p.stdout + p.stderr)

    # LOOP #536: a definition whose LEGACY scheduler still owns execution has
    # no Control Plane ledger row by design, and calling that a missed window
    # made the drift check unreadable — every uncut job read MISSING DUE for
    # days while out/nightly.log proved the chain had run. The carried state
    # must stay visible (rule bd4a6d22), never become silence, and the default
    # stays fail-closed: a definition that does not say it is still on its
    # legacy scheduler is still held to its due windows.
    legacy_live_due = tmp / "legacy-live-due.json"
    legacy_live_due.write_text(json.dumps({
        "observed_at": fixed_now, "exports": None, "errors": [],
        "job_definitions": [{"key": "daily", "version": 1,
                             "recurrence": {"cron": "0 7 * * *",
                                            "timezone": "America/Chicago",
                                            "source": "tracked definition"},
                             "legacy_live": True,
                             "registered_at": "2026-08-16T00:00:00+00:00"}],
        "jobs": [],
    }))
    p = run(str(PY), "tools/health-check.py", "--section", "jobs",
            "--fixture", str(legacy_live_due))
    check("a definition still run by its legacy scheduler is carried, not MISSING DUE",
          p.returncode == 0 and "MISSING DUE" not in p.stdout and
          "1 definition(s) still on a legacy scheduler" in p.stdout,
          p.stdout + p.stderr)

    legacy_live_broken = tmp / "legacy-live-broken.json"
    legacy_live_broken.write_text(json.dumps({
        "observed_at": fixed_now, "exports": None, "errors": [],
        "job_definitions": [{"key": "daily", "version": 1,
                             "recurrence": {"cron": "0 7 * * *",
                                            "timezone": "America/Chicago",
                                            "source": "tracked definition"},
                             "legacy_live": True,
                             "registered_at": "2026-08-16T00:00:00+00:00"}],
        "jobs": [{"id": "dead", "definition_key": "daily",
                  "definition_version": 1, "state": "dead_lettered", "mode": "live",
                  "attempt": 2, "max_attempts": 2,
                  "scheduled_for": "2026-08-17T12:00:00+00:00",
                  "created_at": "2026-08-17T12:00:00+00:00",
                  "completion_receipt_count": 0}],
    }))
    p = run(str(PY), "tools/health-check.py", "--section", "jobs",
            "--fixture", str(legacy_live_broken))
    check("carrying a legacy-scheduled definition never hides a real terminal failure",
          p.returncode == 1 and "CANONICAL_FINDING job_terminal_failure" in p.stdout,
          p.stdout + p.stderr)

    cutover_due = tmp / "cutover-due.json"
    cutover_due.write_text(json.dumps({
        "observed_at": fixed_now, "exports": None, "errors": [],
        "job_definitions": [{"key": "daily", "version": 1,
                             "recurrence": {"cron": "0 7 * * *",
                                            "timezone": "America/Chicago",
                                            "source": "tracked definition"},
                             "legacy_live": False,
                             "legacy_disabled_at": "2026-08-17T13:00:00+00:00",
                             "registered_at": "2026-08-12T00:00:00+00:00"}],
        "jobs": [],
    }))
    p = run(str(PY), "tools/health-check.py", "--section", "jobs",
            "--fixture", str(cutover_due))
    check("windows before the legacy schedule was disabled are not the ledger's to answer",
          p.returncode == 0 and "MISSING DUE" not in p.stdout, p.stdout + p.stderr)

    post_cutover_due = tmp / "post-cutover-due.json"
    post_cutover_due.write_text(json.dumps({
        "observed_at": fixed_now, "exports": None, "errors": [],
        "job_definitions": [{"key": "daily", "version": 1,
                             "recurrence": {"cron": "0 7 * * *",
                                            "timezone": "America/Chicago",
                                            "source": "tracked definition"},
                             "legacy_live": False,
                             "legacy_disabled_at": "2026-08-15T13:00:00+00:00",
                             "registered_at": "2026-08-12T00:00:00+00:00"}],
        "jobs": [],
    }))
    p = run(str(PY), "tools/health-check.py", "--section", "jobs",
            "--fixture", str(post_cutover_due))
    check("a due window AFTER the cutover instant is still MISSING DUE",
          p.returncode == 1 and
          "MISSING DUE execution for 2026-08-17 07:00" in p.stdout,
          p.stdout + p.stderr)

    matched_due = tmp / "matched-due.json"
    matched_due.write_text(json.dumps({
        "observed_at": fixed_now, "errors": [],
        "job_definitions": [{"key": "daily", "version": 1,
                             "recurrence": {"cron": "0 7 * * *",
                                            "timezone": "America/Chicago",
                                            "source": "tracked definition"},
                             "registered_at": "2026-08-16T13:00:00+00:00"}],
        "jobs": [{"id": "due-live", "definition_key": "daily",
                  "definition_version": 1, "state": "succeeded", "mode": "live",
                  "attempt": 1, "max_attempts": 2,
                  "scheduled_for": "2026-08-17T12:00:00+00:00",
                  "created_at": "2026-08-17T12:00:00+00:00",
                  "completion_receipt_count": 1}],
    }))
    p = run(str(PY), "tools/health-check.py", "--section", "jobs",
            "--fixture", str(matched_due))
    check("an exact live scheduled execution satisfies its due window",
          p.returncode == 0 and "MISSING DUE" not in p.stdout, p.stdout + p.stderr)

    # A collapsed monthly cadence permits one of its eligible cron instants,
    # not merely any live row in the same month.  These fixtures pin the P1
    # regressions that used to make all three cases falsely green.
    monthly_definition = {"key": "monthly", "version": 1,
                          "recurrence": {"cron": "0 7 6-10 * *",
                                         "timezone": "America/Chicago",
                                         "source": "collapses window"},
                          "registered_at": "2026-07-01T00:00:00+00:00"}

    cancelled_monthly = tmp / "cancelled-monthly.json"
    cancelled_monthly.write_text(json.dumps({
        "observed_at": fixed_now, "errors": [],
        "job_definitions": [monthly_definition],
        "jobs": [{"id": "cancelled-monthly", "definition_key": "monthly",
                  "definition_version": 1, "state": "cancelled", "mode": "live",
                  "attempt": 1, "max_attempts": 2,
                  "scheduled_for": "2026-08-06T12:00:00+00:00",
                  "created_at": "2026-08-06T12:00:00+00:00",
                  "completion_receipt_count": 0}],
    }))
    p = run(str(PY), "tools/health-check.py", "--section", "jobs",
            "--fixture", str(cancelled_monthly))
    check("cancelled eligible monthly execution is non-success, never green",
          p.returncode == 1 and "monthly window 2026-08 NON-SUCCESS execution (cancelled)" in p.stdout and
          "CANONICAL_FINDING job_due_non_success" in p.stdout, p.stdout + p.stderr)

    old_failed_monthly = tmp / "old-failed-monthly.json"
    old_failed_monthly.write_text(json.dumps({
        "observed_at": fixed_now, "errors": [],
        "job_definitions": [monthly_definition],
        "jobs": [{"id": "old-failed-monthly", "definition_key": "monthly",
                  "definition_version": 1, "state": "failed", "mode": "live",
                  "attempt": 1, "max_attempts": 1,
                  "scheduled_for": "2026-08-06T12:00:00+00:00",
                  "created_at": "2026-08-06T12:00:00+00:00",
                  "completion_receipt_count": 0}],
    }))
    p = run(str(PY), "tools/health-check.py", "--section", "jobs",
            "--fixture", str(old_failed_monthly))
    check("failed monthly execution older than terminal-failure lookback is still non-success",
          p.returncode == 1 and "monthly window 2026-08 NON-SUCCESS execution (failed)" in p.stdout,
          p.stdout + p.stderr)

    for label, scheduled_for in (("off-window", "2026-08-05T12:00:00+00:00"),
                                 ("future", "2026-08-20T12:00:00+00:00")):
        invalid_monthly = tmp / f"{label}-monthly.json"
        invalid_monthly.write_text(json.dumps({
            "observed_at": fixed_now, "errors": [],
            "job_definitions": [monthly_definition],
            "jobs": [{"id": f"{label}-monthly", "definition_key": "monthly",
                      "definition_version": 1, "state": "succeeded", "mode": "live",
                      "attempt": 1, "max_attempts": 1,
                      "scheduled_for": scheduled_for,
                      "created_at": "2026-08-05T12:00:00+00:00",
                      "completion_receipt_count": 1}],
        }))
        p = run(str(PY), "tools/health-check.py", "--section", "jobs",
                "--fixture", str(invalid_monthly))
        check(f"{label} same-month monthly row cannot satisfy the due window",
              p.returncode == 1 and "monthly MISSING DUE execution for monthly window 2026-08" in p.stdout,
              p.stdout + p.stderr)

    stuck = tmp / "stuck.json"
    stuck.write_text(json.dumps({
        "observed_at": fixed_now, "errors": [], "job_definitions": [],
        "jobs": [
            {"id": "queued", "definition_key": "q", "definition_version": 1,
             "state": "queued", "mode": "live", "attempt": 0, "max_attempts": 2,
             "scheduled_for": "2026-08-17T11:00:00+00:00"},
            {"id": "running", "definition_key": "r", "definition_version": 1,
             "state": "running", "mode": "live", "attempt": 1, "max_attempts": 2,
             "started_at": "2026-08-17T11:00:00+00:00",
             "leased_until": "2026-08-17T11:30:00+00:00", "timeout_seconds": 7200},
            {"id": "timeout", "definition_key": "timeout", "definition_version": 1,
             "state": "running", "mode": "live", "attempt": 1, "max_attempts": 2,
             "started_at": "2026-08-17T10:00:00+00:00",
             "leased_until": "2026-08-17T12:30:00+00:00", "timeout_seconds": 3600},
            {"id": "retry", "definition_key": "retry", "definition_version": 1,
             "state": "retry_wait", "mode": "live", "attempt": 1, "max_attempts": 2,
             "next_attempt_at": "2026-08-17T11:30:00+00:00"},
            {"id": "approval", "definition_key": "approval", "definition_version": 1,
             "state": "waiting_approval", "mode": "live", "attempt": 1,
             "max_attempts": 2, "created_at": "2026-08-15T11:00:00+00:00"},
            {"id": "receipt", "definition_key": "receipt", "definition_version": 1,
             "state": "succeeded", "mode": "live", "attempt": 2,
             "max_attempts": 2, "completion_receipt_count": 0,
             "created_at": "2026-08-17T11:00:00+00:00"},
        ],
    }))
    p = run(str(PY), "tools/health-check.py", "--section", "jobs",
            "--fixture", str(stuck))
    for token in ("queued more than 15m", "expired lease", "past registered timeout",
                  "retry_wait more than 5m",
                  "waiting_approval more than 24h", "without exact completion receipt"):
        check(f"canonical jobs flag {token}", p.returncode == 1 and token in p.stdout,
              p.stdout + p.stderr)

    nonlive = tmp / "nonlive.json"
    nonlive.write_text(json.dumps({
        "observed_at": fixed_now, "errors": [],
        "job_definitions": [{"key": "daily", "version": 1,
                             "recurrence": {"cron": "0 7 * * *",
                                            "timezone": "America/Chicago",
                                            "source": "tracked definition"},
                             "registered_at": "2026-08-16T00:00:00+00:00"}],
        "jobs": [{"id": "shadow", "definition_key": "daily",
                  "definition_version": 1, "state": "failed", "mode": "shadow",
                  "attempt": 1, "max_attempts": 1,
                  "scheduled_for": "2026-08-17T12:00:00+00:00",
                  "created_at": "2026-08-17T11:00:00+00:00"}],
    }))
    p = run(str(PY), "tools/health-check.py", "--section", "jobs",
            "--fixture", str(nonlive))
    check("shadow/canary/replay jobs do not contaminate live health",
          p.returncode == 1 and "shadow failed" not in p.stdout and
          "MISSING DUE execution" in p.stdout, p.stdout + p.stderr)

    p = run(str(PY), "tools/health-check.py", "--recovery")
    check("health recovery refuses a missing reason", p.returncode != 0 and
          "nonblank --reason" in p.stderr, p.stdout + p.stderr)

    from lib.registry import REGISTRY_COLUMNS as REGISTRY_COLS
    registry_fixture = tmp / "registry.json"
    row: dict[str, object] = {key: None for key in REGISTRY_COLS}
    row["Lead ID"] = "L-001"
    row["Contact Name"] = "Fixture Person"
    registry_fixture.write_text(json.dumps([row]))
    p = run(str(PY), "tools/registry-audit.py", "--fixture", str(registry_fixture),
            env=poisoned)
    check("normal registry audit uses canonical row shape", p.returncode == 0 and
          "canonical v_export_leads" in p.stdout, p.stdout + p.stderr)
    check("normal registry audit never resolves ambient Drive",
          "DO-NOT-READ" not in p.stdout + p.stderr, p.stdout + p.stderr)

    vault = tmp / "vault"
    leads = vault / "DNA/Leads"
    leads.mkdir(parents=True)
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Registry"
    ws.append(list(REGISTRY_COLS))
    ws.append([row.get(key) for key in REGISTRY_COLS])
    wb.create_sheet("Intake Log")
    wb.save(leads / "lead-registry.xlsx")
    p = run(str(PY), "tools/registry-audit.py", "--recovery", "--reason",
            "local projection drill", "--vault", str(vault))
    check("registry recovery is reasoned and labeled noncanonical", p.returncode == 0 and
          "NONCANONICAL Drive projection" in p.stderr and "local projection drill" in p.stderr,
          p.stdout + p.stderr)

    for script in ("tools/check.sh", "tools/smoke.sh"):
        p = run("zsh", script, "--recovery")
        check(f"{script} recovery refuses a missing reason", p.returncode == 2 and
              "nonblank --reason" in p.stderr, p.stdout + p.stderr)

    p = run(str(PY), "tools/report-card.py", "--validate", "--recovery",
            "--reason", "local projection drill", "--vault", str(vault))
    check("report card recovery is reasoned and labeled", p.returncode == 0 and
          "NONCANONICAL Drive projections" in p.stderr, p.stdout + p.stderr)

    cache = tmp / "report-cache"
    cache.mkdir()
    (cache / "health.txt").write_text(
        "Façade check - canonical receipts\n"
        "  CANONICAL_FINDING fixture - one\n"
        "Export register - canonical export_run receipts\n"
        "  OK 1 registered target(s), all receipted inside 26h\n"
        "  OK stale-sections       0 past review_after\n")
    (cache / "check.txt").write_text("== Code integrity ==\n")
    canonical_source = {"mode": "canonical", "contract": "canonical-record-control-v1",
                        "vault": None}
    (cache / "evidence-source.json").write_text(json.dumps(
        {"source": canonical_source, "captured_at": datetime.now().timestamp()}))
    p = run(str(PY), "tools/report-card.py", "--run", "--skip-evidence",
            "--evidence-dir", str(cache))
    check("normal report card executes versioned canonical metrics",
          p.returncode == 0 and "canonical_health_findings_v1" in p.stdout and
          "canonical_repo_worktree_drift_v1" in p.stdout, p.stdout + p.stderr)
    check("normal report card refuses Drive-era metric semantics",
          "facade_stale_rows" in p.stdout and
          "code_drift_rows" in p.stdout and
          "rules_live_agreement" in p.stdout and
          p.stdout.count("source_mode=recovery; mixed semantics refused") == 3,
          p.stdout + p.stderr)

    vault_a = tmp / "vault-a"
    vault_b = tmp / "vault-b"
    vault_a.mkdir(); vault_b.mkdir()
    recovery_source_a = {"mode": "recovery", "contract": "drive-projection-recovery-v1",
                         "vault": str(vault_a.resolve())}
    (cache / "evidence-source.json").write_text(json.dumps(
        {"source": recovery_source_a, "captured_at": datetime.now().timestamp()}))
    p = run(str(PY), "tools/report-card.py", "--run", "--skip-evidence",
            "--recovery", "--reason", "fixture", "--vault", str(vault_b),
            "--evidence-dir", str(cache))
    check("recovery cache refuses normalized vault A versus vault B",
          p.returncode == 1 and "does not exactly match required source" in p.stdout and
          str(vault_a.resolve()) in p.stdout and str(vault_b.resolve()) in p.stdout,
          p.stdout + p.stderr)

health_src = (ROOT / "tools/health-check.py").read_text()
registry_src = (ROOT / "tools/registry-audit.py").read_text()
run_src = (ROOT / "run.sh").read_text()
smoke_src = (ROOT / "tools/smoke.sh").read_text()
check("health emits the exact canonical export query",
      "from export_run group by target" in health_src)
check("health emits the exact Control Plane ledger query",
      "from ops.v_job_control" in health_src and "r.job_id=v.id and r.attempt=v.attempt" in health_src)
check("registry emits the v_export_leads read through record_sources",
      'load_leads("", MODE_RECORDS)' in registry_src)
check("normal run.sh wrappers do not inject CARR_VAULT into bounded readers",
      'health)       shift; "$PY" "$REPO/tools/health-check.py" "$@"' in run_src and
      'report-card)  shift; "$PY" "$REPO/tools/report-card.py" "$@"' in run_src and
      'registry_audit(){ shift; "$PY"' in run_src)
check("normal smoke calls canonical retrieval without a Drive environment",
      '"$PY" "$REPO/tools/retrieve.py" -n 1' in smoke_src and
      'CARR_VAULT="$VAULT"' not in smoke_src)

print(f"PASS: drive reader slice 2 ({passed} checks)")
