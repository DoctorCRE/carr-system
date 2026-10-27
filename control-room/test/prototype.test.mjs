// Test synthetic control room fixtures and prototype constraints.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const FIXTURE_DIR = path.join(ROOT, "fixtures");
const expectedSurfaces = ["overview", "service", "work-request", "plan-approval", "deployment", "incident", "audit"];
const expectedScenarios = ["normal", "loading", "empty", "partial", "stale", "offline", "unauthorized", "conflict", "refusal", "retry"];
const fixtures = Object.fromEntries(fs.readdirSync(FIXTURE_DIR).filter(name => name.endsWith(".json")).map(name => {
  const value = JSON.parse(fs.readFileSync(path.join(FIXTURE_DIR, name), "utf8"));
  return [value.surface, value];
}).filter(([surface]) => surface));

test("exactly seven canonical synthetic twins exist", () => {
  assert.deepEqual(Object.keys(fixtures).sort(), [...expectedSurfaces].sort());
  for (const surface of expectedSurfaces) {
    const fixture = fixtures[surface];
    assert.equal(fixture.schema_version, "1.0.0");
    assert.equal(fixture.synthetic, true);
    assert.match(fixture.route, /^\//);
    assert.equal(fixture.default_scenario, "normal");
    assert.deepEqual(Object.keys(fixture.scenarios), expectedScenarios);
    assert.ok(fixture.meta.source.ref);
    assert.ok(fixture.meta.observed_at);
    assert.ok(fixture.meta.freshness.state);
    assert.ok(fixture.meta.redaction.classification);
    assert.ok(fixture.meta.correlation_id);
  }
});

test("security-boundary fixtures are classified separately from the seven canonical twins", () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, "contracts", "phase0-manifest.v1.json"), "utf8"));
  const canonicalFiles = expectedSurfaces.map(surface => `${surface}.v1.json`).sort();
  assert.deepEqual([...manifest.artifact_groups.canonical_twins].sort(), canonicalFiles);
  assert.deepEqual(manifest.artifact_groups.security_fixtures, ["tenant-boundary.v1.json", "identity-sponsorship.v1.json"]);
  assert.deepEqual(fs.readdirSync(FIXTURE_DIR).filter(name => name.endsWith(".json")).sort(), [...canonicalFiles, ...manifest.artifact_groups.security_fixtures].sort());
});

test("fixtures contain no secret-shaped material or client payload", () => {
  const text = fs.readdirSync(FIXTURE_DIR).map(name => fs.readFileSync(path.join(FIXTURE_DIR, name), "utf8")).join("\n");
  for (const pattern of [/postgres:\/\//i, /Bearer\s+[A-Za-z0-9._-]+/i, /BEGIN [A-Z ]*PRIVATE KEY/i, /client_email\s*:/i, /password\s*:/i]) {
    assert.doesNotMatch(text, pattern);
  }
});

test("collector outage and planned staging are honestly Unknown", () => {
  const overview = fixtures.overview;
  const production = overview.data.environments.find(item => item.id === "env-production");
  const staging = overview.data.environments.find(item => item.id === "env-staging");
  assert.equal(production.status, "unknown");
  assert.equal(production.freshness, "unknown");
  assert.equal(staging.status, "unknown");
  assert.equal(staging.freshness, "missing");
  assert.equal(overview.data.monitoring_gap.active, true);
  assert.ok(overview.scenarios.stale.message.includes("Unknown"));
  assert.ok(overview.scenarios.offline.message.includes("No action controls"));
});

test("material plan revision invalidates prior approval", () => {
  const plan = fixtures["plan-approval"].data;
  assert.equal(plan.current_revision.revision, 2);
  assert.equal(plan.prior_revision.state, "superseded");
  assert.equal(plan.approval.state, "invalidated");
  assert.notEqual(plan.current_revision.hash, plan.approval.plan_hash);
  assert.match(plan.phase0_notice, /No Approve, Deploy, Grant, or Execute route/);
});

test("deployment cannot complete without full verification", () => {
  const deployment = fixtures.deployment.data;
  assert.equal(deployment.state, "verifying");
  assert.equal(deployment.browser_owns_operation, false);
  assert.equal(deployment.actuator.registered, true);
  assert.equal(deployment.actuator.phase0_mode, "metadata_only");
  assert.ok(deployment.verification.some(item => item.result === "pending"));
  assert.match(deployment.completion_rule, /every named golden workflow pass/);
});

test("incident keeps facts and hypotheses separate and blocks early resolution", () => {
  const incident = fixtures.incident.data;
  assert.ok(incident.facts.length > 0);
  assert.ok(incident.hypotheses.length > 0);
  assert.ok(incident.facts.every(item => item.source));
  assert.ok(incident.hypotheses.every(item => item.confidence === "unconfirmed"));
  assert.equal(incident.resolution_gate.met, false);
});

test("audit chain is complete, immutable, and redacted", () => {
  const audit = fixtures.audit.data;
  assert.deepEqual(audit.chain.map(item => item.stage), ["origin", "agent_session", "plan_revision", "approval", "execution", "verification", "release_or_incident"]);
  assert.equal(audit.append_only, true);
  assert.equal(audit.mutation_controls.update, false);
  assert.equal(audit.mutation_controls.delete, false);
  assert.equal(audit.redaction_declaration.secret_values_present, false);
  for (let i = 1; i < audit.chain.length; i += 1) assert.equal(audit.chain[i].causation_id, audit.chain[i - 1].event_id);
});

test("prototype source has no mutation transport or production action control", () => {
  const js = fs.readFileSync(path.join(ROOT, "public/js/app.js"), "utf8") + fs.readFileSync(path.join(ROOT, "public/js/client.js"), "utf8");
  const html = fs.readFileSync(path.join(ROOT, "public/index.html"), "utf8");
  assert.doesNotMatch(js, /method\s*:\s*["'](?:POST|PUT|PATCH|DELETE)["']/i);
  assert.doesNotMatch(js, /type\s*:\s*["']submit["']/i);
  assert.doesNotMatch(js, /\/mcp\b/);
  assert.match(html, /Read-only/);
});

test("responsive shell and accessibility requirements are represented", () => {
  const html = fs.readFileSync(path.join(ROOT, "public/index.html"), "utf8");
  const css = fs.readFileSync(path.join(ROOT, "public/css/app.css"), "utf8");
  assert.match(html, /Skip to operational view/);
  assert.match(html, /aria-live="polite"/);
  assert.match(html, /Fixture-backed · Read-only/);
  assert.match(css, /@media \(max-width: 760px\)/);
  assert.match(css, /prefers-reduced-motion/);
  assert.match(css, /prefers-contrast: more/);
  assert.match(css, /:focus-visible/);
}
);
