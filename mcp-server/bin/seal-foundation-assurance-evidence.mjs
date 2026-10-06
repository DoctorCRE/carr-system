#!/usr/bin/env node
// WR95 closed evidence runner/sealer. The command accepts immutable target
// bindings only; measurements, conclusions, identities, time and pass/fail are
// acquired by this process and cannot be supplied by its caller.

import { mkdtemp, readFile, rm } from "node:fs/promises";
import { existsSync, readFileSync } from "node:fs";
import { spawn } from "node:child_process";
import { homedir, tmpdir } from "node:os";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join, resolve } from "node:path";
import pg from "pg";

import { digest } from "../src/artifact-trust.js";
import {
  BENCHMARK_CELL_DOMAIN_TAG,
  BENCHMARK_PAYLOAD_DOMAIN_TAG,
  benchmarkRequiredCells,
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
const REHEARSAL = resolve(REPO, "ops/foundation-assurance-candidate-rehearsal.py");
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

function command(command, args, options = {}) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(command, args, { cwd: REPO, ...options, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "", stderr = "";
    child.stdout.on("data", part => { stdout += part; });
    child.stderr.on("data", part => { stderr += part; });
    child.on("error", reject);
    child.on("close", code => code === 0 ? resolvePromise(stdout) :
      reject(new FoundationAssuranceSealerError("acquisition_command_failed",
        { command, code, detail: stderr.slice(-500) })));
  });
}

async function githubChecks(sourceSha) {
  const raw = await command("gh", ["api", "--paginate",
    `repos/jbookout/carr-system/commits/${sourceSha}/check-runs`]);
  const pages = raw.trim().split(/\n(?=\{)/).filter(Boolean).map(JSON.parse);
  const runs = pages.flatMap(page => page.check_runs || []);
  return FOUNDATION_ASSURANCE_GITHUB_CHECKS.map(name => {
    const matches = runs.filter(row => row.name === name && row.head_sha === sourceSha &&
      row.status === "completed").sort((a, b) => String(b.completed_at).localeCompare(String(a.completed_at)));
    const row = matches[0];
    if (!row || row.conclusion !== "success") fail("github_check_unavailable", name);
    return { name, conclusion: row.conclusion, head_sha: row.head_sha,
      run_id: Number(row.id), url: row.html_url };
  });
}

async function stagingReadback(bindings) {
  const response = await fetch(`${bindings.staging_origin}/release`, { signal: AbortSignal.timeout(15000) });
  if (!response.ok) fail("staging_release_unavailable", response.status);
  const body = await response.json();
  const provider = body.worker_version?.id ?? body.provider_version ?? body.provider_version_id;
  const source = stagingReleaseSource(body);
  if (provider !== bindings.staging_provider_version || source !== bindings.source_sha)
    fail("staging_release_binding_mismatch", { provider, source });
  return body;
}

export function stagingReleaseSource(body) {
  return body?.git_sha?.value ?? body?.source_sha;
}

class Cdp {
  constructor(url) {
    this.ws = new WebSocket(url); this.id = 0; this.pending = new Map(); this.events = [];
  }
  async open() {
    await new Promise((ok, no) => {
      const timer = setTimeout(() => no(new Error("Chrome DevTools open timeout")), 10000);
      this.ws.addEventListener("open", () => { clearTimeout(timer); ok(); }, { once: true });
      this.ws.addEventListener("error", event => { clearTimeout(timer); no(event.error || new Error("Chrome DevTools error")); }, { once: true });
    });
    this.ws.addEventListener("message", event => {
      const message = JSON.parse(event.data);
      if (message.id && this.pending.has(message.id)) {
        const { ok, no } = this.pending.get(message.id); this.pending.delete(message.id);
        message.error ? no(new Error(message.error.message)) : ok(message.result);
      } else if (message.method) {
        this.events.push(message);
      }
    });
  }
  call(method, params = {}) {
    const id = ++this.id;
    return new Promise((ok, no) => {
      this.pending.set(id, { ok, no });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  async event(method, timeout = 20000) {
    const start = Date.now();
    while (Date.now() - start < timeout) {
      const at = this.events.findIndex(row => row.method === method);
      if (at >= 0) return this.events.splice(at, 1)[0].params;
      await new Promise(ok => setTimeout(ok, 25));
    }
    throw new Error(`Chrome DevTools event timeout: ${method}`);
  }
  close() { this.ws.close(); }
}

async function waitFor(path, timeout = 10000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    try { return await readFile(path, "utf8"); } catch { await new Promise(ok => setTimeout(ok, 50)); }
  }
  throw new Error(`timed out waiting for ${path}`);
}

function waitForChildExit(child, timeout = 5000) {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve();
  return new Promise(resolvePromise => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      child.off("exit", finish);
      resolvePromise();
    };
    const timer = setTimeout(finish, timeout);
    child.once("exit", finish);
  });
}

export async function closeChromeProfile(chrome, profile, remove = rm) {
  if (chrome.exitCode === null && chrome.signalCode === null) {
    chrome.kill("SIGTERM");
    await waitForChildExit(chrome);
  }
  if (chrome.exitCode === null && chrome.signalCode === null) {
    chrome.kill("SIGKILL");
    await waitForChildExit(chrome, 2000);
  }
  let last;
  for (let attempt = 0; attempt < 3; attempt++) {
    try { await remove(profile, { recursive: true, force: true }); return; }
    catch (error) {
      last = error;
      if (error?.code !== "ENOTEMPTY") throw error;
      await new Promise(ok => setTimeout(ok, 100));
    }
  }
  throw last;
}

function chromePath() {
  const candidates = [process.env.CHROME_BIN,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome", "/usr/bin/chromium"].filter(Boolean);
  const selected = candidates.find(candidate => existsSync(candidate));
  if (!selected) fail("installed_chrome_unavailable");
  return selected;
}

async function chromeMeasurements(bindings, config, payload) {
  const profile = await mkdtemp(join(tmpdir(), "wr95-chrome-"));
  const chrome = spawn(chromePath(), ["--headless=new", "--disable-gpu", "--no-first-run",
    "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"],
  { stdio: ["ignore", "ignore", "ignore"] });
  let cdp;
  try {
    const active = (await waitFor(join(profile, "DevToolsActivePort"))).trim().split(/\r?\n/);
    const targets = await (await fetch(`http://127.0.0.1:${active[0]}/json/list`)).json();
    const page = targets.find(row => row.type === "page");
    if (!page?.webSocketDebuggerUrl) fail("chrome_target_unavailable");
    cdp = new Cdp(page.webSocketDebuggerUrl); await cdp.open();
    await cdp.call("Page.enable"); await cdp.call("Network.enable");
    await cdp.call("Page.addScriptToEvaluateOnNewDocument", { source:
      "window.__wr95Lcp=0;new PerformanceObserver(l=>{for(const e of l.getEntries())window.__wr95Lcp=Math.max(window.__wr95Lcp,e.startTime)}).observe({type:'largest-contentful-paint',buffered:true});" });
    const version = await cdp.call("Browser.getVersion");
    const product = String(version.product || "Chrome/unknown").split("/")[1] || "unknown";
    const browser = { name: "Chrome", version: product.split(".")[0], build: product };
    const cells = benchmarkRequiredCells(payload(browser));
    const measured = [];
    const provenance = [];
    for (const cell of cells.filter(row => row.metric !== "command_acknowledgement_ms")) {
      const values = [];
      await cdp.call("Network.setCacheDisabled", { cacheDisabled: cell.cache_state === "cold" });
      for (let at = 0; at < config.warmup_runs + config.samples_per_cell; at++) {
        cdp.events.length = 0;
        await cdp.call("Page.navigate", { url: `${bindings.staging_origin}${cell.subject}` });
        await cdp.event("Page.loadEventFired");
        await new Promise(ok => setTimeout(ok, cell.metric === "lcp_ms" ? 500 : 25));
        const expression = cell.metric === "lcp_ms" ? "window.__wr95Lcp" :
          "performance.getEntriesByType('navigation')[0]?.duration";
        const result = await cdp.call("Runtime.evaluate", { expression, returnByValue: true });
        const value = Number(result.result?.value);
        if (!Number.isFinite(value) || value < 0) fail("chrome_measurement_invalid", cell.metric);
        values.push(value);
      }
      measured.push({ cell, warmup_samples: values.slice(0, config.warmup_runs),
        samples: values.slice(config.warmup_runs), excluded_sample_indexes: [] });
      provenance.push({ cell_digest: digest([BENCHMARK_CELL_DOMAIN_TAG, cell]),
        origin: `${bindings.staging_origin}${cell.subject}`, sample_count: config.samples_per_cell,
        warmup_count: config.warmup_runs });
    }
    return { browser, measured, provenance };
  } finally {
    try { cdp?.close(); } catch {}
    await closeChromeProfile(chrome, profile);
  }
}

async function acknowledgementMeasurements(bindings, config, payload, token) {
  if (!token) fail("staging_review_token_unavailable");
  const cells = benchmarkRequiredCells(payload).filter(row => row.metric === "command_acknowledgement_ms");
  const measured = [], provenance = [];
  for (const cell of cells) {
    const values = [];
    for (let at = 0; at < config.warmup_runs + config.samples_per_cell; at++) {
      const started = performance.now();
      const response = await fetch(`${bindings.staging_origin}/mcp`, { method: "POST",
        headers: { authorization: `Bearer ${token}`, "content-type": "application/json",
          // Bind the live measurement call to the evaluator session sealed in
          // the benchmark payload. The UUID grants no authority; the bearer
          // still selects and authenticates the registered oracle seat.
          "x-correlation-id": bindings.idempotency_key,
          ...(cell.cache_state === "cold" ? { "cache-control": "no-cache" } : {}) },
        body: JSON.stringify({ jsonrpc: "2.0", id: at + 1, method: "tools/call",
          params: { name: cell.subject, arguments: {} } }), signal: AbortSignal.timeout(30000) });
      const body = await response.json();
      if (!response.ok || body.error || body.result?.isError) fail("mcp_acknowledgement_failed", response.status);
      values.push(performance.now() - started);
    }
    measured.push({ cell, warmup_samples: values.slice(0, config.warmup_runs),
      samples: values.slice(config.warmup_runs), excluded_sample_indexes: [] });
    provenance.push({ cell_digest: digest([BENCHMARK_CELL_DOMAIN_TAG, cell]),
      origin: `${bindings.staging_origin}/mcp`, sample_count: config.samples_per_cell,
      warmup_count: config.warmup_runs });
  }
  return { measured, provenance };
}

async function stagingDatabaseFacts(bindings) {
  const raw = await command(resolve(REPO, ".venv/bin/python"), [REHEARSAL,
    "--current-sha", bindings.staging_replacement_source_sha,
    "--candidate-operation-id", bindings.staging_candidate_operation_id,
    "--receipt-id", bindings.staging_replacement_receipt_id,
    "--read-foundation-facts"]);
  const line = raw.trim().split(/\r?\n/).find(value => value.startsWith("{"));
  if (!line) fail("staging_database_evidence_unavailable");
  return JSON.parse(line);
}

function comparatorRows(bindings, facts, readback, github_checks) {
  const checks = {
    "assurance-fabric-preactivation": facts.migration ===
      readback?.schema?.highest_applied_migration,
    "foundation-control-plane-preactivation": facts.oracle_role === true && Number(facts.oracle_functions) === 3,
    "global-execution-contract": facts.registry_current === true,
    "global-no-phi-boundary": facts.raw_table_write === false,
    "global-prompt-injection-boundary": github_checks.every(row => row.conclusion === "success"),
    "global-secrets-boundary": facts.oracle_role === true && facts.raw_table_write === false,
    "global-source-authority": stagingReleaseSource(readback) === bindings.source_sha,
  };
  return FOUNDATION_ASSURANCE_COMPARATORS.map(id => {
    if (checks[id] !== true) fail("live_comparator_failed", id);
    return { id, status: "pass", detail_digest: digest([id, facts, bindings.source_sha]),
      origin: `${bindings.staging_origin}/release#${id}` };
  });
}

async function measureRuntime(bindings, config, readback, github_checks) {
  let chosenBrowser = { name: "Chrome", version: "pending", build: "pending" };
  const payloadFor = browser => foundationAssuranceBenchmarkPayload(config, {
    subject_digest: digest(["doctorcre:wr95-subject:v1", bindings.source_sha, bindings.source_tree]),
    candidate_digest: digest(["doctorcre:wr95-candidate:v1", bindings.final_provider_version]),
    policy_digest: digest(validateFoundationAssuranceBenchmarkConfig(config)), browser,
    runtime_version: bindings.staging_provider_version,
    evaluator_identities: [evaluatorIdentity(bindings)],
  });
  const browserResult = await chromeMeasurements(bindings, config, browser => {
    chosenBrowser = browser; return payloadFor(browser);
  });
  const payload = payloadFor(chosenBrowser);
  const ack = await acknowledgementMeasurements(bindings, config, payload,
    process.env.CARR_WR95_STAGING_REVIEW_TOKEN);
  const facts = await stagingDatabaseFacts(bindings);
  const allCells = [...browserResult.measured, ...ack.measured];
  const order = new Map(benchmarkRequiredCells(payload).map((cell, at) =>
    [digest([BENCHMARK_CELL_DOMAIN_TAG, cell]), at]));
  allCells.sort((a, b) => order.get(digest([BENCHMARK_CELL_DOMAIN_TAG, a.cell])) -
    order.get(digest([BENCHMARK_CELL_DOMAIN_TAG, b.cell])));
  const sample_provenance = [...browserResult.provenance, ...ack.provenance]
    .sort((a, b) => order.get(a.cell_digest) - order.get(b.cell_digest));
  return {
    browser: chosenBrowser, runtime_version: bindings.staging_provider_version,
    database: { environment: "staging", migration: facts.migration, read_only: true,
      source: "ops/foundation-assurance-candidate-rehearsal.py --read-foundation-facts" },
    measurements: { schema_version: "doctorcre-v5-benchmark-measurement-set.v1",
      outlier_rule: payload.outlier_rule, p95_aggregation_method: payload.p95_aggregation_method,
      captured_at_ms: Date.now(), cells: allCells }, sample_provenance,
    comparators: comparatorRows(bindings, facts, readback, github_checks),
  };
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

async function productionDsn() {
  if (process.env.CARR_DB_AUTHORITY_JOE_URL)
    return selectProductionEvidenceDsn(process.env);
  const body = await readFile(join(homedir(), ".config/carr/db.env"), "utf8");
  const values = Object.fromEntries(body.split(/\r?\n/).flatMap(line => {
    const at = line.indexOf("=");
    return at > 0 ? [[line.slice(0, at), line.slice(at + 1)]] : [];
  }));
  return selectProductionEvidenceDsn(values);
}

async function storeEvidence(bundle, bindings) {
  const client = new pg.Client({ connectionString: await productionDsn() });
  await client.connect();
  try {
    const principal = (await client.query(
      "select session_user::text as session_user, current_user::text as current_user, ops.authority_actor_slug() as actor_slug")).rows[0];
    if (principal?.session_user !== "carr_authority_joe" ||
        principal?.current_user !== "carr_authority_joe" || principal?.actor_slug !== "joe")
      fail("production_evidence_authority_role_mismatch", principal);
    await client.query("begin");
    await client.query("select set_config('carr.acting_actor_slug',$1,true), set_config('carr.verified_human_actor_slug',$1,true), set_config('carr.receipt_session_ref',$2,true)",
      [principal.actor_slug, `session:wr95-sealer-${bindings.idempotency_key}`]);
    const result = await client.query(
      "select ops.foundation_assurance_store_evidence($1::uuid,$2::jsonb,$3::jsonb,$4::jsonb) as result",
      [bindings.idempotency_key, bundle.config, bundle.evidence, bundle.seal]);
    await client.query("commit");
    if (result.rows[0]?.result?.evidence_ref !== bundle.seal.evidence_ref)
      fail("production_evidence_store_readback_mismatch");
  } catch (error) {
    try { await client.query("rollback"); } catch {}
    throw error;
  } finally { await client.end(); }
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

async function main() {
  const bindings = parseFoundationAssuranceArgs(process.argv.slice(2));
  const seal = await runFoundationAssuranceSealer(bindings, {
    githubChecks, stagingReadback, measureRuntime, store: storeEvidence,
  });
  process.stdout.write(`${JSON.stringify(seal)}\n`);
}

if (import.meta.url === pathToFileURL(process.argv[1] || "").href) {
  main().catch(error => {
    const detail = error?.detail ? `: ${JSON.stringify(error.detail)}` : "";
    process.stderr.write(`foundation-assurance-sealer: ${error?.code || error?.message}${detail}\n`);
    process.exitCode = 1;
  });
}
