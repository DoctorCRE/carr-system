import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { TOOLS } from "../src/tools.js";

const file = path => readFile(new URL(`../../${path}`, import.meta.url), "utf8");

test("release 1–5 verbs are registered with human gates on structural creation", () => {
  for (const name of ["start-deal-review","review-deal","end-deal-review",
    "set-market-agent","set-national-account-owner","create-national-account",
    "create-national-market-deal","revert-deal-field"]) {
    assert.ok(TOOLS[name], `${name} must be registered`);
  }
  // humanOnly LABEL RETIRED (WR-000019 slice S1, 2026-08-27): dead since
  // executeRegisteredTool stopped reading it 2026-08-26 (decision dc57f62d);
  // this slice drops the stale declaration from tools.js. Both verbs remain
  // registered structural-creation writes.
  assert.equal(TOOLS["create-national-account"].humanOnly, undefined);
  assert.equal(TOOLS["create-national-market-deal"].humanOnly, undefined);
});

// The migration names the account it assigns; this public test must not
// (WR-000049), and must not pin it by a plain digest of the name either: an
// unkeyed hash of a name is confirmable by anyone with a name dictionary. It
// pins the whole applied migration file instead, which fixes the literal
// without deriving anything from the name alone.
const MIGRATION_0090_SHA256 = "6599fdf05e4e140bb0ea354993c1904d73a5dc0dc2f2bb197d32feb1c60df17d";

test("national-account migration keeps the 0061 hierarchy and explicitly assigns the national account", async () => {
  const sql = await file("migrations/0090_deal_room_workspaces.sql");
  assert.match(sql, /left join v_client_account vca/);
  const assigned = sql.match(/lower\(p\.name\) = '([^']+)'/);
  assert.ok(assigned, "the migration assigns an account by exact lowercased name");
  assert.equal(createHash("sha256").update(sql).digest("hex"), MIGRATION_0090_SHA256);
  assert.match(sql, /join actor a on a\.slug = 'dell'/);
  assert.match(sql, /create table deal_market_assignment/);
  assert.match(sql, /create table deal_review_session/);
  assert.match(sql, /case when vca\.account_client_id is null then 'team' else 'national_account'/);
  assert.doesNotMatch(sql, /update deal set client_id/i,
    "workspace migration must never reparent or duplicate an existing deal");
});
