import { readMigration } from "./migration-source.testhelper.mjs";
import test from "node:test";
import assert from "node:assert/strict";

import { fileURLToPath } from "node:url";

const MIGRATION = fileURLToPath(new URL("../../migrations/0293_lead_board_read_model.sql", import.meta.url));

test("Lead Board migration installs safe reader views and grants", async () => {
  const sql = await readMigration(MIGRATION);

  assert.match(sql, /create\s+(?:or\s+replace\s+)?view\s+v_lead_board\s+as/i);
  assert.match(sql, /l\.version\s+as\s+base_version/i);
  assert.match(sql, /left\s+join\s+actor\s+owner/i);
  assert.match(sql, /join\s+lead_stage\s+ls/i);
  assert.match(sql, /create\s+(?:or\s+replace\s+)?view\s+v_lead_board_stage\s+as/i);
  assert.match(sql, /grant\s+select\s+on\s+v_lead_board\s*,\s*v_lead_board_stage\s+to\s+carr_reader/i);
  assert.doesNotMatch(sql, /\bp\.(?:phone|cell|email|address)\b/i);
  assert.doesNotMatch(sql, /\bl\.(?:notes|notes_path|source_detail)\b/i);
  assert.doesNotMatch(sql, /where\s+(?:not\s+)?l\.suppressed/i,
    "suppressed leads are represented explicitly, never hidden by the view");
  assert.match(sql, /lead_do_not_contact_suppressed/i);
  assert.match(sql, /check\s*\(stage\s*<>\s*'do_not_contact'\s+or\s+suppressed\)/i);
});

test("Lead Board migration gives the existing funnel a deterministic order", async () => {
  const sql = await readMigration(MIGRATION);
  for (const slug of ["new", "qualified", "outreach_active", "engaged", "nurture_drip",
    "opportunity", "active_deal", "closed_won", "closed_lost", "do_not_contact"]) {
    assert.match(sql, new RegExp(`\\('${slug}',\\s*'[^']+',\\s*\\d+\\)`));
  }
});
