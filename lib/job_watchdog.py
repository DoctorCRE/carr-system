"""Report failed local scheduled jobs and disk free below ten percent."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time

from local_jobs import append, command, locked, path_at, read_latest, stamp, tail

SOURCE = Path(__file__).resolve().parents[1]
KINDS = {"job_failed", "disk_low"}


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
        log_line = ""
        if code:
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
                    log_line = lines[-1]
                    break
        jobs.append({"label": label, "exit_code": code, "log_line": log_line})
    total, _, free = shutil.disk_usage(root)
    return {"jobs": jobs, "disk_free_percent": free / total * 100}


def detect(facts):
    found = []
    for job in facts["jobs"]:
        if job["exit_code"]:
            label, code = job["label"], job["exit_code"]
            found.append({"key": f"job_failed:{label}:exit_{code}", "kind": "job_failed",
                          "subject": label, "exit_code": code, "log_line": job["log_line"],
                          "reason": f"{label} exit {code}: {job['log_line'] or '(log unavailable)'}"})
    free = facts["disk_free_percent"]
    if free < 10:
        found.append({"key": "disk_low:local", "kind": "disk_low", "subject": "local disk",
                      "reason": f"disk free {free:.1f}% (below 10%)"})
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
        if (prior.get("kind") == "job_failed" and "exit_code" in prior
                and prior["subject"] not in observed_jobs and prior["subject"] in declared):
            continue
        if key not in current and not prior.get("cleared_at"):
            append(path, {**prior, "cleared_at": stamp(now)})


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
                found = detect(facts)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                append(ledger, {"key": "scan", "status": "failed", "at": stamp(), "error": str(exc)})
                print(f"Watchdog scan failed: {exc}", file=sys.stderr)
                return 1
            reconcile(root, config, found, {job["label"] for job in facts["jobs"]}, now)
            append(ledger, {"key": "scan", "status": "completed", "at": stamp(), "findings": len(found)})
            return 0
    except BlockingIOError:
        return 0
