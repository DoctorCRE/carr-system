// Bound each watch sweep to four batches.
import type { PoolClient, PoolConfig } from "@neondatabase/serverless";
import { Pool } from "@neondatabase/serverless";
import "./postgres.ts";
import { type DealWatchTarget, type EngineEnv, readEngineDealWatch } from "./engine-assistant.ts";

type Controller = { scheduledTime: number };
type Environment = EngineEnv & { DATABASE_URL_WRITER: string; ENGINE_DEAL_WATCH_CHANGES?: string };
const engineBatchLimit = 4;
type EngineWatchAttempt = Pick<DealWatchTarget, "watch_id" | "lifecycle_version"> &
  (Awaited<ReturnType<typeof readEngineDealWatch>> | { status: "retry" });
type QueryParameter = string | string[];
type PoolConstructor = new (config: PoolConfig) => Pick<Pool, "connect" | "end">;

export async function sweepSubjectWatches(controller: Controller, env: Environment, PoolClass: PoolConstructor = Pool) {
  const pool = new PoolClass({
    connectionString: env.DATABASE_URL_WRITER,
    connectionTimeoutMillis: 1500,
    query_timeout: 2000,
    max: 1,
  });
  let client: PoolClient | undefined;
  let deadline = Date.now() + 10000;
  try {
    client = await pool.connect();
    const connectedClient = client;
    const query = <Result = unknown>(text: string, values: QueryParameter[] = []) => {
      const remaining = deadline - Date.now();
      if (remaining <= 0) throw new Error("watch_sweep_deadline");
      const config = {
        text,
        values,
        query_timeout: Math.min(2000, remaining),
      };
      return connectedClient.query<{ result: Result }, QueryParameter[]>(config);
    };
    const now = new Date(controller.scheduledTime).toISOString();
    for (let batch = 0; batch < 4 && Date.now() < deadline - 2000; batch++) {
      await query("begin");
      try {
        await query("set local statement_timeout='1500ms'; set local lock_timeout='100ms'");
        const { rows } = await query("select ops.sweep_subject_watches($1::timestamptz,50) result", [now]);
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
    if (env.ENGINE_DEAL_WATCH_CHANGES === "true") {
      deadline = Date.now() + 10000;
      try {
        const attempted: string[] = [];
        let failures = 0;
        for (let batch = 0; batch < 4 && Date.now() < deadline - 2000; batch++) {
          const { rows } = await query<DealWatchTarget[]>(
            "select ops.read_subject_watch_engine_batch($1::timestamptz,$2::int,$3::uuid[]) result",
            [now, String(engineBatchLimit), attempted],
          );
          const watches = rows[0]?.result;
          if (!Array.isArray(watches)) throw new Error("invalid_engine_watch_batch");
          if (watches.length === 0) break;
          attempted.push(...watches.map((watch) => watch.watch_id));
          const timeout = Math.max(1, Math.min(5000, deadline - Date.now() - 2000));
          const changes = await Promise.all(
            watches.map(async (watch): Promise<EngineWatchAttempt> => {
              const { watch_id, lifecycle_version } = watch;
              try {
                return { watch_id, lifecycle_version, ...(await readEngineDealWatch(env, watch, timeout)) };
              } catch {
                return { watch_id, lifecycle_version, status: "retry" };
              }
            }),
          );

          await query("begin");
          try {
            await query("set local statement_timeout='1500ms'; set local lock_timeout='100ms'");
            const { rows } = await query<number>(
              "select ops.sweep_subject_watch_engine($1::timestamptz,$2::jsonb) result",
              [now, JSON.stringify(changes)],
            );
            await query("commit");
            failures += rows[0]?.result ?? 0;
          } catch (error) {
            await query("rollback");
            throw error;
          }
          if (watches.length < engineBatchLimit) break;
        }
        if (failures > 0) console.warn("subject_watch_engine_retry", { count: failures });
      } catch {
        console.warn("subject_watch_engine_sweep_failed");
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
