// Derive and hash rule delivery source projections.
const SOURCE_SCHEMA = "rule-delivery-source.v1";
const MATCHER = "literal-boundary-v1";

/** A persisted sourced plan, as work-request-intake reads it. Fields are coerced by closedPlan. */
type SourcedPlan = Record<string, unknown> & { caps?: { max_steps?: unknown; max_duration_minutes?: unknown } };
type RulePack = { pack?: unknown; triggers?: unknown; rule_count?: unknown };

// Canonicalizes any JSON value: arrays and objects recurse, everything else is kept.
function canonical(value: any): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    return Object.keys(value).sort().reduce((out: Record<string, unknown>, key) => {
      if (value[key] !== undefined) out[key] = canonical(value[key]);
      return out;
    }, {});
  }
  return value;
}

function escapeRegex(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function wordCharacter(value: string | undefined) {
  return /[A-Za-z0-9_]/.test(value as string);
}

export function literalTriggerMatches(text: unknown, trigger: unknown): boolean {
  const needle = String(trigger || "").trim();
  if (!needle) return false;
  const left = wordCharacter(needle[0]) ? "\\b" : "";
  const right = wordCharacter(needle.at(-1)) ? "\\b" : "";
  return new RegExp(`${left}${escapeRegex(needle)}${right}`, "iu").test(String(text || ""));
}

function acceptanceProse(criteria: unknown): string[] {
  return (Array.isArray(criteria) ? criteria : []).flatMap(item =>
    item && typeof item === "object" && typeof item.text === "string" ? [item.text] : []);
}

export function normalizeRuleMapDigest(value: unknown): string | null {
  const digest = String(value || "").trim().toLowerCase().replace(/^sha256:/, "");
  if (!/^[0-9a-f]{64}$/.test(digest)) return null;
  return `sha256:${digest}`;
}

async function sha256(value: unknown) {
  const bytes = new TextEncoder().encode(JSON.stringify(canonical(value)));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return `sha256:${[...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("")}`;
}

function closedPlan(plan: SourcedPlan) {
  return {
    work_request_ref: String(plan.work_request_ref),
    work_request_title: String(plan.work_request_title || ""),
    desired_outcome: String(plan.desired_outcome || ""),
    acceptance_criteria: Array.isArray(plan.acceptance_criteria)
      ? canonical(plan.acceptance_criteria) : [],
    base_version: Number(plan.base_version),
    plan_ref: String(plan.plan_ref),
    plan_hash: String(plan.plan_hash),
    scope_summary: String(plan.scope_summary || ""),
    runbook_ref: String(plan.runbook_ref || ""),
    dependency_refs: Array.isArray(plan.dependency_refs) ? [...plan.dependency_refs].map(String).sort() : [],
    recovery_ref: String(plan.recovery_ref || ""),
    observability_ref: String(plan.observability_ref || ""),
    caps: {
      max_steps: Number(plan.caps?.max_steps || 0),
      max_duration_minutes: Number(plan.caps?.max_duration_minutes || 0),
    },
  };
}

export async function ruleDeliverySourceDigest(projection: Record<string, unknown>) {
  const preimage = { ...projection };
  delete preimage.contract_digest;
  return sha256(preimage);
}

export async function deriveRuleDeliverySource({
  plan,
  packIndex,
  mapDigest,
}: { plan: SourcedPlan; packIndex: RulePack[]; mapDigest: unknown }) {
  const installedDigest = normalizeRuleMapDigest(mapDigest);
  if (!installedDigest) throw new TypeError("one coherent installed rule map digest is required");
  const storedPlan = closedPlan(plan);
  if (!/^WR-[0-9]{1,12}$/.test(storedPlan.work_request_ref)
      || storedPlan.base_version < 1
      || !/^PLAN-[A-Za-z0-9-]+$/.test(storedPlan.plan_ref)
      || !/^sha256:[0-9a-f]{64}$/.test(storedPlan.plan_hash)) {
    throw new TypeError("an exact persisted sourced plan is required");
  }
  if (!Array.isArray(packIndex) || packIndex.length < 1) {
    throw new TypeError("a nonempty typed rule pack index is required");
  }
  const packNames = new Set<string>();
  for (const pack of packIndex) {
    const name = String(pack?.pack || "").trim().toLowerCase();
    if (!/^[a-z0-9][a-z0-9-]*$/.test(name) || packNames.has(name)
        || !Array.isArray(pack?.triggers) || pack.triggers.length < 1
        || pack.triggers.some((trigger: unknown) => typeof trigger !== "string" || !trigger.trim())
        || !Number.isInteger(Number(pack?.rule_count)) || Number(pack.rule_count) < 1) {
      throw new TypeError("the rule pack index must be unique, nonempty, and fully deliverable");
    }
    packNames.add(name);
  }

  const observedText = [
    storedPlan.work_request_title,
    storedPlan.desired_outcome,
    ...acceptanceProse(storedPlan.acceptance_criteria),
    storedPlan.scope_summary,
  ].join("\n");
  const matched = packIndex
    .map(pack => ({
      pack: String(pack.pack || "").trim().toLowerCase(),
      triggers: [...new Set((Array.isArray(pack.triggers) ? pack.triggers : [])
        .map(String).filter(trigger => literalTriggerMatches(observedText, trigger)))].sort(),
    }))
    .filter(item => item.pack && item.triggers.length > 0)
    .sort((left, right) => left.pack.localeCompare(right.pack));

  const projection = {
    schema_version: SOURCE_SCHEMA,
    source: {
      work_request_ref: storedPlan.work_request_ref,
      base_version: storedPlan.base_version,
      plan_ref: storedPlan.plan_ref,
      plan_hash: storedPlan.plan_hash,
    },
    trigger_map: {
      source: "ops/config/rule-enforcement-map.json",
      map_digest: installedDigest,
      matcher: MATCHER,
    },
    matched,
    required_packs: matched.map(item => item.pack),
  };
  return { ...projection, contract_digest: await ruleDeliverySourceDigest(projection) };
}
