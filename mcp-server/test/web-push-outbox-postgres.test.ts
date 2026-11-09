import assert from "node:assert/strict";
import { generateKeyPairSync, randomBytes, randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { Validator } from "@cfworker/json-schema";
import pg from "pg";
import { drainPushOutbox } from "../src/web-push-outbox.ts";
import type { PushKeys } from "../src/web-push-sender.ts";
import { postgresDatabase } from "./helpers/postgres-database.mjs";

const actor = "20000000-0000-4000-8000-000000000001";
const installation = "20000000-0000-4000-8000-000000000002";
const source = "20000000-0000-4000-8000-000000000003";
const key = generateKeyPairSync("ec", { namedCurve: "prime256v1" }).privateKey.export({ format: "jwk" });
const publicKey = Buffer.concat([
  Buffer.from([4]),
  Buffer.from(key.x ?? "", "base64url"),
  Buffer.from(key.y ?? "", "base64url"),
]).toString("base64url");
const keys: PushKeys = { publicKey, privateKey: key.d ?? "", subject: "mailto:push@example.invalid" };
test("durable outbox lifecycle and concurrent consumers on real PostgreSQL", async (t) => {
  const db = await postgresDatabase("push-outbox-");
  const c: pg.Client = db.client;
  const sql = async <T = Record<string, unknown>>(op: string, args: object = {}) =>
    (await c.query<{ result: T }>("select ops.web_push_transport($1,$2) result", [op, JSON.stringify(args)])).rows[0]
      ?.result;
  const reset = async () => {
    await c.query("truncate ops.web_push_attempt,ops.web_push_outbox,ops.web_push_circuit");
    await c.query(
      "update ops.web_push_policy set hourly_cap=12,daily_cap=40; update ops.notification_preference set device_opt_in=true,quiet_hours_start=null,quiet_hours_end=null; update email_proposal set status='pending'",
    );
    await c.query(
      "update ops.web_push_subscription set state='active',generation=1,endpoint='https://web.push.apple.com/synthetic',p256dh=$1,auth=$2,vapid_key_version='v1'",
      [publicKey, randomBytes(16).toString("base64url")],
    );
  };
  const enqueue = async () => {
    const n = randomUUID();
    await c.query("insert into ops.notification values($1,$2)", [n, actor]);
    return (
      await c.query<{ id: string }>(
        "insert into ops.web_push_outbox(notification_id,installation_id,generation,target_kind,target_id,target_ref,expires_at) values($1,$2,1,'reply_draft',$3,'al1_synthetic',clock_timestamp()+interval '1 day') returning id",
        [n, installation, source],
      )
    ).rows[0]?.id;
  };
  const state = async () =>
    (
      await c.query<{ state: string; reason: string }>(
        "select state,reason from ops.web_push_outbox order by created_at",
      )
    ).rows;
  try {
    await c.query(`create schema ops; create table actor(id uuid primary key,slug text,active boolean,kind text);
   create table ops.notification(id uuid primary key,recipient_actor uuid references actor(id));
   create table ops.notification_preference(actor uuid primary key,device_opt_in boolean,quiet_hours_start time,quiet_hours_end time,timezone text);
   create table activity(id uuid primary key,email_context jsonb);
   create table email_proposal(id uuid primary key,activity_id uuid,owner text,kind text,status text);
   create function ops.portfolio_writer_actor_id() returns uuid language sql as $$select '${actor}'::uuid$$;
   create function ops.scac_current_grant_contract() returns jsonb language sql as $$select '{"secdef_execute":[]}'::jsonb$$;
   create function ops.resolve_action_card(uuid,text,uuid) returns jsonb language sql as $$select null::jsonb$$;
   insert into actor values('${actor}','joe',true,'human');
   insert into activity values('${source}','{}');
   insert into email_proposal values('${source}','${source}','joe','reply_draft','pending');
   insert into ops.notification_preference values('${actor}',true,null,null,'America/Chicago');`);
    for (const role of ["carr_reader", "carr_writer", "carr_authority", "carr_jobs", "dot_reader"])
      await c.query(`create role ${role}`);
    await c.query(await readFile(new URL("../../migrations/1163_web_push_registry.sql", import.meta.url), "utf8"));
    await c.query(await readFile(new URL("../../migrations/1170_web_push_outbox.sql", import.meta.url), "utf8"));
    await c.query("insert into ops.web_push_session values($1,$2,false)", ["a".repeat(64), actor]);
    await c.query(
      "insert into ops.web_push_subscription(installation_id,actor_id,session_hash,generation,state) values($1,$2,$3,1,'revoked')",
      [installation, actor, "a".repeat(64)],
    );
    await t.test("unique notification installation and generation", async () => {
      await reset();
      await enqueue();
      await assert.rejects(
        c.query(
          "insert into ops.web_push_outbox select gen_random_uuid(),notification_id,installation_id,generation,target_kind,target_id,target_ref,state,reason,due_at,created_at,expires_at,attempt_id,attempt_count,lease_until from ops.web_push_outbox",
        ),
        /duplicate key/,
      );
    });
    for (const operation of ["preflight", "settle"]) {
      for (const token of ["absent", "null", "wrong", "expired"]) {
        await t.test(`${operation} refuses ${token} attempt token without effects`, async () => {
          await reset();
          const id = await enqueue();
          const claim = await sql("claim");
          assert.ok(claim);
          const args: Record<string, unknown> = { id, outcome: "provider_accepted" };
          if (token === "null") args.attempt_id = null;
          if (token === "wrong") args.attempt_id = randomUUID();
          if (token === "expired") {
            args.attempt_id = claim.attempt_id;
            await c.query("update ops.web_push_outbox set lease_until=clock_timestamp()-interval '1 second'");
          }
          const before = (await c.query("select to_jsonb(d) row from ops.web_push_outbox d")).rows;
          const attempts = (await c.query("select to_jsonb(a) row from ops.web_push_attempt a")).rows;
          assert.equal(await sql(operation, args), null);
          assert.deepEqual((await c.query("select to_jsonb(d) row from ops.web_push_outbox d")).rows, before);
          assert.deepEqual((await c.query("select to_jsonb(a) row from ops.web_push_attempt a")).rows, attempts);
        });
      }
    }
    await t.test("accepted is not phone delivered and stale settlement refuses", async () => {
      await reset();
      const id = await enqueue();
      let sends = 0;
      await drainPushOutbox(c, keys, async () => {
        sends++;
        return new Response(null, { status: 201 });
      });
      assert.equal(sends, 1);
      assert.equal((await state())[0]?.state, "provider_accepted");
      assert.equal(await sql("settle", { id, attempt_id: randomUUID(), outcome: "failed" }), null);
    });
    await t.test("quiet overnight hours and disabled preferences persist holds", async () => {
      for (const off of [true, false]) {
        await reset();
        await enqueue();
        if (off) await c.query("update ops.notification_preference set device_opt_in=false");
        else
          await c.query(
            "update ops.notification_preference set quiet_hours_start=((clock_timestamp() at time zone 'UTC')::time-interval '1 hour')::time,quiet_hours_end=((clock_timestamp() at time zone 'UTC')::time+interval '1 hour')::time,timezone='UTC'",
          );
        await drainPushOutbox(c, keys, async () => {
          throw new Error("must not submit");
        });
        assert.equal((await state())[0]?.state, "held");
      }
    });
    await t.test("revoked generation, resolved source and expiry never submit", async () => {
      for (const change of [
        "update ops.web_push_subscription set generation=2",
        "update email_proposal set status='sent'",
        "update ops.web_push_outbox set expires_at=clock_timestamp()-interval '1 second'",
      ]) {
        await reset();
        await enqueue();
        await c.query(change);
        let sends = 0;
        await drainPushOutbox(c, keys, async () => {
          sends++;
          return new Response(null, { status: 201 });
        });
        assert.equal(sends, 0);
        assert.ok(["cancelled", "expired"].includes((await state())[0]?.state ?? ""));
      }
    });
    await t.test("explicit rejection retries have an attempt cap and backoff", async () => {
      await reset();
      await enqueue();
      let sends = 0;
      for (let i = 0; i < 3; i++) {
        await c.query("update ops.web_push_outbox set due_at=clock_timestamp()-interval '1 second'");
        await drainPushOutbox(c, keys, async () => {
          sends++;
          return new Response(null, { status: 429, headers: { "Retry-After": "600" } });
        });
      }
      assert.equal(sends, 3);
      assert.equal((await state())[0]?.state, "failed");
      assert.ok(
        (await c.query("select due_at>clock_timestamp()+interval '500 seconds' ok from ops.web_push_outbox")).rows[0]
          .ok,
      );
    });
    await t.test("crash recovery never submits an ambiguous attempt", async () => {
      await reset();
      await enqueue();
      await sql("claim");
      await c.query("update ops.web_push_outbox set lease_until=clock_timestamp()-interval '1 second'");
      let sends = 0;
      await drainPushOutbox(c, keys, async () => {
        sends++;
        return new Response(null, { status: 201 });
      });
      assert.equal(sends, 0);
      assert.equal((await state())[0]?.state, "unknown");
    });
    await t.test("HTTP permanent, gone, authentication and transient outcomes persist", async () => {
      for (const [status, expected] of [
        [400, "failed"],
        [404, "failed"],
        [410, "failed"],
        [401, "held"],
        [403, "held"],
        [500, "held"],
      ] as const) {
        await reset();
        await enqueue();
        await drainPushOutbox(c, keys, async () => new Response(null, { status }));
        assert.equal((await state())[0]?.state, expected);
        if (status === 404 || status === 410)
          assert.equal((await c.query("select state from ops.web_push_subscription")).rows[0].state, "expired");
      }
    });
    await t.test("authentication circuit holds the next delivery", async () => {
      await reset();
      await enqueue();
      await enqueue();
      await enqueue();
      let sends = 0;
      await drainPushOutbox(c, keys, async () => {
        sends++;
        return new Response(null, { status: 401 });
      });
      assert.equal(sends, 2);
      assert.ok((await state()).some((r) => r.reason === "circuit_open"));
    });
    await t.test("global minute budget survives repeated consumers", async () => {
      await reset();
      for (let i = 0; i < 6; i++) await enqueue();
      let sends = 0;
      for (let i = 0; i < 2; i++)
        await drainPushOutbox(c, keys, async () => {
          sends++;
          return new Response(null, { status: 201 });
        });
      assert.equal(sends, 4);
      assert.ok((await state()).some((r) => r.reason === "run_cap"));
    });
    await t.test("transient circuit permits one recovery probe and closes on acceptance", async () => {
      await reset();
      await c.query("update ops.web_push_policy set transient_threshold=2");
      for (let i = 0; i < 3; i++) await enqueue();
      let sends = 0;
      await drainPushOutbox(c, keys, async () => {
        sends++;
        return new Response(null, { status: 503 });
      });
      assert.equal(sends, 2);
      assert.ok((await state()).some((r) => r.reason === "circuit_open"));
      await c.query(
        "update ops.web_push_circuit set open_until=clock_timestamp()-interval '1 second'; update ops.web_push_outbox set due_at=clock_timestamp()-interval '1 second'",
      );
      await drainPushOutbox(c, keys, async () => new Response(null, { status: 201 }));
      assert.equal((await c.query("select open_until from ops.web_push_circuit")).rows[0].open_until, null);
      await c.query("update ops.web_push_policy set transient_threshold=5");
    });
    await t.test("connection loss persists unknown and a second drain does not resend", async () => {
      await reset();
      await enqueue();
      let sends = 0;
      const lost = async () => {
        sends++;
        throw new TypeError("connection lost");
      };
      await drainPushOutbox(c, keys, lost);
      await drainPushOutbox(c, keys, lost);
      assert.equal(sends, 1);
      assert.equal((await state())[0]?.state, "unknown");
    });
    await t.test("hour and day caps hold new logical notifications", async () => {
      for (const column of ["hourly_cap", "daily_cap"]) {
        await reset();
        await c.query(`update ops.web_push_policy set ${column}=1`);
        await enqueue();
        await enqueue();
        let sends = 0;
        await drainPushOutbox(c, keys, async () => {
          sends++;
          return new Response(null, { status: 201 });
        });
        assert.equal(sends, 1);
        assert.ok((await state()).some((r) => r.reason === column));
      }
    });
    await t.test("concurrent consumers submit a claimed attempt once", async () => {
      await reset();
      await enqueue();
      const params = (c as unknown as { connectionParameters: pg.ClientConfig }).connectionParameters;
      const second = new pg.Client(params);
      await second.connect();
      let sends = 0;
      try {
        await Promise.all(
          [c, second].map((client) =>
            drainPushOutbox(client, keys, async () => {
              sends++;
              await new Promise((r) => setTimeout(r, 20));
              return new Response(null, { status: 201 });
            }),
          ),
        );
      } finally {
        await second.end();
      }
      assert.equal(sends, 1);
    });
    await t.test("preflight fences replacement after claim and status redacts secrets", async () => {
      await reset();
      await enqueue();
      const claim = await sql("claim");
      assert.ok(claim);
      await c.query("update ops.web_push_subscription set generation=2");
      assert.deepEqual(await sql("preflight", claim), { state: "cancelled" });
      const status = await sql("status");
      assert.ok(status);
      const schema = JSON.parse(
        await readFile(new URL("../contracts/web-push-delivery.v1.json", import.meta.url), "utf8"),
      );
      assert.equal(new Validator(schema).validate(status).valid, true);
      assert.ok(!JSON.stringify(status).includes("endpoint"));
    });
  } finally {
    await db.close();
  }
});
