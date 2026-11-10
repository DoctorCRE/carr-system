import assert from "node:assert/strict";
import { test } from "node:test";
import { derivedIdempotencyKey } from "../src/derived-key.ts";
import { ToolError } from "../src/tool-error.ts";

const registered: unknown = await import(new URL("../src/tools.js", import.meta.url).href);
assert.ok(registered && typeof registered === "object" && "TOOLS" in registered);
const registry = registered.TOOLS;
assert.ok(registry && typeof registry === "object");
assert.ok("add-loop" in registry);
const addLoopHandlerTool = registry["add-loop"];
assert.ok(
  addLoopHandlerTool &&
    typeof addLoopHandlerTool === "object" &&
    "handler" in addLoopHandlerTool &&
    typeof addLoopHandlerTool.handler === "function",
);
const addLoopHandlerLegacy = addLoopHandlerTool.handler;
const addLoopHandler = async (client: object, actor: object, args: Record<string, unknown>): Promise<unknown> => {
  const result: unknown = await addLoopHandlerLegacy(client, actor, args);
  return result;
};
assert.ok("update-loop" in registry);
const updateLoopHandlerTool = registry["update-loop"];
assert.ok(
  updateLoopHandlerTool &&
    typeof updateLoopHandlerTool === "object" &&
    "handler" in updateLoopHandlerTool &&
    typeof updateLoopHandlerTool.handler === "function",
);
const updateLoopHandlerLegacy = updateLoopHandlerTool.handler;
const updateLoopHandler = async (client: object, actor: object, args: Record<string, unknown>): Promise<unknown> => {
  const result: unknown = await updateLoopHandlerLegacy(client, actor, args);
  return result;
};

// loop-residue-vocabulary.test.ts — the residue gate on add-loop and
// update-loop (Joe 2026-10-12, decision cbe91287).
//
// On 2026-10-12 the governance queue counted 150 loops labelled as waiting on
// Joe, median age 53 days; most were internal engineering steps any session
// may take. Joe's ruling: only an act a provider or the OS reserves to the
// account holder may wait on him. A write that claims human_only, ruling or
// capability must therefore name that act (residue_kind, closed list) and the
// provider or host that reserves it (residue_host), or it is refused with a
// reason that points internal work at system_decides.
//
// Run with: node --test mcp-server/test/loop-residue-vocabulary.test.ts

const ids = {
  joe: "10000000-0000-0000-0000-000000000002",
  block: "30000000-0000-0000-0000-000000000351",
  loop: "20000000-0000-0000-0000-000000000401",
};
const joe = { id: ids.joe, slug: "joe", display: "Joe", human: true, via: "mcp", client_id: "claude" };

const RESIDUE_KINDS = [
  "provider_login",
  "os_password",
  "physical_act",
  "money_movement",
  "external_send",
  "joe_only_fact",
];

class AddLoopFake {
  calls = new Map<unknown, Record<string, unknown>>();
  events: { verb: unknown; payload: string | undefined }[];
  inserts: { sql: string; params: unknown[] }[];
  constructor() {
    this.inserts = [];
    this.events = [];
  }
  async query(text: string, params: unknown[] = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) {
      const prior = this.calls.get(params[0]);
      return { rows: prior ? [prior] : [] };
    }
    if (sql.startsWith("select slug from loop_domain where slug=$1"))
      return { rows: params[0] === "system" ? [{ slug: "system" }] : [] };
    if (sql.startsWith("select id, rel_path, col_order from loop_block"))
      return params[0] === "open_loop" && params[1] === "backlog"
        ? { rows: [{ id: ids.block, rel_path: "00_Context/open-loops-backlog.md", col_order: null }] }
        : { rows: [] };
    if (sql.includes("from loop_item where kind = $1")) return { rows: [{ m: 400 }] };
    if (sql.startsWith("select coalesce(max(render_seq)")) return { rows: [{ n: 1 }] };
    if (sql.startsWith("insert into loop_item")) {
      this.inserts.push({ sql, params });
      return { rows: [{ id: ids.loop }] };
    }
    if (sql.startsWith("insert into event")) {
      this.events.push({
        verb: params[2],
        payload: params.find((p): p is string => typeof p === "string" && p.includes("blocker_residue")),
      });
      return { rows: [] };
    }
    if (sql.startsWith("insert into tool_call")) {
      this.calls.set(params[0], {
        request_hash: params[3],
        response: parseJson(String(params[4])),
        actor_id: params[2],
        organization_tenant_id: params[7] ?? null,
        application_session_id: params[12] ?? null,
      });
      return { rows: [] };
    }
    throw new Error(`unhandled fake query: ${sql}`);
  }
}

let n = 0;
const addLoop = (db: AddLoopFake, extra: Record<string, unknown>) =>
  addLoopHandler(db, joe, {
    idempotency_key: derivedIdempotencyKey("test", `residue-add-${++n}`),
    kind: "open_loop",
    owner: "joe",
    domain: "system",
    body: "residue gate self-test",
    ...extra,
  });

const storedExtraCells = (db: AddLoopFake) => {
  const insert = db.inserts.at(-1);
  assert.ok(insert);
  const { sql, params } = insert;
  assert.ok(sql.includes("extra_cells"), "the insert must write extra_cells");
  const cells: unknown = JSON.parse(String(params.at(-1)));
  assert.ok(cells && typeof cells === "object" && !Array.isArray(cells));
  return cells;
};

const refusedAs =
  (error: string, check: (payload: ToolError["payload"]) => void = () => {}) =>
  (err: unknown) => {
    assert.ok(err instanceof ToolError, "must be a typed refusal");
    assert.equal(err.payload.error, error);
    assert.ok(String(err.payload.hint || "").length > 20, "the refusal carries a readable reason");
    check(err.payload);
    return true;
  };

// ── add-loop: acceptance ────────────────────────────────────────────────

for (const blocker of ["human_only", "ruling", "capability"]) {
  for (const kind of RESIDUE_KINDS) {
    test(`add-loop: ${blocker} with residue ${kind} is accepted and the residue is stored`, async () => {
      const db = new AddLoopFake();
      const r = await addLoop(db, {
        blocker,
        blocker_detail: "Joe signs in to GitHub with two-factor",
        residue_kind: kind,
        residue_host: "  github.com  ",
      });
      assert.ok(r && typeof r === "object" && "ok" in r);
      assert.equal(r.ok, true);
      assert.ok("residue" in r);
      assert.deepEqual(r.residue, { kind, host: "github.com" });
      assert.deepEqual(storedExtraCells(db), {
        writer_surface: "local",
        blocker_residue: { kind, host: "github.com" },
      });
      assert.equal(db.events.length, 1);
      assert.ok(db.events[0]?.payload?.includes(`"kind":"${kind}"`), "the event keeps the claim as made");
    });
  }
}

for (const blocker of ["counterparty", "external_event", "other_lane"]) {
  test(`add-loop: ${blocker} needs no residue and stores none`, async () => {
    const db = new AddLoopFake();
    const r = await addLoop(db, { blocker, blocker_detail: "Sanders, the listing broker on C-112" });
    assert.ok(r && typeof r === "object" && "ok" in r);
    assert.equal(r.ok, true);
    assert.equal("residue" in r ? r.residue : undefined, undefined);
    assert.deepEqual(storedExtraCells(db), { writer_surface: "local" });
  });
}

test("add-loop: a kind without a blocker (idea) is untouched by the gate", async () => {
  const db = new AddLoopFake();
  db.query = (
    (orig) =>
    async (text, params = []) => {
      const sql = text.replace(/\s+/g, " ").trim();
      if (sql.startsWith("select id, rel_path, col_order from loop_block") && params[0] === "idea")
        return { rows: [{ id: ids.block, rel_path: "00_Context/idea-bank.md", col_order: null }] };
      return orig.call(db, text, params);
    }
  )(db.query);
  const r = await addLoopHandler(db, joe, {
    idempotency_key: derivedIdempotencyKey("test", `residue-idea-${++n}`),
    kind: "idea",
    owner: "joe",
    body: "a parked idea",
  });
  assert.ok(r && typeof r === "object" && "ok" in r);
  assert.equal(r.ok, true);
  assert.deepEqual(storedExtraCells(db), { writer_surface: "local" });
});

// ── add-loop: refusal ───────────────────────────────────────────────────

for (const blocker of ["human_only", "ruling", "capability"]) {
  test(`add-loop: ${blocker} without a residue is refused and points internal work at system_decides`, async () => {
    const db = new AddLoopFake();
    await assert.rejects(
      () => addLoop(db, { blocker, blocker_detail: "needs Joe to look at the PR" }),
      refusedAs("residue_required", (p) => {
        assert.deepEqual(p.residue_kinds, RESIDUE_KINDS);
        assert.equal(typeof p.hint, "string");
        assert.match(String(p.hint), /system_decides/);
      }),
    );
    assert.equal(db.inserts.length, 0, "a refused call writes nothing");
  });
}

test("add-loop: an unknown residue_kind is refused with the closed list", async () => {
  const db = new AddLoopFake();
  await assert.rejects(
    () =>
      addLoop(db, {
        blocker: "human_only",
        blocker_detail: "Joe reviews the migration plan",
        residue_kind: "code_review",
        residue_host: "github.com",
      }),
    refusedAs("unknown_residue_kind", (p) => {
      assert.equal(p.got, "code_review");
      assert.deepEqual(p.residue_kinds, RESIDUE_KINDS);
    }),
  );
  assert.equal(db.inserts.length, 0);
});

for (const host of [undefined, "", "   ", "x", 42]) {
  test(`add-loop: a residue with host ${JSON.stringify(host)} is refused`, async () => {
    const db = new AddLoopFake();
    await assert.rejects(
      () =>
        addLoop(db, {
          blocker: "capability",
          blocker_detail: "the Chase login with two-factor",
          residue_kind: "provider_login",
          residue_host: host,
        }),
      refusedAs("residue_host_required", (p) => assert.equal(p.residue_kind, "provider_login")),
    );
    assert.equal(db.inserts.length, 0);
  });
}

test("add-loop: a residue on a class that does not wait on Joe is refused, not stored inert", async () => {
  const db = new AddLoopFake();
  await assert.rejects(
    () =>
      addLoop(db, {
        blocker: "other_lane",
        blocker_detail: "the orchestrator's route repair PR",
        residue_kind: "provider_login",
        residue_host: "github.com",
      }),
    refusedAs("residue_not_carried_by_class", (p) => assert.equal(p.blocker, "other_lane")),
  );
  assert.equal(db.inserts.length, 0);
});

test("add-loop: a residue on a kind that carries no blocker is refused", async () => {
  const db = new AddLoopFake();
  await assert.rejects(
    () =>
      addLoopHandler(db, joe, {
        idempotency_key: derivedIdempotencyKey("test", `residue-team-${++n}`),
        kind: "team_loop",
        owner: "dell",
        title: "handoff",
        residue_kind: "physical_act",
        residue_host: "the site",
      }),
    refusedAs("blocker_not_carried_by_kind"),
  );
});

test("add-loop: blocker 'credential' is refused with the capability + provider_login mapping", async () => {
  const db = new AddLoopFake();
  await assert.rejects(
    () =>
      addLoop(db, {
        blocker: "credential",
        blocker_detail: "the GitHub token Joe holds",
        residue_kind: "provider_login",
        residue_host: "github.com",
      }),
    refusedAs("unknown_blocker_class", (p) => {
      assert.equal(typeof p.hint, "string");
      assert.match(String(p.hint), /capability/);
      assert.equal(typeof p.hint, "string");
      assert.match(String(p.hint), /provider_login/);
    }),
  );
});

// ── update-loop ─────────────────────────────────────────────────────────

class UpdateLoopFake {
  loop: {
    id: string;
    kind: string;
    number: string;
    status: string;
    marker: string;
    due_on: null;
    close_outcome: null;
    blocker_class: string | null;
    section: string;
    version: number;
    created_at: string;
  };
  calls = new Map<unknown, Record<string, unknown>>();
  updates: { sql: string; params: unknown[] }[];
  constructor({ blocker_class = "human_only" }: { blocker_class?: string | null } = {}) {
    this.loop = {
      id: ids.loop,
      kind: "open_loop",
      number: "476",
      status: "open",
      marker: "none",
      due_on: null,
      close_outcome: null,
      blocker_class,
      section: "backlog",
      version: 1,
      created_at: "2026-08-20T00:00:00.000Z",
    };
    this.updates = [];
  }
  async query(text: string, params: unknown[] = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) {
      const prior = this.calls.get(params[0]);
      return { rows: prior ? [prior] : [] };
    }
    if (sql.includes("from loop_item li join loop_block lb") && sql.includes("where li.id = $1"))
      return { rows: [{ ...this.loop }] };
    if (sql.startsWith("select version from loop_item where id=$1 for update"))
      return { rows: [{ version: this.loop.version }] };
    if (sql.startsWith("select created_at from loop_item where id=$1"))
      return { rows: [{ created_at: this.loop.created_at }] };
    if (sql.includes("from event e join actor a on a.id=e.actor_id")) return { rows: [] };
    if (sql.startsWith("update loop_item set")) {
      this.updates.push({ sql, params });
      return { rows: [] };
    }
    if (sql.startsWith("insert into event")) return { rows: [] };
    if (sql.startsWith("insert into tool_call")) {
      this.calls.set(params[0], {
        request_hash: params[3],
        response: parseJson(String(params[4])),
        actor_id: params[2],
        organization_tenant_id: params[7] ?? null,
        application_session_id: params[12] ?? null,
      });
      return { rows: [] };
    }
    throw new Error(`unhandled fake query: ${sql}`);
  }
}

const update = (db: UpdateLoopFake, extra: Record<string, unknown>) =>
  updateLoopHandler(db, joe, {
    idempotency_key: derivedIdempotencyKey("test", `residue-update-${++n}`),
    loop_id: ids.loop,
    base_version: 1,
    ...extra,
  });

const extraCellsAssignments = (sql: string) => sql.match(/extra_cells=/g)?.length ?? 0;

test("update-loop: reclassifying onto human_only without a residue is refused", async () => {
  const db = new UpdateLoopFake({ blocker_class: "other_lane" });
  await assert.rejects(
    () => update(db, { blocker: "human_only", blocker_detail: "Joe signs the LOI for C-112" }),
    refusedAs("residue_required"),
  );
  assert.equal(db.updates.length, 0);
});

test("update-loop: reclassifying onto ruling with a residue stores it in extra_cells", async () => {
  const db = new UpdateLoopFake({ blocker_class: "other_lane" });
  await update(db, {
    blocker: "ruling",
    blocker_detail: "whether the 3% cap applies to renewal years",
    residue_kind: "joe_only_fact",
    residue_host: "Joe's call with Sanders",
  });
  assert.ok(db.updates[0]);
  const { sql, params } = db.updates[0];
  assert.equal(extraCellsAssignments(sql), 1);
  assert.match(sql, /jsonb_build_object\('blocker_residue', \$\d+::jsonb\)/);
  assert.ok(params.includes(JSON.stringify({ kind: "joe_only_fact", host: "Joe's call with Sanders" })));
});

test("update-loop: reclassifying off a human class clears the stored residue", async () => {
  const db = new UpdateLoopFake({ blocker_class: "human_only" });
  await update(db, { blocker: "other_lane", blocker_detail: "the orchestrator's route repair PR" });
  assert.ok(db.updates[0]);
  const { sql } = db.updates[0];
  assert.match(sql, /- 'blocker_residue'/);
  assert.match(sql, /blocker_class=\$\d+/);
});

test("update-loop: a residue alone on a legacy human_only row is accepted (the backfill path)", async () => {
  const db = new UpdateLoopFake({ blocker_class: "human_only" });
  await update(db, { residue_kind: "os_password", residue_host: "macOS on Joe's MacBook" });
  assert.ok(db.updates[0]);
  const { sql, params } = db.updates[0];
  assert.match(sql, /blocker_residue/);
  assert.ok(!/blocker_class=/.test(sql), "a residue alone does not reclassify");
  assert.ok(params.includes(JSON.stringify({ kind: "os_password", host: "macOS on Joe's MacBook" })));
});

test("update-loop: a residue alone on a row whose class carries none is refused", async () => {
  const db = new UpdateLoopFake({ blocker_class: "counterparty" });
  await assert.rejects(
    () => update(db, { residue_kind: "external_send", residue_host: "gmail.com" }),
    refusedAs("residue_not_carried_by_class", (p) => assert.equal(p.blocker, "counterparty")),
  );
  assert.equal(db.updates.length, 0);
});

test("update-loop: an invalid residue alone is refused", async () => {
  const db = new UpdateLoopFake({ blocker_class: "capability" });
  await assert.rejects(() => update(db, { residue_kind: "provider_login" }), refusedAs("residue_host_required"));
  await assert.rejects(
    () => update(db, { residue_kind: "later", residue_host: "github.com" }),
    refusedAs("unknown_residue_kind"),
  );
  assert.equal(db.updates.length, 0);
});

test("update-loop: existing rows are not rewritten — an edit that does not touch the blocker needs no residue", async () => {
  const db = new UpdateLoopFake({ blocker_class: "human_only" });
  await update(db, { body: "sharpened text", blocker_detail: "Joe signs in to Chase with two-factor" });
  assert.ok(db.updates[0]);
  const { sql } = db.updates[0];
  assert.ok(!sql.includes("extra_cells"), "the stored extra_cells are left alone");
});

test("update-loop: residue and last_surfaced compose into one extra_cells assignment", async () => {
  const db = new UpdateLoopFake({ blocker_class: null });
  db.loop.kind = "idea";
  await update(db, {
    blocker: "capability",
    blocker_detail: "the Grok login with verification",
    residue_kind: "provider_login",
    residue_host: "grok.com",
    last_surfaced: "2026-10-12",
  });
  assert.ok(db.updates[0]);
  const { sql } = db.updates[0];
  assert.equal(extraCellsAssignments(sql), 1, "Postgres refuses two assignments to one column");
  assert.match(sql, /blocker_residue/);
  assert.match(sql, /last_surfaced/);
});

function parseJson(value: string): unknown {
  return JSON.parse(value);
}
