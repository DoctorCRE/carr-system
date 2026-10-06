// identity.test.mjs — unit coverage for the identity → actor mapping (loop
// #227's outside-model attribution work). Pure-function tests only: no KV, no
// Neon, no Google, no Worker runtime. Run with:
//
//   node --test mcp-server/test/identity.test.mjs
//
// This exercises exactly the functions google-oidc.js's handleCallback calls
// at /callback time (slugForEmail, agentSlugForClient, propsForSlug,
// actorFromProps) with fixture inputs, so the attribution logic is provable
// before anything is deployed — the Worker's own auth flow needs a live
// Google round-trip and a real OAuth client registration to exercise for
// real, neither of which this branch can do without a deploy.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  slugForEmail,
  agentSlugForClient,
  verifiedAgentSlugForClient,
  propsForSlug,
  authenticatedIdentity,
  isKnownActor,
  isKnownPartner,
  agentActorForToken,
  continuityActorForTokenMaps,
} from "../src/identity.js";
// The whole namespace, so the removed surfaces can be asserted absent by name.
import * as identityModule from "../src/identity.js";

// `actorFromProps` is module-private under amendment 8 (PR 1013). The exported
// grant door is `authenticatedIdentity.connectionForGrant`; called without the
// server's witness it returns exactly the same actor, unbranded, which is what
// every case in this file is about.
const actorFromProps = (props, bindings = null) =>
  authenticatedIdentity.connectionForGrant(props, bindings);


/**
 * THE FIELDS AN ACTOR CARRIES.
 *
 * Under amendment 8 (2026-09-13) an actor carries NOTHING that marks it as
 * authenticated: the brand is membership of a module-private WeakSet in
 * identity.js, so it is object identity and not a property. The second
 * correction's enumerable Symbol stamp is gone — a property that survives the
 * server's spread survived a forger's spread too — and this helper is now a
 * plain own-entries copy kept for the deep comparisons below.
 */
const fieldsOf = actor => (actor === null ? null : Object.fromEntries(Object.entries(actor)));


test("continuity bearer maps derive one exact surface and sponsor", () => {
  const codex = continuityActorForTokenMaps(
    "Bearer codex-secret",
    JSON.stringify({ joe: "codex-secret" }),
    JSON.stringify({ joe: "claude-secret" }),
  );
  assert.deepEqual(fieldsOf(codex), {
    slug: "codex", display: "Codex", human: false, agent: true,
    via: "codex-continuity-token", client_id: null,
    sponsoring_human_slug: "joe", human_slug: "joe", sponsor_required: false,
    native_agent_verified: true, continuity_surface: "codex",
  });

  const claude = continuityActorForTokenMaps(
    "Bearer claude-secret",
    JSON.stringify({ joe: "codex-secret" }),
    JSON.stringify({ dell: "claude-secret" }),
  );
  assert.equal(claude.slug, "claude");
  assert.equal(claude.sponsoring_human_slug, "dell");
  assert.equal(claude.continuity_surface, "claude");
});

test("continuity bearer maps refuse shared, cross-map duplicate, and malformed credentials", () => {
  assert.equal(continuityActorForTokenMaps(
    "Bearer joe-local", '{"joe":"codex-secret"}', '{"joe":"claude-secret"}'), null);
  assert.equal(continuityActorForTokenMaps(
    "Bearer repeated", '{"joe":"repeated"}', '{"joe":"repeated"}'), null);
  assert.equal(continuityActorForTokenMaps(
    "Bearer secret", '{"mallory":"secret"}', '{}'), null);
  assert.equal(continuityActorForTokenMaps("Bearer secret", '[]', '{}'), null);
});

// ---------- slugForEmail: unchanged behavior, regression-guarded ----------

test("slugForEmail: allow-listed identities resolve, case/space tolerant", () => {
  assert.equal(slugForEmail("joe.bookout.carr.us@gmail.com"), "joe");
  assert.equal(slugForEmail("  Joe.Bookout.CARR.us@GMAIL.com  "), "joe");
  assert.equal(slugForEmail("dell.mccraney.carr.us@gmail.com"), "dell");
});

test("slugForEmail: anything not on the list is refused, not guessed", () => {
  assert.equal(slugForEmail("someone.else@gmail.com"), null);
  assert.equal(slugForEmail("codex@openai.com"), null);
  assert.equal(slugForEmail(""), null);
  assert.equal(slugForEmail(undefined), null);
  assert.equal(slugForEmail(123), null);
});

// ---------- agentSlugForClient: the new attribution override ----------

test("agentSlugForClient: Codex CLI's client_name maps to the codex actor", () => {
  assert.equal(agentSlugForClient("Codex"), "codex");
});

test("agentSlugForClient: exact match is case- and space-tolerant, same discipline as slugForEmail", () => {
  assert.equal(agentSlugForClient("codex"), "codex");
  assert.equal(agentSlugForClient("CODEX"), "codex");
  assert.equal(agentSlugForClient("  Codex  "), "codex");
});

test("agentSlugForClient: grok is NOT yet mapped — the real client_name was never confirmed live, so it must not silently match", () => {
  assert.equal(agentSlugForClient("Grok"), null);
  assert.equal(agentSlugForClient("Grok Build"), null);
  assert.equal(agentSlugForClient("grok-build-cli"), null);
});

test("agentSlugForClient: no fuzzy or substring matching — a name that merely contains 'codex' must not match", () => {
  assert.equal(agentSlugForClient("My Codex Wrapper"), null);
  assert.equal(agentSlugForClient("Codexy"), null);
  assert.equal(agentSlugForClient("NotCodex"), null);
});

test("agentSlugForClient: approved Claude Code resolves to its runtime actor", () => {
  assert.equal(agentSlugForClient("Claude Code"), "claude");
  assert.equal(agentSlugForClient("Claude"), null);
});

test("agentSlugForClient: missing/unusual input degrades to null, never throws", () => {
  assert.equal(agentSlugForClient(undefined), null);
  assert.equal(agentSlugForClient(null), null);
  assert.equal(agentSlugForClient(""), null);
  assert.equal(agentSlugForClient(42), null);
});

test("self-declared client_name is attribution only; exact server client-id binding is authority", () => {
  const bindings = JSON.stringify({ "client-codex-verified": "codex" });
  assert.equal(verifiedAgentSlugForClient("client-codex-verified", "codex", bindings), "codex");
  assert.equal(verifiedAgentSlugForClient("attacker-dynamic-client", "codex", bindings), null);
  assert.equal(verifiedAgentSlugForClient("client-codex-verified", "claude", bindings), null);
  assert.equal(verifiedAgentSlugForClient("client-codex-verified", "codex", "not-json"), null);
});

// ---------- isKnownActor / propsForSlug: codex/grok are now real actors ----------

test("isKnownActor: codex and grok are recognized actor slugs", () => {
  assert.equal(isKnownActor("codex"), true);
  assert.equal(isKnownActor("grok"), true);
  assert.equal(isKnownActor("joe"), true);
  assert.equal(isKnownActor("dell"), true);
});

test("isKnownActor: an unrelated slug is still refused", () => {
  assert.equal(isKnownActor("chatgpt"), false);
  assert.equal(isKnownActor("codex-reviewer"), false); // a different lane entirely (REVIEW_TOKENS)
});

test("propsForSlug: builds valid props for an outside-model actor", () => {
  const props = propsForSlug("codex", { email: "joe.bookout.carr.us@gmail.com", human: false, human_slug: "joe" });
  assert.equal(props.slug, "codex");
  assert.equal(props.display, "Codex");
  assert.equal(props.human, false);
  assert.equal(props.human_slug, "joe");
});

test("propsForSlug: still refuses to build props for a genuinely unknown actor", () => {
  assert.throws(() => propsForSlug("chatgpt"), /unknown actor/);
});

// ---------- actorFromProps: what dispatch() actually sees ----------

test("actorFromProps: an ordinary joe session is unaffected by this loop's changes", () => {
  const actor = actorFromProps({ slug: "joe", email: "joe.bookout.carr.us@gmail.com",
                                  via: "oauth-google", client_id: "abc123" });
  assert.equal(actor.slug, "joe");
  assert.equal(actor.display, "Joe");
  assert.equal(actor.human, true);
  assert.equal(actor.human_slug, null); // no override applied — the actor already IS the human
});

test("actorFromProps: an agent-overridden session attributes to codex, keeps the human on record", () => {
  const actor = actorFromProps({
    slug: "codex", human: false, human_slug: "joe",
    via: "oauth-google", client_id: "client-abc",
  });
  assert.equal(actor.slug, "codex");
  assert.equal(actor.display, "Codex");
  assert.equal(actor.human, false);
  assert.equal(actor.human_slug, "joe");
  assert.equal(actor.client_id, "client-abc");
});

test("actorFromProps: fails closed on missing or unrecognized props", () => {
  assert.equal(actorFromProps(null), null);
  assert.equal(actorFromProps({ slug: "chatgpt" }), null);
  assert.equal(actorFromProps({}), null);
});

// ---------- end-to-end simulation of google-oidc.js's handleCallback decision ----------
//
// The exact decision handleCallback makes (agentSlug || humanSlug, with the
// human/human_slug props only added on an override) reproduced here as a pure
// function of fixture inputs, so the whole derivation is provable without a
// live OAuth round-trip.

function deriveWriteActor(humanSlug, clientName) {
  const agentSlug = agentSlugForClient(clientName);
  const slug = agentSlug || humanSlug;
  const extra = { via: "oauth-google" };
  if (agentSlug) {
    extra.human = false;
    extra.human_slug = humanSlug;
  }
  return actorFromProps(propsForSlug(slug, extra));
}

test("derivation: Joe in Claude Code writes as Claude and preserves Joe as sponsor", () => {
  const actor = deriveWriteActor("joe", "Claude Code");
  assert.equal(actor.slug, "claude");
  assert.equal(actor.human, false);
  assert.equal(actor.human_slug, "joe");
});

test("derivation: Joe in Codex CLI writes as codex, not joe", () => {
  const actor = deriveWriteActor("joe", "Codex");
  assert.equal(actor.slug, "codex");
  assert.equal(actor.human, false);
  assert.equal(actor.human_slug, "joe");
});

test("derivation: Dell in Codex CLI writes as codex, human_slug records dell", () => {
  const actor = deriveWriteActor("dell", "Codex");
  assert.equal(actor.slug, "codex");
  assert.equal(actor.human_slug, "dell");
});

test("derivation: Joe in Grok Build CLI still writes as joe today (grok not yet confirmed) — documents the known gap, not a bug", () => {
  const actor = deriveWriteActor("joe", "Grok Build");
  assert.equal(actor.slug, "joe");
  assert.equal(actor.human, true);
  assert.equal(actor.human_slug, null);
});

test("derivation: a missing/unregistered client_name degrades to the human, never throws", () => {
  const actor = deriveWriteActor("joe", undefined);
  assert.equal(actor.slug, "joe");
});

// ---------- agentActorForToken: the AGENT_TOKENS door (loop #227/#239) ----------
// Grok cannot authenticate over OAuth at all (no per-server login command, no
// dynamic client registration, TUI reports "not authenticated" — verified live
// three ways 2026-08-09), so it reaches /mcp with a bearer instead. These tests
// pin the two properties that make that safe: the actor is the TOOL's own, and
// human is false, which is what makes mcp.js's humanOnly gate refuse teach /
// retire-rule / confirm-merge / new-deal / reassign-deal by construction rather
// than by a list anyone has to maintain.

const AGENT_TOKENS = JSON.stringify({ grok: "grok-secret-fixture", codex: "codex-secret-fixture" });

test("agent token resolves to the tool's own actor, never a human", () => {
  const actor = agentActorForToken("Bearer grok-secret-fixture", AGENT_TOKENS);
  assert.deepEqual(fieldsOf(actor), {
    slug: "grok", display: "Agent (grok)", human: false, agent: true,
    via: "agent-token", client_id: null, sponsoring_human_slug: null,
    human_slug: null, sponsor_required: false,
  });
  // This generic agent remains non-human and unsponsored.
  assert.equal(actor.human, false);
  // And it must NOT carry probe/review, which would pin it to a narrow profile.
  assert.equal(actor.probe, undefined);
  assert.equal(actor.review, undefined);
});

test("agent token matches per-slug, so one tool's secret is not another's", () => {
  assert.equal(agentActorForToken("Bearer codex-secret-fixture", AGENT_TOKENS).slug, "codex");
  assert.equal(agentActorForToken("Bearer grok-secret-fixture", AGENT_TOKENS).slug, "grok");
});

test("agent token accepts the bare token as well as the Bearer form", () => {
  assert.equal(agentActorForToken("grok-secret-fixture", AGENT_TOKENS).slug, "grok");
});

test("agent token fails closed on every bad input", () => {
  for (const [label, header, raw] of [
    ["no header", "", AGENT_TOKENS],
    ["null header", null, AGENT_TOKENS],
    ["unknown token", "Bearer nope", AGENT_TOKENS],
    ["empty map", "Bearer grok-secret-fixture", "{}"],
    ["absent secret", "Bearer grok-secret-fixture", undefined],
    ["unparseable json", "Bearer grok-secret-fixture", "{not json"],
    ["json that is not an object", "Bearer grok-secret-fixture", '"a string"'],
  ]) {
    assert.equal(agentActorForToken(header, raw), null, label);
  }
});

test("agent token refuses a slug that is not a known actor", () => {
  // A typo'd or renamed map key must not mint an identity: DISPLAY is the stop.
  const rogue = JSON.stringify({ "grok-typo": "s3cret", attacker: "s3cret2" });
  assert.equal(agentActorForToken("Bearer s3cret", rogue), null);
  assert.equal(agentActorForToken("Bearer s3cret2", rogue), null);
});

test("an empty-string secret in the map never authenticates", () => {
  // Guards the shape where a slug is provisioned but its secret was never set.
  const blank = JSON.stringify({ grok: "" });
  assert.equal(agentActorForToken("Bearer ", blank), null);
  assert.equal(agentActorForToken("Bearer anything", blank), null);
});

// ---------------------------------------------------------------------------
// LOCAL_TOKENS / joe-local (Phase 1, 2026-08-13, decision 97e76a2f). Closes
// the direct-database bypass in mcp-server/local-verb.mjs: that file is now a
// thin HTTPS client of this Worker, carrying a LOCAL_TOKENS bearer that
// resolves through this SAME function (extended, not duplicated) rather than
// a new profile-matching code path.
// ---------------------------------------------------------------------------

const LOCAL_TOKENS = JSON.stringify({ "joe-local": "local-secret-fixture" });

test("local token resolves to joe-local, human:false, sponsored to joe", () => {
  const actor = agentActorForToken("Bearer local-secret-fixture", LOCAL_TOKENS, "local-token");
  assert.deepEqual(fieldsOf(actor), {
    slug: "joe-local", display: "Agent (joe-local)", human: false, agent: true,
    via: "local-token", client_id: null, sponsoring_human_slug: "joe",
    human_slug: "joe", sponsor_required: false, native_agent_verified: true,
  });
  // A local token remains non-human and is not one of the approved native
  // Codex/Claude authority-bearing runtime identities.
  assert.equal(actor.human, false);
  assert.equal(actor.probe, undefined);
  assert.equal(actor.review, undefined);
});

test("via label defaults to agent-token when omitted, unaffected by the LOCAL_SPONSOR extension", () => {
  const AGENT_TOKENS = JSON.stringify({ grok: "grok-secret-fixture" });
  assert.equal(agentActorForToken("Bearer grok-secret-fixture", AGENT_TOKENS).via, "agent-token");
});

test("codex/grok remain unsponsored even when resolved through the extended function", () => {
  // LOCAL_SPONSOR names 'joe-local' only. Any other slug — including one
  // presented on the LOCAL_TOKENS map by mistake — must stay shared-only.
  assert.equal(agentActorForToken("Bearer codex-secret-fixture",
    JSON.stringify({ codex: "codex-secret-fixture" }), "local-token").sponsoring_human_slug, null);
});

// dell-local (2026-08-18). Dell's Mac gets its own machine door so his
// unattended runs reach the verbs. TWO gates had to open, and the tests below
// pin both: DISPLAY, or isKnownActor refuses the slug and the token is dead on
// arrival; and LOCAL_SPONSOR, or it authenticates but resolves shared-only with
// no personal brain. The second failure is the dangerous one — it looks like it
// works until something reads the wrong scope.

const DELL_LOCAL_TOKENS = JSON.stringify({ "dell-local": "dell-local-secret-fixture" });

test("dell-local resolves to Dell's personal scope, human:false", () => {
  const actor = agentActorForToken("Bearer dell-local-secret-fixture", DELL_LOCAL_TOKENS, "local-token");
  assert.deepEqual(fieldsOf(actor), {
    slug: "dell-local", display: "Agent (dell-local)", human: false, agent: true,
    via: "local-token", client_id: null, sponsoring_human_slug: "dell",
    human_slug: "dell", sponsor_required: false, native_agent_verified: true,
  });
  assert.equal(actor.human, false);
});

test("dell-local is a known actor, so the slug survives the isKnownActor stop", () => {
  assert.equal(isKnownActor("dell-local"), true);
});

test("the two machine doors sponsor different humans and never cross", () => {
  // The whole point of a separate slug: Dell's machine must not write as Joe.
  const both = JSON.stringify({
    "joe-local": "local-secret-fixture",
    "dell-local": "dell-local-secret-fixture",
  });
  assert.equal(agentActorForToken("Bearer local-secret-fixture", both, "local-token").sponsoring_human_slug, "joe");
  assert.equal(agentActorForToken("Bearer dell-local-secret-fixture", both, "local-token").sponsoring_human_slug, "dell");
});

test("a machine door is not a partner: dell-local never passes the humanOnly gate", () => {
  // isKnownPartner backs the humanOnly refusal. 'dell' is a partner; the
  // machine credential carrying his scope is not, and must never become one.
  assert.equal(isKnownPartner("dell-local"), false);
  assert.equal(agentActorForToken("Bearer dell-local-secret-fixture",
    DELL_LOCAL_TOKENS, "local-token").human, false);
});

test("local token is per-slug like every other agent token: a stray key does not widen it", () => {
  const mixed = JSON.stringify({ "joe-local": "local-secret-fixture", grok: "grok-secret-fixture" });
  assert.equal(agentActorForToken("Bearer grok-secret-fixture", mixed, "local-token").sponsoring_human_slug, null);
  assert.equal(agentActorForToken("Bearer local-secret-fixture", mixed, "local-token").sponsoring_human_slug, "joe");
});

// ---------------------------------------------------------------------------
// THE AUTHENTICATION BRAND (2026-09-12, PR 1013; amendment 8, 2026-09-13).
//
// The brand is MEMBERSHIP OF A MODULE-PRIVATE WeakSet, so there is nothing on an
// actor to look at. What is observable from out here is the only thing that ever
// mattered: whether a receipt identity can be derived for it, which the dispatch
// path's own door answers.
// ---------------------------------------------------------------------------

const A_CORRELATION_ID = "00000000-0000-4000-8000-000000000000";
const REVIEW_SECRET = "review-secret-fixture";
const CONTINUITY_TOKENS = JSON.stringify({ joe: "codex-secret" });
const GRANT_WITNESS = "oauth-client-secret-fixture";

/**
 * A SECOND BOOT OF THIS MODULE, WITH THE SERVER'S CREDENTIALS IN PLACE.
 *
 * Amendment 8's fourth correction (PR 1013) deleted `sealServerReviewTokens`:
 * there is no function that installs a credential map, because a one-shot
 * installer settles which caller is FIRST and never which caller is the SERVER,
 * and the reviewer's probe simply arrived first — twice. identity.js reads its
 * credentials ONCE, at module initialisation, out of the environment the server
 * process was started with, which in the deployed Worker is what wrangler
 * populates this Worker's secrets into.
 *
 * So the only way a suite can be the server is to write the fixture secrets into
 * its own environment and boot the module. The statically imported instance at
 * the top of this file was evaluated before any of this ran and holds no
 * credentials at all — which is itself worth knowing, and the last test below
 * asserts it: an unprovisioned boot of this file authenticates nobody.
 */
process.env.REVIEW_TOKENS = JSON.stringify({ "codex-reviewer": REVIEW_SECRET });
process.env.AGENT_TOKENS = AGENT_TOKENS;
process.env.CODEX_CONTINUITY_TOKENS = CONTINUITY_TOKENS;
process.env.GOOGLE_CLIENT_SECRET = GRANT_WITNESS;
const booted = await import("../src/identity.js?server-credentials-in-the-environment");
const REQUIRED_REVIEW_ACTORS = [
  "codex-benchmark-author",
  "codex-benchmark-reviewer",
  "codex-fa-assurance",
  "codex-fa-coverage",
  "codex-fa-execution",
  "codex-fa-foundation",
  "codex-fa-minimum",
  "codex-fa-phi",
  "codex-fa-prompt",
  "codex-fa-secrets",
  "codex-fa-source",
  "codex-reviewer",
  "grok-reviewer",
];
const partialReviewTokens = process.env.REVIEW_TOKENS;
process.env.REVIEW_TOKENS = JSON.stringify(Object.fromEntries(
  REQUIRED_REVIEW_ACTORS.map((slug, index) => [slug, `review-secret-${index}`])));
const fullyStaffed = await import("../src/identity.js?fully-staffed-review-credentials");
process.env.REVIEW_TOKENS = JSON.stringify(Object.fromEntries(
  REQUIRED_REVIEW_ACTORS.map((slug, index) => [slug,
    index < 2 ? "duplicated-review-secret" : `review-secret-${index}`])));
const duplicatedSeat = await import("../src/identity.js?duplicated-review-credential");
process.env.REVIEW_TOKENS = partialReviewTokens;

/**
 * WHAT A BEARER ESTABLISHES, asked the way a route asks.
 *
 * THERE IS NO LONGER A WAY TO ASK IT WITH AN ACTOR (amendment 9, fifth round):
 * `dispatchFor(actor)` was an exported factory returning a callable that ENTERED
 * A CONTEXT, and a probe composed it with the equally public review door.
 *
 * AND SINCE THE SIXTH ROUND THERE IS NO WAY TO ASK IT WITH CODE EITHER. The one
 * entry left standing took a CONTINUATION — `(header, correlationId, run)` — and
 * ran it inside the context, so a probe holding the configured bearer ran its
 * own code as `review_agent` without composing anything. The entry now takes the
 * NAME of one of the server's own entries, resolved in a frozen map identity.js
 * holds itself, and refuses a function in that slot rather than running it.
 *
 * So this helper asks what a bearer plus a NAME establishes. The named entry is
 * the Gate Zero gate, which answers its own refusal object in a process with no
 * stores — enough to tell "served" from "not served", which is all these cases
 * ask. What the identity INSIDE that context is, is asserted end to end in
 * gate-zero-producer.v5.test.mjs, against the receipt the producer mints.
 */
const GATE_ZERO_ENTRY = "seam:gate-zero-read-only-outcome-producer";
const servedForBearer = bearer => booted.serveAuthenticatedCall(
  bearer, A_CORRELATION_ID, GATE_ZERO_ENTRY);

test("the one request entry serves the server's own bearer, and nobody else, and runs no caller code",
  async () => {
    // THE SERVER'S BEARER: served, and what runs inside the context is the
    // server's own entry — the name resolves to code this repository ships.
    const served = await servedForBearer(`Bearer ${REVIEW_SECRET}`);
    assert.notEqual(served, null, "the server's own bearer was not served");
    assert.equal(typeof served, "object");

    // EVERY OTHER BEARER: not served at all.
    for (const bearer of ["Bearer a-bearer-the-caller-chose", "Bearer wrong", "", null,
      `Bearer ${REVIEW_SECRET} `, `bearer ${REVIEW_SECRET}x`])
      assert.equal(await servedForBearer(bearer), null, String(bearer));

    // AND A NAME THE SERVER DOES NOT HOLD RUNS NOTHING, whatever the bearer is —
    // which is the whole difference between a name and a callback. A function is
    // a name the server does not hold, and it is refused rather than called.
    let ran = 0;
    const probe = () => { ran += 1; return "served"; };
    for (const entryName of ["seam:something-else", "", null, undefined, 7,
      "SEAM:GATE-ZERO-READ-ONLY-OUTCOME-PRODUCER", "toString", "constructor",
      "__proto__", probe])
      assert.equal(await booted.serveAuthenticatedCall(`Bearer ${REVIEW_SECRET}`,
        A_CORRELATION_ID, entryName), null, String(entryName));
    assert.equal(ran, 0, "the configured bearer ran caller code inside the context");

    // AND A CORRELATION THE SERVER DID NOT WRITE IS STILL SERVED, with the
    // context CLEARED: the request is answered and it obtains no identity. The
    // gate answers its refusal either way, so what proves the clearing is the
    // producer's own receipt, which is asserted in the producer suite.
    for (const correlationId of [null, "", "not-a-correlation-id", "0000"])
      assert.notEqual(await booted.serveAuthenticatedCall(`Bearer ${REVIEW_SECRET}`,
        correlationId, GATE_ZERO_ENTRY), null, String(correlationId));
  });

test("the router door serves a review-council request and enters no context", async () => {
  // WHAT IT TAKES IS THE REQUEST, not an actor and not a callback. index.js has
  // to serve the council's whole MCP session, and the actor it runs as must be
  // one identity.js minted, because the brand is object identity — so the match
  // and the dispatch are one act inside that file.
  //
  // A FUNCTION IN ANY SLOT IS A REFUSAL. That is the case the sixth review round
  // asked for on this door too: an exported door that accepted a callable would
  // be the same finding one name over.
  let ran = 0;
  const probe = () => { ran += 1; return "served"; };
  for (const shape of [probe, null, undefined, 7, "Bearer x", {}, { headers: {} }])
    assert.equal(await booted.serveReviewRequest(shape, shape, shape), null,
      String(shape));
  assert.equal(ran, 0, "the router door ran a caller's function");

  // AND A REQUEST WHOSE BEARER THE SERVER DOES NOT HOLD IS NOT THIS DOOR'S:
  // null is "not a review-council request", and index.js tries the next door.
  const request = header => ({ headers: { get: name =>
    (name === "authorization" ? header : null) } });
  assert.equal(await booted.serveReviewRequest(
    request("Bearer a-bearer-the-caller-chose"), {}, {}), null);
  assert.equal(await booted.serveReviewRequest(request(null), {}, {}), null);

  // THE SERVER'S OWN BEARER REACHES THE DISPATCH, which is proved by what comes
  // back: mcp.js's own method refusal for a GET, rather than null. It obtains no
  // receipt identity, because this door enters no context at all.
  const dispatched = await booted.serveReviewRequest(
    { ...request(`Bearer ${REVIEW_SECRET}`), method: "GET", url: "https://x/mcp" },
    { CORRELATION_ID: A_CORRELATION_ID }, {});
  assert.notEqual(dispatched, null, "the server's own bearer was not dispatched");
  assert.equal(dispatched.status, 405);
  assert.equal(dispatched.headers.get("x-carr-review-token-coverage"), "1/13");
  assert.equal(dispatched.headers.get("x-carr-review-token-coverage-complete"), "false");
  assert.equal(dispatched.headers.get("x-carr-review-token-duplicates"), "");
  assert.equal(dispatched.headers.get("x-carr-review-token-missing"),
    REQUIRED_REVIEW_ACTORS.filter(slug => slug !== "codex-reviewer").join(","));
  assert.equal(booted.authenticatedIdentity.receiptIdentity(), null);
});

test("review-token coverage readback requires thirteen distinct reachable actors", async () => {
  const request = bearer => new Request("https://x/mcp", {
    method: "GET", headers: { authorization: `Bearer ${bearer}` },
  });
  const full = await fullyStaffed.serveReviewRequest(request("review-secret-3"), {}, {});
  assert.equal(full.headers.get("x-carr-review-token-coverage"), "13/13");
  assert.equal(full.headers.get("x-carr-review-token-coverage-complete"), "true");
  assert.equal(full.headers.get("x-carr-review-token-missing"), "");
  assert.equal(full.headers.get("x-carr-review-token-duplicates"), "");

  const duplicated = await duplicatedSeat.serveReviewRequest(
    request("review-secret-3"), {}, {});
  assert.equal(duplicated.headers.get("x-carr-review-token-coverage"), "11/13");
  assert.equal(duplicated.headers.get("x-carr-review-token-coverage-complete"), "false");
  assert.equal(duplicated.headers.get("x-carr-review-token-missing"),
    "codex-benchmark-author,codex-benchmark-reviewer");
  assert.equal(duplicated.headers.get("x-carr-review-token-duplicates"),
    "codex-benchmark-author,codex-benchmark-reviewer");
});

test("a credential map the CALLER supplies authenticates an actor but establishes nothing", () => {
  // THE LEGACY DOORS, CLOSED WITHOUT AN EXCEPTION. They still TAKE a map,
  // because index.js has always called them that way and the map is the
  // server's own; what a match no longer buys is a context, because no export
  // turns an actor into one.
  const callersMap = JSON.stringify({ grok: "a-secret-the-caller-chose" });
  const callersActor = booted.agentActorForToken("Bearer a-secret-the-caller-chose", callersMap);
  assert.notEqual(callersActor, null, "the door stopped returning its actor");
  assert.equal(callersActor.slug, "grok");

  // AND THE GRANT DOOR IS THE SAME STORY WITH A WITNESS INSTEAD OF A MAP.
  const callersGrant = booted.authenticatedIdentity.connectionForGrant(
    propsForSlug("joe", { via: "oauth-google" }), null, "a-witness-the-caller-chose");
  assert.notEqual(callersGrant, null);

  // WHAT NONE OF THEM CAN DO IS REACH AN IDENTITY, and that is asserted of the
  // whole export surface rather than of these two doors: there is no exported
  // callable that takes an actor and answers a receipt identity, so an actor —
  // however obtained, branded or not — is not a capability.
  for (const [name, value] of Object.entries(booted))
    if (typeof value === "function")
      assert.equal(/dispatch|enter|runIn|contextFor/i.test(name), false,
        `${name} reads like a context entry on the export surface`);
  assert.equal(booted.authenticatedIdentity.receiptIdentity(), null,
    "an identity is readable outside a served request");
});

test("a refused credential authenticates nothing", () => {
  assert.equal(booted.agentActorForToken("Bearer wrong", AGENT_TOKENS), null);
  // NON-VACUOUS: the bearer the server's environment does hold still works.
  assert.notEqual(booted.agentActorForToken("Bearer grok-secret-fixture", AGENT_TOKENS), null);
});

test("a boot with no credentials in its environment authenticates nobody", async () => {
  // THE INSTANCE STATICALLY IMPORTED AT THE TOP OF THIS FILE, which was
  // evaluated before the fixture secrets were written into the environment. It
  // is the same source; what it lacks is the server's credentials, and that is
  // the whole of what authenticating means now.
  assert.equal(await identityModule.serveAuthenticatedCall(`Bearer ${REVIEW_SECRET}`,
    A_CORRELATION_ID, GATE_ZERO_ENTRY), null);
  const unbranded = agentActorForToken("Bearer grok-secret-fixture", AGENT_TOKENS);
  assert.notEqual(unbranded, null, "the door stopped returning its actor");
  assert.equal(authenticatedIdentity.receiptIdentity(), null);
});

test("identity.js exports no context entry, no brander and no dispatcher factory", () => {
  // The removed surfaces, by name, so nothing quietly goes back to one. The last
  // three are amendment 9's: the review door that minted a branded actor, the
  // dispatcher factory that returned a callable entering a context, and the
  // build-context reader whose value was the evaluator's own session relabelled.
  for (const gone of ["reviewActorForToken", "dispatchFor", "dispatchAuthenticatedCall",
    "authenticatedCallReceiptIdentity", "runInAuthenticatedCall", "authenticatedCallIdentity",
    "actorFromProps", "sealServerReviewTokens", "buildContext", "committerIdentity",
    "enterAuthenticatedCall", "serveReviewRequestAuthenticated"]) {
    assert.equal(Object.hasOwn(identityModule, gone), false, `${gone} is still exported`);
    assert.equal(Object.hasOwn(authenticatedIdentity, gone), false,
      `${gone} is still on the authenticated-identity surface`);
  }
  assert.ok(Object.isFrozen(authenticatedIdentity));
  // AND THE SURFACE IS EXACTLY THREE READERS. A member added here is a member
  // added in a diff somebody reviews.
  assert.deepEqual(Object.keys(authenticatedIdentity).sort(),
    ["connectionForGrant", "partnerIdentity", "receiptIdentity"]);
});

test("a partner slug maps to the class this system derives for it, and nothing else does", () => {
  // WHAT THIS REPLACED. Until amendment 9 this was a COMMITTER table — Gmail and
  // GitHub-noreply addresses mapped to partner slugs — because the Gate Zero
  // producer took its subject maker from HEAD's committer line. The maker is the
  // release-candidate record now, an authenticated authority write, so a git
  // address is an input to nothing and the table that turned one into a partner
  // is deleted rather than left standing unused.
  const partner = authenticatedIdentity.partnerIdentity;
  for (const slug of ["joe", "JOE", "  joe  "])
    assert.deepEqual(partner(slug), { actor_id: "joe", authority_class: "verified_partner" }, slug);
  assert.deepEqual(partner("dell"), { actor_id: "dell", authority_class: "verified_partner" });
  // A MACHINE SEAT IS NOT A PARTNER, and neither is anything that merely looks
  // like a slug: what comes back is an attribution this system registers, or
  // nothing at all.
  for (const slug of ["codex-reviewer", "codex", "grok", "carr-release-bot",
    "someone-else", "", null, undefined, 7, { toString: () => "joe" }])
    assert.equal(partner(slug), null, String(slug));
});
