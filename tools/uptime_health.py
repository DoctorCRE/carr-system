"""Read the independent Cron monitor. Production incidents reconcile through its durable ledger."""
import json
import os
import fcntl
import subprocess
import sys
import uuid
from http.client import HTTPException
from pathlib import Path
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.request import urlopen
from jev_outage_health import call_verb

MONITOR_SOURCE = "uptime-monitor:availability:v1"

STATUS_URL = "https://uptime.doctorcre.com/healthz"
ACTION = (
    "on breach: carr-uptime replays one production incident loop; this reader opens one monitor-availability loop if monitoring fails; "
    "owner orchestrator; fix: restore failed routes or configure/restart carr-uptime and its secrets; "
    "verify: three JSON probes, Healthchecks down/up delivery, and incident loop reconciliation; "
    "auto-clear: fresh passing monitor, no pending alerts or incident loops"
)


def reconcile_monitor(fault, healthy, state_path=None, verb=None):
    """Keep monitor failure separate from the Worker's one loop per production incident."""
    repo = Path(__file__).resolve().parents[1]
    path = Path(state_path or os.environ.get("CARR_UPTIME_RESPONSE_STATE", repo / "out/uptime-monitor-response.json"))
    verb = verb or call_verb
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state = json.loads(path.read_text()) if path.exists() else {}
            if not isinstance(state, dict):
                return "error"

            def save():
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps(state))
                temporary.replace(path)

            if fault and not state:
                state = {"key": str(uuid.uuid4())}
                save()
            if not state:
                return "none"
            if not isinstance(state.get("key"), str) or not state["key"]:
                return "error"
            if state["key"].startswith("uptime-monitor:"):
                state["key"] = str(uuid.uuid4())
                save()
            while True:
                if not state.get("loop_id"):
                    response = verb("add-loop", {
                        "idempotency_key": state["key"], "kind": "open_loop", "owner": "claude",
                        "domain": "system", "source_note": MONITOR_SOURCE, "body": f"DoctorCRE uptime monitoring unavailable. {ACTION}", "marker": "none",
                        "blocker": "other_lane", "blocker_detail": "The orchestrator must restore carr-uptime scheduling, route, credentials, or notification delivery; this health reader has no deployment authority",
                    })
                    if response.get("ok") is not True or not isinstance(response.get("loop_id"), str):
                        return "error"
                    state["loop_id"] = response["loop_id"]
                    save()
                    if not healthy and not response.get("replayed") and not response.get("deduplicated"):
                        return "open"
                loop = verb("read-loop", {"loop_id": state["loop_id"]})
                if loop.get("loop_id") != state["loop_id"] or type(loop.get("version")) is not int:
                    return "error"
                if loop.get("status") in ("done", "dropped"):
                    if fault:
                        state = {"key": str(uuid.uuid4())}
                        save()
                        continue
                    return "clear"
                if loop.get("status") != "open":
                    return "error"
                if healthy:
                    if not state.get("close_key"):
                        state["close_key"] = str(uuid.uuid4())
                        save()
                    response = verb("close-loop", {
                        "idempotency_key": state["close_key"], "loop_id": state["loop_id"],
                        "base_version": loop["version"], "resolution": "done",
                        "outcome": "Uptime monitor recovered. Fresh status proves three passing JSON probes and no pending alerts or incident loops.",
                    })
                    if response.get("ok") is not True:
                        return "error"
                    return "clear"
                return "open"
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        return "error"


def _read_status(url):
    try:
        response = urlopen(url, timeout=10)
    except HTTPError as error:
        if error.code != 503:
            raise
        response = error
    with response:
        body = response.read(65537)
        if len(body) > 65536:
            raise ValueError("oversized status")
        if response.headers.get("Content-Length") is not None and len(body) != int(response.headers["Content-Length"]):
            raise ValueError("partial status")
        return json.loads(body)


def read_status(timeout=10):
    """Kill a stalled fetch across headers and streaming body within one deadline."""
    url = os.environ.get("CARR_UPTIME_STATUS_URL", STATUS_URL)
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), url],
                            capture_output=True, timeout=timeout, check=True)
    return json.loads(result.stdout)


def row(read=read_status, now=None, respond=reconcile_monitor):
    now = now or datetime.now(timezone.utc)
    try:
        status = read()
        if not isinstance(status, dict) or status.get("schema") != "carr-uptime.v1":
            raise ValueError("shape")
        counts = [status.get(key) for key in ("failures", "pending_records", "pending_alerts")]
        if any(type(value) is not int or value < 0 for value in counts):
            raise ValueError("shape")
        checks = status.get("checks")
        if not isinstance(checks, list) or len(checks) != 3 or {
            check.get("name") for check in checks if isinstance(check, dict)
        } != {"api-release", "app-release", "verb-round-trip"}:
            raise ValueError("shape")
        timestamp = status.get("checked_at")
        if not isinstance(timestamp, str):
            raise ValueError("timestamp")
        checked = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if checked.tzinfo is None:
            raise ValueError("timestamp timezone")
        age = (now - checked).total_seconds()
        passing = all(check.get("ok") is True for check in checks)
        healthy = (status.get("ok") is True and passing and -30 <= age < 180
                   and counts == [0, 0, 0] and not status.get("active_incident")
                   and status.get("configuration_missing") == []
                   and status.get("alert_error") is None and status.get("record_error") is None)
        monitor_fault = (not -30 <= age < 180 or bool(status.get("configuration_missing"))
                         or bool(status.get("alert_error")) or bool(status.get("record_error"))
                         or passing and (counts[1] > 0 or counts[2] > 0))
        detail = (f"last sample {max(0, int(age))}s ago; {counts[0]}/3 failures; "
                  f"{counts[1]} loops pending; {counts[2]} alerts pending")
    except (OSError, ValueError, TypeError, KeyError, HTTPException, subprocess.SubprocessError):
        healthy = False
        monitor_fault = True
        detail = "monitor unreachable or response invalid; activation and phone delivery unverified"
    response = respond(monitor_fault, healthy)
    if response == "error":
        healthy = False
        detail += "; monitor response loop pending (record or local state unavailable)"
    return f"{'OK' if healthy else 'WARN'} production uptime · {detail} · {ACTION}", not healthy


if __name__ == "__main__":
    print(json.dumps(_read_status(sys.argv[1])))
