// Record and verify the registered summary contract against disposable PostgreSQL sources.

import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import test from "node:test";
import { postgresDatabase } from "./helpers/postgres-database.mjs";

const { recordReadCall } = await import(new URL("../src/mcp.js", import.meta.url).href);

const { executeRegisteredTool, TOOLS } = await import(new URL("../src/tools.js", import.meta.url).href);

import type { DealUpdateSummaryV1 } from "../src/deal-update-summary.ts";

const deal = "00000000-0000-4000-8000-000000000002";
const other = "00000000-0000-4000-8000-000000000009";
const actor = { id: "00000000-0000-4000-8000-000000000001", slug: "joe", human: true };
const fixturePath = new URL("../../workspace/contracts/deal-update-summary.v1.fixture.json", import.meta.url);
test("registered projection isolates deals, pages ties, refuses arrivals and records source-bound extracts", async (t) => {
  const db = await postgresDatabase("deal-update-summary-");
  const c = db.client;
  const read = async (args: object = {}): Promise<DealUpdateSummaryV1> =>
    executeRegisteredTool(c, actor, "read-deal-update-summary-v1", { deal_id: deal, ...args });
  try {
    await c.query(`create table v_deal_room_deal(id uuid primary key);
      create table v_deal_room_note(id uuid primary key, deal_id uuid, created_at timestamptz, actor text, text text);
      create table v_deal_room_activity(id uuid primary key, deal_id uuid, occurred_at timestamptz, actor text, summary text, detail text);
      create table v_deal_room_event(id uuid primary key, subject_id uuid, recorded_at timestamptz, actor text, field text, old_value jsonb, new_value jsonb);
      create role carr_reader;
      grant select on all tables in schema public to carr_reader;`);
    await c.query("insert into v_deal_room_deal values($1),($2)", [deal, other]);
    await c.query(
      `insert into v_deal_room_note values
      ('00000000-0000-4000-8000-000000000003',$1,'2026-01-02Z','joe','Correction: $24 was rejected. $26 remains disputed.'),
      ('00000000-0000-4000-8000-000000000004',$2,'2026-01-03Z','joe','Foreign deal private evidence.')`,
      [deal, other],
    );
    await c.query(
      `insert into v_deal_room_activity values
      ('00000000-0000-4000-8000-000000000003',$1,'2026-01-01Z','dell','Landlord offered $24.','Original source detail.')`,
      [deal],
    );
    await c.query(
      `insert into v_deal_room_event values
      ('00000000-0000-4000-8000-000000000005',$1,'2026-01-02Z','joe','phase','"search"','"negotiation"'),
      ('00000000-0000-4000-8000-000000000006',$1,'2026-01-02Z','joe','internal_probe',null,'"Unrelated internal evidence"')`,
      [deal],
    );
    await c.query("set role carr_reader");
    const full = await read();
    assert.equal(full.coverage.complete, false);
    assert.equal(full.coverage.unsupported_events_present, true);
    assert.equal(full.coverage.covered_count, 3);
    assert.ok(full.summary.text.includes("$24 was rejected"));
    assert.ok(full.summary.text.includes("Landlord offered $24"));
    assert.ok(full.summary.text.includes("search"));
    assert.ok(!JSON.stringify(full).includes("Foreign deal"));
    assert.ok(!JSON.stringify(full).includes("Unrelated internal"));
    assert.equal(TOOLS["read-deal-update-summary-v1"].write, false);
    const first = await read({ page_size: 1 });
    const second = await read({ page_size: 1, cursor: first.history.next_cursor });
    const third = await read({ page_size: 1, cursor: second.history.next_cursor });
    assert.deepEqual(
      [first, second, third].flatMap((p) => p.history.entries),
      full.history.entries,
    );
    assert.equal(third.history.next_cursor, null);
    const normalize = (result: DealUpdateSummaryV1) => ({
      ...result,
      source_revision: "<database-snapshot>",
      history: { ...result.history, revision: "<database-snapshot>" },
    });
    const recorded = normalize(full);
    if (process.argv.includes("--record-fixture"))
      await writeFile(fixturePath, JSON.stringify(recorded, null, 2) + "\n");
    else assert.deepEqual(recorded, JSON.parse(await readFile(fixturePath, "utf8")));
    await c.query("reset role");
    await t.test("continuation survives production read auditing and unrelated writes", async () => {
      await c.query(`create table tool_read_count(day date, verb text, actor_slug text,
        sponsoring_human_slug text, via text, ok boolean, n bigint, last_at timestamptz,
        constraint tool_read_count_key unique nulls not distinct(day,verb,actor_slug,sponsoring_human_slug,via,ok));
        create table unrelated(value text);`);
      for (let attempt = 1; attempt <= 3; attempt++) {
        const beforeAudit = await read({ page_size: 1 });
        await recordReadCall(
          async (sql: string, params: unknown[]) => {
            await c.query(sql, params);
          },
          actor,
          "read-deal-update-summary-v1",
          true,
        );
        assert.equal((await c.query("select sum(n)::int n from tool_read_count")).rows[0].n, attempt);
        assert.deepEqual(await read({ page_size: 1, cursor: beforeAudit.history.next_cursor }), second);
      }
      await c.query("insert into unrelated values('unrelated job')");
      assert.deepEqual(await read({ page_size: 1, cursor: first.history.next_cursor }), second);
      await c.query("update v_deal_room_note set text='Foreign edit' where deal_id=$1", [other]);
      await c.query("update v_deal_room_event set new_value='true' where field='internal_probe'");
      assert.deepEqual(await read({ page_size: 1, cursor: first.history.next_cursor }), second);
    });
    await c.query(
      `insert into v_deal_room_note values('00000000-0000-4000-8000-000000000007',$1,'2025-01-01Z','joe','Backdated arrival')`,
      [deal],
    );
    await assert.rejects(read({ cursor: first.history.next_cursor }), /stale_history_cursor/);
    assert.equal((await read()).coverage.covered_count, 4);
    const beforeEdit = await read({ page_size: 1 });
    await c.query("update v_deal_room_note set text='Revised correction' where deal_id=$1", [deal]);
    await assert.rejects(read({ cursor: beforeEdit.history.next_cursor }), /stale_history_cursor/);
    const beforeDelete = await read({ page_size: 1 });
    await c.query("delete from v_deal_room_note where id='00000000-0000-4000-8000-000000000007'");
    await assert.rejects(read({ cursor: beforeDelete.history.next_cursor }), /stale_history_cursor/);
    await c.query(
      `insert into v_deal_room_note
      select ('10000000-0000-4000-8000-' || lpad(n::text,12,'0'))::uuid,$1,'2024-01-01Z','joe','Older original ' || n
      from generate_series(1,503) n`,
      [deal],
    );
    let page = await read({ page_size: 100 });
    assert.equal(page.coverage.complete, false);
    const collected = [...page.history.entries];
    for (let index = 0; index < 6 && page.history.next_cursor; index++) {
      page = await read({ page_size: 100, cursor: page.history.next_cursor });
      collected.push(...page.history.entries);
    }
    assert.equal(page.history.next_cursor, null);
    assert.equal(collected.length, 506);
    assert.equal(new Set(collected.map((row) => row.source + row.id)).size, 506);
    await t.test("revision covers older originals, all source families and new arrivals", async () => {
      for (const sql of [
        "update v_deal_room_note set text='Edited older original' where id='10000000-0000-4000-8000-000000000001'",
        "update v_deal_room_activity set detail='Edited retained detail' where deal_id=$1",
        "update v_deal_room_event set new_value='true' where field='phase' and subject_id=$1",
        "delete from v_deal_room_activity where deal_id=$1",
        "delete from v_deal_room_event where field='phase' and subject_id=$1",
      ]) {
        const before = await read({ page_size: 1 });
        await c.query(sql, sql.includes("$1") ? [deal] : []);
        await assert.rejects(read({ cursor: before.history.next_cursor }), /stale_history_cursor/);
      }
      const before = await read({ page_size: 1 });
      await c.query(
        `insert into v_deal_room_note values
        ('00000000-0000-4000-8000-000000000010',$1,'2027-01-01Z','joe','New arrival')`,
        [deal],
      );
      await assert.rejects(read({ cursor: before.history.next_cursor }), /stale_history_cursor/);
      const fresh = await read({ page_size: 1 });
      await c.query("set time zone 'America/Chicago'");
      assert.equal((await read({ cursor: fresh.history.next_cursor })).source_revision, fresh.source_revision);
      await c.query("set time zone 'UTC'");
    });
    await c.query("alter table v_deal_room_note rename to unavailable_note");
    await assert.rejects(read(), /database_refused_the_statement/);
  } finally {
    await db.close();
  }
});
