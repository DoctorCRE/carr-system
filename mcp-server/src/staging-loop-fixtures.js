import { Pool } from "@neondatabase/serverless";
import { isStagingBrowserEnvironment } from "./staging-browser.js";
import { setWriterActorContext } from "./mcp.js";

const SECTIONS = [
  ["open_loop", "backlog", false], ["open_loop", "hot", false],
  ["team_loop", "open", false], ["team_loop", "done", true],
  ["idea", "parked", false], ["idea", "retired", true],
];

export async function prepareStagingLoopFixtures(env, poolFactory = options => new Pool(options)) {
  if (!isStagingBrowserEnvironment(env)) throw new Error("staging environment required");
  const pool = poolFactory({ connectionString: env.DATABASE_URL_WRITER });
  let client;
  try {
    client = await pool.connect();
    await client.query("begin");
    await client.query("select pg_advisory_xact_lock(hashtextextended('staging-e2e-loop-sections',0))");
    const { rows } = await client.query("select id,slug from public.actor where slug='joe'");
    if (rows.length !== 1) throw new Error("synthetic staging partner required");
    const actor = { ...rows[0], human: true, via: "dealroom-cookie", client_id: "dealroom-pwa" };
    await setWriterActorContext(client, actor);
    for (const [seq, [kind, section, closed]] of SECTIONS.entries()) {
      await client.query(`insert into public.loop_block
        (rel_path,kind,seq,block_key,col_order,renders_closed,created_by,updated_by)
        select $1,$2,$3,$4,$5::text[],$6,$7::uuid,$7::uuid
        where not exists (select 1 from public.loop_block where kind=$2 and block_key=$4)`,
      [`Synthetic staging QA/${kind}.md`, kind, seq,
        section, ["number", "owner", "title", "body", "outcome"], closed, actor.id]);
    }
    await client.query("commit");
  } catch (error) {
    if (client) await client.query("rollback");
    throw error;
  } finally {
    client?.release();
    await pool.end();
  }
}
