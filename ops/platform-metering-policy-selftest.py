#!/usr/bin/env python3
"""Fail-closed contract checks for the CARR platform metering register."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
REGISTRY = REPO / "ops" / "config" / "platform-metering.v1.json"
SERVICES = REPO / "ops" / "config" / "services.json"
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(("  ok    " if condition else "  FAIL  ") + label
          + (f" — {detail}" if detail else ""))
    if not condition:
        FAILED.append(label)


def nonempty_strings(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, str) and bool(item.strip()) for item in value
    )


def main() -> int:
    print("platform-metering-policy-selftest — metered work fails closed\n")
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    services = json.loads(SERVICES.read_text(encoding="utf-8"))
    platforms = registry.get("platforms")

    check("registry is versioned", registry.get("schema_version") == 1)
    standing_binding = registry.get("standing_rule_binding", {})
    check("the approved spending rule is bound to the mechanical cost gate",
          standing_binding.get("rule_id") == "a57d981a-8f6d-4c18-95ee-0e63a5a90b89"
          and standing_binding.get("statement_sha256") ==
              "c6fd62eb91d3f03b21a6098a6fd6b2848b902a45b8c0430b1717edf4e143f668"
          and standing_binding.get("durable_decision_ref") ==
              "8b31938a-e2f2-4b8f-9c29-187efa5c1650"
          and standing_binding.get("decision_event_ref") ==
              "f7ea060c-268b-47f1-8a17-7168841b77e0"
          and standing_binding.get("control_key") == "platform_metering_pre_dispatch"
          and standing_binding.get("approval_substance_already_recorded") is True
          and standing_binding.get("required_post_deploy_state") ==
              "active_hard_enforced")
    check("platform registry is nonempty", isinstance(platforms, list) and bool(platforms))
    if not isinstance(platforms, list):
        return 1

    platform_entries: list[dict[str, Any]] = [
        entry for entry in platforms if isinstance(entry, dict)
    ]
    keys = [str(entry.get("key", "")) for entry in platform_entries]
    check("platform keys are unique", len(keys) == len(set(keys)))
    required = {
        "github", "neon", "cloudflare-workers", "cloudflare-kv", "cloudflare-r2",
        "anthropic-claude", "openai-codex", "google-workspace", "healthchecks",
        "blotato", "make", "salesforce", "market-data-licenses", "local-mac",
    }
    check("every known system platform is registered", required <= set(keys),
          f"missing={sorted(required - set(keys))}")

    control_scope = registry.get("control_scope", {})
    controlled = [
        str(key)
        for class_keys in control_scope.values()
        if isinstance(class_keys, list)
        for key in class_keys
    ] if isinstance(control_scope, dict) else []
    check("every platform has exactly one cost-control class",
          sorted(controlled) == sorted(keys) and len(controlled) == len(set(controlled)))
    check("repo-controlled paid dispatch platforms are pre-dispatch gated",
          set(control_scope.get("pre_dispatch_gated", [])) ==
          {"github", "neon", "cloudflare-workers", "anthropic-claude"})

    covered_runtimes: set[str] = {
        runtime
        for entry in platform_entries
        for runtime in entry.get("covers_service_runtimes", [])
        if isinstance(runtime, str)
    }
    service_runtimes: set[str] = {
        service["runtime"] for service in services.get("services", [])
        if isinstance(service, dict) and isinstance(service.get("runtime"), str)
    }
    check("every registered service runtime has a metering owner",
          service_runtimes <= covered_runtimes,
          f"missing={sorted(service_runtimes - covered_runtimes)}")

    for entry in platforms:
        if not isinstance(entry, dict):
            check("platform entries are objects", False)
            continue
        key = str(entry.get("key", "<missing>"))
        check(f"{key}: billing model is explicit",
              isinstance(entry.get("billing_model"), str) and bool(entry["billing_model"].strip()))
        check(f"{key}: meter units are explicit", nonempty_strings(entry.get("meter_units")))
        check(f"{key}: hard controls are explicit", nonempty_strings(entry.get("hard_controls")))
        check(f"{key}: optimization rule is explicit",
              isinstance(entry.get("optimization"), str) and bool(entry["optimization"].strip()))
        check(f"{key}: live evidence is timestamped",
              isinstance(entry.get("live_evidence"), dict)
              and bool(entry["live_evidence"].get("observed_at")))
        plan = str(entry.get("current_plan", ""))
        if "unknown" in plan:
            check(f"{key}: unknown plan has a human readback action",
                  isinstance(entry.get("next_human_readback"), str)
                  and bool(entry["next_human_readback"].strip()))
        sources = entry.get("sources")
        check(f"{key}: sources field is a list", isinstance(sources, list))
        if isinstance(sources, list):
            check(f"{key}: source URLs are HTTPS",
                  all(isinstance(source, str) and source.startswith("https://")
                      for source in sources))

    policy = registry.get("global_policy", {})
    cloud = policy.get("cloud_full_verification", {})
    temporary = policy.get("temporary_cloud_resources", {})
    check("one remote full verification is allowed per publication candidate",
          cloud.get("max_per_publication_candidate") == 1)
    check("an unchanged remote failure must be diagnosed locally",
          cloud.get("same_failure_requires_local_diagnosis_before_retry") is True)
    check("temporary cloud resources require same-run teardown",
          temporary.get("same_run_teardown_required") is True
          and temporary.get("cleanup_failure_is_run_failure") is True
          and 0 < temporary.get("max_lifetime_minutes", 0) <= 120)
    release_abandon = (REPO / "ops" / "release-abandon-selftest.py").read_text(
        encoding="utf-8"
    )
    check("the release-abandon fixture never falls back to a metered Neon branch",
          "branches create" not in release_abandon
          and ('"branches",' + ' "create"') not in release_abandon
          and "metered-provider fallback is disabled" in release_abandon)
    keepalive_plist = (REPO / "ops" / "launchd" / "com.carr.keepalive-probe.plist").read_text(
        encoding="utf-8"
    )
    check("the local liveness probe does not wake Neon faster than its health cadence",
          "<!-- carr-launchd-interval-seconds: 1800 " in keepalive_plist
          and "<key>StartInterval</key>" not in keepalive_plist)
    check("raising spend requires Joe approval", "Joe approval" in policy.get("authority", ""))

    github: dict[str, Any] = next(
        (entry for entry in platform_entries if entry.get("key") == "github"), {}
    )
    # These were one check named after only half of what it asserted. The $0
    # overage floor is permanent; the Actions pause was temporary by its own
    # ends_after_verified_allowance_reset text and was cleared on 2026-09-12
    # (decision 9935743c-21de-4490-83db-d11a5e20f6b1). Asserted together, a
    # correct clearing of the pause reads as a breach of the overage floor.
    pause = registry.get("temporary_controls", {}).get("github_actions_pause", {})
    check("GitHub paid overage remains hard-stopped",
          github.get("live_evidence", {}).get("paid_overage_budget_usd") == 0
          and pause.get("paid_overage_budget_usd") == 0)
    check("the Actions pause state agrees with the observed repository state",
          pause.get("repository_actions_enabled")
          == github.get("live_evidence", {}).get("repository_actions_enabled"))
    check("a cleared Actions pause names the decision that cleared it",
          pause.get("repository_actions_enabled") is False
          or bool(pause.get("cleared_decision_ref")))

    neon: dict[str, Any] = next(
        (entry for entry in platform_entries if entry.get("key") == "neon"), {}
    )
    check("Neon has a finite observed organization spending limit",
          neon.get("live_evidence", {}).get("organization_spending_limit_usd") == 10)
    check("Neon staging abandoned branches were removed",
          neon.get("live_evidence", {}).get("staging_current_branch_count") == 1
          and neon.get("live_evidence", {}).get("deleted_abandoned_staging_test_branches") == 5)

    raw = REGISTRY.read_text(encoding="utf-8").lower()
    forbidden = ("postgresql://", "postgres://", "api_key=", "bearer ", "token=")
    check("registry contains no credential material",
          not any(marker in raw for marker in forbidden))

    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
