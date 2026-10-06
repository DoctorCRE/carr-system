import assert from "node:assert/strict";
import test from "node:test";

import {
  FOUNDATION_ASSURANCE_APPLICATION_NAME,
  FOUNDATION_ASSURANCE_WRITER_SECRET_NAME,
  foundationAssuranceSeatConnection,
} from "../src/foundation-assurance-seat-connection.v5.js";

test("Foundation Assurance connections identify their workload to PostgreSQL", async () => {
  let options;
  const queries = [];
  class Pool {
    constructor(value) { options = value; }
    async connect() {
      return {
        query: async (sql, params = []) => { queries.push([sql, params]); },
        release() {},
      };
    }
    async end() {}
  }

  const connect = foundationAssuranceSeatConnection({
    [FOUNDATION_ASSURANCE_WRITER_SECRET_NAME]: "postgresql://example.invalid/db",
  }, Pool);
  const value = await connect.call({ foundationAssuranceActorSlug: "codex-fa-minimum" },
    async () => "ok");

  assert.equal(value, "ok");
  assert.deepEqual(options, {
    connectionString: "postgresql://example.invalid/db",
    application_name: FOUNDATION_ASSURANCE_APPLICATION_NAME,
  });
  assert.deepEqual(queries, [
    ["begin", []],
    ["select set_config('carr.acting_actor_slug',$1::text,true)", ["codex-fa-minimum"]],
    ["commit", []],
  ]);
});

test("Foundation Assurance connections refuse a missing server actor context", async () => {
  class Pool { constructor() { throw new Error("must refuse before connecting"); } }
  const connect = foundationAssuranceSeatConnection({
    [FOUNDATION_ASSURANCE_WRITER_SECRET_NAME]: "postgresql://example.invalid/db",
  }, Pool);
  await assert.rejects(() => connect(async () => "no"), error =>
    error.code === "foundation_assurance_actor_context_unavailable");
});
