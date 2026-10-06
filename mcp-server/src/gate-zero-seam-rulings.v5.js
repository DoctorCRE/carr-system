// DoctorCRE v5 slice V5-A02, the seam half: THE RULING TABLE, and it is the
// only switch in this build.
//
// WHAT THIS FILE IS. Three seams that Gate Zero is owed have a store behind
// each of them — cards 11, 12 and 13 of JOE-GATE-ZERO-CARDS.md. The readers for
// all three are built and tested, and ALL THREE RULINGS ARE LIVE: Joe ruled the
// three cards, and his decision ids sit on the three `decision_id:` lines below.
//
//   card 11  16c7cdfb-b675-4b6a-bbff-4bbdab46baf8
//            seam: which store an accepted predecessor outcome comes from —
//            the record layer's own Work Request outcome feedback, status
//            accepted
//   card 12  f7c486d6-5bee-4c4c-a76f-c0f162f66db8
//            seam: which scheduler surface supplies a canary and the
//            observation after it — ops.service / the Control Plane ledger,
//            with bin/run-scheduled.sh's ops.run rows as the observation after
//            dispatch
//   card 13  87e9e11e-64b2-49b3-a6aa-4901c24eaa91
//            seam: which surface reports a gate's own conclusion — hosted CI
//            check conclusions via the GitHub checks API, which is the merge
//            gate today
//
// So all three readers read. The switch is still one line per card, and it
// turns both ways: put `null` back on a card's `decision_id:` line — or any
// value that is not a well-formed decision uuid — and that card's reader
// switches back OFF, returning the identical refusal Gate Zero returns without
// touching its store. The seams are open because of those three lines and
// nothing else.
//
// WHAT EACH RULING NAMES IS SAID IN A COMMENT AND NOT IN AN EXPORTED VALUE:
// the only thing a consumer can reach is the frozen pair `seamRulingRef`
// returns for a card token it already holds. There is no route from this
// module's surface to the table, ruled or not.
//
// WHAT MAKES THIS THE ONLY SWITCH, said plainly because the whole slice exists
// to refuse the alternatives:
//
//   * NO CALLER-SUPPLIED DECISION ID. `seamRulingRef` takes a CARD TOKEN and
//     nothing else. There is no argument that could carry a decision id, and
//     the only decision id it can ever return is the one written on the line
//     below. A ruling is a commit, not a call.
//   * NO EXPORTED TABLE. The table itself is module-private. A consumer cannot
//     read it, cannot enumerate it, and cannot hold a reference to an entry
//     whose `decision_id` it might later hope to see change.
//   * NO ENVIRONMENT OVERRIDE. Nothing in this file or in the reader module
//     reads the process environment. A seam an env var could open is a seam
//     anyone with a shell can open.
//   * NO PARTIAL EVIDENCE. While a decision id is null the reader does not
//     open its store, does not run a query, and reports nothing it might have
//     found. Half an answer read from an unruled store is still an unruled
//     read.
//
// HOW THESE RULINGS LANDED, and how any later one would. Joe pasted each
// decision id — the uuid `log-decision` returns — in place of the `null` on
// that card's `decision_id:` line, and no production logic changed with them.
// Each reader now reads the store named on the `store_ref:` line beside its id.
// Had he ruled a DIFFERENT store than the one the card named, the `store_ref`
// line would have changed too, and it must name a member of STORE_REFS below —
// an unknown store ref makes the lookup answer "not ruled", so that card's
// reader would keep refusing rather than guessing.
//
// WHY THE STORE REF IS HERE AND NOT IN THE READER. Cards 11-13 each ask two
// questions at once: is this store authoritative for this purpose, and is it
// this store. Both answers belong to the same ruling, so they sit on the same
// two lines and are read together. A reader that chose its own store would be
// deciding half of what it was asked. The reader still holds a veto — it serves
// exactly one store ref per card and refuses any other — so the two halves have
// to agree before a single row is fetched.
//
// THE PRODUCER SEAM IS NOT HERE. `seam:gate-zero-read-only-outcome-producer` is
// cards 9 and 10 — who holds the oracle, and whether r7 carries the
// registration — and it is deliberately NOT built. Its refusal is
// `emitGateZeroOutcome()` in gate-zero-assurance.v5.js, unchanged, and this
// slice neither copies it nor adds a ruling line for it.

import { closedCallable } from "./closed-callable.js";

/** The store refs a ruling may name. Closed: an unknown ref is not a ruling. */
const STORE_REFS = Object.freeze([
  "control-plane:ops.service+ops.run",
  "github:checks",
  "record-layer:work-request-outcome-feedback",
]);

/**
 * The shape a pasted ruling must have: the uuid `log-decision` returns. A value
 * that is not one is not a near miss to be tolerated — the lookup treats it the
 * same way it treats null, because a malformed ruling is not a ruling.
 */
const DECISION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/**
 * THE THREE LINES. One entry per card; `decision_id` is the line Joe's ruling
 * goes on, and all three now carry one. Module-private and frozen: nothing can
 * write one at runtime and nothing outside this file can hold one.
 */
const SEAM_RULINGS = Object.freeze({
  "card:11": Object.freeze({
    question: "which store an accepted predecessor outcome comes from",
    store_ref: "record-layer:work-request-outcome-feedback",
    decision_id: "16c7cdfb-b675-4b6a-bbff-4bbdab46baf8",
  }),
  "card:12": Object.freeze({
    question: "which scheduler surface supplies a canary and the observation after it",
    store_ref: "control-plane:ops.service+ops.run",
    decision_id: "f7c486d6-5bee-4c4c-a76f-c0f162f66db8",
  }),
  "card:13": Object.freeze({
    question: "which surface reports a gate's own conclusion",
    store_ref: "github:checks",
    decision_id: "87e9e11e-64b2-49b3-a6aa-4901c24eaa91",
  }),
});

/**
 * Whether a card's ruling is on the record, and which store it names.
 *
 * THE ONLY EXPORT OF THIS MODULE, and the only way anything reaches the table.
 * It takes a card token — "card:11", "card:12", "card:13" — and returns either
 * null or a frozen pair built ENTIRELY from the constants above. Every other
 * argument, of every type, answers null: an unknown token, a Proxy, a number, a
 * decision id someone hoped would be accepted as one.
 *
 * Today it returns a frozen pair for all three cards, because all three carry a
 * live decision id: card 11 is ruled by 16c7cdfb-b675-4b6a-bbff-4bbdab46baf8,
 * card 12 by f7c486d6-5bee-4c4c-a76f-c0f162f66db8, and card 13 by
 * 87e9e11e-64b2-49b3-a6aa-4901c24eaa91. Put a `null` back on one of those lines
 * and this lookup answers null for that card again, which is what switches its
 * reader off.
 */
function seamRulingRefOf(cardRef) {
  if (typeof cardRef !== "string") return null;
  if (!Object.hasOwn(SEAM_RULINGS, cardRef)) return null;
  const entry = SEAM_RULINGS[cardRef];
  const id = entry.decision_id;
  if (typeof id !== "string" || !DECISION_ID.test(id)) return null;
  if (!STORE_REFS.includes(entry.store_ref)) return null;
  return Object.freeze({ decision_ref: id, store_ref: entry.store_ref });
}

// AMENDMENT 2'S CLOSED SHAPE COMES FROM closed-callable.js (amendment 9, fifth
// correction round, 2026-09-14). This file used to define its own copy, on the
// argument that a self-contained module is worth a duplicated primitive. The
// review measured that argument against the copies and it failed: the local
// copies had already DIVERGED from the shared one — they never froze the
// callable, which is clause (c), the clause the first shape enumeration added
// after finding it missing — so the file whose whole job is to close a probe was
// running the unhardened version of the shape. A security primitive that exists
// five times is hardened in one of five places. There is one definition now, and
// the enumeration control walks every export against it.

/**
 * THE SAME GUARDED BOUNDARY THE OTHER TWO SEAM MODULES USE, and it is here for
 * the same reason rather than because this lookup is expected to throw.
 * `Object.hasOwn` on a frozen literal and a regular expression over a string
 * have no throwing path today — but "today" is a property of this one
 * implementation, and the invariant a caller relies on is a property of the
 * SURFACE: nothing this module exports lets an engine-built error out, so no
 * caller's frame name can ever come back in a stack. Not-ruled is the only thing
 * this lookup can say when it cannot say anything, and it is the fail-closed
 * answer: a null here shuts the seam.
 *
 * AND THE CONSTRUCTION DOOR IS SHUT BY THERE BEING NO DOOR. Every ordinary
 * function is a constructor, so `new seamRulingRef()` was always reachable —
 * and the earlier correction answered it from inside the function, which the
 * fifth review round showed is already too late: the engine reads
 * `newTarget.prototype` BEFORE the body runs, so a caller's object was read on
 * the way in, and a caller could reach the raw function again through
 * `seamRulingRef.prototype.constructor`. An ARROW FUNCTION has neither — no
 * [[Construct]], no `prototype` — so construction is refused by the engine in
 * the caller's own frame, having run no line of this file. That is clause (a) of
 * amendment 2 of 2026-09-12, and null stays the only thing this module says.
 */
const seamRulingRefLookup = (cardRef) => {
  try {
    return seamRulingRefOf(cardRef);
  } catch {
    return null;
  }
};

export const seamRulingRef = closedCallable(seamRulingRefLookup);
