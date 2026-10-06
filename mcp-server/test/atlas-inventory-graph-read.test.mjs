import { CURRENT_REGISTRY_VERSION } from "../../ops/scac-mutation-inventory.mjs";
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { TOOLS } from "../src/tools.js";
import { ATLAS_MUTATION_DECLARATIONS as SCAC_MUTATION_OPERATIONS } from "../src/atlas-mutation-declarations.generated.js";
import { AUTHENTICATED_SURFACES } from "../src/workspace-surface-inventory.js";
import {
  assertReadOnly, ATLAS_GRAPH_PATH, ATLAS_KNOWN_GAPS, ATLAS_LEGS, ATLAS_LIMIT_MAX,
  bundleDigest, buildDeclaredLayer, DECLARED_LAYER, decodeCursor, encodeCursor,
  readAtlasInventoryGraph,
} from "../src/atlas-inventory-graph.v5.js";

const JOE = { slug: "joe" };
const NOW = () => new Date("2026-09-16T12:00:00.000Z");
const CORRELATION = "corr-atlas";

// One row set per installed/observed leg, keyed by the relation each leg names.
// Deliberately includes a retired service and a rejected rule: a retired thing
// must stay discoverable on request and hidden by default.
const ROWS = {
  "ops.service": [
    { service_key: "dealroom-worker", service_name: "Deal Room Worker", criticality: "critical",
      retired_at: null, updated_at: "2026-09-16T10:00:00.000Z" },
    { service_key: "record-exporter", service_name: "Record Exporter", criticality: "medium",
      retired_at: null, updated_at: "2026-09-15T10:00:00.000Z" },
    { service_key: "md-renderer", service_name: "Retired MD renderer", criticality: "low",
      retired_at: "2026-08-19T00:00:00.000Z", updated_at: "2026-08-19T00:00:00.000Z" },
  ],
  "ops.service_environment": [
    { service_key: "dealroom-worker", environment: "production", endpoint: "https://dealroom",
      deploy_mechanism: "wrangler", updated_at: "2026-09-16T10:00:00.000Z" },
  ],
  "ops.service_dependency": [
    { service_key: "dealroom-worker", depends_on_key: "record-exporter" },
  ],
  "ops.job_definition": [
    { definition_key: "calendar-prebrief", version: 1, enabled: true, risk: "green",
      execution_kind: "deterministic", legacy_disabled_at: null, updated_at: "2026-09-16T09:00:00.000Z" },
  ],
  "ops.job": [
    { definition_key: "calendar-prebrief", state: "succeeded", mode: "live",
      updated_at: "2026-09-16T09:30:00.000Z" },
  ],
  "ops.rule_admission": [
    { rule_id: "11111111-1111-4111-8111-111111111111", state: "admitted",
      enforcement_class: "machine_enforceable", enforcement_status: "hard_enforced",
      binding_moment: "pre_push", reason: "Gates must be registered. And then some.",
      updated_at: "2026-09-16T08:00:00.000Z" },
    { rule_id: "22222222-2222-4222-8222-222222222222", state: "rejected",
      enforcement_class: "judgment_advisory", enforcement_status: "blocked",
      binding_moment: "session_start", reason: "A rejected rule.",
      updated_at: "2026-09-10T08:00:00.000Z" },
  ],
  "ops.rule_pack": [
    { pack: "engineering-git", title: "Engineering and git", source: "doctrine",
      updated_at: "2026-09-16T07:00:00.000Z" },
  ],
  "ops.rule_enforcement_point": [
    { rule_id: "11111111-1111-4111-8111-111111111111", control_key: "gates.registered",
      implementation_ref: "ci.sh", enforcement_class: "deny_gate", installed: true,
      verified_at: "2026-09-16T07:30:00.000Z" },
  ],
  "ops.rule_control_binding": [
    { rule_id: "11111111-1111-4111-8111-111111111111", control_key: "gates.registered",
      bound_at: "2026-09-16T07:20:00.000Z" },
  ],
  "ops.rule_load_layer": [
    { rule_id: "11111111-1111-4111-8111-111111111111", short_id: "015183f5",
      load_layer: "pack", scope: "shared", packs: ["engineering-git"] },
  ],
  "public.doctrine_edge": [
    { source_section_id: "aaaaaaaa-0000-4000-8000-000000000001",
      target_section_id: "aaaaaaaa-0000-4000-8000-000000000002",
      edge_type: "REFINES", acyclic: true, retired_by: null,
      created_at: "2026-09-14T00:00:00.000Z" },
  ],
  "public.doctrine_link": [
    { source_section_id: "aaaaaaaa-0000-4000-8000-000000000001", target_kind: "rule",
      target_id: "11111111-1111-4111-8111-111111111111", role: "citation",
      created_at: "2026-09-14T00:00:00.000Z" },
  ],
  "ops.v_service_environment_health": [
    { service_key: "dealroom-worker", environment: "production", health: "healthy",
      freshness_state: "fresh", observed_at: "2026-09-16T11:45:00.000Z",
      source_ref: "ops.run" },
  ],
  "ops.v_job_run": [
    { service_key: "record-exporter", environment: "production", state: "succeeded",
      observed_at: "2026-09-16T11:40:00.000Z", source_ref: "ops.run" },
  ],
  "ops.workflow_acceptance": [
    { workflow_key: "control-plane-release", workflow_version: 1, mode: "shadow",
      status: "observed", receipt_ref: "receipt:cp-1", created_at: "2026-09-16T06:00:00.000Z" },
  ],
  "ops.v_rule_enforcement_status": [
    { rule_id: "11111111-1111-4111-8111-111111111111", policy_status: "active",
      enforcement_status: "hard_enforced", installed_controls: ["gates.registered"],
      approved_and_activated_at: "2026-09-01T00:00:00.000Z" },
  ],
};

function keyFor(sql) {
  for (const leg of ATLAS_LEGS) if (sql === leg.sql) return leg.sourceRef;
  return null;
}

/** Fake client keyed on the leg SQL alone — no database, no schema knowledge. */
function makeClient({ throwOn = null, throwCode = "ECONNREFUSED", rows = ROWS } = {}) {
  const seen = [];
  return {
    seen,
    async query(sql, params) {
      seen.push({ sql, params });
      const key = keyFor(sql);
      assert.ok(key, `unmapped leg SQL: ${String(sql).slice(0, 80)}`);
      if (throwOn === key) throw Object.assign(new Error("leg down"), { code: throwCode });
      const limit = params[params.length - 1];
      return { rows: (rows[key] || []).slice(0, limit) };
    },
  };
}

const read = (overrides = {}) => readAtlasInventoryGraph({
  client: makeClient(), actor: JOE, correlationId: CORRELATION, now: NOW,
  limit: ATLAS_LIMIT_MAX, ...overrides,
});

const nodeById = (payload, id) => payload.nodes.find((item) => item.id === id);
const hasEdge = (payload, from, to, type) =>
  payload.edges.some((item) => item.from === from && item.to === to &&
    (type === undefined || item.type === type));

test("the path is the one contract constant and every leg SQL is a read", () => {
  assert.equal(ATLAS_GRAPH_PATH, "/api/v1/atlas-graph");
  assert.ok(ATLAS_LEGS.length >= 16, `expected every declared leg, saw ${ATLAS_LEGS.length}`);
  for (const leg of ATLAS_LEGS) {
    assert.equal(assertReadOnly(leg.sql), true, `${leg.key} sql must be a bare select`);
    assert.ok(["installed", "observed"].includes(leg.layer), `${leg.key} layer`);
  }
  // The predicate has to be able to fail, or it proves nothing.
  assert.equal(assertReadOnly("update ops.service set name='x'"), false);
  assert.equal(assertReadOnly("select 1 from ops.service for update"), false);
  assert.equal(assertReadOnly("select 1 from t where true; delete from t"), false);
  // ...and must NOT fire on the column names every leg legitimately selects.
  assert.equal(assertReadOnly("select updated_at, created_at from t"), true);
});

test("the declared layer is built from the bundled registries and nothing else", async () => {
  const payload = await read({ layer: "declared" });
  assert.deepEqual(payload.layer, ["declared"]);
  // layer=declared performs NO database work at all.
  const client = makeClient();
  await readAtlasInventoryGraph({ client, actor: JOE, correlationId: CORRELATION, now: NOW, layer: "declared", limit: ATLAS_LIMIT_MAX });
  assert.equal(client.seen.length, 0, "the declared layer must not touch the database");

  const verbs = payload.nodes.filter((item) => item.class === "verb");
  const mutations = payload.nodes.filter((item) => item.class === "mutation");
  const surfaces = payload.nodes.filter((item) => item.class === "surface");
  assert.equal(verbs.length, Object.keys(TOOLS).length);
  assert.equal(mutations.length, Object.keys(SCAC_MUTATION_OPERATIONS).length);
  assert.equal(surfaces.length, AUTHENTICATED_SURFACES.length);
  assert.equal(payload.version.declared_counts.verbs, Object.keys(TOOLS).length);
  assert.equal(payload.version.declared_counts.mutations, Object.keys(SCAC_MUTATION_OPERATIONS).length);
  for (const item of [...verbs, ...mutations, ...surfaces]) {
    assert.equal(item.layer, "declared");
    assert.equal(item.evidence, "declared");
  }
  // A known WRITE verb carries its verb -> mutation edge, drawn from the
  // registry's own ingress_key rather than a name guess.
  assert.equal(TOOLS["add-loop"].write, true);
  assert.equal(SCAC_MUTATION_OPERATIONS["add-loop"].ingress_key, "mcp-tool:add-loop");
  assert.equal(hasEdge(payload, "verb:add-loop", "mutation:add-loop", "mutates_through"), true);
  assert.equal(nodeById(payload, "verb:add-loop").status, "write");
  assert.equal(nodeById(payload, "verb:find").status, "read");
  // mutation -> source module, and op -> op delegation.
  const source = SCAC_MUTATION_OPERATIONS["add-loop"].source_locator;
  assert.equal(hasEdge(payload, "mutation:add-loop", `module:${source}`, "implemented_in"), true);
  assert.equal(hasEdge(payload, "mutation:morning-brief", "mutation:loop-board", "delegates_to"), true);
  // A wildcard delegation names no operation, so it must draw no edge.
  assert.deepEqual(SCAC_MUTATION_OPERATIONS["call-verb"].delegates_to, ["*registered_operation"]);
  assert.equal(payload.edges.some((item) => item.from === "mutation:call-verb" && item.type === "delegates_to"), false);
});

test("installed legs produce service nodes, service to service edges and honest status", async () => {
  const payload = await read();
  const worker = nodeById(payload, "service:dealroom-worker");
  assert.equal(worker.class, "service");
  assert.equal(worker.layer, "installed");
  assert.equal(worker.source_ref, "ops.service");
  assert.equal(worker.status, "critical");
  assert.equal(worker.retired_at, null);
  assert.equal(hasEdge(payload, "service:dealroom-worker", "service:record-exporter", "depends_on"), true);
  assert.equal(hasEdge(payload, "service:dealroom-worker", "service_environment:dealroom-worker/production", "runs_in"), true);
  assert.equal(nodeById(payload, "job_definition:calendar-prebrief").status, "enabled");
  assert.equal(nodeById(payload, "rule_pack:engineering-git").class, "rule_pack");
  assert.equal(hasEdge(payload, "rule:11111111-1111-4111-8111-111111111111", "control:gates.registered", "enforced_by"), true);
  assert.equal(hasEdge(payload, "rule:11111111-1111-4111-8111-111111111111", "control:gates.registered", "bound_to"), true);
  assert.equal(hasEdge(payload, "rule:11111111-1111-4111-8111-111111111111", "rule_pack:engineering-git", "loaded_in_pack"), true);
  assert.equal(hasEdge(payload, "doctrine_section:aaaaaaaa-0000-4000-8000-000000000001",
    "doctrine_section:aaaaaaaa-0000-4000-8000-000000000002", "REFINES"), true);
  assert.equal(hasEdge(payload, "doctrine_section:aaaaaaaa-0000-4000-8000-000000000001",
    "rule:11111111-1111-4111-8111-111111111111", "citation"), true);
  // A node nothing points at is flagged, not dropped.
  const orphan = payload.nodes.find((item) => item.class === "surface");
  assert.equal(orphan.unlinked, true);
  assert.equal(nodeById(payload, "service:dealroom-worker").unlinked, false);
});

test("observed evidence attaches to a node and never invents a declared fact", async () => {
  const payload = await read();
  const environment = nodeById(payload, "service_environment:dealroom-worker/production");
  assert.equal(environment.layer, "installed", "evidence never changes the layer");
  assert.equal(environment.evidence, "observed");
  assert.equal(environment.observed_at, "2026-09-16T11:45:00.000Z");
  assert.equal(environment.observed_status, "healthy");
  assert.equal(environment.observed_source_ref, "ops.run");
  const exporter = nodeById(payload, "service:record-exporter");
  assert.equal(exporter.evidence, "observed");
  assert.equal(exporter.observed_at, "2026-09-16T11:40:00.000Z");
  const job = nodeById(payload, "job_definition:calendar-prebrief");
  assert.equal(job.observed_status, "live:succeeded");
  const rule = nodeById(payload, "rule:11111111-1111-4111-8111-111111111111");
  assert.equal(rule.observed_status, "active");
  // A workflow has no declared node in this bundle, so it is born OBSERVED and
  // stays labelled that way rather than being promoted to a declared fact.
  const workflow = nodeById(payload, "workflow:control-plane-release");
  assert.equal(workflow.layer, "observed");
  assert.equal(workflow.evidence, "observed");
  assert.equal(payload.index.observed.workflow.includes("workflow:control-plane-release"), true);
  // Declared nodes that nothing observed keep evidence "declared".
  assert.equal(nodeById(payload, "verb:find").evidence, "declared");
});

test("a 42501 leg becomes unavailable and every other leg still stands", async () => {
  const denied = await readAtlasInventoryGraph({
    client: makeClient({ throwOn: "ops.service", throwCode: "42501" }),
    actor: JOE, correlationId: CORRELATION, now: NOW, limit: ATLAS_LIMIT_MAX,
  });
  const entry = denied.coverage.find((item) => item.source_ref === "ops.service");
  assert.equal(entry.complete, false);
  assert.equal(entry.missing_reason, "DEPENDENCY_UNAVAILABLE");
  assert.equal(entry.node_count, 0);
  assert.equal(nodeById(denied, "service:dealroom-worker"), undefined);
  assert.equal(denied.source.freshness, "unknown");
  assert.match(denied.source.safe_explanation, /INCOMPLETE, not empty/);
  // The rest of the atlas is intact: a grant gap on one relation never empties it.
  assert.ok(nodeById(denied, "rule_pack:engineering-git"));
  assert.ok(nodeById(denied, "verb:add-loop"));
  assert.equal(denied.coverage.filter((item) => item.complete).length >= 10, true);

  for (const code of ["42501", "ECONNREFUSED", "08000", "57P01", "DEPENDENCY_UNAVAILABLE"]) {
    const payload = await readAtlasInventoryGraph({
      client: makeClient({ throwOn: "ops.rule_pack", throwCode: code }),
      actor: JOE, correlationId: CORRELATION, now: NOW, limit: ATLAS_LIMIT_MAX,
    });
    assert.equal(payload.coverage.find((item) => item.source_ref === "ops.rule_pack").missing_reason,
      "DEPENDENCY_UNAVAILABLE", `${code} must be a dependency`);
  }
  // An unrecognized failure stays OUR defect and is not laundered into a dependency.
  const internal = await readAtlasInventoryGraph({
    client: makeClient({ throwOn: "ops.rule_pack", throwCode: "22P02" }),
    actor: JOE, correlationId: CORRELATION, now: NOW, limit: ATLAS_LIMIT_MAX,
  });
  assert.equal(internal.coverage.find((item) => item.source_ref === "ops.rule_pack").missing_reason,
    "INTERNAL_ERROR");
});

test("retired nodes are hidden by default and returned on request", async () => {
  const hidden = await read();
  assert.equal(nodeById(hidden, "service:md-renderer"), undefined);
  assert.equal(nodeById(hidden, "rule:22222222-2222-4222-8222-222222222222"), undefined);
  assert.equal(hidden.include_retired, false);
  assert.equal(Object.values(hidden.index.installed).flat().includes("service:md-renderer"), false);

  const shown = await read({ include_retired: "true" });
  assert.equal(shown.include_retired, true);
  const retired = nodeById(shown, "service:md-renderer");
  assert.equal(retired.status, "retired");
  assert.equal(retired.retired_at, "2026-08-19T00:00:00.000Z");
  assert.equal(nodeById(shown, "rule:22222222-2222-4222-8222-222222222222").status, "retired");
  assert.equal(Object.values(shown.index.installed).flat().includes("service:md-renderer"), true);
  await assert.rejects(() => read({ include_retired: "maybe" }),
    (error) => error.code === "AUTHORIZATION_REFUSED");
});

test("q filters case-insensitively over id, key and title", async () => {
  const payload = await read({ q: "DEALROOM-WORKER" });
  assert.equal(payload.q, "dealroom-worker");
  assert.ok(payload.nodes.length > 0);
  for (const item of payload.nodes) {
    assert.match(`${item.id} ${item.key} ${item.title ?? ""}`.toLowerCase(), /dealroom-worker/);
  }
  assert.ok(nodeById(payload, "service:dealroom-worker"));
  assert.equal(nodeById(payload, "rule_pack:engineering-git"), undefined);
  // The edge list narrows with the node page: no edge may dangle.
  const ids = new Set(payload.nodes.map((item) => item.id));
  for (const item of payload.edges) {
    assert.ok(ids.has(item.from) && ids.has(item.to), `dangling edge ${item.from} -> ${item.to}`);
  }
  assert.equal((await read({ q: "no-such-thing-anywhere" })).nodes.length, 0);
});

test("the cursor round trips: page two continues page one and repeats nothing", async () => {
  const first = await read({ limit: 50 });
  assert.equal(first.nodes.length, 50);
  assert.equal(first.truncated, true);
  assert.equal(decodeCursor(first.next_cursor), first.nodes[49].id);
  assert.equal(encodeCursor(first.nodes[49].id), first.next_cursor);

  const seen = [...first.nodes.map((item) => item.id)];
  let cursor = first.next_cursor;
  let pages = 1;
  while (cursor) {
    const page = await read({ limit: 50, cursor });
    for (const item of page.nodes) {
      assert.equal(seen.includes(item.id), false, `${item.id} repeated across pages`);
      seen.push(item.id);
    }
    cursor = page.next_cursor;
    pages += 1;
    assert.ok(pages < 200, "paging must terminate");
  }
  const full = await read();
  assert.equal(full.truncated, false);
  assert.equal(full.next_cursor, null);
  assert.deepEqual(seen.sort(), full.nodes.map((item) => item.id).sort());
  assert.equal(new Set(seen).size, seen.length);
});

test("coverage always lists the four known gaps, complete run or not", async () => {
  for (const payload of [await read(), await readAtlasInventoryGraph({
    client: makeClient({ throwOn: "ops.service" }), actor: JOE,
    correlationId: CORRELATION, now: NOW, limit: ATLAS_LIMIT_MAX,
  })]) {
    const gaps = payload.coverage.filter((entry) =>
      ["no_grant", "not_in_bundle", "column_not_granted", "no_relation"].includes(entry.missing_reason));
    assert.equal(gaps.length, 4, "all four structural gaps must be published every time");
    assert.deepEqual(gaps.map((entry) => [entry.source_ref, entry.evidence_class, entry.missing_reason]),
      ATLAS_KNOWN_GAPS.map((gap) => [gap.source_ref, gap.evidence_class, gap.missing_reason]));
    for (const gap of gaps) assert.equal(gap.complete, false);
  }
  // The four are exactly the ones the frozen design named, and no fifth crept in.
  assert.deepEqual(ATLAS_KNOWN_GAPS.map((gap) => gap.source_ref), [
    "ops.scac_mutation_registry_entry",
    "ops/config/hooks.json + control-plane-workflows.v1.json + services.json",
    "public.tool_call verb name",
    "verb->service",
  ]);
  // Every reachable leg reports its own row with counts.
  const payload = await read();
  for (const leg of ATLAS_LEGS) {
    const entry = payload.coverage.find((item) => item.source_ref === leg.sourceRef);
    assert.ok(entry, `${leg.sourceRef} missing from coverage`);
    assert.equal(entry.evidence_class, leg.layer);
    assert.equal(entry.complete, true);
    assert.equal(entry.missing_reason, null);
  }
  const service = payload.coverage.find((item) => item.source_ref === "ops.service");
  assert.equal(service.node_count, 3);
  assert.equal(payload.coverage.find((item) => item.source_ref === "ops.service_dependency").edge_count, 1);
});

test("the bundle digest tracks the declared bundle and not the clock", async () => {
  const a = await read();
  const b = await readAtlasInventoryGraph({
    client: makeClient(), actor: JOE, correlationId: CORRELATION,
    now: () => new Date("2026-09-17T12:00:00.000Z"), limit: ATLAS_LIMIT_MAX,
  });
  assert.equal(a.version.bundle_digest, b.version.bundle_digest, "the clock must not move the digest");
  assert.notEqual(a.observed_at, b.observed_at);
  assert.match(a.version.bundle_digest, /^[0-9a-f]{64}$/);
  assert.equal(a.version.bundle_digest, await bundleDigest(DECLARED_LAYER));
  assert.equal(a.version.registry_version, CURRENT_REGISTRY_VERSION);

  // Changing the TOOLS key set MUST move the digest. The tools list is injectable
  // for exactly this: a digest that could not change would prove nothing.
  const fewer = Object.fromEntries(Object.entries(TOOLS).slice(0, 10));
  const changed = await read({ tools: fewer });
  assert.notEqual(changed.version.bundle_digest, a.version.bundle_digest);
  assert.equal(changed.version.declared_counts.verbs, 10);
  assert.equal(await bundleDigest(buildDeclaredLayer(fewer)), changed.version.bundle_digest);
});

test("the closed parameter set is enforced and refusals are typed", async () => {
  assert.equal((await read({ limit: 99999 })).limit, ATLAS_LIMIT_MAX);
  assert.equal((await read({ limit: null })).limit, 500);
  assert.deepEqual((await read({ layer: "installed,observed" })).layer, ["installed", "observed"]);
  assert.deepEqual((await read({ layer: "all" })).layer, ["declared", "installed", "observed"]);
  const refusal = async (overrides, code) => {
    await assert.rejects(() => read(overrides), (error) => error.code === code, `${code} expected`);
  };
  await refusal({ actor: { slug: "mallory" } }, "AUTHORIZATION_REFUSED");
  await refusal({ actor: {} }, "AUTHORIZATION_REFUSED");
  await refusal({ tenant: "other-tenant" }, "TENANT_SCOPE_REFUSED");
  await refusal({ correlationId: "" }, "INTERNAL_ERROR");
  await refusal({ limit: 0 }, "AUTHORIZATION_REFUSED");
  await refusal({ limit: "many" }, "AUTHORIZATION_REFUSED");
  await refusal({ layer: "inferred" }, "AUTHORIZATION_REFUSED");
  await refusal({ cursor: "not-base64-json" }, "AUTHORIZATION_REFUSED");
  await refusal({ cursor: encodeCursor("verb:no-such-verb") }, "AUTHORIZATION_REFUSED");
  // An unreadable leg does not soften an authorization refusal.
  await assert.rejects(() => readAtlasInventoryGraph({
    client: makeClient({ throwOn: "ops.service" }), actor: { slug: "mallory" },
    correlationId: CORRELATION, now: NOW,
  }), (error) => error.code === "AUTHORIZATION_REFUSED");
});

// -----------------------------------------------------------------------------
// The check the fake client cannot make. Every leg runs as carr_reader (the role
// behind DATABASE_URL_READER), so a leg may only touch a relation the schema
// actually gives SELECT to that role. The allowlist is DERIVED from db/schema.sql,
// not written here, so it tracks the schema instead of a belief about it: a future
// leg that reads an unreadable relation fails HERE rather than 42501-ing on every
// production request behind a green suite.
const REPO_ROOT = fileURLToPath(new URL("../../", import.meta.url));

function readerReadableRelations() {
  const schema = readFileSync(`${REPO_ROOT}db/schema.sql`, "utf8");
  const relations = new Set();
  for (const match of schema.matchAll(/^grant select on table ([a-z0-9_.]+) to carr_reader;$/gim)) {
    const name = match[1].toLowerCase();
    relations.add(name.includes(".") ? name : `public.${name}`);
  }
  return relations;
}

/** Relations named after `from` or `join`, normalized to a schema-qualified name. */
function relationsIn(sql) {
  const found = new Set();
  for (const match of String(sql).matchAll(/\b(?:from|join)\s+([a-z0-9_.]+)/gi)) {
    const name = match[1].toLowerCase();
    found.add(name.includes(".") ? name : `public.${name}`);
  }
  return found;
}

test("every leg reads only relations the schema grants to carr_reader", () => {
  const readable = readerReadableRelations();
  // Guard the derivation itself: an empty or tiny allowlist would make this test
  // vacuous, and a regex that silently stopped matching is exactly how a check
  // like this rots into a no-op.
  assert.ok(readable.size > 100, `derived allowlist looks wrong: ${readable.size} relations`);
  assert.equal(readable.has("ops.service"), true);
  assert.equal(readable.has("ops.v_rule_enforcement_status"), true);
  // The two relations the frozen design excluded are genuinely unreadable here.
  assert.equal(readable.has("public.rule"), false);
  assert.equal(readable.has("ops.scac_mutation_registry_entry"), false);

  for (const leg of ATLAS_LEGS) {
    const relations = [...relationsIn(leg.sql)];
    assert.ok(relations.length > 0, `${leg.key} names no relation`);
    for (const relation of relations) {
      assert.ok(readable.has(relation),
        `${leg.key} reads ${relation}, which db/schema.sql does not grant to carr_reader`);
    }
  }
  // The extractor has to be able to catch the defect it was written for.
  assert.equal(relationsIn("select 1 from ops.rule_admission a join rule r on r.id = a.rule_id").has("public.rule"), true);
  assert.equal(relationsIn("select 1 from ops.scac_mutation_registry_entry").has("ops.scac_mutation_registry_entry"), true);
});

test("the module imports nothing that would drag a store or a route into a read", () => {
  const source = readFileSync(`${REPO_ROOT}mcp-server/src/atlas-inventory-graph.v5.js`, "utf8");
  const imports = [...source.matchAll(/^import\s[^;]*?from\s+"([^"]+)";$/gm)].map((match) => match[1]);
  assert.ok(imports.length > 0, "no imports found — the extractor is broken");
  for (const specifier of imports) {
    assert.equal(specifier.includes("dealroom-web"), false, `${specifier} is a route, not a registry`);
    assert.equal(/(^|\/)index\.js$/.test(specifier), false, `${specifier} is the server entry`);
    assert.equal(/-store\.v5\.js$/.test(specifier), false, `${specifier} is a store`);
    assert.equal(specifier.includes("ops/config/"), false, `${specifier} is not in the bundle`);
    assert.equal(specifier.endsWith(".json"), false, `${specifier} is a config file`);
  }
  // And it stays a library: no interpreter line and no self-execution guard, so
  // the sealed script-entrypoint frontier does not move.
  assert.equal(source.startsWith("#!"), false);
  assert.equal(source.includes("import.meta.url ==="), false);
  assert.equal(source.includes("process.argv"), false);
});
