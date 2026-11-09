// Test canonical and short decision IDs, ambiguous references, and detach lookups.
import assert from "node:assert/strict";
import { test } from "node:test";
import { ToolError } from "../src/tool-error.ts";

const registered: unknown = await import(new URL("../src/tools.js", import.meta.url).href);
assert.ok(typeof registered === "object" && registered !== null);
assert.ok("TOOLS" in registered);
function record(value: unknown): asserts value is Record<string, unknown> {
  assert.ok(typeof value === "object" && value !== null && !Array.isArray(value));
}
const TOOLS = registered.TOOLS;
record(TOOLS);
assert.ok(typeof TOOLS === "object" && TOOLS !== null);

const keys: unknown = await import(new URL("../src/derived-key.ts", import.meta.url).href);
assert.ok(
  typeof keys === "object" &&
    keys !== null &&
    "derivedIdempotencyKey" in keys &&
    typeof keys.derivedIdempotencyKey === "function",
);
const derivedIdempotencyKey = keys.derivedIdempotencyKey;

const joe = {
  id: "10000000-0000-0000-0000-000000000002",
  slug: "joe",
  display: "Joe",
  human: true,
  via: "mcp",
  client_id: "claude",
};

const FULL = "f58ffba8-1d2c-4e6b-9a70-2b8c1d4e5f60";
const SHORT = "f58ffba8";
const OTHER = "f58ffbaa-9999-4e6b-9a70-2b8c1d4e5f60"; // shares the first 7 chars

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Reproduces the real driver behaviour: a non-uuid reaching a uuid column
// raises, exactly as Postgres does. Deliberately does NOT pin a column list —
// a fake that asserts the select's exact columns breaks on every widening and
// teaches nothing (the lesson of pull request 475).
class Fake {
  prefixMatches: string[];
  lookups: string[] = [];
  prefixQueries: unknown[] = [];
  writes: { sql: string; params: unknown[] }[] = [];
  constructor({ prefixMatches = [FULL] } = {}) {
    this.prefixMatches = prefixMatches;
    this.lookups = []; // every id that reached a uuid-typed comparison
    this.prefixQueries = [];
    this.writes = [];
  }
  async query(text: string, params: unknown[]) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) return { rows: [] };

    // The prefix resolver.
    if (sql.includes("subject_id::text like")) {
      this.prefixQueries.push(params[0]);
      return {
        rows: this.prefixMatches
          .filter((id) => {
            assert.ok(typeof params[0] === "string");
            return id.startsWith(params[0]);
          })
          .map((id) => ({ id, title: `ruling ${id.slice(0, 8)}`, recorded_at: "2026-08-22" })),
      };
    }

    // The uuid-typed read update-decision performs.
    if (sql.includes("where subject_id = $1") && sql.includes("'log-decision'")) {
      const id = params[0];
      assert.ok(typeof id === "string");
      this.lookups.push(id);
      if (!UUID.test(id)) throw new Error(`invalid input syntax for type uuid: "${id}"`);
      return {
        rows: [
          { id: "event-42", new_value: { title: "the original title" }, human_quote: null, agent_rationale: "because" },
        ],
      };
    }

    this.writes.push({ sql, params });
    if (sql.startsWith("insert into")) return { rows: [{ id: "event-new" }] };
    return { rows: [] };
  }
}

async function amend(decision_id: string, fake = new Fake()) {
  const args = {
    idempotency_key: derivedIdempotencyKey("test", `k-${decision_id}`),
    decision_id,
    title: "a corrected title",
    reason: "the title was wrong",
  };
  record(TOOLS);
  const tool = TOOLS["update-decision"];
  assert.ok(typeof tool === "object" && tool !== null && "handler" in tool && typeof tool.handler === "function");
  try {
    const result: unknown = await tool.handler(fake, joe, args);
    return { fake, result, error: null };
  } catch (e) {
    return {
      fake,
      result: null,
      error:
        e instanceof ToolError
          ? e.payload
          : (() => {
              throw e;
            })(),
    };
  }
}

test("the 8-character short form this system prints is accepted", async () => {
  const { result, error } = await amend(SHORT);
  assert.equal(error, null, "'f58ffba8' is the form loop source_notes and decision-history print");
  assert.ok(typeof result === "object" && result !== null && "decision_id" in result);
  assert.equal(
    result.decision_id,
    FULL,
    "the verb must answer with the canonical id it resolved, so the caller learns the full form",
  );
});

test("the short form never reaches the uuid column — that was the internal error", async () => {
  // Scoped to the uuid-typed lookups alone. Asserting merely that the call did
  // not throw would also pass if the resolver were skipped and the driver had
  // simply been lenient; this pins the actual mechanism.
  const { fake } = await amend(SHORT);
  assert.deepEqual(
    fake.lookups.filter((id) => !UUID.test(id)),
    [],
    "a short id must be resolved before the query, not passed into a uuid comparison",
  );
  assert.deepEqual(fake.prefixQueries, [SHORT], "and it must be resolved by prefix, exactly once");
});

test("the full uuid still works, and skips the resolver entirely", async () => {
  const { fake, error } = await amend(FULL);
  assert.equal(error, null, "the uuid form was never wrong, only insufficient");
  assert.deepEqual(
    fake.prefixQueries,
    [],
    "a full uuid must take the fast path — resolving it would be a needless round trip",
  );
  assert.ok(fake.lookups.includes(FULL));
});

test("an ambiguous prefix is refused with its candidates, never guessed", async () => {
  // Amending the wrong decision rewrites a settled ruling's rationale and leaves
  // no sign the entry was ever different. Guessing here is worse than failing.
  const fake = new Fake({ prefixMatches: [FULL, OTHER] });
  const { error } = await amend("f58ffba", fake);
  assert.equal(error?.error, "ambiguous_decision_id");
  assert.ok(error && Array.isArray(error.candidates));
  assert.deepEqual(
    error.candidates
      .map((c: unknown) => {
        assert.ok(typeof c === "object" && c !== null && "decision_id" in c);
        return c.decision_id;
      })
      .sort(),
    [FULL, OTHER].sort(),
    "the caller must be shown what it matched, not told to try again blind",
  );
  assert.deepEqual(fake.lookups, [], "nothing may be read, and nothing written, on an ambiguous ref");
  assert.deepEqual(fake.writes, []);
});

test("a prefix matching nothing says so, rather than reporting a missing decision", async () => {
  const fake = new Fake({ prefixMatches: [] });
  const { error } = await amend("deadbeef", fake);
  assert.equal(error?.error, "decision_not_found");
  assert.ok(error);
  assert.equal(error.field, "decision_id");
});

test("a ref that is neither a uuid nor hex is refused before any SQL", async () => {
  for (const bad of ["C-127", "the migration one", "#213"]) {
    const fake = new Fake();
    const { error } = await amend(bad, fake);
    assert.equal(error?.error, "decision_id_malformed", `${bad} must be named, not sent to the driver`);
    assert.deepEqual(fake.prefixQueries, [], `${bad} must not reach SQL at all`);
    assert.deepEqual(fake.lookups, []);
  }
});

// ---------- detach-decision: the same field, the quieter symptom ----------

class DetachFake extends Fake {
  pointerLookups: unknown[] = [];
  async query(text: string, params: unknown[]) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.includes("new_value->>'decision_id' = $3")) {
      this.pointerLookups = this.pointerLookups || [];
      this.pointerLookups.push(params[2]);
      return { rows: params[2] === FULL ? [{ id: "event-ptr", new_value: { summary: "decided: a thing" } }] : [] };
    }
    if (sql.includes("subject_id::text like")) return super.query(text, params);
    if (sql.startsWith("select request_hash, response")) return { rows: [] };
    this.writes.push({ sql, params });
    return { rows: [{ id: "C-127", type: "client", ref: "C-127" }] };
  }
}

test("detach-decision resolves the short form too, instead of reporting not_attached", async () => {
  const fake = new DetachFake();
  let error: unknown = null;
  try {
    const tool = TOOLS["detach-decision"];
    assert.ok(typeof tool === "object" && tool !== null && "handler" in tool && typeof tool.handler === "function");
    await tool.handler(fake, joe, {
      idempotency_key: "00000571-0000-4000-8000-000000000118",
      decision_id: SHORT,
      from: "C-127",
      reason: "this ruling is not about that client",
    });
  } catch (e) {
    error = e;
  }

  assert.notEqual(
    error instanceof ToolError ? error.payload.error : undefined,
    "not_attached",
    "a short id matched nothing against a TEXT column and read as 'no such pointer'",
  );
  assert.deepEqual(
    fake.pointerLookups,
    [FULL],
    "the pointer must be looked up by the resolved uuid, which is what it stores",
  );
});

class MissingPointerFake extends DetachFake {
  async query(text: string, params: unknown[]) {
    if (text.includes("new_value->>'decision_id' = $3")) {
      this.pointerLookups = [params[2]];
      return { rows: [] };
    }
    return super.query(text, params);
  }
}

test("detach-decision reports not_attached when the resolved decision has no pointer", async () => {
  const fake = new MissingPointerFake();
  const tool = TOOLS["detach-decision"];
  assert.ok(typeof tool === "object" && tool !== null && "handler" in tool && typeof tool.handler === "function");
  await assert.rejects(
    tool.handler(fake, joe, {
      idempotency_key: "00000571-0000-4000-8000-000000000119",
      decision_id: SHORT,
      from: "C-127",
      reason: "this ruling has no pointer on that client",
    }),
    (error) => error instanceof ToolError && error.payload.error === "not_attached",
  );
  assert.deepEqual(fake.pointerLookups, [FULL]);
});
