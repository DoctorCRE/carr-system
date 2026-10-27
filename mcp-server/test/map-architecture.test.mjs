// Test map architecture source and machine contract reads.
import assert from "node:assert/strict";
import test from "node:test";

import { doctrineTools } from "../src/doctrine.js";

class ToolError extends Error {
  constructor(payload) {
    super(payload.error);
    this.payload = payload;
  }
}

const tools = doctrineTools({
  withEnvelope: async () => {},
  writeEvent: async () => {},
  ToolError,
});
const handler = tools["map-architecture"].handler;

const sourceRows = [
  {
    document_slug: "maps-and-demographics",
    section_key: "ai-built-interactive-tour-maps-source-rendering-routing-and-promotion-gate",
    title: "AI-built interactive tour maps",
    version: 3,
    body: { text: "Canonical records, reviewed GIS layers, deterministic routing." },
  },
  {
    document_slug: "carr-workspace-bduf",
    section_key: "s13-ipad-application-and-tour-mode",
    title: "iPad application and Tour Mode",
    version: 2,
    body: { text: "Validate every address, preserve locked appointments, and keep an offline list." },
  },
];

function client(rows = sourceRows) {
  return {
    async query(sql) {
      if (sql.includes("from doctrine_section s")) return { rows };
      if (sql.includes("from doctrine_meta")) return { rows: [{ generation: 418 }] };
      throw new Error(`unexpected query: ${sql}`);
    },
  };
}

test("map-architecture returns the two live sources and machine contract", async () => {
  const result = await handler(client(), { slug: "joe-local" }, {});
  assert.equal(result.ok, true);
  assert.equal(result.architecture, "carr-map-tour-v1");
  assert.equal(result.doctrine_generation, 418);
  assert.equal(result.sources.length, 2);
  assert.deepEqual(result.sources.map(item => item.version), [3, 2]);
  assert.equal(result.contract.path, "workspace/contracts/market-map-route-planning.v1.json");
  assert.equal(result.contract.version, "1.2.0");
  assert.deepEqual(result.method_ids, [
    "recursive_source_intake",
    "typed_domain_queries",
    "spatial_authoring_workbench",
    "deterministic_component_registry",
    "portable_geospatial_interchange",
    "entrance_level_coordinate_verification",
    "route_label_identity_separation",
    "search_and_tour_modes",
    "map_event_contract",
    "provider_rights_receipt",
    "human_promotion_receipt",
  ]);
  assert(result.sources.every(item => item.body_text.trim().length > 0));
  assert.match(result.required_workflow.join(" "), /business question.*typed domain quer.*analysis.*reviewed dataset.*deterministic map component.*navigation/i);
});

test("map-architecture fails visibly when either live section is missing", async () => {
  await assert.rejects(
    handler(client(sourceRows.slice(0, 1)), { slug: "joe-local" }, {}),
    error => error instanceof ToolError
      && error.payload.error === "map_architecture_unavailable"
      && error.payload.missing.includes("carr-workspace-bduf/s13-ipad-application-and-tour-mode"),
  );
});
