"""Pure executor qualification used by the evening runner. No transport exists."""

from enum import Enum


class MoveEligibility(Enum):
    ANY_READ_STATE = "any_read_state"
    READ_ONLY = "read_only"


REQUIRED_EVIDENCE = frozenset({
    "employer_permission", "mailbox_binding", "stable_identity",
    "same_mailbox_destination", "preserve_read_state", "routing_equivalence",
    "idempotency_reconciliation", "operating_policy",
})


def select_candidate(contract: dict, candidate_id: str) -> str:
    """Return a qualified candidate ID or refuse. Never execute a mailbox effect."""
    if contract.get("schema") != "outlook-triage-executor-contract/v1":
        raise ValueError("unsupported_contract")
    policy = contract.get("read_state_policy", {})
    if (not isinstance(policy, dict)
            or set(policy) != {"source_decision", "preserve_read_state", "move_eligibility"}
            or policy.get("preserve_read_state") is not True):
        raise ValueError("invalid_read_policy")
    try:
        eligibility = MoveEligibility(policy["move_eligibility"])
    except ValueError:
        raise ValueError("invalid_read_policy") from None
    required_read = eligibility is MoveEligibility.READ_ONLY
    matches = [item for item in contract.get("candidates", []) if item.get("id") == candidate_id]
    if len(matches) != 1:
        raise ValueError("unknown_or_duplicate_candidate")
    candidate = matches[0]
    if candidate.get("status") != "sanctioned" or candidate.get("lifecycle") != "verified":
        raise ValueError("capability_not_verified")
    evidence = candidate.get("evidence", {})
    required = REQUIRED_EVIDENCE | ({"atomic_read_condition"} if required_read else set())
    if any(not isinstance(evidence.get(key), str) or not evidence[key].strip() for key in required):
        raise ValueError("missing_capability_evidence")
    if candidate.get("verified_read_policy") != policy:
        raise ValueError("stale_policy_evidence")
    return candidate_id
