// Exercise isolated proposals through the session adapter and real PostgreSQL.
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import test from "node:test";
import { Validator } from "@cfworker/json-schema";
import pg from "pg";
import {
  parseProposalReview,
  type portalProposalAcceptance,
  proposalDecisionSchema,
  proposalReviewSchema,
} from "../src/portal-proposal-acceptance.ts";

type Api = ReturnType<typeof portalProposalAcceptance>;
const registered: unknown = await import(new URL("../src/tools.js", import.meta.url).href);
const { TOOLS } = registered as {
  TOOLS: {
    "read-portal-proposal-review-v1": { handler: Api["read"]; inputSchema: unknown };
    "accept-portal-proposal-v1": { handler: Api["accept"]; inputSchema: unknown };
  };
};

import {
  type PortalProposalReceipt,
  type PortalProposalRequest,
  portalProposalStorage,
} from "../src/portal-proposals.ts";
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
    const extract = (file: string, name: string) => {
      const body = readMigration(file);
      const start = body.lastIndexOf("create or replace function ops." + name + "(");
      return body.slice(start, body.indexOf("end $$;", start) + 7);
    };
    await c.query(
      "create table ops.tour(organization_tenant_id text,id uuid,deal_id uuid,primary key(organization_tenant_id,id))",
    );
    const cartMigration = readMigration("0430_tour_delivery_data_plane.sql");
    const cartStart = cartMigration.indexOf("create table if not exists ops.tour_selection_cart_version");
    await c.query(cartMigration.slice(cartStart, cartMigration.indexOf(";", cartStart) + 1));
    await c.query(extract("0429_tour_domain_route_cheat_sheet.sql", "tour_server_actor_id"));
    await c.query(extract("0430_tour_delivery_data_plane.sql", "append_tour_selection_cart_version"));
    await c.query("alter table public.deal add column owner text");
    const access = readMigration("1142_tour_deal_access.sql");
    await c.query(access.slice(access.indexOf("create function ops.tour_deal_visible"), access.indexOf("$$;") + 3));
    await assert.rejects(c.query("select ops.review_portal_proposal(null,null,null,null)"), { code: "42883" });
    await c.query(
      "create table ops.tour_route_version(id uuid,organization_tenant_id text,tour_id uuid);create table ops.tour_public_projection(id uuid,organization_tenant_id text,tour_id uuid);create table ops.tour_pdf_render_job(id uuid,organization_tenant_id text,projection_id uuid);create table ops.tour_share_grant(id uuid,organization_tenant_id text,projection_id uuid)",
    );
    const targetStart = access.indexOf("create function ops.tour_target_visible");
    await c.query(access.slice(targetStart, access.indexOf("$$;", targetStart) + 3));
    await c.query(
      access.slice(
        access.indexOf("do $guard_internal$"),
        access.indexOf(" select pg_get_functiondef('ops.list_tour_library"),
      ) + "end $guard_internal$;",
    );
    await c.query(readMigration("1184_portal_proposal_acceptance.sql"));
    for (const table of ["event", "tool_call"]) {
      for (const column of [
        "via",
        "client_id",
        "organization_tenant_id",
        "sponsoring_human_slug",
        "personal_scope",
        "authorization_class",
        "correlation_id",
      ])
        await c.query(`alter table public.${table} add column if not exists ${column} text`);
    }
    await c.query(
      "alter role carr_writer login; grant usage on schema ops,public to carr_writer; grant select on public.deal,public.actor,public.event,public.tool_call to carr_writer; grant insert on public.event,public.tool_call to carr_writer; grant usage,select on all sequences in schema public to carr_writer",
    );
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
          "insert into deal(id,client_id,name,deal_type,phase,created_by,updated_by) values($3,$1,'Synthetic','lease','research',$2,$2) returning id",
          [
            client,
            actor,
            slug === "a" ? "00000000-0000-4000-8000-000000000041" : "00000000-0000-4000-8000-000000000042",
          ],
        )
      ).rows[0].id;
      deals.push(deal);
      await c.query("select ops.register_client_portal($1,$2,$3)", [deal, slug, "test"]);
      const pub = slug === "a" ? "00000000-0000-4000-8000-000000000011" : "bbbbbbbb-0000-4000-8000-000000000012";
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
    await t.test("partner read cannot fence a client source update", async () => {
      const config = (
        await c.query(
          "select inet_server_addr() host, current_setting('port')::integer port, current_user as user, current_database() as database",
        )
      ).rows[0];
      const partner = new pg.Client(config);
      await partner.connect();
      try {
        await partner.query("set role carr_writer");
        await partner.query("begin");
        await partner.query("set local statement_timeout='5s'");
        const read = async () =>
          (await partner.query("select ops.read_portal_proposal_for_deal($1,$2,null) result", [deals[0], pubs[0]]))
            .rows[0].result;
        assert.equal((await read()).selection.version, 4);
        await c.query("set statement_timeout='5s'");
        await store.save(scope, 2, {
          version: 4,
          selected: [other],
          notes: { [other]: "changed during partner transaction" },
          idempotency_key: "during-partner",
        });
        assert.equal((await read()).selection.version, 5);
        await partner.query("rollback");
        for (const table of ["client_portal", "portal_proposal_version", "portal_proposal_membership"]) {
          await assert.rejects(partner.query("select * from ops." + table + " for update"), { code: "42501" });
        }
      } finally {
        await partner.query("rollback");
        await partner.end();
      }
    });
    await t.test("guarded acceptance, uncertain replay, second-tab conflict and notification dedup", async (steps) => {
      const tour = "00000000-0000-4000-8000-000000000031";
      await c.query("insert into ops.tour values('carr-internal',$1,$2)", [tour, deals[0]]);
      const config = (
        await c.query(
          "select inet_server_addr() host,current_setting('port')::integer port,current_database() as database",
        )
      ).rows[0];
      const partner = new pg.Client({ ...config, user: "carr_writer" });
      await partner.connect();
      const human = {
        id: actor,
        slug: "joe",
        human: true,
        organization_tenant_id: "carr-internal",
        sponsoring_human_slug: "joe",
        authorization_class: "human",
      };
      const target = { deal_id: deals[0], publication_id: pubs[0], tour_id: tour };
      const read = () => TOOLS["read-portal-proposal-review-v1"].handler(partner, human, target);
      const accept = (input: unknown) => TOOLS["accept-portal-proposal-v1"].handler(partner, human, input);
      const begin = async (repeatable = false, connection = partner) => {
        await connection.query(repeatable ? "begin isolation level repeatable read" : "begin");
        await connection.query(
          "select set_config('carr.acting_actor_slug','joe',true),set_config('carr.verified_human_actor_slug','joe',true),set_config('carr.organization_tenant_id','carr-internal',true)",
        );
      };
      const requestFor = (
        r: ReturnType<typeof parseProposalReview>,
        decision = "accept",
        property_ids = r.selected,
      ) => ({
        ...target,
        publication_version: r.publication_version,
        source_version: r.source_version,
        cart_version: r.cart_version,
        cart_version_id: r.cart_version_id,
        decision,
        property_ids,
        idempotency_key: randomUUID(),
      });
      const cartCount = async () =>
        (await c.query("select count(*)::integer n from ops.tour_selection_cart_version where tour_id=$1", [tour]))
          .rows[0].n;
      const waitOnPortal = async (
        request: unknown,
        mutate: () => Promise<unknown>,
        expected = /proposal_source_changed/,
      ) => {
        await c.query("begin");
        await c.query("select 1 from ops.client_portal where slug='a' for update");
        await begin();
        const pid = (await partner.query("select pg_backend_pid() pid")).rows[0].pid;
        const outcome = accept(request).then(
          (value) => ({ value }),
          (error) => ({ error }),
        );
        try {
          let blocked = false;
          for (let attempt = 0; attempt < 200; attempt++) {
            blocked = (await c.query("select cardinality(pg_blocking_pids($1))>0 blocked", [pid])).rows[0].blocked;
            if (blocked) break;
            await new Promise((resolve) => setTimeout(resolve, 10));
          }
          assert.equal(blocked, true, "acceptance reached the canonical portal lock");
          await mutate();
          await c.query("commit");
          const result = await outcome;
          assert.ok("error" in result, "a refused operation cannot report an accepted receipt");
          assert.match(String(result.error), expected);
        } finally {
          await c.query("rollback");
          await outcome;
          await partner.query("rollback");
        }
      };
      try {
        await begin();
        const pending = await read();
        assert.equal(pending.review.source_version, 5);
        assert.deepEqual(pending.review.added, [other]);
        assert.equal(pending.review.notes[other], "changed during partner transaction");
        assert.deepEqual((await read()).pending, pending.pending);
        if (process.argv.includes("--record"))
          writeFileSync(
            new URL("../../workspace/contracts/fixtures/portal-proposal-acceptance.v1.json", import.meta.url),
            JSON.stringify(pending, null, 2) + "\n",
          );
        assert.deepEqual(
          pending,
          JSON.parse(
            readFileSync(
              new URL("../../workspace/contracts/fixtures/portal-proposal-acceptance.v1.json", import.meta.url),
              "utf8",
            ),
          ),
        );
        const old = requestFor(pending.review);
        await partner.query("commit");
        await steps.test("repeatable snapshots cannot accept a newer client save", async () => {
          await begin(true);
          try {
            await store.save(scope, 2, {
              version: 5,
              selected: [other],
              notes: { [other]: "new source" },
              idempotency_key: "source-six",
            });
            await assert.rejects(accept(old), /proposal_isolation_unsupported/);
          } finally {
            await partner.query("rollback");
          }
        });
        await begin();
        await assert.rejects(accept(old), /proposal_source_changed/);
        await partner.query("rollback");
        await c.query(
          "alter table ops.tour_selection_cart_version alter column id set default '00000000-0000-4000-8000-000000000032'::uuid",
        );
        await begin();
        const request = requestFor((await read()).review);
        const receipt = await accept(request);
        assert.equal(receipt.state, "accepted");
        assert.equal(receipt.source_version, 6);
        const receiptPath = new URL(
          "../../workspace/contracts/fixtures/portal-proposal-acceptance-receipt.v1.json",
          import.meta.url,
        );
        if (process.argv.includes("--record")) writeFileSync(receiptPath, JSON.stringify(receipt, null, 2) + "\n");
        assert.deepEqual(receipt, JSON.parse(readFileSync(receiptPath, "utf8")));
        await c.query("set statement_timeout='200ms'");
        await assert.rejects(
          store.save(scope, 2, { version: 6, selected: [other], notes: {}, idempotency_key: "blocked-save" }),
          { code: "57014" },
        );
        await c.query("set statement_timeout='5s'");
        await partner.query("commit");
        await begin();
        assert.deepEqual(
          (
            await c.query("select property_ids from ops.tour_selection_cart_version where id=$1", [
              receipt.selection_version_id,
            ])
          ).rows[0].property_ids,
          request.property_ids,
        );
        await c.query("alter table ops.tour_selection_cart_version alter column id set default gen_random_uuid()");
        await assert.rejects(accept({ ...request, idempotency_key: randomUUID() }), /proposal_source_changed/);
        await partner.query("rollback");
        await store.save(scope, 2, { version: 6, selected: [other], notes: {}, idempotency_key: "source-seven" });
        await begin();
        const replay = await accept(request);
        assert.equal("replayed" in replay && replay.replayed, true);
        assert.deepEqual({ ...replay, replayed: undefined }, { ...receipt, replayed: undefined });
        await partner.query("commit");
        assert.equal(await cartCount(), 1);
        await begin();
        await assert.rejects(accept({ ...request, property_ids: [] }), /key_reuse/);
        await partner.query("rollback");
        await c.query("update ops.client_publication set version=3 where id=$1", [pubs[0]]);
        await bind(pubs[0], 3, [property]);
        await steps.test("Keep mine preserves the cart despite retired client identities", async () => {
          try {
            await begin();
            const retired = (await read()).review;
            assert.deepEqual(retired.retired, [other]);
            await assert.rejects(accept(requestFor(retired)), /proposal_input_invalid/);
            await partner.query("rollback");
            await begin();
            const keep = requestFor((await read()).review, "keep", retired.mine);
            const kept = await accept(keep);
            assert.equal(kept.state, "kept");
            const resolved = await read();
            assert.equal(resolved.pending, null);
            assert.deepEqual(resolved.decision, kept);
            await partner.query("commit");
            await begin();
            assert.equal("replayed" in (await accept(keep)), true);
            await partner.query("commit");
            await begin();
            await assert.rejects(accept(requestFor(retired, "accept", [])), /proposal_already_reviewed/);
          } finally {
            await partner.query("rollback");
          }
        });
        assert.equal(await cartCount(), 1);
        await store.save(scope, 3, {
          version: 7,
          selected: [property],
          notes: { [property]: "New option" },
          idempotency_key: "source-eight",
        });
        await begin();
        const mixed = await read();
        assert.deepEqual(mixed.review.added, [property]);
        assert.deepEqual(mixed.review.removed, [other]);
        assert.notEqual(mixed.pending?.key, pending.pending?.key);
        const choices = await accept(requestFor(mixed.review, "accept", [property, other]));
        assert.deepEqual(choices.property_ids, [property, other]);
        await partner.query("commit");
        await store.save(scope, 3, { version: 8, selected: [], notes: {}, idempotency_key: "source-nine" });
        await begin();
        const removal = (await read()).review;
        assert.deepEqual(removal.removed, [property, other]);
        const removed = await accept(requestFor(removal, "accept", []));
        await partner.query("commit");
        assert.deepEqual(
          (
            await c.query("select property_ids from ops.tour_selection_cart_version where id=$1", [
              removed.selection_version_id,
            ])
          ).rows[0].property_ids,
          [],
        );
        assert.equal(await cartCount(), 3);
        const event = (
          await c.query("select new_value from event where verb='accept-portal-proposal-v1' and idempotency_key=$1", [
            request.idempotency_key,
          ])
        ).rows[0].new_value;
        assert.equal(event.source_version, 6);
        assert.equal(event.selection_version_id, receipt.selection_version_id);
        await store.save(scope, 3, { version: 9, selected: [property], notes: {}, idempotency_key: "source-ten" });
        await begin();
        const current = requestFor((await read()).review);
        await partner.query("commit");
        await steps.test("revoked authority while review waits cannot report acceptance", async () => {
          try {
            await waitOnPortal(
              current,
              () => c.query("update public.actor set active=false where id=$1", [actor]),
              /proposal_access_denied/,
            );
          } finally {
            await c.query("update public.actor set active=true where id=$1", [actor]);
          }
        });
        await steps.test("a refused business append cannot produce a success receipt", async () => {
          await c.query("create role carr_authority_dell login in role carr_writer");
          const mismatch = new pg.Client({ ...config, user: "carr_authority_dell" });
          await mismatch.connect();
          try {
            await begin(false, mismatch);
            await assert.rejects(
              TOOLS["accept-portal-proposal-v1"].handler(mismatch, human, current),
              /proposal_source_changed/,
            );
          } finally {
            await mismatch.query("rollback");
            await mismatch.end();
          }
        });
        assert.equal(await cartCount(), 3);
        assert.equal(
          (
            await c.query("select count(*)::integer n from tool_call where idempotency_key=$1", [
              current.idempotency_key,
            ])
          ).rows[0].n,
          0,
        );
        await steps.test("a client save committed while acceptance waits requires re-review", async () => {
          await waitOnPortal(current, () =>
            store.save(scope, 3, { version: 10, selected: [], notes: {}, idempotency_key: "source-eleven" }),
          );
        });
        assert.equal(await cartCount(), 3);
        await begin();
        await assert.rejects(
          TOOLS["read-portal-proposal-review-v1"].handler(partner, { ...human, human: false }, target),
          /human_only/,
        );
        await assert.rejects(
          TOOLS["read-portal-proposal-review-v1"].handler(partner, human, { ...target, deal_id: deals[1] }),
          /proposal_retired/,
        );
        await partner.query("rollback");
        await begin();
        await assert.rejects(
          accept({ ...requestFor((await read()).review), property_ids: [randomUUID()] }),
          /proposal_input_invalid/,
        );
        await partner.query("rollback");
        await c.query("update ops.client_publication set published=false where id=$1", [pubs[0]]);
        await begin();
        await assert.rejects(accept(request), /proposal_retired/);
        await partner.query("rollback");
        await c.query("update ops.client_publication set published=true where id=$1", [pubs[0]]);
        await begin();
        const afterRevision = await accept(request);
        assert.deepEqual({ ...afterRevision, replayed: undefined }, { ...receipt, replayed: undefined });
        await partner.query("commit");
        await steps.test("the original receipt survives expiry and removal of tool_call replay rows", async () => {
          try {
            await c.query("update tool_call set created_at=now()-interval '31 days' where idempotency_key=$1", [
              request.idempotency_key,
            ]);
            await begin();
            const expired = await accept(request);
            assert.deepEqual({ ...expired, replayed: undefined }, { ...receipt, replayed: undefined });
            await partner.query("commit");
            await c.query("delete from tool_call where idempotency_key=$1", [request.idempotency_key]);
            await begin();
            const removedLedger = await accept(request);
            assert.deepEqual({ ...removedLedger, replayed: undefined }, { ...receipt, replayed: undefined });
            assert.equal("replayed" in removedLedger && removedLedger.replayed, true);
            await partner.query("commit");
            assert.equal(await cartCount(), 3);
            await begin();
            await assert.rejects(accept({ ...request, property_ids: [] }), /key_reuse/);
            await partner.query("rollback");
          } finally {
            await partner.query("rollback");
          }
        });
      } finally {
        await partner.query("rollback");
        await partner.end();
      }
    });
    await t.test("partner receipts preserve canonical identities for uppercase scope inputs", async () => {
      const tour = "bbbbbbbb-0000-4000-8000-000000000031";
      await c.query("insert into ops.tour values('carr-internal',$1,$2)", [tour, deals[1]]);
      const digestB = createHash("sha256").update("synthetic B session").digest("hex");
      await c.query("select ops.exchange_client_portal_session('b','test',$1)", [digestB]);
      await store.save({ slug: "b", digest: digestB, publication_id: pubs[1] }, 1, {
        version: 0,
        selected: [other],
        notes: {},
        idempotency_key: "canonical-case",
      });
      const config = (
        await c.query(
          "select inet_server_addr() host,current_setting('port')::integer port,current_database() as database",
        )
      ).rows[0];
      const partner = new pg.Client({ ...config, user: "carr_writer" });
      await partner.connect();
      const human = { id: actor, slug: "joe", human: true, organization_tenant_id: "carr-internal" };
      const target = { deal_id: deals[1], publication_id: pubs[1].toUpperCase(), tour_id: tour.toUpperCase() };
      const begin = async () => {
        await partner.query("begin");
        await partner.query(
          "select set_config('carr.acting_actor_slug','joe',true),set_config('carr.verified_human_actor_slug','joe',true),set_config('carr.organization_tenant_id','carr-internal',true)",
        );
      };
      try {
        await begin();
        const args = {
          ...target,
          publication_version: 1,
          source_version: 1,
          cart_version: 0,
          cart_version_id: null,
          decision: "keep",
          property_ids: [],
          idempotency_key: randomUUID(),
        };
        const kept = await TOOLS["accept-portal-proposal-v1"].handler(partner, human, args);
        assert.equal(kept.publication_id, pubs[1]);
        assert.equal(kept.tour_id, tour);
        await partner.query("commit");
        await begin();
        const resolved = await TOOLS["read-portal-proposal-review-v1"].handler(partner, human, target);
        assert.equal(resolved.pending, null);
        assert.deepEqual(resolved.decision, kept);
        await assert.rejects(
          TOOLS["accept-portal-proposal-v1"].handler(partner, human, { ...args, idempotency_key: randomUUID() }),
          /proposal_already_reviewed/,
        );
      } finally {
        await partner.query("rollback");
        await partner.end();
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
                "ops." + table,
              ])
            ).rows[0].allowed,
            false,
          );
    });
    if (process.argv.includes("--record"))
      writeFileSync(
        new URL("../../workspace/contracts/fixtures/portal-proposals.v1.json", import.meta.url),
        JSON.stringify(receipt, null, 2) + "\n",
      );
    const fixture = JSON.parse(
      readFileSync(new URL("../../workspace/contracts/fixtures/portal-proposals.v1.json", import.meta.url), "utf8"),
    );
    assert.deepEqual(receipt, fixture);
  } finally {
    await db.close();
  }
});

test("registered proposal review contract and recorded SQL response stay pinned", () => {
  const contract = JSON.parse(
    readFileSync(new URL("../../workspace/contracts/portal-proposal-acceptance.v1.json", import.meta.url), "utf8"),
  );
  const fixture = JSON.parse(
    readFileSync(
      new URL("../../workspace/contracts/fixtures/portal-proposal-acceptance.v1.json", import.meta.url),
      "utf8",
    ),
  );
  assert.deepEqual(contract.accept_response, {
    ...proposalDecisionSchema,
    properties: { ...proposalDecisionSchema.properties, replayed: { type: "boolean" } },
  });
  const accepted = JSON.parse(
    readFileSync(
      new URL("../../workspace/contracts/fixtures/portal-proposal-acceptance-receipt.v1.json", import.meta.url),
      "utf8",
    ),
  );
  const receiptValidator = new Validator(contract.accept_response, "2020-12", false);
  assert.equal(receiptValidator.validate(accepted).valid, true);
  assert.equal(receiptValidator.validate({ ...accepted, replayed: true }).valid, true);
  assert.equal(receiptValidator.validate({ ...accepted, selection_version_id: null }).valid, false);
  assert.deepEqual(contract.review, proposalReviewSchema);
  assert.deepEqual(contract.read_request, TOOLS["read-portal-proposal-review-v1"].inputSchema);
  assert.deepEqual(contract.accept_request, TOOLS["accept-portal-proposal-v1"].inputSchema);
  assert.deepEqual(parseProposalReview(fixture.review), fixture.review);
  assert.throws(() => parseProposalReview({ ...fixture.review, source_version: "5" }), /proposal_response_invalid/);
  assert.throws(
    () => parseProposalReview({ ...fixture.review, state: "accepted", property_ids: [], selection_version_id: null }),
    /proposal_response_invalid/,
  );
  assert.throws(() => parseProposalReview({ ...fixture.review, state: "kept" }), /proposal_response_invalid/);
});
