// Drain durable push attempts under generation fences and bounded runtime policy.
import type { PoolClient } from "@neondatabase/serverless";
import { Pool } from "@neondatabase/serverless";
import "./postgres.js";
import type { PushDelivery, PushKeys } from "./web-push-sender.ts";
import { submitPush } from "./web-push-sender.ts";

type Environment = {
  DATABASE_URL_WRITER: string;
  WEB_PUSH_ENABLED?: string;
  VAPID_PUBLIC_KEY?: string;
  WEB_PUSH_VAPID_PRIVATE_KEY?: string;
  WEB_PUSH_VAPID_SUBJECT?: string;
};
type Policy = { batch_size: number; request_ms: number; run_ms: number };
type Claim = { id: string; attempt_id: string; state: string };
export async function drainPushOutbox(
  client: Pick<PoolClient, "query">,
  keys: PushKeys,
  http: typeof fetch = fetch,
  correlation: string = crypto.randomUUID(),
) {
  let deadline = Date.now() + 2000;
  const call = async <T>(op: string, args: object = {}): Promise<T> => {
    const remaining = deadline - Date.now();
    if (remaining <= 0) throw new Error("push_run_deadline");
    const config = {
      text: `/* push:${correlation} */ select ops.web_push_transport($1::text,$2::jsonb) result`,
      values: [op, JSON.stringify(args)],
      query_timeout: Math.min(2000, remaining),
    };
    const result = await client.query<{ result: T }, string[]>(config);
    const row = result.rows[0];
    if (!row) throw new Error("push_store_unavailable");
    return row.result;
  };
  const policy = await call<Policy>("policy");
  deadline = Date.now() + policy.run_ms;
  for (let i = 0; i < policy.batch_size && Date.now() < deadline - policy.request_ms - 2000; i++) {
    const claim = await call<Claim | null>("claim");
    if (!claim) break;
    if (claim.state !== "submitting") continue;
    await client.query("begin");
    try {
      await client.query(
        "set local statement_timeout='2000ms'; set local lock_timeout='100ms'; set local idle_in_transaction_session_timeout='5000ms'",
      );
      const delivery = await call<PushDelivery | { state: string } | null>("preflight", claim);
      if (delivery && "endpoint" in delivery) {
        const outcome = await submitPush(delivery, keys, Math.min(policy.request_ms, deadline - Date.now()), http);
        await call("settle", { ...claim, ...outcome });
      }
      await client.query("commit");
    } catch {
      await client.query("rollback");
      // The committed attempt survives and becomes unknown after its lease. Never resubmit it.
      throw new Error("push_store_unavailable");
    }
  }
}
export async function scheduledPushOutbox(env: Environment) {
  if (
    env.WEB_PUSH_ENABLED !== "true" ||
    !env.VAPID_PUBLIC_KEY ||
    !env.WEB_PUSH_VAPID_PRIVATE_KEY ||
    !env.WEB_PUSH_VAPID_SUBJECT
  )
    return;
  const pool = new Pool({
    connectionString: env.DATABASE_URL_WRITER,
    max: 1,
    connectionTimeoutMillis: 1500,
    query_timeout: 2000,
  });
  let client: PoolClient | undefined;
  const correlation = crypto.randomUUID();
  try {
    client = await pool.connect();
    await drainPushOutbox(
      client,
      {
        publicKey: env.VAPID_PUBLIC_KEY,
        privateKey: env.WEB_PUSH_VAPID_PRIVATE_KEY,
        subject: env.WEB_PUSH_VAPID_SUBJECT,
      },
      fetch,
      correlation,
    );
  } catch {
    console.error(JSON.stringify({ event: "push_sender_unavailable", correlation_id: correlation }));
  } finally {
    client?.release(true);
    await pool.end();
  }
}
