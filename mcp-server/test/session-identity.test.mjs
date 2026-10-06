// WR-000117 — the session-identity read pair's acceptance proofs.
//
// EVERY IDENTITY CASE MINTS TWO ACTORS. Both verbs derive the acting actor
// server-side, so a case that mints ONE actor and asserts ONE answer passes
// under an implementation that ignores the derivation entirely. A single-actor
// case is a false pass by construction, and there is not one in this file.
//
// EVERY FIXTURE TIMESTAMP IS RELATIVE TO now(). work_state separates idle from
// disconnected by an age, and a hard-coded instant passes for months and fails
// on one run.
//
// The store cases skip in the unit class; the migration class supplies
// DATABASE_URL and sets CARR_SESSION_IDENTITY_DB_REQUIRED=1, which turns a skip
// into a failure.

import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

import { sessionIdentityTools, sessionIdentityProjection, sessionDispatchProjection }
  from "../src/session-identity.js";
import { TOOLS } from "../src/tools.js";
import { PROFILES } from "../src/mcp.js";

const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_SESSION_IDENTITY_DB_REQUIRED === "1";
const LOOPBACK = /@(localhost|127[.]0[.]0[.]1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;

class ToolError extends Error {
  constructor(fields) { super(fields.error); Object.assign(this, fields); }
}

const tools = sessionIdentityTools({ ToolError });

async function skipUnlessDatabase(t) {
  if (!DSN) {
    assert.equal(REQUIRED, false, "this proof was required and no database URL was given to it");
    t.skip("the migration class supplies DATABASE_URL");
    return null;
  }
  assert.ok(LOOPBACK.test(DSN),
    "REFUSED: this proof seeds four session books and runs against a disposable loopback only");
  return (await import("pg")).default ?? (await import("pg"));
}

const wrap = client => ({ query: (text, values = []) => client.query(text, values) });

/**
 * ONE fixture, TWO actors, rolled back whole. The four books stamp updated_at
 * from BEFORE triggers, so the ageing rows are seeded with the session's
 * replication role relaxed and every READ below runs with it restored --
 * nothing the FUNCTIONS do is measured under a relaxed session.
 */
async function seeded(pg, fn) {
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  const tag = randomUUID().slice(0, 8);
  const alpha = `si-${tag}-alpha`;
  const beta = `si-${tag}-beta`;
  try {
    await client.query("begin");
    const actors = await client.query(
      `insert into public.actor(slug, kind, display_name, active)
       values ($1,'automation','WR117 alpha',true), ($2,'automation','WR117 beta',true)
       returning id, slug`, [alpha, beta]);
    const idOf = slug => actors.rows.find(row => row.slug === slug).id;
    const requests = await client.query(
      `insert into ops.work_request(ref, title, requester_actor)
       values ($1,'WR117 node proof alpha',$3), ($2,'WR117 node proof beta',$3)
       returning id, ref`,
      [`WR-117-NODE-A-${tag}`, `WR-117-NODE-B-${tag}`, alpha]);
    await client.query("set local session_replication_role = replica");
    await client.query(
      `insert into public.claude_continuity_leaf
        (organization_tenant_id, surface_principal_actor_id, owner_actor_id, session_id,
         transcript_path_digest, project_affinity, parent_session_id, native_agent_id,
         latest_cwd, latest_model_id, created_at, updated_at)
       values
        ('wr117',$1,$1,$3,repeat('a',64),'wr117-project',$4,'agent-child','/wr117','claude-opus-5',
         now() - interval '2 minutes', now() - interval '2 minutes'),
        ('wr117',$1,$1,$4,repeat('b',64),'wr117-project',null,'agent-root','/wr117','claude-opus-5',
         now() - interval '45 minutes', now() - interval '45 minutes'),
        ('wr117',$2,$2,$5,repeat('c',64),'wr117-project',null,'agent-beta','/wr117','claude-opus-5',
         now() - interval '3 days', now() - interval '3 days')`,
      [idOf(alpha), idOf(beta), `${tag}-claude-child`, `${tag}-claude-root`, `${tag}-claude-beta`]);
    await client.query(
      `insert into public.codex_continuity_checkpoint
        (organization_tenant_id, owner_actor_id, native_task_id, project_id, cwd, state,
         created_at, updated_at)
       values ('wr117',$1,$3,'wr117-project','/wr117','{}',
               now() - interval '5 minutes', now() - interval '5 minutes'),
              ('wr117',$2,$4,'wr117-project','/wr117','{}',
               now() - interval '5 minutes', now() - interval '5 minutes')`,
      [idOf(alpha), idOf(beta), `${tag}-codex-alpha`, `${tag}-codex-beta`]);
    const sessions = await client.query(
      `insert into ops.capability_agent_session
        (work_request_id, executor_actor_id, created_by_actor_id, state, source_commit_sha,
         worktree_ref, started_at, updated_at)
       values ($1,$3,$3,'in_progress',repeat('1',40),$5,
               now() - interval '4 minutes', now() - interval '4 minutes'),
              ($2,$4,$4,'in_progress',repeat('2',40),$6,
               now() - interval '4 minutes', now() - interval '4 minutes')
       returning id, worktree_ref`,
      [requests.rows[0].id, requests.rows[1].id, idOf(alpha), idOf(beta),
        `${tag}-alpha-tree`, `${tag}-beta-tree`]);
    await client.query(
      `insert into public.session_work(id, kind, title, last_seen)
       values ($1,'worktree','wr117 harvested worktree', now() - interval '1 minute')`,
      [`worktree:${tag}-harvested`]);
    await client.query(
      `insert into public.partner_room_turn(room_id, sponsor, seat, kind, body, msg_id,
                                            origin_channel, origin_actor, at)
       values ('model-room','joe',$2,'turn',$3,gen_random_uuid(),'mcp',$1, now() - interval '30 minutes'),
              ('model-room','joe',$2,'turn',$4,gen_random_uuid(),'mcp',$1, now() - interval '10 minutes'),
              ('model-room','joe','si-outsider','turn',$5,gen_random_uuid(),'mcp','si-outsider',
               now() - interval '20 minutes')`,
      [alpha, beta,
        `dispatch one for ${tag}-claude-child`,
        `dispatch two for ${tag}-claude-child, superseding the first`,
        `a turn naming ${tag}-claude-child that neither actor sent`]);
    await client.query("set local session_replication_role = origin");
    const acting = async slug => {
      await client.query("select set_config('carr.acting_actor_slug',$1,true)", [slug]);
    };
    await fn({
      client, c: wrap(client), tag, alpha, beta, acting,
      capabilityAlpha: sessions.rows[0].id,
      requestAlpha: requests.rows[0].id,
    });
  } finally {
    await client.query("rollback").catch(() => {});
    await client.end();
  }
}

// ---------------------------------------------------------------------------
// AC-SI-REGISTRY — the verb surface, proved without a database.
// ---------------------------------------------------------------------------

test("AC-SI-REGISTRY: both verbs are registered as READS on the writer connection", () => {
  for (const name of ["read-session-identity", "read-dispatch-history"]) {
    assert.ok(TOOLS[name], `${name} is not registered`);
    assert.equal(TOOLS[name].write, undefined,
      `${name} is a read; a write flag would open a writable transaction`);
    assert.equal(TOOLS[name].writerConnection, true,
      `${name} resolves the caller's actor from a context only the writer path installs`);
    assert.equal(TOOLS[name].authorityOnly, undefined,
      "the Control Room calls this as the signed-in partner, who holds no authority binding");
    for (const profile of ["capture", "away"]) {
      assert.ok(!PROFILES[profile].has(name), `${name} must not be in the ${profile} profile`);
    }
  }
});

test("AC-SI-IDENTITY: the schema accepts no actor property", () => {
  const identity = TOOLS["read-session-identity"].inputSchema;
  assert.equal(identity.additionalProperties, false,
    "an open schema makes the server-side derivation forgeable rather than absent");
  assert.deepEqual(Object.keys(identity.properties).sort(),
    ["include_closed", "limit", "query"]);
  assert.deepEqual(identity.required, []);
  const dispatch = TOOLS["read-dispatch-history"].inputSchema;
  assert.equal(dispatch.additionalProperties, false);
  assert.deepEqual(Object.keys(dispatch.properties).sort(),
    ["cursor", "limit", "session_id"]);
  assert.deepEqual(dispatch.required, ["session_id"]);
  for (const schema of [identity, dispatch]) {
    for (const forbidden of ["actor", "actor_id", "acting_actor", "owner", "on_behalf_of"]) {
      assert.equal(Object.hasOwn(schema.properties, forbidden), false,
        `${forbidden} would let a caller name the acting actor`);
    }
  }
});

test("the shapers refuse anything that is not the definer function's own shape", () => {
  assert.throws(() => sessionIdentityProjection({ sessions: [] }, ToolError),
    /session_identity_unavailable/);
  assert.throws(() => sessionIdentityProjection(null, ToolError),
    /session_identity_unavailable/);
  assert.throws(() => sessionDispatchProjection({ ok: false, reason_id: "dispatch_cursor_invalid" },
    ToolError), /dispatch_cursor_invalid/);
});

test("AC-SI-REGISTRY: is_write_action classifies both verbs as non-writes, with the gate unedited",
  async () => {
    const { readFileSync } = await import("node:fs");
    const gate = readFileSync(
      new URL("../../hooks/completion-evidence-gate.py", import.meta.url), "utf8");
    const literals = name => {
      const start = gate.indexOf(`${name} = {`);
      assert.ok(start >= 0, `${name} is missing from the completion-evidence gate`);
      const body = gate.slice(start, gate.indexOf("\n}", start));
      return new Set([...body.matchAll(/"([a-z0-9-]+)"/g)].map(match => match[1]));
    };
    const prefixes = literals("WRITE_ACTION_PREFIXES");
    const exact = literals("WRITE_ACTION_EXACT");
    // hooks/completion-evidence-gate.py's own classifier, replayed.
    const isWriteAction = action => exact.has(action) || prefixes.has(action.split("-")[0]);

    assert.equal(isWriteAction("read-session-identity"), false);
    assert.equal(isWriteAction("read-dispatch-history"), false);
    assert.ok(!prefixes.has("read"),
      "`read` is not a write prefix, which is why no gate entry is owed");
    for (const name of ["read-session-identity", "read-dispatch-history"]) {
      assert.ok(!exact.has(name),
        "a redundant exact entry would teach the next reader the opposite rule");
    }
  });

// ---------------------------------------------------------------------------
// AC-SI-IDENTITY
// ---------------------------------------------------------------------------

test("AC-SI-IDENTITY: a Claude leaf resolves to canonical id, parent and native host", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  await seeded(pg, async ({ c, tag, alpha, acting }) => {
    await acting(alpha);
    const answer = await tools["read-session-identity"].handler(c, null, { query: tag, limit: 50 });
    const child = answer.sessions.find(
      row => row.canonical_session_id === `${tag}-claude-child`);
    assert.ok(child, "the Claude leaf did not resolve");
    assert.equal(child.surface, "claude");
    assert.equal(child.parent_session_id, `${tag}-claude-root`);
    assert.equal(child.parent_known, true);
    assert.equal(child.native_host_id, "agent-child");
    assert.equal(child.native_host_supported, true);
    assert.equal(child.observation_source, "continuity_event");
    assert.equal(child.alias_source, "derived",
      "no relation stores a human alias, so no row may claim one");
    assert.equal(child.work_state, "working");
    assert.equal(child.attempt_count, 2, "the root and the child are one attempt group");
    assert.ok(child.work_state_evidence.includes("continuity_event"),
      "the state must carry the observation it came from");
    const root = answer.sessions.find(row => row.canonical_session_id === `${tag}-claude-root`);
    assert.equal(root.work_state, "idle",
      "a forty-five-minute-old row is idle, measured against now() and not a literal");
  });
});

test("AC-SI-IDENTITY: a Codex checkpoint resolves with parent_known false, not parent null pretending to be a root",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    await seeded(pg, async ({ c, tag, alpha, acting }) => {
      await acting(alpha);
      const answer = await tools["read-session-identity"].handler(c, null, { query: tag, limit: 50 });
      const codex = answer.sessions.find(
        row => row.canonical_session_id === `${tag}-codex-alpha`);
      assert.ok(codex, "the Codex checkpoint did not resolve");
      assert.equal(codex.surface, "codex");
      assert.equal(codex.parent_session_id, null);
      assert.equal(codex.parent_known, false,
        "the Codex book has no parent column, so a null parent means UNRECORDED");
      assert.equal(codex.observation_source, "checkpoint");
      // THE DISCRIMINATING COMPARISON: a Claude ROOT also has a null parent and
      // it is KNOWN to be null. A plain non-null test collapses these two.
      const root = answer.sessions.find(row => row.canonical_session_id === `${tag}-claude-root`);
      assert.equal(root.parent_session_id, null);
      assert.equal(root.parent_known, true);
      assert.notEqual(codex.parent_known, root.parent_known,
        "an unrecorded parent and a root must not be the same answer");
    });
  });

test("AC-SI-IDENTITY: a harvested worktree row resolves with observation_source harvest and never claims liveness",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    await seeded(pg, async ({ c, tag, alpha, acting }) => {
      await acting(alpha);
      const answer = await tools["read-session-identity"].handler(c, null, { query: tag, limit: 50 });
      const harvested = answer.sessions.find(
        row => row.canonical_session_id === `worktree:${tag}-harvested`);
      assert.ok(harvested, "the harvested row did not resolve");
      assert.equal(harvested.surface, "harvested");
      assert.equal(harvested.observation_source, "harvest");
      // ONE MINUTE OLD AND STILL `unknown`: the harvest stamps its own run time
      // and is not scheduled, so its clock is not the session's.
      assert.equal(harvested.work_state, "unknown",
        "a harvested row may never claim a work state however fresh its timestamp looks");
      assert.match(harvested.work_state_evidence, /not scheduled/);
      assert.equal(harvested.parent_known, false);
      assert.equal(harvested.native_host_supported, false);
    });
  });

test("AC-SI-IDENTITY: two actors, one query shape, two different answers", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  await seeded(pg, async ({ c, tag, alpha, beta, acting }) => {
    const args = { query: tag, limit: 50 };
    await acting(alpha);
    const first = await tools["read-session-identity"].handler(c, null, args);
    await acting(beta);
    const second = await tools["read-session-identity"].handler(c, null, args);
    assert.notDeepEqual(first.sessions, second.sessions,
      "the same arguments under two actors must not produce one answer");
    const keys = answer => answer.sessions.map(row => row.canonical_session_id).sort();
    assert.ok(keys(first).includes(`${tag}-claude-child`));
    assert.ok(!keys(second).includes(`${tag}-claude-child`));
    assert.ok(keys(second).includes(`${tag}-claude-beta`));
    assert.ok(!keys(first).includes(`${tag}-claude-beta`));
    // The second actor's own oldest row exercises the far end of the age rule.
    const stale = second.sessions.find(row => row.canonical_session_id === `${tag}-claude-beta`);
    assert.equal(stale.work_state, "disconnected");
  });
});

// ---------------------------------------------------------------------------
// AC-SI-FILTER
// ---------------------------------------------------------------------------

test("AC-SI-FILTER: rows the acting actor may not see are absent AND permission_filtered is true",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    await seeded(pg, async ({ c, tag, alpha, acting, capabilityAlpha }) => {
      await acting(alpha);
      const answer = await tools["read-session-identity"].handler(c, null, { query: tag, limit: 50 });
      const keys = answer.sessions.map(row => row.canonical_session_id);
      // PER BOOK, not once over the union: each of the three owned books has a
      // different owner column and a union filtered once leaks the branch whose
      // owner column is absent.
      assert.ok(keys.includes(`${tag}-claude-child`) && !keys.includes(`${tag}-claude-beta`));
      assert.ok(keys.includes(`${tag}-codex-alpha`) && !keys.includes(`${tag}-codex-beta`));
      assert.ok(keys.includes(capabilityAlpha));
      assert.equal(answer.permission_filtered, true);
      assert.ok(answer.total_seen > answer.total_returned,
        "the filter must be reported as a count comparison, not as a shorter list");
      assert.equal(answer.total_returned, answer.sessions.length);
    });
  });

test("AC-SI-FILTER: an actor who may see nothing gets an empty list with permission_filtered true, never an empty system",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    await seeded(pg, async ({ c, tag, beta, acting }) => {
      await acting(beta);
      // The other actor's session id, typed as a query. It is a SEARCH STRING
      // and never an identity claim, so it authorises nothing.
      const answer = await tools["read-session-identity"].handler(
        c, null, { query: `${tag}-claude-child`, limit: 50 });
      assert.deepEqual(answer.sessions, []);
      assert.equal(answer.permission_filtered, true,
        "an empty filtered answer must be distinguishable from an empty system");
      assert.ok(answer.total_seen > 0);
      assert.equal(answer.total_returned, 0);
    });
  });

// ---------------------------------------------------------------------------
// AC-SI-DISPATCH
// ---------------------------------------------------------------------------

test("AC-SI-DISPATCH: sent and acted carry the row that proves them", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  await seeded(pg, async ({ c, tag, alpha, acting, capabilityAlpha, requestAlpha }) => {
    await acting(alpha);
    const sent = await tools["read-dispatch-history"].handler(
      c, null, { session_id: `${tag}-claude-child`, limit: 50 });
    assert.ok(sent.events.length >= 2);
    for (const event of sent.events) {
      assert.equal(event.stage, "sent");
      assert.match(event.stage_evidence, /^public\.partner_room_turn id \d+ msg_id /);
      assert.equal(event.room_id, "model-room");
    }
    const acted = await tools["read-dispatch-history"].handler(
      c, null, { session_id: capabilityAlpha, limit: 50 });
    const transition = acted.events.find(event => event.stage === "acted");
    assert.ok(transition, "the capability session's own transition did not resolve");
    assert.match(transition.stage_evidence, /column started_at/);
    assert.equal(transition.work_request_ref, requestAlpha);
    assert.equal(transition.attempt_ref, capabilityAlpha);
  });
});

test("AC-SI-DISPATCH: acknowledged and received are null with stage_unavailable_reason no_dispatch_spine",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    await seeded(pg, async ({ c, tag, alpha, beta, acting }) => {
      for (const actor of [alpha, beta]) {
        await acting(actor);
        const answer = await tools["read-dispatch-history"].handler(
          c, null, { session_id: `${tag}-claude-child`, limit: 50 });
        assert.equal(answer.received, null);
        assert.equal(answer.acknowledged, null);
        assert.equal(answer.stage_unavailable_reason, "no_dispatch_spine",
          "an inferred acknowledgement is the conflation V5-UX-C13 clause 1 forbids");
        for (const event of answer.events) {
          assert.ok(["sent", "acted"].includes(event.stage),
            `${event.stage} is a stage this substrate cannot prove`);
        }
      }
    });
  });

test("AC-SI-DISPATCH: a superseded instruction is marked superseded_by and is not returned as current",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    await seeded(pg, async ({ c, tag, alpha, acting }) => {
      await acting(alpha);
      const answer = await tools["read-dispatch-history"].handler(
        c, null, { session_id: `${tag}-claude-child`, limit: 50 });
      const first = answer.events.find(event => event.rationale.startsWith("dispatch one"));
      const second = answer.events.find(event => event.rationale.startsWith("dispatch two"));
      assert.ok(first && second);
      assert.ok(first.superseded_by,
        "an instruction a later turn replaced must carry what replaced it");
      assert.equal(second.superseded_by, null, "the newest instruction is the current one");
      // The superseded row is RETURNED, marked, rather than hidden: a filter
      // that dropped it would leave the reader unable to see the trail at all.
      assert.equal(answer.events.indexOf(second) < answer.events.indexOf(first), true,
        "the answer is newest first");
    });
  });

test("AC-SI-DISPATCH: the cursor is opaque and a stale cursor neither skips nor repeats", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  await seeded(pg, async ({ c, tag, alpha, acting }) => {
    await acting(alpha);
    const whole = await tools["read-dispatch-history"].handler(
      c, null, { session_id: `${tag}-claude-child`, limit: 50 });
    const pageOne = await tools["read-dispatch-history"].handler(
      c, null, { session_id: `${tag}-claude-child`, limit: 1 });
    assert.equal(pageOne.events.length, 1);
    assert.equal(pageOne.more, true);
    assert.ok(pageOne.next_cursor, "a page with more rows must mint a cursor");
    // OPAQUE: it is the server's own token, not a readable offset.
    assert.doesNotMatch(pageOne.next_cursor, /^\d+$/);
    const pageTwo = await tools["read-dispatch-history"].handler(
      c, null, { session_id: `${tag}-claude-child`, limit: 1, cursor: pageOne.next_cursor });
    assert.equal(pageTwo.events.length, 1);
    assert.notEqual(pageTwo.events[0].event_id, pageOne.events[0].event_id,
      "the cursor repeated a row");
    const walked = [pageOne.events[0].event_id, pageTwo.events[0].event_id];
    assert.deepEqual(walked, whole.events.slice(0, 2).map(event => event.event_id),
      "the cursor skipped a row the unpaged answer returns");
    await assert.rejects(
      () => tools["read-dispatch-history"].handler(
        c, null, { session_id: `${tag}-claude-child`, cursor: "not-a-token", limit: 50 }),
      /dispatch_cursor_invalid/);
  });
});
