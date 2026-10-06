import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";

const JOE = {
  id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe",
  human: true, via: "oauth-google", client_id: "claude",
};

class Fake {
  constructor(plan = {}) { this.plan = plan; this.sql = []; }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    this.sql.push([sql, params]);
    for (const [match, rows] of Object.entries(this.plan))
      if (sql.includes(match)) return { rows: typeof rows === "function" ? rows(params) : rows };
    return { rows: [] };
  }
}

const EVENT = {
  id: "20000000-0000-0000-0000-000000000001",
  organization_tenant_id: "carr-internal", title: "ASCO Quality Care Symposium",
  organizer: "ASCO", kind: "conference", starts_at: "2026-11-05T09:00:00-05:00",
  ends_at: "2026-11-07T17:00:00-05:00", location: "Chicago, IL", is_virtual: false,
  url: "https://example.test/asco", relevance_note: "Hospital operator relationships",
  attendance_intent: "considering", owner_partner: "joe", status: "planned",
  source: "https://example.test/source", version: 1,
};

test("industry event verbs publish a tenant-scoped versioned contract", () => {
  assert.ok(TOOLS["add-industry-event"]);
  assert.ok(TOOLS["list-industry-events"]);
  assert.ok(TOOLS["update-industry-event"]);
  assert.equal(TOOLS["list-industry-events"].write, false);
  assert.equal(TOOLS["add-industry-event"].write, true);
  assert.equal(TOOLS["update-industry-event"].write, true);
  assert.deepEqual(TOOLS["add-industry-event"].inputSchema.required,
    ["idempotency_key", "title", "organizer", "kind", "starts_at", "ends_at",
      "owner_partner", "source"]);
  assert.ok(TOOLS["update-industry-event"].inputSchema.required.includes("base_version"));
  assert.ok(TOOLS["update-industry-event"].inputSchema.required.includes("idempotency_key"));
  assert.ok(TOOLS["list-industry-events"].inputSchema.properties.limit);
});

test("add-industry-event rejects a timestamp without an explicit timezone before SQL", async () => {
  const fake = new Fake();
  await assert.rejects(
    () => TOOLS["add-industry-event"].handler(fake, JOE, {
      idempotency_key: "event-add-invalid-zone", title: "A", organizer: "B",
      kind: "conference", starts_at: "2026-11-05T09:00:00",
      ends_at: "2026-11-05T10:00:00-05:00", owner_partner: "joe", source: "calendar",
    }),
    error => error instanceof ToolError && error.payload.error === "industry_event_timestamp_invalid",
  );
  assert.deepEqual(fake.sql, []);
});

test("add-industry-event enforces the table's title length before SQL", async () => {
  const fake = new Fake();
  await assert.rejects(
    () => TOOLS["add-industry-event"].handler(fake, JOE, {
      idempotency_key: "event-title-too-long", title: "x".repeat(501), organizer: "ASCO",
      kind: "conference", starts_at: EVENT.starts_at, ends_at: EVENT.ends_at,
      owner_partner: "joe", source: EVENT.source,
    }),
    error => error instanceof ToolError && error.payload.error === "industry_event_text_invalid"
      && error.payload.field === "title",
  );
  assert.deepEqual(fake.sql, []);
});

test("event schema permits clearing optional URL and relevance note", () => {
  for (const verb of ["add-industry-event", "update-industry-event"])
    for (const field of ["url", "relevance_note"])
      assert.deepEqual(TOOLS[verb].inputSchema.properties[field].type, ["string", "null"]);
});

test("list-industry-events scopes the query to the authenticated tenant and returns version", async () => {
  const fake = new Fake({ "from industry_event": [EVENT] });
  const result = await TOOLS["list-industry-events"].handler(fake, JOE, { limit: 20 });
  assert.deepEqual(result.events, [EVENT]);
  assert.equal(result.count, 1);
  const [sql, params] = fake.sql.find(([text]) => text.includes("from industry_event"));
  assert.match(sql, /organization_tenant_id=\$1/);
  assert.deepEqual(params, ["carr-internal", 20]);
});

test("add-industry-event writes the row and audit event through the envelope", async () => {
  const fake = new Fake({
    "from tool_call where idempotency_key": [],
    "insert into industry_event": [EVENT],
    "insert into event": [{ id: "30000000-0000-0000-0000-000000000001" }],
  });
  const result = await TOOLS["add-industry-event"].handler(fake, JOE, {
    idempotency_key: "event-add-1", title: EVENT.title, organizer: EVENT.organizer,
    kind: EVENT.kind, starts_at: EVENT.starts_at, ends_at: EVENT.ends_at,
    location: EVENT.location, is_virtual: EVENT.is_virtual, url: EVENT.url,
    relevance_note: EVENT.relevance_note, attendance_intent: EVENT.attendance_intent,
    owner_partner: EVENT.owner_partner, status: EVENT.status, source: EVENT.source,
  });
  assert.equal(result.ok, true);
  assert.equal(result.event, EVENT);
  const [sql, params] = fake.sql.find(([text]) => text.includes("insert into industry_event"));
  assert.match(sql, /organization_tenant_id/);
  assert.equal(params[0], "carr-internal");
});

test("update-industry-event uses base_version and tenant in its optimistic update", async () => {
  const updated = { ...EVENT, title: "ASCO Quality Care Symposium 2026", version: 2 };
  const fake = new Fake({
    "from tool_call where idempotency_key": [],
    "update industry_event": [updated],
    "insert into event": [{ id: "30000000-0000-0000-0000-000000000002" }],
  });
  const result = await TOOLS["update-industry-event"].handler(fake, JOE, {
    idempotency_key: "event-update-1", event_id: EVENT.id, base_version: 1,
    title: updated.title,
  });
  assert.equal(result.ok, true);
  assert.equal(result.event.version, 2);
  const [sql, params] = fake.sql.find(([text]) => text.includes("update industry_event"));
  assert.match(sql, /organization_tenant_id=\$\d+/);
  assert.match(sql, /version=\$\d+/);
  assert.deepEqual(params.slice(-3), [EVENT.id, "carr-internal", 1]);
});
