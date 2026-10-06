// V5-F01 on REAL PostgreSQL: the migration, the two SQL fixtures, and the
// registered verbs end to end.
//
// WHAT THIS PROVES THAT NOTHING ELSE IN THE SUITE CAN. The kernel and store
// suites run against a scripted fake handle; the two *-postgres.sql fixtures
// were written for a local gate that never landed and had never executed. This
// file runs, on the migration class's disposable database AFTER every pending
// migration has applied:
//
//   1. both SQL fixtures, each on its OWN template copy of that database, as a
//      superuser that can SET SESSION AUTHORIZATION to the real principals
//      (they share synthetic identities, so one would trip over the other's
//      committed rows);
//   2. the nine verbs exactly as tools.js registers them — kernel, store and
//      door unchanged — against a third copy, on the connection identities the
//      Worker uses: carr_writer with the transaction-local acting actor for
//      ordinary writes, and the partner's authority login for the two
//      authority verbs. The checkable_done clauses of the F01 catalog entry are
//      asserted here by name: stale/newer, recycled native ID, forbidden
//      overwrite and conflict refuse or surface reconciliation; document
//      state/hash/home round-trips through a read.
//
// DISPOSABLE ONLY. It refuses any DSN that is not loopback and any connecting
// role that is not a superuser, creates the two authority LOGIN roles only if
// the disposable cluster lacks them (production provisions them outside this
// repository; the migration grants their surface to the carr_authority group
// so it follows membership), and drops every database it created.
//
// EVERY VALUE IS SYNTHETIC and every registry below is TEST POLICY — what a
// partner might install, never a claim about CARR's real field owners.

import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_F01_FIELD_REGISTRY_SCHEMA_VERSION,
  V5_F01_RETENTION_REGISTRY_SCHEMA_VERSION,
  compileFieldAuthorityRegistry,
  compileRetentionRegistry,
  v5F01DecisionSubsetDigest,
  v5F01PolicyDigest,
} from "../src/record-source-authority.v5.js";
import {
  V5_F01_OPERATIONS,
  recordSourceAuthorityStoreTools,
} from "../src/record-source-authority-store.v5.js";
import { connectionRouteForTool } from "../src/mcp.js";

const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_F01_DB_REQUIRED === "1";
const LOOPBACK = /@(localhost|127[.]0[.]0[.]1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;
const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURES = [
  "document-derivative-registration-postgres.sql",
  "record-source-authority-postgres.sql",
];

function psqlBinary() {
  const candidates = [process.env.PSQL, "psql", "/opt/homebrew/opt/postgresql@17/bin/psql"]
    .filter(Boolean);
  for (const candidate of candidates) {
    try {
      execFileSync(candidate, ["--version"], { stdio: "ignore" });
      return candidate;
    } catch { /* next */ }
  }
  return null;
}

function dsnFor(database) {
  const url = new URL(DSN);
  url.pathname = `/${database}`;
  return url.toString();
}

class ToolError extends Error {
  constructor(payload) {
    super(payload?.error ?? "tool_error");
    this.payload = payload;
  }
}

// The shared envelope's replay row is tools.js's concern and is proved there;
// here the door runs its body exactly once inside the transaction we opened.
const withEnvelope = async (_client, _actor, _verb, _args, fn) => fn();
const TOOLS = recordSourceAuthorityStoreTools({ withEnvelope, ToolError });

const JOE = Object.freeze({ slug: "joe", display: "Joe", human: true, via: "oauth-google" });
const AGENT = Object.freeze({
  slug: "codex", display: "Codex", human: false, via: "oauth-google",
  sponsoring_human_slug: "joe", human_slug: "joe",
});

const D = n => `sha256:${String(n).padStart(2, "0").repeat(32)}`;
const VALUE_A = D(1);
const VALUE_B = D(2);
const T = { early: "2026-09-01T09:00:00Z", mid: "2026-09-05T09:00:00Z", late: "2026-09-08T09:00:00Z" };

const salesforceEntry = (field, conflict_behavior) => ({
  entity: "deal", field,
  authoritative_home: "salesforce", owner_source: "salesforce",
  permitted_sources: [
    { source_system: "salesforce", direction: "inbound" },
    { source_system: "neon_record_layer", direction: "outbound" },
  ],
  requires_account_identity: true, requires_native_identity: true,
  version_comparator: "integer_sequence",
  conflict_behavior, human_resolver_class: "deal_owner",
  readback_required: true, sensitivity_classes: ["lease_economics"],
  taint_class: "corporate_source_of_record",
});

const FIELD_POLICY = Object.freeze({
  schema_version: V5_F01_FIELD_REGISTRY_SCHEMA_VERSION,
  registry_version: 1,
  tenant: ORGANIZATION_TENANT_ID,
  entries: [
    salesforceEntry("commission_amount", "reconcile"),
    salesforceEntry("synthetic_refuse_field", "refuse"),
  ],
});
const RETENTION_POLICY = Object.freeze({
  schema_version: V5_F01_RETENTION_REGISTRY_SCHEMA_VERSION,
  registry_version: 1,
  tenant: ORGANIZATION_TENANT_ID,
  classes: [{
    artifact_class: "synthetic_test_lease", authoritative_home: "onedrive",
    default_retention_days: 1, governing_constraints: ["synthetic_test_constraint"],
    deletion_proof_required: true, surviving_derivatives: ["synthetic_test_abstract"],
  }],
});
const FIELD_REGISTRY = compileFieldAuthorityRegistry(FIELD_POLICY);
const RETENTION_REGISTRY = compileRetentionRegistry(RETENTION_POLICY);
const POLICY_ARGS = Object.freeze({
  idempotency_key: "syn-live-policy-0001",
  field_registry: FIELD_POLICY, retention_registry: RETENTION_POLICY,
  field_registry_digest: FIELD_REGISTRY.registry_digest,
  retention_registry_digest: RETENTION_REGISTRY.registry_digest,
  expected_prior_policy_digest: null,
});

const observation = (overrides = {}) => ({
  entity: "deal", field: "commission_amount", source_system: "salesforce",
  account: "synthetic-account-0001",
  native_identity: {
    source_system: "salesforce", native_id: "SYNTHETIC-NATIVE-0001",
    native_id_epoch: "synthetic-epoch-1",
  },
  value_digest: VALUE_A, version: 5, observed_at: T.mid,
  provenance: {
    adapter_kind: "synthetic_test_adapter", evidence_ref: "synthetic-evidence-0010",
    retrieval_class: "corporate_record_export",
  },
  taint_class: "corporate_source_of_record",
  readback: { confirmed: true, readback_at: T.late, readback_value_digest: VALUE_A },
  ...overrides,
});

const DOCUMENT = Object.freeze({
  document_class: "synthetic_test_agreement",
  neon_identity: { document_id: "synthetic-live-document-0001", content_digest: VALUE_A, version_no: 1 },
  object_storage_identity: {
    object_key: "synthetic/live/object-0001", content_digest: VALUE_A, byte_length: 2048, sealed: true,
  },
  onedrive_identity: {
    drive_id: "synthetic-drive-0001", item_id: "synthetic-item-0001",
    content_digest: VALUE_A, filing_state: "filed",
  },
  preparation_state: "approved_for_delivery",
  delivery_state: "delivered",
  signature_state: "fully_executed",
  validity_state: "effective",
  version_state: "current",
});
const ORIGINAL_SOURCE = Object.freeze({
  provenance_state: "original_first_party",
  basis_statement: "synthetic live fixture: authored in this record layer, no corporate original",
});

async function adminClient(pg, database = "postgres") {
  const client = new pg.Client({ connectionString: dsnFor(database) });
  await client.connect();
  return client;
}

/**
 * One verb on the identity the Worker would use. Writer verbs run as
 * carr_writer with the transaction-local acting actor mcp.js sets; the two
 * authority verbs run as the partner's authority login. The door is exactly
 * the one tools.js registers.
 */
async function call(pg, database, { role, actor }, name, args) {
  const client = await adminClient(pg, database);
  try {
    await client.query(`SET SESSION AUTHORIZATION ${role}`);
    await client.query("BEGIN");
    await client.query("select set_config('carr.acting_actor_slug',$1::text,true)", [actor.slug]);
    try {
      const answer = await TOOLS[name].handler(client, actor, args);
      await client.query("COMMIT");
      return answer;
    } catch (error) {
      await client.query("ROLLBACK").catch(() => {});
      throw error;
    }
  } finally {
    await client.end();
  }
}

/**
 * One verb on the ROUTE mcp.js's connectionRouteForTool picks for it, rather
 * than on a role the test chooses. `call` above always runs as carr_writer with
 * the acting actor set — reads included — which is why this suite never saw the
 * 2026-09-27 live refusal: the read was routed to the reader connection, where
 * 0732 derives the fixed actor 'carr-reader'. The reader route here is the
 * carr_reader bundle with no transaction and no actor context, as mcp.js runs
 * it; a writer route is carr_writer, `begin read only` for a declared read,
 * with the acting actor set.
 */
async function callOnRoute(pg, database, route, actor, name, args) {
  const client = await adminClient(pg, database);
  try {
    if (route === "reader") {
      await client.query("SET SESSION AUTHORIZATION carr_reader");
      return { answer: await TOOLS[name].handler(client, actor, args), read_only: null };
    }
    assert.equal(route, "writer_read_only", "this helper only runs the read routes");
    await client.query("SET SESSION AUTHORIZATION carr_writer");
    await client.query("BEGIN READ ONLY");
    await client.query("select set_config('carr.acting_actor_slug',$1::text,true)", [actor.slug]);
    try {
      const readOnly = (await client.query("show transaction_read_only")).rows[0].transaction_read_only;
      const answer = await TOOLS[name].handler(client, actor, args);
      await client.query("COMMIT");
      return { answer, read_only: readOnly };
    } catch (error) {
      await client.query("ROLLBACK").catch(() => {});
      throw error;
    }
  } finally {
    await client.end();
  }
}

async function refusedWith(promise, code) {
  await assert.rejects(promise, error => {
    const got = error?.payload?.error ?? error?.code ?? error?.message;
    assert.equal(got, code, `expected refusal ${code}, got ${got}`);
    return true;
  });
}

const WRITER_AGENT = { role: "carr_writer", actor: AGENT };
const WRITER_JOE = { role: "carr_writer", actor: JOE };
const AUTHORITY_JOE = { role: "carr_authority_joe", actor: JOE };

test("F01 on real PostgreSQL: migration, SQL fixtures and the registered verbs", async t => {
  if (!DSN) {
    assert.equal(REQUIRED, false, "the F01 database proof is required but DATABASE_URL is unset");
    return t.skip("the migration class supplies DATABASE_URL");
  }
  assert.match(DSN, LOOPBACK, "the F01 database proof runs only against a loopback disposable database");
  const pg = (await import("pg")).default;
  const source = new URL(DSN).pathname.replace(/^\//, "");
  const admin = await adminClient(pg);
  const copies = [];
  try {
    const su = await admin.query("select rolsuper from pg_roles where rolname = current_user");
    assert.equal(su.rows[0]?.rolsuper, true,
      "the proof needs a superuser to SET SESSION AUTHORIZATION to the real principals");

    // The migration is applied, and applied as the numbered file.
    const probe = await adminClient(pg, source);
    try {
      const applied = await probe.query(
        "select filename from public.schema_migrations where filename ~ '^[0-9]{4}_f01_record_source_authority[.]sql$'");
      assert.equal(applied.rows.length, 1, "the F01 migration is recorded as applied exactly once");
      const policy = await probe.query("select ops.f01_current_policy_digest() as d");
      assert.equal(policy.rows[0].d, null, "the migration installs no policy of its own");
    } finally {
      await probe.end();
    }

    // Disposable authority logins, joined to the group the migration granted.
    for (const role of ["carr_authority_joe", "carr_authority_dell"]) {
      const exists = await admin.query("select 1 from pg_roles where rolname = $1", [role]);
      if (!exists.rows.length) await admin.query(`CREATE ROLE ${role} LOGIN`);
      await admin.query(`GRANT carr_authority TO ${role}`);
    }

    const copy = async label => {
      const name = `f01_${label}_${process.pid}`;
      await admin.query(`DROP DATABASE IF EXISTS ${name}`);
      await admin.query(`CREATE DATABASE ${name} TEMPLATE ${source}`);
      copies.push(name);
      return name;
    };

    await t.test("both SQL fixtures pass, each on its own copy", async st => {
      const psql = psqlBinary();
      if (!psql) {
        assert.equal(REQUIRED, false, "the F01 database proof is required but no psql binary is reachable");
        return st.skip("no psql binary");
      }
      for (const fixture of FIXTURES) {
        const database = await copy(fixture.startsWith("document") ? "docsrc" : "rsa");
        try {
          execFileSync(psql, [
            "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", dsnFor(database),
            "-v", `f01ds_domain_policy_digest=${v5F01PolicyDigest()}`,
            "-v", `f01ds_decision_subset_digest=${v5F01DecisionSubsetDigest()}`,
            "-f", resolve(HERE, fixture),
          ], { stdio: ["ignore", "pipe", "pipe"], maxBuffer: 64 * 1024 * 1024 });
        } catch (error) {
          const stderr = String(error.stderr ?? "").split("\n").filter(l => /ERROR|FAIL/.test(l));
          assert.fail(`${fixture} failed on real PostgreSQL: ${stderr.slice(0, 5).join(" | ")}`);
        }
      }
    });

    const db = await copy("verbs");

    await t.test("re-applying the migration refuses rather than repairing", async () => {
      const [file] = readdirSync(resolve(HERE, "../../migrations"))
        .filter(name => /^\d{4}_f01_record_source_authority\.sql$/.test(name));
      const client = await adminClient(pg, db);
      try {
        await assert.rejects(client.query(readFileSync(resolve(HERE, "../../migrations", file), "utf8")),
          error => /f01_already_installed/.test(error.message));
      } finally {
        await client.end();
      }
    });

    await t.test("the static reader/writer grant restatement is exactly what the loops granted", async () => {
      const [file] = readdirSync(resolve(HERE, "../../migrations"))
        .filter(name => /^\d{4}_f01_record_source_authority\.sql$/.test(name));
      const text = readFileSync(resolve(HERE, "../../migrations", file), "utf8");
      const restated = text.split("\n")
        .filter(line => /^grant (execute on function|select on table) ops\.f01_\S.* to carr_(reader|writer);$/.test(line))
        .sort();
      const client = await adminClient(pg, db);
      try {
        const rows = await client.query(`
          select format('grant execute on function %s to %s;', p.oid::regprocedure::text, g.rolname) as stmt
            from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            cross join lateral aclexplode(p.proacl) a join pg_roles g on g.oid = a.grantee
           where n.nspname = 'ops' and p.proname like 'f01\\_%' and a.privilege_type = 'EXECUTE'
             and g.rolname in ('carr_reader', 'carr_writer')
          union all
          select format('grant select on table %s to %s;', c.oid::regclass::text, g.rolname)
            from pg_class c join pg_namespace n on n.oid = c.relnamespace
            cross join lateral aclexplode(c.relacl) a join pg_roles g on g.oid = a.grantee
           where n.nspname = 'ops' and c.relkind = 'r' and c.relname like 'f01\\_%'
             and g.rolname in ('carr_reader', 'carr_writer')`);
        const catalog = rows.rows.map(row => row.stmt).sort();
        // A restated signature may carry an argument name (the canonical-plan
        // grammar needs one before a multi-word type); the database resolves it
        // to the same function, so compare through its own regprocedure text.
        const normalized = [];
        for (const line of restated) {
          const fn = /^grant execute on function (.+) to (carr_reader|carr_writer);$/.exec(line);
          if (!fn) { normalized.push(line); continue; }
          // regprocedure input takes types only, so the F01 p_ argument names go.
          const typesOnly = fn[1].replace(/\b(p_[a-z0-9_]+) (?=[a-z])/g, "");
          const sig = (await client.query("select $1::regprocedure::text as sig", [typesOnly])).rows[0].sig;
          normalized.push(`grant execute on function ${sig} to ${fn[2]};`);
        }
        restated.splice(0, restated.length, ...normalized.sort());
        assert.ok(restated.length > 50, "the migration restates the runtime grants statically");
        assert.deepEqual(catalog, restated,
          "every reader/writer F01 privilege in the catalog is restated, and nothing else");
      } finally {
        await client.end();
      }
    });

    await t.test("the authority surface follows carr_authority membership, not the install moment", async () => {
      const client = await adminClient(pg, db);
      try {
        const rows = await client.query(
          `select r, has_function_privilege(r, 'ops.f01_install_policy(jsonb,text,text,text)', 'EXECUTE') as can
             from unnest(array['carr_authority_joe','carr_authority_dell','carr_writer','carr_reader']) r`);
        const can = Object.fromEntries(rows.rows.map(row => [row.r, row.can]));
        assert.deepEqual(can, { carr_authority_joe: true, carr_authority_dell: true,
          carr_writer: false, carr_reader: false });
        const helper = await client.query(
          "select has_function_privilege('carr_authority', 'ops.f01_claim_idempotency(text,text,text)', 'EXECUTE') as can");
        assert.equal(helper.rows[0].can, false, "the private idempotency helper stays owner-only");
      } finally {
        await client.end();
      }
    });

    await t.test("no authority login keeps a direct F01 grant, so the sealed catalog is the same everywhere", async () => {
      // Production has carr_authority_joe when 0626 runs; this lane has no login
      // then. The SCAC catalog 0627 seals counts grants to every connected carr_*
      // role, so a direct login grant would make production measure a catalog CI
      // never saw. Tail 3 takes those grants back. Reproduce the production
      // moment on a copy: hand the logins direct grants the way the sources do,
      // run Tail 3 exactly as the migration spells it, and read the ACLs back.
      const [file] = readdirSync(resolve(HERE, "../../migrations"))
        .filter(name => /^\d{4}_f01_record_source_authority\.sql$/.test(name));
      const text = readFileSync(resolve(HERE, "../../migrations", file), "utf8");
      const start = text.indexOf("DO $f01_authority_login_direct_grants$");
      const endMarker = "$f01_authority_login_direct_grants$;";
      const end = text.indexOf(endMarker, start + 1);
      assert.ok(start > 0 && end > start, "the migration carries the Tail 3 login-grant block");
      const tail3 = text.slice(start, end + endMarker.length);
      const loginDb = await copy("logins");
      const client = await adminClient(pg, loginDb);
      const direct = async () => (await client.query(`
          select g.rolname from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            cross join lateral aclexplode(p.proacl) a join pg_roles g on g.oid = a.grantee
           where n.nspname = 'ops' and p.proname like 'f01\\_%'
             and g.rolname in ('carr_authority_joe', 'carr_authority_dell')
          union all
          select g.rolname from pg_class c join pg_namespace n on n.oid = c.relnamespace
            cross join lateral aclexplode(c.relacl) a join pg_roles g on g.oid = a.grantee
           where n.nspname = 'ops' and c.relkind = 'r' and c.relname like 'f01\\_%'
             and g.rolname in ('carr_authority_joe', 'carr_authority_dell')`)).rows.length;
      try {
        assert.equal(await direct(), 0, "the applied migration left no direct login grant");
        await client.query(
          "grant execute on function ops.f01_install_policy(jsonb,text,text,text) to carr_authority_joe");
        await client.query("grant select on table ops.f01_state_transition to carr_authority_dell");
        assert.equal(await direct(), 2);
        await client.query(tail3);
        assert.equal(await direct(), 0, "Tail 3 takes every direct login grant back");
        const can = await client.query(
          `select r, has_function_privilege(r, 'ops.f01_install_policy(jsonb,text,text,text)', 'EXECUTE') as can
             from unnest(array['carr_authority_joe','carr_authority_dell']) r`);
        assert.deepEqual(can.rows.map(row => row.can), [true, true],
          "each login still reaches the authority writer through carr_authority");

        // A login that exists outside the group would lose its direct grants
        // and gain nothing back, so Tail 3 refuses instead of announcing it,
        // and the transaction leaves the direct grant it found in place.
        // Membership is cluster-wide, so it is restored before anything else runs.
        await client.query("grant select on table ops.f01_state_transition to carr_authority_dell");
        await admin.query("REVOKE carr_authority FROM carr_authority_dell");
        try {
          await assert.rejects(client.query(tail3),
            error => /f01_grant_posture_violation: authority login carr_authority_dell exists but is not a carr_authority member/
              .test(error.message));
        } finally {
          await admin.query("GRANT carr_authority TO carr_authority_dell");
        }
        assert.equal(await direct(), 1, "the refused Tail 3 rolled back its revokes");
      } finally {
        await client.end();
      }
    });

    await t.test("the nine verbs are exactly the store's operations", () => {
      assert.deepEqual(Object.keys(TOOLS).sort(), [...V5_F01_OPERATIONS].sort());
    });

    await t.test("no policy: observations refuse, nothing is invented", async () => {
      const read = await call(pg, db, WRITER_AGENT, "read-record-source-authority",
        { selector: { kind: "current_policy" } });
      assert.equal(read.readback.body, null);
      await refusedWith(call(pg, db, WRITER_AGENT, "record-source-observation",
        { idempotency_key: "syn-live-obs-nopolicy", observation: observation() }), "no_installed_policy");
    });

    await t.test("field owners are installed only by a partner on the authority connection", async () => {
      await refusedWith(call(pg, db, WRITER_AGENT, "register-record-source-authority-policy", POLICY_ARGS),
        "human_only_operation_refused");
      // Joe's own actor on the WRITER connection is not the authority session.
      await refusedWith(call(pg, db, WRITER_JOE, "register-record-source-authority-policy", POLICY_ARGS),
        "actor_context_mismatch");
      const installed = await call(pg, db, AUTHORITY_JOE, "register-record-source-authority-policy", POLICY_ARGS);
      assert.equal(installed.decision, "allow");
      assert.equal(installed.entries_invented, 0);
      assert.equal(installed.field_registry_digest, FIELD_REGISTRY.registry_digest);
      const read = await call(pg, db, WRITER_AGENT, "read-record-source-authority",
        { selector: { kind: "current_policy" } });
      assert.equal(read.readback.policy_digest, installed.policy_digest,
        "the installed policy reads back under the digest the install reported");
      // A second install against a stale prior refuses (CAS).
      await refusedWith(call(pg, db, AUTHORITY_JOE, "register-record-source-authority-policy",
        { ...POLICY_ARGS, idempotency_key: "syn-live-policy-0002" }), "stale_policy_digest");
    });

    await t.test("the read allows on the route mcp.js picks for it, and the reader bundle cannot attribute it", async () => {
      const args = { selector: { kind: "current_policy" } };
      // The live failure, on real roles: the reader bundle's database actor is
      // 'carr-reader', never the caller, so the strict check refuses.
      await assert.rejects(callOnRoute(pg, db, "reader", AGENT, "read-record-source-authority", args),
        error => {
          assert.equal(error?.payload?.error, "actor_context_mismatch");
          assert.equal(error.payload.detail?.database_actor, "carr-reader");
          assert.equal(error.payload.detail?.handler_actor, AGENT.slug);
          return true;
        });
      const route = connectionRouteForTool(TOOLS["read-record-source-authority"]);
      assert.equal(route, "writer_read_only");
      const { answer, read_only } = await callOnRoute(pg, db, route, AGENT,
        "read-record-source-authority", args);
      assert.equal(read_only, "on", "the read ran inside a read-only transaction");
      assert.equal(answer.decision, "allow");
      assert.equal(answer.actor_slug, AGENT.slug);
      // The body is the installed policy as the database itself digests it.
      const probe = await adminClient(pg, db);
      try {
        const installed = (await probe.query("select ops.f01_current_policy_digest() as d")).rows[0].d;
        assert.match(installed, /^sha256:[0-9a-f]{64}$/);
        assert.equal(answer.readback.policy_digest, installed);
      } finally {
        await probe.end();
      }
    });

    await t.test("owner observation establishes the field, with its four records", async () => {
      const answer = await call(pg, db, WRITER_AGENT, "record-source-observation",
        { idempotency_key: "syn-live-obs-0001", observation: observation() });
      assert.equal(answer.decision, "accept");
      assert.equal(answer.silent_last_write_wins, false);
      assert.ok(answer.event_digest && answer.mutation_receipt_digest && answer.current_state_transition_digest);
      const state = await call(pg, db, WRITER_AGENT, "read-record-source-authority",
        { selector: { kind: "field_state", entity: "deal", field: "commission_amount" } });
      assert.equal(state.readback.body.current_state.value_digest, VALUE_A);
    });

    await t.test("stale (older version) observation refuses", async () => {
      const answer = await call(pg, db, WRITER_AGENT, "record-source-observation", {
        idempotency_key: "syn-live-obs-stale",
        observation: observation({ version: 4, value_digest: VALUE_B,
          readback: { confirmed: true, readback_at: T.late, readback_value_digest: VALUE_B } }),
      });
      assert.equal(answer.decision, "refuse");
      assert.equal(answer.reason_id, "stale_observation_refused");
      assert.equal(answer.ok, false);
    });

    await t.test("a recycled native ID refuses", async () => {
      const answer = await call(pg, db, WRITER_AGENT, "record-source-observation", {
        idempotency_key: "syn-live-obs-recycled",
        observation: observation({ version: 6, native_identity: {
          source_system: "salesforce", native_id: "SYNTHETIC-NATIVE-0001",
          native_id_epoch: "synthetic-epoch-2" } }),
      });
      assert.equal(answer.decision, "refuse");
      assert.equal(answer.reason_id, "recycled_native_id_refused");
    });

    await t.test("a source writing against its registered direction refuses (forbidden overwrite)", async () => {
      const answer = await call(pg, db, WRITER_AGENT, "record-source-observation", {
        idempotency_key: "syn-live-obs-forbidden",
        observation: observation({ source_system: "neon_record_layer", version: 6,
          value_digest: VALUE_B,
          native_identity: { source_system: "neon_record_layer", native_id: "SYNTHETIC-NATIVE-0001",
            native_id_epoch: "synthetic-epoch-1" },
          readback: { confirmed: true, readback_at: T.late, readback_value_digest: VALUE_B } }),
      });
      assert.equal(answer.decision, "refuse");
      assert.equal(answer.reason_id, "forbidden_write_direction");
    });

    await t.test("a same-version conflict surfaces a visible reconciliation item and leaves state alone", async () => {
      const answer = await call(pg, db, WRITER_AGENT, "record-source-observation", {
        idempotency_key: "syn-live-obs-conflict",
        observation: observation({ value_digest: VALUE_B,
          readback: { confirmed: true, readback_at: T.late, readback_value_digest: VALUE_B } }),
      });
      assert.equal(answer.decision, "reconcile");
      assert.ok(answer.reconciliation_item_digest);
      const items = await call(pg, db, WRITER_AGENT, "read-record-source-authority",
        { selector: { kind: "reconciliation_items", entity: "deal", field: "commission_amount" } });
      assert.equal(items.readback.body.length, 1, "the conflict is visible as exactly one item");
      const state = await call(pg, db, WRITER_AGENT, "read-record-source-authority",
        { selector: { kind: "field_state", entity: "deal", field: "commission_amount" } });
      assert.equal(state.readback.body.current_state.value_digest, VALUE_A,
        "reconciliation never overwrites the established value");
    });

    await t.test("a conflict on a refuse-behaviour field refuses", async () => {
      const first = await call(pg, db, WRITER_AGENT, "record-source-observation", {
        idempotency_key: "syn-live-obs-refusefield-1",
        observation: observation({ field: "synthetic_refuse_field" }),
      });
      assert.equal(first.decision, "accept");
      const conflict = await call(pg, db, WRITER_AGENT, "record-source-observation", {
        idempotency_key: "syn-live-obs-refusefield-2",
        observation: observation({ field: "synthetic_refuse_field", value_digest: VALUE_B,
          readback: { confirmed: true, readback_at: T.late, readback_value_digest: VALUE_B } }),
      });
      assert.equal(conflict.decision, "refuse", "the field's registered behaviour is refuse, not reconcile");
      assert.equal(conflict.conflict_kind, "equal_version_contradiction");
      // A REFUSED conflict is still SURFACED: the kernel files the visible item
      // either way, so a refusal is never a silent drop of the other value.
      assert.ok(conflict.reconciliation_item_digest, "the refused conflict is still filed visibly");
      const state = await call(pg, db, WRITER_AGENT, "read-record-source-authority",
        { selector: { kind: "field_state", entity: "deal", field: "synthetic_refuse_field" } });
      assert.equal(state.readback.body.current_state.value_digest, VALUE_A,
        "a refused conflict leaves the established value alone");
    });

    await t.test("a newer owner version advances the field", async () => {
      const answer = await call(pg, db, WRITER_AGENT, "record-source-observation", {
        idempotency_key: "syn-live-obs-newer",
        observation: observation({ version: 6, value_digest: VALUE_B,
          readback: { confirmed: true, readback_at: T.late, readback_value_digest: VALUE_B } }),
      });
      assert.equal(answer.decision, "accept");
      assert.equal(answer.reason_id, "owner_value_updated");
    });

    await t.test("document state, hash and home round-trip through a read", async () => {
      const written = await call(pg, db, WRITER_AGENT, "record-document-identity", {
        idempotency_key: "syn-live-doc-0001", document: DOCUMENT, source: ORIGINAL_SOURCE,
        expected_prior_document_digest: null,
      });
      assert.equal(written.decision, "allow");
      assert.equal(written.official_filing_state, "filed");
      assert.equal(written.provenance_state, "original_first_party");
      const read = await call(pg, db, WRITER_AGENT, "read-record-source-authority",
        { selector: { kind: "document", document_id: DOCUMENT.neon_identity.document_id } });
      const body = read.readback.body;
      assert.equal(body.record_digest, written.document_digest, "the hash reads back exactly");
      const record = body.record;
      assert.deepEqual(record.neon_identity, DOCUMENT.neon_identity);
      assert.deepEqual(record.object_storage_identity, DOCUMENT.object_storage_identity);
      assert.deepEqual(record.onedrive_identity, DOCUMENT.onedrive_identity);
      for (const key of ["preparation_state", "delivery_state", "signature_state",
        "validity_state", "version_state"]) assert.equal(record[key], DOCUMENT[key], key);

      // A second version against a stale prior refuses; against the right one it lands.
      const v2 = { ...DOCUMENT, neon_identity: { ...DOCUMENT.neon_identity, version_no: 2, content_digest: VALUE_B },
        object_storage_identity: { ...DOCUMENT.object_storage_identity, content_digest: VALUE_B },
        onedrive_identity: { ...DOCUMENT.onedrive_identity, content_digest: VALUE_B } };
      await refusedWith(call(pg, db, WRITER_AGENT, "record-document-identity", {
        idempotency_key: "syn-live-doc-0002-stale", document: v2, source: ORIGINAL_SOURCE,
        expected_prior_document_digest: null,
      }), "stale_document_digest");
      const second = await call(pg, db, WRITER_AGENT, "record-document-identity", {
        idempotency_key: "syn-live-doc-0002", document: v2, source: ORIGINAL_SOURCE,
        expected_prior_document_digest: written.document_digest,
      });
      assert.equal(second.decision, "allow");
      const versions = await call(pg, db, WRITER_AGENT, "read-record-source-authority",
        { selector: { kind: "document_versions", document_id: DOCUMENT.neon_identity.document_id } });
      assert.equal(versions.readback.body.length, 2, "both versions are kept");

      // A replay of the first write returns the stored outcome, not a stale refusal.
      const replay = await call(pg, db, WRITER_AGENT, "record-document-identity", {
        idempotency_key: "syn-live-doc-0001", document: DOCUMENT, source: ORIGINAL_SOURCE,
        expected_prior_document_digest: null,
      });
      assert.equal(replay.document_digest, written.document_digest);
    });

    await t.test("a document with no source statement is refused before any write", async () => {
      await assert.rejects(call(pg, db, WRITER_AGENT, "record-document-identity", {
        idempotency_key: "syn-live-doc-nosource", document: DOCUMENT,
      }));
    });
  } finally {
    for (const name of copies) {
      await admin.query(`DROP DATABASE IF EXISTS ${name} WITH (FORCE)`).catch(() => {});
    }
    await admin.end();
  }
});

test("the live proof is wired into the migration class", () => {
  const ci = resolve(HERE, "../../ops/ci.sh");
  assert.ok(existsSync(ci));
  const text = execFileSync("grep", ["-c", "record-source-authority-live-pg.v5.test.mjs", ci]).toString();
  assert.ok(Number(text.trim()) >= 1, "ops/ci.sh must run this proof in the migration class");
});
