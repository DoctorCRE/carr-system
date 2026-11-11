// Exercise isolated proposals through the session adapter and real PostgreSQL.
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import test from "node:test";
import pg from "pg";
import {
  type PortalProposalReceipt,
  type PortalProposalRequest,
  portalProposalStorage,
} from "../src/portal-proposals.ts";
import { createPortalWebHandler, PORTAL_ORIGIN, portalSqlOperations } from "../src/portal-web.ts";
import { postgresDatabase } from "./helpers/postgres-database.mjs";
import { readMigration } from "./migration-source.testhelper.ts";

test("proposal session, exact replay, CAS, membership and canonical isolation", async (t) => {
  const db = await postgresDatabase("portal-proposals-");
  const c = db.client;
  try {
    for (const n of ["0001_init.sql", "0008_client_merged_into.sql"]) await c.query(readMigration(n));
    await c.query(
      "create schema ops; create role carr_reader; create role carr_writer; create role carr_authority; create role dot_reader; create extension pgcrypto",
    );
    for (const n of [
      "0975_client_portal.sql",
      "0976_client_portal_binding.sql",
      "0981_client_portal_writer_denials.sql",
      "1013_register_client_portal.sql",
      "1158_deal_deliverables.sql",
      "1152_client_portal_sessions.sql",
      "1179_client_publications.sql",
    ])
      await c.query(readMigration(n));
    // Only identity columns used by this feature. The migration gate checks the full canonical schema.
    await c.query(
      "create table ops.tour_property(organization_tenant_id text,id uuid,primary key(organization_tenant_id,id))",
    );
    await c.query(readMigration("1183_portal_proposals.sql"));
    const actor = randomUUID();
    await c.query("insert into actor(id,slug,kind,display_name) values($1,'joe','human','Synthetic')", [actor]);
    await c.query(
      "insert into client_status(slug,label) values('active','Active'); insert into deal_phase(slug,label,sort) values('research','Research',1)",
    );
    const digest = createHash("sha256").update("synthetic session").digest("hex");
    const deals: string[] = [];
    const pubs: string[] = [];
    for (const slug of ["a", "b"]) {
      const party = (
        await c.query(
          "insert into party(kind,name,created_by,updated_by) values('person','Synthetic',$1,$1) returning id",
          [actor],
        )
      ).rows[0].id;
      const client = (
        await c.query(
          "insert into client(party_id,status,created_by,updated_by) values($1,'active',$2,$2) returning id",
          [party, actor],
        )
      ).rows[0].id;
      const deal = (
        await c.query(
          "insert into deal(client_id,name,deal_type,phase,created_by,updated_by) values($1,'Synthetic','lease','research',$2,$2) returning id",
          [client, actor],
        )
      ).rows[0].id;
      deals.push(deal);
      await c.query("select ops.register_client_portal($1,$2,$3)", [deal, slug, "test"]);
      const pub = slug === "a" ? "00000000-0000-4000-8000-000000000011" : "00000000-0000-4000-8000-000000000012";
      pubs.push(pub);
      await c.query("select ops.write_client_publication($1,$2,0,true,'tour','Options','Shared',null,null,'joe')", [
        deal,
        pub,
      ]);
    }
    const property = "00000000-0000-4000-8000-000000000021",
      other = "00000000-0000-4000-8000-000000000022";
    await c.query("insert into ops.tour_property values('carr-internal',$1),('carr-internal',$2)", [property, other]);
    const bind = async (pub = pubs[0], version = 1, ids = [property]) =>
      (await c.query("select ops.bind_portal_proposal_publication($1,$2,$3::uuid[]) result", [pub, version, ids]))
        .rows[0].result;
    await assert.rejects(bind(pubs[0], 1, [randomUUID()]), /publication_properties_invalid/);
    await bind();
    await bind(pubs[1], 1, [other]);
    await c.query("select ops.exchange_client_portal_session('a','test',$1)", [digest]);
    const store = portalProposalStorage(c);
    const scope = { slug: "a", digest, publication_id: String(pubs[0]) };
    const request: PortalProposalRequest = {
      version: 0,
      selected: [property],
      notes: { [property]: "Client note" },
      idempotency_key: "first",
    };
    const canonical = async () =>
      (
        await c.query(
          "select jsonb_build_object('deals',(select jsonb_agg(to_jsonb(d) order by d.id) from deal d),'clients',(select jsonb_agg(to_jsonb(c) order by c.id) from client c),'parties',(select jsonb_agg(to_jsonb(p) order by p.id) from party p)) value",
        )
      ).rows[0].value;
    const before = await canonical();
    let receipt: PortalProposalReceipt | undefined;
    await t.test("empty read, save, independent reload and frozen retry", async () => {
      assert.deepEqual((await store.read(scope)).selection, { version: 0, selected: [], notes: {} });
      receipt = await store.save(scope, 1, request);
      assert.equal((receipt as PortalProposalReceipt).selection.version, 1);
      assert.deepEqual(await store.save(scope, 1, request), receipt);
      assert.deepEqual((await store.read(scope)).selection, (receipt as PortalProposalReceipt).selection);
      await assert.rejects(
        store.save(scope, 1, { ...request, notes: { [property]: "changed" } }),
        /idempotency_conflict/,
      );
      await assert.rejects(store.save(scope, 1, { ...request, idempotency_key: "stale" }), /version_conflict/);
    });
    await t.test("forged scope, malformed input and current publication membership", async () => {
      await assert.rejects(
        store.read({ ...scope, slug: "b", publication_id: String(pubs[1]) }),
        /portal_access_denied/,
      );
      await assert.rejects(store.read({ ...scope, publication_id: String(pubs[1]) }), /publication_unavailable/);
      await assert.rejects(store.save(scope, 1, { ...request, deal_id: deals[1] }), /invalid_proposal/);
      await assert.rejects(store.save(scope, 1, null), /invalid_proposal/);
      await assert.rejects(
        store.save(scope, 1, { ...request, version: 10000, idempotency_key: "capacity" }),
        /proposal_capacity/,
      );
      for (const change of [
        { selected: [other] },
        { selected: [property, property] },
        { notes: { [other]: "secret" } },
        { notes: { [property]: "x".repeat(2001) } },
        { notes: { [property]: "😀".repeat(1001) } },
      ])
        await assert.rejects(
          store.save(scope, 1, { ...request, version: 1, idempotency_key: randomUUID(), ...change }),
        );
    });
    await t.test("newer writes retain original retry and historical partner read", async () => {
      await store.save(scope, 1, {
        ...request,
        version: 1,
        idempotency_key: "second",
        notes: { [property]: "New note" },
      });
      assert.deepEqual(await store.save(scope, 1, request), receipt);
      const partner = (await c.query("select ops.read_portal_proposal_for_deal($1,$2,1) result", [deals[0], pubs[0]]))
        .rows[0].result;
      assert.deepEqual(partner.selection, (receipt as PortalProposalReceipt).selection);
      assert.equal(
        (await c.query("select ops.read_portal_proposal_for_deal($1,$2,1) result", [deals[1], pubs[0]])).rows[0].result,
        null,
      );
    });
    await t.test("publication changes retire identities without rewriting their notes", async () => {
      await c.query("select ops.write_client_publication($1,$2,1,true,'tour','Options','New',null,null,'joe')", [
        deals[0],
        pubs[0],
      ]);
      await assert.rejects(
        store.save(scope, 1, { ...request, version: 2, idempotency_key: "old-source" }),
        /publication_unavailable/,
      );
      await bind(pubs[0], 2, [other]);
      assert.deepEqual(await store.save(scope, 1, request), receipt);
      const read = await store.read(scope);
      assert.deepEqual(read.retired, [property]);
      assert.equal(read.selection.notes[property], "New note");
      await assert.rejects(bind(pubs[0], 2, [property]), /publication_binding_conflict/);
      await store.save(scope, 2, {
        ...request,
        version: 2,
        idempotency_key: "retired",
        selected: [other],
        notes: { [other]: "Current" },
      });
      assert.deepEqual(
        (await c.query("select ops.read_portal_proposal_for_deal($1,$2,2) result", [deals[0], pubs[0]])).rows[0].result
          .selection.notes,
        { [property]: "New note" },
      );
    });
    await t.test("two tabs race on one version and reload from a new connection", async () => {
      const config = (
        await c.query(
          "select inet_server_addr() host, current_setting('port')::integer port, current_user as user, current_database() as database",
        )
      ).rows[0];
      const connection = new pg.Client(config);
      await connection.connect();
      try {
        const second = portalProposalStorage(connection);
        const input = { version: 3, selected: [other], notes: { [other]: "Race" } };
        const outcomes = await Promise.allSettled([
          store.save(scope, 2, { ...input, idempotency_key: "race-a" }),
          second.save(scope, 2, { ...input, idempotency_key: "race-b" }),
        ]);
        assert.equal(outcomes.filter((o) => o.status === "fulfilled").length, 1);
        assert.equal(outcomes.filter((o) => o.status === "rejected").length, 1);
        assert.deepEqual(await second.read(scope), await store.read(scope));
      } finally {
        await connection.end();
      }
    });
    await t.test("disabled, expired and reassigned portals refuse before replay", async () => {
      await c.query("update ops.client_publication set published=false where id=$1", [pubs[0]]);
      await assert.rejects(store.read(scope), /publication_unavailable/);
      await assert.rejects(store.save(scope, 1, request), /publication_unavailable/);
      await c.query("update ops.client_publication set published=true where id=$1", [pubs[0]]);
      await c.query("update ops.client_portal set disabled_at=now() where slug='a'");
      await assert.rejects(store.read(scope), /portal_access_denied/);
      await c.query("update ops.client_portal set disabled_at=null where slug='a'");
      await c.query(
        "update ops.client_portal_session set created_at=now()-interval '192 hours',expires_at=now()-interval '24 hours' where digest=$1",
        [digest],
      );
      await assert.rejects(store.save(scope, 1, request), /portal_access_denied/);
      await c.query(
        "update ops.client_portal_session set created_at=now(),expires_at=now()+interval '168 hours' where digest=$1",
        [digest],
      );
      await c.query("begin");
      try {
        await c.query("update deal set client_id=(select client_id from deal where id=$2) where id=$1", [
          deals[0],
          deals[1],
        ]);
        await assert.rejects(store.read(scope), /portal_access_denied/);
      } finally {
        await c.query("rollback");
      }
    });
    await t.test("session retirement refuses reads, saves and retained replay", async () => {
      await c.query("select ops.revoke_client_portal_session('a',$1)", [digest]);
      await assert.rejects(store.read(scope), /portal_access_denied/);
      await assert.rejects(store.save(scope, 1, request), /portal_access_denied/);
    });
    await t.test("canonical records and private storage permissions remain unchanged", async () => {
      assert.deepEqual(await canonical(), before);
      for (const role of ["carr_reader", "carr_writer", "dot_reader"])
        for (const table of ["portal_proposal_version", "portal_proposal_membership"])
          assert.equal(
            (
              await c.query("select has_table_privilege($1,$2,'INSERT,UPDATE,DELETE,SELECT') allowed", [
                role,
                `ops.${table}`,
              ])
            ).rows[0].allowed,
            false,
          );
    });
    await t.test("HTTP issue, proposal round trip, refusals and revoke through real SQL", async () => {
      const host = createPortalWebHandler({
        ...portalSqlOperations(async (fn) => {
          await c.query("begin");
          try {
            const result = await fn(c);
            await c.query("commit");
            return result;
          } catch (error) {
            await c.query("rollback");
            throw error;
          }
        }),
        deadlineMs: 10000,
      });
      const send = (path: string, method = "GET", cookie = "", body?: string, headers: Record<string, string> = {}) =>
        host.fetch(
          new Request(`${PORTAL_ORIGIN}/a/${path}`, {
            method,
            headers: { origin: PORTAL_ORIGIN, cookie, ...headers },
            ...(body === undefined ? {} : { body }),
          }),
        );
      const form = await send("");
      assert.equal(form.status, 200);
      const csrf = /name="portal-csrf" content="([a-f0-9]+)"/.exec(await form.text())?.[1];
      assert.ok(csrf);
      const anonymous = String(form.headers.get("set-cookie")).split(";")[0];
      const login = await send("login", "POST", anonymous, `password=test&csrf=${csrf}`, {
        "content-type": "application/x-www-form-urlencoded",
      });
      assert.equal(login.status, 303);
      const cookie = String(login.headers.get("set-cookie")).split(";")[0];
      const status = await (await send("status", "GET", cookie)).json();
      assert.equal(status.authenticated, true);
      const route = `proposals/${pubs[0]}`;
      const read = await send(route, "GET", cookie);
      assert.equal(read.status, 200);
      const current = await read.json();
      const body = JSON.stringify({
        publication_version: current.publication_version,
        request: {
          version: current.selection.version,
          selected: [other],
          notes: { [other]: "HTTP note" },
          idempotency_key: "http-save",
        },
      });
      const headers = { "content-type": "application/json", "x-portal-csrf": status.csrf };
      assert.equal((await send(route, "GET")).status, 401);
      assert.equal((await send(route, "POST", cookie, body, { "content-type": "application/json" })).status, 403);
      assert.equal((await send(route, "POST", cookie, body, { ...headers, origin: "https://evil.test" })).status, 403);
      const savedResponse = await send(route, "POST", cookie, body, headers);
      assert.equal(savedResponse.status, 200);
      const saved = await savedResponse.json();
      assert.equal(saved.selection.notes[other], "HTTP note");
      assert.deepEqual(await (await send(route, "GET", cookie)).json(), saved);
      assert.deepEqual(await (await send(route, "POST", cookie, body, headers)).json(), saved);
      const stale = JSON.parse(body);
      stale.request.idempotency_key = "stale-http";
      const conflict = await send(route, "POST", cookie, JSON.stringify(stale), headers);
      assert.equal(conflict.status, 409);
      assert.equal((await conflict.json()).error, "stale_version");
      const retired = JSON.parse(body);
      retired.request.version = saved.selection.version;
      retired.request.selected = [property];
      retired.request.notes = {};
      retired.request.idempotency_key = "retired-http";
      const refused = await send(route, "POST", cookie, JSON.stringify(retired), headers);
      assert.equal(refused.status, 409);
      assert.equal((await refused.json()).error, "retired");
      assert.equal((await send(`proposals/${pubs[1]}`, "GET", cookie)).status, 409);
      assert.equal(
        (
          await send("logout", "POST", cookie, `csrf=${status.csrf}`, {
            "content-type": "application/x-www-form-urlencoded",
          })
        ).status,
        303,
      );
      assert.equal((await send(route, "GET", cookie)).status, 401);
    });
    if (process.argv.includes("--record"))
      writeFileSync(
        new URL("../../workspace/contracts/fixtures/portal-proposals.v1.json", import.meta.url),
        `${JSON.stringify(receipt, null, 2)}\n`,
      );
    const fixture = JSON.parse(
      readFileSync(new URL("../../workspace/contracts/fixtures/portal-proposals.v1.json", import.meta.url), "utf8"),
    );
    assert.deepEqual(receipt, fixture);
  } finally {
    await db.close();
  }
});
