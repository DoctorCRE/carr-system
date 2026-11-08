// Bound scheduled watch work to one connection and four transactional batches.
import { Pool } from './postgres.js';

export async function sweepSubjectWatches(controller, env, PoolClass = Pool) {
  const pool = new PoolClass({ connectionString: env.DATABASE_URL_WRITER,
    connectionTimeoutMillis: 1500, query_timeout: 2000, max: 1 });
  let client;
  const deadline = Date.now() + 10000;
  try {
    client = await pool.connect();
    const query = (text, values) => {
      const remaining = deadline - Date.now();
      if (remaining <= 0) throw new Error('watch_sweep_deadline');
      return client.query({ text, values, query_timeout: Math.min(1500, remaining) });
    };
    for (let batch = 0; batch < 4 && Date.now() < deadline - 2000; batch++) {
      await query('begin');
      try {
        await query("set local statement_timeout='1500ms'; set local lock_timeout='100ms'");
        const { rows } = await query('select ops.sweep_subject_watches($1::timestamptz,50) result',
          [new Date(controller.scheduledTime).toISOString()]);
        await query('commit');
        if (rows[0].result.scanned < 50) break;
      } catch (error) {
        await query('rollback');
        throw error;
      }
    }
  } finally {
    client?.release(true);
    await pool.end();
  }
}

export function scheduledSubjectWatches(controller, env, context) {
  context.waitUntil(sweepSubjectWatches(controller, env));
}
