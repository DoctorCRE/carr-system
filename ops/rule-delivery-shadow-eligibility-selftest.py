#!/usr/bin/env python3
"""Acceptance cases for the append-only shadow epoch and finding ledger."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "eligibility", REPO / "ops" / "rule-delivery-shadow-eligibility.py")
assert spec and spec.loader
eligibility = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eligibility)

NOW = datetime(2026, 8, 25, 22, 0, tzinfo=timezone.utc)
IDENTITY = {"policy_digest": "1" * 64, "map_digest": "2" * 64,
            "source_digest": "3" * 64}
OTHER_SOURCE = {**IDENTITY, "source_digest": "4" * 64}
FAILURES: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    if not condition:
        FAILURES.append(f"{label}: {detail}")


def ts(hours_ago: int) -> datetime:
    return NOW - timedelta(hours=hours_ago)


def row(hours: int, **changes) -> dict:
    error = changes.pop("error", None)
    if error is not None:
        return eligibility.make_error_observation(
            session=f"s-{hours}", error=error, detail="fixture error",
            map_digest=IDENTITY["map_digest"],
            source_digest=IDENTITY["source_digest"], at=ts(hours))
    result = {"mode": "shadow", "needed": ["engineering-git"],
              "loaded": ["engineering-git"], "missing": [], "triggers": {},
              "would_omit_count": 150, "missed_rules": []}
    result.update(changes)
    return eligibility.make_observation(
        session=f"s-{hours}", map_digest=IDENTITY["map_digest"],
        source_digest=IDENTITY["source_digest"], result=result, at=ts(hours))


def epoch(hours: int, identity: dict = IDENTITY, **changes) -> dict:
    value = eligibility.make_epoch(
        identity, owner="rule-delivery", reason="post-remedy clean window",
        remedy_ref="WR-000007", rollback_ref="retain prior epoch",
        at=ts(hours))
    value.update(changes)
    return value


def disposition(event: dict, hours: int, kind: str = "explained", **changes) -> dict:
    value = eligibility.make_disposition(
        eligibility.observation_id(event), kind, owner="rule-delivery",
        remedy_ref="WR-000007", evidence_ref="INC-1",
        rollback_ref="retain raw observation", at=ts(hours))
    value.update(changes)
    return value


def evaluate(rows: list[dict], identity: dict | None = IDENTITY) -> dict:
    return eligibility.evaluate(rows, NOW, identity)


legacy = evaluate([row(170), row(0)])
check("legacy log requires an explicit epoch", not legacy["eligible"], legacy)
check("legacy refusal names the missing epoch",
      "no valid shadow epoch" in legacy["reasons"], legacy)

explained_miss = row(120, missed_rules=["deadbeef"])
explained_rows = [epoch(170), row(169), row(144), explained_miss,
                  disposition(explained_miss, 119), row(96), row(72), row(48),
                  row(24), row(0)]
explained = evaluate(explained_rows)
check("explained miss permits a complete clean-equivalent window", explained["eligible"], explained)
check("closed finding is reported", explained["closed_findings"] == 1, explained)
check("closed finding carries owner and remedy",
      explained["closed"][0]["owner"] == "rule-delivery"
      and explained["closed"][0]["remedy_ref"] == "WR-000007", explained)

explained_error = row(120, error="KeyError", loaded=[], would_omit_count=0)
error_rows = [epoch(170), row(169), row(144), explained_error,
              disposition(explained_error, 119), row(96), row(72), row(48),
              row(24), row(0)]
error_result = evaluate(error_rows)
check("explained gate error is closed without deleting its raw event",
      error_result["eligible"] and error_result["gate_errors"] == 1
      and error_result["closed"][0]["kind"] == "error", error_result)

fixed_miss = row(120, missed_rules=["cafebabe"])
fixed_disp = disposition(fixed_miss, 119, "remediated")
fixed = evaluate([epoch(170), row(169), fixed_miss, fixed_disp, row(0)])
check("remediated finding requires a post-remedy epoch", not fixed["eligible"], fixed)
check("restart reason is explicit", any("new epoch" in r for r in fixed["reasons"]), fixed)
older_fixed = row(240, missed_rules=["cafebabe"])
older_fixed_disp = disposition(older_fixed, 239, "remediated")
restarted = evaluate([epoch(340), row(300), older_fixed, older_fixed_disp,
                      epoch(170), row(169), row(144), row(120), row(96), row(72),
                      row(48), row(24), row(0)])
check("fresh seven days after remedy can qualify", restarted["eligible"], restarted)

allowed, reason = eligibility.can_start_epoch([epoch(24), row(23)], IDENTITY)
check("rolling reset is refused", not allowed and "no remediated finding" in reason,
      reason)
allowed, reason = eligibility.can_start_epoch(
    [epoch(170), row(169), fixed_miss, fixed_disp], IDENTITY)
check("restart after an actual remedy is allowed", allowed, reason)

orphan = eligibility.make_disposition(
    "a" * 64, "explained", owner="x", remedy_ref="r", evidence_ref="e",
    rollback_ref="b", at=ts(1))
bad = evaluate([epoch(170), row(169), orphan, row(0)])
check("orphan disposition fails closed", not bad["eligible"]
      and any("orphan disposition" in r for r in bad["reasons"]), bad)
event = row(120, missed_rules=["deadbeef"])
dup = disposition(event, 119)
bad = evaluate([epoch(170), row(169), event, dup, dup, row(0)])
check("duplicate disposition fails closed", not bad["eligible"]
      and any("duplicate disposition" in r for r in bad["reasons"]), bad)
malformed = {**dup, "surprise": True}
bad = evaluate([epoch(170), row(169), event, malformed, row(0)])
check("extra disposition key fails closed", not bad["eligible"]
      and any("malformed" in r for r in bad["reasons"]), bad)

legacy_after_epoch = {"ts": ts(120).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "mode": "shadow", "loaded": ["engineering-git"],
                      "would_omit_count": 1, "missed_rules": []}
bad = evaluate([epoch(170), row(169), legacy_after_epoch, row(0)])
check("legacy observation after epoch fails closed", not bad["eligible"]
      and any("legacy observation" in r for r in bad["reasons"]), bad)
extra_observation = {**row(120), "surprise": True}
bad = evaluate([epoch(170), row(169), extra_observation, row(0)])
check("post-epoch observation extra key fails closed", not bad["eligible"]
      and any("malformed observation" in r for r in bad["reasons"]), bad)
coerced = {**row(120), "would_omit_count": "150"}
coerced["event_id"] = eligibility.observation_id(coerced)
bad = evaluate([epoch(170), row(169), coerced, row(0)])
check("post-epoch count coercion fails closed", not bad["eligible"]
      and any("malformed observation" in r for r in bad["reasons"]), bad)
future_observation = row(-1)
bad = evaluate([epoch(170), row(169), future_observation])
check("future observation gives no elapsed-time credit", not bad["eligible"]
      and any("future ledger timestamp" in r for r in bad["reasons"]), bad)
future_epoch = epoch(-1)
bad = evaluate([future_epoch])
check("future epoch fails closed", not bad["eligible"]
      and any("future ledger timestamp" in r for r in bad["reasons"]), bad)
past_finding = row(120, missed_rules=["deadbeef"])
future_disposition = disposition(past_finding, -1)
bad = evaluate([epoch(170), row(169), past_finding, future_disposition, row(0)])
check("future disposition fails closed", not bad["eligible"]
      and any("future ledger timestamp" in r for r in bad["reasons"]), bad)

missing_identity = evaluate([epoch(170), row(169), row(0)], None)
check("absent current identity fails closed", not missing_identity["eligible"], missing_identity)
mismatch = evaluate([epoch(170), row(169), row(0)], OTHER_SOURCE)
check("current source mismatch fails closed", not mismatch["eligible"]
      and any("source_digest" in r for r in mismatch["reasons"]), mismatch)

presence = row(169, loaded=[], would_omit_count=0)
qualifying = evaluate([epoch(170), presence, row(120), row(72), row(24), row(0)])
check("hook presence is not a scoped selection event",
      not qualifying["eligible"] and qualifying["qualifying_observations"] == 4,
      qualifying)

if FAILURES:
    print("rule-delivery-shadow-eligibility-selftest: FAIL")
    for failure in FAILURES:
        print("  " + failure)
    raise SystemExit(1)
print("rule-delivery-shadow-eligibility-selftest: 24 cases passed")
