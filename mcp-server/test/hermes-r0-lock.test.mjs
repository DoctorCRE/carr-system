// hermes-r0-lock.test.mjs — the twelve R0 refusal cases, run against the real
// server logic instead of against a fixture that asserts itself.
//
// PROVENANCE. phase0/hermes-runtime-evaluation-fixtures.v1.json has carried
// twelve expected outcomes since 2026-08-12 and had never been executed: its
// capability profile (`hermes_r0_readonly_pilot`) and its three named verbs
// existed nowhere in the server, so there was nothing for the cases to run
// against. This file is what makes them a test rather than a description.
//
// WHAT CHANGED IN THE MAPPING, stated plainly rather than quietly. The fixture
// names three bespoke read verbs (carr.deal_health.read_v1,
// carr.doctrine_excerpt.read_v1, carr.runtime_context_metadata.read_v1). None
// was built, and building them would have duplicated deal-board, read-doctrine
// and standing-context, which already return exactly those three things. Rule
// 49c627cc says to match a capability on BEHAVIOR, never on a verb name, so the
// allow cases assert the behaviour the fixture asked for — reads reach the
// runtime — against the verbs that actually serve it. The refusal cases are
// unchanged in substance, because a refusal is the half that has to be exact.
//
// The boundary being tested is held credentials and autonomous authority, never
// confidentiality (rule d7f74c93). Joe closed the exposure question on
// 2026-08-12, and every frontier council seat, Grok included, has read entire
// CARR doctrines already.
//
//   node --test mcp-server/test/hermes-r0-lock.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  hermesActorForToken,
  hermesActorForTokenMaps,
  agentActorForToken,
  authorizationClassForActor,
  personalScopeForActor,
} from "../src/identity.js";
import { PROFILES, allowedIn, profileForActor } from "../src/mcp.js";

const TOKENS = JSON.stringify({ "hermes-pilot": "s3cr3t-r0" });
const BEARER = "Bearer s3cr3t-r0";
const actor = () => hermesActorForToken(BEARER, TOKENS);

// A stand-in for the tool registry's shape. `write` and `fullOnly` are the only
// two fields allowedIn reads.
const READ = { write: false };
const WRITE = { write: true };
const FULL_ONLY = { write: false, fullOnly: true };

// ---------- the door itself ----------

test("a matching HERMES_TOKENS bearer resolves to a locked actor sponsored by Joe", () => {
  const a = actor();
  assert.equal(a.slug, "hermes-pilot");
  assert.equal(a.hermes, true);
  assert.equal(a.human, false, "never a human seat: humanOnly verbs refuse on this");
  assert.equal(a.sponsoring_human_slug, "joe",
    "Joe sponsored the pilot 2026-08-16 so it can carry his personal work");
  assert.equal(a.via, "hermes-token", "the door is legible in tool_call rows");
});

test("an additive projector token resolves without replacing the primary Hermes map", () => {
  const projected = hermesActorForTokenMaps("Bearer projector-secret", TOKENS,
    JSON.stringify({ "hermes-pilot": "projector-secret" }));
  assert.equal(projected.slug, "hermes-pilot");
  assert.equal(projected.via, "hermes-token");
  assert.equal(hermesActorForTokenMaps("Bearer wrong", TOKENS, "{}"), null);
});

test("an unlisted Hermes slug stays shared-only", () => {
  // Sponsorship is a named entry, never a property of holding a Hermes token.
  const other = hermesActorForToken("Bearer other-secret",
    JSON.stringify({ "hermes-scratch": "other-secret" }));
  assert.equal(other.hermes, true);
  assert.equal(other.sponsoring_human_slug, null,
    "a slug absent from HERMES_SPONSOR gets no personal brain");
});

test("the door fails closed on every malformed input", () => {
  assert.equal(hermesActorForToken("", TOKENS), null, "no header");
  assert.equal(hermesActorForToken(BEARER, "{not json"), null, "unparseable map");
  assert.equal(hermesActorForToken(BEARER, "[]"), null, "non-object map");
  assert.equal(hermesActorForToken(BEARER, "{}"), null, "empty map");
  assert.equal(hermesActorForToken("Bearer wrong", TOKENS), null, "token matches nothing");
  assert.equal(hermesActorForToken(BEARER, JSON.stringify({ x: "" }), null), null, "empty secret never matches");
});

test("a Hermes token is not an outside-model CLI token", () => {
  // AGENT_TOKENS actors take profileFor(request) and can therefore ask for a
  // writing profile. If the two doors ever collapsed into one, this fails.
  assert.equal(agentActorForToken(BEARER, TOKENS), null,
    "the Hermes secret must not authenticate through the CLI door");
});

// ---------- the lock ----------

test("dispatch forces the hermes profile and ignores ?profile=", () => {
  const a = actor();
  for (const asked of [null, "full", "capture", "away", "read", "probe", "reviewer", "nonsense"]) {
    const url = asked === null
      ? "https://api.doctorcre.com/mcp"
      : `https://api.doctorcre.com/mcp?profile=${asked}`;
    assert.equal(profileForActor(a, new Request(url)), "hermes",
      `?profile=${asked} must not widen a Hermes token`);
  }
});

test("the hermes business-write set is a strict subset of capture", () => {
  // The set is written out rather than spread from capture, so a verb added to
  // capture never lands in a persistent daemon's hands unreviewed. This is the
  // assertion that keeps the two honest in the other direction: hermes may
  // never hold a verb capture does not.
  const runtimeOnly = new Set(["project-room-queue", "record-dispatch-link"]);
  for (const verb of PROFILES.hermes) {
    if (runtimeOnly.has(verb)) continue;
    assert.ok(PROFILES.capture.has(verb),
      `${verb} is in hermes but not in capture — hermes must never exceed the additive set`);
  }
  assert.ok(PROFILES.hermes.size - runtimeOnly.size < PROFILES.capture.size,
    "strict subset: hermes deliberately excludes the investigation verbs");
  assert.equal(allowedIn("hermes", "project-room-queue", WRITE), true,
    "the server-bound projector door is an allowed runtime write");
  assert.equal(allowedIn("hermes", "record-dispatch-link", WRITE), true,
    "the server-bound dispatch-link door is an allowed runtime write");
});

test("the investigation verbs stay out of the hermes set", () => {
  // An investigation is a chain of reasoning a human is steering. A runtime
  // dropping evidence into one mid-flight changes what the adjudicator sees.
  for (const verb of ["record-signal", "record-branch-evidence"]) {
    assert.ok(PROFILES.capture.has(verb), `${verb} should still be in capture`);
    assert.equal(allowedIn("hermes", verb, WRITE), false, `${verb} must refuse under hermes`);
  }
});

// ---------- HERMES-CASE-001: an allowed read reaches the runtime ----------

test("HERMES-CASE-001 — reads are allowed", () => {
  for (const verb of ["deal-board", "read-doctrine", "standing-context", "catch-me-up", "find"]) {
    assert.equal(allowedIn("hermes", verb, READ), true, `${verb} should reach R0`);
  }
});

// ---------- HERMES-CASE-002..005: the client cannot select its own identity ----------
//
// These four are satisfied by the actor shape rather than by a per-call check:
// the server derives identity from the token, and callTool's
// assertNoCallerAuthorityFields refuses caller-claimed authority arguments
// outright. What this file proves is the half those cases actually turn on —
// that the derived identity is unsponsored and shared-only, so there is no
// personal brain for a client hint to point at.

test("HERMES-CASE-002/003/004 — the derived class is never a verified partner", () => {
  assert.notEqual(authorizationClassForActor(actor()), "verified_partner",
    "a Hermes token must never resolve to a partner seat, sponsored or not");
});

test("HERMES-CASE-005 — the brain is Joe's and only Joe's", () => {
  // Joe sponsored the pilot on 2026-08-16 so it can carry his personal work.
  // The scope is derived from HERMES_SPONSOR server-side; no argument, model
  // name, or client hint selects it, and Dell's brain is not reachable from
  // this actor by any path.
  const scope = personalScopeForActor(actor());
  assert.equal(scope.status, "personal");
  assert.equal(scope.sponsor, "joe");
  assert.notEqual(scope.sponsor, "dell", "cross-brain reads have no target here");
});

test("HERMES-CASE-010 — an unsponsored Hermes runtime still resolves, shared-only", () => {
  const other = hermesActorForToken("Bearer other-secret",
    JSON.stringify({ "hermes-pilot": "other-secret" }));
  const scope = personalScopeForActor({ ...other, sponsoring_human_slug: null, human_slug: null });
  assert.equal(scope.status, "none", "no sponsor means shared metadata only, never an error");
});

// ---------- HERMES-CASE-006: mutations refuse ----------

test("HERMES-CASE-006 — the consequential write verbs still refuse", () => {
  // Joe granted the additive set on 2026-08-16. These are the ones that stayed
  // out, and they are the substance of the boundary: destroy, re-point
  // ownership, create a party, advance a deal, touch a rule, draft for a client.
  for (const verb of ["update-deal", "new-client", "new-lead", "new-vendor", "add-party",
                      "confirm-merge", "reassign-deal", "set-lead", "log-decision",
                      "activate-rule", "retire-rule", "prepare-document", "close-loop"]) {
    assert.equal(allowedIn("hermes", verb, WRITE), false, `${verb} must refuse under hermes`);
  }
});

test("the granted additive verbs are allowed", () => {
  for (const verb of ["log-activity", "stamp-touch", "add-loop", "update-loop",
                      "set-next-action", "complete-action", "add-critical-date",
                      "record-finding", "record-defect"]) {
    assert.equal(allowedIn("hermes", verb, WRITE), true, `${verb} should be callable`);
  }
});

test("raw room writing stays out while the shape-checked projector door is allowed", () => {
  assert.equal(allowedIn("hermes", "add-room-turn", WRITE), false,
    "the unattended runtime must not gain an arbitrary transcript writer");
  assert.equal(allowedIn("hermes", "project-room-queue", WRITE), true);
});

test("close-loop is excluded even though add-loop is granted", () => {
  // add-loop opens a task; close-loop asserts an outcome, which is a claim
  // about work having been finished. capture does not hold it either.
  assert.equal(allowedIn("hermes", "add-loop", WRITE), true);
  assert.equal(allowedIn("hermes", "close-loop", WRITE), false);
});

// ---------- HERMES-CASE-007: humanOnly verbs refuse ----------

test("HERMES-CASE-007 — humanOnly verbs refuse on a machine actor", () => {
  assert.equal(actor().human, false,
    "mcp.js's humanOnly gate keys on this; activate-rule and friends refuse by construction");
  assert.equal(allowedIn("hermes", "activate-rule", WRITE), false);
});

// ---------- HERMES-CASE-008/009: no channels, no scheduler ----------

test("HERMES-CASE-008/009 — message delivery and scheduling are absent", () => {
  // There is no send verb in this system at all (one human gate: Claude drafts,
  // Joe sends) and no scheduler verb. Absence is the control; this asserts the
  // absence rather than assuming it.
  for (const verb of ["channel.send", "cron.create", "send-message", "schedule-task"]) {
    assert.equal(allowedIn("hermes", verb, WRITE), false, `${verb} must not be reachable`);
  }
});

// ---------- HERMES-CASE-011/012: no engineering dispatch in R0 ----------

test("HERMES-CASE-011/012 — request creation and engineering dispatch refuse", () => {
  for (const verb of ["carr.engineering_work_request.create_v1", "carr.engineering_dispatch.execute_v1"]) {
    assert.equal(allowedIn("hermes", verb, WRITE), false,
      `${verb} graduates behind its own separate gate, never by R0 succeeding`);
  }
});

// ---------- the fullOnly carve-out ----------

test("sensitive operational reads stay off R0", () => {
  assert.equal(allowedIn("hermes", "integrity-digest", FULL_ONLY), false,
    "fullOnly reads are withheld from every locked profile, R0 included");
});
