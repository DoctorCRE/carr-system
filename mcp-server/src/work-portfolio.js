// DoctorCRE v5 slice V5-S00: the typed portfolio hierarchy.
//
// Validates one portfolio revision against a closed contract (21 master
// milestone nodes, four immutable child-program references, an acyclic parent
// hierarchy and an acyclic dependency DAG) and derives its digests over
// versioned closed preimages. Canonicalization and hashing come from
// artifact-trust.js; this file does not reimplement either.
//
// TWO DIGESTS. The GRAPH digest covers the settled shape. The ACCEPTED digest
// covers the graph PLUS every child binding — identity, ordinal, version, the
// child's own content digest and its applicable accepted source. Those decide
// what a descendant inherits, so acceptance binds the accepted digest; a hash
// that omitted them would let the governing facts move under an unchanged
// signature. The child hashes only its own content, so neither hash is
// recursive.
//
// A revision carrying an executable-effect field is refused, not ignored: the
// authority argument for this substrate is that a portfolio is inert until a
// human accepts its exact hash through a separate path.
//
// The persistence and verb layers live here too, but every write goes through a
// database function that derives its own actor: no verb in this file takes an
// actor, a partner or a tenant.

import { canonicalJson, digest } from "./artifact-trust.js";

export const PORTFOLIO_REVISION_SCHEMA_VERSION = "doctorcre-v5-portfolio-revision.v1";
export const MASTER_NODE_COUNT = 21;
export const CHILD_PROGRAM_COUNT = 4;

// Immutable identity from the accepted r7 design, not configuration: a rename
// or reorder is a different portfolio.
export const CHILD_PROGRAM_REFS = Object.freeze([
  "foundation-and-control-plane",
  "assurance-fabric",
  "product-journeys",
  "rollout-and-retirement",
]);

export const NODE_KINDS = Object.freeze(["portfolio", "child", "milestone", "slice"]);
export const SOURCE_DIGEST_KEYS = Object.freeze(["constitution", "design", "integration", "requirements"]);

const REVISION_KEYS = Object.freeze([
  "schema_version", "revision_version", "portfolio_ref", "source_digests",
  "child_program_refs", "nodes", "edges",
]);

const NODE_REQUIRED_KEYS = Object.freeze(["node_ref", "node_kind", "ordinal", "parent_ref"]);
const NODE_OPTIONAL_KEYS = Object.freeze([
  "authority_class", "effect_class", "data_class",
  "budget_identity", "budget_ceiling",
  "model_floor", "recovery_ref", "terminal_predicate",
]);
const NODE_KEYS = Object.freeze([...NODE_REQUIRED_KEYS, ...NODE_OPTIONAL_KEYS]);
const EDGE_KEYS = Object.freeze(["from_node_ref", "to_node_ref"]);
const MODEL_FLOOR_KEYS = Object.freeze(["provider", "model", "version", "effort"]);

// Optional metadata is never invented, but a present value must be well formed:
// an unvalidated slot is hashed into the acceptance digest as-is.
const CLASS_TOKEN = /^[a-z][a-z0-9_]{1,63}$/;
const MAX_BUDGET_CEILING = 1e12;

// Field names meaning this inert record reached into execution. Matched on the
// normalized key at every depth, so a wrapper object cannot smuggle one in.
const EFFECT_KEY_FRAGMENTS = Object.freeze([
  "ops_job", "job_id", "job_ref", "jobs",
  "capability", "envelope", "admission", "admit",
  "schedule", "scheduler", "cron",
  "acceptance", "accepted_by", "accepted_at", "acceptor",
  "outcome_receipt", "deployment", "release_ref", "j1_clock",
]);

const SHA256_REF = /^sha256:[0-9a-f]{64}$/;
// Refs span the lower-case step namespace and the upper-case Work Request one.
const REF = /^[A-Za-z0-9][A-Za-z0-9:._-]{2,199}$/;

export class PortfolioValidationError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "PortfolioValidationError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function refuse(code, message, detail) {
  throw new PortfolioValidationError(code, message, detail);
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

/** Locale-independent code-unit ordering. localeCompare varies by ICU build. */
function compareCodeUnits(a, b) {
  if (a === b) return 0;
  return a < b ? -1 : 1;
}

// JSON-safe means canonicalJson round-trips it with its meaning intact. Beyond
// the obvious unhashable types this rejects the shapes that would silently hash
// a DIFFERENT payload than the caller holds: a cycle (canonicalJson would
// recurse forever), a sparse array (holes serialize as null), and symbol,
// accessor or non-enumerable properties (Object.keys skips them, so two objects
// that differ would hash identically).
function assertJsonSafe(value, path, stack = new Set()) {
  if (value === undefined) refuse("unsupported_value", `undefined is not hashable at ${path}`, { path });
  if (value === null) return;
  const type = typeof value;
  if (type === "string" || type === "boolean") return;
  if (type === "number") {
    if (!Number.isFinite(value)) refuse("unsupported_value", `non-finite number at ${path}`, { path });
    return;
  }
  if (type === "bigint" || type === "function" || type === "symbol") {
    refuse("unsupported_value", `${type} is not hashable at ${path}`, { path });
  }
  if (stack.has(value)) {
    refuse("cyclic_input", `object graph is cyclic at ${path}; it cannot be canonicalized`, { path });
  }
  const isArray = Array.isArray(value);
  if (!isArray && !isPlainObject(value)) {
    refuse("unsupported_value", `only plain JSON objects and arrays are hashable at ${path}`, { path });
  }
  if (Object.getOwnPropertySymbols(value).length > 0) {
    refuse("unsupported_value", `symbol-keyed property at ${path} would be dropped by canonicalization`, { path });
  }
  const descriptors = Object.getOwnPropertyDescriptors(value);
  for (const [key, descriptor] of Object.entries(descriptors)) {
    if (isArray && key === "length") continue;
    if (descriptor.get !== undefined || descriptor.set !== undefined) {
      refuse("unsupported_value", `accessor property "${key}" at ${path} is not a stable value`, { path, key });
    }
    if (!descriptor.enumerable) {
      refuse("unsupported_value", `non-enumerable property "${key}" at ${path} would be dropped by canonicalization`,
        { path, key });
    }
  }
  stack.add(value);
  if (isArray) {
    for (let i = 0; i < value.length; i += 1) {
      if (!Object.prototype.hasOwnProperty.call(value, i)) {
        refuse("unsupported_value", `sparse array hole at ${path}[${i}] would serialize as null`, { path, index: i });
      }
      assertJsonSafe(value[i], `${path}[${i}]`, stack);
    }
    const indexCount = Object.keys(descriptors).filter(key => key !== "length").length;
    if (indexCount !== value.length) {
      refuse("unsupported_value", `array at ${path} carries non-index own properties`, { path });
    }
  } else {
    for (const key of Object.keys(value)) assertJsonSafe(value[key], `${path}.${key}`, stack);
  }
  stack.delete(value);
}

function assertNoEffectPayload(value, path) {
  if (Array.isArray(value)) {
    value.forEach((item, i) => assertNoEffectPayload(item, `${path}[${i}]`));
    return;
  }
  if (!isPlainObject(value)) return;
  for (const key of Object.keys(value)) {
    const normalized = key.toLowerCase();
    for (const fragment of EFFECT_KEY_FRAGMENTS) {
      if (normalized === fragment || normalized.endsWith(`_${fragment}`) || normalized.startsWith(`${fragment}_`)) {
        refuse("effect_payload_refused",
          `a portfolio revision may not carry executable-effect field "${key}" at ${path}`,
          { path: `${path}.${key}`, key });
      }
    }
    assertNoEffectPayload(value[key], `${path}.${key}`);
  }
}

function assertClosedKeys(object, allowed, path) {
  for (const key of Object.keys(object)) {
    if (!allowed.includes(key)) {
      refuse("unknown_field", `unknown field "${key}" at ${path}`, { path: `${path}.${key}`, key });
    }
  }
}

function assertRef(value, path) {
  if (typeof value !== "string" || !REF.test(value)) {
    refuse("invalid_ref", `expected a reference string at ${path}`, { path, value });
  }
}

function assertClassToken(value, path) {
  if (typeof value !== "string" || !CLASS_TOKEN.test(value)) {
    refuse("invalid_metadata", `${path} must be a lower-case classification token`, { path, value });
  }
}

/** Type and range checks for the optional typed metadata slots. */
function validateNodeMetadata(node, path) {
  for (const key of ["authority_class", "effect_class", "data_class"]) {
    if (key in node) assertClassToken(node[key], `${path}.${key}`);
  }
  if ("budget_identity" in node) {
    if (typeof node.budget_identity !== "string" || node.budget_identity.length === 0) {
      refuse("invalid_metadata", `${path}.budget_identity must be a non-empty string`, { path: `${path}.budget_identity` });
    }
  }
  if ("budget_ceiling" in node) {
    const ceiling = node.budget_ceiling;
    if (typeof ceiling !== "number" || !Number.isFinite(ceiling) || ceiling < 0 || ceiling > MAX_BUDGET_CEILING) {
      refuse("invalid_metadata",
        `${path}.budget_ceiling must be a finite number between 0 and ${MAX_BUDGET_CEILING}`,
        { path: `${path}.budget_ceiling`, value: ceiling });
    }
  }
  if ("recovery_ref" in node) assertRef(node.recovery_ref, `${path}.recovery_ref`);
  if ("terminal_predicate" in node) {
    if (typeof node.terminal_predicate !== "string" || node.terminal_predicate.length === 0) {
      refuse("invalid_metadata", `${path}.terminal_predicate must be a non-empty string`,
        { path: `${path}.terminal_predicate` });
    }
  }
  if ("model_floor" in node) {
    const floor = node.model_floor;
    const floorPath = `${path}.model_floor`;
    if (!isPlainObject(floor)) {
      refuse("invalid_metadata", `${floorPath} must be an object`, { path: floorPath });
    }
    assertClosedKeys(floor, MODEL_FLOOR_KEYS, floorPath);
    for (const key of MODEL_FLOOR_KEYS) {
      if (!(key in floor)) {
        refuse("invalid_metadata", `${floorPath}.${key} is required once a model floor is stated`,
          { path: `${floorPath}.${key}` });
      }
      if (typeof floor[key] !== "string" || floor[key].length === 0) {
        refuse("invalid_metadata", `${floorPath}.${key} must be a non-empty string`, { path: `${floorPath}.${key}` });
      }
    }
  }
}

function validateSourceDigests(source, path) {
  if (!isPlainObject(source)) refuse("invalid_shape", `${path} must be an object`, { path });
  assertClosedKeys(source, SOURCE_DIGEST_KEYS, path);
  for (const key of SOURCE_DIGEST_KEYS) {
    const value = source[key];
    if (typeof value !== "string" || !SHA256_REF.test(value)) {
      refuse("invalid_source_digest", `${path}.${key} must be a sha256: reference`, { path: `${path}.${key}` });
    }
  }
}

function validateNodes(nodes) {
  if (!Array.isArray(nodes)) refuse("invalid_shape", "revision.nodes must be an array", { path: "revision.nodes" });
  if (nodes.length !== MASTER_NODE_COUNT) {
    refuse("node_count", `expected exactly ${MASTER_NODE_COUNT} master nodes, saw ${nodes.length}`,
      { expected: MASTER_NODE_COUNT, actual: nodes.length });
  }
  const seen = new Map();
  nodes.forEach((node, index) => {
    const path = `revision.nodes[${index}]`;
    if (!isPlainObject(node)) refuse("invalid_shape", `${path} must be an object`, { path });
    assertClosedKeys(node, NODE_KEYS, path);
    for (const key of NODE_REQUIRED_KEYS) {
      if (!(key in node)) refuse("missing_field", `${path}.${key} is required`, { path: `${path}.${key}` });
    }
    // Absent is the only way to write "not stated": an explicit null would give
    // "not stated" two distinct preimages.
    for (const key of NODE_OPTIONAL_KEYS) {
      if (key in node && node[key] === null) {
        refuse("unsupported_value", `${path}.${key} may be absent or stated, never null`, { path: `${path}.${key}` });
      }
    }
    assertRef(node.node_ref, `${path}.node_ref`);
    assertRef(node.parent_ref, `${path}.parent_ref`);
    if (!NODE_KINDS.includes(node.node_kind)) {
      refuse("invalid_node_kind", `${path}.node_kind must be one of ${NODE_KINDS.join(", ")}`,
        { path: `${path}.node_kind`, value: node.node_kind });
    }
    if (node.ordinal !== index + 1) {
      refuse("invalid_ordinal", `${path}.ordinal must be ${index + 1}; nodes are an ordered set`,
        { path: `${path}.ordinal`, expected: index + 1, actual: node.ordinal });
    }
    if (seen.has(node.node_ref)) {
      refuse("duplicate_node", `node_ref "${node.node_ref}" appears at ordinal ${seen.get(node.node_ref)} and ${node.ordinal}`,
        { node_ref: node.node_ref });
    }
    validateNodeMetadata(node, path);
    seen.set(node.node_ref, node.ordinal);
  });
  return seen;
}

function validateEdges(edges, knownNodes) {
  if (!Array.isArray(edges)) refuse("invalid_shape", "revision.edges must be an array", { path: "revision.edges" });
  const seen = new Set();
  edges.forEach((edge, index) => {
    const path = `revision.edges[${index}]`;
    if (!isPlainObject(edge)) refuse("invalid_shape", `${path} must be an object`, { path });
    assertClosedKeys(edge, EDGE_KEYS, path);
    for (const key of EDGE_KEYS) {
      if (!(key in edge)) refuse("missing_field", `${path}.${key} is required`, { path: `${path}.${key}` });
      assertRef(edge[key], `${path}.${key}`);
    }
    if (!knownNodes.has(edge.from_node_ref)) {
      refuse("dangling_edge", `${path}.from_node_ref "${edge.from_node_ref}" is not a node in this revision`,
        { path, node_ref: edge.from_node_ref });
    }
    if (!knownNodes.has(edge.to_node_ref)) {
      refuse("dangling_edge", `${path}.to_node_ref "${edge.to_node_ref}" is not a node in this revision`,
        { path, node_ref: edge.to_node_ref });
    }
    if (edge.from_node_ref === edge.to_node_ref) {
      refuse("cycle", `${path} is a self-edge on "${edge.from_node_ref}"`, { cycle: [edge.from_node_ref] });
    }
    const key = `${edge.from_node_ref} ${edge.to_node_ref}`;
    if (seen.has(key)) refuse("duplicate_edge", `${path} repeats an edge already declared`, { path });
    seen.add(key);
  });
}

/**
 * The parent hierarchy is a separate structure from the dependency DAG and
 * needs its own cycle check: a self-parent or a parent loop leaves every node
 * closure-valid while belonging to no portfolio.
 */
function validateParentHierarchy(nodes, knownNodes, portfolioRef) {
  const parentOf = new Map(nodes.map(node => [node.node_ref, node.parent_ref]));
  for (const node of nodes) {
    if (node.parent_ref === node.node_ref) {
      refuse("parent_cycle", `node "${node.node_ref}" is its own parent`, { cycle: [node.node_ref] });
    }
    if (node.parent_ref !== portfolioRef && !knownNodes.has(node.parent_ref)) {
      refuse("dangling_edge",
        `node "${node.node_ref}" names parent "${node.parent_ref}", which is neither the portfolio nor a node in this revision`,
        { node_ref: node.node_ref, parent_ref: node.parent_ref });
    }
    const walked = [node.node_ref];
    const seen = new Set(walked);
    let current = node.parent_ref;
    while (current !== portfolioRef) {
      if (seen.has(current)) {
        const start = walked.indexOf(current);
        refuse("parent_cycle",
          `the parent hierarchy loops: ${walked.slice(start).concat(current).join(" -> ")}`,
          { cycle: walked.slice(start) });
      }
      seen.add(current);
      walked.push(current);
      current = parentOf.get(current);
    }
  }
}

/**
 * One actual cycle from the dependency graph, by depth-first search over the
 * grey stack. Reported instead of Kahn's residual set, which also contains
 * nodes merely downstream of a cycle and would name innocent nodes as members.
 */
function findDependencyCycle(nodes, outgoing) {
  const WHITE = 0, GREY = 1, BLACK = 2;
  const colour = new Map(nodes.map(node => [node.node_ref, WHITE]));
  const stack = [];

  const visit = ref => {
    colour.set(ref, GREY);
    stack.push(ref);
    for (const next of outgoing.get(ref)) {
      const state = colour.get(next);
      if (state === GREY) return stack.slice(stack.indexOf(next));
      if (state === WHITE) {
        const found = visit(next);
        if (found) return found;
      }
    }
    stack.pop();
    colour.set(ref, BLACK);
    return null;
  };

  for (const node of nodes) {
    if (colour.get(node.node_ref) === WHITE) {
      const found = visit(node.node_ref);
      if (found) return found;
    }
  }
  return null;
}

/** Kahn's algorithm; an edge means from_node_ref precedes to_node_ref. */
function topologicalOrder(nodes, edges) {
  const indegree = new Map(nodes.map(node => [node.node_ref, 0]));
  const outgoing = new Map(nodes.map(node => [node.node_ref, []]));
  for (const edge of edges) {
    outgoing.get(edge.from_node_ref).push(edge.to_node_ref);
    indegree.set(edge.to_node_ref, indegree.get(edge.to_node_ref) + 1);
  }
  const ready = nodes.filter(node => indegree.get(node.node_ref) === 0).map(node => node.node_ref);
  const order = [];
  while (ready.length > 0) {
    const current = ready.shift();
    order.push(current);
    for (const next of outgoing.get(current)) {
      const remaining = indegree.get(next) - 1;
      indegree.set(next, remaining);
      if (remaining === 0) ready.push(next);
    }
  }
  if (order.length !== nodes.length) {
    const ordered = new Set(order);
    const blocked = nodes.map(node => node.node_ref).filter(ref => !ordered.has(ref));
    const cycle = findDependencyCycle(nodes, outgoing) ?? [];
    refuse("cycle",
      `the dependency graph is not acyclic; cycle: ${cycle.join(" -> ")}`,
      { cycle, blocked_nodes: blocked });
  }
  return order;
}

function validateChildProgramRefs(refs) {
  if (!Array.isArray(refs)) {
    refuse("invalid_shape", "revision.child_program_refs must be an array", { path: "revision.child_program_refs" });
  }
  if (refs.length !== CHILD_PROGRAM_COUNT) {
    refuse("child_count", `expected exactly ${CHILD_PROGRAM_COUNT} child-program references, saw ${refs.length}`,
      { expected: CHILD_PROGRAM_COUNT, actual: refs.length });
  }
  const seen = new Set();
  refs.forEach((ref, index) => {
    if (typeof ref !== "string") {
      refuse("invalid_shape", `revision.child_program_refs[${index}] must be a string`, { index });
    }
    if (seen.has(ref)) refuse("duplicate_child", `child-program reference "${ref}" is repeated`, { child_ref: ref });
    seen.add(ref);
    if (ref !== CHILD_PROGRAM_REFS[index]) {
      refuse("child_identity",
        `child-program reference ${index} must be "${CHILD_PROGRAM_REFS[index]}", saw "${ref}"`,
        { index, expected: CHILD_PROGRAM_REFS[index], actual: ref });
    }
  });
}

/**
 * Validate one portfolio revision against the closed contract and return a
 * frozen structural view. Throws PortfolioValidationError with a stable `code`.
 * Performs no I/O and mutates nothing.
 */
export function validatePortfolioRevision(revision) {
  if (!isPlainObject(revision)) refuse("invalid_shape", "revision must be an object", { path: "revision" });
  assertJsonSafe(revision, "revision");
  assertNoEffectPayload(revision, "revision");
  assertClosedKeys(revision, REVISION_KEYS, "revision");
  for (const key of REVISION_KEYS) {
    if (!(key in revision)) refuse("missing_field", `revision.${key} is required`, { path: `revision.${key}` });
  }
  if (revision.schema_version !== PORTFOLIO_REVISION_SCHEMA_VERSION) {
    refuse("schema_version", `revision.schema_version must be "${PORTFOLIO_REVISION_SCHEMA_VERSION}"`,
      { expected: PORTFOLIO_REVISION_SCHEMA_VERSION, actual: revision.schema_version });
  }
  if (!Number.isInteger(revision.revision_version) || revision.revision_version < 1) {
    refuse("invalid_revision_version", "revision.revision_version must be an integer of at least 1",
      { actual: revision.revision_version });
  }
  assertRef(revision.portfolio_ref, "revision.portfolio_ref");
  validateSourceDigests(revision.source_digests, "revision.source_digests");
  validateChildProgramRefs(revision.child_program_refs);
  const knownNodes = validateNodes(revision.nodes);
  if (knownNodes.has(revision.portfolio_ref)) {
    refuse("invalid_ref", `revision.portfolio_ref "${revision.portfolio_ref}" is also a master node`,
      { portfolio_ref: revision.portfolio_ref });
  }
  validateEdges(revision.edges, knownNodes);
  validateParentHierarchy(revision.nodes, knownNodes, revision.portfolio_ref);
  const order = topologicalOrder(revision.nodes, revision.edges);
  return Object.freeze({
    portfolio_ref: revision.portfolio_ref,
    revision_version: revision.revision_version,
    node_count: revision.nodes.length,
    edge_count: revision.edges.length,
    child_program_count: revision.child_program_refs.length,
    acyclic: true,
    topological_order: Object.freeze(order),
    child_program_refs: Object.freeze([...revision.child_program_refs]),
  });
}

/**
 * The versioned closed preimage the revision digest is taken over.
 *
 * Ordering is declared, not incidental: `nodes` and `child_program_refs` are
 * ordered sets whose order carries meaning, so their order is preserved;
 * `edges` is an unordered set and is sorted here by code unit, which is the one
 * place this file reorders anything. Nothing situational is bound — no
 * timestamp, maker, reviewer or acceptance fact — so two callers describing the
 * same portfolio reach the same digest.
 */
export function portfolioRevisionPreimage(revision) {
  validatePortfolioRevision(revision);
  const nodes = revision.nodes.map(node => {
    const projected = {
      node_ref: node.node_ref,
      node_kind: node.node_kind,
      ordinal: node.ordinal,
      parent_ref: node.parent_ref,
    };
    for (const key of NODE_OPTIONAL_KEYS) {
      if (key in node) projected[key] = node[key];
    }
    return projected;
  });
  const edges = revision.edges
    .map(edge => ({ from_node_ref: edge.from_node_ref, to_node_ref: edge.to_node_ref }))
    .sort((a, b) => compareCodeUnits(a.from_node_ref, b.from_node_ref)
      || compareCodeUnits(a.to_node_ref, b.to_node_ref));
  return {
    schema_version: PORTFOLIO_REVISION_SCHEMA_VERSION,
    revision_version: revision.revision_version,
    portfolio_ref: revision.portfolio_ref,
    source_digests: {
      constitution: revision.source_digests.constitution,
      design: revision.source_digests.design,
      integration: revision.source_digests.integration,
      requirements: revision.source_digests.requirements,
    },
    child_program_refs: [...revision.child_program_refs],
    nodes,
    edges,
  };
}

/** The deterministic `sha256:` digest of one validated portfolio revision. */
export function portfolioRevisionDigest(revision) {
  return digest(portfolioRevisionPreimage(revision));
}

/** The exact canonical bytes hashed, so a reviewer can check the digest by hand. */
export function portfolioRevisionCanonicalBytes(revision) {
  return canonicalJson(portfolioRevisionPreimage(revision));
}

export const PORTFOLIO_CHILD_SCHEMA_VERSION = "doctorcre-v5-portfolio-child.v1";
// The hash a partner accepts. Distinct from the graph schema version above
// because it covers strictly more: the graph AND every child binding.
export const PORTFOLIO_ACCEPTED_SCHEMA_VERSION = "doctorcre-v5-portfolio-accepted-revision.v1";

/**
 * The child preimage: one child program's own immutable identity, version and
 * ordered membership.
 *
 * A child is not an unversioned string. It carries its own version and its own
 * hash over its own content: which nodes it governs, and which accepted source
 * binding applies to it.
 *
 * THE HASH IS NOT RECURSIVE. A child hashes only content it owns. The parent
 * then hashes the complete child BINDINGS -- identity, ordinal, version, the
 * child digest and the accepted source reference -- so nothing that governs a
 * descendant sits outside the hash a partner accepts, and neither side needs
 * the other's digest to compute its own.
 */
export function portfolioChildPreimage({ child_ref, child_version, member_node_refs, accepted_plan_ref }) {
  if (!CHILD_PROGRAM_REFS.includes(child_ref)) {
    refuse("child_identity", `"${child_ref}" is not one of the four child programs`, { child_ref });
  }
  if (!Number.isInteger(child_version) || child_version < 1) {
    refuse("invalid_revision_version", "child_version must be an integer of at least 1", { child_version });
  }
  if (!Array.isArray(member_node_refs) || member_node_refs.length === 0) {
    refuse("invalid_shape", `child "${child_ref}" must name at least one member node`, { child_ref });
  }
  const seen = new Set();
  for (const ref of member_node_refs) {
    assertRef(ref, `member_node_refs of ${child_ref}`);
    if (seen.has(ref)) refuse("duplicate_node", `child "${child_ref}" repeats member "${ref}"`, { child_ref, ref });
    seen.add(ref);
  }
  // Absent means "no accepted source binding applies yet" and is written as an
  // explicit null, so a child that gains one later hashes differently.
  if (accepted_plan_ref !== null && accepted_plan_ref !== undefined) {
    assertRef(accepted_plan_ref, `accepted_plan_ref of ${child_ref}`);
  }
  return {
    schema_version: PORTFOLIO_CHILD_SCHEMA_VERSION,
    child_ref,
    child_version,
    member_node_refs: [...member_node_refs].sort(compareCodeUnits),
    accepted_plan_ref: accepted_plan_ref ?? null,
  };
}

/** The deterministic `sha256:` digest of one child program's own content. */
export function portfolioChildDigest(child) {
  return digest(portfolioChildPreimage(child));
}

/**
 * The ACCEPTED preimage: the graph plus every child binding.
 *
 * This is the hash a partner accepts, and it is deliberately not the graph
 * hash. The graph preimage answers "is this the settled 21-node shape"; it says
 * nothing about which child governs which node, what version that child is, or
 * which accepted source binding applies. Those three decide what a descendant
 * inherits, so a hash that omitted them would let the governing facts change
 * under an unchanged signature.
 *
 * The graph preimage itself is embedded unchanged, so the pure graph contract
 * keeps its own schema version and its own meaning.
 */
export function portfolioAcceptedPreimage(revision, childBindings) {
  const graph = portfolioRevisionPreimage(revision);
  if (!Array.isArray(childBindings) || childBindings.length !== CHILD_PROGRAM_COUNT) {
    refuse("child_count",
      `an accepted preimage binds exactly ${CHILD_PROGRAM_COUNT} children, saw ${childBindings?.length}`,
      { expected: CHILD_PROGRAM_COUNT, actual: childBindings?.length });
  }
  const bindings = childBindings.map((binding, index) => {
    if (!isPlainObject(binding)) {
      refuse("invalid_shape", `child_bindings[${index}] must be an object`, { index });
    }
    assertClosedKeys(binding,
      ["child_ref", "child_ordinal", "child_version", "child_digest", "accepted_plan_ref"],
      `child_bindings[${index}]`);
    if (binding.child_ordinal !== index || binding.child_ref !== CHILD_PROGRAM_REFS[index]) {
      refuse("child_identity",
        `child_bindings[${index}] must be "${CHILD_PROGRAM_REFS[index]}" at ordinal ${index}`,
        { index, expected: CHILD_PROGRAM_REFS[index], actual: binding.child_ref });
    }
    if (typeof binding.child_digest !== "string" || !SHA256_REF.test(binding.child_digest)) {
      refuse("invalid_source_digest", `child_bindings[${index}].child_digest must be a sha256: reference`,
        { index });
    }
    if (!Number.isInteger(binding.child_version) || binding.child_version < 1) {
      refuse("invalid_revision_version", `child_bindings[${index}].child_version must be an integer of at least 1`,
        { index });
    }
    if (binding.accepted_plan_ref !== null) assertRef(binding.accepted_plan_ref, `child_bindings[${index}].accepted_plan_ref`);
    return {
      child_ref: binding.child_ref,
      child_ordinal: binding.child_ordinal,
      child_version: binding.child_version,
      child_digest: binding.child_digest,
      accepted_plan_ref: binding.accepted_plan_ref,
    };
  });
  return {
    schema_version: PORTFOLIO_ACCEPTED_SCHEMA_VERSION,
    graph,
    child_bindings: bindings,
  };
}

/** The deterministic `sha256:` digest a partner accepts. */
export function portfolioAcceptedDigest(revision, childBindings) {
  return digest(portfolioAcceptedPreimage(revision, childBindings));
}

/** The exact canonical bytes of the accepted preimage, for hand-checking. */
export function portfolioAcceptedCanonicalBytes(revision, childBindings) {
  return canonicalJson(portfolioAcceptedPreimage(revision, childBindings));
}

/**
 * Persistence demands strictly more than the pure contract: every typed
 * metadata slot must be present, every node must name the child program that
 * governs it, and every child must state its own version and accepted source
 * binding.
 *
 * The pure module leaves metadata optional because the authenticated source
 * states node identity and dependency only. That is the right shape for
 * validating a graph. It is the wrong shape for storing one: a node whose
 * authority class, budget ceiling or model floor is absent cannot be governed
 * by them later, so absence is refused at the persistence boundary rather than
 * defaulted to something nobody chose.
 *
 * `childInputs` carries each child's own `child_version` and its
 * `accepted_plan_ref`. Versions are real and per-child: a child that changes
 * membership or source binding advances its own version, and the parent's
 * accepted digest moves with it.
 */
export function validatePortfolioRevisionForPersistence(revision, nodeChildRefs, childInputs = {}) {
  const view = validatePortfolioRevision(revision);
  if (!isPlainObject(nodeChildRefs)) {
    refuse("invalid_shape", "nodeChildRefs must be an object mapping node_ref to child_ref",
      { path: "nodeChildRefs" });
  }
  if (!isPlainObject(childInputs)) {
    refuse("invalid_shape", "childInputs must be an object keyed by child_ref", { path: "childInputs" });
  }
  assertClosedKeys(childInputs, [...CHILD_PROGRAM_REFS], "childInputs");
  const members = new Map(CHILD_PROGRAM_REFS.map(ref => [ref, []]));
  for (const node of revision.nodes) {
    const path = `revision.nodes[${node.ordinal - 1}]`;
    for (const key of NODE_OPTIONAL_KEYS) {
      if (!(key in node)) {
        refuse("incomplete_metadata",
          `${path}.${key} is required to persist a node; the pure contract allows it to be absent, storage does not`,
          { path: `${path}.${key}`, node_ref: node.node_ref, missing: key });
      }
    }
    const childRef = nodeChildRefs[node.node_ref];
    if (!CHILD_PROGRAM_REFS.includes(childRef)) {
      refuse("child_identity",
        `node "${node.node_ref}" must name one of the four child programs, saw ${JSON.stringify(childRef)}`,
        { node_ref: node.node_ref, child_ref: childRef });
    }
    members.get(childRef).push(node.node_ref);
  }
  const unknown = Object.keys(nodeChildRefs).filter(ref =>
    !revision.nodes.some(node => node.node_ref === ref));
  if (unknown.length > 0) {
    refuse("dangling_edge", `nodeChildRefs names ${unknown.length} node(s) not in this revision`,
      { node_refs: unknown });
  }
  const children = CHILD_PROGRAM_REFS.map((child_ref, index) => {
    const member_node_refs = members.get(child_ref);
    if (member_node_refs.length === 0) {
      refuse("child_identity", `child "${child_ref}" governs no node in this revision`, { child_ref });
    }
    const supplied = childInputs[child_ref];
    if (supplied !== undefined && !isPlainObject(supplied)) {
      refuse("invalid_shape", `childInputs.${child_ref} must be an object`, { child_ref });
    }
    if (supplied) assertClosedKeys(supplied, ["child_version", "accepted_plan_ref"], `childInputs.${child_ref}`);
    const child = {
      child_ref,
      child_version: supplied?.child_version ?? 1,
      member_node_refs,
      accepted_plan_ref: supplied?.accepted_plan_ref ?? null,
    };
    const preimage = portfolioChildPreimage(child);
    return {
      child_ref,
      child_ordinal: index,
      child_version: preimage.child_version,
      child_digest: portfolioChildDigest(child),
      accepted_plan_ref: preimage.accepted_plan_ref,
      member_node_refs: preimage.member_node_refs,
    };
  });
  const childBindings = children.map(({ child_ref, child_ordinal, child_version, child_digest, accepted_plan_ref }) =>
    ({ child_ref, child_ordinal, child_version, child_digest, accepted_plan_ref }));
  return Object.freeze({
    ...view,
    graph_digest: portfolioRevisionDigest(revision),
    accepted_digest: portfolioAcceptedDigest(revision, childBindings),
    children: Object.freeze(children),
    child_bindings: Object.freeze(childBindings),
    node_child_refs: Object.freeze({ ...nodeChildRefs }),
  });
}

/**
 * The zero-effect readback projection.
 *
 * `expected_digest` is the stale-hash guard: a caller that believed it held one
 * revision is refused rather than shown another under the old name.
 * `maker_actor`/`reviewer_actor` enforce the separation the accepted contract
 * requires. Neither is persisted; this function produces no effect, which is
 * what `effects` reports.
 */
export function portfolioReadback(revision, options = {}) {
  if (!isPlainObject(options)) refuse("invalid_shape", "options must be an object", { path: "options" });
  assertClosedKeys(options, ["expected_digest", "maker_actor", "reviewer_actor"], "options");
  const view = validatePortfolioRevision(revision);
  const revisionDigest = portfolioRevisionDigest(revision);

  if (options.expected_digest !== undefined) {
    if (typeof options.expected_digest !== "string" || !SHA256_REF.test(options.expected_digest)) {
      refuse("invalid_expected_digest", "options.expected_digest must be a sha256: reference",
        { path: "options.expected_digest" });
    }
    if (options.expected_digest !== revisionDigest) {
      refuse("stale_expected_digest",
        "the revision no longer hashes to the expected digest; re-read it rather than acting on the stale one",
        { expected: options.expected_digest, actual: revisionDigest });
    }
  }

  const maker = options.maker_actor;
  const reviewer = options.reviewer_actor;
  if (maker !== undefined || reviewer !== undefined) {
    if (typeof maker !== "string" || maker.length === 0) {
      refuse("missing_field", "options.maker_actor is required when a reviewer is named",
        { path: "options.maker_actor" });
    }
    if (typeof reviewer !== "string" || reviewer.length === 0) {
      refuse("missing_field", "options.reviewer_actor is required when a maker is named",
        { path: "options.reviewer_actor" });
    }
    if (maker === reviewer) {
      refuse("self_review", `"${maker}" cannot be both maker and independent reviewer of the same revision`,
        { actor: maker });
    }
  }

  return Object.freeze({
    schema_version: PORTFOLIO_REVISION_SCHEMA_VERSION,
    portfolio_ref: view.portfolio_ref,
    revision_version: view.revision_version,
    revision_digest: revisionDigest,
    node_count: view.node_count,
    edge_count: view.edge_count,
    child_program_count: view.child_program_count,
    child_program_refs: view.child_program_refs,
    acyclic: view.acyclic,
    topological_order: view.topological_order,
    unresolved_dependencies: Object.freeze([]),
    accepted: false,
    // Inert by construction: nothing here can produce an effect, and acceptance
    // remains a separate human exact-hash act.
    effects: Object.freeze({
      creates_effect: false,
      jobs: 0,
      capabilities: 0,
      execution_envelopes: 0,
      admissions: 0,
      schedules: 0,
      acceptances: 0,
      deployments: 0,
    }),
  });
}

/**
 * The four portfolio verbs.
 *
 * No verb takes an actor, a partner or a tenant: proposal and review derive
 * their author from the writer context the server established, and acceptance
 * runs on the per-partner authority connection whose session_user the database
 * reads. A caller can name a digest, and the database will only ever compare it
 * against one recomputed from the stored rows.
 */
export function workPortfolioTools({ withEnvelope, writeEvent, ToolError }) {
  const digestSchema = { type: "string", pattern: "^sha256:[0-9a-f]{64}$" };
  const refuse2 = (error, detail) => { throw new ToolError({ error, ...detail }); };

  return {
    "read-portfolio": {
      write: false,
      description: "Read one DoctorCRE v5 portfolio: its current revision, the digest recomputed from the stored rows, structural validity, every child binding with its own version and accepted source, the reviews recorded against it, and whether a partner has accepted it. Exposes only content inside the accepted digest, and reports the zero executable effect the record carries.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { portfolio_ref: { type: "string" } }, required: ["portfolio_ref"],
      },
      handler: async (c, _actor, args) => {
        const row = (await c.query("select ops.portfolio_readback($1::text) as readback",
          [args.portfolio_ref])).rows[0]?.readback;
        if (!row) refuse2("portfolio_readback_unavailable", { portfolio_ref: args.portfolio_ref });
        return { ok: true, ...row };
      },
    },

    "propose-portfolio-revision": {
      write: true,
      description: "Propose one inert DoctorCRE v5 portfolio revision: the 21 master milestones with complete typed metadata, the four child programs with their own versions and applicable accepted source bindings, and the dependency edges. The proposal creates no job, execution envelope, capability session, schedule, deployment or clock. Its proposer is the authenticated writer, never a field in this payload, and both supplied digests are compared against digests recomputed from the stored rows before the transaction may commit.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          portfolio_ref: { type: "string" },
          revision_version: { type: "integer", minimum: 1 },
          source_digests: { type: "object" },
          graph_digest: digestSchema,
          accepted_digest: digestSchema,
          children: { type: "array", minItems: 4, maxItems: 4 },
          nodes: { type: "array", minItems: 21, maxItems: 21 },
          edges: { type: "array" },
        },
        required: ["idempotency_key", "portfolio_ref", "revision_version", "source_digests",
          "graph_digest", "accepted_digest", "children", "nodes", "edges"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "propose-portfolio-revision", args, async () => {
        // Validated in the module first so a malformed revision is refused with
        // a named clause rather than a database constraint message.
        const nodeChildRefs = Object.fromEntries(args.nodes.map(node => [node.node_ref, node.child_ref]));
        const childInputs = Object.fromEntries(args.children.map(child =>
          [child.child_ref, { child_version: child.child_version,
            accepted_plan_ref: child.accepted_plan_ref ?? null }]));
        const view = validatePortfolioRevisionForPersistence(
          { schema_version: PORTFOLIO_REVISION_SCHEMA_VERSION, revision_version: args.revision_version,
            portfolio_ref: args.portfolio_ref, source_digests: args.source_digests,
            child_program_refs: [...CHILD_PROGRAM_REFS],
            nodes: args.nodes.map(({ child_ref, ...node }) => node), edges: args.edges },
          nodeChildRefs, childInputs);
        if (view.graph_digest !== args.graph_digest) {
          refuse2("portfolio_graph_digest_mismatch",
            { expected: view.graph_digest, supplied: args.graph_digest });
        }
        if (view.accepted_digest !== args.accepted_digest) {
          refuse2("portfolio_accepted_digest_mismatch",
            { expected: view.accepted_digest, supplied: args.accepted_digest });
        }
        const revisionId = (await c.query(
          `select ops.portfolio_propose_revision($1::text,$2::integer,$3::uuid,$4::jsonb,
             $5::text,$6::text,$7::jsonb,$8::jsonb,$9::jsonb) as id`,
          [args.portfolio_ref, args.revision_version, args.idempotency_key,
            JSON.stringify(args.source_digests), args.graph_digest, args.accepted_digest,
            JSON.stringify(view.child_bindings), JSON.stringify(args.nodes),
            JSON.stringify(args.edges)])).rows[0].id;
        await writeEvent(c, actor, "propose-portfolio-revision", "portfolio", revisionId,
          { field: "revision_proposed",
            new: { portfolio_ref: args.portfolio_ref, revision_version: args.revision_version,
              accepted_digest: args.accepted_digest },
            idempotency_key: args.idempotency_key });
        return { ok: true, revision_id: revisionId, portfolio_ref: args.portfolio_ref,
          graph_digest: view.graph_digest, accepted_digest: view.accepted_digest,
          child_bindings: view.child_bindings, accepted: false,
          effects: { creates_effect: false, jobs: 0, capabilities: 0, execution_envelopes: 0,
            admissions: 0, schedules: 0, deployments: 0 } };
      }),
    },

    "review-portfolio-revision": {
      write: true,
      description: "Record one independent review of an exact DoctorCRE v5 portfolio accepted digest. The reviewer is the authenticated writer and is never a field in this payload. A review naming a digest the revision no longer has is refused, and a proposer cannot pass their own revision, so a passing review can never be carried onto different bytes or onto the proposer's own work.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" }, revision_id: { type: "string" },
          reviewed_digest: digestSchema, verdict: { type: "string", enum: ["pass", "fail"] },
          review_summary: { type: "string" },
        },
        required: ["idempotency_key", "revision_id", "reviewed_digest", "verdict", "review_summary"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "review-portfolio-revision", args, async () => {
        const reviewId = (await c.query(
          `select ops.portfolio_review_revision($1::uuid,$2::uuid,$3::text,$4::text,$5::text) as id`,
          [args.revision_id, args.idempotency_key, args.reviewed_digest, args.verdict,
            args.review_summary])).rows[0].id;
        await writeEvent(c, actor, "review-portfolio-revision", "portfolio", args.revision_id,
          { field: "revision_reviewed",
            new: { verdict: args.verdict, reviewed_digest: args.reviewed_digest },
            idempotency_key: args.idempotency_key });
        return { ok: true, review_id: reviewId, verdict: args.verdict,
          reviewed_digest: args.reviewed_digest,
          effects: { creates_effect: false, jobs: 0, capabilities: 0, execution_envelopes: 0,
            admissions: 0, schedules: 0, deployments: 0 } };
      }),
    },

    "accept-portfolio-revision": {
      write: true, humanOnly: true, authorityOnly: true,
      description: "HUMAN-ONLY: accept one exact DoctorCRE v5 portfolio accepted digest. The acceptor is derived from the authenticated partner authority session and is never a field in this payload; a writer connection cannot reach this verb at all. Acceptance requires a fresh passing independent review on the same bytes and three distinct identities: the proposer, the reviewer and the acceptor. It makes that revision the current accepted ancestor and creates no job, envelope, capability, schedule, deployment or clock.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" }, revision_id: { type: "string" },
          accepted_digest: digestSchema, review_id: { type: "string" },
        },
        required: ["idempotency_key", "revision_id", "accepted_digest", "review_id"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "accept-portfolio-revision", args, async () => {
        const receiptId = (await c.query(
          `select ops.portfolio_accept_revision($1::uuid,$2::uuid,$3::text,$4::uuid) as id`,
          [args.revision_id, args.idempotency_key, args.accepted_digest, args.review_id])).rows[0].id;
        await writeEvent(c, actor, "accept-portfolio-revision", "portfolio", args.revision_id,
          { field: "revision_accepted",
            new: { accepted_digest: args.accepted_digest },
            idempotency_key: args.idempotency_key });
        return { ok: true, receipt_id: receiptId, accepted_digest: args.accepted_digest,
          accepted: true,
          effects: { creates_effect: false, jobs: 0, capabilities: 0, execution_envelopes: 0,
            admissions: 0, schedules: 0, deployments: 0 } };
      }),
    },
  };
}
