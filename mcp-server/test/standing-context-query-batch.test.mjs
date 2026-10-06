import { test } from "node:test";
import assert from "node:assert/strict";
import { executeRegisteredTool } from "../src/tools.js";

const ACTOR = {
  id: "11111111-1111-4111-8111-111111111111",
  slug: "joe",
  human: true,
  via: "oauth-google",
};

const RULES = [
  {
    id: "1fddcffb-0000-4000-8000-000000000001",
    statement: "A shared standing rule with enough words to produce a stable gist for this regression test.",
    human_quote: "shared quote",
    taught_by: "Joe Bookout",
    personal_to: null,
    scope: {},
  },
  {
    id: "7e9739f2-0000-4000-8000-000000000002",
    statement: "A personal standing rule with enough words to produce a stable gist for this regression test.",
    human_quote: "personal quote",
    taught_by: "Joe Bookout",
    personal_to: "joe",
    scope: {},
  },
];

function client() {
  const calls = [];
  return {
    calls,
    query: async (sql) => {
      calls.push(sql);
      if (/standing-context:baseline-batch/.test(sql)) {
        assert.doesNotMatch(sql, /from rule\b/,
          "the mandatory batch must not absorb the intentionally fail-soft proposed-rule read");
        return { rows: [{
          all_rules: RULES,
          action_required: [{ number: 9, title: "Act", body: "Do it", owner: "joe" }],
          doctrine_generation: 1165,
        }] };
      }
      if (/from rule\s+where status = 'proposed'/.test(sql)) {
        assert.match(sql, /personal_to =\s*retrieval_visibility_actor_id\(\$1\)/,
          "the sponsor slug must resolve through the read-safe UUID function");
        throw Object.assign(new Error("permission denied for table rule"), { code: "42501" });
      }
      if (/v_guidance_registry_state/.test(sql)) return { rows: [] };
      if (/with registry as/.test(sql)) {
        return { rows: [{
          mode: "shadow",
          map_versions: 1,
          map_digest: "d".repeat(64),
          tagged_rules: 1,
          delivery_plan: [{
            short_id: "1fddcffb",
            load_layer: "layer0",
            packs: [],
            scope: "shared",
            selected: true,
          }],
          pack_index: [],
        }] };
      }
      if (/from v_defect_class/.test(sql)) return { rows: [] };
      throw new Error(`unexpected standalone standing-context query: ${sql}`);
    },
  };
}

test("standing-context batches stable baseline reads without changing its payload", async () => {
  const c = client();
  const out = await executeRegisteredTool(c, ACTOR, "standing-context", {});

  assert.match(out.recite, /1 shared, 1 joe-personal/);
  assert.equal(out.action_required.length, 1);
  assert.equal(out.awaiting_activation, undefined,
    "a denied proposed-rule read stays fail-soft instead of killing standing-context");
  assert.equal(out.doctrine.generation, 1165);
  assert.equal(c.calls.filter(sql => /standing-context:baseline-batch/.test(sql)).length, 1);
  assert.equal(c.calls.some(sql => /from loop_item/.test(sql) &&
    !/standing-context:baseline-batch/.test(sql)), false);
  assert.equal(c.calls.filter(sql => /from rule\s+where status = 'proposed'/.test(sql) &&
    !/standing-context:baseline-batch/.test(sql)).length, 1);
  assert.equal(c.calls.some(sql => /from doctrine_meta/.test(sql) &&
    !/standing-context:baseline-batch/.test(sql)), false);
  assert.equal(c.calls.length, 3,
    "baseline, fail-soft proposals, and the optional guidance/delivery snapshot are the inactive path");
});

test("standing-context launches independent snapshot reads concurrently", async () => {
  let release;
  let timedOut = false;
  const gate = new Promise(resolve => { release = resolve; });
  const started = new Set();
  const timer = setTimeout(() => {
    timedOut = true;
    release();
  }, 100);
  const c = {
    query: async (sql) => {
      const marker = /standing-context:baseline-batch/.test(sql) ? "baseline"
        : /v_guidance_registry_state/.test(sql) ? "registry"
        : /from rule\s+where status = 'proposed'/.test(sql) ? "proposed"
        : "unexpected";
      started.add(marker);
      if (started.size === 3) release();
      await gate;
      if (marker === "baseline") return { rows: [{
        all_rules: RULES, action_required: [], doctrine_generation: 1165,
      }] };
      if (marker === "registry") return { rows: [] };
      if (marker === "proposed") {
        throw Object.assign(new Error("permission denied for table rule"), { code: "42501" });
      }
      throw new Error(`unexpected standing-context query: ${sql}`);
    },
  };

  const out = await executeRegisteredTool(c, { ...ACTOR }, "standing-context", {});
  clearTimeout(timer);
  assert.equal(timedOut, false,
    "independent reads must all start before any one network round trip completes");
  assert.deepEqual([...started].sort(), ["baseline", "proposed", "registry"]);
  assert.match(out.recite, /1 shared, 1 joe-personal/);
});
