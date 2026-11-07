// Atomic redemption for Deal Room's exact Program 6 approval challenge.
// Cloudflare KV holds the short-lived opaque preimage, but KV cannot perform a
// compare-and-delete. PostgreSQL's unique token digest is the single-use tooth.
import { Pool } from "./postgres.js";
import { authorityDsnForActor } from "./mcp.js";

export async function redeemProgram6BrowserChallenge({ env, actor, tokenDigest,
  sessionDigest, action, materialDigest, idempotencyKey }) {
  const connectionString = authorityDsnForActor(env, actor);
  if (!connectionString) return { ok: false, error: "authority_connection_unavailable" };
  const pool = new Pool({ connectionString });
  const client = await pool.connect();
  try {
    const result = await client.query(
      `select ops.redeem_program6_browser_action_challenge(
         $1::text,$2::text,$3::text,$4::text,$5::uuid) as redeemed
         /* program6-browser-challenge:atomic-redemption */`,
      [tokenDigest, sessionDigest, action, materialDigest, idempotencyKey]);
    return { ok: result.rows[0]?.redeemed === true };
  } catch {
    return { ok: false, error: "action_challenge_redemption_unavailable" };
  } finally {
    client.release();
    env.ctx?.waitUntil?.(pool.end());
  }
}
