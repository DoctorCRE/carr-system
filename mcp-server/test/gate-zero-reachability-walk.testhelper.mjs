// THE RUNTIME REACHABILITY WALKER — ONE COPY, SHARED (2026-09-12, PR 1013
// correction round).
//
// WHY IT MOVED HERE. This walk was written and corrected over eight review
// rounds inside gate-zero-seam-readers.v5.test.mjs, and the producer suite owes
// the SAME proof about its own callable: that no container, symbol, prototype,
// accessor, accessor function or inherited getter reached under a child receiver
// hands the value to a consumer. The producer's first draft proved that with two
// source-text patterns instead, which is a grep wearing a guard's clothes —
// `export { v5A02GateZeroEmitOutcome as emit }` matches neither.
//
// IT IS LIFTED, NOT RETYPED. A retyped walk is a second implementation that
// passes because it was written from the same misunderstanding as the code it
// checks. Both suites import THIS file, so a correction to the walk corrects
// both proofs at once, and the readers suite's part-by-part mutation controls
// still measure this code rather than an imitation of it.
//
// It decides nothing about the modules it walks: it answers with the ROUTE a
// value was found by, or null.

/**
 * THE PROPERTY PATH BY WHICH A VALUE IS REACHABLE FROM A NAMESPACE, or null.
 *
 * This is the half that answers "by any path", and after the sixth review it
 * answers it literally. The walk is UNBOUNDED — a cycle-safe visited set is what
 * makes it terminate, in place of the six-edge budget a seven-edge container
 * walked straight past. It enumerates `Reflect.ownKeys`, so a SYMBOL-keyed
 * property is read exactly like a string-named one. And it follows the
 * [[Prototype]] chain, because a value held on a prototype is handed to a caller
 * as readily as one held on the object itself. It returns the route it found the
 * value by, so a failure names `seam.ruledCardBinding` rather than just the
 * module.
 *
 * AND ACCESSORS ARE READ, which is what the seventh review corrected. The sixth
 * correction stepped over a getter unread and said so as a deliberate boundary;
 * that boundary was wrong, because `export const api = { get seam() { return
 * predicate; } }` hands a consumer the predicate at `api.seam` exactly as a data
 * property would, and a guard that answers "by any path" cannot decline to look
 * down the path a consumer actually uses. Every accessor's `get` is invoked
 * INSIDE try/catch and its return value is walked like any other edge. A getter
 * that THROWS is an opaque leaf — a consumer could not have taken a value
 * through it either — and the walk continues with the next key rather than
 * failing.
 *
 * AND THE EIGHTH REVIEW CLOSED THE TWO ROUTES THE SEVENTH LEFT, which are the
 * last two members of the closed set amendment 6 names.
 *
 *   THE ACCESSOR FUNCTIONS ARE THEMSELVES EDGES. `Object.getOwnPropertyDescriptor
 *   (api, "seam").get` is public, retrievable by any consumer, and can BE the
 *   predicate — `Object.defineProperty(api, "seam", { get: predicate })` hands it
 *   over without the getter ever returning it. Both `get` and `set` are walked as
 *   objects, at the route `.seam<get>` / `.seam<set>`, before the value the
 *   getter returns.
 *
 *   AND AN INHERITED GETTER IS INVOKED WITH THE EXPORTED CHILD AS RECEIVER, via
 *   `Reflect.get(proto, key, child)`. The seventh correction invoked it with the
 *   object it was found ON, which for a prototype is not the object a consumer
 *   holds: `Object.create({ get seam() { return this === shape ? null : predicate;
 *   } })` answers the predicate at `api.seam` and answers null to a walk standing
 *   on the prototype. The receiver is threaded down the [[Prototype]] chain and
 *   reset at every ordinary edge, and the visited set is keyed by (value,
 *   receiver) rather than by value, because the same prototype reached under two
 *   receivers is two different answers.
 *
 * The one shape this cannot terminate on is a getter that mints a fresh object
 * on every read, forever; no reachability scan terminates on that, and neither
 * does a consumer reach anything through it.
 *
 * `parts` exists so the mutation controls can revert ONE part of the walk at a
 * time against THIS code rather than against a retyped imitation of it.
 */
export const WHOLE_WALK = Object.freeze({
  bounded: Infinity, symbols: true, prototypes: true, accessors: true,
  accessorFunctions: true, inheritedReceiver: true });

/**
 * AMENDMENT 2'S CLOSED SHAPE, for the two callables this helper exports
 * (2026-09-12, second correction round — the first shipped them as plain
 * function declarations, which carry a prototype, are constructable, and answer
 * `instanceof` by walking the left operand's prototype chain).
 *
 * Bound, so there is no prototype and `Reflect.construct` throws; an own
 * `Symbol.hasInstance` DATA property answering false without reading the operand;
 * frozen, so nothing can be written over it afterwards. It is the same shape the
 * producer, the gate and the readers wear, retyped here rather than imported
 * because a test helper importing a production module to borrow its shape is the
 * dependency this suite's reachability guard exists to forbid.
 */
function closedCallable(callable) {
  const closed = callable.bind(null);
  Object.defineProperty(closed, Symbol.hasInstance, {
    value: () => false, writable: false, enumerable: false, configurable: false,
  });
  return Object.freeze(closed);
}

/** How a key is spelled in a route: `.name` for a string, `[Symbol(x)]` for a symbol. */
const stepFor = key => (typeof key === "symbol" ? `[${String(key)}]` : `.${key}`);

/**
 * THE ONE WALK, with the question left to the caller.
 *
 * `pathToValue` asks "where is this value"; `reachableCallables` asks "what
 * callables are there". They were two walks for about an hour and that is
 * exactly the shape the fifth review round refused in the suite that used them:
 * an enumeration that looked at top-level functions and one object level while
 * the assertion next to it claimed "every callable on the module". One
 * traversal, two visitors — a correction to the walk corrects both answers.
 *
 * `visit(value, path)` is called for every edge, before the edge is descended.
 * Answering a non-undefined value stops the walk and is returned.
 */
function traverse(root, parts, visit) {
  // Keyed by the PAIR (value, receiver): the same prototype reached while a
  // consumer holds two different children can answer two different values, so a
  // set keyed by the object alone would skip the second answer unread.
  const seen = new Map();
  const walk = (value, path, left, receiver) => {
    const answered = visit(value, path);
    if (answered !== undefined) return answered;
    if (left === 0 || value === null) return null;
    const kind = typeof value;
    if (kind !== "object" && kind !== "function") return null;
    // The visited map is what replaces the depth budget: a value whose whole
    // subtree has already been searched under THIS receiver cannot hide the
    // target on a second visit, so revisiting is redundant rather than unsound
    // once the walk is unbounded.
    const held = receiver === undefined ? value : receiver;
    const under = seen.get(value);
    if (under === undefined) seen.set(value, new Set([held]));
    else if (under.has(held)) return null;
    else under.add(held);
    const keys = parts.symbols ? Reflect.ownKeys(value) : Object.getOwnPropertyNames(value);
    for (const key of keys) {
      let descriptor;
      try { descriptor = Object.getOwnPropertyDescriptor(value, key); } catch { continue; }
      // A property whose descriptor cannot be taken at all is the only key
      // stepped over unread.
      if (descriptor === undefined) continue;
      const step = `${path}${stepFor(key)}`;
      if (Object.hasOwn(descriptor, "value")) {
        // An ordinary edge: the value is a new object in the consumer's hand, so
        // the receiver does not travel with it.
        const found = walk(descriptor.value, step, left - 1, undefined);
        if (found !== null) return found;
        continue;
      }
      // THE ACCESSOR FUNCTIONS FIRST, because `descriptor.get` is public and can
      // be the target itself even when calling it returns something harmless.
      if (parts.accessorFunctions)
        for (const role of ["get", "set"]) {
          if (typeof descriptor[role] !== "function") continue;
          const found = walk(descriptor[role], `${step}<${role}>`, left - 1, undefined);
          if (found !== null) return found;
        }
      if (!parts.accessors || typeof descriptor.get !== "function") continue;
      // THEN THE VALUE, taken the way a consumer takes it: with the exported
      // child as the receiver, not the prototype the descriptor was found on. A
      // throw makes the key an opaque leaf rather than an error in this walk.
      let edge;
      try {
        edge = parts.inheritedReceiver
          ? Reflect.get(value, key, held)
          : descriptor.get.call(value);
      } catch { continue; }
      const found = walk(edge, step, left - 1, undefined);
      if (found !== null) return found;
    }
    if (!parts.prototypes) return null;
    let proto;
    try { proto = Object.getPrototypeOf(value); } catch { return null; }
    // The receiver travels UP the prototype chain unchanged: the object a
    // consumer holds is the child, however far up the property lives.
    return walk(proto, `${path}.[[Prototype]]`, left - 1, held);
  };
  return walk(root, "", parts.bounded, undefined);
}

export const pathToValue = closedCallable((root, target, parts = WHOLE_WALK) =>
  traverse(root, parts, (value, path) =>
    (value === target ? (path === "" ? "<the namespace itself>" : path) : undefined)) ?? null);

/**
 * EVERY CALLABLE REACHABLE FROM A NAMESPACE, as `[route, callable]` pairs — the
 * enumeration half of the same walk.
 *
 * WHY IT EXISTS (amendment 9's fifth correction round, 2026-09-14). The producer
 * suite enumerated identity.js's surface by hand: `Object.entries`, then one
 * level into each object member, functions only. The review mutated a nested
 * export in — `export const api = { deep: { leak } }` — and the enumeration
 * walked straight past it while the assertion beside it claimed to cover "every
 * callable on the module". It now walks with the same traversal the reachability
 * guard uses: symbols, prototypes, accessors, accessor functions and inherited
 * getters under the exported child as receiver.
 *
 * INTRINSICS ARE NOT EXPORTS, and they are excluded by MEASUREMENT rather than
 * by a name list: the same walk is run first over a bare object, a bare
 * function, a frozen object and an array, and every callable it finds there —
 * `bind`, `toString`, every `Object.prototype` member — is a callable the
 * language supplies to anything, not one this module published. What is left is
 * the module's own surface at every depth.
 */
const INTRINSIC_CALLABLES = (() => {
  const found = new Set();
  for (const root of [{}, () => {}, Object.freeze({}), [], new Map()])
    traverse(root, WHOLE_WALK, (value) => {
      if (typeof value === "function") found.add(value);
      return undefined;
    });
  return found;
})();

export const reachableCallables = closedCallable((root, parts = WHOLE_WALK) => {
  const found = [];
  traverse(root, parts, (value, path) => {
    if (typeof value === "function" && path !== "" && !INTRINSIC_CALLABLES.has(value))
      found.push([path, value]);
    return undefined;
  });
  return found;
});

/** The identity check the fourth correction shipped, kept so the self-test can measure it. */
export const topLevelIdentityOnly = closedCallable((namespace, target) => {
  for (const [exportedAs, value] of Object.entries(namespace))
    if (value === target) return exportedAs;
  return null;
});
