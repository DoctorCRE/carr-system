import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import pg from "pg";
import { acquireDisposablePostgres } from "./helpers/disposable-postgres.ts";

const registryPath = "../src/tools.js";
const registry: unknown = await import(registryPath);
assert.ok(typeof registry === "object" && registry !== null && "executeRegisteredTool" in registry);
const invoke = registry.executeRegisteredTool;
assert.ok(typeof invoke === "function");
function object(value: unknown): Record<string, unknown> {
  assert.ok(typeof value === "object" && value !== null && !Array.isArray(value));
  return Object.fromEntries(Object.entries(value));
}
function objects(value: unknown) {
  assert.ok(Array.isArray(value));
  return value.map(object);
}
async function executeRegisteredTool(client: object, actor: object, verb: string, args: object) {
  assert.ok(typeof invoke === "function");
  return object(await invoke(client, actor, verb, args));
}
function cardsFor(result: Record<string, unknown>, session: unknown) {
  const call = objects(result.calls).find((call) => call.session_id === session);
  assert.ok(call);
  return objects(call.open_cards);
}

const pairs = objects(
  JSON.parse(readFileSync(new URL("./fixtures/post-call-action-pairs.json", import.meta.url), "utf8")),
).map((pair) => {
  const { id, proposed, existing, duplicate } = pair;
  assert.ok(
    typeof id === "string" &&
      typeof proposed === "string" &&
      typeof existing === "string" &&
      typeof duplicate === "boolean",
  );
  return { id, proposed, existing, duplicate };
});

test("labeled action pairs return neutral deal context through read-call-outcomes", async (t) => {
  const fixture = await acquireDisposablePostgres({
    prefix: "call-action-pairs-",
    snapshot: true,
    pgCtl: undefined,
    sourceTemplate: undefined,
  });
  const c = new pg.Client(fixture.connection);
  await c.connect();
  try {
    const actor = { ...(await c.query("select id,slug from actor where slug='joe'")).rows[0], human: true };
    const party = (
      await c.query(
        "insert into party(kind,name,created_by,updated_by) values('person','Synthetic pair physician',$1,$1) returning id",
        [actor.id],
      )
    ).rows[0].id;
    await c.query(
      "insert into client_status(slug,label) values('fixture_active','Synthetic active') on conflict do nothing",
    );
    const client = (
      await c.query(
        "insert into client(party_id,status,created_by,updated_by) values($1,'fixture_active',$2,$2) returning id",
        [party, actor.id],
      )
    ).rows[0].id;
    const session = (
      await c.query(
        `insert into capture_session(nonce,device_id,actor_id,mode,started_at,consent_announced_at,
      session_token_hash,expires_at,state,state_at,post_call) values($1,'fixture',$2,'meeting',now(),now(),$1,
      now()+interval '1 day','distilling',now(),true) returning id`,
        [randomUUID(), actor.id],
      )
    ).rows[0].id;
    const actions = new Map();
    for (const [index, pair] of pairs.entries()) {
      const deal = (
        await c.query(
          "insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by) values($1,$2,'lease','site_selection','joe',$3,$3) returning id",
          [`Synthetic ${pair.id}`, client, actor.id],
        )
      ).rows[0].id;
      await c.query(
        `insert into capture_post_call_candidate
        (session_id,idempotency_key,batch_hash,item_index,kind,deal_id,assignee_slug,action_description,evidence_quote,confidence)
        values($1,$2,$2,$3,'assigned_action',$4,'joe',$5,'Synthetic pair evidence',1)`,
        [session, randomUUID(), index, deal, pair.proposed],
      );
      actions.set(
        pair.id,
        (
          await c.query(
            `insert into next_action(subject_type,subject_id,owner_id,description,status,created_by)
        values('deal',$1,$2,$3,'done',$2) returning id`,
            [deal, actor.id, pair.existing],
          )
        ).rows[0].id,
      );
    }
    for (const status of ["done", "open", "dropped"]) {
      await c.query("update next_action set status=$1 where id=any($2::uuid[])", [status, [...actions.values()]]);
      const before = (
        await c.query("select * from next_action where id=any($1::uuid[]) order by id", [[...actions.values()]])
      ).rows;
      const result = await executeRegisteredTool(c, actor, "read-call-outcomes", {});
      const cards = cardsFor(result, session);
      assert.equal(cards.length, pairs.length);
      for (const pair of pairs) {
        const card = cards.find((card) => object(card.payload).action === pair.proposed);
        assert.ok(card);
        const comparison = object(card.existing_action_comparison);
        assert.equal(comparison.schema_version, "post-call-action-comparison.v2");
        assert.equal(comparison.status, status === "open" ? "related_open_tasks" : "no_match");
        assert.equal(comparison.exact_match, null);
        assert.equal(comparison.date_effect, null);
        assert.equal("possible_matches" in comparison, false);
        assert.deepEqual(
          objects(comparison.related_open_tasks).map((action) => action.id),
          status === "open" ? [actions.get(pair.id)] : [],
        );
        t.diagnostic(`${pair.id} gold=${pair.duplicate} history=${status} observed=${comparison.status}`);
      }
      assert.deepEqual(
        (await c.query("select * from next_action where id=any($1::uuid[]) order by id", [[...actions.values()]])).rows,
        before,
      );
    }
    await assert.rejects(
      () =>
        executeRegisteredTool(
          {
            query(sql: string, args: unknown[]) {
              if (sql.includes("capture:preview-related-open")) throw new Error("Synthetic comparison unavailable");
              return c.query(sql, args);
            },
          },
          actor,
          "read-call-outcomes",
          {},
        ),
      /Synthetic comparison unavailable/,
    );
    const unavailable = await executeRegisteredTool(
      {
        query(sql: string, args: unknown[]) {
          return sql.includes("capture:preview-assignee") ? { rows: [{ id: null }] } : c.query(sql, args);
        },
      },
      actor,
      "read-call-outcomes",
      {},
    );
    const unavailableCards = cardsFor(unavailable, session);
    assert.equal(unavailableCards.length, pairs.length);
    assert.ok(
      unavailableCards.every((card) => object(card.existing_action_comparison).status === "assignee_unavailable"),
    );
  } finally {
    await c.end();
    await fixture.close();
  }
});
