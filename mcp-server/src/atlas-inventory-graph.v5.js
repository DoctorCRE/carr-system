// V5-UX-C07 — Atlas inventory graph and linked index.
//
// A pure, read-only projection of what this system DECLARES it has, what is
// INSTALLED in the canonical database, and what has actually been OBSERVED
// running. Three layers, never blended: a declared node is a promise in the
// bundle, an installed node is a row, and an observed node carries evidence
// from a run. Anything we can only guess at is labelled "inferred" and is
// never counted as either of the first two.
//
// WHY THIS EXISTS, in one line: the inventory of this system is spread across
// a tool registry, a sealed mutation registry, a surface inventory and a dozen
// ops relations, so nothing today can answer "what exists, and is it wired?"
// without a human joining four surfaces by hand. This read does that join and,
// crucially, PUBLISHES ITS OWN GAPS: `coverage` always names the sources it
// could not reach, including the four gaps that are structural today, so an
// incomplete atlas is never mistaken for a complete one.
//
// This module is deliberately a library, not a program: it takes no arguments
// from a process, has no interpreter line and no self-execution guard. The
// sealed frontier predicate in ops/scac-mutation-inventory.mjs matches on
// PLAIN TEXT, so even naming those constructs in a comment here would move the
// frontier and cost a registry successor. That lesson was paid for once.

import { organizationTenantForActor } from "./identity.js";
import { TOOLS } from "./tools.js";
import { ATLAS_MUTATION_DECLARATIONS as SCAC_MUTATION_OPERATIONS } from "./atlas-mutation-declarations.generated.js";
import { SCAC_MUTATION_REGISTRY_DIGEST, SCAC_MUTATION_REGISTRY_VERSION } from "./mutation-registry.js";
import { AUTHENTICATED_SURFACES, WORKSPACE_ROUTES } from "./workspace-surface-inventory.js";

export const ATLAS_GRAPH_PATH = "/api/v1/atlas-graph";

export const ATLAS_LAYERS = ["declared", "installed", "observed"];
export const ATLAS_LIMIT_DEFAULT = 500;
export const ATLAS_LIMIT_MAX = 2000;

const VALID_ACTORS = new Set(["joe", "dell"]);
const TENANT = "carr-internal";

// 42501 insufficient_privilege: a missing grant is a DEPENDENCY the reader role
// does not have, not a defect in this module. Classing it INTERNAL_ERROR would
// bury a grant gap under the code reserved for our own bugs.
const DEPENDENCY_CODES = new Set([
  "DEPENDENCY_UNAVAILABLE", "ECONNRESET", "ECONNREFUSED", "ETIMEDOUT", "ENOTFOUND",
  "08000", "57P01", "42501",
]);

/**
 * The gaps that are structural in this release. They are published on EVERY
 * response, complete or not, because a gap that only appears when something
 * fails is a gap nobody plans around.
 */
export const ATLAS_KNOWN_GAPS = Object.freeze([
  Object.freeze({
    source_ref: "ops.scac_mutation_registry_entry", evidence_class: "installed",
    missing_reason: "no_grant",
  }),
  Object.freeze({
    source_ref: "ops/config/hooks.json + control-plane-workflows.v1.json + services.json",
    evidence_class: "declared", missing_reason: "not_in_bundle",
  }),
  Object.freeze({
    source_ref: "public.tool_call verb name", evidence_class: "observed",
    missing_reason: "column_not_granted",
  }),
  Object.freeze({
    source_ref: "verb->service", evidence_class: "inferred", missing_reason: "no_relation",
  }),
]);

// Word-bounded on purpose: a substring test would flag `updated_at` and
// `created_at`, which is exactly the kind of check that gets weakened to pass.
export const WRITE_KEYWORDS = [
  "insert", "update", "delete", "merge", "truncate", "drop", "alter", "create",
  "grant", "revoke", "copy", "call", "lock", "nextval", "setval",
];

/** A leg that is not a bare select, or that carries any write keyword, is a defect, not a query. */
export function assertReadOnly(sql) {
  const text = String(sql).toLowerCase();
  if (!text.trimStart().startsWith("select")) return false;
  if (/\bfor\s+(update|share|no\s+key\s+update)\b/.test(text)) return false;
  return !WRITE_KEYWORDS.some((keyword) => new RegExp(`\\b${keyword}\\b`).test(text));
}

function typedError(code) {
  const error = new Error(code);
  error.code = code;
  return error;
}

function classifyReadError(error) {
  if (DEPENDENCY_CODES.has(error?.code)) return "DEPENDENCY_UNAVAILABLE";
  return "INTERNAL_ERROR";
}

function isoOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.valueOf()) ? null : date.toISOString();
}

function firstSentence(text, cap = 140) {
  const flat = String(text || "").replace(/\s+/g, " ").trim();
  if (flat === "") return null;
  const stop = flat.search(/[.!?](\s|$)/);
  return (stop > 0 ? flat.slice(0, stop) : flat).slice(0, cap);
}

function node({ id, nodeClass, key, title, layer, status = null, retiredAt = null, sourceRef, evidence }) {
  return {
    id, class: nodeClass, key, title: title ?? null, layer,
    status, retired_at: retiredAt, source_ref: sourceRef, evidence, unlinked: true,
  };
}

function edge({ from, to, type, evidence, sourceRef, observedAt = null }) {
  return { from, to, type, evidence, source_ref: sourceRef, observed_at: observedAt };
}

// -----------------------------------------------------------------------------
// DECLARED LAYER — bundled registries only.
//
// Nothing here reads a database, a config file or the filesystem: the verbs come
// from the tool registry the MCP server actually serves, the operations from the
// sealed mutation registry the runtime authorizes against, and the surfaces from
// the workspace's own inventory of itself. A declared node is therefore exactly
// as true as the code that ships beside it.
// -----------------------------------------------------------------------------

const DECLARED_TOOLS_SOURCE = "mcp-server/src/tools.js";
const DECLARED_MUTATION_SOURCE = "mcp-server/src/mutation-registry.js";
const DECLARED_SURFACE_SOURCE = "mcp-server/src/workspace-surface-inventory.js";

export function buildDeclaredLayer(tools = TOOLS, operations = SCAC_MUTATION_OPERATIONS,
  surfaces = AUTHENTICATED_SURFACES) {
  const nodes = [];
  const edges = [];
  const verbNames = Object.keys(tools).sort();

  for (const name of verbNames) {
    const tool = tools[name] || {};
    nodes.push(node({
      id: `verb:${name}`, nodeClass: "verb", key: name,
      title: firstSentence(tool.description) || name, layer: "declared",
      status: tool.write === true ? (tool.humanOnly === true ? "write_human_only" : "write") : "read",
      sourceRef: DECLARED_TOOLS_SOURCE, evidence: "declared",
    }));
  }

  const verbSet = new Set(verbNames);
  const operationNames = Object.keys(operations).sort();
  for (const name of operationNames) {
    const row = operations[name] || {};
    nodes.push(node({
      id: `mutation:${name}`, nodeClass: "mutation", key: name, title: name, layer: "declared",
      status: row.write === true ? (row.human_only === true ? "write_human_only" : "write") : "read",
      sourceRef: DECLARED_MUTATION_SOURCE, evidence: "declared",
    }));
    // A registry row names its ingress explicitly. The edge follows that key
    // rather than assuming operation name === verb name, because the two are
    // separate namespaces and have drifted before.
    const ingress = typeof row.ingress_key === "string" ? row.ingress_key : "";
    if (ingress.startsWith("mcp-tool:")) {
      const verb = ingress.slice("mcp-tool:".length);
      if (verbSet.has(verb)) {
        edges.push(edge({
          from: `verb:${verb}`, to: `mutation:${name}`, type: "mutates_through",
          evidence: "declared", sourceRef: DECLARED_MUTATION_SOURCE,
        }));
      }
    }
    if (typeof row.source_locator === "string" && row.source_locator !== "") {
      const moduleId = `module:${row.source_locator}`;
      nodes.push(node({
        id: moduleId, nodeClass: "module", key: row.source_locator, title: row.source_locator,
        layer: "declared", sourceRef: DECLARED_MUTATION_SOURCE, evidence: "declared",
      }));
      edges.push(edge({
        from: `mutation:${name}`, to: moduleId, type: "implemented_in",
        evidence: "declared", sourceRef: DECLARED_MUTATION_SOURCE,
      }));
    }
    for (const target of Array.isArray(row.delegates_to) ? row.delegates_to : []) {
      // A wildcard delegation ("*registered_operation") names no specific
      // operation, so it gets no edge rather than a fabricated fan-out.
      if (typeof target !== "string" || target.startsWith("*")) continue;
      if (!operations[target]) continue;
      edges.push(edge({
        from: `mutation:${name}`, to: `mutation:${target}`, type: "delegates_to",
        evidence: "declared", sourceRef: DECLARED_MUTATION_SOURCE,
      }));
    }
  }

  for (const surface of surfaces) {
    nodes.push(node({
      id: `surface:${surface.asset}`, nodeClass: "surface", key: surface.asset,
      title: [...(surface.routes || [])].join(", ") || surface.asset, layer: "declared",
      status: surface.workspace_stylesheet ? "on_shell" : "off_shell",
      sourceRef: DECLARED_SURFACE_SOURCE, evidence: "declared",
    }));
  }

  return {
    nodes, edges,
    counts: {
      verbs: verbNames.length, mutations: operationNames.length, surfaces: surfaces.length,
      routes: WORKSPACE_ROUTES.length,
    },
    sources: [
      { source_ref: DECLARED_TOOLS_SOURCE, evidence_class: "declared" },
      { source_ref: DECLARED_MUTATION_SOURCE, evidence_class: "declared" },
      { source_ref: DECLARED_SURFACE_SOURCE, evidence_class: "declared" },
    ],
  };
}

/** Built once: the bundle does not change between requests inside one deployment. */
export const DECLARED_LAYER = buildDeclaredLayer();

// -----------------------------------------------------------------------------
// INSTALLED and OBSERVED LEGS
//
// Every leg is its own select against its own relation, and every relation below
// was checked against db/schema.sql for BOTH a `select` privilege held by
// carr_reader and the exact column names used. A leg that failed either check
// does not exist here; it is a coverage gap instead. The suite re-derives the
// readable set from the schema so a future leg cannot quietly reintroduce an
// unreadable relation behind a green fake-client run.
// -----------------------------------------------------------------------------

export const ATLAS_LEGS = [
  {
    key: "service", layer: "installed", sourceRef: "ops.service",
    sql: `select s.key as service_key, s.name as service_name, s.criticality,
                 s.retired_at, s.updated_at
            from ops.service s
           order by s.key asc
           limit $1::int`,
    params: ({ fetch }) => [fetch],
    emit(row, out) {
      out.nodes.push(node({
        id: `service:${row.service_key}`, nodeClass: "service", key: row.service_key,
        title: row.service_name, layer: "installed",
        status: row.retired_at ? "retired" : (row.criticality ?? null),
        retiredAt: isoOrNull(row.retired_at), sourceRef: "ops.service", evidence: "installed",
      }));
    },
  },
  {
    key: "service_environment", layer: "installed", sourceRef: "ops.service_environment",
    sql: `select s.key as service_key, se.environment, se.endpoint, se.deploy_mechanism,
                 se.updated_at
            from ops.service_environment se
            join ops.service s on s.id = se.service_id
           order by s.key asc, se.environment asc
           limit $1::int`,
    params: ({ fetch }) => [fetch],
    emit(row, out) {
      const id = `service_environment:${row.service_key}/${row.environment}`;
      out.nodes.push(node({
        id, nodeClass: "service_environment", key: `${row.service_key}/${row.environment}`,
        title: row.endpoint ?? row.deploy_mechanism ?? row.environment, layer: "installed",
        status: row.environment, sourceRef: "ops.service_environment", evidence: "installed",
      }));
      out.edges.push(edge({
        from: `service:${row.service_key}`, to: id, type: "runs_in",
        evidence: "installed", sourceRef: "ops.service_environment",
      }));
    },
  },
  {
    key: "service_dependency", layer: "installed", sourceRef: "ops.service_dependency",
    sql: `select s.key as service_key, d.key as depends_on_key
            from ops.service_dependency sd
            join ops.service s on s.id = sd.service_id
            join ops.service d on d.id = sd.depends_on_id
           order by s.key asc, d.key asc
           limit $1::int`,
    params: ({ fetch }) => [fetch],
    emit(row, out) {
      out.edges.push(edge({
        from: `service:${row.service_key}`, to: `service:${row.depends_on_key}`,
        type: "depends_on", evidence: "installed", sourceRef: "ops.service_dependency",
      }));
    },
  },
  {
    key: "job_definition", layer: "installed", sourceRef: "ops.job_definition",
    sql: `select jd.key as definition_key, jd.version, jd.enabled, jd.risk,
                 jd.execution_kind, jd.legacy_disabled_at, jd.updated_at
            from ops.job_definition jd
           order by jd.key asc, jd.version desc
           limit $1::int`,
    params: ({ fetch }) => [fetch],
    emit(row, out) {
      out.nodes.push(node({
        id: `job_definition:${row.definition_key}`, nodeClass: "job_definition",
        key: row.definition_key, title: `${row.execution_kind ?? "job"} - risk ${row.risk ?? "?"}`,
        layer: "installed", status: row.enabled === false ? "disabled" : "enabled",
        retiredAt: isoOrNull(row.legacy_disabled_at),
        sourceRef: "ops.job_definition", evidence: "installed",
      }));
    },
  },
  {
    key: "job", layer: "installed", sourceRef: "ops.job",
    // The most recent jobs only: this is an inventory read, not a run log. The
    // page cap bounds it and `coverage.complete` says so when it bites.
    sql: `select j.definition_key, j.state, j.mode, j.updated_at
            from ops.job j
           order by j.updated_at desc, j.id asc
           limit $1::int`,
    params: ({ fetch }) => [fetch],
    emit(row, out) {
      out.evidence.push({
        node: `job_definition:${row.definition_key}`,
        observed_at: isoOrNull(row.updated_at), source_ref: "ops.job",
        status: `${row.mode ?? "live"}:${row.state ?? "unknown"}`,
      });
    },
  },
  {
    key: "rule", layer: "installed", sourceRef: "ops.rule_admission",
    // DELIBERATELY NO JOIN TO public.rule: db/schema.sql gives SELECT on it to
    // carr_writer and carr_authority only, never carr_reader, so a join would
    // 42501 on every production request while every fake-client test stayed green.
    sql: `select a.rule_id::text as rule_id, a.state, a.enforcement_class,
                 a.enforcement_status, a.binding_moment, a.reason, a.updated_at
            from ops.rule_admission a
           order by a.updated_at desc, a.rule_id asc
           limit $1::int`,
    params: ({ fetch }) => [fetch],
    emit(row, out) {
      out.nodes.push(node({
        id: `rule:${row.rule_id}`, nodeClass: "rule", key: row.rule_id,
        title: firstSentence(row.reason) || `${row.enforcement_class} - ${row.binding_moment}`,
        layer: "installed",
        status: row.state === "rejected" ? "retired" : (row.enforcement_status ?? row.state),
        retiredAt: row.state === "rejected" ? isoOrNull(row.updated_at) : null,
        sourceRef: "ops.rule_admission", evidence: "installed",
      }));
    },
  },
  {
    key: "rule_pack", layer: "installed", sourceRef: "ops.rule_pack",
    sql: `select p.pack, p.title, p.source, p.updated_at
            from ops.rule_pack p
           order by p.pack asc
           limit $1::int`,
    params: ({ fetch }) => [fetch],
    emit(row, out) {
      out.nodes.push(node({
        id: `rule_pack:${row.pack}`, nodeClass: "rule_pack", key: row.pack,
        title: row.title, layer: "installed", status: row.source ?? null,
        sourceRef: "ops.rule_pack", evidence: "installed",
      }));
    },
  },
  {
    key: "enforcement_point", layer: "installed", sourceRef: "ops.rule_enforcement_point",
    sql: `select ep.rule_id::text as rule_id, ep.control_key, ep.implementation_ref,
                 ep.enforcement_class, ep.installed, ep.verified_at
            from ops.rule_enforcement_point ep
           order by ep.control_key asc, ep.rule_id asc
           limit $1::int`,
    params: ({ fetch }) => [fetch],
    emit(row, out) {
      const id = `control:${row.control_key}`;
      out.nodes.push(node({
        id, nodeClass: "control", key: row.control_key, title: row.implementation_ref,
        layer: "installed", status: row.installed === true ? "installed" : "declared_only",
        sourceRef: "ops.rule_enforcement_point", evidence: "installed",
      }));
      out.edges.push(edge({
        from: `rule:${row.rule_id}`, to: id, type: "enforced_by", evidence: "installed",
        sourceRef: "ops.rule_enforcement_point", observedAt: isoOrNull(row.verified_at),
      }));
    },
  },
  {
    key: "control_binding", layer: "installed", sourceRef: "ops.rule_control_binding",
    sql: `select b.rule_id::text as rule_id, b.control_key, b.bound_at
            from ops.rule_control_binding b
           order by b.bound_at desc, b.rule_id asc
           limit $1::int`,
    params: ({ fetch }) => [fetch],
    emit(row, out) {
      out.edges.push(edge({
        from: `rule:${row.rule_id}`, to: `control:${row.control_key}`, type: "bound_to",
        evidence: "installed", sourceRef: "ops.rule_control_binding",
        observedAt: isoOrNull(row.bound_at),
      }));
    },
  },
  {
    key: "load_layer", layer: "installed", sourceRef: "ops.rule_load_layer",
    sql: `select l.rule_id::text as rule_id, l.short_id, l.load_layer, l.scope, l.packs
            from ops.rule_load_layer l
           order by l.short_id asc
           limit $1::int`,
    params: ({ fetch }) => [fetch],
    emit(row, out) {
      for (const pack of Array.isArray(row.packs) ? row.packs : []) {
        out.edges.push(edge({
          from: `rule:${row.rule_id}`, to: `rule_pack:${pack}`, type: "loaded_in_pack",
          evidence: "installed", sourceRef: "ops.rule_load_layer",
        }));
      }
    },
  },
  {
    key: "doctrine_edge", layer: "installed", sourceRef: "public.doctrine_edge",
    sql: `select e.source_section_id::text as source_section_id,
                 e.target_section_id::text as target_section_id,
                 e.edge_type, t.acyclic, e.retired_by_revision_id::text as retired_by,
                 e.created_at
            from public.doctrine_edge e
            left join public.doctrine_edge_type t on t.edge_type = e.edge_type
           order by e.created_at desc, e.id asc
           limit $1::int`,
    params: ({ fetch }) => [fetch],
    emit(row, out) {
      for (const section of [row.source_section_id, row.target_section_id]) {
        out.nodes.push(node({
          id: `doctrine_section:${section}`, nodeClass: "doctrine_section", key: section,
          title: section, layer: "installed",
          status: row.retired_by ? "retired" : "live",
          retiredAt: row.retired_by ? isoOrNull(row.created_at) : null,
          sourceRef: "public.doctrine_edge", evidence: "installed",
        }));
      }
      out.edges.push(edge({
        from: `doctrine_section:${row.source_section_id}`,
        to: `doctrine_section:${row.target_section_id}`,
        type: row.edge_type, evidence: "installed", sourceRef: "public.doctrine_edge",
        observedAt: isoOrNull(row.created_at),
      }));
    },
  },
  {
    key: "doctrine_link", layer: "installed", sourceRef: "public.doctrine_link",
    sql: `select k.source_section_id::text as source_section_id, k.target_kind,
                 k.target_id::text as target_id, k.role, k.created_at
            from public.doctrine_link k
           order by k.created_at desc, k.id asc
           limit $1::int`,
    params: ({ fetch }) => [fetch],
    emit(row, out) {
      out.edges.push(edge({
        from: `doctrine_section:${row.source_section_id}`,
        to: `${row.target_kind}:${row.target_id}`, type: row.role ?? "citation",
        evidence: "installed", sourceRef: "public.doctrine_link",
        observedAt: isoOrNull(row.created_at),
      }));
    },
  },
  {
    key: "service_health", layer: "observed", sourceRef: "ops.v_service_environment_health",
    sql: `select h.service_key, h.environment, h.health, h.freshness_state,
                 h.observed_at, h.source_ref
            from ops.v_service_environment_health h
           order by h.service_key asc, h.environment asc
           limit $1::int`,
    params: ({ fetch }) => [fetch],
    emit(row, out) {
      out.evidence.push({
        node: `service_environment:${row.service_key}/${row.environment}`,
        observed_at: isoOrNull(row.observed_at),
        source_ref: row.source_ref || "ops.v_service_environment_health",
        status: row.freshness_state === "fresh" ? row.health : "unknown",
      });
    },
  },
  {
    key: "job_run", layer: "observed", sourceRef: "ops.v_job_run",
    sql: `select s.key as service_key, r.environment, r.state, r.observed_at, r.source_ref
            from ops.v_job_run r
            join ops.service s on s.id = r.service_id
           order by r.observed_at desc
           limit $1::int`,
    params: ({ fetch }) => [fetch],
    emit(row, out) {
      out.evidence.push({
        node: `service:${row.service_key}`, observed_at: isoOrNull(row.observed_at),
        source_ref: row.source_ref || "ops.v_job_run", status: row.state ?? null,
      });
    },
  },
  {
    key: "workflow_acceptance", layer: "observed", sourceRef: "ops.workflow_acceptance",
    // A workflow has no declared node in this bundle (its config file is not
    // bundled — see ATLAS_KNOWN_GAPS), so its only evidence is an observation.
    // The node is therefore born OBSERVED and labelled so, never promoted.
    sql: `select w.workflow_key, w.workflow_version, w.mode, w.status, w.receipt_ref,
                 w.created_at
            from ops.workflow_acceptance w
           order by w.created_at desc, w.id asc
           limit $1::int`,
    params: ({ fetch }) => [fetch],
    emit(row, out) {
      const id = `workflow:${row.workflow_key}`;
      out.nodes.push(node({
        id, nodeClass: "workflow", key: row.workflow_key,
        title: `v${row.workflow_version} - ${row.mode}`, layer: "observed",
        status: row.status ?? null, sourceRef: "ops.workflow_acceptance", evidence: "observed",
      }));
      out.evidence.push({
        node: id, observed_at: isoOrNull(row.created_at),
        source_ref: row.receipt_ref || "ops.workflow_acceptance", status: row.status ?? null,
      });
    },
  },
  {
    key: "rule_enforcement_status", layer: "observed", sourceRef: "ops.v_rule_enforcement_status",
    sql: `select v.rule_id::text as rule_id, v.policy_status, v.enforcement_status,
                 v.installed_controls, v.approved_and_activated_at
            from ops.v_rule_enforcement_status v
           limit $1::int`,
    params: ({ fetch }) => [fetch],
    emit(row, out) {
      out.evidence.push({
        node: `rule:${row.rule_id}`, observed_at: isoOrNull(row.approved_and_activated_at),
        source_ref: "ops.v_rule_enforcement_status",
        status: row.policy_status ?? row.enforcement_status ?? null,
      });
    },
  },
];

// -----------------------------------------------------------------------------
// Paging, filtering and the bundle digest
// -----------------------------------------------------------------------------

export function encodeCursor(id) {
  return Buffer.from(JSON.stringify({ after: String(id) }), "utf8").toString("base64");
}

export function decodeCursor(cursor) {
  if (cursor === null || cursor === undefined || cursor === "") return null;
  let parsed;
  try {
    parsed = JSON.parse(Buffer.from(String(cursor), "base64").toString("utf8"));
  } catch {
    throw typedError("AUTHORIZATION_REFUSED");
  }
  if (!parsed || typeof parsed.after !== "string" || parsed.after === "") {
    throw typedError("AUTHORIZATION_REFUSED");
  }
  return parsed.after;
}

function normalizeLayer(value) {
  if (value === null || value === undefined || value === "" || value === "all") return ATLAS_LAYERS;
  const list = (Array.isArray(value) ? value : String(value).split(","))
    .map((entry) => String(entry).trim()).filter((entry) => entry !== "");
  if (list.length === 0) return ATLAS_LAYERS;
  if (list.some((entry) => !ATLAS_LAYERS.includes(entry))) throw typedError("AUTHORIZATION_REFUSED");
  return ATLAS_LAYERS.filter((layer) => list.includes(layer));
}

function normalizeFlag(value) {
  if (value === null || value === undefined || value === "") return false;
  if (value === true || value === "true") return true;
  if (value === false || value === "false") return false;
  throw typedError("AUTHORIZATION_REFUSED");
}

function normalizeLimit(value) {
  if (value === null || value === undefined || value === "") return ATLAS_LIMIT_DEFAULT;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) throw typedError("AUTHORIZATION_REFUSED");
  return Math.min(parsed, ATLAS_LIMIT_MAX);
}

function normalizeQuery(value) {
  if (value === null || value === undefined) return null;
  const text = String(value).trim().toLowerCase();
  return text === "" ? null : text;
}

async function sha256Hex(value) {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/**
 * The bundle digest identifies WHAT WAS DECLARED, and nothing else. It is
 * deliberately separate from observed_at: two responses taken a minute apart
 * carry the same bundle_digest and different observed_at, so a consumer can tell
 * "the system changed" from "the clock moved".
 */
export async function bundleDigest(declared = DECLARED_LAYER) {
  const verbs = declared.nodes.filter((item) => item.class === "verb").map((item) => item.key).sort();
  const surfaces = declared.nodes.filter((item) => item.class === "surface").map((item) => item.key).sort();
  return sha256Hex(JSON.stringify({
    registry_version: SCAC_MUTATION_REGISTRY_VERSION,
    registry_digest: SCAC_MUTATION_REGISTRY_DIGEST,
    tools: verbs,
    surfaces,
  }));
}

function matchesQuery(item, q) {
  if (!q) return true;
  return `${item.id} ${item.key} ${item.title ?? ""}`.toLowerCase().includes(q);
}

/**
 * The Atlas inventory graph. Pure: the caller injects `client`
 * ({ query(sql, params) }) and `now`. Server-scoped: the tenant is derived from
 * the actor and never taken from a caller argument.
 */
export async function readAtlasInventoryGraph({
  client, actor, tenant = organizationTenantForActor(actor), correlationId,
  now = () => new Date(), layer, q, include_retired, limit, cursor = null,
  tools = null,
}) {
  if (!correlationId || typeof correlationId !== "string") throw typedError("INTERNAL_ERROR");
  const boundTenant = organizationTenantForActor(actor);
  if (tenant !== boundTenant || tenant !== TENANT) throw typedError("TENANT_SCOPE_REFUSED");
  if (!actor?.slug || !VALID_ACTORS.has(actor.slug)) throw typedError("AUTHORIZATION_REFUSED");

  const layers = normalizeLayer(layer);
  const query = normalizeQuery(q);
  const includeRetired = normalizeFlag(include_retired);
  const pageLimit = normalizeLimit(limit);
  const after = decodeCursor(cursor);
  const declared = tools ? buildDeclaredLayer(tools) : DECLARED_LAYER;

  const observedAtDate = now();
  const observedAt = observedAtDate.toISOString();

  const nodeMap = new Map();
  const allEdges = [];
  const coverage = [];
  const evidenceRows = [];

  // A node seen twice keeps its strongest layer: declared beats installed beats
  // observed, because a promise plus a row is still a promise kept.
  const LAYER_RANK = { declared: 3, installed: 2, observed: 1 };
  function addNode(candidate) {
    const existing = nodeMap.get(candidate.id);
    if (!existing) { nodeMap.set(candidate.id, { ...candidate }); return; }
    if (LAYER_RANK[candidate.layer] > LAYER_RANK[existing.layer]) {
      nodeMap.set(candidate.id, { ...candidate });
    }
  }

  if (layers.includes("declared")) {
    for (const item of declared.nodes) addNode(item);
    allEdges.push(...declared.edges);
    for (const source of declared.sources) {
      coverage.push({ ...source, node_count: 0, edge_count: 0, complete: true, missing_reason: null });
    }
  }
  if (layers.includes("declared")) {
    for (const entry of coverage) {
      entry.node_count = declared.nodes.filter((item) => item.source_ref === entry.source_ref).length;
      entry.edge_count = declared.edges.filter((item) => item.source_ref === entry.source_ref).length;
    }
  }

  const legs = ATLAS_LEGS.filter((leg) => layers.includes(leg.layer));
  const fetch = Math.min(pageLimit * 4, ATLAS_LIMIT_MAX);

  const results = await Promise.all(legs.map(async (leg) => {
    if (!assertReadOnly(leg.sql)) throw typedError("INTERNAL_ERROR");
    try {
      const result = await client.query(leg.sql, leg.params({ fetch }));
      return { leg, rows: result?.rows || [] };
    } catch (error) {
      // A failed leg leaves every other leg standing: the atlas says which
      // source it could not reach rather than shrinking in silence.
      return { leg, error: classifyReadError(error) };
    }
  }));

  for (const result of results) {
    const { leg } = result;
    if (result.error) {
      coverage.push({
        source_ref: leg.sourceRef, evidence_class: leg.layer, node_count: 0, edge_count: 0,
        complete: false, missing_reason: result.error,
      });
      continue;
    }
    const out = { nodes: [], edges: [], evidence: [] };
    for (const row of result.rows) leg.emit(row, out);
    for (const item of out.nodes) addNode(item);
    allEdges.push(...out.edges);
    evidenceRows.push(...out.evidence);
    const capped = result.rows.length >= fetch;
    coverage.push({
      source_ref: leg.sourceRef, evidence_class: leg.layer,
      node_count: out.nodes.length, edge_count: out.edges.length,
      complete: !capped, missing_reason: capped ? "page_capped" : null,
    });
  }

  // Observed evidence ATTACHES; it never creates a declared or installed fact.
  for (const row of evidenceRows) {
    const target = nodeMap.get(row.node);
    if (!target) continue;
    const current = target.observed_at;
    if (current && row.observed_at && current >= row.observed_at) continue;
    target.evidence = "observed";
    target.observed_at = row.observed_at;
    target.observed_status = row.status ?? null;
    target.observed_source_ref = row.source_ref;
  }

  for (const gap of ATLAS_KNOWN_GAPS) {
    coverage.push({ ...gap, node_count: 0, edge_count: 0, complete: false });
  }

  const visible = [...nodeMap.values()]
    .filter((item) => (includeRetired ? true : item.retired_at === null))
    .filter((item) => matchesQuery(item, query))
    .sort((a, b) => (a.id === b.id ? 0 : a.id < b.id ? -1 : 1));

  const start = after === null ? 0 : visible.findIndex((item) => item.id === after) + 1;
  // A cursor naming an id this filter no longer yields cannot be continued
  // deterministically; refusing beats silently restarting at page one.
  if (after !== null && start === 0) throw typedError("AUTHORIZATION_REFUSED");
  const page = visible.slice(start, start + pageLimit);
  const truncated = start + page.length < visible.length;
  const nextCursor = truncated && page.length > 0 ? encodeCursor(page[page.length - 1].id) : null;

  const pageIds = new Set(page.map((item) => item.id));
  const edges = allEdges.filter((item) => pageIds.has(item.from) && pageIds.has(item.to));
  const linked = new Set();
  for (const item of edges) { linked.add(item.from); linked.add(item.to); }
  for (const item of page) item.unlinked = !linked.has(item.id);

  const index = {};
  for (const layerName of layers) {
    index[layerName] = {};
    for (const item of page) {
      if (item.layer !== layerName) continue;
      (index[layerName][item.class] ||= []).push(item.id);
    }
  }

  const failed = coverage.filter((entry) => entry.missing_reason === "DEPENDENCY_UNAVAILABLE" ||
    entry.missing_reason === "INTERNAL_ERROR" || entry.missing_reason === "page_capped");
  return {
    version: {
      bundle_digest: await bundleDigest(declared),
      registry_version: SCAC_MUTATION_REGISTRY_VERSION,
      registry_digest: SCAC_MUTATION_REGISTRY_DIGEST,
      declared_counts: declared.counts,
    },
    observed_at: observedAt,
    viewer: actor.slug,
    tenant,
    layer: layers,
    q: query,
    include_retired: includeRetired,
    limit: pageLimit,
    nodes: page,
    edges,
    index,
    coverage,
    truncated,
    next_cursor: nextCursor,
    source: {
      source: "atlas_inventory_graph",
      source_ref: [...new Set(coverage.map((entry) => entry.source_ref))].join("+"),
      observed_at: observedAt,
      correlation_id: correlationId,
      freshness: failed.length > 0 ? "unknown" : "fresh",
      safe_explanation: failed.length > 0
        ? `This atlas is INCOMPLETE, not empty: ${failed.map((entry) => entry.source_ref).join(", ")} could not be read in full. Every other source answered at request time.`
        : "Every reachable source answered a no-store request-time read; the four structural gaps are named in coverage.",
    },
  };
}
