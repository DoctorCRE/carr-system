export class PolicyEpochRefusal extends Error {
  constructor(reason, status = null) {
    super(reason);
    this.name = "PolicyEpochRefusal";
    this.reason = reason;
    this.status = status;
  }
}

const EPOCH_STATES = new Set(["current", "stale", "future", "rolled_back"]);
const COMPATIBILITY_STATES = new Set(["compatible", "incompatible"]);

function safeDigest(value) {
  return typeof value === "string" && /^sha256:[0-9a-f]{64}$/.test(value);
}

export function normalizePolicyEpochStatus(value) {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new PolicyEpochRefusal("epoch_status_unavailable");
  const epochState = value.epoch_state;
  const compatibilityState = value.compatibility_state;
  if (epochState !== null && !EPOCH_STATES.has(epochState))
    throw new PolicyEpochRefusal("epoch_status_malformed");
  if (!COMPATIBILITY_STATES.has(compatibilityState))
    throw new PolicyEpochRefusal("epoch_status_malformed");
  const current = value.current_epoch;
  const request = value.request_epoch;
  if ((current !== null && (!Number.isSafeInteger(current) || current < 1)) ||
      (request !== null && (!Number.isSafeInteger(request) || request < 1)))
    throw new PolicyEpochRefusal("epoch_status_malformed");
  if (epochState !== null &&
      (!safeDigest(value.current_entry_digest) || !safeDigest(value.registry_digest)))
    throw new PolicyEpochRefusal("epoch_status_malformed");
  const coherent = epochState === "current" && compatibilityState === "compatible"
    ? current === request && value.reason_id === null
    : compatibilityState === "incompatible" && value.reason_id === "scac.refusal.epoch_incompatible";
  if (!coherent || value.compatibility_authority !== "fact_only_not_enforcement")
    throw new PolicyEpochRefusal("epoch_status_malformed");
  return Object.freeze({
    schema_version: "scac-policy-epoch-status.v1",
    current_epoch: current,
    request_epoch: request,
    epoch_state: epochState,
    compatibility_state: compatibilityState,
    reason_id: value.reason_id,
    current_entry_digest: value.current_entry_digest || null,
    registry_version: value.registry_version || null,
    registry_digest: value.registry_digest || null,
  });
}

export function assertCurrentPolicyEpoch(value) {
  const status = normalizePolicyEpochStatus(value);
  if (status.epoch_state !== "current" || status.compatibility_state !== "compatible")
    throw new PolicyEpochRefusal("epoch_incompatible", status);
  return status;
}
