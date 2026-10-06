import { restoreEventIdentity } from './helpers/snapshot-schema.mjs';
import { acquirePostgresFixtureGroup } from './helpers/disposable-postgres.mjs';
import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, renameSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import pg from "pg";
import { TOOLS, executeRegisteredTool } from "../src/tools.js";

const root = fileURLToPath(new URL("../../", import.meta.url));
const schema = readFileSync(path.join(root, "db/schema.sql"), "utf8");
const tables = ["party", "lead", "client", "vendor", "deal", "activity", "deal_participant",
  "party_link", "record_flag", "tool_call", "event"];
// Execute the relevant current-schema table definitions, rather than a SQL fake
// that answers marker comments and cannot detect nonexistent columns. No remote
// DB, environment credential, production fixture, or schema data is used.
const ddl = tables.map(name => {
  const match = schema.match(new RegExp(`CREATE TABLE public\\.${name} \\(.*?\\n\\);`, "s"));
  assert.ok(match, `current schema must define ${name}`);
  return match[0];
}).join("\n");
const actor = { id: "10000000-0000-4000-8000-000000000001", slug: "joe", human: true,
  via: "mcp", client_id: "codex" };
const survivor = "20000000-0000-4000-8000-000000000001";
const loser = "20000000-0000-4000-8000-000000000002";
const unrelated = "20000000-0000-4000-8000-000000000003";
const role = n => `30000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const options = {
  idempotency_key: "synthetic-confirm-merge", survivor_party: "C-900001",
  merged_party: "C-900002", match_basis: "matching synthetic phone and address",
};

function pgBin() {
  const candidates = ["/opt/homebrew/opt/postgresql@17/bin", "/usr/lib/postgresql/17/bin",
    "/usr/lib/postgresql/16/bin", ...process.env.PATH.split(path.delimiter)];
  const directory = candidates.find(dir => ["initdb", "pg_ctl"].every(bin => existsSync(path.join(dir, bin))));
  assert.ok(directory, "PostgreSQL binaries required for confirm-merge schema regression");
  return directory;
}

test("confirm-merge executes against the current activity schema", async t => {
  const bin = pgBin();
  const out = path.join(root, "out");
  mkdirSync(out, { recursive: true });
  const cluster = mkdtempSync(path.join(out, "wr182-pg-"));
  const data = path.join(cluster, "data");
  // Short socket path avoids macOS's Unix socket length limit for worktrees.
  const socket = "/tmp";
  const port = 20000 + process.pid % 30000;
  const run = (command, args) => execFileSync(path.join(bin, command), args, { encoding: "utf8", stdio: "pipe" });
  let db;
  let startAttempted = false;
  const releaseBudget = await acquirePostgresFixtureGroup();
  try {
    run("initdb", ["-D", data, "-U", "carr_fixture", "--auth=trust", "--encoding=UTF8", "--no-locale"]);
    startAttempted = true;
    run("pg_ctl", ["-D", data, "-l", path.join(cluster, "postgres.log"), "-o",
      `-h '' -k ${socket} -p ${port}`, "-w", "start"]);
    db = new pg.Client({ host: socket, port, user: "carr_fixture", database: "postgres" });
    await db.connect();
    await db.query(ddl);
    await restoreEventIdentity(db, schema);
    const refView = schema.match(/CREATE VIEW public\.v_ref_index AS\n.*?;\n/s);
    assert.ok(refView, "current schema must define v_ref_index");
    await db.query(refView[0]);
    const graphView = schema.match(/CREATE VIEW public\.v_party_graph AS\n.*?;\n/s);
    assert.ok(graphView, "current schema must define v_party_graph");
    await db.query(graphView[0]);

    async function fixture(fn, { committed = false } = {}) {
      await db.query("begin");
      try {
        for (const [id, date] of [[survivor, "2020-01-01"], [loser, "2021-01-01"], [unrelated, "2022-01-01"]])
          await db.query(`insert into party (id,kind,name,created_at,created_by,updated_by)
            values ($1,'person','Synthetic merge fixture',$2,$3,$3)`, [id, date, actor.id]);
        for (const [n, id] of [[1, survivor], [2, loser], [3, unrelated]])
          await db.query(`insert into client (id,party_id,roster_ref,created_by,updated_by)
            values ($1,$2,$3,$4,$4)`, [role(n), id, `C-90000${n}`, actor.id]);
        if (committed) await db.query("commit");
        await fn();
      } finally {
        await db.query("rollback");
        if (committed) await db.query(`truncate ${tables.join(", ")}`);
      }
    }
    async function activity(refs) {
      await db.query(`insert into activity (occurred_at,actor_id,kind,summary,client_id,lead_id,vendor_id)
        values (now(),$1,'note','Synthetic activity',$2,$3,$4)`, [actor.id, ...refs]);
    }
    const merge = extra => TOOLS["confirm-merge"].handler(db, actor, { ...options, ...extra });

    for (const endpoint of ["from", "to", "via"]) {
      await t.test(`graph attachments on the losing ${endpoint} endpoint refuse without stranding refs`, () => fixture(async () => {
        // The loser can be either graph end or the third-party broker.
        const ends = endpoint === "from" ? [loser, unrelated, survivor]
          : endpoint === "to" ? [unrelated, loser, survivor] : [survivor, unrelated, loser];
        await db.query(`insert into party_link (from_party,to_party,via_party,kind,note,source,created_by)
          values ($1,$2,$3,'knows','Synthetic graph evidence','synthetic',$4)`, [...ends, actor.id]);
        await activity([role(1), null, null]);
        const before = (await db.query("select * from v_party_graph order by from_ref,to_ref")).rows;
        const links = (await db.query("select * from party_link order by id")).rows;
        assert.ok(before.every(edge => edge.from_ref && edge.to_ref));
        await assert.rejects(() => executeRegisteredTool(db, actor, "confirm-merge", options),
          error => error.payload?.error === "merge_graph_attachments_require_resolution"
            && error.payload.party_id === loser && error.payload.count === 1);
        assert.deepEqual((await db.query("select * from v_party_graph order by from_ref,to_ref")).rows, before);
        assert.deepEqual((await db.query("select * from party_link order by id")).rows, links);
        assert.equal((await db.query("select party_id from client where id=$1", [role(2)])).rows[0].party_id, loser);
        assert.equal((await db.query("select merged_into from party where id=$1", [loser])).rows[0].merged_into, null);
        assert.equal(Number((await db.query("select count(*) from event")).rows[0].count), 0);
        assert.equal(Number((await db.query("select count(*) from tool_call")).rows[0].count), 0);
      }));
    }

    for (const retiredSide of ["survivor", "loser"]) {
      await t.test(`a sequential merge refuses a retired UUID ${retiredSide}`, () => fixture(async () => {
        await db.query("delete from client");
        assert.equal((await merge({ survivor_party: survivor, merged_party: loser })).ok, true);
        await assert.rejects(() => merge({
          idempotency_key: "synthetic-retired-endpoint",
          survivor_party: retiredSide === "survivor" ? loser : unrelated,
          merged_party: retiredSide === "survivor" ? unrelated : loser,
        }), error => error.payload?.error === "party_already_merged");
        assert.equal((await db.query("select merged_into from party where id=$1", [unrelated])).rows[0].merged_into, null);
        assert.equal((await db.query("select merged_into from party where id=$1", [loser])).rows[0].merged_into, survivor);
        assert.equal(Number((await db.query("select count(*) from event where verb='confirm-merge'")).rows[0].count), 1);
      }));
    }

    await t.test("overlapping reverse merges cannot retire both endpoints", { timeout: 10000 }, () => fixture(async () => {
      const first = new pg.Client({ host: socket, port, user: "carr_fixture", database: "postgres" });
      const second = new pg.Client({ host: socket, port, user: "carr_fixture", database: "postgres" });
      await first.connect();
      await second.connect();
      let resume;
      const paused = new Promise(resolve => { resume = resolve; });
      let metricsRead;
      const ready = new Promise(resolve => { metricsRead = resolve; });
      const wrapped = { query: async (sql, params) => {
        const result = await first.query(sql, params);
        if (sql.includes("merge_survivorship")) { metricsRead(); await paused; }
        return result;
      } };
      let firstCall, secondCall;
      try {
        await first.query("begin");
        await second.query("begin");
        const pid = (await second.query("select pg_backend_pid() as pid")).rows[0].pid;
        firstCall = executeRegisteredTool(wrapped, actor, "confirm-merge", options);
        await ready;
        // Change the winner after T1 read its score, exactly as in the review.
        await second.query(`insert into activity (occurred_at,actor_id,kind,summary,client_id)
          values (now(),$1,'note','Synthetic interleaving activity',$2)`, [actor.id, role(2)]);
        let settled = false;
        secondCall = executeRegisteredTool(second, actor, "confirm-merge", {
          ...options, idempotency_key: "synthetic-reverse-merge",
          survivor_party: loser, merged_party: survivor,
        }).then(result => ({ result }), error => ({ error })).then(outcome => { settled = true; return outcome; });
        // Observe the PostgreSQL wait, rather than assuming a sleep means blocked.
        const deadline = Date.now() + 3000;
        let waiting = false;
        while (!settled && Date.now() < deadline) {
          waiting = (await db.query("select wait_event_type from pg_stat_activity where pid=$1", [pid]))
            .rows[0]?.wait_event_type === "Lock";
          if (waiting) break;
          await new Promise(resolve => setTimeout(resolve, 10));
        }
        assert.equal(waiting, true, "reverse merge must wait for the first merge's endpoint locks");
        resume();
        assert.equal((await firstCall).ok, true);
        await first.query("commit");
        const reverse = await secondCall;
        assert.equal(reverse.error?.payload?.error, "party_already_merged");
        await second.query("rollback");
        const rows = (await db.query("select id,merged_into from party order by id")).rows;
        assert.equal(rows.find(row => row.id === survivor).merged_into, null);
        assert.equal(rows.find(row => row.id === loser).merged_into, survivor);
        assert.equal(Number((await db.query("select count(*) from event where verb='confirm-merge'")).rows[0].count), 1);
      } finally {
        resume();
        if (firstCall) await firstCall.catch(() => {});
        await first.query("rollback");
        if (secondCall) await secondCall;
        await second.query("rollback");
        await first.end();
        await second.end();
      }
    }, { committed: true }));

    await t.test("client refs merge and keep activity attached through moved roles", () => fixture(async () => {
      await activity([role(1), null, null]);
      await activity([role(1), null, null]);
      await activity([role(2), null, null]);
      await activity([role(3), null, null]);
      const result = await executeRegisteredTool(db, actor, "confirm-merge", options);
      assert.equal(result.ok, true);
      assert.deepEqual(result.roles_moved, { client: 1 });
      assert.equal(result.orphan_sweep.find(row => row.attachment === "activity").count, 1);
      const event = (await db.query("select new_value from event where verb='confirm-merge'")).rows[0].new_value;
      assert.equal(event.survivorship.linked_records, 2);
      assert.equal(event.match_basis, options.match_basis);
      assert.equal((await db.query("select merged_into from party where id=$1", [loser])).rows[0].merged_into, survivor);
      assert.equal(Number((await db.query(`select count(*) from activity a join client c on c.id=a.client_id
        where c.party_id=$1`, [survivor])).rows[0].count), 3);
    }));

    await t.test("activity corroboration can override oldest-row tie-break", () => fixture(async () => {
      await activity([role(2), null, null]);
      await assert.rejects(() => merge(), error => error.payload?.error === "wrong_merge_survivor"
        && error.payload.required_survivor === loser);
      assert.equal((await db.query("select merged_into from party where id=$1", [loser])).rows[0].merged_into, null);
      assert.equal((await merge({ survivor_party: "C-900002", merged_party: "C-900001" })).ok, true);
    }));

    await t.test("lead/vendor activities count once even when one activity carries several role FKs", () => fixture(async () => {
      for (const [n, id] of [[4, survivor], [5, loser]]) {
        await db.query(`insert into lead (id,party_id,stage,created_by,updated_by)
          values ($1,$2,'new',$3,$3)`, [role(n), id, actor.id]);
        await db.query(`insert into vendor (id,party_id,category,created_by,updated_by)
          values ($1,$2,'synthetic',$3,$3)`, [role(n + 2), id, actor.id]);
      }
      await activity([role(1), role(4), role(6)]);
      await activity([null, role(4), null]);
      await activity([null, null, role(6)]);
      await activity([role(2), role(5), role(7)]);
      const result = await merge();
      assert.equal(result.orphan_sweep.find(row => row.attachment === "activity").count, 1);
      assert.equal((await db.query("select new_value from event")).rows[0].new_value.survivorship.linked_records, 3);
      assert.deepEqual(result.roles_moved, { lead: 1, client: 1, vendor: 1 });
    }));

    await t.test("a short match basis is still refused before any merge", () => fixture(async () => {
      await assert.rejects(() => merge({ match_basis: "name" }), error => error.payload?.error === "match_basis_required");
      assert.equal(Number((await db.query("select count(*) from party where merged_into is not null")).rows[0].count), 0);
    }));
  } finally {
    try {
      if (db) await db.end();
      if (startAttempted) run("pg_ctl", ["-D", data, "-m", "fast", "-w", "stop"]);
      const staged = path.join(out, "_to_delete");
      mkdirSync(staged, { recursive: true });
      renameSync(cluster, path.join(staged, path.basename(cluster)));
    } finally {
      await releaseBudget();
    }
  }
});
