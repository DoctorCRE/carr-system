// Pure credential selection for local-verb.mjs.  The caller selects a bounded
// client purpose; the Worker still derives identity from the bearer token.
// Token values never appear in the returned notice or in process arguments.

const CLIENT_PROFILES = Object.freeze({
  local: Object.freeze({
    tokenVariable: "CARR_MCP_LOCAL_TOKEN",
    identityNotice: "local machine actor (via local-token)",
    workerSecret: "LOCAL_TOKENS",
  }),
  "codex-continuity": Object.freeze({
    tokenVariable: "CARR_CODEX_CONTINUITY_MCP_TOKEN",
    identityNotice: "Codex continuity actor (via codex-continuity-token)",
    workerSecret: "CODEX_CONTINUITY_TOKENS",
  }),
  "claude-continuity": Object.freeze({
    tokenVariable: "CARR_CLAUDE_CONTINUITY_MCP_TOKEN",
    identityNotice: "Claude continuity actor (via claude-continuity-token)",
    workerSecret: "CLAUDE_CONTINUITY_TOKENS",
  }),
  "hermes-projector": Object.freeze({
    tokenVariable: "CARR_HERMES_MCP_TOKEN",
    identityNotice: "Hermes queue projector (via hermes-token)",
    workerSecret: "HERMES_TOKENS_EXTRA",
  }),
  "hermes-cos": Object.freeze({
    tokenVariable: "CARR_HERMES_COS_MCP_TOKEN",
    identityNotice: "Hermes chief-of-staff client (via hermes-cos-token)",
    workerSecret: "HERMES_COS_TOKENS",
  }),
});

export const LOCAL_CLIENT_PROFILE_NAMES = Object.freeze(Object.keys(CLIENT_PROFILES));

function tokenFromFile(raw, variable) {
  for (const line of String(raw || "").split("\n")) {
    const text = line.trim();
    if (text.startsWith(`${variable}=`))
      return text.slice(variable.length + 1).trim().replace(/^['"]|['"]$/g, "");
  }
  return "";
}

export function selectLocalClientCredential(env = {}, tokenFileRaw = "") {
  const profile = env.CARR_MCP_CLIENT_PROFILE || "local";
  const config = CLIENT_PROFILES[profile];
  if (!config)
    throw new Error(`unsupported local MCP client profile: ${profile}`);
  const token = env[config.tokenVariable] || tokenFromFile(tokenFileRaw, config.tokenVariable);
  return { profile, token, ...config };
}

// Credential files are long-lived bearer stores.  Refuse symlinks, foreign
// ownership, non-regular files, and anything except the documented 0600 mode.
// Kept pure so the exact policy has unit coverage without touching a real
// user's credential store.
export function tokenFileSecurityIssue(metadata, expectedUid) {
  if (metadata.isSymbolicLink) return "token file must not be a symbolic link";
  if (!metadata.isFile) return "token file must be a regular file";
  if (expectedUid !== undefined && metadata.uid !== expectedUid)
    return "token file must be owned by the current user";
  if ((metadata.mode & 0o777) !== 0o600) return "token file mode must be 600";
  return null;
}
