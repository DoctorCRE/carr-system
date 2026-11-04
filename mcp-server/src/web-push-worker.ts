// Drain partner-needed deliveries under the jobs principal.
import { Pool } from '@neondatabase/serverless';
import { drainWebPush, postgresDeliveryStore, pushConfiguration } from './web-push-delivery.ts';
import type { PushConfig } from './web-push-delivery.ts';

type Environment = PushConfig & { DATABASE_URL_JOBS?: string };
export async function runWebPush(env: Environment) {
  if (!pushConfiguration(env) || !env.DATABASE_URL_JOBS) return {state:'disabled',attempted:0,accepted:0};
  const pool = new Pool({connectionString:env.DATABASE_URL_JOBS,connectionTimeoutMillis:5000});
  try {
    const c = await pool.connect();
    try {
      const who = (await c.query(`select session_user='carr_jobs' and current_user='carr_jobs'
        and not pg_has_role(session_user,'carr_writer','member')
        and not pg_has_role(session_user,'carr_authority','member') as jobs_only`)).rows[0];
      if (who?.jobs_only !== true) throw new Error('push_jobs_principal_required');
      await c.query("set statement_timeout='5s'");
      return await drainWebPush({config:env,store:postgresDeliveryStore(c)});
    } finally { c.release(); }
  } catch { throw new Error('web_push_drain_failed'); }
  finally { await pool.end(); }
}
export default { scheduled: (_event: unknown, env: Environment, ctx: {waitUntil(p: Promise<unknown>): void}) => {
  ctx.waitUntil(runWebPush(env));
} };
