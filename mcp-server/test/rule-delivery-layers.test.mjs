// THE DELIVERY HALF of the 2026-08-23 rules council: standing-context stops
// being a verb with one mode.
//
// Every test here is about a way scoping goes wrong rather than a way it goes
// right, because the failure mode both chairs named is silent: a session that
// boots with fewer rules looks exactly like a session that boots correctly.
import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, executeRegisteredTool } from "../src/tools.js";

const ACTOR = { id: "11111111-1111-4111-8111-111111111111", slug: "joe",
                human: true, via: "oauth-google" };

const SHARED = ["1fddcffb", "347a9ca6", "424ba0cc", "4a53ff82"];
const PERSONAL = ["7e9739f2", "57d13061"];

function rows(ids, personal) {
  return ids.map((id, i) => ({
    id: `${id}-0000-4000-8000-${String(i).padStart(12, "0")}`,
    statement: `A statement long enough to produce a real gist for rule ${id}, with more words after it.`,
    human_quote: `quote ${id}`, taught_by: "Joe Bookout",
    personal_to: personal ? "joe" : null, scope: {},
  }));
}

// short id -> load layer, mirroring how the reviewed map tags these four.
const PLAN = [
  { short_id: "1fddcffb", load_layer: "layer0", packs: [], scope: "shared" },
  { short_id: "347a9ca6", load_layer: "layer0", packs: [], scope: "shared" },
  { short_id: "424ba0cc", load_layer: "pack", packs: ["client-deal"], scope: "shared" },
  { short_id: "4a53ff82", load_layer: "control", packs: ["engineering-git"], scope: "shared" },
  { short_id: "7e9739f2", load_layer: "layer0", packs: [], scope: "joe" },
  { short_id: "57d13061", load_layer: "pack", packs: ["joe-comms"], scope: "joe" },
];

const PACK_INDEX = [
  { pack: "client-deal", title: "Clients, prospects, LOIs and deals",
    triggers: ["deal", "loi", "client"], rule_count: 1 },
  { pack: "engineering-git", title: "Repo, git, gates, migrations and deploys",
    triggers: ["git", "worktree", "ci"], rule_count: 1 },
  { pack: "joe-comms", title: "Joe's X, mail, calendar and social lanes",
    triggers: ["x.com", "mail"], rule_count: 1 },
];

function client({ mode = "shadow", plan = PLAN, packIndex = PACK_INDEX,
                  mapState = null } = {}) {
  const calls = [];
  return {
    calls,
    query: async (sql, params = []) => {
      calls.push({ sql, params });
      if (/from v_compiled_rules/i.test(sql))
        return { rows: [...rows(SHARED, false), ...rows(PERSONAL, true)] };
      if (/with guidance_registry as/i.test(sql)) {
        const state = mapState || { map_versions: 1, map_digest: "d".repeat(64),
                                    tagged_rules: plan.length };
        const declared = params[5] || [];
        return { rows: [{ state: null, manifest_digest: null,
          standing_rules: [], projection_summary: [], mode, ...state,
          pack_index: packIndex,
          delivery_plan: plan
            .filter(r => r.scope === "shared" || (params[4] && r.scope === params[4]))
            .map(r => ({ ...r, selected: r.load_layer === "layer0"
                         || r.packs.some(p => declared.includes(p)) })) }] };
      }
      if (/v_guidance_registry_state/i.test(sql)) return { rows: [] };
      if (/with registry as/i.test(sql)) {
        const state = mapState || { map_versions: 1, map_digest: "d".repeat(64),
                                    tagged_rules: plan.length };
        const declared = params[1] || [];
        return { rows: [{ mode, ...state, pack_index: packIndex,
          delivery_plan: plan
            .filter(r => r.scope === "shared" || (params[0] && r.scope === params[0]))
            .map(r => ({ ...r, selected: r.load_layer === "layer0"
                         || r.packs.some(p => declared.includes(p)) })) }] };
      }
      if (/from ops\.rule_delivery_policy/i.test(sql))
        return { rows: mode ? [{ mode }] : [] };
      if (/count\(distinct map_digest\)/i.test(sql))
        return { rows: [mapState || { map_versions: 1, map_digest: "d".repeat(64),
                                      tagged_rules: plan.length }] };
      if (/ops\.rule_delivery_plan/i.test(sql)) {
        const declared = params[1] || [];
        return { rows: plan
          .filter(r => r.scope === "shared" || (params[0] && r.scope === params[0]))
          .map(r => ({ ...r, selected: r.load_layer === "layer0"
                       || r.packs.some(p => declared.includes(p)) })) };
      }
      if (/ops\.rule_pack_index/i.test(sql)) return { rows: packIndex };
      if (/from doctrine_meta/i.test(sql)) return { rows: [{ generation: 7 }] };
      if (/from actor/i.test(sql)) return { rows: [{ id: ACTOR.id }] };
      return { rows: [] };
    },
  };
}

const call = (c, args = {}) => executeRegisteredTool(c, ACTOR, "standing-context", args);

test("shadow mode changes nothing a session receives, and says what it would have cut", async () => {
  const c = client({ mode: "shadow" });
  const out = await call(c);
  assert.equal(out.shared_rules.length, 4, "shadow must not cut a single rule");
  assert.equal(out.personal_rules.length, 2);
  assert.match(out.recite, /Rules loaded: 4 shared, 2 joe-personal/,
    "the recited counts stay the true totals while shadow is on");
  assert.equal(out.rule_delivery.mode, "shadow");
  assert.equal(out.rule_delivery.enforcing, false);
  assert.deepEqual(out.rule_delivery.would_omit.sort(),
    ["424ba0cc", "4a53ff82", "57d13061"],
    "the shadow evidence is exactly the rules a scoped boot would not have sent");
});

test("an undeclared boot compiles Layer 0, never everything and never nothing", async () => {
  const c = client({ mode: "enforced" });
  const out = await call(c);
  assert.deepEqual(out.shared_rules.map(r => r.id), ["1fddcffb", "347a9ca6"]);
  assert.deepEqual(out.personal_rules.map(r => r.id), ["7e9739f2"]);
  assert.ok(out.shared_rules.length > 0, "Layer 0 is never empty");
  assert.match(out.recite, /2 of 4 shared/, "the counts say what was NOT loaded, too");
  assert.equal(out.rule_delivery.enforcing, true);
});

test("a declared pack ADDS rules and never subtracts Layer 0", async () => {
  const c = client({ mode: "enforced" });
  const bare = await call(c);
  const withPack = await call(client({ mode: "enforced" }), { packs: ["client-deal"] });
  for (const id of bare.shared_rules.map(r => r.id))
    assert.ok(withPack.shared_rules.some(r => r.id === id),
      `declaring a pack dropped Layer 0 rule ${id}`);
  assert.equal(bare.shared_rules.some(r => r.id === "424ba0cc"), false,
    "the pack's rule must be ABSENT before the pack is declared, or this test "
    + "would pass on a build that scopes nothing at all");
  assert.ok(withPack.shared_rules.some(r => r.id === "424ba0cc"),
    "the pack's own rule must arrive");
});

test("vendor-intros is a real pack, not 14 selected rows absent from the consumer pool", async () => {
  const introId = "e6e0b0e7";
  const introRow = { ...rows([introId], false)[0], scope: { kind: "intro_politics" } };
  const plan = [...PLAN, { short_id: introId, load_layer: "pack",
    packs: ["vendor-intros"], scope: "shared" }];
  const packIndex = [...PACK_INDEX, { pack: "vendor-intros", title: "Vendor intros",
    triggers: ["introduction"], rule_count: 1 }];
  const make = () => {
    const c = client({ mode: "enforced", plan, packIndex });
    const inner = c.query;
    c.query = async (sql, params = []) => {
      if (/from v_compiled_rules/i.test(sql) && !/intro_politics/i.test(sql))
        return { rows: [...rows(SHARED, false), ...rows(PERSONAL, true), introRow] };
      return inner(sql, params);
    };
    return c;
  };
  const bare = await call(make());
  assert.equal(bare.shared_rules.some(r => r.id === introId), false,
    "intro politics must not leak into an ordinary boot");
  const loaded = await call(make(), { packs: ["vendor-intros"] });
  assert.equal(loaded.shared_rules.some(r => r.id === introId), true,
    "declaring vendor-intros must deliver its actual rule body");
});

test("workflow= is read as a pack, so the council's compile path works as written", async () => {
  const out = await call(client({ mode: "enforced" }), { workflow: "engineering-git" });
  assert.ok(out.shared_rules.some(r => r.id === "4a53ff82"));
  assert.deepEqual(out.rule_delivery.declared_packs, ["engineering-git"]);
});

test("an unknown pack is reported, never silently empty", async () => {
  const out = await call(client({ mode: "enforced" }), { packs: ["enginering-git"] });
  assert.deepEqual(out.rule_delivery.packs_not_found, ["enginering-git"]);
  assert.equal(out.rule_delivery.enforcing, false);
  assert.match(out.rule_delivery.fallback, /FULL set/);
  assert.ok(out.shared_rules.length >= 2, "Layer 0 still arrives after a typo");
});

test("an empty requested pack and an incoherent registry both fall back to full delivery", async () => {
  const emptyIndex = [...PACK_INDEX, { pack: "empty-pack", title: "Empty",
    triggers: ["empty"], rule_count: 0 }];
  const empty = await call(client({ mode: "enforced", packIndex: emptyIndex }),
    { packs: ["empty-pack"] });
  assert.deepEqual(empty.rule_delivery.empty_packs, ["empty-pack"]);
  assert.equal(empty.rule_delivery.enforcing, false);
  assert.equal(empty.shared_rules.length, 4);

  const split = await call(client({ mode: "enforced",
    mapState: { map_versions: 2, map_digest: "d".repeat(64), tagged_rules: PLAN.length } }),
    { packs: ["engineering-git"] });
  assert.equal(split.rule_delivery.map_versions, 2);
  assert.equal(split.rule_delivery.enforcing, false);
  assert.equal(split.shared_rules.length, 4);
});

test("delivery reports the one installed map digest used for scoped selection", async () => {
  const c = client({ mode: "enforced" });
  const out = await call(c, { packs: ["engineering-git"] });
  assert.equal(out.rule_delivery.map_digest, `sha256:${"d".repeat(64)}`);
  assert.equal(out.rule_delivery.map_versions, 1);
  assert.equal(out.rule_delivery.tagged_rules, PLAN.length);
  const snapshots = c.calls.filter(call => /rule_delivery_policy|rule_load_layer|rule_delivery_plan|rule_pack_index/i.test(call.sql));
  assert.equal(snapshots.length, 1);
  for (const name of ["rule_delivery_policy", "rule_load_layer", "rule_delivery_plan", "rule_pack_index"])
    assert.match(snapshots[0].sql, new RegExp(name));
});

test("the pack index ships instead of the packs, with what fires each one", async () => {
  const out = await call(client({ mode: "enforced" }));
  const names = out.rule_delivery.pack_index.map(p => p.pack);
  assert.deepEqual(names, ["client-deal", "engineering-git", "joe-comms"]);
  for (const p of out.rule_delivery.pack_index)
    assert.ok(p.triggers.length > 0, `${p.pack} has no trigger, so nothing can load it`);
  assert.match(out.rule_delivery.hint, /observed work/i);
  assert.match(out.rule_delivery.hint, /347a9ca6/, "the drift law must be named at the boot");
});

test("a rule asked for by id is delivered even when scoping would omit it", async () => {
  const out = await call(client({ mode: "enforced" }), { rule_ids: ["424ba0cc"] });
  const got = out.shared_rules.find(r => r.id === "424ba0cc");
  assert.ok(got, "the lookup path must never be scoped away");
  assert.ok(got.statement, "and it must come back in full, not as a gist");
});

test("detail=full is never scoped", async () => {
  const out = await call(client({ mode: "enforced" }), { detail: "full" });
  assert.equal(out.shared_rules.length, 4);
  assert.equal(out.personal_rules.length, 2);
});

test("no delivery tags installed means the FULL set, said out loud", async () => {
  const out = await call(client({ mode: "enforced", plan: [] }));
  assert.equal(out.shared_rules.length, 4, "an empty plan must never empty the boot");
  assert.equal(out.rule_delivery.enforcing, false);
  assert.match(out.rule_delivery.fallback, /never returns nothing/);
});

test("a worker running ahead of the migration behaves exactly as it did before", async () => {
  const out = await call(client({ mode: null }));
  assert.equal(out.shared_rules.length, 4);
  assert.equal(out.rule_delivery, undefined, "no tags, no delivery block, no error");
});

test("an unsponsored runtime is never handed a partner's personal rules", async () => {
  const bare = { ...ACTOR, slug: "smoke-probe", human: false, probe: true, via: "probe-token" };
  const c = client({ mode: "enforced" });
  const out = await executeRegisteredTool(c, bare, "standing-context", {});
  assert.equal(out.personal_rules.length, 0);
  const planCall = c.calls.find(x => /rule_delivery_plan/i.test(x.sql));
  assert.equal(planCall.params[0], null,
    "the plan must be asked for with no partner, not with a guessed one");
});

test("an active guidance registry cannot narrow the boot below Layer 0", async () => {
  // The registry path returns a NARROWED set of its own — constitution plus
  // applicable constraints. Two independent narrowings composed would land
  // below Layer 0, which is the floor of a boot in both designs.
  const c = client({ mode: "enforced" });
  const inner = c.query;
  c.query = async (sql, params = []) => {
    if (/with guidance_registry as/i.test(sql)) {
      const result = await inner(sql, params);
      return { rows: [{ ...result.rows[0], state: "active",
        manifest_digest: "d".repeat(64), standing_rules: [], projection_summary: [] }] };
    }
    if (/v_guidance_registry_state/i.test(sql))
      return { rows: [{ state: "active", manifest_digest: "d".repeat(64) }] };
    if (/ops\.standing_guidance/i.test(sql))
      return { rows: [] };            // the registry chose to load nothing at all
    if (/v_guidance_projection_summary/i.test(sql)) return { rows: [] };
    return inner(sql, params);
  };
  const out = await executeRegisteredTool(c, ACTOR, "standing-context", {});
  assert.deepEqual(out.shared_rules.map(r => r.id), ["1fddcffb", "347a9ca6"],
    "Layer 0 must survive an empty registry selection");
  assert.deepEqual(out.personal_rules.map(r => r.id), ["7e9739f2"]);
});

test("the verb still advertises both ways to name a pack", () => {
  const props = TOOLS["standing-context"].inputSchema.properties;
  assert.ok(props.packs, "packs must be callable for work that spans two domains");
  assert.match(props.workflow.description, /pack/i);
});
