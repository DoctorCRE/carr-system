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
  JSON.parse(readFileSync(new URL("../contracts/lead-reachability.v1.json", import.meta.url), "utf8")),
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
        return object(await executeRegisteredTool(c, actor, "lead-board", { workspace: "leads", lead_id: lead }));
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
    if (process.env.CARR_REACHABILITY_RECORDING)
      writeFileSync(
        process.env.CARR_REACHABILITY_RECORDING,
        `${JSON.stringify(
          {
            verb: "lead-board",
            args: { workspace: "leads", lead_id: lead },
            cases: { unknown: empty.detail, verified_identity: verified.detail, restricted: restricted.detail },
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
