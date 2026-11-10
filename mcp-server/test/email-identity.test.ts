import assert from "node:assert/strict";
import test from "node:test";
import { capturedEmail, guardEmailAction, lockEmailIdentity } from "../src/email-identity.ts";
import { ToolError } from "../src/tool-error.ts";

test("email locks trim the item and preserve mailbox scope", async () => {
  const calls: { sql: string; params: unknown[] }[] = [];
  const c = {
    query: async (sql: string, params: unknown[]) => {
      calls.push({ sql, params });
      return { rows: [] };
    },
  };
  const identity = await lockEmailIdentity(c, "joe", "joe@example.test", " item ");
  assert.deepEqual(identity, { mailbox_owner: "joe", mailbox_account: "joe@example.test", item_id: "item" });
  assert.match(calls[0]?.sql ?? "", /pg_advisory_xact_lock/);
  assert.deepEqual(calls[0]?.params, [JSON.stringify(["email", "joe", "joe@example.test", "item"])]);
});

test("captured email scopes the lookup and returns the first receipt or undefined", async () => {
  const receipt = { id: "receipt", kind: "email", email_context: { send_id: "sent" } };
  const identity = { mailbox_owner: "dell", mailbox_account: "dell@example.test", item_id: "item" };
  const c = {
    query: async (sql: string, params: unknown[]) => {
      assert.match(sql, /order by .*lifecycle/);
      assert.deepEqual(params, ["dell", "dell@example.test", "item", "Subject"]);
      return { rows: [receipt] };
    },
  };
  assert.deepEqual(await capturedEmail(c, identity, "Subject"), receipt);
  assert.equal(await capturedEmail({ query: async () => ({ rows: [] }) }, identity), undefined);
});

test("email actions bypass reads without references and refuse every non-live lifecycle", async () => {
  await guardEmailAction({ query: async () => assert.fail("unexpected query") }, {}, ToolError);
  for (const state of ["live", "deleted", "superseded", ""]) {
    const c = {
      query: async (sql: string, params: unknown[]) => {
        assert.match(sql, /for update/);
        assert.deepEqual(params, [["activity", "thread", "source"], "proposal", "event"]);
        return { rows: [{ email_context: { lifecycle: { state } } }] };
      },
    };
    const pending = guardEmailAction(
      c,
      {
        activity_id: "activity",
        thread_activity_id: "thread",
        email_context: { source_activity_id: "source" },
        proposal_id: "proposal",
        event_id: "event",
      },
      ToolError,
    );
    if (state && state !== "live") await assert.rejects(pending, /email_not_live/);
    else await pending;
  }
});

test("unlocked email actions retain proposal lookup and nullable contexts", async () => {
  await guardEmailAction(
    {
      query: async (sql: string, params: unknown[]) => {
        assert.doesNotMatch(sql, /for update|v_email_thread_filing_event/);
        assert.deepEqual(params, [[], "proposal"]);
        return { rows: [{ email_context: null }, { email_context: {} }] };
      },
    },
    { proposal_id: "proposal" },
    ToolError,
    false,
  );
});
