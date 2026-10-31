"""Report failed local scheduled jobs and disk free below ten percent."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from email_outcomes import business_day_deadline
from outlook_settings import load_settings

from local_jobs import append, command, locked, path_at, read_latest, stamp, tail

SOURCE = Path(__file__).resolve().parents[1]
KINDS = {"job_failed", "job_degraded", "disk_low"}
RECEIPT = re.compile(r"^([a-z][a-z-]*-receipt) (\{.*\})$")
SOCIAL_BATCH_REPORT_KEY = "job_failed:social-batch-weekly:batch-output"
SOCIAL_BATCH_FAILURES = {"no_output", "zero_drafts", "zero_duration"}


def load_config(path):
    config = json.loads(Path(path).read_text())
    if config["schema_version"] != 1:
        raise ValueError("unsupported watchdog config")
    return config


def collect(root, config):
    statuses = {}
    for line in command(["launchctl", "list"], config).splitlines()[1:]:
        _, status, label = line.split(maxsplit=2)
        statuses[label] = int(status)
    jobs = []
    for path in sorted((SOURCE / "ops/launchd").glob("*.plist")):
        definition = plistlib.loads(path.read_bytes())
        if not any(k in definition for k in ("StartCalendarInterval", "StartInterval")):
            continue
        label = definition["Label"]
        if label not in statuses:
            continue
        code = statuses[label]
        log_line, degraded = "", None
        for key in ("StandardErrorPath", "StandardOutPath"):
            configured = definition.get(key)
            if not configured:
                continue
            log = Path(configured.replace("{{REPO}}", str(root)).replace("{{HOME}}", str(Path.home()))).expanduser()
            try:
                lines = [line.strip() for line in tail(log, config["thresholds"]["log_tail_bytes"]).splitlines() if line.strip()]
            except OSError:
                continue
            if lines:
                log_line = lines[-1] if code else ""
                degraded = degraded_receipt(lines)
                break
        jobs.append({"label": label, "exit_code": code, "log_line": log_line, "degraded": degraded})
    total, _, free = shutil.disk_usage(root)
    facts = {"jobs": jobs, "disk_free_percent": free / total * 100}
    if "com.carr.outlook-intake" in statuses:
        facts["outlook"] = collect_outlook(config)
    return facts


def collect_outlook(config):
    settings = load_settings()
    state = json.loads((settings.repo / "out/outlook-intake/state.json").read_text())
    try:
        result = subprocess.run(
            [str(settings.repo / "run.sh"), "call", "email-router-queue", "-"],
            input=json.dumps({"purpose": "outcomes", "account": settings.account}),
            cwd=settings.repo, env=settings.transport_environment(), capture_output=True,
            text=True, timeout=config["thresholds"]["command_timeout_seconds"])
        if result.returncode:
            raise ValueError("outlook outcome read failed")
        reply = json.loads(result.stdout)
        if reply.get("schema_version") != "email-outcomes.v1":
            raise ValueError("outlook outcome contract unavailable")
        for field in ("journey_live_at", "last_email_in_filed_at"):
            if reply[field] is not None:
                datetime.fromisoformat(reply[field].replace("Z", "+00:00"))
        if type(reply["reply_draft_count"]) is not int or reply["reply_draft_count"] < 0:
            raise ValueError("outlook outcome count invalid")
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError("outlook outcome read invalid or unavailable") from exc
    return {**{field: reply[field] for field in
               ("journey_live_at", "last_email_in_filed_at", "reply_draft_count")},
            "timezone": str(settings.timezone),
            "filing_observed_at": state.get("filing_observed_at"),
            "coverage_failed": state.get("coverage_failed", False)}


def degraded_receipt(lines):
    """The latest receipt of each kind; a degraded one names its reason and rates.

    A degraded job keeps exiting 0 after its first alarm, so its exit status
    alone would clear the finding while the degradation continues.
    """
    latest = {}
    reconcile_failures = 0
    coverage_failure = None
    for line in lines:
        match = RECEIPT.match(line)
        if match:
            try:
                receipt = json.loads(match.group(2))
                latest[match.group(1)] = receipt
                if match.group(1) == "outlook-intake-receipt":
                    if isinstance(receipt, dict) and receipt.get("state") == "failed" and receipt.get("reason") == "reconcile_failed":
                        reconcile_failures += 1
                    else:
                        reconcile_failures = 0
                if match.group(1) == "outlook-intake-receipt" and isinstance(receipt, dict):
                    counts = receipt.get("counts", {})
                    run = counts.get("run", counts) if isinstance(counts, dict) else {}
                    if receipt.get("reason") == "coverage_failed":
                        reasons = []
                        if run.get("coverage_missed_verdict"):
                            reasons.append("previous day's verdict was missed")
                        if run.get("coverage_held_recipients_over_limit"):
                            reasons.append("more than 20 Sent items have unknown recipients")
                        if not reasons:
                            reasons.append("mail coverage fell short")
                        coverage_failure = "; ".join(reasons)
                    elif isinstance(run, dict) and run.get("coverage_checked") == 1:
                        coverage_failure = None
            except ValueError:
                continue
    if reconcile_failures >= 3:
        return "reconciliation failed three consecutive times; mail is not being filed"
    if coverage_failure:
        return f"(coverage_failed): {coverage_failure}"
    for receipt in latest.values():
        if isinstance(receipt, dict) and receipt.get("state") == "degraded":
            counts = receipt.get("counts") if isinstance(receipt.get("counts"), dict) else {}
            if isinstance(counts.get("run"), dict):
                counts = counts["run"]
            rates = ", ".join(f"{k[:-3].removeprefix('decoder_')} {v / 100:.1f}%" for k, v in counts.items()
                              if k.endswith("_bp") and type(v) is int)
            return f"({receipt.get('reason')}): {rates or 'no rates reported'}"
    return None


def outlook_findings(facts, now):
    def instant(value):
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, timezone.utc)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    last = facts.get("last_email_in_filed_at") or facts.get("filing_observed_at")
    overdue = last is not None and now > business_day_deadline(instant(last), ZoneInfo(facts["timezone"]))
    alarms = {
        "no_email_in": (overdue, "no email_in filed for more than one business day"),
        "no_reply_draft": ((facts.get("journey_live_at") or facts.get("filing_observed_at")) is not None and facts["reply_draft_count"] == 0,
                           "zero reply_draft rows since the email journey went live"),
        "coverage_failed": (facts.get("coverage_failed") is True, "daily coverage check failed"),
    }
    return [{"key": f"job_degraded:outlook:{code}", "kind": "job_degraded",
             "subject": "com.carr.outlook-intake", "source": "outlook-outcomes",
             "reason": f"Outlook intake: {reason}"}
            for code, (alarm, reason) in alarms.items() if alarm]


def detect(facts, now=None):
    found = []
    for job in facts["jobs"]:
        if job.get("degraded"):
            label = job["label"]
            found.append({"key": f"job_degraded:{label}", "kind": "job_degraded", "subject": label,
                          "reason": f"{label} degraded {job['degraded']}"})
        if job["exit_code"]:
            label, code = job["label"], job["exit_code"]
            found.append({"key": f"job_failed:{label}:exit_{code}", "kind": "job_failed",
                          "subject": label, "exit_code": code, "log_line": job["log_line"],
                          "reason": f"{label} exit {code}: {job['log_line'] or '(log unavailable)'}"})
    free = facts["disk_free_percent"]
    if free < 10:
        found.append({"key": "disk_low:local", "kind": "disk_low", "subject": "local disk",
                      "reason": f"disk free {free:.1f}% (below 10%)"})
    if facts.get("outlook") is not None:
        found.extend(outlook_findings(facts["outlook"],
                     datetime.fromtimestamp(time.time() if now is None else now, timezone.utc)))
    return found


def reconcile(root, config, found, observed_jobs, now=None):
    path = path_at(root, config["paths"]["findings"])
    previous = read_latest(path)
    current = {f["key"]: f for f in found}
    for key, finding in current.items():
        prior = previous.get(key, {})
        if prior.get("cleared_at") or any(prior.get(k) != v for k, v in finding.items()):
            append(path, {**finding, "first_seen": stamp(now) if prior.get("cleared_at") else prior.get("first_seen", stamp(now)),
                          "cleared_at": None})
    # An unloaded job keeps its last failure; a job whose definition is gone is retired.
    declared = {plistlib.loads(p.read_bytes()).get("Label") for p in (SOURCE / "ops/launchd").glob("*.plist")}
    for key, prior in previous.items():
        if prior.get("source") == "social-batch-report":
            continue
        if prior.get("source") == "outlook-outcomes" and prior["subject"] not in observed_jobs:
            continue
        if (prior.get("kind") == "job_failed" and "exit_code" in prior
                and prior["subject"] not in observed_jobs and prior["subject"] in declared):
            continue
        if key not in current and not prior.get("cleared_at"):
            append(path, {**prior, "cleared_at": stamp(now)})


def report_social_batch(root, config, *, week_start, queue_count=None,
                        queue_complete=False, failure=None, now=None):
    try:
        week = date.fromisoformat(week_start)
    except (TypeError, ValueError) as exc:
        raise ValueError("week_start must be an ISO Monday date") from exc
    if week.isoformat() != week_start or week.weekday() != 0:
        raise ValueError("week_start must be an ISO Monday date")

    if failure is None:
        if not queue_complete or type(queue_count) is not int or queue_count < 0:
            raise ValueError("a complete queue observation with a nonnegative count is required")
        state = "failed" if queue_count == 0 else "succeeded"
        failure_class = "zero_drafts" if queue_count == 0 else None
    else:
        if failure not in {"no_output", "zero_duration"} or queue_count is not None or queue_complete:
            raise ValueError("failure must be no_output or zero_duration without queue counts")
        state = "failed"
        failure_class = failure

    path = path_at(root, config["paths"]["findings"])
    previous = read_latest(path).get(SOCIAL_BATCH_REPORT_KEY, {})
    previous_week = previous.get("week_start")
    if previous_week and previous_week > week_start:
        return {"state": state, "week_start": week_start, "queue_count": queue_count,
                "queue_complete": queue_complete, "failure_class": failure_class}

    if state == "succeeded":
        if previous and not previous.get("cleared_at"):
            append(path, {**previous, "cleared_at": stamp(now)})
    else:
        reason = {
            "no_output": "the run produced no batch output or complete-week queue result",
            "zero_drafts": f"zero drafts were scheduled for the following week starting {week_start}",
            "zero_duration": "the scheduled run completed with zero duration",
        }[failure_class]
        finding = {
            "key": SOCIAL_BATCH_REPORT_KEY,
            "kind": "job_failed",
            "subject": "social-batch-weekly",
            "exit_code": 1,
            "failure_class": failure_class,
            "source": "social-batch-report",
            "week_start": week_start,
            "queue_count": queue_count,
            "reason": f"social-batch-weekly failed: {reason}",
            "first_seen": previous.get("first_seen") if previous_week == week_start and previous.get("first_seen") and not previous.get("cleared_at") else stamp(now),
            "cleared_at": None,
        }
        if any(previous.get(key) != value for key, value in finding.items()):
            append(path, finding)

    return {"state": state, "week_start": week_start, "queue_count": queue_count,
            "queue_complete": queue_complete, "failure_class": failure_class}


def digest(root, config, *, session_id=None):
    active = sorted((f for f in read_latest(path_at(root, config["paths"]["findings"])).values()
                     if f.get("kind") in KINDS and (f["kind"] != "job_failed" or "exit_code" in f)
                     and not f.get("cleared_at")), key=lambda f: f["key"])
    view = [{k: f.get(k) for k in ("key", "reason")} for f in active]
    if session_id:
        signature = hashlib.sha256(json.dumps(view, sort_keys=True).encode()).hexdigest()
        session = hashlib.sha256(session_id.encode()).hexdigest()
        path = root / "out/watchdog/banner-sessions" / (session + ".digest")
        with locked(path.with_suffix(".lock")):
            if path.exists() and path.read_text() == signature:
                return ""
            path.write_text(signature)
    if not active:
        return ""
    lines = [f"Orchestrator watchdog: {len(active)} open finding(s)."]
    limit = 4 if len(active) <= 4 else 3
    for finding in active[:limit]:
        lines.append("- " + " ".join(finding["reason"].split())[:300])
    if len(active) > limit:
        lines.append(f"- {len(active) - limit} more; see {config['paths']['findings']}")
    return "\n".join(lines)


def scan(root, config):
    ledger = path_at(root, config["paths"]["scan_ledger"])
    try:
        with locked(path_at(root, config["paths"]["scan_lock"]), blocking=False):
            now = time.time()
            try:
                facts = collect(root, config)
                found = detect(facts, now)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                append(ledger, {"key": "scan", "status": "failed", "at": stamp(), "error": str(exc)})
                print(f"Watchdog scan failed: {exc}", file=sys.stderr)
                return 1
            observed = {job["label"] for job in facts["jobs"]}
            if facts.get("outlook") is not None:
                observed.add("com.carr.outlook-intake")
            reconcile(root, config, found, observed, now)
            append(ledger, {"key": "scan", "status": "completed", "at": stamp(), "findings": len(found)})
            return 0
    except BlockingIOError:
        return 0
