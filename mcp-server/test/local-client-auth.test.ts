// Test local client credential selection and isolation between profiles.

import assert from "node:assert/strict";
import test from "node:test";

import {
  LOCAL_CLIENT_PROFILE_NAMES,
  selectLocalClientCredential,
  tokenFileSecurityIssue,
} from "../local-client-auth.ts";

const tokenFile = [
  "CARR_MCP_LOCAL_TOKEN=joe-secret",
  "CARR_HERMES_MCP_TOKEN=hermes-secret",
  "CARR_HERMES_COS_MCP_TOKEN=cos-secret",
  "CARR_CODEX_CONTINUITY_MCP_TOKEN=codex-continuity-secret",
].join("\n");

test("ordinary calls keep the local-machine credential", () => {
  const selected = selectLocalClientCredential({}, tokenFile);
  assert.equal(selected.token, "joe-secret");
  assert.equal(selected.profile, "local");
  assert.equal(selected.tokenVariable, "CARR_MCP_LOCAL_TOKEN");
  assert.equal(selected.workerSecret, "LOCAL_TOKENS");
});

test("the queue projector selects the separate Hermes credential", () => {
  const selected = selectLocalClientCredential({ CARR_MCP_CLIENT_PROFILE: "hermes-projector" }, tokenFile);
  assert.equal(selected.token, "hermes-secret");
  assert.equal(selected.profile, "hermes-projector");
  assert.equal(selected.tokenVariable, "CARR_HERMES_MCP_TOKEN");
  assert.equal(selected.workerSecret, "HERMES_TOKENS_EXTRA");
  assert.doesNotMatch(selected.identityNotice, /hermes-secret|joe-secret/);
});

test("the CoS client selects only its distinct credential and Worker secret", () => {
  const selected = selectLocalClientCredential({ CARR_MCP_CLIENT_PROFILE: "hermes-cos" }, tokenFile);
  assert.equal(selected.token, "cos-secret");
  assert.equal(selected.profile, "hermes-cos");
  assert.equal(selected.tokenVariable, "CARR_HERMES_COS_MCP_TOKEN");
  assert.equal(selected.workerSecret, "HERMES_COS_TOKENS");
  assert.doesNotMatch(selected.identityNotice, /cos-secret|hermes-secret|joe-secret/);
});

test("client profiles never borrow another profile's credential", () => {
  assert.equal(
    selectLocalClientCredential({ CARR_MCP_CLIENT_PROFILE: "hermes-cos" }, "CARR_HERMES_MCP_TOKEN=projector-only\n")
      .token,
    "",
  );
  assert.equal(
    selectLocalClientCredential({ CARR_MCP_CLIENT_PROFILE: "hermes-projector" }, "CARR_HERMES_COS_MCP_TOKEN=cos-only\n")
      .token,
    "",
  );
  assert.equal(selectLocalClientCredential({}, "CARR_HERMES_COS_MCP_TOKEN=cos-only\n").token, "");
  assert.deepEqual(
    [...LOCAL_CLIENT_PROFILE_NAMES],
    ["local", "unattended", "codex-continuity", "hermes-projector", "hermes-cos"],
  );
});

test("Codex continuity selects its isolated token and Worker map", () => {
  const codex = selectLocalClientCredential({ CARR_MCP_CLIENT_PROFILE: "codex-continuity" }, tokenFile);
  assert.deepEqual(
    { token: codex.token, variable: codex.tokenVariable, secret: codex.workerSecret },
    {
      token: "codex-continuity-secret",
      variable: "CARR_CODEX_CONTINUITY_MCP_TOKEN",
      secret: "CODEX_CONTINUITY_TOKENS",
    },
  );
  assert.equal(
    selectLocalClientCredential(
      { CARR_MCP_CLIENT_PROFILE: "codex-continuity" },
      "CARR_CLAUDE_CONTINUITY_MCP_TOKEN=wrong-surface\n",
    ).token,
    "",
  );
});

test("a missing Hermes credential never falls back to joe-local", () => {
  const selected = selectLocalClientCredential(
    { CARR_MCP_CLIENT_PROFILE: "hermes-projector" },
    "CARR_MCP_LOCAL_TOKEN=joe-secret\n",
  );
  assert.equal(selected.token, "");
  assert.equal(selected.tokenVariable, "CARR_HERMES_MCP_TOKEN");
});

test("unknown client profiles fail closed", () => {
  assert.throws(
    () => selectLocalClientCredential({ CARR_MCP_CLIENT_PROFILE: "joe" }, tokenFile),
    /unsupported local MCP client profile/,
  );
});

test("persistent MCP credential files require exact owner-controlled 0600 metadata", () => {
  const secure = { mode: 0o100600, uid: 501, isFile: true, isSymbolicLink: false };
  assert.equal(tokenFileSecurityIssue(secure, 501), null);
  assert.equal(tokenFileSecurityIssue({ ...secure, mode: 0o100644 }, 501), "token file mode must be 600");
  assert.equal(tokenFileSecurityIssue({ ...secure, mode: 0o100400 }, 501), "token file mode must be 600");
  assert.equal(tokenFileSecurityIssue({ ...secure, uid: 502 }, 501), "token file must be owned by the current user");
  assert.equal(tokenFileSecurityIssue({ ...secure, isFile: false }, 501), "token file must be a regular file");
  assert.equal(
    tokenFileSecurityIssue({ ...secure, isSymbolicLink: true }, 501),
    "token file must not be a symbolic link",
  );
});

test("environment credential takes precedence and file values are trimmed and unquoted", () => {
  assert.equal(
    selectLocalClientCredential({ CARR_MCP_LOCAL_TOKEN: "env-token" }, "CARR_MCP_LOCAL_TOKEN=file-token").token,
    "env-token",
  );
  assert.equal(selectLocalClientCredential({}, "  CARR_MCP_LOCAL_TOKEN= 'file-token'  \n").token, "file-token");
});

test("unattended profile never borrows the local credential", () => {
  const selected = selectLocalClientCredential(
    { CARR_MCP_CLIENT_PROFILE: "unattended" },
    "CARR_MCP_LOCAL_TOKEN=local-token",
  );
  assert.equal(selected.token, "");
  assert.equal(selected.workerSecret, "UNATTENDED_TOKENS");
});
