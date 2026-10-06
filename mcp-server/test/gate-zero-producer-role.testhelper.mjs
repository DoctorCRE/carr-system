// THE GATE ZERO PRODUCER'S DATABASE ROLES, for the two proofs that need a real
// connection authenticated as one (2026-09-14, PR 1014 third correction).
//
// STANDING-RULE AMENDMENT 9: seat-only write is enforced by CONNECTION ROLE, not
// by a session setting. migrations/0502_gate_zero_read_only_outcome.sql creates
// the seat's LOGIN role and gives it the sole EXECUTE on
// ops.gate_zero_record_read_only_outcome. It creates no CREDENTIAL -- a rebuilt
// schema mints no secret -- so the password and the DSN come from
// tools/provision-staging-app-writer.py out of band. On the throwaway database
// these proofs run against, the passwordless role is reached by SET SESSION
// AUTHORIZATION, the same door ops/assurance-evidence-acceptance-local-pg-gate.py
// and ops/calendar-prebrief-projection-local-pg-gate.py already use.
//
// THE NAME IS READ OUT OF THE DATABASE, NEVER RETYPED HERE.
// ops.gate_zero_producer_login_role() is the same literal the writer's authority
// test compares against, so a proof built on it cannot pass against a role the
// record layer does not actually admit -- which is exactly how a retyped copy of
// a constant turns a boundary proof into a demonstration.

/** The role names, as the record layer itself states them. */
export async function producerRoleNames(client) {
  const row = (await client.query(
    `select ops.gate_zero_producer_login_role() as login,
            ops.gate_zero_producer_seat_holder_ref() as seat`)).rows[0];
  return { login: row.login, seat: row.seat, lane: String(row.seat).split(":")[1] };
}

/**
 * 0502 creates the seat's LOGIN role itself, so this only has to make the
 * ordinary carr_writer login role the boundary is probed with. Idempotent, and
 * it mints no password: both are reached by SET SESSION AUTHORIZATION on a
 * throwaway database only.
 */
export async function ensureProducerRoles(client, { probeRole = "gate_zero_probe_writer" } = {}) {
  const names = await producerRoleNames(client);
  await client.query(`
    do $$
    begin
      if not exists (select 1 from pg_roles where rolname = ${quote(names.login)}) then
        raise exception 'migration 0502 did not create the Gate Zero producer seat role %',
          ${quote(names.login)};
      end if;
      if not exists (select 1 from pg_roles where rolname = ${quote(probeRole)}) then
        execute 'create role ' || quote_ident(${quote(probeRole)}) || ' login';
      end if;
    end $$;`);
  await client.query(`grant carr_writer to ${probeRole}`);
  return { ...names, probeRole };
}

/** SQL string literal, for the DO block above. */
function quote(value) {
  return `'${String(value).replace(/'/g, "''")}'`;
}

/**
 * Authenticate an already-open connection as one role. SET SESSION AUTHORIZATION
 * is the only statement that moves session_user, it is superuser-only, and --
 * unlike SET ROLE -- it is precisely what the record layer's authority test
 * reads. That is why the boundary proofs use it and not SET ROLE: a proof built
 * on SET ROLE would pass while session_user stayed the superuser's.
 */
export async function authorizeAs(client, role) {
  await client.query(`set session authorization ${role}`);
  const row = (await client.query("select session_user as who")).rows[0];
  if (row.who !== role)
    throw new Error(`session authorization did not take: session_user is ${row.who}, not ${role}`);
}

/** Back to the connection's own authenticated identity. */
export async function resetAuthorization(client) {
  await client.query("reset session authorization");
}
