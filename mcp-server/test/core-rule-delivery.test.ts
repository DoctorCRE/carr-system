// Tests delivery of core rules through registered tools.

import assert from "node:assert/strict";
import { test } from "node:test";

const registered: unknown = await import(new URL("../src/tools.js", import.meta.url).href);
assert.ok(typeof registered === "object" && registered !== null);
assert.ok("executeRegisteredTool" in registered);
const executeRegisteredTool = registered.executeRegisteredTool;
assert.ok(typeof executeRegisteredTool === "function");

import { CORE_RULE_IDS } from "../src/core-rule-ids.ts";

const ACTOR = { id: "11111111-1111-4111-8111-111111111111", slug: "joe", human: true, via: "oauth-google" };

const SHARED = ["24e10ee8", "347a9ca6", "424ba0cc", "4a53ff82"];
const PERSONAL = ["7e9739f2", "57d13061"];

function rows(ids: string[], personal: boolean) {
  return ids.map((id, i) => ({
    id: `${id}-0000-4000-8000-${String(i).padStart(12, "0")}`,
    statement: `A statement long enough to produce a real gist for rule ${id}, with more words after it.`,
    human_quote: `quote ${id}`,
    taught_by: "Joe Bookout",
    personal_to: personal ? "joe" : null,
    scope: {},
  }));
}

const PLAN = [
  { short_id: "24e10ee8", load_layer: "layer0", packs: [], scope: "shared" },
  { short_id: "347a9ca6", load_layer: "layer0", packs: [], scope: "shared" },
  { short_id: "424ba0cc", load_layer: "pack", packs: ["client-deal"], scope: "shared" },
  { short_id: "4a53ff82", load_layer: "control", packs: ["engineering-git"], scope: "shared" },
  { short_id: "7e9739f2", load_layer: "layer0", packs: [], scope: "joe" },
  { short_id: "57d13061", load_layer: "pack", packs: ["joe-comms"], scope: "joe" },
];

const PACK_INDEX = [
  {
    pack: "client-deal",
    title: "Clients, prospects, LOIs and deals",
    triggers: ["deal", "loi", "client"],
    rule_count: 1,
  },
  {
    pack: "engineering-git",
    title: "Repo, git, gates, migrations and deploys",
    triggers: ["git", "worktree", "ci"],
    rule_count: 1,
  },
  { pack: "joe-comms", title: "Joe's X, mail, calendar and social lanes", triggers: ["x.com", "mail"], rule_count: 1 },
];

function client({
  mode = "shadow",
  plan = PLAN,
  packIndex = PACK_INDEX,
}: {
  mode?: string | null;
  plan?: typeof PLAN;
  packIndex?: typeof PACK_INDEX;
} = {}) {
  return {
    query: async (sql: string, params: unknown[] = []) => {
      if (/from v_compiled_rules/i.test(sql)) return { rows: [...rows(SHARED, false), ...rows(PERSONAL, true)] };
      if (/with guidance_registry as/i.test(sql)) {
        const declared = params[5];
        assert.ok(Array.isArray(declared));
        const deliveryPlan = plan
          .filter((r) => r.scope === "shared" || (params[4] && r.scope === params[4]))
          .map((r) => ({ ...r, selected: r.load_layer === "layer0" || r.packs.some((p) => declared.includes(p)) }));
        return {
          rows: [
            {
              state: null,
              manifest_digest: null,
              standing_rules: [],
              projection_summary: [],
              mode,
              map_versions: 1,
              map_digest: "b513180786cf7212877870ab3bc14c03bb78b17b3397eb6ee474187a152b13f2",
              tagged_rules: plan.length,
              delivery_plan: deliveryPlan,
              pack_index: packIndex,
            },
          ],
        };
      }
      if (/v_guidance_registry_state/i.test(sql)) return { rows: [] };
      if (/with registry as/i.test(sql) && /plan\.rows as delivery_plan/i.test(sql)) {
        const declared = params[1];
        assert.ok(Array.isArray(declared));
        const deliveryPlan = plan
          .filter((r) => r.scope === "shared" || (params[0] && r.scope === params[0]))
          .map((r) => ({ ...r, selected: r.load_layer === "layer0" || r.packs.some((p) => declared.includes(p)) }));
        return {
          rows: [
            {
              mode,
              map_versions: 1,
              map_digest: "b513180786cf7212877870ab3bc14c03bb78b17b3397eb6ee474187a152b13f2",
              tagged_rules: plan.length,
              delivery_plan: deliveryPlan,
              pack_index: packIndex,
            },
          ],
        };
      }
      if (/from ops\.rule_delivery_policy/i.test(sql)) return { rows: mode ? [{ mode }] : [] };
      if (/ops\.rule_delivery_plan/i.test(sql)) {
        const declared = params[1];
        assert.ok(Array.isArray(declared));
        return {
          rows: plan
            .filter((r) => r.scope === "shared" || (params[0] && r.scope === params[0]))
            .map((r) => ({ ...r, selected: r.load_layer === "layer0" || r.packs.some((p) => declared.includes(p)) })),
        };
      }
      if (/ops\.rule_pack_index/i.test(sql)) return { rows: packIndex };
      if (/from doctrine_meta/i.test(sql)) return { rows: [{ generation: 7 }] };
      if (/from actor/i.test(sql)) return { rows: [{ id: ACTOR.id }] };
      return { rows: [] };
    },
  };
}

type Rule = { id: string; gist?: string; statement?: string };
function rule(value: unknown): Rule {
  assert.ok(typeof value === "object" && value !== null && "id" in value && typeof value.id === "string");
  if ("gist" in value) assert.ok(typeof value.gist === "string");
  if ("statement" in value) assert.ok(typeof value.statement === "string");
  return {
    id: value.id,
    ...("gist" in value ? { gist: String(value.gist) } : {}),
    ...("statement" in value ? { statement: String(value.statement) } : {}),
  };
}
const call = async (c: ReturnType<typeof client>, args = {}) => {
  const out: unknown = await executeRegisteredTool(c, ACTOR, "standing-context", args);
  assert.ok(typeof out === "object" && out !== null && "shared_rules" in out && Array.isArray(out.shared_rules));
  const delivery = "rule_delivery" in out ? out.rule_delivery : undefined;
  if (delivery !== undefined) assert.ok(typeof delivery === "object" && delivery !== null && "enforcing" in delivery);
  const enforcing =
    typeof delivery === "object" && delivery !== null && "enforcing" in delivery ? delivery.enforcing : undefined;
  let core_preview:
    | {
        core_rule_count: number;
        core_rules_found: number;
        missing_core_ids: unknown[];
        core_rules: unknown;
        pack_index: unknown;
        measured: unknown;
      }
    | undefined;
  if ("core_preview" in out && out.core_preview !== undefined) {
    const preview = out.core_preview;
    assert.ok(
      typeof preview === "object" &&
        preview !== null &&
        "core_rule_count" in preview &&
        "core_rules_found" in preview &&
        "missing_core_ids" in preview,
    );
    assert.ok(
      typeof preview.core_rule_count === "number" &&
        typeof preview.core_rules_found === "number" &&
        Array.isArray(preview.missing_core_ids),
    );
    core_preview = {
      ...preview,
      core_rule_count: preview.core_rule_count,
      core_rules_found: preview.core_rules_found,
      missing_core_ids: preview.missing_core_ids,
      core_rules: "core_rules" in preview ? preview.core_rules : undefined,
      pack_index: "pack_index" in preview ? preview.pack_index : undefined,
      measured: "measured" in preview ? preview.measured : undefined,
    };
  }
  return { shared_rules: out.shared_rules.map(rule), rule_delivery: { enforcing }, core_preview };
};

test("24e10ee8 and 4a53ff82 really are CORE ids in the shipped triage", () => {
  assert.ok(CORE_RULE_IDS.includes("24e10ee8"));
  assert.ok(CORE_RULE_IDS.includes("4a53ff82"));
  assert.equal(
    CORE_RULE_IDS.includes("347a9ca6"),
    false,
    "347a9ca6 must stay a non-core comparison point for this file to mean anything",
  );
});

test("enforced mode keeps CORE Layer 0 rules as gists", async () => {
  const out = await call(client({ mode: "enforced" }));
  const core = out.shared_rules.find((r) => r.id === "24e10ee8");
  assert.ok(core?.gist);
  assert.equal(core.statement, undefined);
});

test("a selected CORE pack rule also stays concise until requested by id", async () => {
  const out = await call(client({ mode: "enforced" }), { packs: ["engineering-git"] });
  const core = out.shared_rules.find((r) => r.id === "4a53ff82");
  assert.ok(core?.gist);
  assert.equal(core.statement, undefined);
  const looked = await call(client({ mode: "enforced" }), { rule_ids: ["4a53ff82"] });
  assert.ok(looked.shared_rules.find((r) => r.id === "4a53ff82")?.statement);
});

test("a non-core rule stays a gist under enforced mode, core or not notwithstanding", async () => {
  const bare = await call(client({ mode: "enforced" }));
  const noncore = bare.shared_rules.find((r) => r.id === "347a9ca6");
  assert.ok(noncore?.gist, "the non-core Layer 0 rule must still be a gist");
  assert.equal(
    noncore.statement,
    undefined,
    "the boot diet must not spend full text on a rule the triage never called core",
  );

  const withPack = await call(client({ mode: "enforced" }), { packs: ["client-deal"] });
  const packRule = withPack.shared_rules.find((r) => r.id === "424ba0cc");
  assert.ok(packRule?.gist, "a selected non-core pack rule stays a gist too");
});

test("detail=full and an explicit rule_ids lookup still override a non-core gist", async () => {
  const full = await call(client({ mode: "enforced" }), { detail: "full" });
  assert.ok(
    full.shared_rules.find((r) => r.id === "347a9ca6")?.statement,
    "detail=full must still be unscoped, core or not",
  );
  const looked = await call(client({ mode: "enforced" }), { rule_ids: ["424ba0cc"] });
  assert.ok(
    looked.shared_rules.find((r) => r.id === "424ba0cc")?.statement,
    "an explicit rule_ids lookup is never scoped away, core or not",
  );
});

test("shadow diagnostic reports coverage without duplicating rule text", async () => {
  const out = await call(client({ mode: "shadow" }));
  assert.ok(out.core_preview);
  assert.equal(out.rule_delivery.enforcing, false);
  assert.equal(out.core_preview.core_rule_count, CORE_RULE_IDS.length);
  assert.ok(out.core_preview.core_rules_found > 0);
  assert.equal(out.core_preview.core_rules, undefined);
  assert.equal(out.core_preview.pack_index, undefined);
  assert.equal(out.core_preview.measured, undefined);
});

test("core_preview is a shadow-only field", async () => {
  const enforced = await call(client({ mode: "enforced" }));
  assert.equal(enforced.core_preview, undefined, "the enforced branch has no shadow preview");

  const noTags = await call(client({ mode: null }));
  assert.equal(
    noTags.core_preview,
    undefined,
    "a worker running ahead of the migration must stay exactly as it was before this slice",
  );

  const fallback = await call(client({ mode: "enforced", plan: [] }));
  assert.equal(
    fallback.core_preview,
    undefined,
    "an unusable enforced plan already has its own fallback story; no preview is bolted on",
  );
});

test("a stale triage id absent from the rule table is reported, not silently dropped", async () => {
  const c = client({ mode: "shadow" });
  const out = await call(c);
  assert.ok(out.core_preview);
  const anyMissing = out.core_preview.missing_core_ids;
  assert.ok(Array.isArray(anyMissing));
  assert.ok(out.core_preview.core_rules_found + anyMissing.length === CORE_RULE_IDS.length);
});
