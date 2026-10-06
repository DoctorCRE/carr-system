// WR-000111 — the durable half of the V5 hierarchical cost ledger.
//
// LIBRARY ONLY, and the phrasing here is deliberate: this file carries no
// shebang and none of the three JavaScript main-module constructs the SCAC
// inventory scans for. It scans by SUBSTRING, so naming those constructs in a
// comment is itself enough to register the file as a new sealed script
// entrypoint and move the inventoried frontier. Describe them, never spell them.
//
// TWO ROLES, AND THE SPLIT IS THE WHOLE DESIGN.
//   - The DATABASE is the serialization point. ops.lock_cost_ledger_cell holds
//     the cell row for the caller's whole transaction, which is exactly what
//     hierarchical-cost-ledger.v5.js says a durable store must supply.
//   - The MODULE is the rederiver. The transition is JavaScript over a frozen
//     operation registry and cannot be re-implemented in PL/pgSQL without
//     forking the thing this work exists to preserve. So commitLedgerOperation
//     below loads under the lock, REBUILDS the ledger value, proves the rebuild
//     hashes to the stored digest, and lets openLedgerCell rederive the
//     transition from its own held value. What reaches the database is what the
//     module produced, never a value a caller supplied.

import { createHash } from "node:crypto";

import {
  assertLedgerConservation,
  compileScopeTree,
  ledgerStateDigest,
  ledgerVersion,

  openLedgerCell,
  projectLedger,
  V5_SCOPE_TREE_SCHEMA_VERSION,
} from "./hierarchical-cost-ledger.v5.js";
import { detectCostThresholdCrossings } from "./cost-variance-replan.v5.js";

export const METERING_PATH = "/api/v1/metering";
export const METERING_SCHEMA_VERSION = "doctorcre-v5-metering.v1";

const TREE_REF = /^[a-z0-9][a-z0-9_.:/-]{0,127}$/;
const PERIOD = /^[0-9]{4}-(0[1-9]|1[0-2])$/;

function refuse(code, message, fields = {}) {
  return Object.assign(new Error(message), { code, ...fields });
}

function instant(value) {
  return value instanceof Date ? value.toISOString() : new Date(value).toISOString();
}

// ---------------------------------------------------------------------------
// Loading and rebuilding.
// ---------------------------------------------------------------------------

/**
 * Read the five relations for one tree. Every column named here is inside
 * carr_reader's column grant in 0519; argument_digest and correlation_id are
 * read only on the writer path, which is why they are selected separately.
 */
async function loadTree(client, treeRef, { withArgumentDigest = false } = {}) {
  const tree = await client.query(
    "select tree_ref, tree_id, tree_version from ops.cost_scope_tree where tree_ref = $1",
    [treeRef]);
  if (!tree.rows.length) {
    throw refuse("TENANT_SCOPE_REFUSED", "no such cost scope tree", { tree_ref: treeRef });
  }
  const nodes = await client.query(
    `select node_id, parent_node_id, scope_kind, authorization_ceiling_units
       from ops.cost_scope_node where tree_ref = $1 order by node_id`, [treeRef]);
  const operations = await client.query(
    `select operation_id, kind, ${withArgumentDigest ? "argument_digest," : ""}
            outcome, refusal_reason_id, produced_version
       from ops.cost_ledger_operation where tree_ref = $1 order by produced_version, operation_id`,
    [treeRef]);
  const entries = await client.query(
    `select sequence, entry_id, kind, node_id, amount_units, operation_id, occurred_at,
            reservation_id, liability_id, conversion_id, vendor_reference, basis_digest
       from ops.cost_ledger_entry where tree_ref = $1 order by sequence`, [treeRef]);
  const cell = await client.query(
    "select ledger_version, state_digest from ops.cost_ledger_cell where tree_ref = $1", [treeRef]);
  return {
    tree: tree.rows[0], nodes: nodes.rows, operations: operations.rows,
    entries: entries.rows, cell: cell.rows[0] ?? null,
  };
}

/** The stored node rows, back in the shape compileScopeTree takes. */
function scopeTreeFrom(loaded) {
  return compileScopeTree({
    schema_version: V5_SCOPE_TREE_SCHEMA_VERSION,
    tree_id: loaded.tree.tree_id,
    tree_version: Number(loaded.tree.tree_version),
    nodes: loaded.nodes.map(node => ({
      node_id: node.node_id,
      parent_node_id: node.parent_node_id ?? null,
      scope_kind: node.scope_kind,
      authorization_ceiling_units: Number(node.authorization_ceiling_units),
    })),
  });
}

/** One stored entry row, back in the module's frozen ENTRY_KEYS shape. */
function entryFrom(row) {
  return Object.freeze({
    entry_id: row.entry_id,
    sequence: Number(row.sequence),
    kind: row.kind,
    node_id: row.node_id,
    amount_units: Number(row.amount_units),
    operation_id: row.operation_id,
    occurred_at: instant(row.occurred_at),
    reservation_id: row.reservation_id ?? null,
    liability_id: row.liability_id ?? null,
    conversion_id: row.conversion_id ?? null,
    vendor_reference: row.vendor_reference ?? null,
    basis_digest: row.basis_digest ?? null,
  });
}

/**
 * Rebuild the ledger VALUE from the stored log.
 *
 * Every derived index is recomputed from the entries rather than stored beside
 * them, for the same reason the module derives its own: an index stored next to
 * the log it summarises can drift from it, and a derived one cannot. The proof
 * that this rebuild is the right one is the digest comparison the two callers
 * below run, never this function's own confidence.
 */
function rebuildLedger(loaded) {
  const tree = scopeTreeFrom(loaded);
  const entries = loaded.entries.map(entryFrom);
  const openReservations = {};
  const closedReservations = {};
  const openLiabilities = {};
  const closedLiabilities = {};
  const vendorCharges = {};
  for (const entry of entries) {
    if (entry.kind === "reservation") {
      openReservations[entry.reservation_id] = {
        node_id: entry.node_id, amount_units: entry.amount_units, entry_id: entry.entry_id,
      };
    } else if (entry.kind === "reservation_release") {
      closedReservations[entry.reservation_id] = openReservations[entry.reservation_id];
      delete openReservations[entry.reservation_id];
    } else if (entry.kind === "late_liability") {
      openLiabilities[entry.liability_id] = {
        node_id: entry.node_id, amount_units: entry.amount_units, entry_id: entry.entry_id,
      };
    } else if (entry.kind === "liability_release") {
      closedLiabilities[entry.liability_id] = openLiabilities[entry.liability_id];
      delete openLiabilities[entry.liability_id];
    }
    // A conversion writes its release before its actual, so the LAST entry in
    // sequence order carrying a vendor reference is the one the module holds.
    if (entry.vendor_reference !== null) vendorCharges[entry.vendor_reference] = entry.entry_id;
  }
  const applied = {};
  for (const operation of loaded.operations) {
    applied[operation.operation_id] = {
      argument_digest: operation.argument_digest ?? null,
      outcome: operation.outcome,
    };
  }
  return Object.freeze({
    schema_version: "doctorcre-v5-hierarchical-cost-ledger.v1",
    tree,
    entries: Object.freeze(entries),
    sequence: entries.length,
    applied,
    open_reservations: openReservations,
    closed_reservations: closedReservations,
    open_liabilities: openLiabilities,
    closed_liabilities: closedLiabilities,
    vendor_charges: vendorCharges,
  });
}

// ---------------------------------------------------------------------------
// The writer path.
// ---------------------------------------------------------------------------

/**
 * Apply ONE operation to a stored ledger, under the database's row lock.
 *
 * The order is the contract:
 *   0. ops.lock_cost_ledger_cell — the cross-process admission point;
 *   1. load tree, nodes, operations and entries UNDER that lock;
 *   2. rebuild and prove the rebuilt value hashes to the locked state_digest;
 *   3. openLedgerCell(value).prepare(step) then .commit(prepared), which is
 *      where THE MODULE rederives the transition from its own held value and
 *      can refuse prepared_commit_mismatch;
 *   4. ops.commit_cost_ledger_operation with what the module produced.
 *
 * Nothing a caller supplied is ever passed through. The caller names a step;
 * every version, digest and entry that reaches the database is computed here.
 */
export async function commitLedgerOperation({ client, tree_ref: treeRef, step }) {
  if (typeof treeRef !== "string" || !TREE_REF.test(treeRef)) {
    throw refuse("AUTHORIZATION_REFUSED", "tree_ref is not a reference", { tree_ref: treeRef });
  }
  const locked = await client.query("select ops.lock_cost_ledger_cell($1::text) as cell", [treeRef]);
  const cell = locked.rows[0]?.cell;
  if (!cell?.ok) {
    throw refuse("TENANT_SCOPE_REFUSED", "no cost ledger cell for this tree",
      { tree_ref: treeRef, reason_id: cell?.reason_id ?? "cost_ledger_cell_absent" });
  }

  const loaded = await loadTree(client, treeRef, { withArgumentDigest: true });
  const ledger = rebuildLedger(loaded);
  const rebuiltDigest = ledgerStateDigest(ledger);
  if (rebuiltDigest !== cell.state_digest || ledgerVersion(ledger) !== cell.ledger_version) {
    // The residual hole §B1 names, caught on the NEXT read exactly as promised:
    // an authority caller that wrote a digest its rows do not produce is
    // refused here rather than believed.
    throw refuse("FRESHNESS_UNKNOWN", "stored cost ledger does not rebuild to its own cell", {
      tree_ref: treeRef, current_version: cell.ledger_version,
      current_state_digest: cell.state_digest, rederived_state_digest: rebuiltDigest,
    });
  }

  const holder = openLedgerCell(ledger);
  const prepared = holder.prepare(step);
  const committed = holder.commit(prepared);
  const nextLedger = committed.ledger;
  const newEntries = nextLedger.entries.slice(ledger.entries.length);
  const outcome = committed.outcome;

  const applied = await client.query(
    `select ops.commit_cost_ledger_operation(
       $1::text,$2::integer,$3::text,$4::text,$5::text,$6::text,
       $7::jsonb,$8::text,$9::integer,$10::text,$11::jsonb) as result`,
    [treeRef, prepared.base_version, prepared.base_state_digest,
     prepared.operation.operation_id, `sha256:${argumentDigestOf(prepared)}`,
     prepared.kind, JSON.stringify(outcome),
     outcome.accepted === false ? (outcome.reason_id ?? null) : null,
     ledgerVersion(nextLedger), ledgerStateDigest(nextLedger),
     JSON.stringify(newEntries)]);
  const result = applied.rows[0].result;
  // THE SIGNALS FIRE HERE AND ONLY HERE: on a commit that really moved the
  // cell to a new version. A module-level replay (the operation id is already
  // applied), a database-level replay and a refused cell move all fire nothing,
  // and because the lock above is held, a second process that raced this one
  // loads the ledger AFTER this commit and finds no crossing left to fire.
  //
  // FAIL-CLOSED ON PURPOSE. Detection runs inside the caller's transaction,
  // after the commit function, so a throw here rolls the operation back. That
  // is intended: a crossing is detectable only across THIS commit, so a commit
  // that landed while its signal was lost would lose that warning or replan
  // for good. Nothing about the ledger's own truth is refused by this; the
  // same operation can be retried.
  const moved = committed.committed === true && outcome.replayed !== true
    && result?.ok === true && result?.replayed !== true;
  const signals = moved
    ? detectCostThresholdCrossings({ before_ledger: ledger, after_ledger: nextLedger, scope_ref: treeRef })
    : [];
  return { ok: true, outcome, result, signals };
}

/**
 * The argument digest the database stores for an operation.
 *
 * The module hashes the operation's arguments itself inside every operation
 * body, and does not export that hash — so it is recomputed here from the
 * prepared commit's own copy of the operation, by the same canonical-JSON
 * digest the module uses. A replay offered with different arguments therefore
 * meets a different digest and is refused by the function's step 1.
 */
function argumentDigestOf(prepared) {
  return sha256Hex(canonicalJson(prepared.operation));
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort()
      .map(key => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value === undefined ? null : value);
}

function sha256Hex(text) {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

// ---------------------------------------------------------------------------
// The read path — the C02 metering payload.
// ---------------------------------------------------------------------------

/**
 * THREE HARD RULES, each of which is its own acceptance criterion:
 *   (a) an unenumerable source is PUBLISHED in coverage.sources with
 *       enumerable:false and complete:false, and its quantity contributes
 *       nothing — it is never zeroed silently and never imputed;
 *   (b) policy.cap_units lives only under policy and is never folded into
 *       as_of.quantity or charge_projection;
 *   (c) the scheduler's flat budget is not a node, and this module does not
 *       read it at all.
 */
export async function readMeteringProjection({ client, actor, correlationId, tree_ref: treeRef, period }) {
  if (typeof treeRef !== "string" || !TREE_REF.test(treeRef)) {
    throw refuse("AUTHORIZATION_REFUSED", "tree_ref is not a reference", { tree_ref: treeRef });
  }
  if (period !== undefined && period !== null && period !== "" && !PERIOD.test(String(period))) {
    throw refuse("AUTHORIZATION_REFUSED", "period is not a YYYY-MM window", { period });
  }
  let loaded;
  try {
    loaded = await loadTree(client, treeRef);
  } catch (error) {
    if (error?.code) throw error;
    throw refuse("DEPENDENCY_UNAVAILABLE", "the cost ledger store is unavailable",
      { detail: String(error?.message || error).slice(0, 160) });
  }
  if (!loaded.cell) {
    throw refuse("TENANT_SCOPE_REFUSED", "no cost ledger cell for this tree", { tree_ref: treeRef });
  }

  const ledger = rebuildLedger(loaded);
  assertLedgerConservation(ledger);
  const projection = projectLedger(ledger);
  const root = projection.by_node[projection.root_node_id];

  // The one source this projection has: the stored operation log. It is
  // ENUMERABLE exactly when the cell's own ledger_version equals the number of
  // operation rows a reader can actually list. A cell claiming more applied
  // operations than the log accounts for is a real, reader-visible
  // incompleteness, and the honest answer to it is to WITHHOLD the quantity
  // rather than report a total over a log that is missing rows.
  //
  // The state-digest re-verification is deliberately NOT this check: 0519
  // excludes argument_digest from carr_reader's column grant, so the reader
  // cannot rebuild the exact preimage, and the digest comparison belongs to the
  // writer path above, which holds the lock and reads that column.
  const enumerable = Number(loaded.cell.ledger_version) === loaded.operations.length;
  const sources = [{
    source: `ops.cost_ledger_entry:${treeRef}`,
    enumerable,
    reason_id: enumerable ? null : "operation_log_incomplete",
  }];

  const windowed = period
    ? loaded.entries.filter(row => instant(row.occurred_at).slice(0, 7) === period)
    : loaded.entries;
  const chargeUnits = windowed.reduce((sum, row) => {
    if (row.kind === "actual" || row.kind === "late_liability") return sum + Number(row.amount_units);
    if (row.kind === "liability_release") return sum - Number(row.amount_units);
    return sum;
  }, 0);

  return {
    schema_version: METERING_SCHEMA_VERSION,
    // The four C02 identity fields, mapped from the stored tree and stated so a
    // reader can check the mapping rather than guess it.
    provider: "carr",
    account: loaded.tree.tree_id,
    project: treeRef,
    product: projection.root_node_id,
    period: period || null,
    as_of: {
      // WITHHELD, not zeroed, when the one source is unenumerable.
      quantity: enumerable ? root.rolled_up.committed_units : null,
      // Built in JS. now() is constant inside a transaction and would report
      // the transaction's start as the observation time.
      observed_at: new Date().toISOString(),
    },
    allowance: {
      ceiling_units: root.authorization_ceiling_units,
      effective_ceiling_units: root.effective_ceiling_units,
    },
    // NEVER folded into quantity or charge_projection.
    policy: {
      cap_units: root.effective_ceiling_units,
      source: "ops.cost_scope_node.authorization_ceiling_units",
    },
    estimate: { units: enumerable ? root.rolled_up.estimate_units : null },
    charge_projection: { units: enumerable ? chargeUnits : null },
    coverage: { complete: enumerable, sources },
    hierarchy_overdrawn: projection.hierarchy_overdrawn,
    requires: root.requires,
    correlation_id: correlationId ?? null,
    actor_slug: actor?.slug ?? null,
  };
}

