import assert from "node:assert/strict";
import test from "node:test";
import { setWriterActorContext } from "../src/mcp.js";

function client() {
  const calls = [];
  return {
    calls,
    async query(sql, params) { calls.push({ sql, params }); return { rows: [{}] }; },
  };
}

test("writer transactions set server-derived actor context before Tour mutations", async () => {
  const human = client();
  await setWriterActorContext(human, {
    slug: "joe", human: true,
  });
  assert.match(human.calls[0].sql, /carr\.acting_actor_slug/);
  assert.match(human.calls[0].sql, /carr\.verified_human_actor_slug/);
  assert.match(human.calls[0].sql, /carr\.organization_tenant_id/);
  assert.match(human.calls[0].sql, /carr\.execution_host_id/);
  assert.match(human.calls[0].sql, /carr\.sponsoring_human_slug/);
  // The fifth value is whose personal rows row security shows (migration
  // 0573): the same server-derived sponsor memory.js filters by.
  assert.deepEqual(human.calls[0].params, ["joe", "joe", "carr-internal", "", "joe"]);

  const sponsored = client();
  await setWriterActorContext(sponsored, {
    slug: "codex", human: false, authorization_class: "sponsored_agent",
    sponsoring_human_slug: "joe", native_agent_verified: true,
  });
  assert.deepEqual(sponsored.calls[0].params, ["codex", "", "carr-internal", "", "joe"]);

  const delegated = client();
  await setWriterActorContext(delegated, {
    slug: "codex", human: false, authorization_class: "sponsored_agent",
    sponsoring_human_slug: "joe", native_agent_verified: true,
  }, { partnerAuthorityAct: true });
  assert.deepEqual(delegated.calls[0].params, ["codex", "joe", "carr-internal", "", "joe"]);

  const unverified = client();
  await setWriterActorContext(unverified, {
    slug: "codex", human: false, authorization_class: "sponsored_agent",
    sponsoring_human_slug: "joe", native_agent_verified: false,
  }, { partnerAuthorityAct: true });
  // An unverified native agent still acts for its server-written grant
  // sponsor when reading, exactly as memory.js scopes it today; what it loses
  // is partner authority (the second value), not its sponsor's memories.
  assert.deepEqual(unverified.calls[0].params, ["codex", "", "carr-internal", "", "joe"]);

  // A token with no sponsor sees shared rows only.
  const machine = client();
  await setWriterActorContext(machine, { slug: "nightly", human: false, authorization_class: "machine" });
  assert.equal(machine.calls[0].params.at(-1), "");
});
