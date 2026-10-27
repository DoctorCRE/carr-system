// Load and prepare control room fixtures for presentation and safe telemetry.
const FIXTURES = {
  overview: "../../fixtures/overview.v1.json",
  service: "../../fixtures/service.v1.json",
  "work-request": "../../fixtures/work-request.v1.json",
  "plan-approval": "../../fixtures/plan-approval.v1.json",
  deployment: "../../fixtures/deployment.v1.json",
  incident: "../../fixtures/incident.v1.json",
  audit: "../../fixtures/audit.v1.json"
};

const FIXTURE_SCHEMA = "../../contracts/fixture-schema.v1.json";

export const SURFACES = [
  {id: "overview", label: "Overview", group: "Overview"},
  {id: "service", label: "Service", group: "Systems"},
  {id: "work-request", label: "Work Request", group: "Work"},
  {id: "plan-approval", label: "Plan & Approval", group: "Work"},
  {id: "deployment", label: "Deployment", group: "Changes"},
  {id: "incident", label: "Incident", group: "Reliability"},
  {id: "audit", label: "Audit", group: "Governance"}
];

export const SCENARIOS = ["normal", "loading", "empty", "partial", "stale", "offline", "unauthorized", "conflict", "refusal", "retry"];

function removePath(target, rawPath) {
  const path = rawPath.startsWith("data.") ? rawPath.split(".") : ["data", ...rawPath.split(".")];
  const key = path.pop();
  let parent = target;
  for (const segment of path) {
    if (parent == null) return;
    parent = parent[segment];
  }
  if (Array.isArray(parent) && /^\d+$/.test(key)) parent.splice(Number(key), 1);
  else if (parent && Object.hasOwn(parent, key)) delete parent[key];
}

function removeHealthyClaims(value, freshnessState) {
  if (Array.isArray(value)) {
    for (const item of value) removeHealthyClaims(item, freshnessState);
    return;
  }
  if (!value || typeof value !== "object") return;
  for (const [key, item] of Object.entries(value)) {
    if (["status", "result"].includes(key) && item === "healthy") value[key] = "unknown";
    else if (key === "freshness" && typeof item === "string") value[key] = freshnessState;
    else removeHealthyClaims(item, freshnessState);
  }
}

export function applyScenario(source, scenario) {
  const fixture = structuredClone(source);
  const config = fixture.scenarios[scenario];
  if (!config) throw new Error(`Unknown prototype scenario: ${scenario}`);
  for (const path of config.withhold || []) removePath(fixture, path);
  if (["stale", "offline", "conflict"].includes(scenario)) {
    const freshnessState = scenario === "stale" ? "stale" : "unknown";
    fixture.meta.freshness.state = freshnessState;
    removeHealthyClaims(fixture.data, freshnessState);
  }
  if (config.health_override) removeHealthyClaims(fixture.data, config.health_override);
  return fixture;
}

const SECRET_VALUE = /(?:CARR_SECRET_CANARY_[A-Za-z0-9_-]+|postgres(?:ql)?:\/\/[^\s]+|Bearer\s+[A-Za-z0-9._-]+)/i;

function resolveSchema(schema, root) {
  if (!schema?.$ref) return schema;
  return schema.$ref.replace(/^#\//, "").split("/").reduce((node, key) => node[key.replaceAll("~1", "/").replaceAll("~0", "~")], root);
}

function schemaForValue(value, candidates, root) {
  const scored = candidates.map(candidate => {
    const schema = resolveSchema(candidate, root);
    if (schema.type === "null") return {schema, score: value === null ? 0 : Number.MAX_SAFE_INTEGER};
    if (schema.type === "object" && value && typeof value === "object" && !Array.isArray(value)) {
      const keys = new Set(Object.keys(schema.properties || {}));
      return {schema, score: Object.keys(value).filter(key => !keys.has(key)).length};
    }
    if (schema.type === "array") return {schema, score: Array.isArray(value) ? 0 : Number.MAX_SAFE_INTEGER};
    return {schema, score: typeof value === schema.type ? 0 : Number.MAX_SAFE_INTEGER};
  });
  return scored.sort((a, b) => a.score - b.score)[0]?.schema;
}

function serializeDeclared(value, rawSchema, root) {
  let schema = resolveSchema(rawSchema, root);
  if (schema.oneOf) schema = schemaForValue(value, schema.oneOf, root);
  if (value === null || value === undefined) return value;
  if (schema.type === "array") return Array.isArray(value) ? value.map(item => serializeDeclared(item, schema.items, root)) : [];
  if (schema.type === "object") {
    const result = {};
    for (const [key, childSchema] of Object.entries(schema.properties || {})) {
      if (Object.hasOwn(value, key)) result[key] = serializeDeclared(value[key], childSchema, root);
    }
    return result;
  }
  return typeof value === "string" && SECRET_VALUE.test(value) ? "[REDACTED]" : value;
}

export function prepareFixtureForPresentation(source, scenario, schema) {
  if (!schema) throw new Error("Fixture schema is required for closed presentation serialization");
  return serializeDeclared(applyScenario(source, scenario), schema, schema);
}

export function toSafeTelemetry(fixture, scenario) {
  return {
    event: "prototype.view_rendered",
    fixture_id: fixture.fixture_id,
    surface: fixture.surface,
    scenario,
    environment_scope: fixture.meta.environment_scope,
    freshness_state: fixture.meta.freshness.state,
    correlation_id: fixture.meta.correlation_id
  };
}

export async function loadFixture(surface) {
  const path = FIXTURES[surface];
  if (!path) throw new Error("Unknown prototype surface");
  const [fixtureResponse, schemaResponse] = await Promise.all([
    fetch(new URL(path, import.meta.url), {cache: "no-store"}),
    fetch(new URL(FIXTURE_SCHEMA, import.meta.url), {cache: "no-store"})
  ]);
  if (!fixtureResponse.ok) throw new Error(`Fixture read failed: ${fixtureResponse.status}`);
  if (!schemaResponse.ok) throw new Error(`Fixture schema read failed: ${schemaResponse.status}`);
  return {fixture: await fixtureResponse.json(), schema: await schemaResponse.json()};
}
