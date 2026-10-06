import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const sql = fs.readFileSync(new URL("../../migrations/0480_codex_continuity.sql", import.meta.url), "utf8");

test("continuity migration is bounded, scoped and append-only", () => {
  assert.match(sql, /create table codex_continuity_checkpoint/);
  assert.match(sql, /unique \(organization_tenant_id, owner_actor_id, native_task_id\)/);
  assert.match(sql, /octet_length\(state::text\)<=24000/);
  assert.match(sql, /codex_continuity_revision_append_only/);
  assert.match(sql, /unique \(organization_tenant_id, owner_actor_id, native_task_id, idempotency_key\)/);
  assert.doesNotMatch(sql, /transcript_body|transcript_text|raw_transcript/);
});
