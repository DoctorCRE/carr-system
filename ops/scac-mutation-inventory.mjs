#!/usr/bin/env node
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { SCAC_MUTATION_OPERATIONS, SCAC_MUTATION_REGISTRY_VERSION } from "../mcp-server/src/scac-mutation-registry.current.generated.js";
const REPO_ROOT = fileURLToPath(new URL("../", import.meta.url));
export const CURRENT_REGISTRY_VERSION = SCAC_MUTATION_REGISTRY_VERSION;
let defaultTools;
function requireTools(tools) {
  if (!tools) throw new Error("pass TOOLS explicitly");
  return tools;
}
export function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === "object")
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonicalize(value[key])]));
  return value;
}

export function sha256(value) {
  const bytes = typeof value === "string" ? value : JSON.stringify(canonicalize(value));
  return createHash("sha256").update(bytes).digest("hex");
}

function delegatesTo(name) {
  if (name === "stamp-touch") return ["log-activity"];
  if (name === "resolve-candidate") return ["log-activity", "new-deal", "patch-deal-field", "set-next-step"];
  if (name === "find-and-catch-up") return ["catch-me-up", "find"];
  if (name === "prepare-conversation") return ["find-and-catch-up", "who-do-we-know"];
  if (name === "morning-brief") return ["claim-card", "deal-room-board", "loop-board", "today-triage"];
  if (name === "call-verb") return ["*registered_operation"];
  return [];
}

export function mcpInventory(tools = defaultTools) {
  return Object.entries(requireTools(tools)).sort(([left], [right]) => left.localeCompare(right)).map(([name, tool]) => {
    const write = tool.write === true;
    const authorityOnly = tool.authorityOnly === true;
    const humanOnly = tool.humanOnly === true;
    return {
      ingress_key: `mcp-tool:${name}`,
      ingress_kind: "mcp_tool",
      operation: name,
      effect_class: name === "call-verb" ? "delegating" : write
        ? "administrative_mutation"
        : "audit_side_effect",
      source_locator: tool.registrySource,
      schema_digest: sha256(tool.inputSchema || {}),
      source_digest: sourceDigest(tool.registrySource),
      write,
      human_only: humanOnly,
      authority_only: authorityOnly,
      principal_mode: authorityOnly ? "server_verified_partner_authority" : humanOnly
        ? tool.humanCallerOnly === true ? "server_verified_human" : "server_verified_human_or_native_agent" : "authenticated_registered_principal",
      mutation_kind: write ? "scac.mutation.admin" : null,
      target_surface: "scac.surface.database",
      delegates_to: delegatesTo(name),
      request_shape: "closed_top_level_schema",
      idempotency_mode: write ? "operation_principal_manifest_bound" : "not_applicable",
      rollback_class: !write ? "append_only_audit" : "forward_fix_only",
      admission_class: "application_default_deny",
      owner_package: "11",
      implementation_state: "source_guarded_not_deployed",
      classification_authorizing: false,
    };
  });
}

const APPLICATION_INGRESSES = [
  ["worker-route:ingest", "worker_route", "mcp-server/src/index.js", "record_mutation"],
  ["worker-route:capture", "worker_route", "mcp-server/src/capture.js", "record_mutation"],
  ["worker-route:deal-room-turn", "worker_route", "mcp-server/src/index.js", "record_mutation"],
  ["worker-route:program6-routine", "worker_route", "mcp-server/src/program6-routine-controller.js", "record_mutation"],
  ["worker-route:oauth-kv", "worker_route", "mcp-server/src/google-oidc.js", "administrative_mutation"],
  ["worker-route:browser-challenge", "worker_route", "mcp-server/src/program6-browser-challenge.js", "record_mutation"],
  ["worker-sidewrite:failure-record", "worker_sidewrite", "mcp-server/src/trace.js", "audit_side_effect"],
  ["worker-sidewrite:tool-read-call", "worker_sidewrite", "mcp-server/src/mcp.js", "audit_side_effect"],
  ["worker-sidewrite:situation-retrieval", "worker_sidewrite", "mcp-server/src/situation-retrieval.js", "audit_side_effect"],
];

function sourceDigest(path) {
  return createHash("sha256").update(readFileSync(resolve(REPO_ROOT, path))).digest("hex");
}

export function parseGitIndexEntries(raw) {
  const records = Buffer.isBuffer(raw) ? raw.toString("utf8") : String(raw || "");
  return records.split("\0").filter(Boolean).flatMap(record => {
    const tab = record.indexOf("\t");
    if (tab < 0) throw new Error("git index entry missing path separator");
    const [mode, _objectId, stage] = record.slice(0, tab).split(" ");
    if (!/^(?:100644|100755)$/.test(mode) || stage !== "0") return [];
    return [{ path: record.slice(tab + 1), executable: mode === "100755" }];
  });
}

function trackedIndexEntries() {
  return parseGitIndexEntries(execFileSync("git", ["ls-files", "--stage", "-z"], {
    cwd: REPO_ROOT, encoding: "buffer",
  }));
}

export function isScriptEntrypoint(path, executable, source) {
  const base = path.split("/").at(-1);
  if (base.startsWith("test-") || base.startsWith("test_") || path.includes("/tests/")) return false;
  if (/(?:^|[-_])selftest(?:[-_.]|$)/.test(base) || path.includes("/test/")) return false;
  const knownExtension = /\.(?:py|sh|applescript|mjs|js)$/.test(path);
  if (!knownExtension && !executable) return false;
  if (source.startsWith("#!")) return true;
  if (path.endsWith(".applescript")) return true;
  if (!/\.(?:py|mjs|js)$/.test(path)) return false;
  if (path.endsWith(".py"))
    return /if\s+__name__\s*==\s*["']__main__["']\s*:/.test(source);
  return /\.(?:mjs|js)$/.test(path) &&
    (source.includes("process.argv") || source.includes("import.meta.url ===") || source.includes("require.main === module"));
}

export function discoverScriptEntrypoints(indexEntries = trackedIndexEntries(),
  sourceReader = path => readFileSync(resolve(REPO_ROOT, path), "utf8")) {
  return indexEntries.filter(({ path, executable }) =>
    isScriptEntrypoint(path, executable, sourceReader(path))).map(({ path }) => path).sort();
}

export function nonMcpInventory() {
  const application = APPLICATION_INGRESSES.map(([ingress_key, ingress_kind, source_locator, effect_class]) => ({
    ingress_key, ingress_kind, operation: ingress_key.split(":").slice(1).join(":"), effect_class,
    source_locator, schema_digest: sourceDigest(source_locator), handler_digest: sourceDigest(source_locator),
    write: true, human_only: false, authority_only: effect_class === "administrative_mutation",
    principal_mode: "existing_entrypoint_specific_authentication", mutation_kind: effect_class === "administrative_mutation" ? "scac.mutation.admin" : "scac.mutation.business_record",
    target_surface: "scac.surface.database", delegates_to: [], request_shape: "entrypoint_specific_current_contract",
    idempotency_mode: "entrypoint_specific_registered_current_control", rollback_class: "forward_fix_only",
    admission_class: "registered_inventory_only", owner_package: "11",
    implementation_state: "inventoried_not_atomically_mediated", classification_authorizing: false,
  }));
  const scripts = discoverScriptEntrypoints().map(source_locator => {
    const breakGlass = new Set(["tools/db-tap.py", "tools/call-verb.py", "tools/run-breakglass.py"]).has(source_locator);
    const genericDelegator = new Set(["run.sh", "mcp-server/local-verb.mjs"]).has(source_locator);
    const externalAdmin = !breakGlass && /(?:deploy|migrate|provision|rotate-credential|cleanup|cutoff|install-|sync-.*prod)/.test(source_locator);
    const ingress_kind = breakGlass ? "break_glass" : externalAdmin ? "external_admin" : "script_entrypoint";
    return {
    ingress_key: `${ingress_kind.replaceAll("_", "-")}:${source_locator}`, ingress_kind,
    operation: source_locator, effect_class: breakGlass ? "break_glass" : genericDelegator ? "delegating" : "administrative_mutation", source_locator,
    schema_digest: sourceDigest(source_locator), handler_digest: sourceDigest(source_locator),
    write: true, human_only: false, authority_only: true,
    principal_mode: "existing_script_specific_credential_boundary",
    mutation_kind: breakGlass ? "scac.mutation.break_glass" : "scac.mutation.admin",
    target_surface: "scac.surface.runtime",
    delegates_to: source_locator === "run.sh" ? ["*registered_script_entrypoint"]
      : source_locator === "mcp-server/local-verb.mjs" ? ["*registered_mcp_tool"] : [],
    request_shape: "fixed_script_cli_contract",
    idempotency_mode: "script_specific_registered_current_control", rollback_class: "forward_fix_only",
    admission_class: "registered_inventory_only", owner_package: "11",
    implementation_state: "inventoried_not_atomically_mediated", classification_authorizing: false,
  }});
  return [...application, ...scripts].sort((left, right) => left.ingress_key.localeCompare(right.ingress_key));
}

export function jobDefinitionInventory() {
  const manifest = JSON.parse(readFileSync(resolve(REPO_ROOT, "ops/config/control-plane-workflows.v1.json"), "utf8"));
  return manifest.workflows.map(workflow => {
    const execution = { ...workflow.execution };
    delete execution.kind;
    const projected = {
      ingress_key: `job-definition:${workflow.key}:${workflow.version}`,
      ingress_kind: "job_definition",
      key: workflow.key,
      version: workflow.version,
      enabled: workflow.enabled,
      risk: workflow.risk,
      owner_actor: workflow.inventory?.owner || "system",
      execution_kind: workflow.execution.kind,
      entrypoint: workflow.execution.entrypoint || workflow.execution.cognition_job,
      execution_contract: execution,
      inventory_contract: workflow.inventory || {},
      recurrence: workflow.recurrence,
      state_contract: workflow.state,
      routing_contract: workflow.routing,
      filtering_contract: workflow.filtering,
      validation_contract: workflow.validation,
      retry_policy: workflow.retry,
      deduplication: workflow.deduplication,
      completion_contract: workflow.completion,
      legacy_schedule: workflow.legacy_schedule,
    };
    return {
      ...projected,
      effect_class: "administrative_mutation",
      source_locator: `ops/config/control-plane-workflows.v1.json#${workflow.key}:v${workflow.version}`,
      write: true,
      human_only: false,
      authority_only: true,
      principal_mode: "existing_control_plane_authority_sync",
      mutation_kind: "scac.mutation.admin",
      target_surface: "scac.surface.runtime",
      delegates_to: [projected.entrypoint],
      request_shape: "closed_reviewed_workflow_contract",
      idempotency_mode: "job_key_version_and_schedule_bound",
      rollback_class: "forward_fix_only",
      admission_class: "registered_inventory_only",
      owner_package: "11",
      implementation_state: "inventoried_not_atomically_mediated",
      classification_authorizing: false,
    };
  }).sort((left, right) => left.ingress_key.localeCompare(right.ingress_key));
}

function yamlSections(source, key) {
  const lines = source.split(/\r?\n/);
  const sections = [];
  for (let index = 0; index < lines.length; index += 1) {
    const match = lines[index].match(new RegExp(`^(\\s*)${key}:`));
    if (!match) continue;
    const indent = match[1].length;
    const section = [lines[index]];
    for (let cursor = index + 1; cursor < lines.length; cursor += 1) {
      const line = lines[cursor];
      if (line.trim() && !line.trimStart().startsWith("#") && line.search(/\S/) <= indent) break;
      section.push(line);
    }
    sections.push(section.join("\n"));
  }
  return sections;
}

function decodeXmlText(value) {
  return value
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'")
    .replaceAll("&amp;", "&")
    .replace(/&#x([0-9a-f]+);/gi, (_, hex) => String.fromCodePoint(Number.parseInt(hex, 16)))
    .replace(/&#([0-9]+);/g, (_, decimal) => String.fromCodePoint(Number.parseInt(decimal, 10)));
}

export function parsePlistXml(source) {
  const clean = source
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/<\?xml[\s\S]*?\?>/g, "")
    .replace(/<!DOCTYPE[\s\S]*?>/g, "");
  const tokens = clean.match(/<[^>]+>|[^<]+/g) || [];
  let cursor = 0;
  const skipWhitespace = () => {
    while (cursor < tokens.length && !tokens[cursor].startsWith("<") && !tokens[cursor].trim()) cursor += 1;
  };
  const parseElement = () => {
    skipWhitespace();
    const opening = tokens[cursor++];
    const selfMatch = opening?.match(/^<([A-Za-z][A-Za-z0-9]*)(?:\s[^>]*)?\/>$/);
    if (selfMatch) {
      if (selfMatch[1] === "true") return true;
      if (selfMatch[1] === "false") return false;
      throw new Error(`unsupported self-closing plist element ${selfMatch[1]}`);
    }
    const openMatch = opening?.match(/^<([A-Za-z][A-Za-z0-9]*)(?:\s[^>]*)?>$/);
    if (!openMatch) throw new Error(`malformed plist token ${opening || "<eof>"}`);
    const tag = openMatch[1];
    if (tag === "plist") {
      const value = parseElement();
      skipWhitespace();
      if (tokens[cursor++] !== "</plist>") throw new Error("plist root is not closed");
      return value;
    }
    if (tag === "dict") {
      const value = {};
      while (true) {
        skipWhitespace();
        if (tokens[cursor] === "</dict>") { cursor += 1; return value; }
        if (!tokens[cursor]?.startsWith("<key")) throw new Error("plist dict key is missing");
        const key = parseElement();
        if (Object.hasOwn(value, key)) throw new Error(`duplicate plist key ${key}`);
        value[key] = parseElement();
      }
    }
    if (tag === "array") {
      const value = [];
      while (true) {
        skipWhitespace();
        if (tokens[cursor] === "</array>") { cursor += 1; return value; }
        value.push(parseElement());
      }
    }
    if (!["key", "string", "integer", "real", "date", "data"].includes(tag))
      throw new Error(`unsupported plist element ${tag}`);
    let raw = "";
    while (cursor < tokens.length && tokens[cursor] !== `</${tag}>`) raw += tokens[cursor++];
    if (tokens[cursor++] !== `</${tag}>`) throw new Error(`plist ${tag} is not closed`);
    const text = decodeXmlText(raw);
    if (tag === "integer") {
      if (!/^-?[0-9]+$/.test(text.trim())) throw new Error(`invalid plist integer ${text}`);
      return Number.parseInt(text.trim(), 10);
    }
    if (tag === "real") {
      const value = Number(text.trim());
      if (!Number.isFinite(value)) throw new Error(`invalid plist real ${text}`);
      return value;
    }
    return tag === "data" ? text.replace(/\s+/g, "") : text;
  };
  const value = parseElement();
  skipWhitespace();
  if (cursor !== tokens.length) throw new Error(`unexpected plist content ${tokens[cursor]}`);
  return value;
}

// A LaunchAgent with no trigger and no load-time start cannot run: launchd has
// no moment at which to fire it. Such a definition is a reviewed artifact rather
// than a deployed service, so it is exempt from ops.service catalog closure --
// requiring a deploy_mechanism for it would assert a deployment that must not
// exist. The exemption is derived from the artifact itself rather than from a
// list, so it cannot drift out of step with the file it describes, and it is
// two-directional: a definition-only agent that DOES appear in the service
// catalog is a contradiction and still refuses below.
const LAUNCHD_TRIGGER_KEYS = Object.freeze([
  "StartCalendarInterval", "StartInterval", "WatchPaths", "KeepAlive",
  "StartOnMount", "QueueDirectories", "Sockets", "MachServices",
]);

export function isDefinitionOnlyLaunchd(plist) {
  if (!plist || typeof plist !== "object") return false;
  if (plist.RunAtLoad === true) return false;
  return !LAUNCHD_TRIGGER_KEYS.some(key => plist[key] !== undefined);
}

export function validateLaunchdAuthorityCatalogs(launchdPaths, services, legacy,
  definitionOnlyPaths = []) {
  const definitionOnly = new Set(definitionOnlyPaths);
  const reviewedPaths = new Set(launchdPaths);
  if (reviewedPaths.size !== launchdPaths.length) throw new Error("duplicate launchd source path");
  const serviceKeys = new Set();
  for (const service of services.services || []) {
    if (serviceKeys.has(service.key)) throw new Error(`duplicate ops.service key ${service.key}`);
    serviceKeys.add(service.key);
  }
  const servicesByPlist = new Map();
  for (const service of services.services || []) {
    const serviceEnvironments = new Set();
    for (const environment of service.environments || []) {
      if (serviceEnvironments.has(environment.environment))
        throw new Error(`duplicate ops.service environment ${service.key}:${environment.environment}`);
      serviceEnvironments.add(environment.environment);
      const path = environment.deploy_mechanism;
      if (typeof path !== "string" || !path.startsWith("ops/launchd/") || !path.endsWith(".plist")) continue;
      if (typeof environment.environment !== "string" || !environment.environment)
        throw new Error(`launchd ops.service environment is missing ${service.key}:${path}`);
      const mappings = servicesByPlist.get(path) || [];
      const mapping = { service_key: service.key, environment: environment.environment };
      servicesByPlist.set(path, [...mappings, mapping]
        .sort((left, right) => `${left.service_key}:${left.environment}`.localeCompare(`${right.service_key}:${right.environment}`)));
    }
  }
  for (const path of definitionOnly) {
    if (!reviewedPaths.has(path))
      throw new Error(`definition-only launchd path is not a reviewed source: ${path}`);
    if (servicesByPlist.has(path))
      throw new Error(`definition-only launchd agent claims a deploy mechanism: ${path}`);
  }
  const missingServices = [...reviewedPaths]
    .filter(path => !servicesByPlist.has(path) && !definitionOnly.has(path)).sort();
  const orphanServices = [...servicesByPlist.keys()].filter(path => !reviewedPaths.has(path)).sort();
  if (missingServices.length || orphanServices.length)
    throw new Error(`launchd ops.service catalog closure mismatch missing=${missingServices.join(",")} orphan=${orphanServices.join(",")}`);

  const legacyByPlist = new Map();
  const legacySurfaceIds = new Set();
  for (const surface of legacy.surfaces || []) {
    if (surface.scheduler_kind !== "launchd") continue;
    if (legacySurfaceIds.has(surface.surface_id)) throw new Error(`duplicate launchd legacy surface ${surface.surface_id}`);
    legacySurfaceIds.add(surface.surface_id);
    if (!reviewedPaths.has(surface.repo_plist_relpath))
      throw new Error(`orphan launchd legacy path ${surface.repo_plist_relpath}`);
    if (legacyByPlist.has(surface.repo_plist_relpath))
      throw new Error(`duplicate launchd legacy path ${surface.repo_plist_relpath}`);
    legacyByPlist.set(surface.repo_plist_relpath, surface);
  }
  return { servicesByPlist, legacyByPlist };
}

export function assertLegacyLaunchdSource(surface, sourceLocator, plist) {
  if (surface.repo_plist_relpath !== sourceLocator || plist.Label !== surface.locator ||
      JSON.stringify(plist.ProgramArguments) !== JSON.stringify(surface.canonical_program_arguments) ||
      sha256(plist) !== surface.canonical_plist_fingerprint)
    throw new Error(`launchd legacy source mismatch ${surface.surface_id}`);
}

function launchdAuthorityMaps(launchdPaths) {
  const servicesPath = "ops/config/services.json";
  const legacyPath = "ops/config/control-plane-scheduler-cutover.v1.json";
  const services = JSON.parse(readFileSync(resolve(REPO_ROOT, servicesPath), "utf8"));
  const legacy = JSON.parse(readFileSync(resolve(REPO_ROOT, legacyPath), "utf8"));
  const definitionOnlyPaths = launchdPaths.filter(path =>
    isDefinitionOnlyLaunchd(parsePlistXml(readFileSync(resolve(REPO_ROOT, path), "utf8"))));
  const { servicesByPlist, legacyByPlist } = validateLaunchdAuthorityCatalogs(
    launchdPaths, services, legacy, definitionOnlyPaths);
  return {
    servicesByPlist,
    legacyByPlist,
    catalogDigests: {
      services: `sha256:${sourceDigest(servicesPath)}`,
      legacy_schedule_launchd: `sha256:${sourceDigest(legacyPath)}`,
    },
  };
}

export function workflowDefinitionInventory() {
  const trackedPaths = trackedIndexEntries().map(entry => entry.path);
  const github = trackedPaths.filter(path => path.startsWith(".github/workflows/") && /\.ya?ml$/.test(path))
    .sort().map(source_locator => {
    const source = readFileSync(resolve(REPO_ROOT, source_locator), "utf8");
    const runSource = yamlSections(source, "run").join("\n");
    const actionDelegates = [...source.matchAll(/^\s*-?\s*uses:\s*([^\s#]+)/gm)]
      .map(match => `github-action:${match[1]}`);
    const scriptDelegates = [...runSource.matchAll(/(?:^|[\s"'])(?:(?:\.\/)?((?:bin|tools|ops|pipelines|hooks)\/[A-Za-z0-9_./-]+))/gm)]
      .map(match => `script:${match[1]}`);
    const commandDelegates = [
      ...(runSource.includes("aws s3api put-object") ? ["shell:aws-s3api-put-object"] : []),
      ...(runSource.includes("gh pr merge") ? ["shell:gh-pr-merge"] : []),
    ];
    return {
      ingress_key: `github-workflow:${source_locator.split("/").at(-1)}`,
      ingress_kind: "workflow_entrypoint",
      operation: source_locator,
      effect_class: "administrative_mutation",
      source_locator,
      schema_digest: sourceDigest(source_locator),
      handler_digest: sourceDigest(source_locator),
      trigger_contract_digest: sha256(yamlSections(source, "on")),
      permissions_contract_digest: sha256(yamlSections(source, "permissions")),
      write: true,
      human_only: false,
      authority_only: true,
      principal_mode: "github_actions_token_and_declared_workflow_permissions",
      mutation_kind: "scac.mutation.admin",
      target_surface: "scac.surface.runtime",
      delegates_to: [...new Set([...actionDelegates, ...scriptDelegates, ...commandDelegates])].sort(),
      request_shape: "github_workflow_trigger_and_permissions_source_bound",
      idempotency_mode: "github_run_and_attempt_bound_current_control",
      rollback_class: "forward_fix_only",
      admission_class: "registered_inventory_only",
      owner_package: "11",
      implementation_state: "inventoried_not_atomically_mediated",
      classification_authorizing: false,
    };
  });
  const scriptEntrypoints = new Set(discoverScriptEntrypoints());
  const launchdPaths = trackedPaths.filter(path => path.startsWith("ops/launchd/") && path.endsWith(".plist")).sort();
  const { servicesByPlist, legacyByPlist, catalogDigests } = launchdAuthorityMaps(launchdPaths);
  const launchd = launchdPaths.map(source_locator => {
    const source = readFileSync(resolve(REPO_ROOT, source_locator), "utf8");
    const plist = parsePlistXml(source);
    const programArguments = Array.isArray(plist.ProgramArguments) ? plist.ProgramArguments : [];
    const label = typeof plist.Label === "string" ? plist.Label : null;
    const delegates = programArguments.filter(argument => argument.includes("/")).map(argument => {
      let candidate = argument;
      if (candidate.startsWith("{{REPO}}/")) candidate = candidate.slice("{{REPO}}/".length);
      else if (candidate.startsWith("/Users/booko/carr-system/")) candidate = candidate.slice("/Users/booko/carr-system/".length);
      if (scriptEntrypoints.has(candidate)) return `script:${candidate}`;
      return argument.startsWith("/") ? `executable:${argument}` : `argument:${argument}`;
    });
    const triggerContract = Object.fromEntries(["KeepAlive", "RunAtLoad", "StartInterval", "StartCalendarInterval", "WatchPaths", "QueueDirectories"]
      .filter(key => Object.hasOwn(plist, key)).map(key => [key, plist[key]]));
    const environmentContract = plist.EnvironmentVariables || {};
    const serviceMappings = servicesByPlist.get(source_locator) || [];
    const definitionOnly = isDefinitionOnlyLaunchd(plist);
    // A deployed agent must name the service environment that deploys it. A
    // definition-only agent has no deployment to name, and is inventoried with
    // an explicit non-deployed authority ref instead of an absent one, so it is
    // still a registered row a reviewer can see rather than a silent gap.
    if (!serviceMappings.length && !definitionOnly)
      throw new Error(`launchd workflow lacks ops.service authority mapping: ${source_locator}`);
    // A DISTINCT NAMESPACE, not an ops.service_environment ref: this agent has
    // no service environment, and borrowing that prefix would have made it
    // count as one wherever deployed environments are totalled.
    const physicalAuthorityRefs = definitionOnly
      ? ["ops.definition_only_launchd:not_deployed"]
      : serviceMappings.map(mapping =>
        `ops.service_environment:${mapping.service_key}:${mapping.environment}`);
    const legacySurface = legacyByPlist.get(source_locator);
    if (legacySurface) {
      assertLegacyLaunchdSource(legacySurface, source_locator, plist);
      physicalAuthorityRefs.push(`ops.legacy_schedule_launchd_contract:${legacySurface.surface_id}`);
    }
    return {
      ingress_key: `launchd-workflow:${label || source_locator.split("/").at(-1)}`,
      ingress_kind: "workflow_entrypoint",
      operation: source_locator,
      effect_class: "administrative_mutation",
      source_locator,
      schema_digest: sourceDigest(source_locator),
      handler_digest: sourceDigest(source_locator),
      launchd_label: label,
      trigger_contract_digest: sha256(triggerContract),
      environment_contract_digest: sha256(environmentContract),
      program_arguments_digest: sha256(programArguments),
      write: true,
      human_only: false,
      authority_only: true,
      principal_mode: "launchd_user_session_and_declared_environment",
      mutation_kind: "scac.mutation.admin",
      target_surface: "scac.surface.runtime",
      delegates_to: [...new Set(delegates)].sort(),
      request_shape: "launchd_trigger_arguments_environment_source_bound",
      idempotency_mode: "delegated_entrypoint_current_control",
      rollback_class: "forward_fix_only",
      admission_class: "registered_inventory_only",
      physical_authority_refs: physicalAuthorityRefs.sort(),
      physical_authority_catalog_digests: catalogDigests,
      owner_package: "11",
      implementation_state: "inventoried_not_atomically_mediated",
      classification_authorizing: false,
    };
  });
  return [...github, ...launchd].sort((left, right) => left.ingress_key.localeCompare(right.ingress_key));
}

export function fullInventory(tools = defaultTools) {
  requireTools(tools);
  return [...mcpInventory(tools), ...nonMcpInventory(), ...jobDefinitionInventory(), ...workflowDefinitionInventory()]
    .sort((left, right) => left.ingress_key.localeCompare(right.ingress_key));
}


export function assertCurrentContracts(tools) {
  const rows = mcpInventory(tools);
  if (rows.length !== Object.keys(SCAC_MUTATION_OPERATIONS).length)
    throw new Error("current MCP operation set drifted");
  for (const row of rows) {
    const expected = SCAC_MUTATION_OPERATIONS[row.operation];
    for (const field of ["ingress_key", "source_locator", "schema_digest", "write", "human_only", "authority_only", "delegates_to"])
      if (JSON.stringify(row[field]) !== JSON.stringify(expected?.[field]))
        throw new Error(`current MCP contract drifted: ${row.operation}.${field}`);
  }
  return true;
}
if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  const { TOOLS } = await import("../mcp-server/src/tools.js");
  assertCurrentContracts(TOOLS);
  console.log("current MCP contracts: OK");
}
