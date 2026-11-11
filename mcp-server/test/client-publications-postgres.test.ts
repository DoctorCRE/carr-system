import { execFileSync, spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const { authorizeDeliverable } = await import(`../src/${"deal-deliverables"}.js`);
const { executeRegisteredTool } = await import(`../src/${"tools"}.js`);

import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import pg from "pg";
import { clientPublicationTools } from "../src/client-publications.ts";
import type { Actor, WriteEvent } from "../src/verb-contract.ts";
import { postgresDatabase } from "./helpers/postgres-database.mjs";
import { restoreEventIdentity } from "./helpers/snapshot-schema.mjs";
import { readMigration } from "./migration-source.testhelper.ts";

test("publication lifecycle, client isolation and exact preview", async () => {
  const root = fileURLToPath(new URL("../../", import.meta.url));
  const db = await postgresDatabase("publications-");
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
    ])
      await c.query(readMigration(n));
    await c.query(readMigration("1152_client_portal_sessions.sql"));
    await c.query(
      "create function public.retrieval_visibility_actor_id(text) returns uuid language sql as $$select id from actor where slug=$1 and active$$",
    );
    if (!process.argv.includes("--baseline")) await c.query(readMigration("1179_client_publications.sql"));
    const actor = randomUUID();
    await c.query("insert into actor(id,slug,kind,display_name) values($1,'joe','human','Synthetic partner')", [actor]);
    await c.query(
      "insert into client_status(slug,label) values('active','Active'); insert into deal_phase(slug,label,sort) values('research','INTERNAL_PHASE',1)",
    );
    const deals: string[] = [];
    for (const slug of ["synthetic-a", "synthetic-b"]) {
      const party = (
        await c.query(
          "insert into party(kind,name,created_by,updated_by) values('person','Synthetic client',$1,$1) returning id",
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
          "insert into deal(client_id,name,deal_type,phase,created_by,updated_by) values($1,'INTERNAL_DEAL','lease','research',$2,$2) returning id",
          [client, actor],
        )
      ).rows[0].id;
      await c.query("select ops.register_client_portal($1,$2,$3)", [deal, slug, "test"]);
      deals.push(deal);
    }
    const [dealA, dealB] = deals;
    assert.ok(dealA && dealB);
    await c.query("alter table deal add column owner text default 'joe'");
    const events: Parameters<WriteEvent>[] = [];
    const tools = clientPublicationTools({
      authorizeDeal: authorizeDeliverable,
      withEnvelope: async (_c, _a, _n, _args, fn) => fn(),
      writeEvent: async (...args) => {
        events.push(args);
      },
    });
    const partner: Actor = { id: actor, slug: "joe", human: true };
    for (const who of [
      { id: actor, slug: "joe", human: false },
      { id: actor, slug: "outsider", human: true },
    ]) {
      await assert.rejects(
        tools["preview-deal-client-view"].handler(c, who, {
          deal_id: dealA,
        }),
      );
    }
    const project = async (slug = "synthetic-a") =>
      (await c.query("select ops.client_portal_projection($1) p", [slug])).rows[0].p;
    const write = async (
      id: string,
      version: number,
      published: boolean,
      kind = "milestone",
      file: string | null = null,
      title = "Client milestone",
      deal = dealA,
    ) =>
      (
        await c.query("select ops.write_client_publication($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) p", [
          deal,
          id,
          version,
          published,
          kind,
          title,
          file ? null : "Published text",
          file,
          file ? (await c.query("select version from ops.deal_deliverable where id=$1", [file])).rows[0].version : null,
          "joe",
        ])
      ).rows[0].p;
    assert.deepEqual(await project(), {
      slug: "synthetic-a",
      roadmap: { current_phase: null, phases: [] },
      materials: [],
      publications: [],
    });
    const id = randomUUID();
    await write(id, 0, false);
    assert.equal((await project()).publications.length, 0);
    assert.equal(
      (
        await tools["list-deal-client-publications"].handler(c, partner, {
          deal_id: dealA,
        })
      ).publications.length,
      1,
    );
    assert.deepEqual(
      (
        await tools["preview-deal-client-view"].handler(c, partner, {
          deal_id: dealA,
        })
      ).portal,
      await project(),
    );
    await write(id, 1, true);
    assert.equal((await project()).publications[0].title, "Client milestone");
    await assert.rejects(write(id, 1, false), /publication_version_conflict/);
    await write(id, 2, true, "milestone", null, "Updated milestone");
    assert.equal((await project()).publications[0].title, "Updated milestone");
    assert.deepEqual(
      (await c.query("select ops.read_client_portal('synthetic-a','test') p")).rows[0].p,
      await project(),
    );
    assert.equal(JSON.stringify(await project()).includes("INTERNAL"), false);
    assert.equal((await project("synthetic-b")).publications.length, 0);
    await assert.rejects(write(id, 3, true, "milestone", null, "Cross client", dealB), /publication_not_found/);
    await write(id, 3, false);
    assert.equal((await project()).publications.length, 0);
    const file = randomUUID();
    await c.query(
      "insert into ops.deal_deliverable(id,organization_tenant_id,deal_id,filename,mime,bytes,sha256,object_key,created_by,state,finalized_at,visibility,version) values($1,'carr-internal',$2,'Synthetic.pdf','application/pdf',10,$3,'INTERNAL_OBJECT','joe','finalized',now(),'private',1)",
      [file, dealA, "a".repeat(64)],
    );
    const fid = randomUUID();
    await assert.rejects(write(fid, 0, true, "deliverable", file), /publication_file_not_shared/);
    await c.query("update ops.deal_deliverable set visibility='shared',version=2 where id=$1", [file]);
    await assert.rejects(
      write(randomUUID(), 0, true, "deliverable", file, "Cross file", dealB),
      /publication_file_not_shared/,
    );
    await write(fid, 0, true, "deliverable", file);
    assert.equal((await project()).publications[0].deliverable.id, file);
    assert.equal(JSON.stringify(await project()).includes("INTERNAL_OBJECT"), false);
    await c.query("update ops.deal_deliverable set visibility='private',version=3 where id=$1", [file]);
    assert.equal((await project()).publications.length, 0);
    await c.query("update ops.deal_deliverable set visibility='shared',version=4 where id=$1", [file]);
    assert.equal((await project()).publications.length, 0);
    await write(fid, 1, true, "deliverable", file);
    assert.equal((await project()).publications.length, 1);
    await assert.rejects(
      tools["set-client-publication"].handler(c, partner, {
        deal_id: dealA,
        idempotency_key: randomUUID(),
        publication_id: randomUUID(),
        base_version: 0,
        published: true,
        title: "Invalid",
        kind: "deliverable",
        content: "bad",
      }),
    );
    await assert.rejects(
      c.query("select ops.write_client_publication($1,$2,1,true,'deliverable','Stale',null,$3,2,'joe')", [
        dealA,
        fid,
        file,
      ]),
      /publication_file_not_shared/,
    );
    await assert.rejects(
      tools["preview-deal-client-view"].handler(c, partner, { deal_id: randomUUID() }),
      /deliverable_denied/,
    );
    const digest = "a".repeat(64);
    await c.query("select ops.exchange_client_portal_session('synthetic-a','test',$1)", [digest]);
    assert.deepEqual(
      (await c.query("select ops.read_client_portal_session('synthetic-a',$1) p", [digest])).rows[0].p.portal,
      await project(),
    );
    const published = await tools["set-client-publication"].handler(c, partner, {
      deal_id: dealA,
      idempotency_key: randomUUID(),
      publication_id: id,
      base_version: 4,
      published: true,
      kind: "milestone",
      title: "Tool milestone",
      content: "Tool text",
    });
    assert.equal(published.publication.version, 5);
    assert.equal(events.length, 1);
    const pendingFile = randomUUID();
    await c.query(
      "insert into ops.deal_deliverable(id,organization_tenant_id,deal_id,filename,mime,bytes,sha256,object_key,created_by) values($1,'carr-internal',$2,'Unfinalized.pdf','application/pdf',10,$3,$4,'joe')",
      [pendingFile, dealA, "b".repeat(64), "unfinalized-" + pendingFile],
    );
    await assert.rejects(write(randomUUID(), 0, true, "deliverable", pendingFile), /publication_file_not_shared/);
    await c.query("update ops.deal_deliverable set visibility='private',version=5 where id=$1", [file]);
    await write(fid, 2, false, "deliverable", file);
    assert.equal(
      (await project()).publications.some((row: { kind: string }) => row.kind === "deliverable"),
      false,
    );
    const scratch = await mkdtemp(path.join(tmpdir(), "j9-proof-"));
    try {
      const revision = execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8", cwd: root }).trim();
      const preview = await tools["preview-deal-client-view"].handler(c, partner, { deal_id: dealA });
      const list = await tools["list-deal-client-publications"].handler(c, partner, { deal_id: dealA });
      const client = { source_commit: revision, response: { portal: await project() } };
      const fixture = {
        release: { git_sha: { value: revision } },
        "preview-deal-client-view": preview,
        "list-deal-client-publications": list,
      };
      await writeFile(path.join(scratch, "fixture.json"), JSON.stringify(fixture));
      await writeFile(path.join(scratch, "client.json"), JSON.stringify(client));
      const proof = () =>
        spawnSync(
          "python3",
          [
            path.join(root, "tools/prove-client-publications.py"),
            "--expected-revision",
            revision,
            "--deal-id",
            dealA,
            "--client-response",
            path.join(scratch, "client.json"),
            "--fixture",
            path.join(scratch, "fixture.json"),
          ],
          { encoding: "utf8" },
        );
      assert.equal(proof().status, 0);
      client.response.portal.publications[0].title = "Near miss";
      await writeFile(path.join(scratch, "client.json"), JSON.stringify(client));
      assert.equal(proof().status, 1);
    } finally {
      await rm(scratch, { recursive: true, force: true });
    }
    const schema = await readFile(new URL("../../db/schema.sql", import.meta.url), "utf8");
    for (const name of ["event", "tool_call"]) {
      const ddl = schema.match(new RegExp(`CREATE TABLE public\\.${name} \\([\\s\\S]*?\\n\\);`));
      assert.ok(ddl);
      await c.query(ddl[0]);
    }
    await restoreEventIdentity(c, schema);
    await c.query("alter table tool_call add primary key(idempotency_key)");
    const command = {
      idempotency_key: randomUUID(),
      deal_id: dealA,
      publication_id: randomUUID(),
      base_version: 0,
      published: false,
      kind: "milestone",
      title: "Retry draft",
      content: "Hidden",
    };
    const registered = async (args = command) => {
      await c.query("begin");
      try {
        const value = await executeRegisteredTool(c, partner, "set-client-publication", args);
        await c.query("commit");
        return value;
      } catch (error) {
        await c.query("rollback");
        throw error;
      }
    };
    const created = await registered();
    const replay = await registered();
    assert.deepEqual(replay.publication, created.publication);
    assert.equal(replay.replayed, true);
    await assert.rejects(registered({ ...command, title: "Changed retry" }), /key_reuse/);
    await c.query("update actor set active=false where slug='joe'");
    await assert.rejects(registered(), /deliverable_denied/);
    await c.query("update actor set active=true where slug='joe'");
    for (let i = 0; i < 99; i++) await write(randomUUID(), 0, true);
    await assert.rejects(write(randomUUID(), 0, true), /publication_capacity/);
    assert.equal((await project()).publications.length, 100);
    const other = new pg.Client({
      host: "127.0.0.1",
      port: (await c.query("select inet_server_port() port")).rows[0].port,
      user: "fixture",
      database: "postgres",
    });
    await other.connect();
    try {
      const raceId = randomUUID();
      const outcomes = await Promise.allSettled([
        write(raceId, 0, false),
        other.query("select ops.write_client_publication($1,$2,0,false,$3,$4,$5,null,null,$6)", [
          dealA,
          raceId,
          "milestone",
          "Concurrent draft",
          "Text",
          "joe",
        ]),
      ]);
      assert.equal(outcomes.filter((x) => x.status === "fulfilled").length, 1);
      assert.match(
        outcomes.find((x): x is PromiseRejectedResult => x.status === "rejected")?.reason.message,
        /publication_version_conflict/,
      );
    } finally {
      await other.end();
    }
    await c.query(
      "insert into ops.client_publication(id,portal_slug,kind,title,content,published,version,updated_by) select md5('synthetic-'||n)::uuid,'synthetic-b','milestone','Draft','Text',false,1,'joe' from generate_series(1,1000) n",
    );
    await assert.rejects(write(randomUUID(), 0, false, "milestone", null, "Overflow", dealB), /publication_capacity/);
    await c.query("grant select on deal,client,party,actor to carr_writer");
    await c.query("set role carr_writer");
    assert.equal(
      (
        await tools["preview-deal-client-view"].handler(c, partner, {
          deal_id: dealA,
        })
      ).portal.publications.length,
      100,
    );
    await c.query("reset role");
    await c.query("update ops.client_portal set disabled_at=now() where slug='synthetic-a'");
    assert.equal(await project(), null);
    await assert.rejects(write(id, 4, true), /publication_portal_unavailable/);
    await c.query("set role carr_reader");
    await assert.rejects(write(id, 4, true), /permission denied/);
    await assert.rejects(c.query("select * from ops.client_publication"), /permission denied/);
    await c.query("reset role");
    await c.query("set role dot_reader");
    await assert.rejects(project(), /permission denied/);
    await c.query("reset role");
  } finally {
    await db.close();
  }
});
