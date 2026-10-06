// THE GATE ZERO PRODUCER SEAT'S OWN DATABASE CONNECTION — a module of its own,
// and the reason it is one (2026-09-14, PR 1014 third correction).
//
// This could have lived in gate-zero-outcome-store.v5.js beside the receipt
// contract, and it deliberately does not. That module decides WHO MAY SIGN a
// Gate Zero receipt, and its suite holds a closed-set proof that exactly one
// module in src may import it: the verb registry. mcp.js has to reach the door
// below in order to open it, and widening that closed set so it could would have
// traded a real invariant for a line of convenience. So the authority stays
// where it was and only the plumbing moved here.
//
// NOTHING IN THIS FILE DECIDES ANYTHING. It names one secret and opens one
// transaction on it. Every question about whether that transaction may write is
// answered by the database, from session_user, under standing-rule amendment 9.

/**
 * THE SECRET THIS WRITE RUNS ON, AND IT IS USED FOR NOTHING ELSE.
 *
 * Standing-rule amendment 9 (2026-09-14): seat-only write is enforced by
 * CONNECTION ROLE, not by a session setting. Migration 0502 revokes EXECUTE on
 * ops.gate_zero_record_read_only_outcome from carr_writer and grants it to one
 * capability bundle, reachable only by one login role, and derives the producing
 * seat from session_user. That is only true end-to-end if the verb stops sending
 * this call down the ordinary writer connection — which is what this seam is
 * for. The Worker carries the DSN as its own secret, beside DATABASE_URL_READER
 * and DATABASE_URL_WRITER; tools/provision-staging-app-writer.py provisions the
 * role and publishes the value as a third LoginProfile.
 */
export const GATE_ZERO_WRITER_SECRET_NAME = "DATABASE_URL_GATE_ZERO_WRITER";

/**
 * ONE TRANSACTION ON THE SEAT'S OWN CONNECTION, or null when the Worker carries
 * no such secret.
 *
 * WHY A SECOND TRANSACTION IS THE HONEST SHAPE, and what it costs. The verb's
 * outer transaction is the ordinary writer's: it resolves the actor, sets the
 * writer actor context and writes the audit event. This inner one authenticates
 * as a different database role entirely, so it cannot be the same transaction —
 * that is the whole point of the amendment, not an accident of plumbing. The
 * cost is that the outcome row commits before the audit event does, so an outer
 * failure after this returns leaves a recorded outcome with no event row. The
 * opposite arrangement — holding the seat's transaction open across the event
 * write — would put an ordinary writer failure in a position to roll back an
 * oracle's signature, which is worse.
 *
 * SO THE COST IS PAID BY MAKING THE RETRY HEAL IT, UNCONDITIONALLY (2026-09-13,
 * the third release candidate's refusal, finding 3). It is not enough that the
 * record is append-only and keyed on the candidate digest: until migration 0505
 * the record layer RAISED when a retry offered different evidence for a recorded
 * candidate, and the gateway refused for the same reason, so precisely the retry
 * that was meant to write the missing event could not run. 0505 returns the
 * existing row instead; the gateway reads it back, reports which receipt bound,
 * and writes the event only if that outcome row has none. One row, one event, and
 * no state a second call cannot reach — with nothing overwritten and the
 * append-only triggers untouched.
 *
 * IT CARRIES NO ACTOR CONTEXT. `setWriterActorContext` is deliberately not called
 * here: carr.acting_actor_slug decides nothing on this path any more, and setting
 * it would invite a later reader to believe it did.
 */
export function gateZeroSeatConnection(env, PoolConstructor) {
  const connectionString = env?.[GATE_ZERO_WRITER_SECRET_NAME];
  if (!connectionString || typeof PoolConstructor !== "function") return null;
  return async function withGateZeroSeatConnection(run) {
    const pool = new PoolConstructor({ connectionString });
    const client = await pool.connect();
    try {
      await client.query("begin");
      const result = await run(client);
      await client.query("commit");
      return result;
    } catch (error) {
      await client.query("rollback").catch(() => {});
      throw error;
    } finally {
      client.release();
      const ended = pool.end();
      if (env?.ctx?.waitUntil) env.ctx.waitUntil(ended); else await ended.catch(() => {});
    }
  };
}
