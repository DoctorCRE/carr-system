import assert from "node:assert/strict";
import { type TestContext, test } from "node:test";
import { Pool } from "@neondatabase/serverless";
import { publicError } from "../src/public-errors.ts";
import { NOW } from "./helpers/fixed-clock.ts";
import { derivedIdempotencyKey, executeRegisteredTool, record, ToolError } from "./helpers/legacy-test-registry.ts";

const { callTool } = record(await import(new URL("../src/mcp.js", import.meta.url).href));
const identity = record(await import(new URL("../src/identity.js", import.meta.url).href));
const { connectionForGrant } = record(identity.authenticatedIdentity);
assert.ok(typeof callTool === "function");

const deal = "01000000-0000-4000-8000-000000000001";
const savedId = "01000000-0000-4000-8000-000000000002";
const key = "01000000-0000-4000-8000-000000000003";
const agent = {
  id: "01000000-0000-4000-8000-000000000004",
  slug: "codex",
  human: false,
  sponsoring_human_slug: "dell",
  native_agent_verified: true,
};
const activity = {
  idempotency_key: key,
  ref: deal,
  kind: "call",
  summary: "Reviewed the specimen",
  detail: "Follow up on the measurements.",
  occurred_at: "2026-10-10T15:13:27-05:00",
};

class LegacyReached extends Error {}

function legacy({ writes = false, subject = "deal", found = true, matches = 1 } = {}) {
  const changes: string[] = [];
  return {
    changes,
    async query(sql: string) {
      if (sql.includes("lower(ref)=lower($2)"))
        return { rows: Array.from({ length: found ? matches : 0 }, () => ({ subject_id: deal })) };
      if (sql.includes("v_ref_index")) return { rows: found ? [{ subject_type: subject, subject_id: deal }] : [] };
      if (writes && sql.includes("select w.ref from ops.future_work_link")) return { rows: [{ ref: "WR-901" }] };
      if (writes && sql.startsWith("select ")) return { rows: [] };
      if (writes && /^(insert |update next_action)/.test(sql)) {
        changes.push(sql);
        return { rows: [{ id: savedId }] };
      }
      throw new LegacyReached(sql);
    },
  };
}

function engine(t: TestContext, respond: (body: unknown, path: string) => Response, enabled = "true") {
  const calls: {
    path: string;
    headers: Headers;
    method: string | undefined;
    redirect: RequestRedirect | undefined;
    body: unknown;
  }[] = [];
  t.mock.method(globalThis, "fetch", async (url: URL, request: RequestInit) => {
    const { headers, method, redirect, body } = request;
    assert.ok(typeof body === "string");
    const parsed: unknown = JSON.parse(body);
    calls.push({ path: url.pathname, headers: new Headers(headers), method, redirect, body: parsed });
    return respond(parsed, url.pathname);
  });
  return {
    calls,
    env: {
      ENGINE_ASSISTANT: enabled,
      ENGINE_URL: "https://engine.example.test/",
      ENGINE_ACCESS_CLIENT_ID: "example-access-client",
      ENGINE_ACCESS_CLIENT_SECRET: "example-access-value",
    },
  };
}

test("off saves the activity through the legacy handler", async (t) => {
  const upstream = engine(t, () => assert.fail("off made an engine request"), "false");
  const c = legacy({ writes: true });
  const reply = record(await executeRegisteredTool(c, agent, "log-activity", { ...activity }, upstream.env));
  assert.equal(reply.activity_id, savedId);
  assert.ok(c.changes.some((sql) => sql.startsWith("insert into activity")));
  assert.equal(upstream.calls.length, 0);
});

test("deal activity retries a committed save without changing its time, key, or payload", async (t) => {
  const receipts = new Map<string, unknown>();
  const upstream = engine(t, (request) => {
    assert.ok(typeof request === "object" && request && "idempotency_key" in request);
    assert.ok(typeof request.idempotency_key === "string");
    if (!receipts.has(request.idempotency_key)) {
      receipts.set(request.idempotency_key, request);
      return Response.json({ error: "database_unavailable" }, { status: 503 });
    }
    assert.deepEqual(request, receipts.get(request.idempotency_key));
    return Response.json({ activity_id: savedId, replayed: true }, { status: 201 });
  });
  const c = legacy();
  for (let i = 0; i < 2; i++) {
    const reply = await executeRegisteredTool(c, agent, "log-activity", { ...activity }, upstream.env);
    assert.deepEqual(reply, { ok: true, subject: { type: "deal", id: deal }, activity_id: savedId, replayed: true });
  }
  const [request] = upstream.calls;
  assert.ok(request);
  assert.equal(upstream.calls.length, 3);
  assert.equal(receipts.size, 1);
  assert.equal(request.path, "/jobs/activities");
  assert.equal(request.method, "POST");
  assert.equal(request.redirect, "error");
  assert.equal(request.headers.get("Engine-Job"), "agent");
  assert.equal(request.headers.get("Engine-For-User"), "dell");
  assert.equal(request.headers.get("CF-Access-Client-Id"), upstream.env.ENGINE_ACCESS_CLIENT_ID);
  assert.equal(request.headers.get("CF-Access-Client-Secret"), upstream.env.ENGINE_ACCESS_CLIENT_SECRET);
  assert.deepEqual(request.body, {
    idempotency_key: key,
    kind: "call",
    happened_at: "2026-10-10T15:13:27-05:00",
    summary: "Reviewed the specimen\n\nFollow up on the measurements.",
    about: [{ deal_id: deal }],
  });
});

test("a final refusal never retries or writes to the legacy database", async (t) => {
  const upstream = engine(t, () =>
    Response.json({ error: "unknown_job", hint: "untrusted response" }, { status: 403 }),
  );
  await assert.rejects(executeRegisteredTool(legacy(), agent, "log-activity", { ...activity }, upstream.env), {
    payload: { error: "engine_assistant_refused", engine_status: 403, engine_error: "unknown_job" },
  });
  assert.equal(upstream.calls.length, 1);
});

test("sets a deal step using the atomic replacement and explicit owner contract", async (t) => {
  const upstream = engine(t, () => Response.json({ next_step_id: savedId, change_id: 7 }, { status: 201 }));
  const reply = await executeRegisteredTool(
    legacy(),
    agent,
    "set-next-action",
    {
      idempotency_key: key,
      ref: deal,
      description: "Send sample measurements",
      owner: "joe",
    },
    upstream.env,
  );
  assert.deepEqual(reply, {
    ok: true,
    next_action_id: savedId,
    subject: { type: "deal", id: deal },
    owner: "joe",
  });
  const [request] = upstream.calls;
  assert.ok(request);
  assert.equal(request.path, "/jobs/next-steps");
  assert.deepEqual(request.body, {
    idempotency_key: key,
    deal_id: deal,
    what: "Send sample measurements",
    owner: "joe",
    replaces: "current",
  });
});

test("unsupported activity does not spill into the engine", async (t) => {
  const upstream = engine(t, () => assert.fail("unmoved activity made an engine request"));
  const c = legacy({ writes: true });
  const { occurred_at, ...withoutTime } = activity;
  const reply = record(
    await executeRegisteredTool(
      c,
      agent,
      "log-activity",
      { ...withoutTime, human_quote: "Keep this verbatim." },
      upstream.env,
    ),
  );
  assert.equal(reply.activity_id, savedId);
  assert.ok(c.changes.some((sql) => sql.startsWith("insert into activity")));
  assert.equal(upstream.calls.length, 0);
});

function worker(t: TestContext, query: (sql: string) => Promise<unknown> = legacy().query) {
  assert.ok(typeof connectionForGrant === "function");
  t.mock.method(Pool.prototype, "connect", async () => ({
    async query(sql: string) {
      if (sql.startsWith("select id from actor")) return { rows: [{ id: agent.id }] };
      if (sql.includes("session_user::text"))
        return {
          rows: [
            {
              session_principal: "carr_writer",
              current_principal: "carr_writer",
              privilege_bundle: "carr_writer",
              member_carr_writer: true,
              member_carr_jobs: false,
              member_carr_authority: false,
              backend_pid: 9,
            },
          ],
        };
      if (/^(begin|commit|rollback)|set_config\(/.test(sql)) return { rows: [] };
      return query(sql);
    },
    release() {},
  }));
  t.mock.method(Pool.prototype, "end", async () => {});
  return connectionForGrant({ slug: "dell" });
}

test("call-verb uses the partner grant and lets the engine default the owner", async (t) => {
  const upstream = engine(t, () => Response.json({ next_step_id: savedId }, { status: 201 }));
  const actor: unknown = worker(t);
  assert.ok(actor);
  const env = { ...upstream.env, DATABASE_URL_WRITER: "postgres://fixture@synthetic.invalid/fixture" };
  const result = record(
    await callTool(env, actor, "call-verb", {
      verb: "set-next-action",
      args: { idempotency_key: key, ref: deal, description: "Send sample measurements", due_on: "2026-10-20" },
    }),
  );
  assert.equal(result.next_action_id, savedId);
  assert.equal(result.owner, "dell");
  const [request] = upstream.calls;
  assert.ok(request);
  assert.equal(request.headers.get("Engine-For-User"), "dell");
  assert.deepEqual(request.body, {
    idempotency_key: key,
    deal_id: deal,
    what: "Send sample measurements",
    replaces: "current",
    due_on: "2026-10-20",
  });
});

test("untimed deal calls, meetings and notes let the engine assign the first save time", async (t) => {
  let attempts = 0;
  const upstream = engine(t, () =>
    ++attempts % 2
      ? Response.json({ error: "database_unavailable" }, { status: 503 })
      : Response.json({ activity_id: savedId, replayed: true }, { status: 201 }),
  );
  const { occurred_at, ...untimed } = activity;
  for (const [index, kind] of ["call", "meeting", "note"].entries()) {
    const idempotency_key = `01000000-0000-4000-8000-00000000001${index}`;
    const reply = record(
      await executeRegisteredTool(legacy(), agent, "log-activity", { ...untimed, kind, idempotency_key }, upstream.env),
    );
    assert.equal(reply.activity_id, savedId);
    assert.equal(reply.replayed, true);
    const expected = {
      idempotency_key,
      kind,
      about: [{ deal_id: deal }],
      summary: "Reviewed the specimen\n\nFollow up on the measurements.",
    };
    for (const request of upstream.calls.slice(index * 2, index * 2 + 2)) {
      assert.equal(request.path, "/jobs/activities");
      assert.deepEqual(request.body, expected);
    }
  }
  assert.equal(upstream.calls.length, 6);
});

test("completes the sponsor's current step by deal or party and preserves its replay", async (t) => {
  const upstream = engine(t, () =>
    Response.json({
      next_step_id: savedId,
      status: "done",
      replayed: true,
    }),
  );
  for (const type of ["deal", "party"]) {
    const reply = await executeRegisteredTool(
      legacy({ subject: type }),
      agent,
      "complete-action",
      { idempotency_key: key, ref: deal, outcome: "  " },
      upstream.env,
    );
    assert.deepEqual(reply, {
      ok: true,
      subject: { type, id: deal },
      count: 1,
      completed: [{ next_action_id: savedId }],
      replayed: true,
    });
  }
  assert.deepEqual(
    upstream.calls.map(({ path, body, headers }) => [path, body, headers.get("Engine-For-User")]),
    [
      ["/jobs/next-steps/complete", { idempotency_key: key, deal_id: deal }, "dell"],
      ["/jobs/next-steps/complete", { idempotency_key: key, party_id: deal }, "dell"],
    ],
  );
});

test("a completion refusal is final and does not fall through to legacy writes", async (t) => {
  const upstream = engine(t, () => Response.json({ error: "no_open_step" }, { status: 409 }));
  await assert.rejects(
    executeRegisteredTool(
      legacy(),
      agent,
      "complete-action",
      { idempotency_key: key, ref: deal, outcome: "The example survey was sent." },
      upstream.env,
    ),
    {
      payload: { error: "engine_assistant_refused", engine_status: 409, engine_error: "no_open_step" },
    },
  );
  assert.equal(upstream.calls.length, 1);
  assert.equal(upstream.calls[0]?.path, "/jobs/next-steps/complete");
});

for (const type of ["deal", "party"]) {
  test(`a ${type} completion replays and saves its outcome after three note failures`, async (t) => {
    const noteId = "01000000-0000-4000-8000-000000000005";
    let closed = false;
    let noteAttempts = 0;
    const upstream = engine(t, (_, path) => {
      if (path === "/jobs/next-steps/complete") {
        const replayed = closed;
        closed = true;
        return Response.json({ next_step_id: savedId, status: "done", replayed });
      }
      assert.equal(path, "/jobs/activities");
      return ++noteAttempts <= 3
        ? Response.json({ error: "database_unavailable" }, { status: 503 })
        : Response.json({ activity_id: noteId }, { status: 201 });
    });
    const c = legacy({ subject: type });
    const summary = "The example survey was sent.";
    const args = { idempotency_key: key, ref: deal, outcome: `  ${summary}  ` };
    const complete = () => executeRegisteredTool(c, agent, "complete-action", { ...args }, upstream.env);
    await assert.rejects(complete(), { payload: { error: "engine_assistant_unavailable" } });
    assert.equal(noteAttempts, 3);
    assert.deepEqual(await complete(), {
      ok: true,
      subject: { type, id: deal },
      count: 1,
      completed: [{ next_action_id: savedId }],
      replayed: true,
    });
    const target = { [`${type}_id`]: deal };
    const completion = ["/jobs/next-steps/complete", { idempotency_key: key, ...target }];
    const note = [
      "/jobs/activities",
      {
        idempotency_key: derivedIdempotencyKey("complete-action-outcome", key),
        kind: "note",
        about: [target],
        summary,
      },
    ];
    assert.deepEqual(
      upstream.calls.map(({ path, body }) => [path, body]),
      [completion, note, note, note, completion, note],
    );
  });
}

const copiedParty = "01000000-0000-4000-8000-000000000019";
const roleRefs = { lead: "L-901", client: "C-901", vendor: "V-SAMPLE-901" };

for (const [subject, ref] of [...Object.entries(roleRefs), ["vendor", "T-SAMPLE-901"]]) {
  test(`saves and completes the current step for ${ref} using its old role ID`, async (t) => {
    const receipts = new Map<string, unknown>();
    const upstream = engine(t, (body, path) => {
      if (!receipts.has(path)) {
        receipts.set(path, body);
        return Response.json({ error: "database_unavailable" }, { status: 503 });
      }
      assert.deepEqual(body, receipts.get(path));
      return Response.json({ next_step_id: savedId, party_id: copiedParty, replayed: true });
    });
    const { env } = upstream;
    const c = legacy({ subject });
    const args = { idempotency_key: key, ref };
    const old_role = { kind: subject, id: deal };
    const step = { ...args, description: "Send sample measurements", due_on: "2026-10-20" };
    const done = { ...args, idempotency_key: "01000000-0000-4000-8000-000000000006" };

    const saved = record(await executeRegisteredTool(c, agent, "set-next-action", step, env));
    assert.equal(saved.next_action_id, savedId);
    assert.equal(saved.replayed, true);
    const completed = record(await executeRegisteredTool(c, agent, "complete-action", done, env));
    assert.deepEqual(completed.completed, [{ next_action_id: savedId }]);
    assert.equal(completed.replayed, true);
    assert.deepEqual(await executeRegisteredTool(c, agent, "complete-action", done, env), completed);
    assert.deepEqual(
      [...receipts],
      [
        [
          "/jobs/next-steps",
          { idempotency_key: key, old_role, what: step.description, due_on: step.due_on, replaces: "current" },
        ],
        ["/jobs/next-steps/complete", { idempotency_key: done.idempotency_key, old_role }],
      ],
    );
    assert.equal(upstream.calls.length, 5);
    assert.deepEqual(c.changes, []);
  });
}

test("switch off saves and completes a role step through the legacy handler", async (t) => {
  const upstream = engine(t, () => assert.fail("switch off reached the engine"), "false");
  const c = legacy({ subject: "client", writes: true });
  const { client: ref } = roleRefs;
  const step = { idempotency_key: key, ref, description: "Send sample measurements" };
  assert.equal(
    record(await executeRegisteredTool(c, agent, "set-next-action", step, upstream.env)).next_action_id,
    savedId,
  );
  const completed = record(
    await executeRegisteredTool(c, agent, "complete-action", { idempotency_key: key, ref }, upstream.env),
  );
  assert.equal(completed.count, 1);
  assert.ok(c.changes.some((sql) => sql.startsWith("insert into next_action")));
  assert.ok(c.changes.some((sql) => sql.includes("update next_action set status='done'")));
  assert.equal(upstream.calls.length, 0);
});

test("missing or ambiguous role refs never fall through to name search", async (t) => {
  const upstream = engine(t, () => assert.fail("unresolved ref reached the engine"));
  for (const [ref, matches] of [
    ["T-SAMPLE-901", 0],
    [roleRefs.lead, 2],
  ] as const) {
    const c = legacy({ subject: "lead", matches, writes: true });
    await assert.rejects(
      executeRegisteredTool(c, agent, "complete-action", { idempotency_key: key, ref }, upstream.env),
      { payload: { error: "engine_assistant_party_unmapped" } },
    );
    assert.deepEqual(c.changes, []);
  }
  assert.equal(upstream.calls.length, 0);
});

test("a role step with an unknown owner leaves both stores untouched", async (t) => {
  const upstream = engine(t, () => assert.fail("unknown owner reached the engine"));
  const c = legacy({ subject: "client", writes: true });
  const queries: string[] = [];
  const db = {
    query: (sql: string) => {
      queries.push(sql);
      return c.query(sql);
    },
  };
  await assert.rejects(
    executeRegisteredTool(
      db,
      agent,
      "set-next-action",
      {
        idempotency_key: key,
        ref: roleRefs.client,
        description: "Send sample measurements",
        owner: "someone-else",
      },
      upstream.env,
    ),
    (error: unknown) => error instanceof ToolError && error.payload.error === "owner_not_permitted",
  );
  assert.ok(
    queries.every((sql) => sql.includes("v_ref_index")),
    "only the role lookup may run",
  );
  assert.deepEqual(c.changes, []);
  assert.equal(upstream.calls.length, 0);
});

test("an old role missing from the Engine copy is a final refusal", async (t) => {
  const upstream = engine(t, () => Response.json({ error: "old_role_not_found" }, { status: 404 }));
  const c = legacy({ subject: "vendor", writes: true });
  const args = { idempotency_key: key, ref: roleRefs.vendor, description: "Send sample measurements" };
  await assert.rejects(executeRegisteredTool(c, agent, "set-next-action", args, upstream.env), {
    payload: { error: "engine_assistant_refused", engine_status: 404, engine_error: "old_role_not_found" },
  });
  assert.equal(upstream.calls.length, 1);
  assert.deepEqual(c.changes, []);
});

test("log-outreach refuses with the Engine on and points to the routed verbs", async (t) => {
  const upstream = engine(t, () => assert.fail("outreach reached the engine"));
  const c = legacy({ subject: "client", writes: true });
  await assert.rejects(
    executeRegisteredTool(
      c,
      agent,
      "log-outreach",
      {
        idempotency_key: key,
        ref: roleRefs.client,
        channel: "call",
        outcome: "connected",
        summary: "Sample outreach",
      },
      upstream.env,
    ),
    (error: unknown) => {
      const response = publicError(error);
      assert.equal(response.error, "engine_assistant_outreach_unavailable");
      assert.match(response.hint, /complete-action.*log-activity/);
      return true;
    },
  );
  assert.deepEqual(c.changes, []);
  assert.equal(upstream.calls.length, 0);
});

test("the Worker files a client outcome on the party returned by the Engine", async (t) => {
  const upstream = engine(t, (_, path) =>
    Response.json(
      path === "/jobs/activities"
        ? { activity_id: savedId }
        : { next_step_id: savedId, status: "done", party_id: copiedParty },
    ),
  );
  const actor: unknown = worker(t, legacy({ subject: "client" }).query);
  const env = {
    ...upstream.env,
    DATABASE_URL_WRITER: "postgres://fixture@synthetic.invalid/fixture",
  };
  const outcome = "The sample was sent.";
  const reply = record(
    await callTool(env, actor, "call-verb", {
      verb: "complete-action",
      args: { idempotency_key: key, ref: roleRefs.client, outcome },
    }),
  );
  assert.equal(reply.count, 1);
  assert.deepEqual(
    upstream.calls.map(({ path, body }) => [path, body]),
    [
      ["/jobs/next-steps/complete", { idempotency_key: key, old_role: { kind: "client", id: deal } }],
      [
        "/jobs/activities",
        {
          idempotency_key: derivedIdempotencyKey("complete-action-outcome", key),
          kind: "note",
          about: [{ party_id: copiedParty }],
          summary: outcome,
        },
      ],
    ],
  );
});

test("with the Engine on, email step confirmation directs the partner to Home without writing", async (t) => {
  const upstream = engine(t, () => assert.fail("email step confirmation reached the Engine"));
  const c = { query: t.mock.fn(() => assert.fail("email step confirmation reached the legacy database")) };
  const joe = { id: agent.id, slug: "joe", human: true };
  const args = {
    activity_id: savedId,
    evidence_quote: "Send the sample by Friday",
    text: "Send the sample",
    next_date: "2026-11-20",
    idempotency_key: key,
    proposal_digest: `sha256:${"a".repeat(64)}`,
  };

  for (const target of [{}, { deal }]) {
    await assert.rejects(
      executeRegisteredTool(c, joe, "confirm-email-next-step", { ...args, ...target }, upstream.env),
      (error: unknown) => {
        const response = publicError(error);
        assert.equal(response.error, "engine_assistant_email_step_on_home");
        assert.equal(response.hint, "The partner accepts this suggestion on Home.");
        return true;
      },
    );
  }
  assert.equal(c.query.mock.callCount(), 0);
  assert.equal(upstream.calls.length, 0);
});

test("the Worker files a deal note and Home review without running legacy after-commit work", async (t) => {
  const upstream = engine(t, () =>
    Response.json(
      {
        activity_id: savedId,
        proposal_id: key,
        next_step_required: false,
      },
      { status: 201 },
    ),
  );
  const c = legacy();
  const reads: string[] = [];
  const actor: unknown = worker(t, (sql) => {
    reads.push(sql);
    return c.query(sql);
  });
  const background: Promise<unknown>[] = [];
  const env = {
    ...upstream.env,
    DATABASE_URL_WRITER: "postgres://fixture@synthetic.invalid/fixture",
    ctx: { waitUntil: (task: Promise<unknown>) => background.push(task) },
  };
  const reply = await callTool(env, actor, "call-verb", {
    verb: "add-deal-note",
    args: { idempotency_key: key, deal, text: "  The example survey was sent.  " },
  });
  await Promise.all(background);
  assert.deepEqual(reply, {
    ok: true,
    deal_id: deal,
    note_id: savedId,
    activity_id: savedId,
    proposal_id: key,
    next_step_required: false,
  });
  assert.equal(reads.length, 1);
  assert.match(reads[0] ?? "", /v_ref_index/);
  assert.equal(upstream.calls.length, 1);
  assert.equal(upstream.calls[0]?.path, "/jobs/activities/deal-note");
  assert.deepEqual(upstream.calls[0]?.body, {
    idempotency_key: key,
    deal_id: deal,
    text: "The example survey was sent.",
  });
});

test("a deal note and replacement plan replay as a single engine save", async (t) => {
  let attempts = 0;
  const upstream = engine(t, () =>
    Response.json(
      ++attempts === 1
        ? { activity_id: savedId, next_step_required: false }
        : { activity_id: savedId, next_step_id: key, next_step_required: false, replayed: true },
      { status: 201 },
    ),
  );
  const reply = await executeRegisteredTool(
    legacy(),
    agent,
    "add-deal-note",
    {
      idempotency_key: key,
      deal,
      text: "The example survey was sent.",
      next_step: "  Review it  ",
      next_date: "2026-10-20",
    },
    upstream.env,
  );
  assert.deepEqual(reply, {
    ok: true,
    deal_id: deal,
    note_id: savedId,
    activity_id: savedId,
    next_step_id: key,
    next_action_id: key,
    next_step_required: false,
    replayed: true,
  });
  assert.equal(upstream.calls.length, 2);
  for (const { path, body } of upstream.calls) {
    assert.equal(path, "/jobs/activities/deal-note");
    assert.deepEqual(body, {
      idempotency_key: key,
      deal_id: deal,
      text: "The example survey was sent.",
      plan: { what: "Review it", due_on: "2026-10-20" },
    });
  }
});

test("a deal note surfaced without a plan date asks for a next step", async (t) => {
  const upstream = engine(t, () =>
    Response.json(
      {
        activity_id: savedId,
        proposal_id: null,
        next_step_required: true,
      },
      { status: 201 },
    ),
  );
  const reply = record(
    await executeRegisteredTool(
      legacy(),
      agent,
      "add-deal-note",
      { idempotency_key: key, deal, text: "The example survey was sent." },
      upstream.env,
    ),
  );
  assert.equal(reply.proposal_id, null);
  assert.equal(reply.next_step_required, true);
  assert.equal(upstream.calls.length, 1);
});

test("a partial deal note plan is refused before either save path", async (t) => {
  const upstream = engine(t, () => assert.fail("incomplete plan made an engine request"));
  await assert.rejects(
    executeRegisteredTool(
      legacy(),
      agent,
      "add-deal-note",
      {
        idempotency_key: key,
        deal,
        text: "The example survey was sent.",
        next_step: "Review it",
      },
      upstream.env,
    ),
    (error: unknown) => error instanceof ToolError && error.payload.error === "next_step_and_date_required",
  );
  assert.equal(upstream.calls.length, 0);
});

test("off leaves deal note and completion execution on the legacy path", async (t) => {
  const upstream = engine(t, () => assert.fail("off made an engine request"), "false");
  const args = { idempotency_key: key };
  for (const [verb, fields] of [
    ["complete-action", { ref: deal }],
    ["add-deal-note", { deal, text: "The example survey was sent." }],
  ] as const)
    await assert.rejects(
      executeRegisteredTool(legacy(), agent, verb, { ...args, ...fields }, upstream.env),
      LegacyReached,
    );
  assert.equal(upstream.calls.length, 0);
});

test("a future activity is still refused before either write path", async (t) => {
  const upstream = engine(t, () => assert.fail("future activity made an engine request"));
  const occurred_at = new Date(NOW + 60 * 60 * 1000).toISOString();
  await assert.rejects(
    executeRegisteredTool(legacy(), agent, "log-activity", { ...activity, occurred_at }, upstream.env),
    (error: unknown) => error instanceof ToolError && error.payload.error === "occurred_at_in_future",
  );
  assert.equal(upstream.calls.length, 0);
});

test("saves the engine cannot take yet stay on the legacy handler", async (t) => {
  const step = { idempotency_key: key, ref: deal, description: "Send sample measurements" };
  const link = { from_ref: "L-001", to_ref: "C-001", kind: "intro" };
  const assignment = { vendor_id: savedId, base_version: 1, assignment_base_version: 1 };
  const cases = [
    { name: "client activity", subject: "client", verb: "log-activity", args: activity },
    { name: "human quote", verb: "log-activity", args: { ...activity, human_quote: "Send it over." } },
    { name: "links", verb: "log-activity", args: { ...activity, links: [link] } },
    { name: "vendor roles", verb: "log-activity", args: { ...activity, vendor_assignments: [assignment] } },
    { name: "conditional step", verb: "set-next-action", args: { ...step, expected_action: null } },
    { name: "agent-owned step", verb: "set-next-action", args: { ...step, owner: agent.slug } },
    { name: "unpermitted owner", verb: "set-next-action", args: { ...step, owner: "someone-else" } },
    {
      name: "non-partner caller",
      actor: { ...agent, native_agent_verified: false },
      verb: "log-activity",
      args: activity,
    },
  ];
  const upstream = engine(t, () => Response.json({ activity_id: savedId, next_step_id: savedId }, { status: 201 }));
  for (const { name, subject, actor = agent, verb, args } of cases)
    await assert.rejects(
      executeRegisteredTool(legacy({ subject }), actor, verb, { ...args }, upstream.env),
      LegacyReached,
      name,
    );
  assert.equal(upstream.calls.length, 0);
});

test("owed information stays in the activity summary", async (t) => {
  const upstream = engine(t, () => Response.json({ activity_id: savedId }, { status: 201 }));
  await executeRegisteredTool(legacy(), agent, "log-activity", { ...activity, owed: "The asking rent" }, upstream.env);
  const [request] = upstream.calls;
  assert.ok(request);
  assert.equal(
    record(request.body).summary,
    "Reviewed the specimen\n\nFollow up on the measurements.\n\nInformation still missing\nThe asking rent",
  );
});

test("an ownerless step from a sponsored agent belongs to its partner", async (t) => {
  const upstream = engine(t, () => Response.json({ next_step_id: savedId }, { status: 201 }));
  const reply = record(
    await executeRegisteredTool(
      legacy(),
      agent,
      "set-next-action",
      { idempotency_key: key, ref: deal, description: "Send sample measurements" },
      upstream.env,
    ),
  );
  assert.equal(reply.owner, "dell");
  const [request] = upstream.calls;
  assert.ok(request);
  assert.equal(request.headers.get("Engine-For-User"), "dell");
  assert.equal(Object.hasOwn(record(request.body), "owner"), false);
});

test("a success without an id is retried with the same key", async (t) => {
  let replies = 0;
  const upstream = engine(t, () =>
    Response.json(replies++ ? { activity_id: savedId, replayed: true } : {}, { status: 201 }),
  );
  const reply = record(await executeRegisteredTool(legacy(), agent, "log-activity", { ...activity }, upstream.env));
  assert.equal(reply.activity_id, savedId);
  assert.equal(upstream.calls.length, 2);
  assert.deepEqual(upstream.calls[0]?.body, upstream.calls[1]?.body);
});

test("three server failures end as unavailable without a legacy write", async (t) => {
  const upstream = engine(t, () => Response.json({ error: "database_unavailable" }, { status: 503 }));
  await assert.rejects(executeRegisteredTool(legacy(), agent, "log-activity", { ...activity }, upstream.env), {
    payload: { error: "engine_assistant_unavailable" },
  });
  assert.equal(upstream.calls.length, 3);
});

test("the caller sees the engine's refusal code", async (t) => {
  t.mock.method(console, "error", () => {});
  const refuse = (error: string) => engine(t, () => Response.json({ error }, { status: 409 })).env;
  const refusal = async (error: string) =>
    publicError(
      await executeRegisteredTool(legacy(), agent, "log-activity", { ...activity }, refuse(error)).catch(
        (thrown: unknown) => thrown,
      ),
    );
  const projected = await refusal("open_step_exists");
  assert.equal(projected.error, "engine_assistant_refused");
  assert.equal(projected.engine_error, "open_step_exists");
  assert.equal(Object.hasOwn(await refusal("Not A Slug"), "engine_error"), false);
});

const invoice = {
  idempotency_key: key,
  native_ref: "local-mail:synthetic-invoice",
  from_address: "invoice@example.test",
  deal_name: "Synthetic Lease",
  occurred_at: "2026-10-03T23:30:00-05:00",
  property_address: "  ",
};
const mailboxPartner = { id: agent.id, slug: "dell", human: true };
const localMailbox = { ...agent, slug: "dell-local", via: "local-token" };

test("invoice capture off writes the legacy invoice with its local date", async (t) => {
  const upstream = engine(t, () => assert.fail("off made an engine request"), "false");
  const c = legacy({ writes: true });
  const db = {
    async query(sql: string, params?: unknown[]) {
      if (sql === "select now() as now") return { rows: [{ now: new Date(NOW) }] };
      if (sql.startsWith("insert into deal_invoice_email")) assert.equal(params?.[6], "2026-10-03");
      return c.query(sql);
    },
  };
  const reply = await executeRegisteredTool(db, mailboxPartner, "record-deal-invoice", { ...invoice }, upstream.env);
  assert.deepEqual(reply, { ok: true, invoice_id: savedId, evidence_ref: invoice.native_ref });
  assert.ok(c.changes.some((sql) => sql.startsWith("insert into deal_invoice_email")));
  assert.equal(upstream.calls.length, 0);
});

test("invoice capture retries with a key derived from the caller's save", async (t) => {
  let attempts = 0;
  const upstream = engine(t, () => {
    const attempt = attempts++;
    return attempt === 0
      ? Response.json({ error: "database_unavailable" }, { status: 503 })
      : Response.json({
          invoice_id: savedId,
          evidence_ref: invoice.native_ref,
          ...(attempt === 1 && { replayed: true }),
        });
  });
  for (const [actor, idempotency_key, receipt] of [
    [mailboxPartner, key, { replayed: true }],
    [localMailbox, savedId, {}],
  ] as const) {
    const reply = await executeRegisteredTool(
      legacy(),
      actor,
      "record-deal-invoice",
      {
        ...invoice,
        idempotency_key,
      },
      upstream.env,
    );
    assert.deepEqual(reply, { ok: true, invoice_id: savedId, evidence_ref: invoice.native_ref, ...receipt });
  }
  assert.equal(upstream.calls.length, 3);
  for (const [index, request] of upstream.calls.entries()) {
    assert.equal(request.method, "POST");
    assert.equal(request.path, "/jobs/invoice-emails");
    assert.equal(request.headers.get("Engine-Job"), "mail_capture");
    assert.equal(request.headers.get("Engine-For-User"), mailboxPartner.slug);
    assert.deepEqual(request.body, {
      idempotency_key: derivedIdempotencyKey("record-deal-invoice", index < 2 ? key : savedId),
      evidence_ref: invoice.native_ref,
      sender: invoice.from_address,
      deal_name: invoice.deal_name,
      occurred_at: invoice.occurred_at,
      client_name: null,
      property_address: null,
    });
  }
});

test("invoice capture does not accept a receipt for different evidence", async (t) => {
  const upstream = engine(t, () => Response.json({ invoice_id: savedId, evidence_ref: "local-mail:other-sample" }));
  await assert.rejects(
    executeRegisteredTool(legacy(), mailboxPartner, "record-deal-invoice", { ...invoice }, upstream.env),
    {
      payload: { error: "engine_assistant_refused", engine_status: 200 },
    },
  );
  assert.equal(upstream.calls.length, 1);
});

test("invoice capture preserves the engine's key_reuse refusal", async (t) => {
  const upstream = engine(t, () => Response.json({ error: "key_reuse" }, { status: 409 }));
  await assert.rejects(
    executeRegisteredTool(legacy(), mailboxPartner, "record-deal-invoice", { ...invoice }, upstream.env),
    { payload: { error: "key_reuse", engine_status: 409, engine_error: "key_reuse" } },
  );
  assert.equal(upstream.calls.length, 1);
});

test("invoice capture refuses callers without a mailbox and never falls back on an engine refusal", async (t) => {
  const upstream = engine(t, () => Response.json({ error: "database_refused_the_statement" }, { status: 422 }));
  await assert.rejects(executeRegisteredTool(legacy(), agent, "record-deal-invoice", { ...invoice }, upstream.env), {
    payload: { error: "invoice_mailbox_identity_required" },
  });
  assert.equal(upstream.calls.length, 0);
  await assert.rejects(
    executeRegisteredTool(legacy(), mailboxPartner, "record-deal-invoice", { ...invoice }, upstream.env),
    {
      payload: {
        error: "engine_assistant_refused",
        engine_status: 422,
        engine_error: "database_refused_the_statement",
      },
    },
  );
  assert.equal(upstream.calls.length, 1);
});

test("switched-on Worker never applies or undoes old invoices directly", async (t) => {
  const upstream = engine(t, () => assert.fail("legacy close requested an engine save"));
  const db = {
    async query(sql: string) {
      assert.doesNotMatch(sql, /deal_invoice_email/);
      if (sql === "select now() as now") return { rows: [{ now: new Date(NOW) }] };
      return { rows: [] };
    },
  };
  const result = record(
    await executeRegisteredTool(
      db,
      mailboxPartner,
      "advance-leads",
      {
        idempotency_key: key,
        engine_invoice_closes: false,
      },
      upstream.env,
    ),
  );
  assert.deepEqual(result.invoice_closes, []);
  await assert.rejects(
    executeRegisteredTool(
      db,
      mailboxPartner,
      "undo-invoice-close",
      {
        idempotency_key: key,
        invoice_id: savedId,
        base_version: 2,
      },
      upstream.env,
    ),
    { payload: { error: "invoice_undo_requires_engine_session" } },
  );
});
