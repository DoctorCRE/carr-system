// V5-A02 — THE ONE RULING PREDICATE THE READERS AND THE GATE BOTH ASK, and the
// only reason it is a file of its own.
//
// THE QUESTION. "Is this card ruled?" has two halves, and both must hold: the
// ruling table must carry a well-formed decision id for the card, AND the store
// that ruling names must be the ONE store that card's reader actually opens. A
// ruling naming another registered store is a valid, live ruling — and not a
// ruling this reader can act on.
//
// WHY IT IS NOT WRITTEN TWICE. gate-zero-assurance.v5.js used to import the
// ruling table itself and read any non-null ruling as a bound seam, which is
// only the first half. A ruling that named another registered store therefore
// made the gate report the seam BOUND while the reader refused that same ruling
// and fell back without a ruling reference: two predicates for one question,
// disagreeing. One predicate, imported by both, is the only shape in which they
// cannot — a second copy of these lines is the defect again, whichever file
// holds it.
//
// WHY IT IS NOT ON THE READER'S SURFACE EITHER, which is the third correction's
// finding on PR 1004. The reader module's promise is FOUR public names — three
// readers and a schema version — and a shared predicate is not one of them; the
// second correction kept the predicate correct by widening that surface, which
// bought agreement between the two files at the price of the promise. So the
// predicate lives here, under src/internal/: reachable by a module that imports
// this path on purpose, reachable through NO public namespace, and asserted so
// by a parsed import graph rather than by a grep — the readers and the gate are
// the only two modules in src that may name this file, and neither re-exports it.
//
// IT IS STILL NOT A DOOR. It takes a CARD TOKEN and nothing else; every value it
// returns is built from the frozen table below and the ruling table's own
// constants; and it is strictly NARROWER than `seamRulingRef`, which is already
// public — a caller learns nothing here that it could not already ask the ruling
// table for, and learns less for every ruling this predicate would refuse. An
// unknown token, a Proxy, a number, a card with no store behind it: each is
// null, which is the fail-closed answer and shuts the seam in both files at once.

import { seamRulingRef } from "../gate-zero-seam-rulings.v5.js";
import { closedCallable } from "../closed-callable.js";

/**
 * The ONE store ref each card's reader will serve. This is the reader's half of
 * the two-part binding, and it is HERE rather than beside the fetchers so that
 * the predicate and the table it consults cannot come apart: the reader pairs a
 * fetcher with a card, this table says which store that card's ruling must name,
 * and a pairing that disagreed with either would still be refused at fetch time
 * by the store's own statement of which store answered.
 */
const CARD_STORE_REF = Object.freeze({
  "card:11": "record-layer:work-request-outcome-feedback",
  "card:12": "control-plane:ops.service+ops.run",
  "card:13": "github:checks",
});

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

/** Both halves, or null. A throw on the way in is a null too. */
const ruledCardBindingOf = (cardRef) => {
  try {
    const ruling = seamRulingRef(cardRef);
    if (ruling === null) return null;
    if (!Object.hasOwn(CARD_STORE_REF, cardRef)) return null;
    if (ruling.store_ref !== CARD_STORE_REF[cardRef]) return null;
    return ruling;
  } catch {
    return null;
  }
};

export const ruledCardBinding = closedCallable(ruledCardBindingOf);
