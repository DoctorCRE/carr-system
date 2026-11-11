import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import test from "node:test";
import pg from "pg";
import { navigationFixture, unavailableFixture } from "./fixtures/tour-navigation-evidence.ts";
import { acquireDisposablePostgres } from "./helpers/disposable-postgres.ts";

test("app consumer fixture binds the route and reviewed entrance without a fallback coordinate", () => {
  assert.equal(navigationFixture.evidence.route_version, navigationFixture.evidence.promotion.route_version);
  assert.deepEqual(navigationFixture.evidence.stops[0]?.position, { latitude: 30.421, longitude: -87.216 });
  assert.equal(unavailableFixture.evidence, null);
});

test("Tour navigation uses sealed entrances and newest promotion through the authorized detail read", async () => {
  const fixture = await acquireDisposablePostgres({ prefix: "tour-navigation-", snapshot: true });
  const c = new pg.Client(fixture.connection);
  await c.connect();
  try {
    const sql = readFileSync(new URL("./tour-delivery-data-plane-postgres.sql", import.meta.url), "utf8");
    const statements = sql.replace(/^\\set ON_ERROR_STOP on\n/, "");
    await c.query("begin");
    try {
      const original = await c.query(
        "select pg_get_functiondef('ops.read_tour_internal_detail(text,uuid,text)'::regprocedure) definition",
      );
      const definition: string = original.rows[0].definition;
      const withoutNavigation = definition.replace(
        "'navigation',ops.tour_route_navigation_evidence(v.organization_tenant_id,v.id),", "",
      );
      assert.notEqual(withoutNavigation, definition);
      await c.query(withoutNavigation);
      await assert.rejects(c.query(statements), /navigation guessed evidence before promotion/);
    } finally {
      await c.query("rollback");
    }
    await c.query(statements);
    const result = await c.query(
      "select has_function_privilege('carr_writer','ops.tour_route_navigation_evidence(text,uuid)','execute') allowed",
    );
    assert.equal(result.rows[0].allowed, false);
    const unknown = await c.query("select ops.tour_route_navigation_evidence('unknown',$1::uuid) nav", [
      crypto.randomUUID(),
    ]);
    assert.deepEqual(unknown.rows[0].nav, {
      contract_version: "tour-navigation-evidence.v1",
      status: "unavailable",
      reason: "route_unknown",
      evidence: null,
    });
    execFileSync(process.execPath, ["--test", "mcp-server/test/tour-deal-access.test.ts"], {
      cwd: new URL("../../", import.meta.url),
      env: { ...process.env, CARR_TOUR_DEAL_TEST_DATABASE_URL: fixture.dsn },
      timeout: 60_000,
      stdio: "pipe",
    });
  } finally {
    await c.end();
    await fixture.close();
  }
});
