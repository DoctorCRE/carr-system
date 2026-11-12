"""Fail-closed admission for CARR-controlled metered dispatches.

This module does not guess vendor usage and it does not turn a caller-provided
"approved" boolean into authority.  It enforces the finite, repository-owned
dispatch contracts.  A cap increase or paid-overage exception therefore needs
an audited policy/authority change; there is no environment-variable bypass.
"""
from __future__ import annotations

from datetime import date
import math
from typing import Any


class MeteringRefusal(RuntimeError):
    """The requested paid/quota-consuming dispatch was not admitted."""


def _required_bool(request: dict[str, Any], field: str) -> None:
    if request.get(field) is not True:
        raise MeteringRefusal(f"{field} must be proven true before dispatch")


def _required_nonempty(request: dict[str, Any], field: str) -> None:
    value = request.get(field)
    if not isinstance(value, str) or not value.strip():
        raise MeteringRefusal(f"{field} is required before dispatch")


def _bounded_number(request: dict[str, Any], field: str, maximum: float) -> None:
    value = request.get(field)
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise MeteringRefusal(f"{field} must be a non-negative number")
    if float(value) > maximum:
        raise MeteringRefusal(f"{field}={value} exceeds registered cap {maximum}")


def authorize_metered_execution(policy: dict[str, Any], gate_key: str,
                                 request: dict[str, Any], *,
                                 today: date | None = None) -> dict[str, Any]:
    """Admit one finite repo-controlled metered action or raise.

    The result is intentionally small and secret-free so callers can retain it
    in their normal receipt.  It is an admission decision, not vendor usage
    evidence and not human overage authority.
    """
    if not isinstance(request, dict):
        raise MeteringRefusal("metering request must be an object")
    gates = policy.get("execution_gates")
    if not isinstance(gates, dict) or gate_key not in gates:
        raise MeteringRefusal(f"unregistered metered dispatch: {gate_key}")
    gate = gates[gate_key]
    if not isinstance(gate, dict) or gate.get("installed") is not True:
        raise MeteringRefusal(f"metered dispatch gate is not installed: {gate_key}")

    platform = gate.get("platform")
    platform_keys = {
        row.get("key") for row in policy.get("platforms", []) if isinstance(row, dict)
    }
    if not isinstance(platform, str) or platform not in platform_keys:
        raise MeteringRefusal(f"metered dispatch has no registered platform: {gate_key}")

    temporary_control = gate.get("temporary_control")
    if temporary_control is not None:
        controls = policy.get("temporary_controls", {})
        control = controls.get(temporary_control) if isinstance(controls, dict) else None
        if not isinstance(control, dict):
            raise MeteringRefusal(f"temporary control is missing: {temporary_control}")
        if control.get("repository_actions_enabled") is False:
            raise MeteringRefusal("GitHub Actions is intentionally disabled")
        if control.get("verified_allowance_reset") is not True:
            raise MeteringRefusal("GitHub Actions allowance reset has not been verified")

    for field in gate.get("required_true", []):
        _required_bool(request, str(field))
    for field in gate.get("required_nonempty", []):
        _required_nonempty(request, str(field))
    for field, maximum in gate.get("maximums", {}).items():
        if isinstance(maximum, bool) or not isinstance(maximum, (int, float)):
            raise MeteringRefusal(f"invalid registered maximum for {field}")
        _bounded_number(request, str(field), float(maximum))

    registration = gate.get("registration")
    if registration is not None:
        if request.get("registration") != registration:
            raise MeteringRefusal("request must match the persistent registration exactly")
        ceiling = registration["monthly_cost_ceiling_usd"]
        _bounded_number(request, "monthly_spend_usd", ceiling)
        if request["monthly_spend_usd"] >= ceiling:
            raise MeteringRefusal("monthly cost ceiling exhausted")
        _bounded_number(request, "organization_spending_limit_usd", ceiling)
        if request.get("meter_month") != (today or date.today()).strftime("%Y-%m"):
            raise MeteringRefusal("current monthly consumption evidence is required")

    # SHA identity is a finite contract, not a free-form assertion.
    if "candidate_sha" in request:
        sha = request["candidate_sha"]
        if not isinstance(sha, str) or len(sha) != 40 or any(
                ch not in "0123456789abcdefABCDEF" for ch in sha):
            raise MeteringRefusal("candidate_sha must be an exact 40-character git SHA")

    return {
        "admitted": True,
        "gate": gate_key,
        "platform": platform,
        "policy_schema_version": policy.get("schema_version"),
        "decided_on": (today or date.today()).isoformat(),
        "authority": "registered_budget_only",
    }
