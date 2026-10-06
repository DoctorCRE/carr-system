// V5-F02 — the authoritative release state holder named by
// seam:deployment-controller-release-receipt-store.
//
// WHAT THIS IS. A module-bound, in-process snapshot of release receipt bodies
// read from ops.release_receipt (WR-000110, migrations/0517_program_controller_seams.sql).
// engineering-program-controller.v5.js binds it at its ONE binding line and
// resolves every release fact through it. It is not a port and not a ledger a
// caller can hand in: nothing on a request path reaches this module, and the
// only writer is the census reader in program-controller-census.v5.js, which
// fills the snapshot from the database immediately before an evaluation.
//
// WHY resolveReceipt IS SYNCHRONOUS. The controller calls it inside a pure
// evaluation, with no await available. The asynchronous work — the read — is
// therefore done first, by the census reader, and this module only answers from
// what that read already returned.
//
// WHY THIS FILE HAS ZERO IMPORTS. The census reader imports the controller and
// this holder; the controller imports this holder. A dependency of its own here
// would close that into a cycle.
//
// This module is deliberately a library, not a program: it takes nothing from a
// process and carries no self-execution construct. The sealed frontier
// predicate in ops/scac-mutation-inventory.mjs matches on PLAIN TEXT, so even
// naming those constructs in a comment here would move the frontier and cost a
// registry successor.

const SNAPSHOT = new Map();

/**
 * Replace the snapshot with exactly the entries given.
 *
 * REPLACE, never merge: a stale receipt left behind from an earlier read is a
 * fact nobody recorded, and the whole point of the seam is that a release fact
 * has one source. `entries` is an iterable of `[receipt_ref, body]` pairs, or a
 * plain object keyed by reference. Each body is frozen on the way in, so a
 * later holder of the same object cannot edit what the evaluator already read.
 */
export function installReleaseReceiptSnapshot(entries) {
  SNAPSHOT.clear();
  const pairs = entries == null
    ? []
    : (typeof entries[Symbol.iterator] === "function" ? [...entries] : Object.entries(entries));
  for (const [receiptRef, body] of pairs) {
    if (typeof receiptRef !== "string" || receiptRef.length === 0) continue;
    SNAPSHOT.set(receiptRef, deepFreeze(structuredClone(body)));
  }
  return SNAPSHOT.size;
}

/** How many receipts the snapshot holds. A proof that nothing was installed. */
export function releaseReceiptSnapshotSize() {
  return SNAPSHOT.size;
}

function deepFreeze(value) {
  if (Array.isArray(value)) { value.forEach(deepFreeze); return Object.freeze(value); }
  if (value !== null && typeof value === "object") {
    Object.values(value).forEach(deepFreeze);
    return Object.freeze(value);
  }
  return value;
}

/**
 * The holder itself. Frozen, with one synchronous method and no setter: the
 * controller binds THIS object, and an empty snapshot answers `null` for every
 * reference — which is a bound holder that holds nothing, not an absent seam.
 */
export const RELEASE_RECEIPT_HOLDER = Object.freeze({
  resolveReceipt(receiptRef) {
    return SNAPSHOT.get(receiptRef) ?? null;
  },
});
