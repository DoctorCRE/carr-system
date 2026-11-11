// Check producer ownership and evidence against the registered PostgreSQL read.

import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import test from "node:test";
import { Validator } from "@cfworker/json-schema";
import pg from "pg";
import { acquireDisposablePostgres } from "./helpers/disposable-postgres.ts";
import { array, executeRegisteredTool, object } from "./helpers/legacy-test-registry.ts";

const contract = new Validator(
  JSON.parse(readFileSync(new URL("../contracts/lead-reachability.v2.json", import.meta.url), "utf8")),
);
test("registered board preserves identity, owns classification, and exposes exact source evidence under carr_reader", async () => {
  const fixture = await acquireDisposablePostgres({ prefix: "lead-reachability-", snapshot: true });
  const c = new pg.Client(fixture.connection);
  await c.connect();
  try {
    const actor = { ...(await c.query("select id,slug from actor where slug='joe'")).rows[0], human: true };
    const party = randomUUID(),
      lead = randomUUID();
    await c.query(
      `insert into party(id,kind,name,created_by,updated_by) values($1,'person','Synthetic reachability',$2,$2)`,
      [party, actor.id],
    );
    await c.query(`insert into lead(id,party_id,stage,created_by,updated_by) values($1,$2,'new',$3,$3)`, [
      lead,
      party,
      actor.id,
    ]);
    const read = async () => {
      await c.query("set role carr_reader");
      try {
        let calls = 0;
        const db = {
          query: (...args: Parameters<typeof c.query>) => {
            calls++;
            return c.query(...args);
          },
        };
        const result = object(
          await executeRegisteredTool(db, actor, "lead-board", { workspace: "leads", lead_id: lead }),
        );
        assert.equal(calls, 1);
        assert.deepEqual(
          object(result.detail).reachability,
          object(array(result.leads).find((r) => object(r).id === lead)).reachability,
        );
        return result;
      } finally {
        await c.query("reset role");
      }
    };
    const empty = await read();
    assert.equal(object(object(empty.detail).reachability).evidence_state, "unknown");
    const finding = (
      await c.query(
        `insert into record_flag(subject_type,subject_id,kind,value,source,observed_at,expires_on,created_by)
      values('party',$1,'verified','{"found":true,"checked":["identity"],"epistemic_status":"source_backed"}',
      'https://example.test/registry/1','2020-01-01','2099-01-01',$2) returning id`,
        [party, actor.id],
      )
    ).rows[0].id;
    const verified = await read();
    const row = array(verified.leads)
      .map(object)
      .find((r) => r.id === lead);
    assert.ok(row);
    const reachability = object(row.reachability);
    assert.equal(row.party_id, party);
    assert.deepEqual(row.reachability, object(verified.detail).reachability);
    assert.equal(reachability.classification, "research");
    assert.equal(reachability.research_state, "verified");
    assert.equal(object(array(reachability.evidence)[0]).flag_id, finding);
    assert.equal(object(array(reachability.evidence)[0]).source, "https://example.test/registry/1");
    assert.equal(contract.validate(row.reachability).valid, true);
    assert.notEqual(reachability.source_revision, object(object(empty.detail).reachability).source_revision);
    await c.query("update party set contact_state='paused' where id=$1", [party]);
    const restricted = await read();
    assert.equal(object(object(restricted.detail).reachability).contact_eligible, false);
    assert.equal(array(restricted.leads).length, array(verified.leads).length);
    await c.query("update record_flag set kind='discrepancy' where id=$1", [finding]);
    assert.equal(object(object((await read()).detail).reachability).evidence_state, "conflicting");
    await c.query("update record_flag set kind='verified' where id=$1", [finding]);
    await c.query("update party set contact_state='active',email='reachable@example.test' where id=$1", [party]);
    await c.query("update lead set created_at=now()-interval '2 days' where id=$1", [lead]);
    const route = (
      await c.query(
        `insert into record_flag(subject_type,subject_id,kind,value,source,observed_at,expires_on,created_by)
      values('party',$1,'email','{"found":true,"email":"reachable@example.test"}','https://example.test/contact',now()-interval '1 minute',current_date+90,$2) returning id`,
        [party, actor.id],
      )
    ).rows[0].id;
    const activity = (
      await c.query(
        `insert into activity(actor_id,kind,summary,lead_id,connected,occurred_at)
      values($1,'call','Synthetic successful contact',$2,false,now()-interval '1 minute') returning id`,
        [actor.id, lead],
      )
    ).rows[0].id;
    const classification = async () => object(object((await read()).detail).reachability).classification;
    assert.equal(await classification(), "research");
    await c.query("update activity set connected=true where id=$1", [activity]);
    const reachable = await read();
    const positive = object(object(reachable.detail).reachability);
    assert.equal(positive.classification, "reachable");
    assert.equal(object(positive.successful_contact).activity_id, activity);
    assert.equal(object(positive.usable_route).flag_id, route);
    assert.equal(contract.validate(positive).valid, true);
    for (const sql of [
      "update record_flag set expires_on=current_date-1 where id=$1",
      'update record_flag set value=\'{"found":true,"email":"wrong@example.test"}\' where id=$1',
    ]) {
      await c.query(sql, [route]);
      assert.equal(await classification(), "research");
      await c.query(
        `update record_flag set expires_on=current_date+90,value='{"found":true,"email":"reachable@example.test"}' where id=$1`,
        [route],
      );
    }
    await c.query("update lead set suppressed=true where id=$1", [lead]);
    assert.equal(await classification(), "research");
    await c.query("update lead set suppressed=false where id=$1", [lead]);
    await c.query("update party set contact_state='paused' where id=$1", [party]);
    assert.equal(await classification(), "research");
    await c.query("update party set contact_state='active' where id=$1", [party]);
    await c.query("update record_flag set kind='discrepancy' where id=$1", [finding]);
    assert.equal(await classification(), "research");
    await c.query("update record_flag set kind='verified' where id=$1", [finding]);
    for (const date of ["now()+interval '1 day'", "now()-interval '91 days'", "now()-interval '3 days'"]) {
      await c.query(`update activity set occurred_at=${date} where id=$1`, [activity]);
      assert.equal(await classification(), "research");
    }
    await c.query(
      `update activity set occurred_at=now()-interval '1 minute',connected=null,kind='email_in',source='mail_ingest',detail=$2 where id=$1`,
      [activity, JSON.stringify({ match: "exact", party_id: party, evidence_ref: "fixture:reply", automated: false })],
    );
    const inbound = await read();
    assert.equal(object(object(inbound.detail).reachability).classification, "reachable");
    for (const change of [{ party_id: randomUUID() }, { match: "fuzzy" }, { automated: true }, { evidence_ref: "" }]) {
      await c.query("update activity set detail=$2 where id=$1", [
        activity,
        JSON.stringify({ match: "exact", party_id: party, evidence_ref: "fixture:reply", automated: false, ...change }),
      ]);
      assert.equal(await classification(), "research");
    }
    if (process.env.CARR_REACHABILITY_RECORDING)
      writeFileSync(
        process.env.CARR_REACHABILITY_RECORDING,
        `${JSON.stringify(
          {
            verb: "lead-board",
            args: { workspace: "leads", lead_id: lead },
            cases: {
              unknown: empty.detail,
              verified_identity: verified.detail,
              restricted: restricted.detail,
              reachable: reachable.detail,
              exact_inbound: inbound.detail,
            },
          },
          null,
          2,
        )}\n`,
      );
  } finally {
    await c.end();
    await fixture.close();
  }
});
