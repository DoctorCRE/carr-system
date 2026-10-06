import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const migration = readFileSync(new URL("../../migrations/0571_tour_writer_login_membership.sql", import.meta.url), "utf8");
const body = migration.slice(migration.indexOf("create or replace function ops.tour_server_actor_id"), migration.indexOf("end $$;"));

test("tour actor gate admits writer-bundle logins by membership", () => {
  assert.match(body, /pg_has_role\(session_user,'carr_writer','member'\)/);
  assert.match(body, /pg_has_role\(session_user,'carr_authority','member'\)/);
  assert.match(body, /current_setting\('carr\.acting_actor_slug', true\)/);
});

test("authority logins keep the name-derived branch, checked first", () => {
  const authority = body.indexOf("session_user ~ '^carr_authority_'");
  const membership = body.indexOf("pg_has_role(session_user,'carr_writer'");
  assert.ok(authority > 0 && membership > authority);
});

test("other sessions are still refused and the actor slug stays bounded", () => {
  assert.match(body, /raise exception 'tour mutation requires an authority connection or sponsored writer session'/);
  assert.match(body, /\^\[A-Za-z0-9\._:-\]\{1,160\}\$/);
});

test("the migration changes no EXECUTE grant on any Tour mutation function", () => {
  assert.doesNotMatch(migration, /grant execute/i);
});
