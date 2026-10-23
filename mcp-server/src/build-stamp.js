// Build stamp names shared by release metadata and candidate fixtures.

/** The name of every build stamp the GitHub production deployment workflow writes. Closed. */
export const BUILD_STAMP_NAMES = Object.freeze({
  gitSha: "GIT_SHA",
  candidateManifest: "CANDIDATE_MANIFEST",
  candidateManifestDigest: "CANDIDATE_MANIFEST_DIGEST",
});

/** The schema the stamped manifest declares. A manifest without it is refused. */
export const CANDIDATE_MANIFEST_SCHEMA = "carr-gate-zero-candidate-manifest.v1";

/** The revision this deploy was built from, exactly as /release reports it. */
export function stampedGitSha(env) {
  return (env && env[BUILD_STAMP_NAMES.gitSha]) || null;
}
