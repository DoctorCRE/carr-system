// Seal staging foundation assurance evidence.
import { readFile } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { digest } from "../src/artifact-trust.js";
import {
  BENCHMARK_PAYLOAD_DOMAIN_TAG,
} from "../src/benchmark-minimum.v5.js";
import {
  FOUNDATION_ASSURANCE_COMPARATORS,
  FOUNDATION_ASSURANCE_EVIDENCE_SCHEMA,
  FOUNDATION_ASSURANCE_GITHUB_CHECKS,
  foundationAssuranceBenchmarkPayload,
  foundationAssuranceEvidenceDigest,
  sealFoundationAssuranceEvidence,
  validateFoundationAssuranceBenchmarkConfig,
} from "../src/foundation-assurance-evidence.v5.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "../..");
const CONFIG = resolve(REPO, "ops/config/foundation-assurance-benchmark.v1.json");
const SERVICES = resolve(REPO, "ops/config/services.json");
const SHA = /^[0-9a-f]{40}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const RELEASE_KEY = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/;
const FORBIDDEN_FLAGS = /(?:measurement|evidence|comparator|pass|identity|captured|time|sample)/i;

export class FoundationAssuranceSealerError extends Error {
  constructor(code, detail) { super(code); this.code = code; this.detail = detail; }
}
const fail = (code, detail) => { throw new FoundationAssuranceSealerError(code, detail); };

export function canonicalStagingOriginFromRegistry(services) {
  const serviceMatches = (services?.services || []).filter(service => service?.key === "carr-mcp");
  if (serviceMatches.length !== 1 || !Array.isArray(serviceMatches[0].environments))
    fail("canonical_staging_origin_unavailable");
  const matches = serviceMatches[0].environments
    .filter(environment => environment?.environment === "staging");
  if (matches.length !== 1 || typeof matches[0].endpoint !== "string" ||
      !/^[a-z0-9.-]+$/.test(matches[0].endpoint))
    fail("canonical_staging_origin_unavailable");
  let origin;
  try { origin = new URL(`https://${matches[0].endpoint}`); }
  catch { fail("canonical_staging_origin_unavailable"); }
  if (origin.hostname !== matches[0].endpoint || origin.port || origin.username || origin.password ||
      origin.search || origin.hash || origin.pathname !== "/")
    fail("canonical_staging_origin_unavailable");
  return origin.origin;
}

export function canonicalStagingOrigin() {
  let services;
  try { services = JSON.parse(readFileSync(SERVICES, "utf8")); }
  catch { fail("canonical_staging_origin_unavailable"); }
  return canonicalStagingOriginFromRegistry(services);
}

function evaluatorIdentity(bindings) {
  // correlation.js accepts this UUID as a request correlation and identity.js
  // derives the corresponding authenticated session as `session:<uuid>`.
  // Keeping the UUID unadorned makes the declared evaluator a session the
  // review bearer can actually occupy; the former wr95-evidence- prefix could
  // never be emitted by deriveCallIdentity and made coverage impossible.
  return Object.freeze({
    actor_id: "codex-fa-coverage",
    session_ref: `session:${bindings.idempotency_key}`,
    authority_class: "review_agent",
  });
}

export function parseFoundationAssuranceArgs(argv) {
  const allowed = new Set(["--source-sha", "--source-tree", "--staging-provider-version",
    "--final-provider-version", "--release-key", "--staging-origin", "--idempotency-key",
    "--staging-candidate-operation-id", "--staging-replacement-receipt-id",
    "--staging-replacement-source-sha"]);
  const values = {};
  for (let at = 0; at < argv.length; at += 2) {
    const flag = argv[at];
    if (typeof flag !== "string" || FORBIDDEN_FLAGS.test(flag) || !allowed.has(flag))
      fail("caller_evidence_input_refused", flag);
    if (at + 1 >= argv.length || String(argv[at + 1]).startsWith("--"))
      fail("missing_binding_value", flag);
    if (Object.hasOwn(values, flag)) fail("duplicate_binding", flag);
    values[flag] = argv[at + 1];
  }
  if ([...allowed].some(flag => !Object.hasOwn(values, flag)))
    fail("missing_required_binding");
  const parsed = {
    source_sha: values["--source-sha"], source_tree: values["--source-tree"],
    staging_provider_version: values["--staging-provider-version"],
    final_provider_version: values["--final-provider-version"],
    release_key: values["--release-key"], staging_origin: values["--staging-origin"],
    idempotency_key: values["--idempotency-key"],
    staging_candidate_operation_id: values["--staging-candidate-operation-id"],
    staging_replacement_receipt_id: values["--staging-replacement-receipt-id"],
    staging_replacement_source_sha: values["--staging-replacement-source-sha"],
  };
  if (!SHA.test(parsed.source_sha) || !SHA.test(parsed.source_tree)) fail("invalid_source_binding");
  if (!UUID.test(parsed.staging_provider_version) || !UUID.test(parsed.final_provider_version) ||
      parsed.staging_provider_version === parsed.final_provider_version) fail("invalid_provider_binding");
  if (!UUID.test(parsed.idempotency_key)) fail("invalid_idempotency_key");
  if (!UUID.test(parsed.staging_candidate_operation_id) ||
      !UUID.test(parsed.staging_replacement_receipt_id)) fail("invalid_staging_database_binding");
  if (!SHA.test(parsed.staging_replacement_source_sha)) fail("invalid_staging_database_binding");
  if (!RELEASE_KEY.test(parsed.release_key)) fail("invalid_release_key");
  let origin;
  try { origin = new URL(parsed.staging_origin); } catch { fail("invalid_staging_origin"); }
  if (origin.protocol !== "https:" || origin.username || origin.password || origin.search ||
      origin.hash || origin.pathname !== "/" || origin.origin !== canonicalStagingOrigin())
    fail("invalid_staging_origin");
  parsed.staging_origin = origin.origin;
  return Object.freeze(parsed);
}

function exactNames(rows, expected, code) {
  const actual = rows.map(row => row.name ?? row.id).sort();
  if (JSON.stringify(actual) !== JSON.stringify([...expected].sort())) fail(code, actual);
}

export function assembleFoundationAssuranceEvidence(bindings, config, acquired) {
  const closed = Object.keys(acquired).sort();
  const expected = ["browser", "comparators", "database", "github_checks", "measurements",
    "runtime_version", "sample_provenance"].sort();
  if (JSON.stringify(closed) !== JSON.stringify(expected)) fail("acquisition_shape_invalid", closed);
  exactNames(acquired.github_checks, FOUNDATION_ASSURANCE_GITHUB_CHECKS, "github_check_set_invalid");
  exactNames(acquired.comparators, FOUNDATION_ASSURANCE_COMPARATORS, "comparator_set_invalid");
  const subject = digest(["doctorcre:wr95-subject:v1", bindings.source_sha, bindings.source_tree]);
  const candidate = digest(["doctorcre:wr95-candidate:v1", bindings.final_provider_version]);
  const policy = digest(validateFoundationAssuranceBenchmarkConfig(config));
  const benchmark_payload = foundationAssuranceBenchmarkPayload(config, {
    subject_digest: subject, candidate_digest: candidate, policy_digest: policy,
    browser: acquired.browser, runtime_version: acquired.runtime_version,
    evaluator_identities: [evaluatorIdentity(bindings)],
  });
  const evidence = {
    schema_version: FOUNDATION_ASSURANCE_EVIDENCE_SCHEMA,
    source_sha: bindings.source_sha, source_tree: bindings.source_tree,
    staging_provider_version: bindings.staging_provider_version,
    final_provider_version: bindings.final_provider_version,
    release: { key: bindings.release_key, provider_version: bindings.final_provider_version,
      source_sha: bindings.source_sha, test_evidence_ref: null },
    database: acquired.database, github_checks: acquired.github_checks,
    benchmark_config_digest: digest(validateFoundationAssuranceBenchmarkConfig(config)),
    benchmark_payload,
    measurements: { ...acquired.measurements,
      benchmark_payload_digest: digest([BENCHMARK_PAYLOAD_DOMAIN_TAG, benchmark_payload]) },
    sample_provenance: acquired.sample_provenance,
    comparators: acquired.comparators,
    captured_at: new Date(acquired.measurements.captured_at_ms).toISOString(),
  };
  delete evidence.measurements.captured_at_ms;
  const evidenceDigest = foundationAssuranceEvidenceDigest(evidence);
  evidence.release.test_evidence_ref = `safe:wr95-evidence/${evidenceDigest.slice(7)}`;
  const seal = sealFoundationAssuranceEvidence(evidence, config);
  return Object.freeze({ config, evidence, seal });
}

export function stagingReleaseSource(body) {
  return body?.git_sha?.value ?? body?.source_sha;
}

function unquote(value) {
  const text = value.trim();
  if ((text.startsWith("'") && text.endsWith("'")) ||
      (text.startsWith('"') && text.endsWith('"'))) return text.slice(1, -1);
  return text;
}

export function selectProductionEvidenceDsn(values) {
  const dsn = values.CARR_DB_AUTHORITY_JOE_URL;
  if (typeof dsn !== "string" || !dsn.trim())
    fail("production_evidence_authority_credential_unavailable");
  return unquote(dsn);
}

export async function runFoundationAssuranceSealer(bindings, dependencies) {
  const config = JSON.parse(await readFile(CONFIG, "utf8"));
  const readback = await dependencies.stagingReadback(bindings);
  const github_checks = await dependencies.githubChecks(bindings.source_sha);
  const runtime = await dependencies.measureRuntime(
    bindings, config, readback, github_checks);
  const bundle = assembleFoundationAssuranceEvidence(bindings, config,
    { ...runtime, github_checks });
  await dependencies.store(bundle, bindings);
  return bundle.seal;
}
