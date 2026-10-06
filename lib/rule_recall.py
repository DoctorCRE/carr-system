from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

SHORT_ID = re.compile(r"^[0-9a-f]{8}$")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def log_delivery(path, receipt_id, ids, *, observed_at=None):
    """Write only confirmed full-text ids; a missing meter never changes a gate."""
    import fcntl
    row = {"schema": "rule-recall-delivery-observation/v1", "receipt_id": receipt_id,
           "observed_at": observed_at or datetime.now(timezone.utc).isoformat(), "delivered": sorted(set(ids))}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def timestamp(value):
    try:
        if isinstance(value, (float, int)):
            return datetime.fromtimestamp(value, timezone.utc)
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def delivered_ids(row):
    schema = row.get("schema", "")
    if schema in {"rule-route-trigger-delivery/v1", "rule-jev-message-delivery/v3",
                  "rule-jit-trigger-delivery/v1", "rule-delivery-preuse-reselection/v1"}:
        return [r["id"] for r in row.get("rules", []) if isinstance(r, dict)
                and r.get("statement") and SHORT_ID.fullmatch(str(r.get("id", "")))]
    if row.get("schema") in {"rule-recall-boot-observation/v1", "rule-recall-delivery-observation/v1"}:
        return row.get("delivered", [])
    if isinstance(row.get("delivered"), list) and row.get("bind_status") is not None:
        return [r for r in row["delivered"] if isinstance(r, str) and SHORT_ID.fullmatch(r)]
    return []


def delivery_counts(rows, active_ids, now, days):
    now = timestamp(now)
    if now is None:
        raise ValueError("measurement time is required")
    start = now - timedelta(days=days)
    counts = Counter({rid: 0 for rid in active_ids})
    observed = 0
    seen = set()
    oldest = newest = None
    for row in rows:
        at = timestamp(row.get("observed_at", row.get("ts", row.get("at"))))
        if at is None or not start <= at <= now:
            continue
        ids = delivered_ids(row)
        if not ids:
            continue
        key = row.get("receipt_id") or digest(row)
        if key in seen:
            continue
        seen.add(key)
        observed += 1
        oldest = min(oldest, at) if oldest else at
        newest = max(newest, at) if newest else at
        counts.update(rid for rid in set(ids) if rid in counts)
    return {"counts": dict(counts), "readable": bool(observed), "receipts": observed,
            "start": start.isoformat(), "end": now.isoformat(),
            "oldest": oldest.isoformat() if oldest else None,
            "newest": newest.isoformat() if newest else None,
            "zero": sorted(rid for rid, n in counts.items() if n == 0)}
