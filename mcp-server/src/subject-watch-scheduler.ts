// Bound scheduled watch work to one connection and four transactional batches.
import type { PoolClient, PoolConfig } from "@neondatabase/serverless";
import { Pool } from "@neondatabase/serverless";
import "./postgres.ts";

type Controller = { scheduledTime: number };
type Environment = { DATABASE_URL_WRITER: string };
type PoolConstructor = new (config: PoolConfig) => Pick<Pool, "connect" | "end">;

export async function sweepSubjectWatches(controller: Controller, env: Environment, PoolClass: PoolConstructor = Pool) {
  const pool = new PoolClass({
    connectionString: env.DATABASE_URL_WRITER,
    connectionTimeoutMillis: 1500,
    query_timeout: 2000,
    max: 1,
  });
  let client: PoolClient | undefined;
  const deadline = Date.now() + 10000;
  try {
    client = await pool.connect();
    const connectedClient = client;
    const query = (text: string, values: string[] = []) => {
      const remaining = deadline - Date.now();
      if (remaining <= 0) throw new Error("watch_sweep_deadline");
      const config = {
        text,
        values,
        query_timeout: Math.min(2000, remaining),
      };
      return connectedClient.query<{ result: unknown }, string[]>(config);
    };
    for (let batch = 0; batch < 4 && Date.now() < deadline - 2000; batch++) {
      await query("begin");
      try {
        await query("set local statement_timeout='1500ms'; set local lock_timeout='100ms'");
        const { rows } = await query("select ops.sweep_subject_watches($1::timestamptz,50) result", [
          new Date(controller.scheduledTime).toISOString(),
        ]);
        await query("commit");
        const result = rows[0]?.result;
        if (
          typeof result !== "object" ||
          result === null ||
          !("scanned" in result) ||
          typeof result.scanned !== "number"
        )
          throw new Error("invalid_watch_sweep_result");
        if (result.scanned < 50) break;
      } catch (error) {
        await query("rollback");
        throw error;
      }
    }
  } finally {
    client?.release(true);
    await pool.end();
  }
}

export function scheduledSubjectWatches(
  controller: Controller,
  env: Environment,
  context: { waitUntil(promise: Promise<void>): void },
) {
  context.waitUntil(sweepSubjectWatches(controller, env));
}
