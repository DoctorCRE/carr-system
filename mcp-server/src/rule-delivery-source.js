const SOURCE_SCHEMA = "rule-delivery-source.v1";
const MATCHER = "literal-boundary-v1";

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    return Object.keys(value).sort().reduce((out, key) => {
      if (value[key] !== undefined) out[key] = canonical(value[key]);
      return out;
    }, {});
  }
  return value;
}

function escapeRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function wordCharacter(value) {
  return /[A-Za-z0-9_]/.test(value);
}

export function literalTriggerMatches(text, trigger) {
  const needle = String(trigger || "").trim();
  if (!needle) return false;
  const left = wordCharacter(needle[0]) ? "\\b" : "";
  const right = wordCharacter(needle.at(-1)) ? "\\b" : "";
  return new RegExp(`${left}${escapeRegex(needle)}${right}`, "iu").test(String(text || ""));
}

function reasonText(reason) {
  const value = String(reason || "");
  const body = value.includes(":") ? value.slice(value.indexOf(":") + 1) : value;
  return body.replaceAll("_", " ").replaceAll("-", " ");
}

function textValues(value) {
  if (Array.isArray(value)) return value.flatMap(textValues);
  if (value && typeof value === "object") return Object.values(value).flatMap(textValues);
  return value === null || value === undefined ? [] : [String(value)];
}

function acceptanceProse(criteria) {
  return (Array.isArray(criteria) ? criteria : []).flatMap(item =>
    item && typeof item === "object" && typeof item.text === "string" ? [item.text] : []);
}

function masterPlanProse(plan) {
  if (!plan || typeof plan !== "object" || Array.isArray(plan)) return [];
  return [
    "product_goal", "non_goals", "architecture", "authority_boundaries",
    "planned_checks", "baseline_comparison", "release_strategy", "rollback_strategy",
    "observability_strategy", "fully_shipped_definition", "prerequisite_policy",
  ].flatMap(field => textValues(plan[field]));
}

function normalizeDigest(value) {
  const digest = String(value || "").trim().toLowerCase().replace(/^sha256:/, "");
  if (!/^[0-9a-f]{64}$/.test(digest)) return null;
  return `sha256:${digest}`;
}

async function sha256(value) {
  const bytes = new TextEncoder().encode(JSON.stringify(canonical(value)));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return `sha256:${[...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("")}`;
}

function closedPlan(plan) {
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

function closedAdmission(admission) {
  if (!admission) return null;
  return {
    admission_ref: String(admission.admission_ref),
    admission_hash: String(admission.admission_hash),
    builder_session_ref: String(admission.builder_session_ref),
    master_plan: admission.master_plan && typeof admission.master_plan === "object"
      ? canonical(admission.master_plan) : null,
  };
}

export async function ruleDeliverySourceDigest(projection) {
  const preimage = { ...projection };
  delete preimage.contract_digest;
  return sha256(preimage);
}

export async function deriveRuleDeliverySource({
  plan,
  heavyClassification,
  admittedHeavyContract,
  packIndex,
  mapDigest,
}) {
  const installedDigest = normalizeDigest(mapDigest);
  if (!installedDigest) throw new TypeError("one coherent installed rule map digest is required");
  const storedPlan = closedPlan(plan);
  if (!/^WR-[0-9]{1,12}$/.test(storedPlan.work_request_ref)
      || storedPlan.base_version < 1
      || !/^PLAN-[A-Za-z0-9-]+$/.test(storedPlan.plan_ref)
      || !/^sha256:[0-9a-f]{64}$/.test(storedPlan.plan_hash)) {
    throw new TypeError("an exact persisted sourced plan is required");
  }
  if (!heavyClassification || !["standard", "heavy"].includes(heavyClassification.tier)
      || !Array.isArray(heavyClassification.reasons)
      || heavyClassification.reasons.some(reason => typeof reason !== "string" || !reason.trim())) {
    throw new TypeError("the exact typed heavy-build classifier result is required");
  }
  const tier = heavyClassification.tier;
  const reasons = [...new Set(heavyClassification.reasons)].sort();
  const admission = closedAdmission(admittedHeavyContract);
  if (tier === "heavy" && !admission) throw new TypeError("heavy delivery source requires its admitted contract");

  if (!Array.isArray(packIndex) || packIndex.length < 1) {
    throw new TypeError("a nonempty typed rule pack index is required");
  }
  const packNames = new Set();
  for (const pack of packIndex) {
    const name = String(pack?.pack || "").trim().toLowerCase();
    if (!/^[a-z0-9][a-z0-9-]*$/.test(name) || packNames.has(name)
        || !Array.isArray(pack?.triggers) || pack.triggers.length < 1
        || pack.triggers.some(trigger => typeof trigger !== "string" || !trigger.trim())
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
    ...reasons.map(reasonText),
    ...masterPlanProse(admission?.master_plan),
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
    build_classifier: {
      function: "ops.heavy_build_classification",
      tier,
      reasons,
    },
    admission: admission ? {
      admission_ref: admission.admission_ref,
      admission_hash: admission.admission_hash,
      builder_session_ref: admission.builder_session_ref,
    } : null,
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

export function normalizeRuleMapDigest(value) {
  return normalizeDigest(value);
}
