// Separate CPU budget; invoked by the app's existing share route through a service binding.
import { Pool } from '@neondatabase/serverless';
import { createRequirementUploadHandler } from './requirement-upload.js';

export default {
  async fetch(request, env) {
    const pool = new Pool({ connectionString: env.DATABASE_URL_WRITER });
    try {
      return await createRequirementUploadHandler({ bucket: env.requirement_quarantine,
        transaction: async fn => {
          const c = await pool.connect();
          try {
            await c.query('begin');
            const result = await fn(c);
            await c.query('commit');
            return result;
          } catch (error) { await c.query('rollback'); throw error; }
          finally { c.release(); }
        },
      })(request);
    } finally { await pool.end(); }
  },
};
