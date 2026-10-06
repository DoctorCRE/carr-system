import assert from "node:assert/strict";
import test from "node:test";

const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_FOUNDATION_ASSURANCE_DB_REQUIRED === "1";
const LOOPBACK = /@(localhost|127[.]0[.]0[.]1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;
const MATERIAL =
  "ops.foundation_assurance_producer_material(text,jsonb,jsonb)";
const RECORD =
  "ops.foundation_assurance_record_production(text,uuid,jsonb,jsonb)";
const STORE =
  "ops.foundation_assurance_store_evidence(uuid,jsonb,jsonb,jsonb)";

async function connection(pg) {
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  return client;
}

async function materialError(client, identity) {
  try {
    await client.query(
      "select ops.foundation_assurance_producer_material($1,$2::jsonb,$3::jsonb)",
      ["produce-global-secrets-boundary-receipt", JSON.stringify(identity),
        JSON.stringify({ schema_version:
          "doctorcre-v5-foundation-assurance-runtime-binding.v1",
        environment: "production", source_sha: "a".repeat(40),
        provider: "cloudflare-workers",
        provider_version: "00000000-0000-4000-8000-000000000001" })]);
  } catch (error) {
    return error;
  }
  return null;
}

test("foundation assurance write authority is the dedicated connection role", async t => {
  if (!DSN) {
    assert.equal(REQUIRED, false, "database proof required without DATABASE_URL");
    return t.skip("migration class supplies DATABASE_URL");
  }
  assert.ok(LOOPBACK.test(DSN), "REFUSED: role proof runs only on disposable loopback");
  const pg = (await import("pg")).default ?? (await import("pg"));
  const owner = await connection(pg);
  t.after(() => owner.end().catch(() => {}));

  const acl = (await owner.query(
    `select has_function_privilege('carr_foundation_assurance_oracle',$1,'execute') oracle,
            has_function_privilege('carr_writer',$1,'execute') writer,
            has_function_privilege('carr_reader',$1,'execute') reader,
            has_function_privilege('carr_jobs',$1,'execute') jobs,
            has_function_privilege('carr_authority',$1,'execute') authority`,
    [MATERIAL])).rows[0];
  assert.deepEqual(acl, { oracle: true, writer: false, reader: false,
    jobs: false, authority: false });
  assert.equal((await owner.query(
    "select has_function_privilege('carr_foundation_assurance_oracle',$1,'execute') ok",
    [RECORD])).rows[0].ok, true);

  const storeAcl = (await owner.query(
    `select has_function_privilege('carr_foundation_assurance_oracle',$1,'execute') oracle,
            has_function_privilege('carr_writer',$1,'execute') writer,
            has_function_privilege('carr_reader',$1,'execute') reader,
            has_function_privilege('carr_jobs',$1,'execute') jobs,
            has_function_privilege('carr_authority',$1,'execute') authority`,
    [STORE])).rows[0];
  assert.deepEqual(storeAcl, { oracle: false, writer: false, reader: false,
    jobs: false, authority: true });

  const identity = { actor_id: "codex-fa-secrets",
    session_ref: "session:wr95-role-boundary",
    authority_class: "review_agent" };
  const ordinary = await connection(pg);
  t.after(() => ordinary.end().catch(() => {}));
  await ordinary.query("set role carr_writer");
  let denied = await materialError(ordinary, identity);
  assert.equal(denied?.code, "42501");

  await owner.query(`grant execute on function ${MATERIAL} to carr_writer`);
  try {
    denied = await materialError(ordinary, identity);
    assert.ok(denied);
    assert.notEqual(denied.code, "42501");
    assert.match(denied.message, /requires the dedicated oracle connection/);
  } finally {
    await owner.query(`revoke execute on function ${MATERIAL} from carr_writer`);
  }
  assert.equal((await owner.query(
    "select has_function_privilege('carr_writer',$1,'execute') ok", [MATERIAL]))
    .rows[0].ok, false);

  const oracle = await connection(pg);
  t.after(() => oracle.end().catch(() => {}));
  await oracle.query("set session authorization carr_foundation_assurance_oracle");
  assert.equal((await oracle.query("select session_user as who")).rows[0].who,
    "carr_foundation_assurance_oracle");
  const acceptedSeat = await materialError(oracle, identity);
  assert.ok(acceptedSeat);
  assert.notEqual(acceptedSeat.code, "42501");
  assert.match(acceptedSeat.message,
    /foundation assurance evidence is unavailable for the serving Worker/);

  const replayProbe = await oracle.query(
    `select ops.foundation_assurance_record_production(
       $1,$2::uuid,$3::jsonb,null::jsonb) as result`,
    ["produce-global-secrets-boundary-receipt",
      "00000000-0000-4000-8000-000000000095", JSON.stringify(identity)]);
  assert.equal(replayProbe.rows[0].result, null,
    "a missing idempotent production must return a cache miss before material is built");

  const scope = { tenant: "carr-internal", scope_ref: "safe:wr95-regex-proof",
    clock_origin_gate_id: "foundation-assurance-minimum-accepted",
    clock_terminus_gate_id: "journey-one-kernel-production-accepted",
    benchmark_subject_digest: `sha256:${"1".repeat(64)}`,
    benchmark_candidate_digest: `sha256:${"2".repeat(64)}`,
    benchmark_policy_digest: `sha256:${"3".repeat(64)}` };
  const scopeDigest = (await owner.query(
    "select ops.j1_clock_scope_digest($1::jsonb) as digest", [JSON.stringify(scope)]))
    .rows[0].digest;
  assert.match(scopeDigest, /^sha256:[0-9a-f]{64}$/,
    "the production-shaped Journey One scope must compile and hash in PostgreSQL");

  const definitions = (await owner.query(
    `select pg_get_functiondef(p.oid) as definition
       from pg_proc p join pg_namespace n on n.oid=p.pronamespace
      where n.nspname='ops' and p.proname in
        ('j1_clock_scope_digest','j1_minimum_append_guard',
         'foundation_assurance_producer_material')
      union all
     select pg_get_constraintdef(c.oid)
       from pg_constraint c
      where c.conname in
        ('j1_clock_scope_binding_clock_scope_ref_check',
         'j1_clock_revision_verifier_ref_check',
         'j1_minimum_inventory_clock_scope_ref_check',
         'j1_minimum_admission_source_ref_check')`)).rows;
  assert.equal(definitions.length, 7);
  for (const { definition } of definitions)
    assert.doesNotMatch(definition,
      /'\^(?:safe|session):[^']*\{\d+,\d{3,}\}\$'/,
      "PostgreSQL regexes must not use unsupported finite repetition bounds above 255");
  const materialDefinition = definitions.map(row => row.definition)
    .find(definition => definition.includes("foundation_assurance_producer_material"));
  assert.match(materialDefinition,
    /'gate_zero',ops\.benchmark_gate_zero_outcome\(\),'observed_at',to_jsonb\(now\(\)\)\)/,
    "the minimum receipt and its admission must share the transaction timestamp");

  const role = (await owner.query(
    `select rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls
       from pg_roles where rolname='carr_foundation_assurance_oracle'`)).rows[0];
  assert.deepEqual(role, { rolcanlogin: true, rolsuper: false, rolcreatedb: false,
    rolcreaterole: false, rolreplication: false, rolbypassrls: false });
});
