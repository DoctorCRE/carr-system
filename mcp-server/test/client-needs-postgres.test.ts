import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import test from "node:test";
import pg from "pg";
import { acquireDisposablePostgres } from "./helpers/disposable-postgres.ts";
import { record, records, tool } from "./helpers/registered-tool.ts";

const names = [
  "record-deal-client-need-v1",
  "approve-deal-client-need-v1",
  "read-deal-client-needs-v1",
  "read-vendor-client-needs-v1",
] as const;
test("need guards declare partner authority in the registry", async () => {
  for (const name of names) {
    const verb = tool(name);
    assert.equal(verb.authorityOnly, true, name);
    await assert.rejects(
      verb.handler(
        {
          query: async () => {
            throw Error("database reached");
          },
        },
        { id: randomUUID(), slug: "robot", human: false },
        {},
      ),
      /AUTHORIZATION_REFUSED/,
    );
  }
});

test("deal need lifecycle, exact vendor matching, audit and recorded contract", async (t) => {
  const fixture = await acquireDisposablePostgres({ prefix: "client-needs-", snapshot: true });
  const db = new pg.Client(fixture.connection);
  try {
    await db.connect();
    await db.query("begin");
    const joe = record((await db.query("select id,slug from actor where slug='joe'")).rows[0]);
    const actor = { ...joe, human: true };
    const party = async (name: string) =>
      (
        await db.query("insert into party(kind,name,created_by,updated_by) values('org',$1,$2,$2) returning id", [
          name,
          joe.id,
        ])
      ).rows[0].id;
    const client = await db.query(
      "insert into client(party_id,roster_ref,created_by,updated_by) values($1,$2,$3,$3) returning id",
      [await party("Synthetic practice"), `C-${randomUUID()}`, joe.id],
    );
    const clientId = client.rows[0].id;
    const deal = (
      await db.query(
        "insert into deal(client_id,name,deal_type,phase,created_by,updated_by) values($1,'Synthetic deal','lease','closed',$2,$2) returning id",
        [clientId, joe.id],
      )
    ).rows[0].id;
    const vendor = (
      await db.query(
        "insert into vendor(party_id,vendor_ref,category,created_by,updated_by) values($1,$2,'banker',$3,$3) returning id",
        [await party("Synthetic vendor"), `V-${randomUUID()}`, joe.id],
      )
    ).rows[0].id;
    const call = async (name: string, args: Record<string, unknown>, who: Record<string, unknown> = actor) => {
      await db.query("savepoint call");
      try {
        const result = await tool(name).handler(db, who, args);
        await db.query("release savepoint call");
        return result;
      } catch (e) {
        await db.query("rollback to savepoint call");
        throw e;
      }
    };
    const read = () => call(names[3], { vendor });
    const fields = {
      vendor,
      description: "Practice needs equipment financing",
      applicability: "This vendor offers the recorded equipment program",
      source_ref: "synthetic:deal-review",
      source_revision: "review-1",
      observed_at: "2026-01-01T00:00:00Z",
      fresh_until: "2099-01-01T00:00:00Z",
      restriction: "allowed",
      withdrawn: false,
    };
    const request = { deal, fields, idempotency_key: randomUUID() };
    const created = await call(names[0], request);
    const need = record(created.need);
    assert.equal(need.state, "draft");
    assert.equal((await read()).state, "missing_approval");
    assert.equal((await call(names[0], request)).replayed, true);
    let version = 1;
    const approve = () => call(names[1], { deal, need: need.id, base_version: version, idempotency_key: randomUUID() });
    await assert.rejects(
      call(
        names[1],
        { deal, need: need.id, base_version: version, idempotency_key: randomUUID() },
        { ...actor, slug: "dell" },
      ),
      /AUTHORIZATION_REFUSED/,
    );
    const approvalRequest = { deal, need: need.id, base_version: version, idempotency_key: randomUUID() };
    await call(names[1], approvalRequest);
    version++;
    assert.equal((await call(names[1], approvalRequest)).replayed, true);
    let response = await read();
    assert.equal(response.state, "ready");
    let row = record(records(response.rows)[0]);
    assert.equal(row.client_id, clientId);
    assert.equal(row.deal_id, deal);
    assert.equal(row.vendor_id, vendor);
    assert.equal(row.approved_revision, row.version);
    assert.equal(row.source_revision, fields.source_revision);
    assert.equal(row.applicability, fields.applicability);
    await t.test("another vendor never matches shared context", async () => {
      const other = (
        await db.query(
          "insert into vendor(party_id,vendor_ref,category,created_by,updated_by) values($1,$2,'banker',$3,$3) returning id",
          [await party("Other synthetic vendor"), `V-${randomUUID()}`, joe.id],
        )
      ).rows[0].id;
      assert.equal((await call(names[3], { vendor: other })).state, "empty");
      const second = await call(names[0], { deal, fields, idempotency_key: randomUUID() });
      const secondNeed = record(second.need);
      await call(names[1], { deal, need: secondNeed.id, base_version: 1, idempotency_key: randomUUID() });
      const page = await call(names[3], { vendor, limit: 1 });
      assert.equal(page.next_offset, 1);
      const next = await call(names[3], { vendor, limit: 1, offset: 1 });
      assert.equal(next.next_offset, null);
      assert.notEqual(record(records(page.rows)[0]).id, record(records(next.rows)[0]).id);
      await call(names[0], {
        deal,
        need: secondNeed.id,
        base_version: 2,
        fields: { ...fields, withdrawn: true },
        idempotency_key: randomUUID(),
      });
    });
    const schema = JSON.parse(readFileSync(new URL("../contracts/client-needs.v1.json", import.meta.url), "utf8"));
    const { Validator } = await import("@cfworker/json-schema");
    const validator = new Validator(schema);
    assert.equal(validator.validate(response).valid, true);
    if (process.env.RECORD_CLIENT_NEEDS_FIXTURE === "1")
      writeFileSync(
        new URL("./fixtures/client-needs.v1.json", import.meta.url),
        JSON.stringify(response, null, 2) + "\n",
      );
    const edit = async (overrides: Record<string, unknown>) => {
      const result = await call(names[0], {
        deal,
        need: need.id,
        base_version: version,
        fields: { ...fields, ...overrides },
        idempotency_key: randomUUID(),
      });
      version = Number(record(result.need).version);
      return record(result.need);
    };
    await t.test("approval lapses after edit", async () => {
      assert.equal((await edit({ source_revision: "review-2" })).state, "edited_lapsed");
      assert.equal((await read()).state, "missing_approval");
    });
    await assert.rejects(
      call(names[1], { deal, need: need.id, base_version: 1, idempotency_key: randomUUID() }),
      /version_conflict/,
    );
    await t.test("edited need can be approved again", async () => {
      await approve();
      version++;
      assert.equal((await read()).state, "ready");
    });
    await t.test("approved need can be withdrawn and restored without approval", async () => {
      await edit({ withdrawn: true });
      assert.equal((await read()).state, "empty");
      await assert.rejects(approve(), /need_not_approvable/);
      assert.equal((await edit({ withdrawn: false })).state, "edited_lapsed");
    });
    await t.test("restricted need never matches", async () => {
      await approve();
      version++;
      await edit({ restriction: "restricted" });
      assert.equal((await read()).state, "empty");
      await assert.rejects(approve(), /need_not_approvable/);
    });
    await t.test("stale and future source evidence never matches", async () => {
      await edit({ fresh_until: "2026-01-02T00:00:00Z" });
      assert.equal((await read()).state, "empty");
      await assert.rejects(approve(), /need_not_approvable/);
      await edit({ observed_at: "2098-01-01T00:00:00Z" });
      assert.equal((await read()).state, "empty");
      assert.equal((await edit({})).state, "edited_lapsed");
    });
    await approve();
    version++;
    await t.test("approved need becomes stale when freshness expires", async () => {
      const expiry = (await db.query("select (clock_timestamp()+interval '2 seconds')::text as expiry")).rows[0].expiry;
      await edit({ fresh_until: expiry });
      await approve();
      version++;
      assert.equal((await read()).state, "ready");
      await db.query("select pg_sleep(2.1)");
      assert.equal((await read()).state, "empty");
      const stale = record(records((await call(names[2], { deal })).rows).find((r) => r.id === need.id));
      assert.equal(stale.state, "stale");
      await edit({});
      await approve();
      version++;
    });
    const otherClient = (
      await db.query("insert into client(party_id,roster_ref,created_by,updated_by) values($1,$2,$3,$3) returning id", [
        await party("Other synthetic practice"),
        `C-${randomUUID()}`,
        joe.id,
      ])
    ).rows[0].id;
    await db.query("update deal set client_id=$2 where id=$1", [deal, otherClient]);
    assert.equal((await read()).state, "empty");
    await db.query("update deal set client_id=$2 where id=$1", [deal, clientId]);
    await db.query("update deal_client_need set description='Edited outside verb' where id=$1", [need.id]);
    response = await call(names[2], { deal });
    row = record(records(response.rows).find((r) => r.id === need.id));
    assert.equal(row.state, "edited_lapsed");
    assert.equal(row.approved_revision, null);
    await t.test("draft, edited and stale needs can be withdrawn", async () => {
      for (const state of ["draft", "edited_lapsed", "stale"]) {
        const f = { ...fields, ...(state === "stale" ? { fresh_until: "2026-01-02T00:00:00Z" } : {}) };
        const created = record((await call(names[0], { deal, fields: f, idempotency_key: randomUUID() })).need);
        let revision = 1;
        if (state === "edited_lapsed") {
          await call(names[0], {
            deal,
            need: created.id,
            base_version: 1,
            fields: { ...f, description: "Changed need" },
            idempotency_key: randomUUID(),
          });
          revision++;
        }
        const withdrawn = record(
          (
            await call(names[0], {
              deal,
              need: created.id,
              base_version: revision,
              fields: { ...f, withdrawn: true },
              idempotency_key: randomUUID(),
            })
          ).need,
        );
        assert.equal(withdrawn.state, "withdrawn");
        assert.equal(withdrawn.approved_revision, null);
      }
    });
    const audit = await db.query("select id from event where subject_id=$1 and field='client_need'", [deal]);
    assert.ok(audit.rows.length >= 10);
    await assert.rejects(call(names[3], { vendor: randomUUID() }), /not_found/);
    await assert.rejects(call(names[3], { vendor, limit: 51 }), /invalid_need_input/);
    const recorded = JSON.parse(readFileSync(new URL("./fixtures/client-needs.v1.json", import.meta.url), "utf8"));
    assert.equal(validator.validate(recorded).valid, true);
    const invalid = structuredClone(recorded);
    invalid.rows[0].state = "stale";
    assert.equal(validator.validate(invalid).valid, false, "contract refuses a stale vendor match");
  } finally {
    await db.query("rollback").catch(() => {});
    await db.end();
    await fixture.close();
  }
});
