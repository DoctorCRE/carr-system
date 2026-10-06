// DoctorCRE v5 slice V5-A04: THE HIERARCHICAL COST LEDGER — reservation,
// cancellation, late liability, conversion and truthful actuals across a slice,
// its child program and its portfolio ancestors (decision Q142.D1).
//
// THE SETTLED SENTENCE THIS FILE EXISTS TO ENFORCE, in full because every
// clause of it is a rule below:
//
//   Q142.D1 — "Record estimate, reservation, authorization ceiling, actual
//   incurred charge, late liability, and variance separately. Ceiling
//   compliance governs admission and reservation, never truthfully booking
//   incurred cost. Atomically convert matching reservation or liability to
//   actual without double counting; always post over-ceiling actuals through
//   every ancestor, mark the hierarchy overdrawn, deny new reservations, and
//   require incident, replan, or explicit authority amendment."
//
// WHAT THE MONEY IS. Nothing here pays anybody. The slice's effect class is
// internal financial accounting with no external payment, so a cost is an
// abstract integer UNIT, there is no currency field, no provider is called and
// no charge is settled. A "vendor reference" below is an EXTERNAL IDENTIFIER
// used to recognise the same charge arriving twice; it is not a payment
// instruction and this module has no way to act on one.
//
// SIX SEAMS, and they are why the file is shaped this way:
//
//   1. THE LOG IS THE TRUTH AND EVERY TOTAL IS DERIVED FROM IT. There is no
//      running balance anywhere. Ancestor figures are computed by rolling the
//      children up on every read, so "post the actual through every ancestor"
//      is a STRUCTURAL property rather than a bookkeeping step that could be
//      skipped on one path. A maintained counter can drift from its log; a
//      derived one cannot, and that is the whole reason conservation is
//      provable here rather than merely tested.
//
//   2. THE CEILING GOVERNS ADMISSION, NEVER TRUTH. `reserve` is the only
//      operation a ceiling can refuse. `postActual` and `recordLateLiability`
//      are append-only truth and are accepted ABOVE the ceiling, every time,
//      because a charge that was really incurred does not stop existing when it
//      is inconvenient. Refusing one would not save the money; it would only
//      lose the record of it. Over-ceiling truth is what marks the hierarchy
//      overdrawn, which is the actual consequence.
//
//   3. CONVERSION REPLACES EXACTLY ONCE, AND ATOMICALLY. Converting is not
//      "add an actual and hope someone releases the reservation": one call
//      appends BOTH entries or neither, under one conversion id, and the
//      reservation moves from open to closed in the same step. A second
//      conversion of the same reservation is a returned `reservation_not_open`,
//      not a second actual — which is precisely the double count Q142 names.
//
//   4. A RETRY IS NOT A SECOND OPERATION. Every call carries an `operation_id`.
//      A replay of one already applied returns the RECORDED outcome and the
//      ledger unchanged. A replay carrying the same id and different arguments
//      is not a retry at all and THROWS, so an id reused by accident cannot
//      quietly shadow a different intent.
//
//   5. A RACE IS SETTLED BY A CELL THAT OWNS THE LEDGER, NOT BY THE ORDER A
//      TEST HAPPENS TO CHOOSE AND NOT BY A CALLER CHECKING ITSELF. Every
//      ledger value has a VERSION — the number of distinct operations it has
//      applied — and a state digest. A concurrent writer computes its commit
//      against a NAMED base (`prepareCommit`) and then offers it to the CELL
//      that holds the ledger (`openLedgerCell`). The cell is the only thing
//      that can admit a commit: it admits one only while it is still standing
//      on that exact base, it REDERIVES the transition from its own value
//      rather than installing the one the proposal carries, and it replaces
//      its value in one synchronous step. Two commits computed from one base
//      therefore produce exactly one landing and one `version_conflict`, and
//      the loser must recompute against what actually committed — which is
//      where it meets the ceiling it would otherwise have talked its way past.
//      That is a property of the ledger, not of a serial permutation and not
//      of a well-behaved caller. What it still does not prove is stated on the
//      projection: both writers here share ONE cell in ONE process, and a
//      durable store would need a row lock or a serializable transaction to
//      supply the same single admission point across processes.
//
//   6. OVERDRAWN IS NOT NETTED AWAY BY A SIBLING. A node that breaches its own
//      ceiling marks every ancestor overdrawn, and a reservation is denied when
//      any node on its own ancestor chain is overdrawn. A sibling shares those
//      ancestors, so it is denied too — there is no path by which one slice's
//      unused headroom silently absorbs another's overage, which this slice's
//      excluded scope names as sibling netting.
//
// A REFUSAL IS AN ANSWER; A MALFORMED INPUT IS A THROW. "This reservation is no
// longer open" and "this would breach the portfolio ceiling" are facts about
// the ledger and are RETURNED, with the numbers behind them. "This node does
// not exist" and "this entry has an unknown key" are contract violations and
// THROW. The two are never mixed, matching V5-F04's kernels exactly.
//
// NO EFFECTS. No filesystem, no network, no database, no environment and no
// clock: every instant arrives on an argument. Nothing here survives the
// process; the durable half is named in the projection, not imitated.

import { digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";

export const V5_LEDGER_SCHEMA_VERSION = "doctorcre-v5-hierarchical-cost-ledger.v1";
export const V5_SCOPE_TREE_SCHEMA_VERSION = "cost-scope-tree.v1";
export const V5_LEDGER_ENTRY_SCHEMA_VERSION = "cost-ledger-entry.v1";
export const V5_LEDGER_PROJECTION_SCHEMA_VERSION = "cost-ledger-projection.v1";

/**
 * The three scope kinds this slice accounts across, outermost first. The order
 * is meaningful: a child's parent must be a portfolio, a slice's parent must be
 * a child, and nothing may parent a portfolio. That keeps "slice, child and
 * portfolio ancestors" a property of the compiled tree rather than a naming
 * convention a caller is trusted to follow.
 */
export const V5_SCOPE_KINDS = Object.freeze(["portfolio", "child", "slice"]);

/**
 * The six entry kinds, C-sorted. CONVERSION IS NOT ONE OF THEM, on purpose: a
 * conversion emits a release and an actual under one conversion id, so "the
 * reservation was replaced" and "the charge was booked" cannot come apart.
 */
export const V5_LEDGER_ENTRY_KINDS = Object.freeze([
  "actual",
  "estimate",
  "late_liability",
  "liability_release",
  "reservation",
  "reservation_release",
]);

/** Everything a refusal can say, C-sorted. Each is RETURNED, never thrown. */
export const V5_LEDGER_REFUSAL_REASONS = Object.freeze([
  "ancestor_overdrawn",
  "ceiling_exceeded",
  "duplicate_reservation_id",
  "duplicate_vendor_charge",
  "liability_not_open",
  "prepared_commit_mismatch",
  "reservation_not_open",
  "version_conflict",
]);

/** What an overdrawn hierarchy requires before it can move again — Q142.D1's own list. */
export const V5_OVERDRAWN_REMEDIES = Object.freeze([
  "incident",
  "replan_or_explicit_authority_amendment",
]);

const REF = /^[a-z0-9][a-z0-9_.:/-]{0,127}$/;
const VENDOR_REF = /^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,191}$/;
const ISO_INSTANT =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|([+-])(\d{2}):(\d{2}))$/;

export class V5CostLedgerError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5CostLedgerError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5CostLedgerError(code, message, detail);
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function deepFreeze(value) {
  if (Array.isArray(value)) { value.forEach(deepFreeze); return Object.freeze(value); }
  if (isPlainObject(value)) { Object.values(value).forEach(deepFreeze); return Object.freeze(value); }
  return value;
}

function copy(value) {
  return JSON.parse(JSON.stringify(value));
}

function assertObject(value, path) {
  if (!isPlainObject(value)) fail("invalid_shape", `${path} must be a plain object`, { path });
}

function assertClosedKeys(value, allowed, path) {
  const unknown = Object.keys(value).filter(key => !allowed.includes(key)).sort();
  if (unknown.length > 0) {
    fail("unknown_field", `${path} carries fields this contract does not declare`,
      { path, unknown, allowed: [...allowed] });
  }
}

function assertRequiredKeys(value, required, path) {
  const missing = required.filter(key => value[key] === undefined).sort();
  if (missing.length > 0) {
    fail("missing_field", `${path} is missing required fields`, { path, missing });
  }
}

function assertRef(value, path) {
  if (typeof value !== "string" || !REF.test(value)) {
    fail("invalid_reference", `${path} must be a lower-case reference token`, { path, value });
  }
  return value;
}

/**
 * A vendor reference is an OUTSIDE identifier, so it is not forced into this
 * system's lower-case token shape — a provider that issues mixed-case invoice
 * numbers must be recordable verbatim, because a normalised one would make two
 * genuinely different charges collide in the duplicate check.
 */
function assertVendorReference(value, path) {
  if (typeof value !== "string" || !VENDOR_REF.test(value)) {
    fail("invalid_reference", `${path} must be an external reference token`, { path, value });
  }
  return value;
}

/**
 * Cost is a safe non-negative integer of units. A NEGATIVE amount is refused
 * everywhere: a refund or a correction is its own kind of entry that this slice
 * does not settle, and allowing a negative one here would make "append-only
 * truth" silently reversible by anyone who could post.
 */
function assertCostUnits(value, path, { minimum = 0 } = {}) {
  if (!Number.isSafeInteger(value) || value < minimum) {
    fail("invalid_cost_units", `${path} must be a safe integer of cost units >= ${minimum}`,
      { path, value });
  }
  return value;
}

function daysInMonth(year, month) {
  if (month === 2) {
    return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0 ? 29 : 28;
  }
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

/**
 * An instant is parsed, never inferred, and THE CALENDAR IS CHECKED AGAINST THE
 * LITERAL FIELDS BEFORE PARSING — because `Date.parse` silently NORMALIZES an
 * impossible date rather than rejecting it: "2026-02-30T00:00:00Z" becomes 2
 * March. A charge stamped with a date nobody wrote is a charge whose ordering
 * and whose audit trail are both about a different day, and shape alone cannot
 * catch it since 02-30 matches the pattern perfectly.
 *
 * S01's `global-boundaries.v5.js` and J102's `cre-lifecycle-store.v5.js` each
 * hold this same check privately; neither exports it, so this is a third copy
 * rather than a second authority reused. That is a real seam and it is named in
 * the slice's author report rather than quietly tolerated.
 */
function assertInstant(value, path) {
  const match = typeof value === "string" ? ISO_INSTANT.exec(value) : null;
  if (!match) {
    fail("invalid_instant", `${path} must be an ISO-8601 instant`, { path, value });
  }
  const [, year, month, day, hour, minute, second, , offsetHour, offsetMinute] = match;
  const y = Number(year), mo = Number(month), d = Number(day);
  const h = Number(hour), mi = Number(minute), s = Number(second);
  if (mo < 1 || mo > 12 || d < 1 || d > daysInMonth(y, mo) || h > 23 || mi > 59 || s > 59
    || (offsetHour !== undefined && (Number(offsetHour) > 23 || Number(offsetMinute) > 59))) {
    fail("invalid_instant",
      `${path} names an instant that does not exist on the calendar; it is not normalized into a different one`,
      { path, value });
  }
  const ms = Date.parse(value);
  if (!Number.isFinite(ms)) {
    fail("invalid_instant", `${path} is not a readable instant`, { path, value });
  }
  return ms;
}

// ---------------------------------------------------------------------------
// The scope tree.
// ---------------------------------------------------------------------------

const TREE_KEYS = Object.freeze(["schema_version", "tree_id", "tree_version", "nodes"]);
const NODE_KEYS = Object.freeze([
  "node_id", "parent_node_id", "scope_kind", "authorization_ceiling_units",
]);

const PARENT_KIND = Object.freeze({
  portfolio: null,
  child: "portfolio",
  slice: "child",
});

/**
 * Compile a scope tree: exactly one portfolio root, every parent present, no
 * cycles, and each node's parent of the kind its own kind requires.
 *
 * Ancestors are precomputed self-first so that the admission walk below reads
 * in the order a refusal should name: the tightest scope that actually binds is
 * reported, not the outermost one that happens to be checked last.
 */
export function compileScopeTree(tree) {
  assertObject(tree, "tree");
  assertClosedKeys(tree, TREE_KEYS, "tree");
  assertRequiredKeys(tree, TREE_KEYS, "tree");
  if (tree.schema_version !== V5_SCOPE_TREE_SCHEMA_VERSION) {
    fail("wrong_schema_version", `tree.schema_version must be "${V5_SCOPE_TREE_SCHEMA_VERSION}"`,
      { path: "tree.schema_version", value: tree.schema_version });
  }
  assertRef(tree.tree_id, "tree.tree_id");
  if (!Number.isInteger(tree.tree_version) || tree.tree_version < 1) {
    fail("invalid_shape", "tree.tree_version must be an integer >= 1",
      { path: "tree.tree_version", value: tree.tree_version });
  }
  if (!Array.isArray(tree.nodes) || tree.nodes.length === 0) {
    fail("invalid_shape", "tree.nodes must be a non-empty array", { path: "tree.nodes" });
  }

  const nodes = Object.create(null);
  tree.nodes.forEach((node, index) => {
    const path = `tree.nodes[${index}]`;
    assertObject(node, path);
    assertClosedKeys(node, NODE_KEYS, path);
    assertRequiredKeys(node, NODE_KEYS, path);
    const nodeId = assertRef(node.node_id, `${path}.node_id`);
    if (nodes[nodeId] !== undefined) {
      fail("duplicate_entry", `${path}.node_id is declared twice`, { path, node_id: nodeId });
    }
    if (!V5_SCOPE_KINDS.includes(node.scope_kind)) {
      fail("unknown_scope_kind", `${path}.scope_kind must be one of ${V5_SCOPE_KINDS.join(", ")}`,
        { path: `${path}.scope_kind`, value: node.scope_kind });
    }
    const parentId = node.parent_node_id === null
      ? null
      : assertRef(node.parent_node_id, `${path}.parent_node_id`);
    if (parentId === nodeId) {
      fail("cycle_detected", `${path} is its own parent`, { path, node_id: nodeId });
    }
    nodes[nodeId] = {
      node_id: nodeId,
      parent_node_id: parentId,
      scope_kind: node.scope_kind,
      authorization_ceiling_units: assertCostUnits(
        node.authorization_ceiling_units, `${path}.authorization_ceiling_units`),
    };
  });

  const ids = Object.keys(nodes).sort();
  const roots = ids.filter(id => nodes[id].parent_node_id === null);
  if (roots.length !== 1) {
    fail("invalid_scope_tree", "a scope tree has exactly one portfolio root",
      { path: "tree.nodes", roots });
  }
  if (nodes[roots[0]].scope_kind !== "portfolio") {
    fail("invalid_scope_tree", "the root of a scope tree must be the portfolio",
      { path: "tree.nodes", root: roots[0], scope_kind: nodes[roots[0]].scope_kind });
  }

  for (const id of ids) {
    const node = nodes[id];
    const requiredParentKind = PARENT_KIND[node.scope_kind];
    if (requiredParentKind === null) {
      if (node.parent_node_id !== null) {
        fail("invalid_scope_tree", "a portfolio has no parent",
          { node_id: id, parent_node_id: node.parent_node_id });
      }
      continue;
    }
    const parent = nodes[node.parent_node_id];
    if (parent === undefined) {
      fail("unknown_node", "a node names a parent the tree does not declare",
        { node_id: id, parent_node_id: node.parent_node_id });
    }
    if (parent.scope_kind !== requiredParentKind) {
      fail("invalid_scope_tree",
        `a ${node.scope_kind} must hang from a ${requiredParentKind}`,
        { node_id: id, parent_node_id: parent.node_id, parent_scope_kind: parent.scope_kind });
    }
  }

  // Ancestors, self first. The kind rules above already forbid a cycle, but the
  // walk is bounded anyway: a depth guard is cheap, and a compiler that can
  // loop forever on malformed input is a worse failure than a wrong answer.
  const ancestors = Object.create(null);
  const children = Object.create(null);
  for (const id of ids) children[id] = [];
  for (const id of ids) {
    const chain = [];
    let cursor = id;
    while (cursor !== null) {
      if (chain.includes(cursor)) {
        fail("cycle_detected", "the scope tree contains a cycle", { node_id: id, chain });
      }
      chain.push(cursor);
      if (chain.length > ids.length) {
        fail("cycle_detected", "the scope tree contains a cycle", { node_id: id, chain });
      }
      cursor = nodes[cursor].parent_node_id;
    }
    ancestors[id] = Object.freeze(chain);
    const parentId = nodes[id].parent_node_id;
    if (parentId !== null) children[parentId].push(id);
  }
  for (const id of ids) children[id] = Object.freeze(children[id].sort());

  // The effective ceiling is the tightest one on the chain. A slice authorised
  // for more than its portfolio is not an error — the portfolio's ceiling binds
  // anyway — but a reader needs the number that actually applies.
  const effective = Object.create(null);
  for (const id of ids) {
    effective[id] = Math.min(...ancestors[id].map(a => nodes[a].authorization_ceiling_units));
  }

  return deepFreeze({
    schema_version: V5_SCOPE_TREE_SCHEMA_VERSION,
    tree_id: tree.tree_id,
    tree_version: tree.tree_version,
    root_node_id: roots[0],
    node_ids: ids,
    nodes: Object.fromEntries(ids.map(id => [id, { ...nodes[id] }])),
    ancestors: Object.fromEntries(ids.map(id => [id, [...ancestors[id]]])),
    children: Object.fromEntries(ids.map(id => [id, [...children[id]]])),
    effective_ceiling_units: Object.fromEntries(ids.map(id => [id, effective[id]])),
    tree_digest: digest(copy(tree)),
  });
}

function requireNode(tree, nodeId, path) {
  assertRef(nodeId, path);
  if (tree.nodes[nodeId] === undefined) {
    fail("unknown_node", `${path} names a node the scope tree does not declare`,
      { path, node_id: nodeId });
  }
  return nodeId;
}

// ---------------------------------------------------------------------------
// The ledger.
// ---------------------------------------------------------------------------

const ENTRY_KEYS = Object.freeze([
  "entry_id", "sequence", "kind", "node_id", "amount_units", "operation_id",
  "occurred_at", "reservation_id", "liability_id", "conversion_id",
  "vendor_reference", "basis_digest",
]);

function makeEntry(sequence, fields) {
  const entry = {
    entry_id: `entry:${String(sequence).padStart(8, "0")}`,
    sequence,
    kind: fields.kind,
    node_id: fields.node_id,
    amount_units: fields.amount_units,
    operation_id: fields.operation_id,
    occurred_at: fields.occurred_at,
    reservation_id: fields.reservation_id ?? null,
    liability_id: fields.liability_id ?? null,
    conversion_id: fields.conversion_id ?? null,
    vendor_reference: fields.vendor_reference ?? null,
    basis_digest: fields.basis_digest ?? null,
  };
  assertClosedKeys(entry, ENTRY_KEYS, "entry");
  return Object.freeze(entry);
}

/** An empty ledger over a compiled scope tree. */
export function openLedger(tree) {
  if (!isPlainObject(tree) || tree.schema_version !== V5_SCOPE_TREE_SCHEMA_VERSION) {
    fail("invalid_shape", "openLedger takes a compiled scope tree", { path: "tree" });
  }
  return deepFreeze({
    schema_version: V5_LEDGER_SCHEMA_VERSION,
    tree,
    entries: [],
    sequence: 0,
    applied: {},
    open_reservations: {},
    closed_reservations: {},
    open_liabilities: {},
    closed_liabilities: {},
    vendor_charges: {},
  });
}

function assertLedger(ledger) {
  if (!isPlainObject(ledger) || ledger.schema_version !== V5_LEDGER_SCHEMA_VERSION) {
    fail("invalid_shape", "this operation takes a ledger from openLedger", { path: "ledger" });
  }
  return ledger;
}

/**
 * Per-node own totals, straight off the log.
 *
 * ESTIMATE IS A RESTATEMENT, NOT AN ACCRUAL, and is the one measure that is not
 * a sum: re-estimating a node to 400 after estimating it at 300 means the
 * estimate is 400, not 700. The latest estimate entry for the node wins, and
 * the accrual conservation law below deliberately excludes estimates for that
 * reason.
 */
function ownTotals(ledger, nodeId) {
  let reservationGross = 0, reservationReleased = 0;
  let liabilityGross = 0, liabilityReleased = 0;
  let actual = 0;
  let estimate = 0, estimateSequence = -1;
  for (const entry of ledger.entries) {
    if (entry.node_id !== nodeId) continue;
    switch (entry.kind) {
      case "reservation": reservationGross += entry.amount_units; break;
      case "reservation_release": reservationReleased += entry.amount_units; break;
      case "late_liability": liabilityGross += entry.amount_units; break;
      case "liability_release": liabilityReleased += entry.amount_units; break;
      case "actual": actual += entry.amount_units; break;
      case "estimate":
        if (entry.sequence > estimateSequence) {
          estimate = entry.amount_units;
          estimateSequence = entry.sequence;
        }
        break;
      default:
        fail("unknown_entry_kind", "the ledger holds an entry of an unregistered kind",
          { entry_id: entry.entry_id, kind: entry.kind });
    }
  }
  const reservationOutstanding = reservationGross - reservationReleased;
  const liabilityOutstanding = liabilityGross - liabilityReleased;
  return {
    estimate_units: estimate,
    reservation_gross_units: reservationGross,
    reservation_released_units: reservationReleased,
    reservation_outstanding_units: reservationOutstanding,
    liability_gross_units: liabilityGross,
    liability_released_units: liabilityReleased,
    liability_outstanding_units: liabilityOutstanding,
    actual_units: actual,
    committed_units: reservationOutstanding + liabilityOutstanding + actual,
    incurred_units: liabilityOutstanding + actual,
  };
}

const MEASURES = Object.freeze([
  "estimate_units", "reservation_gross_units", "reservation_released_units",
  "reservation_outstanding_units", "liability_gross_units", "liability_released_units",
  "liability_outstanding_units", "actual_units", "committed_units", "incurred_units",
]);

function rollUp(ledger) {
  const { tree } = ledger;
  const own = Object.create(null);
  for (const id of tree.node_ids) own[id] = ownTotals(ledger, id);

  // Deepest first, so a parent is only summed once every child is final.
  const depth = id => tree.ancestors[id].length;
  const order = [...tree.node_ids].sort((a, b) => depth(b) - depth(a));
  const rolled = Object.create(null);
  for (const id of order) {
    const totals = {};
    for (const measure of MEASURES) {
      totals[measure] = tree.children[id].reduce(
        (sum, child) => sum + rolled[child][measure], own[id][measure]);
    }
    rolled[id] = totals;
  }

  // Overdrawn: a node breaches when its own rolled-up commitment passes its own
  // declared ceiling, and that breach marks every ancestor. `breached_by` names
  // the nodes responsible, so an overdrawn portfolio says which slice did it
  // rather than merely that something did.
  const breached = Object.create(null);
  for (const id of tree.node_ids) {
    breached[id] = rolled[id].committed_units > tree.nodes[id].authorization_ceiling_units;
  }
  const overdrawn = Object.create(null);
  const breachedBy = Object.create(null);
  for (const id of tree.node_ids) {
    const causes = tree.node_ids
      .filter(other => breached[other] && tree.ancestors[other].includes(id))
      .sort();
    breachedBy[id] = causes;
    overdrawn[id] = causes.length > 0;
  }
  return { own, rolled, breached, overdrawn, breachedBy };
}

/**
 * A read-only view of the whole hierarchy: what each node has of its own, what
 * rolls up into it, what its ceilings are, and whether it is overdrawn.
 */
export function projectLedger(ledger) {
  assertLedger(ledger);
  const { tree } = ledger;
  const { own, rolled, breached, overdrawn, breachedBy } = rollUp(ledger);
  const nodes = tree.node_ids.map(id => {
    const ceiling = tree.nodes[id].authorization_ceiling_units;
    const effective = tree.effective_ceiling_units[id];
    return {
      node_id: id,
      scope_kind: tree.nodes[id].scope_kind,
      parent_node_id: tree.nodes[id].parent_node_id,
      authorization_ceiling_units: ceiling,
      effective_ceiling_units: effective,
      own: { ...own[id] },
      rolled_up: { ...rolled[id] },
      // Variance is against the rolled-up estimate, and it is what has really
      // been incurred — actuals plus liabilities still owed — never the
      // reservations, which are only intentions.
      variance_units: rolled[id].incurred_units - rolled[id].estimate_units,
      available_units: Math.max(0, effective - rolled[id].committed_units),
      breaches_own_ceiling: breached[id],
      overdrawn: overdrawn[id],
      overdrawn_caused_by_node_ids: [...breachedBy[id]],
      requires: overdrawn[id] ? [...V5_OVERDRAWN_REMEDIES] : [],
    };
  });
  return deepFreeze({
    schema_version: V5_LEDGER_PROJECTION_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    tree_id: tree.tree_id,
    tree_version: tree.tree_version,
    tree_digest: tree.tree_digest,
    root_node_id: tree.root_node_id,
    entry_count: ledger.entries.length,
    nodes,
    by_node: Object.fromEntries(nodes.map(node => [node.node_id, node])),
    hierarchy_overdrawn: overdrawn[tree.root_node_id],
    open_reservation_ids: Object.keys(ledger.open_reservations).sort(),
    open_liability_ids: Object.keys(ledger.open_liabilities).sort(),
  });
}

// ---------------------------------------------------------------------------
// Applying an operation.
// ---------------------------------------------------------------------------

function outcome(fields) {
  return deepFreeze({ replayed: false, ...fields });
}

/**
 * The idempotency gate. A replay returns the recorded outcome and the ledger
 * untouched; the same id over different arguments is not a replay and throws,
 * because silently answering with a different operation's result is worse than
 * refusing.
 */
function replayOrApply(ledger, operationId, argumentDigest, apply) {
  assertRef(operationId, "operation.operation_id");
  const recorded = ledger.applied[operationId];
  if (recorded !== undefined) {
    if (recorded.argument_digest !== argumentDigest) {
      fail("operation_id_reused_with_different_arguments",
        "this operation_id has already been applied with different arguments; a retry replays one intent, it does not shadow a second",
        { operation_id: operationId });
    }
    return { ledger, outcome: deepFreeze({ ...recorded.outcome, replayed: true }) };
  }
  const result = apply();
  const next = deepFreeze({
    ...result.ledger,
    applied: {
      ...result.ledger.applied,
      [operationId]: { argument_digest: argumentDigest, outcome: result.outcome },
    },
  });
  // Self-check on every accepted write. If the arithmetic below ever stops
  // conserving, the fixture that hits it fails immediately rather than three
  // operations later with a plausible-looking wrong total.
  assertLedgerConservation(next);
  return { ledger: next, outcome: result.outcome };
}

function appended(ledger, entries) {
  return {
    ...ledger,
    entries: Object.freeze([...ledger.entries, ...entries]),
    sequence: ledger.sequence + entries.length,
  };
}

const ESTIMATE_KEYS = Object.freeze([
  "operation_id", "node_id", "expected_total_cost_units", "basis_digest", "recorded_at",
]);

/**
 * Record what this node is expected to cost. An estimate commits nothing, so no
 * ceiling can refuse it; it is the denominator the variance and replan module
 * measures against, and Q142 requires it be held separately from every other
 * figure — which is why it is its own entry kind and its own projection field.
 */
export function recordEstimate(ledger, operation) {
  assertLedger(ledger);
  assertObject(operation, "operation");
  assertClosedKeys(operation, ESTIMATE_KEYS, "operation");
  assertRequiredKeys(operation, ESTIMATE_KEYS, "operation");
  const nodeId = requireNode(ledger.tree, operation.node_id, "operation.node_id");
  const amount = assertCostUnits(
    operation.expected_total_cost_units, "operation.expected_total_cost_units", { minimum: 1 });
  const basisDigest = assertRef(operation.basis_digest, "operation.basis_digest");
  assertInstant(operation.recorded_at, "operation.recorded_at");
  const argumentDigest = digest(copy(operation));

  return replayOrApply(ledger, operation.operation_id, argumentDigest, () => {
    const entry = makeEntry(ledger.sequence, {
      kind: "estimate",
      node_id: nodeId,
      amount_units: amount,
      operation_id: operation.operation_id,
      occurred_at: operation.recorded_at,
      basis_digest: basisDigest,
    });
    return {
      ledger: appended(ledger, [entry]),
      outcome: outcome({
        accepted: true, reason_id: "estimate_recorded",
        node_id: nodeId, entry_id: entry.entry_id, amount_units: amount,
      }),
    };
  });
}

/**
 * The refusal text for `ancestor_overdrawn`. The typed part of the refusal is
 * `reason_id`; this sentence is for the human reading the outcome, and it is a
 * constant so every denial says the same thing in the same words.
 */
export const V5_ANCESTOR_OVERDRAWN_DETAIL =
  "New reservation refused: this budget or a budget containing it is marked overdrawn, because it or some budget inside it has committed more than its authorized ceiling (the refusal lists which). Nothing already reserved is changed; existing reservations can still be cancelled or turned into actual charges. To move forward, open an incident, replan the work, or get the ceiling explicitly raised. New reservations are accepted again once no budget on this chain is marked overdrawn.";

const RESERVE_KEYS = Object.freeze([
  "operation_id", "node_id", "reservation_id", "amount_units", "requested_at",
]);

/**
 * Reserve headroom against a node — THE ONE OPERATION A CEILING CAN REFUSE.
 *
 * The walk is self-first up the chain and returns on the FIRST binding scope,
 * so the refusal names the ceiling that actually stopped it. Overdrawn is
 * checked before headroom on purpose: an overdrawn hierarchy denies new
 * reservations outright, and reporting "you have 40 units left" to a caller
 * inside an overdrawn portfolio would be true and dangerously misleading.
 */
export function reserve(ledger, operation) {
  assertLedger(ledger);
  assertObject(operation, "operation");
  assertClosedKeys(operation, RESERVE_KEYS, "operation");
  assertRequiredKeys(operation, RESERVE_KEYS, "operation");
  const nodeId = requireNode(ledger.tree, operation.node_id, "operation.node_id");
  const reservationId = assertRef(operation.reservation_id, "operation.reservation_id");
  const amount = assertCostUnits(operation.amount_units, "operation.amount_units", { minimum: 1 });
  assertInstant(operation.requested_at, "operation.requested_at");
  const argumentDigest = digest(copy(operation));

  return replayOrApply(ledger, operation.operation_id, argumentDigest, () => {
    if (ledger.open_reservations[reservationId] !== undefined
      || ledger.closed_reservations[reservationId] !== undefined) {
      return {
        ledger,
        outcome: outcome({
          accepted: false, reason_id: "duplicate_reservation_id",
          reservation_id: reservationId,
        }),
      };
    }

    const { rolled, overdrawn, breachedBy } = rollUp(ledger);
    const chain = ledger.tree.ancestors[nodeId];
    const overdrawnOnChain = chain.filter(id => overdrawn[id]);
    if (overdrawnOnChain.length > 0) {
      return {
        ledger,
        outcome: outcome({
          accepted: false, reason_id: "ancestor_overdrawn",
          node_id: nodeId,
          overdrawn_node_ids: overdrawnOnChain,
          // The budgets actually over their own ceilings. When a sibling's
          // breach is what marks the shared parent, this is the only field
          // that names the sibling; overdrawn_node_ids names the chain.
          overdrawn_caused_by_node_ids: [...new Set(
            overdrawnOnChain.flatMap(id => breachedBy[id]))].sort(),
          requires: [...V5_OVERDRAWN_REMEDIES],
          // Says what is denied, what is NOT touched, and what clears it. The
          // middle clause matters: a refusal here withdraws nothing, and a
          // caller holding an open reservation must not read this as a reason
          // to abandon it.
          detail: V5_ANCESTOR_OVERDRAWN_DETAIL,
        }),
      };
    }
    for (const id of chain) {
      const ceiling = ledger.tree.nodes[id].authorization_ceiling_units;
      if (rolled[id].committed_units + amount > ceiling) {
        return {
          ledger,
          outcome: outcome({
            accepted: false, reason_id: "ceiling_exceeded",
            node_id: nodeId,
            binding_node_id: id,
            authorization_ceiling_units: ceiling,
            committed_units: rolled[id].committed_units,
            requested_units: amount,
            available_units: Math.max(0, ceiling - rolled[id].committed_units),
          }),
        };
      }
    }

    const entry = makeEntry(ledger.sequence, {
      kind: "reservation",
      node_id: nodeId,
      amount_units: amount,
      operation_id: operation.operation_id,
      occurred_at: operation.requested_at,
      reservation_id: reservationId,
    });
    const next = appended(ledger, [entry]);
    return {
      ledger: {
        ...next,
        open_reservations: {
          ...next.open_reservations,
          [reservationId]: { node_id: nodeId, amount_units: amount, entry_id: entry.entry_id },
        },
      },
      outcome: outcome({
        accepted: true, reason_id: "reservation_accepted",
        node_id: nodeId, reservation_id: reservationId,
        amount_units: amount, entry_id: entry.entry_id,
      }),
    };
  });
}

function closeReservation(ledger, reservationId, releaseEntry, extraEntries = []) {
  const open = ledger.open_reservations[reservationId];
  const next = appended(ledger, [releaseEntry, ...extraEntries]);
  const openReservations = { ...next.open_reservations };
  delete openReservations[reservationId];
  return {
    ...next,
    open_reservations: openReservations,
    closed_reservations: { ...next.closed_reservations, [reservationId]: open },
  };
}

function closeLiability(ledger, liabilityId, releaseEntry, extraEntries = []) {
  const open = ledger.open_liabilities[liabilityId];
  const next = appended(ledger, [releaseEntry, ...extraEntries]);
  const openLiabilities = { ...next.open_liabilities };
  delete openLiabilities[liabilityId];
  return {
    ...next,
    open_liabilities: openLiabilities,
    closed_liabilities: { ...next.closed_liabilities, [liabilityId]: open },
  };
}

const CANCEL_KEYS = Object.freeze(["operation_id", "reservation_id", "cancelled_at"]);

/**
 * Cancel a reservation: the headroom comes back and nothing was ever incurred.
 *
 * An id the ledger has never seen is a broken caller and THROWS; an id that
 * exists but is already closed is a RETURNED `reservation_not_open`, because
 * that is the ordinary outcome of a cancel racing a conversion and the caller
 * needs to carry on rather than crash.
 */
export function cancelReservation(ledger, operation) {
  assertLedger(ledger);
  assertObject(operation, "operation");
  assertClosedKeys(operation, CANCEL_KEYS, "operation");
  assertRequiredKeys(operation, CANCEL_KEYS, "operation");
  const reservationId = assertRef(operation.reservation_id, "operation.reservation_id");
  assertInstant(operation.cancelled_at, "operation.cancelled_at");
  if (ledger.open_reservations[reservationId] === undefined
    && ledger.closed_reservations[reservationId] === undefined) {
    fail("unknown_reservation", "operation.reservation_id names a reservation this ledger never made",
      { path: "operation.reservation_id", reservation_id: reservationId });
  }
  const argumentDigest = digest(copy(operation));

  return replayOrApply(ledger, operation.operation_id, argumentDigest, () => {
    const open = ledger.open_reservations[reservationId];
    if (open === undefined) {
      return {
        ledger,
        outcome: outcome({
          accepted: false, reason_id: "reservation_not_open", reservation_id: reservationId,
        }),
      };
    }
    const entry = makeEntry(ledger.sequence, {
      kind: "reservation_release",
      node_id: open.node_id,
      amount_units: open.amount_units,
      operation_id: operation.operation_id,
      occurred_at: operation.cancelled_at,
      reservation_id: reservationId,
    });
    return {
      ledger: closeReservation(ledger, reservationId, entry),
      outcome: outcome({
        accepted: true, reason_id: "reservation_cancelled",
        node_id: open.node_id, reservation_id: reservationId,
        released_units: open.amount_units, entry_id: entry.entry_id,
      }),
    };
  });
}

const LIABILITY_KEYS = Object.freeze([
  "operation_id", "node_id", "liability_id", "amount_units", "vendor_reference", "incurred_at",
]);

/**
 * Record a late liability: a charge that has been incurred and not yet settled,
 * arriving after the fact with no reservation waiting for it.
 *
 * ACCEPTED ABOVE THE CEILING, ALWAYS. This is the clause of Q142 that costs the
 * most to get wrong: a ceiling governs what may be COMMITTED, never what may be
 * BOOKED, and refusing a real liability would leave the hierarchy looking
 * solvent while the money was already gone. It is the acceptance that marks the
 * hierarchy overdrawn.
 */
export function recordLateLiability(ledger, operation) {
  assertLedger(ledger);
  assertObject(operation, "operation");
  assertClosedKeys(operation, LIABILITY_KEYS, "operation");
  assertRequiredKeys(operation, LIABILITY_KEYS, "operation");
  const nodeId = requireNode(ledger.tree, operation.node_id, "operation.node_id");
  const liabilityId = assertRef(operation.liability_id, "operation.liability_id");
  const amount = assertCostUnits(operation.amount_units, "operation.amount_units", { minimum: 1 });
  const vendorReference = assertVendorReference(
    operation.vendor_reference, "operation.vendor_reference");
  assertInstant(operation.incurred_at, "operation.incurred_at");
  const argumentDigest = digest(copy(operation));

  return replayOrApply(ledger, operation.operation_id, argumentDigest, () => {
    if (ledger.open_liabilities[liabilityId] !== undefined
      || ledger.closed_liabilities[liabilityId] !== undefined) {
      return {
        ledger,
        outcome: outcome({
          accepted: false, reason_id: "liability_not_open", liability_id: liabilityId,
          detail: "this liability_id is already in the ledger",
        }),
      };
    }
    const existing = ledger.vendor_charges[vendorReference];
    if (existing !== undefined) {
      return {
        ledger,
        outcome: outcome({
          accepted: false, reason_id: "duplicate_vendor_charge",
          vendor_reference: vendorReference, existing_entry_id: existing,
        }),
      };
    }
    const entry = makeEntry(ledger.sequence, {
      kind: "late_liability",
      node_id: nodeId,
      amount_units: amount,
      operation_id: operation.operation_id,
      occurred_at: operation.incurred_at,
      liability_id: liabilityId,
      vendor_reference: vendorReference,
    });
    const next = appended(ledger, [entry]);
    return {
      ledger: {
        ...next,
        open_liabilities: {
          ...next.open_liabilities,
          [liabilityId]: { node_id: nodeId, amount_units: amount, entry_id: entry.entry_id },
        },
        vendor_charges: { ...next.vendor_charges, [vendorReference]: entry.entry_id },
      },
      outcome: outcome({
        accepted: true, reason_id: "late_liability_recorded",
        node_id: nodeId, liability_id: liabilityId,
        amount_units: amount, entry_id: entry.entry_id,
      }),
    };
  });
}

const ACTUAL_KEYS = Object.freeze([
  "operation_id", "node_id", "amount_units", "vendor_reference", "incurred_at",
]);

/**
 * Post an actual charge with no matching reservation or liability — the late
 * actual. Like a liability, it is append-only truth and is accepted above the
 * ceiling; unlike a liability, it is already settled and so has nothing left to
 * convert.
 */
export function postActual(ledger, operation) {
  assertLedger(ledger);
  assertObject(operation, "operation");
  assertClosedKeys(operation, ACTUAL_KEYS, "operation");
  assertRequiredKeys(operation, ACTUAL_KEYS, "operation");
  const nodeId = requireNode(ledger.tree, operation.node_id, "operation.node_id");
  const amount = assertCostUnits(operation.amount_units, "operation.amount_units", { minimum: 1 });
  const vendorReference = assertVendorReference(
    operation.vendor_reference, "operation.vendor_reference");
  assertInstant(operation.incurred_at, "operation.incurred_at");
  const argumentDigest = digest(copy(operation));

  return replayOrApply(ledger, operation.operation_id, argumentDigest, () => {
    const existing = ledger.vendor_charges[vendorReference];
    if (existing !== undefined) {
      return {
        ledger,
        outcome: outcome({
          accepted: false, reason_id: "duplicate_vendor_charge",
          vendor_reference: vendorReference, existing_entry_id: existing,
        }),
      };
    }
    const entry = makeEntry(ledger.sequence, {
      kind: "actual",
      node_id: nodeId,
      amount_units: amount,
      operation_id: operation.operation_id,
      occurred_at: operation.incurred_at,
      vendor_reference: vendorReference,
    });
    const next = appended(ledger, [entry]);
    return {
      ledger: { ...next, vendor_charges: { ...next.vendor_charges, [vendorReference]: entry.entry_id } },
      outcome: outcome({
        accepted: true, reason_id: "actual_posted",
        node_id: nodeId, amount_units: amount, entry_id: entry.entry_id,
      }),
    };
  });
}

const CONVERT_RESERVATION_KEYS = Object.freeze([
  "operation_id", "conversion_id", "reservation_id", "actual_amount_units",
  "vendor_reference", "settled_at",
]);

/**
 * Convert a reservation to an actual, atomically and exactly once.
 *
 * ONE CALL, TWO ENTRIES, ONE CONVERSION ID. The release always carries the
 * reservation's OWN amount — the whole intention is withdrawn — and the actual
 * carries the VENDOR'S amount, which may be more or less. That is the only
 * honest way to handle a bill that differs from the estimate: netting the two
 * into a single adjusted figure would lose both the intention and the charge,
 * and adding the actual without the release would double count exactly as
 * Q142 forbids.
 *
 * An over-ceiling conversion is ACCEPTED. The ceiling governed the reservation
 * when it was made; a vendor who then charged more has not asked permission,
 * and the consequence is an overdrawn hierarchy, not a lost charge.
 */
export function convertReservationToActual(ledger, operation) {
  assertLedger(ledger);
  assertObject(operation, "operation");
  assertClosedKeys(operation, CONVERT_RESERVATION_KEYS, "operation");
  assertRequiredKeys(operation, CONVERT_RESERVATION_KEYS, "operation");
  const conversionId = assertRef(operation.conversion_id, "operation.conversion_id");
  const reservationId = assertRef(operation.reservation_id, "operation.reservation_id");
  const amount = assertCostUnits(
    operation.actual_amount_units, "operation.actual_amount_units", { minimum: 1 });
  const vendorReference = assertVendorReference(
    operation.vendor_reference, "operation.vendor_reference");
  assertInstant(operation.settled_at, "operation.settled_at");
  if (ledger.open_reservations[reservationId] === undefined
    && ledger.closed_reservations[reservationId] === undefined) {
    fail("unknown_reservation", "operation.reservation_id names a reservation this ledger never made",
      { path: "operation.reservation_id", reservation_id: reservationId });
  }
  const argumentDigest = digest(copy(operation));

  return replayOrApply(ledger, operation.operation_id, argumentDigest, () => {
    const open = ledger.open_reservations[reservationId];
    if (open === undefined) {
      return {
        ledger,
        outcome: outcome({
          accepted: false, reason_id: "reservation_not_open", reservation_id: reservationId,
          detail: "this reservation has already been cancelled or converted; converting it again would double count the charge",
        }),
      };
    }
    const existing = ledger.vendor_charges[vendorReference];
    if (existing !== undefined) {
      return {
        ledger,
        outcome: outcome({
          accepted: false, reason_id: "duplicate_vendor_charge",
          vendor_reference: vendorReference, existing_entry_id: existing,
        }),
      };
    }
    const release = makeEntry(ledger.sequence, {
      kind: "reservation_release",
      node_id: open.node_id,
      amount_units: open.amount_units,
      operation_id: operation.operation_id,
      occurred_at: operation.settled_at,
      reservation_id: reservationId,
      conversion_id: conversionId,
    });
    const actual = makeEntry(ledger.sequence + 1, {
      kind: "actual",
      node_id: open.node_id,
      amount_units: amount,
      operation_id: operation.operation_id,
      occurred_at: operation.settled_at,
      reservation_id: reservationId,
      conversion_id: conversionId,
      vendor_reference: vendorReference,
    });
    const closed = closeReservation(ledger, reservationId, release, [actual]);
    return {
      ledger: {
        ...closed,
        vendor_charges: { ...closed.vendor_charges, [vendorReference]: actual.entry_id },
      },
      outcome: outcome({
        accepted: true, reason_id: "reservation_converted",
        node_id: open.node_id, reservation_id: reservationId, conversion_id: conversionId,
        released_units: open.amount_units, actual_units: amount,
        variance_units: amount - open.amount_units,
        release_entry_id: release.entry_id, actual_entry_id: actual.entry_id,
      }),
    };
  });
}

const CONVERT_LIABILITY_KEYS = Object.freeze([
  "operation_id", "conversion_id", "liability_id", "actual_amount_units",
  "vendor_reference", "settled_at",
]);

/**
 * Convert a late liability to an actual — the same exactly-once replacement,
 * for a charge that was recorded as owed before it was settled.
 *
 * The vendor reference must be a NEW one: the liability already consumed the
 * reference it arrived under, and settling it is a second event about the same
 * money rather than the same charge posted twice. A caller who has only one
 * reference for both should convert with a settlement-scoped reference; the
 * conversion id is what ties the two entries together.
 */
export function convertLiabilityToActual(ledger, operation) {
  assertLedger(ledger);
  assertObject(operation, "operation");
  assertClosedKeys(operation, CONVERT_LIABILITY_KEYS, "operation");
  assertRequiredKeys(operation, CONVERT_LIABILITY_KEYS, "operation");
  const conversionId = assertRef(operation.conversion_id, "operation.conversion_id");
  const liabilityId = assertRef(operation.liability_id, "operation.liability_id");
  const amount = assertCostUnits(
    operation.actual_amount_units, "operation.actual_amount_units", { minimum: 1 });
  const vendorReference = assertVendorReference(
    operation.vendor_reference, "operation.vendor_reference");
  assertInstant(operation.settled_at, "operation.settled_at");
  if (ledger.open_liabilities[liabilityId] === undefined
    && ledger.closed_liabilities[liabilityId] === undefined) {
    fail("unknown_liability", "operation.liability_id names a liability this ledger never recorded",
      { path: "operation.liability_id", liability_id: liabilityId });
  }
  const argumentDigest = digest(copy(operation));

  return replayOrApply(ledger, operation.operation_id, argumentDigest, () => {
    const open = ledger.open_liabilities[liabilityId];
    if (open === undefined) {
      return {
        ledger,
        outcome: outcome({
          accepted: false, reason_id: "liability_not_open", liability_id: liabilityId,
          detail: "this liability has already been settled; converting it again would double count the charge",
        }),
      };
    }
    const existing = ledger.vendor_charges[vendorReference];
    if (existing !== undefined) {
      return {
        ledger,
        outcome: outcome({
          accepted: false, reason_id: "duplicate_vendor_charge",
          vendor_reference: vendorReference, existing_entry_id: existing,
        }),
      };
    }
    const release = makeEntry(ledger.sequence, {
      kind: "liability_release",
      node_id: open.node_id,
      amount_units: open.amount_units,
      operation_id: operation.operation_id,
      occurred_at: operation.settled_at,
      liability_id: liabilityId,
      conversion_id: conversionId,
    });
    const actual = makeEntry(ledger.sequence + 1, {
      kind: "actual",
      node_id: open.node_id,
      amount_units: amount,
      operation_id: operation.operation_id,
      occurred_at: operation.settled_at,
      liability_id: liabilityId,
      conversion_id: conversionId,
      vendor_reference: vendorReference,
    });
    const closed = closeLiability(ledger, liabilityId, release, [actual]);
    return {
      ledger: {
        ...closed,
        vendor_charges: { ...closed.vendor_charges, [vendorReference]: actual.entry_id },
      },
      outcome: outcome({
        accepted: true, reason_id: "liability_converted",
        node_id: open.node_id, liability_id: liabilityId, conversion_id: conversionId,
        released_units: open.amount_units, actual_units: amount,
        variance_units: amount - open.amount_units,
        release_entry_id: release.entry_id, actual_entry_id: actual.entry_id,
      }),
    };
  });
}

/**
 * The registry of operations, so a caller can drive a sequence — a race
 * fixture, a replay, a recovery — without a switch of its own, and so that
 * every operation this module supports is enumerable rather than discovered by
 * reading the exports.
 */
export const V5_LEDGER_OPERATIONS = Object.freeze({
  cancel_reservation: cancelReservation,
  convert_liability_to_actual: convertLiabilityToActual,
  convert_reservation_to_actual: convertReservationToActual,
  post_actual: postActual,
  record_estimate: recordEstimate,
  record_late_liability: recordLateLiability,
  reserve,
});

export const V5_LEDGER_OPERATION_KINDS = Object.freeze(Object.keys(V5_LEDGER_OPERATIONS).sort());

/**
 * Apply a sequence of operations in the given order, returning the final ledger
 * and every outcome.
 *
 * THIS IS AN ORDER AND IT IS NOT A RACE. Each step here sees the ledger the
 * step before it produced, which is the single-writer case: what it proves is
 * that no sequence can admit more than a ceiling allows, and that is worth
 * proving in both directions. It proves nothing about two callers who computed
 * against the SAME ledger, because a sequence cannot express that — the
 * compare-and-swap boundary below is where that case is settled, and the
 * fixtures that drive it are the ones labelled RACE.
 */
export function applyOperations(ledger, operations) {
  assertLedger(ledger);
  if (!Array.isArray(operations)) {
    fail("invalid_shape", "operations must be an array", { path: "operations" });
  }
  let current = ledger;
  const outcomes = [];
  operations.forEach((step, index) => {
    const path = `operations[${index}]`;
    assertObject(step, path);
    assertClosedKeys(step, ["kind", "operation"], path);
    assertRequiredKeys(step, ["kind", "operation"], path);
    const apply = V5_LEDGER_OPERATIONS[step.kind];
    if (apply === undefined) {
      fail("unknown_operation_kind", `${path}.kind is not a registered ledger operation`,
        { path: `${path}.kind`, value: step.kind, registered: [...V5_LEDGER_OPERATION_KINDS] });
    }
    const result = apply(current, step.operation);
    current = result.ledger;
    outcomes.push(result.outcome);
  });
  return { ledger: current, outcomes: deepFreeze(outcomes) };
}

// ---------------------------------------------------------------------------
// The compare-and-swap commit boundary.
// ---------------------------------------------------------------------------

/**
 * THE PROBLEM THIS SECTION EXISTS TO SOLVE, said plainly because the first
 * version of this module did not solve it and said so.
 *
 * The direct calls above — `reserve`, `convertReservationToActual` and the
 * rest — are a SINGLE-WRITER interface. Each takes a ledger value and returns
 * the next one, and a caller that has the current value is by construction the
 * only writer. Sequencing such calls proves the arithmetic, and it is all the
 * single-writer path ever needs.
 *
 * It proves nothing at all about two writers. Two callers who each READ the
 * same ledger value and then each call `reserve` both get an answer computed
 * against a ledger that no longer exists by the time the second one acts, and
 * nothing in the single-writer interface can tell the second one so. Both can
 * be told "accepted" for the same headroom. Applying the two calls in a chosen
 * ORDER hides this rather than fixing it, because the order is exactly the
 * thing a race does not have.
 *
 * So a commit here NAMES THE BASE IT WAS COMPUTED FROM and the ledger admits
 * it only from that base:
 *
 *   1. `prepareCommit(base, step)` computes the whole operation against `base`
 *      and hands back the result WITHOUT installing it — the outcome the
 *      caller would get, the ledger it would produce, and the version and
 *      state digest of the base it assumed.
 *   2. A cell opened by `openLedgerCell` OWNS the value, and `cell.commit(
 *      prepared)` is the only point at which anything is installed. The cell
 *      checks the proposal's base against the version and state digest it is
 *      actually holding, rederives the step itself rather than trusting the
 *      ledger it was handed, and replaces the held value exclusively — the
 *      read, the decision and the replacement are one synchronous step. A
 *      proposal computed against a base the cell has moved off is refused
 *      `version_conflict` naming both versions; a proposal whose rederived
 *      result does not match what it carries is refused
 *      `prepared_commit_mismatch`. There is no commit point that takes the
 *      current value as an argument, because a caller-supplied current value
 *      is not an admission point at all.
 *   3. `cell.recompute(prepared)` is the loser's only honest move: compute the
 *      same step again against what actually committed. The second answer can
 *      differ from the first — that is the point. A reservation that fitted
 *      the old headroom meets `ceiling_exceeded` against the new one; a
 *      conversion of a reservation somebody else just converted meets
 *      `reservation_not_open`.
 *
 * WHY BOTH A VERSION AND A DIGEST. The version alone cannot tell two different
 * ledgers of the same age apart, and a commit computed against one lineage must
 * never install onto another. The digest pins the exact state; the version is
 * what a human reads in the refusal.
 *
 * WHAT THIS IS NOT. ONE cell in ONE process, holding one in-memory value: the
 * boundary is only as wide as that cell, and two writers are admitted against
 * each other only because they go through the same one. This is a real
 * admission boundary against a stale or a doctored proposal — the thing that
 * was missing — and it is not durable serialization. Two processes
 * against a stored ledger need a row lock or a serializable transaction to
 * supply this same single admission point, and that gap stays named on the
 * projection rather than quietly closed by this section.
 */

export const V5_LEDGER_COMMIT_SCHEMA_VERSION = "cost-ledger-commit.v1";

const PREPARED_COMMIT_KEYS = Object.freeze([
  "schema_version", "kind", "operation", "base_version", "base_state_digest",
  "outcome", "next_ledger",
]);

/**
 * A ledger's version: how many DISTINCT operations it has applied.
 *
 * Derived from the applied index rather than maintained beside it, for the same
 * reason every total here is derived — a maintained counter can drift from the
 * thing it counts and a derived one cannot. It counts operations and not
 * entries on purpose: a refused reservation writes no entry but is still a
 * decision the ledger made against a state, and a commit computed before that
 * refusal is stale. A replay applies no new operation and so does not advance
 * it.
 */
export function ledgerVersion(ledger) {
  assertLedger(ledger);
  return Object.keys(ledger.applied).length;
}

function sortedPairs(map) {
  return Object.keys(map).sort().map(key => [key, map[key]]);
}

/**
 * The exact bytes a commit pins itself to. Key order is canonicalised, so two
 * ledgers that genuinely hold the same state digest the same however they got
 * there, and only the outcomes' ARGUMENTS are hashed — outcomes are a
 * deterministic function of the log and hashing them would add nothing but a
 * second chance to differ.
 */
function ledgerStatePreimage(ledger) {
  return {
    schema_version: ledger.schema_version,
    tree_id: ledger.tree.tree_id,
    tree_version: ledger.tree.tree_version,
    sequence: ledger.sequence,
    entries: ledger.entries,
    applied: Object.keys(ledger.applied).sort()
      .map(id => [id, ledger.applied[id].argument_digest]),
    open_reservations: sortedPairs(ledger.open_reservations),
    closed_reservations: sortedPairs(ledger.closed_reservations),
    open_liabilities: sortedPairs(ledger.open_liabilities),
    closed_liabilities: sortedPairs(ledger.closed_liabilities),
    vendor_charges: sortedPairs(ledger.vendor_charges),
  };
}

/** The identity of a ledger's state — what `base_state_digest` compares. */
export function ledgerStateDigest(ledger) {
  assertLedger(ledger);
  return digest(copy(ledgerStatePreimage(ledger)));
}

function assertStep(step, path) {
  assertObject(step, path);
  assertClosedKeys(step, ["kind", "operation"], path);
  assertRequiredKeys(step, ["kind", "operation"], path);
  const apply = V5_LEDGER_OPERATIONS[step.kind];
  if (apply === undefined) {
    fail("unknown_operation_kind", `${path}.kind is not a registered ledger operation`,
      { path: `${path}.kind`, value: step.kind, registered: [...V5_LEDGER_OPERATION_KINDS] });
  }
  return apply;
}

/**
 * Compute an operation against a base ledger WITHOUT installing it.
 *
 * The base is untouched: everything here is immutable, so the prepared ledger
 * is a value beside the base rather than a mutation of it, and preparing two
 * commits from one base is exactly what two concurrent readers do.
 */
export function prepareCommit(ledger, step) {
  assertLedger(ledger);
  const apply = assertStep(step, "step");
  const baseVersion = ledgerVersion(ledger);
  const baseStateDigest = ledgerStateDigest(ledger);
  const result = apply(ledger, step.operation);
  return deepFreeze({
    schema_version: V5_LEDGER_COMMIT_SCHEMA_VERSION,
    kind: step.kind,
    operation: copy(step.operation),
    base_version: baseVersion,
    base_state_digest: baseStateDigest,
    outcome: result.outcome,
    next_ledger: result.ledger,
  });
}

function assertPreparedCommit(prepared) {
  if (!isPlainObject(prepared)
    || prepared.schema_version !== V5_LEDGER_COMMIT_SCHEMA_VERSION) {
    fail("invalid_shape", "this operation takes a commit from prepareCommit",
      { path: "prepared" });
  }
  assertClosedKeys(prepared, PREPARED_COMMIT_KEYS, "prepared");
  assertRequiredKeys(prepared, PREPARED_COMMIT_KEYS, "prepared");
  // The INSIDE of a prepared commit, not only its outer shape. A commit is an
  // ordinary value: a caller can hold one, copy it and edit the copy, so every
  // field the cell reads is checked here. None of this makes the proposal
  // TRUSTWORTHY — the cell rederives the whole transition rather than
  // believing it — it only makes a malformed commit THROW at the boundary
  // instead of being answered as though it were merely a stale one.
  if (V5_LEDGER_OPERATIONS[prepared.kind] === undefined) {
    fail("unknown_operation_kind", "prepared.kind is not a registered ledger operation",
      { path: "prepared.kind", value: prepared.kind,
        registered: [...V5_LEDGER_OPERATION_KINDS] });
  }
  assertObject(prepared.operation, "prepared.operation");
  assertRef(prepared.operation.operation_id, "prepared.operation.operation_id");
  if (!Number.isInteger(prepared.base_version) || prepared.base_version < 0) {
    fail("invalid_shape", "prepared.base_version must be a count of applied operations",
      { path: "prepared.base_version", value: prepared.base_version });
  }
  if (typeof prepared.base_state_digest !== "string") {
    fail("invalid_shape", "prepared.base_state_digest must be a state digest",
      { path: "prepared.base_state_digest" });
  }
  assertObject(prepared.outcome, "prepared.outcome");
  assertLedger(prepared.next_ledger);
  return prepared;
}

function conflictOutcome(reasonId, prepared, fields) {
  return outcome({
    accepted: false,
    reason_id: reasonId,
    operation_id: prepared.operation.operation_id,
    kind: prepared.kind,
    base_version: prepared.base_version,
    ...fields,
  });
}

/**
 * THE CELL IS THE ADMISSION POINT, and it is the only one.
 *
 * A prepared commit is a proposal and nothing more: it is computed against a
 * base that the ledger may already have left, and it is a value its holder can
 * edit. Neither of those is a defect in the proposal — they are what a
 * concurrent caller's answer IS. What they mean is that a proposal cannot be
 * allowed to admit itself. So a cell OWNS the current ledger value, and:
 *
 *   - `cell.commit(prepared)` admits a proposal only while the cell is still
 *     standing on the exact base the proposal names, by version AND by state
 *     digest, and otherwise returns `version_conflict` and installs nothing;
 *   - it then REDERIVES the whole transition from its own current value and
 *     the proposal's operation, and installs THAT. `prepared.next_ledger` and
 *     `prepared.outcome` are never installed and never believed: they are
 *     compared against the rederivation, and a proposal that disagrees with it
 *     is refused `prepared_commit_mismatch`;
 *   - the read of the held value, the decision, and the replacement are one
 *     synchronous step with no await and no yield between them, so no second
 *     caller can interleave: two proposals from one base produce exactly one
 *     landing and one refusal, whichever is offered first.
 *
 * The loser's only honest move is `cell.recompute(prepared)`: the same step
 * against what actually committed. The second answer can differ from the first
 * — that is the entire point — and it is deliberately not wrapped in a retry
 * loop, because a loop would swallow the refusal the conflict exists to
 * produce.
 *
 * WHY BOTH A VERSION AND A DIGEST. A version alone cannot tell two different
 * ledgers of the same age apart, and a commit computed against one lineage must
 * never install onto another. The digest pins the exact state; the version is
 * what a human reads in the refusal.
 *
 * WHAT THIS IS NOT. ONE cell in ONE process, holding one in-memory value. This
 * is a real admission boundary against a stale or a doctored proposal — the
 * thing that was missing — and it is not durable serialization. Two processes
 * against a stored ledger need a row lock or a serializable transaction to
 * supply this same single admission point, and that gap stays named on the
 * projection rather than quietly closed by this section.
 */
export function openLedgerCell(ledger) {
  assertLedger(ledger);
  let held = ledger;

  return Object.freeze({
    /** The value the cell is standing on right now. */
    read() { return held; },

    version() { return ledgerVersion(held); },

    stateDigest() { return ledgerStateDigest(held); },

    /** Compute a proposal against the cell's current value, installing nothing. */
    prepare(step) { return prepareCommit(held, step); },

    /** The loser's protocol: the same step again, against what actually committed. */
    recompute(prepared) {
      assertPreparedCommit(prepared);
      return prepareCommit(held, { kind: prepared.kind, operation: copy(prepared.operation) });
    },

    commit(prepared) {
      assertPreparedCommit(prepared);
      // From here to the assignment is one synchronous step.
      const current = held;
      const currentVersion = ledgerVersion(current);
      const currentStateDigest = ledgerStateDigest(current);
      if (currentVersion !== prepared.base_version
        || currentStateDigest !== prepared.base_state_digest) {
        return {
          committed: false,
          ledger: current,
          outcome: conflictOutcome("version_conflict", prepared, {
            current_version: currentVersion,
            base_state_digest: prepared.base_state_digest,
            current_state_digest: currentStateDigest,
          }),
        };
      }
      const rederived = V5_LEDGER_OPERATIONS[prepared.kind](current, prepared.operation);
      const rederivedStateDigest = ledgerStateDigest(rederived.ledger);
      const proposedStateDigest = ledgerStateDigest(prepared.next_ledger);
      if (rederivedStateDigest !== proposedStateDigest
        || digest(copy(rederived.outcome)) !== digest(copy(prepared.outcome))) {
        return {
          committed: false,
          ledger: current,
          outcome: conflictOutcome("prepared_commit_mismatch", prepared, {
            current_version: currentVersion,
            proposed_state_digest: proposedStateDigest,
            rederived_state_digest: rederivedStateDigest,
          }),
        };
      }
      held = rederived.ledger;
      return { committed: true, ledger: held, outcome: rederived.outcome };
    },
  });
}

// ---------------------------------------------------------------------------
// The conservation law.
// ---------------------------------------------------------------------------

/**
 * Prove the ledger conserves. Throws on the first violation; returns the proof
 * it computed so a test can assert against the numbers rather than only against
 * the absence of a throw.
 *
 * THE SIX CLAUSES:
 *
 *   1. The log is contiguous and each entry id appears once.
 *   2. Every open and closed reservation partitions the reservation ids the log
 *      carries, and no reservation is released twice.
 *   3. Outstanding reservations summed over nodes equal the open reservations
 *      held on the ledger — the derived view and the index agree.
 *   4. The same for liabilities.
 *   5. Every rolled-up figure equals own plus the children's rolled-up figures,
 *      at every node and for every measure. Estimates are excluded from the
 *      accrual clauses below because an estimate is a restatement, not a sum,
 *      but they ARE included in this one: a parent's rolled-up estimate is
 *      genuinely the sum of its own current estimate and its children's.
 *   6. Every conversion id carries exactly two entries — one release and one
 *      actual, on the same node — and the release equals the full original
 *      amount, so nothing is netted and nothing is counted twice.
 */
export function assertLedgerConservation(ledger) {
  assertLedger(ledger);
  const { tree, entries } = ledger;

  if (entries.length !== ledger.sequence) {
    fail("conservation_violated", "the entry count and the sequence disagree",
      { entries: entries.length, sequence: ledger.sequence });
  }
  const entryIds = new Set();
  entries.forEach((entry, index) => {
    if (entry.sequence !== index) {
      fail("conservation_violated", "the log is not contiguous",
        { entry_id: entry.entry_id, sequence: entry.sequence, index });
    }
    if (entryIds.has(entry.entry_id)) {
      fail("conservation_violated", "an entry id appears twice", { entry_id: entry.entry_id });
    }
    entryIds.add(entry.entry_id);
    if (entry.reservation_id !== null && entry.liability_id !== null) {
      fail("conservation_violated", "an entry claims both a reservation and a liability",
        { entry_id: entry.entry_id });
    }
  });

  const releasedReservations = Object.create(null);
  const madeReservations = Object.create(null);
  const releasedLiabilities = Object.create(null);
  const madeLiabilities = Object.create(null);
  for (const entry of entries) {
    if (entry.kind === "reservation") {
      if (madeReservations[entry.reservation_id] !== undefined) {
        fail("conservation_violated", "one reservation id was reserved twice",
          { reservation_id: entry.reservation_id });
      }
      madeReservations[entry.reservation_id] = entry.amount_units;
    }
    if (entry.kind === "reservation_release") {
      if (releasedReservations[entry.reservation_id] !== undefined) {
        fail("conservation_violated", "one reservation was released twice",
          { reservation_id: entry.reservation_id });
      }
      releasedReservations[entry.reservation_id] = entry.amount_units;
    }
    if (entry.kind === "late_liability") {
      if (madeLiabilities[entry.liability_id] !== undefined) {
        fail("conservation_violated", "one liability id was recorded twice",
          { liability_id: entry.liability_id });
      }
      madeLiabilities[entry.liability_id] = entry.amount_units;
    }
    if (entry.kind === "liability_release") {
      if (releasedLiabilities[entry.liability_id] !== undefined) {
        fail("conservation_violated", "one liability was released twice",
          { liability_id: entry.liability_id });
      }
      releasedLiabilities[entry.liability_id] = entry.amount_units;
    }
  }
  for (const [reservationId, amount] of Object.entries(releasedReservations)) {
    if (madeReservations[reservationId] === undefined) {
      fail("conservation_violated", "a release names a reservation the log never made",
        { reservation_id: reservationId });
    }
    if (madeReservations[reservationId] !== amount) {
      fail("conservation_violated", "a release does not withdraw the whole reservation",
        { reservation_id: reservationId, reserved: madeReservations[reservationId], released: amount });
    }
  }
  for (const [liabilityId, amount] of Object.entries(releasedLiabilities)) {
    if (madeLiabilities[liabilityId] === undefined) {
      fail("conservation_violated", "a release names a liability the log never recorded",
        { liability_id: liabilityId });
    }
    if (madeLiabilities[liabilityId] !== amount) {
      fail("conservation_violated", "a release does not withdraw the whole liability",
        { liability_id: liabilityId, recorded: madeLiabilities[liabilityId], released: amount });
    }
  }

  const openReservationTotal = Object.values(ledger.open_reservations)
    .reduce((sum, held) => sum + held.amount_units, 0);
  const openLiabilityTotal = Object.values(ledger.open_liabilities)
    .reduce((sum, held) => sum + held.amount_units, 0);

  const { own, rolled } = rollUp(ledger);
  const ownOutstandingReservations = tree.node_ids
    .reduce((sum, id) => sum + own[id].reservation_outstanding_units, 0);
  if (ownOutstandingReservations !== openReservationTotal) {
    fail("conservation_violated",
      "the outstanding reservations derived from the log do not match the open reservation index",
      { derived: ownOutstandingReservations, index: openReservationTotal });
  }
  const ownOutstandingLiabilities = tree.node_ids
    .reduce((sum, id) => sum + own[id].liability_outstanding_units, 0);
  if (ownOutstandingLiabilities !== openLiabilityTotal) {
    fail("conservation_violated",
      "the outstanding liabilities derived from the log do not match the open liability index",
      { derived: ownOutstandingLiabilities, index: openLiabilityTotal });
  }

  for (const id of tree.node_ids) {
    for (const measure of MEASURES) {
      const expected = tree.children[id]
        .reduce((sum, child) => sum + rolled[child][measure], own[id][measure]);
      if (rolled[id][measure] !== expected) {
        fail("conservation_violated", "a rolled-up figure does not equal own plus children",
          { node_id: id, measure, rolled: rolled[id][measure], expected });
      }
    }
  }
  for (const measure of ["actual_units", "reservation_gross_units", "liability_gross_units"]) {
    const everyNode = tree.node_ids.reduce((sum, id) => sum + own[id][measure], 0);
    if (rolled[tree.root_node_id][measure] !== everyNode) {
      fail("conservation_violated", "the portfolio total does not equal every node's own total",
        { measure, root: rolled[tree.root_node_id][measure], every_node: everyNode });
    }
  }

  const conversions = Object.create(null);
  for (const entry of entries) {
    if (entry.conversion_id === null) continue;
    (conversions[entry.conversion_id] ??= []).push(entry);
  }
  for (const [conversionId, group] of Object.entries(conversions)) {
    if (group.length !== 2) {
      fail("conservation_violated", "a conversion did not append exactly two entries",
        { conversion_id: conversionId, entries: group.map(entry => entry.entry_id) });
    }
    const releases = group.filter(entry =>
      entry.kind === "reservation_release" || entry.kind === "liability_release");
    const actuals = group.filter(entry => entry.kind === "actual");
    if (releases.length !== 1 || actuals.length !== 1) {
      fail("conservation_violated", "a conversion is one release and one actual",
        { conversion_id: conversionId, kinds: group.map(entry => entry.kind).sort() });
    }
    if (releases[0].node_id !== actuals[0].node_id) {
      fail("conservation_violated", "a conversion moved the charge to another node",
        { conversion_id: conversionId, released_from: releases[0].node_id, posted_to: actuals[0].node_id });
    }
  }

  return deepFreeze({
    entry_count: entries.length,
    reservation_ids: Object.keys(madeReservations).sort(),
    liability_ids: Object.keys(madeLiabilities).sort(),
    open_reservation_units: openReservationTotal,
    open_liability_units: openLiabilityTotal,
    portfolio_actual_units: rolled[tree.root_node_id].actual_units,
    portfolio_committed_units: rolled[tree.root_node_id].committed_units,
    conversion_ids: Object.keys(conversions).sort(),
  });
}

// ---------------------------------------------------------------------------
// The honest, zero-effect projection.
// ---------------------------------------------------------------------------

const PREIMAGE = deepFreeze({
  schema_version: V5_LEDGER_SCHEMA_VERSION,
  commit_schema_version: V5_LEDGER_COMMIT_SCHEMA_VERSION,
  scope_kinds: [...V5_SCOPE_KINDS],
  entry_kinds: [...V5_LEDGER_ENTRY_KINDS],
  entry_keys: [...ENTRY_KEYS],
  refusal_reasons: [...V5_LEDGER_REFUSAL_REASONS],
  overdrawn_remedies: [...V5_OVERDRAWN_REMEDIES],
  operation_kinds: [...V5_LEDGER_OPERATION_KINDS],
  measures: [...MEASURES],
});

/** The exact bytes every closed vocabulary in this module is hashed over. */
export function v5CostLedgerPreimage() {
  return PREIMAGE;
}

export const V5_COST_LEDGER_DIGEST = digest(copy(PREIMAGE));

/** What this ledger guarantees, what it refuses, and what is still missing. */
export function v5CostLedgerProjection() {
  return deepFreeze({
    schema_version: V5_LEDGER_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    tree_schema_version: V5_SCOPE_TREE_SCHEMA_VERSION,
    entry_schema_version: V5_LEDGER_ENTRY_SCHEMA_VERSION,
    projection_schema_version: V5_LEDGER_PROJECTION_SCHEMA_VERSION,
    commit_schema_version: V5_LEDGER_COMMIT_SCHEMA_VERSION,
    preimage_digest: V5_COST_LEDGER_DIGEST,
    // Properties, not aspirations: each is enforced above and tested.
    ancestor_totals_are_derived_not_maintained: true,
    ceiling_can_refuse_an_actual: false,
    ceiling_can_refuse_a_late_liability: false,
    conversion_can_run_twice: false,
    retry_creates_a_second_entry: false,
    overdrawn_denies_new_reservation: true,
    overdrawn_refusal_touches_existing_reservations: false,
    commit_names_the_base_it_was_computed_from: true,
    stale_base_commit_can_be_admitted: false,
    two_commits_from_one_base_can_both_land: false,
    commit_admission_point_is_a_cell_that_owns_the_ledger: true,
    a_caller_supplied_next_ledger_can_be_installed: false,
    a_commit_is_rederived_before_it_is_installed: true,
    sibling_headroom_absorbs_an_overage: false,
    negative_amount_accepted: false,
    // The effect class, said plainly so nobody reads a vendor reference as a bill.
    effect_class: "internal_financial_accounting_no_external_payment",
    external_payment_reachable_here: false,
    unimplemented_dependencies: [
      "a durable store: this ledger lives in a JavaScript value and nothing it records survives the process; the tables and the migration it would need are stated in the slice's author report under migration_owed and are deliberately NOT written here, because the migration-number frontier is a serialized surface owned by one writer at a time",
      "a durable cross-process serialization boundary: the compare-and-swap above is real — the ledger cell OWNS the current value, a commit names the version and state digest of the base it was computed from, the cell admits exactly one commit per base and rederives the transition from its own value rather than installing the one it was handed, so a caller holding a stale read or a doctored proposal is refused rather than quietly granted a second claim on one headroom — but both callers are still two holders of ONE cell inside ONE process; two processes against the durable store above need a row lock or a serializable transaction to supply the same single admission point, and no test in this repository can stand in for that",
      "an incident opener: Q142.D1 requires an overdrawn hierarchy to open an incident, and this module can only REPORT that requirement on its projection — opening one is the record layer's open-incident verb, which is authority-bound and unreachable from a pure module",
      "an authority amendment path: raising a ceiling means a new scope-tree version from whoever holds the authority; this module recompiles a tree it is handed and has no way to tell an authorised amendment from an edited fixture",
      "a vendor charge feed: every actual and liability here arrives on an argument, so the ledger cannot tell a real provider charge from a typed-in one and never claims to",
    ],
    effects: V5_NO_EFFECTS,
  });
}
