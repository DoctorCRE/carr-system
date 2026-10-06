"""Read Jev call evidence and keep one outage loop tied to its recovery."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import record_call, repair_loop  # noqa: E402

THRESHOLD_HOURS = 2
REMEDIATION = {
    "billing_exhausted": "add TypeSafe credits",
    "auth_failed": "repair the TypeSafe API credential",
    "rate_limited": "restore TypeSafe quota or wait for its reset",
    "timeout": "repair TypeSafe call timeout",
    "network": "restore network reachability to TypeSafe",
    "server_5xx": "verify TypeSafe service recovery",
    "invalid_data": "repair the Jev response contract",
    "log_unreadable": "repair the Jev evidence logs",
    "state_unreadable": "repair the Jev outage state file",
    "unknown": "diagnose the failed TypeSafe call",
}
STATES = frozenset({"healthy", "failing_in_grace", "outage_open", "unknown"})
EVENTS = frozenset({"usable_success", "unusable_success", "classified_failure",
                    "log_missing", "log_corrupt", "log_truncated",
                    "legacy_incomplete", "state_missing", "state_corrupt"})


def action(reason):
    remedy = REMEDIATION.get(reason, REMEDIATION["unknown"])
    return ("on breach: open/update one deduplicated loop (owner Joe; "
            f"{remedy}); verify a usable Jev judgment; auto-clear on that judgment")


def parse_time(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except ValueError:
        return None


def _rows(path):
    with open(path, encoding="utf-8") as source:
        for line in source:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("non-object log row")
            yield row


def _log(path):
    try:
        return list(_rows(path)), None
    except FileNotFoundError:
        return [], "log_missing"
    except json.JSONDecodeError:
        return [], "log_truncated"
    except (OSError, UnicodeError, ValueError):
        return [], "log_corrupt"


def usable_receipt(row):
    """A receipt can prove recovery only after the client validated its answer."""
    usage = row.get("usage")
    status = row.get("http_status")
    return (row.get("ok") is True and row.get("usable") is True and
            row.get("schema_valid") is True and type(status) is int and 200 <= status < 300 and
            isinstance(row.get("model"), str) and bool(row["model"].strip()) and
            isinstance(usage, dict) and
            all(type(usage.get(key)) is int and usage[key] >= 0
                for key in ("input_tokens", "output_tokens")))


def clear(state, evidence):
    """The only transition into healthy; caller must prove usable_success."""
    if not isinstance(evidence, dict) or not usable_receipt(evidence):
        raise ValueError("Jev recovery requires a usable success receipt")
    at = parse_time(evidence.get("ts"))
    if at is None:
        raise ValueError("Jev recovery requires a timestamped receipt")
    last_attempt = parse_time(state.get("attempt_at"))
    if last_attempt and at <= last_attempt:
        raise ValueError("Jev recovery receipt predates the failed attempt")
    return {**state, "state": "healthy", "event_at": at.isoformat(),
            "last_success_at": at.isoformat(), "first_failure_at": None,
            "attempt_at": None, "reason": None,
            "recovered_by_usable_success": True}


def transition(state, event, *, at, now, threshold_hours=THRESHOLD_HOURS,
               legacy_mtime=None, reason=None, evidence=None):
    """One state transition and its health verdict for every monitored input."""
    if event not in EVENTS:
        raise ValueError(f"unknown Jev outage event: {event}")
    result = dict(state)
    before = result.get("state", "unknown")
    if before not in STATES:
        before = "unknown"
    result["state"] = before
    anchor = parse_time(result.get("first_failure_at"))
    if event == "usable_success":
        result = clear(result, evidence)
    elif event in ("classified_failure", "unusable_success"):
        if before not in ("failing_in_grace", "outage_open"):
            anchor = at
        elif anchor is None:
            anchor = legacy_mtime or at
        result.update(first_failure_at=anchor.isoformat(), attempt_at=at.isoformat(),
                      event_at=at.isoformat(), reason=reason or
                      ("invalid_data" if event == "unusable_success" else "unknown"))
        expired = (now - anchor).total_seconds() >= threshold_hours * 3600
        result["state"] = ("outage_open" if before in ("outage_open", "unknown") or
                           event == "unusable_success" or expired else "failing_in_grace")
    elif event in ("legacy_incomplete", "state_corrupt"):
        anchor = anchor or legacy_mtime or at
        result.update(state="outage_open", first_failure_at=anchor.isoformat(),
                      attempt_at=result.get("attempt_at") or anchor.isoformat(),
                      reason="state_unreadable" if event == "state_corrupt" else
                      result.get("reason") or "log_unreadable")
    elif event in ("log_missing", "log_corrupt", "log_truncated"):
        if before == "failing_in_grace":
            result["state"] = "outage_open"
        elif before == "healthy":
            result["state"] = "unknown"
        result["reason"] = "log_unreadable"
    elif event == "state_missing":
        anchor = anchor or legacy_mtime or at
        result.update(state="outage_open", first_failure_at=anchor.isoformat(),
                      event_at=at.isoformat(), reason="state_unreadable")
    current = result["state"]
    result["status"] = ("ok" if current == "healthy" else "warn" if
                        current == "outage_open" or current == "unknown" and
                        result.get("reason") in ("log_unreadable", "state_unreadable")
                        else "skip")
    result["pending"] = current in ("failing_in_grace", "outage_open")
    return result


def _safe_reason(error):
    advisory = re.fullmatch(r"build_advisory:([a-z_]+)", error or "")
    if advisory and advisory.group(1) in (
            "billing_exhausted", "auth_failed", "rate_limited", "timeout",
            "network", "server_5xx", "unknown"):
        return advisory.group(1)
    match = re.search(r"\bTypeSafe returned HTTP (\d{3})\b", error or "")
    status = int(match.group(1)) if match else None
    if status == 402:
        return "billing_exhausted"
    if status in (401, 403):
        return "auth_failed"
    if status == 429:
        return "rate_limited"
    if status is not None and 500 <= status <= 599:
        return "server_5xx"
    return "unknown"


def evaluate(judge_path, calls_path, *, now=None, threshold_hours=THRESHOLD_HOURS,
             state_path=None):
    """Replay valid evidence through the same reducer used by the transition test."""
    now = now or datetime.now(timezone.utc)
    mtime = None
    try:
        state = json.loads(Path(state_path).read_text(encoding="utf-8")) if state_path else {}
        if state_path:
            mtime = datetime.fromtimestamp(Path(state_path).stat().st_mtime, timezone.utc)
        if not isinstance(state, dict):
            raise ValueError("non-object state")
    except FileNotFoundError:
        state = transition({}, "state_missing", at=now, now=now)
    except (OSError, UnicodeError, ValueError):
        if state_path and Path(state_path).exists():
            mtime = datetime.fromtimestamp(Path(state_path).stat().st_mtime, timezone.utc)
        state = transition({}, "state_corrupt", at=now, now=now, legacy_mtime=mtime)
    if "state" not in state and (state.get("loop_id") or state.get("open_key")):
        state = transition(state, "legacy_incomplete", at=now, now=now, legacy_mtime=mtime)
    elif "state" not in state:
        if state_path:
            state = transition(state, "legacy_incomplete", at=now, now=now,
                               legacy_mtime=mtime)
        else:
            state["state"] = "failing_in_grace" if parse_time(state.get("first_failure_at")) else "unknown"
    if state["state"] not in STATES:
        state = transition(state, "legacy_incomplete", at=now, now=now, legacy_mtime=mtime)
    if ((state["state"] in ("failing_in_grace", "outage_open") and
         not parse_time(state.get("first_failure_at"))) or
            (state.get("loop_id") and state["state"] == "healthy" and
             state.get("recovered_by_usable_success") is not True)):
        state = transition(state, "legacy_incomplete", at=now, now=now, legacy_mtime=mtime)

    calls, call_loss = _log(calls_path)
    judges, judge_loss = _log(judge_path)
    recorded_attempt = parse_time(state.get("attempt_at"))
    latest_logged_attempt = max((at for row in judges
                                 if isinstance(row.get("error"), str)
                                 if (at := parse_time(row.get("at")))), default=None)
    if (recorded_attempt and
            (latest_logged_attempt is None or latest_logged_attempt < recorded_attempt)):
        judge_loss = judge_loss or "log_truncated"
    lost_evidence = (judge_loss if state.get("attempt_at") else None) or (
        call_loss if state.get("last_success_at") else None)
    if lost_evidence:
        state = transition(state, lost_evidence, at=now, now=now,
                           threshold_hours=threshold_hours)
    events = []
    for row in calls:
        at = parse_time(row.get("ts"))
        if at and at <= now:
            if row.get("cache_hit") is True:
                continue
            # An old success claim without validation is uncertain. Provider
            # failures have no HTTP 2xx response; the judgment log classifies them.
            if "schema_valid" not in row:
                if row.get("ok") is True:
                    events.append((at, "legacy_incomplete", "log_unreadable", None))
                continue
            status = row.get("http_status")
            if type(status) is not int or not 200 <= status < 300:
                continue
            events.append((at, "usable_success" if usable_receipt(row) else "unusable_success",
                           "invalid_data", row))
    for row in judges:
        at = parse_time(row.get("at"))
        if at and at <= now and isinstance(row.get("error"), str):
            events.append((at, "classified_failure", _safe_reason(row["error"]), None))
    boundary = parse_time(state.get("event_at")) or parse_time(state.get("attempt_at"))
    for at, event, reason, evidence in sorted(events, key=lambda item: (item[0], item[1])):
        if boundary and at <= boundary:
            continue
        state = transition(state, event, at=at, now=now,
                           threshold_hours=threshold_hours, legacy_mtime=mtime,
                           reason=reason, evidence=evidence)
    if state["state"] == "failing_in_grace":
        anchor = parse_time(state.get("first_failure_at"))
        if anchor and (now - anchor).total_seconds() >= threshold_hours * 3600:
            state = transition(state, "classified_failure",
                               at=parse_time(state.get("attempt_at")) or anchor,
                               now=now, threshold_hours=threshold_hours,
                               reason=state.get("reason"))
    if "status" not in state:
        state["status"] = ("ok" if state["state"] == "healthy" else
                           "warn" if state["state"] == "outage_open" else "skip")
        state["pending"] = state["state"] in ("failing_in_grace", "outage_open")
    success = parse_time(state.get("last_success_at"))
    state["age_hours"] = round(max((now - success).total_seconds() / 3600, 0), 1) if success else None
    if state["state"] == "healthy" and state["age_hours"] is not None and state["age_hours"] > threshold_hours and not state.get("loop_id"):
        state["status"] = "skip"
    if state["status"] == "skip":
        state["reason"] = None if not state["pending"] else state.get("reason")
    return state


def _body(reason):
    if reason in ("log_unreadable", "state_unreadable"):
        return ("Jev outage remains open: judgment log or call receipt log is "
                "missing, truncated, or unreadable, or the outage state is unreadable. "
                "Repair the affected evidence and state file, "
                "then verify with a usable Jev judgment. This loop auto-closes "
                "only after that judgment.")
    cause = ("TypeSafe account has no API credits" if reason == "billing_exhausted"
             else f"TypeSafe call failed ({reason})")
    return (f"Jev is offline: {cause}. Joe owns remediation: add TypeSafe credits "
            "when billing is exhausted; otherwise repair the named provider failure. "
            "Verify with a usable Jev judgment. This loop auto-closes on that judgment.")


def reconcile(result, state_path, verb):
    """Idempotently open/update the one outage loop, then close on success."""
    try:
        state = repair_loop.read_state(state_path)
        previous = dict(state)
        state.update(result)
        first = parse_time(result.get("first_failure_at"))
        if first:
            state["first_failure_at"] = first.isoformat()
        def save():
            repair_loop.save_state(state_path, state)
        if result["status"] == "skip" and result["pending"]:
            save()
            return "none"
        if result["status"] == "warn":
            reason = result["reason"]
            payload = {"kind": "open_loop", "owner": "Joe", "domain": "system",
                       "blocker": "human_only" if reason == "billing_exhausted" else "capability",
                       "blocker_detail": ("Joe must add TypeSafe account credits" if reason == "billing_exhausted"
                                          else f"TypeSafe provider failure: {reason}"), "body": _body(reason)}
            outcome = repair_loop.reconcile(state, payload, verb, save)
            if state.get("loop_id"):
                state["state"] = "outage_open"
            save()
            return outcome
        if (result["status"] == "ok" and result.get("state") == "healthy" and
                result.get("recovered_by_usable_success") is True):
            outcome = repair_loop.reconcile(state, None, verb, save, outcome=
                "A new usable Jev judgment verified that the TypeSafe outage cleared.")
            save()
            return "cleared" if outcome == "cleared" or previous.get("first_failure_at") else "none"
        return "none"
    except (OSError, ValueError, RuntimeError):
        return "error"


def call_verb(name, payload):
    """A loop verb's reply, or {"ok": False} for anything but an ok outcome."""
    result = record_call.call_verb(name, payload, timeout=35)
    return result.reply if result.ok and isinstance(result.reply, dict) else {"ok": False}
