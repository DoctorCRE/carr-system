// CARR MCP server — AMENDMENT 2'S CLOSED CALLABLE, in ONE place.
//
// WHY THIS FILE EXISTS (PR 1013, fourth correction round). Two copies of this
// helper guarded two security-critical export surfaces — identity.js's
// authenticated-identity door and the Gate Zero producer's emit seam — and the
// review found them already drifting: the bodies still matched byte for byte,
// but the clauses each copy documented no longer did, which is the state a
// duplicated primitive is in immediately before the copy that gets hardened
// stops being the copy the forger calls. A shape whose whole job is to close a
// probe is not a shape worth maintaining twice.
//
// WHAT THE SHAPE IS, clause by clause:
//
//   (a) BOUND, NOT BARE. A bound function has no own `prototype` and is not a
//       constructor, so `Reflect.construct` on it throws — and the engine's
//       refusal names `function () { [native code] }` rather than quoting the
//       defining module's own source text back at whoever probed it.
//   (b) An own `Symbol.hasInstance` DATA property answering false WITHOUT
//       reading the left operand. `x instanceof closed` therefore neither walks
//       x's prototype chain nor runs one of its traps; the control the suites
//       use is a Proxy whose `get` and `getPrototypeOf` both throw.
//   (c) FROZEN, so neither (a) nor (b) can be written over afterwards. This is
//       the clause the first shape enumeration found missing.
//
// WHAT THE FIFTH ROUND FOLDED IN (amendment 9, 2026-09-14). The Gate Zero seam
// modules — readers, stores, rulings, the assurance gate and the internal
// binding — each kept a local definition, on the argument that a self-contained
// module is worth a duplicated primitive and that a by-source assertion proved
// each file held exactly one copy. The review measured that argument against the
// copies: all five had already DIVERGED from this one, none of them freezing the
// callable, so clause (c) — added here after the first shape enumeration found
// it missing — was absent from every file whose job is to close a probe. The
// by-source assertion had proved the copy existed, not that it was the hardened
// shape. There is one definition in the tree now and every module imports it;
// the readers suite asserts by source that its module defines NONE.

/** Amendment 2's closed shape, applied to `callable`. */
export function closedCallable(callable) {
  const closed = callable.bind(null);
  Object.defineProperty(closed, Symbol.hasInstance, {
    value: () => false, writable: false, enumerable: false, configurable: false,
  });
  return Object.freeze(closed);
}
