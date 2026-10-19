import test from "node:test";
import assert from "node:assert/strict";
import { prepareStagingLoopFixtures } from "../src/staging-loop-fixtures.js";

const env = { CARR_ENV: "staging", APP_HOST: "carr-mcp-staging.joe-bookout-carr-us.workers.dev",
  DOCTORCRE_APP_HOST: "doctorcre-app-staging.joe-bookout-carr-us.workers.dev", DATABASE_URL_WRITER: "synthetic" };

test("fixture preparation refuses production before opening a connection", async () => {
  for (const changed of [{ CARR_ENV: "production" }, { APP_HOST: "app.doctorcre.com" },
    { DOCTORCRE_APP_HOST: "app.doctorcre.com" }]) {
    let opened = false;
    await assert.rejects(prepareStagingLoopFixtures({ ...env, ...changed }, () => { opened = true; }));
    assert.equal(opened, false);
  }
});

test("a failed fixture transaction rolls back and releases its database connection", async () => {
  const calls = [];
  const client = { query: async sql => {
    calls.push(sql);
    if (sql.includes("from public.actor")) throw new Error("synthetic failure");
    return { rows: [] };
  }, release: () => calls.push("release") };
  await assert.rejects(prepareStagingLoopFixtures(env, () => ({
    connect: async () => client, end: async () => calls.push("end"),
  })), /synthetic failure/);
  assert.deepEqual(calls.slice(-3), ["rollback", "release", "end"]);
  assert.equal(calls.includes("commit"), false);
});

test("a refused database connection still closes its pool", async () => {
  let ended = false;
  await assert.rejects(prepareStagingLoopFixtures(env, () => ({
    connect: async () => { throw new Error("synthetic refusal"); }, end: async () => { ended = true; },
  })), /synthetic refusal/);
  assert.equal(ended, true);
});
