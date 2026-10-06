import test from "node:test";
import assert from "node:assert/strict";

import {
  LOCAL_CLIENT_PROFILE_NAMES, selectLocalClientCredential, tokenFileSecurityIssue,
} from "../local-client-auth.mjs";
import { hermesActorForToken, hermesCosActorForToken } from "../src/identity.js";

const tokenFile = [
  "CARR_MCP_LOCAL_TOKEN=joe-secret",
  "CARR_HERMES_MCP_TOKEN=hermes-secret",
  "CARR_HERMES_COS_MCP_TOKEN=cos-secret",
  "CARR_CODEX_CONTINUITY_MCP_TOKEN=codex-continuity-secret",
  "CARR_CLAUDE_CONTINUITY_MCP_TOKEN=claude-continuity-secret",
].join("\n");

test("ordinary calls keep the local-machine credential", () => {
  const selected = selectLocalClientCredential({}, tokenFile);
  assert.equal(selected.token, "joe-secret");
  assert.equal(selected.profile, "local");
  assert.equal(selected.tokenVariable, "CARR_MCP_LOCAL_TOKEN");
  assert.equal(selected.workerSecret, "LOCAL_TOKENS");
});

test("the queue projector selects the separate Hermes credential", () => {
  const selected = selectLocalClientCredential(
    { CARR_MCP_CLIENT_PROFILE: "hermes-projector" }, tokenFile);
  assert.equal(selected.token, "hermes-secret");
  assert.equal(selected.profile, "hermes-projector");
  assert.equal(selected.tokenVariable, "CARR_HERMES_MCP_TOKEN");
  assert.equal(selected.workerSecret, "HERMES_TOKENS_EXTRA");
  assert.doesNotMatch(selected.identityNotice, /hermes-secret|joe-secret/);
  const serverActor = hermesActorForToken(
    `Bearer ${selected.token}`, JSON.stringify({ "hermes-pilot": "hermes-secret" }));
  assert.deepEqual({ slug: serverActor.slug, sponsor: serverActor.sponsoring_human_slug,
    via: serverActor.via, hermes: serverActor.hermes }, {
    slug: "hermes-pilot", sponsor: "joe", via: "hermes-token", hermes: true,
  }, "the selected client credential resolves to exact server-derived reader provenance");
});

test("the CoS client selects only its distinct credential and Worker secret", () => {
  const selected = selectLocalClientCredential(
    { CARR_MCP_CLIENT_PROFILE: "hermes-cos" }, tokenFile);
  assert.equal(selected.token, "cos-secret");
  assert.equal(selected.profile, "hermes-cos");
  assert.equal(selected.tokenVariable, "CARR_HERMES_COS_MCP_TOKEN");
  assert.equal(selected.workerSecret, "HERMES_COS_TOKENS");
  assert.doesNotMatch(selected.identityNotice, /cos-secret|hermes-secret|joe-secret/);
  const serverActor = hermesCosActorForToken(
    `Bearer ${selected.token}`, JSON.stringify({ "hermes-pilot": "cos-secret" }));
  assert.deepEqual({ slug: serverActor.slug, sponsor: serverActor.sponsoring_human_slug,
    via: serverActor.via, hermesCos: serverActor.hermesCos }, {
    slug: "hermes-pilot", sponsor: "joe", via: "hermes-cos-token", hermesCos: true,
  });
});

test("client profiles never borrow another profile's credential", () => {
  assert.equal(selectLocalClientCredential(
    { CARR_MCP_CLIENT_PROFILE: "hermes-cos" }, "CARR_HERMES_MCP_TOKEN=projector-only\n").token, "");
  assert.equal(selectLocalClientCredential(
    { CARR_MCP_CLIENT_PROFILE: "hermes-projector" }, "CARR_HERMES_COS_MCP_TOKEN=cos-only\n").token, "");
  assert.equal(selectLocalClientCredential(
    {}, "CARR_HERMES_COS_MCP_TOKEN=cos-only\n").token, "");
  assert.deepEqual([...LOCAL_CLIENT_PROFILE_NAMES], [
    "local", "codex-continuity", "claude-continuity", "hermes-projector", "hermes-cos",
  ]);
});

test("native continuity profiles select independent tokens and Worker maps", () => {
  const codex = selectLocalClientCredential(
    { CARR_MCP_CLIENT_PROFILE: "codex-continuity" }, tokenFile);
  const claude = selectLocalClientCredential(
    { CARR_MCP_CLIENT_PROFILE: "claude-continuity" }, tokenFile);
  assert.deepEqual(
    { token: codex.token, variable: codex.tokenVariable, secret: codex.workerSecret },
    { token: "codex-continuity-secret", variable: "CARR_CODEX_CONTINUITY_MCP_TOKEN",
      secret: "CODEX_CONTINUITY_TOKENS" },
  );
  assert.deepEqual(
    { token: claude.token, variable: claude.tokenVariable, secret: claude.workerSecret },
    { token: "claude-continuity-secret", variable: "CARR_CLAUDE_CONTINUITY_MCP_TOKEN",
      secret: "CLAUDE_CONTINUITY_TOKENS" },
  );
  assert.equal(selectLocalClientCredential(
    { CARR_MCP_CLIENT_PROFILE: "codex-continuity" },
    "CARR_CLAUDE_CONTINUITY_MCP_TOKEN=wrong-surface\n").token, "");
});

test("a missing Hermes credential never falls back to joe-local", () => {
  const selected = selectLocalClientCredential(
    { CARR_MCP_CLIENT_PROFILE: "hermes-projector" }, "CARR_MCP_LOCAL_TOKEN=joe-secret\n");
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
  assert.match(tokenFileSecurityIssue({ ...secure, mode: 0o100644 }, 501), /mode must be 600/);
  assert.match(tokenFileSecurityIssue({ ...secure, mode: 0o100400 }, 501), /mode must be 600/);
  assert.match(tokenFileSecurityIssue({ ...secure, uid: 502 }, 501), /owned by the current user/);
  assert.match(tokenFileSecurityIssue({ ...secure, isFile: false }, 501), /regular file/);
  assert.match(tokenFileSecurityIssue({ ...secure, isSymbolicLink: true }, 501), /symbolic link/);
});
