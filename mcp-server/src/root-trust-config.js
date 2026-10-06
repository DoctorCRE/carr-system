// SIEP-14 source-only root policy. Provisioning this digest requires a reviewed
// offline ceremony and a new source/registry/epoch receipt. Null is fail-closed.
export const SCAC_ROOT_TRUST_CONFIG = Object.freeze({
  schema_version: "scac-root-trust-config.v1",
  reviewed_custodian_set_digest: null,
  reviewed_custodian_key_digests: Object.freeze([]),
  reviewed_threshold: null,
  review_state: "unprovisioned",
  root_trust_operational: false,
  production_enforcement_active: false,
});
