// release.js — builds the /release payload: what code this Worker is
// actually running, so a verification pass never has to guess (Phase 1,
// 2026-08-13, closing the deploy-provenance gap named in the Phase 0 audit).
//
// WHY A SEPARATE FILE. index.js imports OAuthProvider from
// @cloudflare/workers-oauth-provider, which pulls in `cloudflare:` runtime
// modules — that file cannot be loaded outside the Worker runtime, so
// nothing implemented there is provable before a deploy (see index.js's
// "local token" comment, same reasoning identity.js was split out for).
// This module holds no cloudflare: import and no OAuth machinery, so
// node --test can exercise the actual payload-building logic with a fake
// env and a fake `sql` tag function — no live database, no deploy required.
//
// THE FIELDS, EACH HONEST ON ITS OWN:
//   verb_count          — Object.keys(TOOLS).length, computed from the code
//                          bundled INTO THIS DEPLOY. Never a written marker
//                          (mcp-server/.last-deployed-verb-count is exactly
//                          that, and going stale is the gap this endpoint
//                          closes) — this number cannot lie about what
//                          shipped because it is read from the shipped code.
//   git_sha              — the ONE field with no runtime source: a Worker
//                          cannot read git at request time. Stamped in at
//                          deploy time as env.GIT_SHA (bin/deploy-worker.sh:
//                          `wrangler deploy --var GIT_SHA:<sha>`), and read
//                          through build-stamp.js's `stampedGitSha` — the one
//                          read, shared with the Gate Zero producer, which binds
//                          its candidate to this same stamp. Missing
//                          means a deploy happened OUTSIDE that script —
//                          reported as null + a reason, never guessed or
//                          left silently absent.
//   provider / worker_version — Cloudflare's runtime version-metadata binding,
//                          not a source digest or a value supplied by the
//                          deploy wrapper. `id` is the immutable Worker version
//                          identity that Cloudflare is actually serving; `tag`
//                          and `timestamp` are provider metadata. Local dev or
//                          an older deployment can lack the binding, in which
//                          case the entire identity is visibly unknown.
//   schema                — schema_migrations is tools/migrate.py's ledger
//                          of applied migrations. Read through v_schema_ledger
//                          (0113), a two-column view over schema_migrations,
//                          because carr_reader — the role this endpoint's
//                          DATABASE_URL_READER connection uses — is a
//                          views-only role by design (0004); the endpoint's
//                          first live call found that gap (permission denied
//                          on schema_migrations direct) and 0113 closed it
//                          with a view rather than a base-table grant, to
//                          keep that posture intact. It is NOT ground truth
//                          either way: tools/ledger-repair.py exists because
//                          this exact table drifted from reality once already
//                          (four migrations physically applied to production
//                          outside the runner were invisible to it until
//                          repaired 2026-07-31). So this field reports what
//                          the TRACKING TABLE CLAIMS, and says so in its own
//                          `note`, every time.
//   command_contract      — the SCAC mutation registry identity this deploy is
//                          actually running, from mutation-registry.js's own
//                          mutationManifestIdentity() — the same identity
//                          tools.js binds into every write's request_hash, read
//                          here rather than reimplemented, so there is no second
//                          registry. It is a code-identity field: a digest of a
//                          checked-in generated file, not an environment
//                          discriminator and not a secret. Q058 needs a
//                          command-contract version to compare and this payload
//                          is the only place a caller could read one.
//   doctrine_generation   — same query doctrine.js's standing-context verb
//                          already runs (`select generation from
//                          doctrine_meta where id = 1`), reused rather than
//                          reinvented.
//   program6_actions      — exact public posture of the reviewed browser
//                          mutation flag. It is derived by the same parser as
//                          the Deal Room gate and never echoes arbitrary env.
//
// A field that cannot be read returns { value: null, reason: "<why>" } (or
// the schema-shaped equivalent) — never omitted, never a guess. A stale or
// absent value must be visibly absent, per the honesty requirement this was
// built against.

import { stampedGitSha } from "./build-stamp.js";
import { mutationManifestIdentity } from "./mutation-registry.js";
import { program6ActionPosture } from "./program6-feature-flag.js";
import { workspaceCommandCenterPosture } from "./workspace-feature-flag.js";

export async function buildRelease({ env, sql, verbCount, now = () => new Date() }) {
  // THE SAME READ THE GATE ZERO PRODUCER BINDS ITS CANDIDATE TO, and it is the
  // same function rather than the same expression written twice — amendment 9,
  // 2026-09-14. See build-stamp.js for why the producer cannot read git.
  const sha = stampedGitSha(env);
  const versionMetadata = env && env.CF_VERSION_METADATA;
  const versionId = versionMetadata && typeof versionMetadata.id === "string"
    ? versionMetadata.id.trim()
    : "";
  const workerVersion = versionId
    ? {
      id: versionId,
      tag: typeof versionMetadata.tag === "string" ? versionMetadata.tag : null,
      timestamp: typeof versionMetadata.timestamp === "string" ? versionMetadata.timestamp : null,
      reason: null,
    }
    : {
      id: null,
      tag: null,
      timestamp: null,
      reason: versionMetadata
        ? "CF_VERSION_METADATA has no version id; no Cloudflare Worker version identity was observed"
        : "CF_VERSION_METADATA binding is unavailable; no Cloudflare Worker version identity was observed",
    };

  // These readbacks are independent and Neon executes each tagged query over
  // its own HTTP request. Start both before awaiting either so /release pays
  // the slower database round trip once, while each field still degrades on
  // its own if only one source is unavailable.
  const schemaPromise = (async () => {
    try {
      const rows = await sql`
        select count(*)::int as applied_count, (max(filename collate "C") collate "default") as highest_applied_migration,
               'sha256:' || encode(public.digest(coalesce(string_agg(
                 convert_to(filename, 'UTF8') || decode('00', 'hex') ||
                 convert_to(sha256, 'UTF8') || decode('0a', 'hex'),
                 ''::bytea order by filename collate "C"), ''::bytea),
                 'sha256'), 'hex') as ledger_sha256
          from v_schema_ledger`;
      const row = (rows && rows[0]) || {};
      return {
        highest_applied_migration: row.highest_applied_migration ?? null,
        applied_count: row.applied_count != null ? Number(row.applied_count) : 0,
        ledger_sha256: row.ledger_sha256 ?? null,
        reason: null,
      };
    } catch (e) {
      return {
        highest_applied_migration: null,
        applied_count: null,
        ledger_sha256: null,
        reason: "database unreachable: " + String((e && e.message) || e).slice(0, 200),
      };
    }
  })();
  const doctrinePromise = (async () => {
    try {
      const rows = await sql`select generation from doctrine_meta where id = 1`;
      return rows && rows.length
        ? { value: Number(rows[0].generation), reason: null }
        : { value: null, reason: "doctrine_meta has no row with id=1" };
    } catch (e) {
      return {
        value: null,
        reason: "database unreachable: " + String((e && e.message) || e).slice(0, 200),
      };
    }
  })();
  const [schema, doctrineGeneration] = await Promise.all([schemaPromise, doctrinePromise]);

  // WHICH DEPLOYMENT AM I? Added 2026-08-14, the day a staging Worker deployed
  // without `routes = []`, inherited all three production domains, and served
  // api.doctorcre.com for about two minutes.
  //
  // Nothing else in this payload can answer that question. `schema` cannot, and
  // that is not a gap but a consequence of the design: staging's database is
  // built from db/schema.sql, production's COMMITTED structure INCLUDING its
  // schema_migrations ledger, so highest_applied_migration and applied_count are
  // byte-identical between the two environments by construction. git_sha cannot
  // either — both environments deploy the same commit. verb_count and
  // doctrine_generation happen to differ today, but only because staging holds
  // no doctrine rows yet; neither is a declaration of identity, and reading them
  // as one is the same "a signal that happens to correlate" mistake this whole
  // endpoint exists to stop.
  //
  // So the endpoint whose entire purpose is "what is production running?" would
  // have answered, during the incident, with a confident and completely
  // plausible payload describing the wrong Worker. It reports what it IS now,
  // from a per-environment var, declared rather than inferred. `unknown` when
  // the var is absent, never a guess and never a default of "production" — an
  // unlabelled deployment claiming to be production is the failure itself.
  const environment = (env && env.CARR_ENV) || null;

  return {
    ok: true,
    ts: now().toISOString(),
    env: environment
      ? { value: environment, reason: null }
      : { value: "unknown", reason: "CARR_ENV not set on this Worker — an unlabelled deployment is never assumed to be production" },
    verb_count: verbCount,
    provider: "cloudflare-workers",
    worker_version: workerVersion,
    git_sha: sha
      ? { value: sha, reason: null }
      : { value: null, reason: "not stamped: deployed outside bin/deploy-worker.sh" },
    schema: {
      highest_applied_migration: schema.highest_applied_migration,
      applied_count: schema.applied_count,
      ledger_sha256: schema.ledger_sha256,
      reason: schema.reason,
      note: "what the tracking table claims, not ground truth — tools/ledger-repair.py "
          + "records a past drift where schema_migrations fell behind migrations already "
          + "applied to production",
    },
    // Read from the registry the runtime itself dispatches against, so this can
    // no more lie about the shipped command contract than verb_count can about
    // the shipped verbs. Additive: every existing consumer reads this payload
    // field by field.
    command_contract: mutationManifestIdentity(),
    doctrine_generation: doctrineGeneration,
    // This is intentionally a public boolean posture, not a secret value. The
    // checked-in Wrangler configuration is fingerprinted in each release plan,
    // so changing false→true requires a reviewed immutable version promotion.
    program6_actions: program6ActionPosture(env),
    workspace_command_center: workspaceCommandCenterPosture(env),
  };
}
