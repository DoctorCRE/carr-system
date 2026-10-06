// build-stamp.js — WHAT THIS DEPLOY WAS BUILT FROM, as the deploy wrapper
// stamped it, and the ONE place that reads those stamps.
//
// WHY THIS FILE EXISTS (standing-rule amendment 9, 2026-09-14). The Gate Zero
// producer used to derive the candidate it judges by reading `.git` at request
// time: it walked up for a `.git` directory, read HEAD, resolved the ref and
// zlib-inflated loose objects. The fifth review round's P0 was that the DEPLOYED
// WORKER HAS NO CHECKOUT. Cloudflare serves the bundled modules over a read-only
// virtual filesystem and supplies no `.git` directory, so in production that
// derivation could only ever refuse — every test that passed did so because it
// had staged a local git tree, which is not the condition the code runs in.
//
// SO THE CANDIDATE IS BUILD-TIME METADATA, and the deploy wrapper is what states
// it. bin/deploy-worker.sh already stamps the revision as a Worker var
// (`wrangler deploy --var GIT_SHA:<sha>`), which release.js has read since
// 2026-08-13; amendment 9 adds the SEALED CANDIDATE MANIFEST and its digest
// beside it, by the same mechanism and in the same invocation. `--var` is scoped
// to exactly one upload, so a deploy that bypasses the wrapper carries none of
// them — and a producer with no stamps refuses by name rather than guessing.
//
// THE READS ARE release.js'S OWN, LITERALLY. `stampedGitSha` is the expression
// release.js has always used, lifted here and imported back by that file, so
// "the sha the producer binds" and "the sha /release reports" cannot become two
// different reads of two different things.
//
// WHERE `env` COMES FROM ON EACH SIDE. release.js is handed the request `env`
// and passes it. The producer is reached through a verb handler, which takes
// `(client, actor, args)` and no env, so it passes `serverBuildEnvironment()` —
// `process.env`, which wrangler populates from this Worker's vars and secrets
// whenever `nodejs_compat` is on and the compatibility date is 2025-04-01 or
// later (mcp-server/wrangler.toml: nodejs_compat, 2026-07-01). That is the same
// binding store `env` exposes and the same mechanism identity.js already relies
// on for its credentials; it is readable at module scope and at call time, which
// `env` is not.
//
// NOTHING HERE READS A FILE, A REPOSITORY OR A CLOCK. A stamp is absent or it is
// a string, and an absent stamp is reported as absent.

/** The name of every build stamp bin/deploy-worker.sh writes. Closed. */
export const BUILD_STAMP_NAMES = Object.freeze({
  gitSha: "GIT_SHA",
  candidateManifest: "CANDIDATE_MANIFEST",
  candidateManifestDigest: "CANDIDATE_MANIFEST_DIGEST",
});

/** The schema the stamped manifest declares. A manifest without it is refused. */
export const CANDIDATE_MANIFEST_SCHEMA = "carr-gate-zero-candidate-manifest.v1";

/**
 * The process environment, or an empty object where there is no `process`.
 *
 * Guarded for the same reason identity.js guards its own read: a runtime without
 * the global must degrade to "this build carries no stamps" rather than throw on
 * import.
 */
export function serverBuildEnvironment() {
  try {
    return typeof process === "undefined" || !process.env ? {} : process.env;
  } catch {
    return {};
  }
}

/** The revision this deploy was built from, exactly as /release reports it. */
export function stampedGitSha(env) {
  return (env && env[BUILD_STAMP_NAMES.gitSha]) || null;
}

/** The sealed candidate manifest this deploy was stamped with, as text. */
export function stampedCandidateManifestText(env) {
  return (env && env[BUILD_STAMP_NAMES.candidateManifest]) || null;
}

/** The digest the wrapper computed over that manifest, as text. */
export function stampedCandidateManifestDigest(env) {
  return (env && env[BUILD_STAMP_NAMES.candidateManifestDigest]) || null;
}
