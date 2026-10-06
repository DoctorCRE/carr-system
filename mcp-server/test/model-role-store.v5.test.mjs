// Unit tests for the DoctorCRE v5 durable role-description store.
//
// HOW TO RUN EXACTLY THESE CASES:
//   cd mcp-server && node --test test/model-role-store.v5.test.mjs
// `npm test` in that directory runs `node --test test/*.test.js test/*.test.mjs`,
// which is the WHOLE repository suite. A green `npm test` is therefore not a
// statement about this file and must not be reported as one; name this file.
//
// THE DATABASE HERE IS A MOCK, AND NOTHING IN THIS FILE IS EVIDENCE THAT
// ANYTHING WAS STORED. `mockDatabase` records the statements the module issues
// and returns the rows the test hands it. That is enough to prove what the
// module SENDS, what it REFUSES, and what it refuses to send — and it is not,
// and is not treated as, a claim about durable behaviour. The
// transaction-scoped proofs against a real PostgreSQL live in
// model-role-store-postgres.sql. Migration 0542 promotes the reviewed candidate;
// these unit tests alone do not prove that a live database has applied it.
//
// THE LAST FOUR CASES READ ops/model-role-store.candidate.sql AS TEXT and assert
// what it does and does not contain — chiefly that it holds no migration program:
// no ALTER TABLE, no ADD COLUMN, no DROP CONSTRAINT, no backfill. That is a
// static source check, it is labelled as one where it sits, and it is evidence
// about a FILE rather than about a database.
//
// The pure half of this file is stronger than the mocked half, and deliberately
// so: the round trip, the digest binding, the readback revalidation and every
// refusal of a forged or edited role are properties of functions that touch
// nothing. The mock exists only to show which parameters cross the boundary and
// which calls never happen at all.
//
// THREE THINGS THIS FILE CANNOT REACH, named rather than left implicit:
//   * CONCURRENCY. A stale compare-and-swap losing a race, and a second
//     creation colliding with a first, are properties of the lock and the
//     ledger inside PostgreSQL. What is asserted here is that the module states
//     the expectation EXPLICITLY on every call and carries the creation
//     assertion as its own parameter rather than as a null the database has to
//     interpret. model-role-store-postgres.sql exercises the refusals.
//   * THE SQL SIDE OF THE NON-EMPTY-TEXT RULE. What is pinned here is the
//     ECMAScript half, exhaustively: every code point `String.prototype.trim`
//     strips, and no other. ops.model_role_ecmascript_whitespace() enumerates
//     the same list by code point so the two can be compared, and
//     model-role-store-postgres.sql asserts the SQL half against that list on a
//     live server. Neither file can prove the other's engine.
//   * WHETHER A HUMAN WAS PRESENT. The module evaluates an actor object the
//     server built and the record layer derives its own two principals; this
//     file can show that all three are compared and that a disagreement refuses.
//     It cannot show that the runtime that built the actor object was honest,
//     and `principal_binding.unbound_seam` says so on every result.

import test from "node:test";
import assert from "node:assert/strict";
// Read-only, and only to assert what ops/model-role-store.candidate.sql CONTAINS.
// Nothing in this file executes SQL or opens a database connection.
import { readFileSync } from "node:fs";
import { digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import { V5_SYSTEM_AUTHORITY_PARTNER } from "../src/global-boundaries.v5.js";
import {
  V5_ROLE_DESCRIPTION_SCHEMA_VERSION, V5_ROLE_KEYS, assignOccupant, defineRole,
} from "../src/model-routing.v5.js";
import {
  MODEL_ROLE_CANDIDATE_INSTALL_SCOPE,
  MODEL_ROLE_CONTENT_FIELDS, MODEL_ROLE_CURRENT_POINTER_ACTION,
  MODEL_ROLE_ECMASCRIPT_TRIM_CODE_POINTS,
  MODEL_ROLE_READBACK_SCHEMA, MODEL_ROLE_READ_COMPATIBILITY_SCOPE,
  MODEL_ROLE_REF_FIELDS, MODEL_ROLE_REVISION_ENTRY_SCHEMA,
  MODEL_ROLE_REVISION_ROWS_SCHEMA, MODEL_ROLE_SCALAR_FIELDS, MODEL_ROLE_TEXT_FIELDS,
  ModelRoleStoreError, assertNoSelfAssertedAuthority, deriveRoleCurrentPointerAuthority,
  modelRoleCanonicalBytes, modelRoleFromPreimage, modelRoleFromRows, modelRolePreimage,
  modelRoleRevisionRows, modelRoleStorePrerequisites, modelRoleStoreTools,
  validateRoleDescription,
} from "../src/model-role-store.v5.js";

// --- fixtures ---------------------------------------------------------------

const copy = value => JSON.parse(JSON.stringify(value));
const hex = cp => `U+${cp.toString(16).toUpperCase().padStart(4, "0")}`;

const REVISION_ID = "11111111-2222-4333-8444-555555555555";
const REVISION_ID_2 = "22222222-3333-4444-8555-666666666666";
const POINTER_ID = "33333333-4444-4555-8666-777777777777";
const ACTOR_ID = "44444444-5555-4666-8777-888888888888";
const KEY = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
const NOW = "2026-09-09T12:00:00.000Z";

/**
 * One synthetic role description. It is a FIXTURE for the mechanism and is not
 * a proposal of any real DoctorCRE role: nothing in the module or the candidate
 * SQL seeds, defaults or hardcodes a role, and this object exists only in this
 * test file.
 *
 * The reference sets are supplied UNSORTED and the text lists in a deliberate
 * order, because the two behave differently in the digest and the tests below
 * turn on that difference.
 */
function role(overrides = {}) {
  return {
    role_key: "reviewer",
    title: "Reviewer",
    mission: "Establish independently that delivered work meets its stated contract.",
    skills: [
      "read a diff against the contract it claims to satisfy",
      "reproduce a claimed finding from source rather than from a report",
    ],
    rules: [
      "never grade work you produced",
      "a refusal names the exact missing fact, authority or dependency",
    ],
    authority: {
      authority_class: "developer",
      capability_refs: ["source.read", "receipt.read", "evidence.read"],
    },
    evidence_requirements: [
      "the exact command run and its output",
      "the file and line a finding rests on",
    ],
    quality_floor_refs: ["floor.independent_review", "floor.evidence_bound"],
    minimum_strength_ref: "strength.high_risk_engineering",
    task_classes: ["review.source", "review.contract"],
    ...overrides,
  };
}

const SEALED = defineRole(role());

/** One revision entry shaped exactly as ops.model_role_revision_readback emits it. */
function entry(description = role(), overrides = {}) {
  const sealed = defineRole(description);
  return {
    schema_version: MODEL_ROLE_REVISION_ENTRY_SCHEMA,
    revision_id: REVISION_ID,
    role_key: sealed.role_key,
    revision_no: 1,
    // The ROLE CONTRACT version this revision was stored under, which the record
    // layer reports so a reader can refuse a version it cannot revalidate by
    // name rather than failing somewhere inside a rebuild.
    role_schema_version: V5_ROLE_DESCRIPTION_SCHEMA_VERSION,
    role_digest: sealed.role_digest,
    recomputed_role_digest: sealed.role_digest,
    structure_error: null,
    recorded_at: "2026-09-09T11:59:00.000Z",
    preimage: copy(modelRolePreimage(description)),
    confers_authority: false,
    occupant_bound: false,
    measured_qualification_bound: false,
    integrity: "recomputed_from_committed_rows",
    ...overrides,
  };
}

/** One whole readback shaped exactly as ops.model_role_readback emits it. */
function readback({ current = null, history = [entry()], pointer_history = [] } = {}) {
  return {
    schema_version: MODEL_ROLE_READBACK_SCHEMA,
    role_schema_version: V5_ROLE_DESCRIPTION_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    role_key: "reviewer",
    current,
    history,
    pointer_history,
    integrity: "recomputed_from_committed_rows",
    signed: false,
    capability_token_issued: false,
    confers_authority: false,
    occupant_bound: false,
    measured_qualification_bound: false,
  };
}

/**
 * The pointer fields ops.model_role_current_revision merges onto an entry.
 *
 * TWO PRINCIPALS, NOT ONE. `set_by_partner_slug` is the authority LOGIN scope,
 * which partner-authority.js shares with that partner's sponsored agents;
 * `set_by_acting_actor_slug` is who actually acted, derived on the same
 * transaction from the server-established actor context. The authority fields
 * are POINTER-prefixed because the role's own declared class lives at
 * `preimage.authority.authority_class` and can never be system_authority.
 */
function currentEntry(description = role(), overrides = {}) {
  return {
    ...entry(description),
    pointer_id: POINTER_ID,
    pointer_no: 1,
    expected_prior_revision_no: null,
    set_by_partner_slug: V5_SYSTEM_AUTHORITY_PARTNER,
    set_by_acting_actor_id: ACTOR_ID,
    set_by_acting_actor_slug: V5_SYSTEM_AUTHORITY_PARTNER,
    pointer_authority_class: "system_authority",
    pointer_authority_grant_kind: "retained_system_authority",
    set_at: "2026-09-09T12:00:01.000Z",
    ...overrides,
  };
}

// --- actors -----------------------------------------------------------------
//
// Built the way identity.js's actorFromProps builds them, so the authority
// derivations under test read the same shapes the server hands a verb.

const JOE = Object.freeze({ slug: "joe", display: "Joe", human: true, via: "oauth-google" });
const DELL = Object.freeze({ slug: "dell", display: "Dell", human: true, via: "oauth-google" });
const SPONSORED_AGENT = Object.freeze({
  slug: "codex", display: "Codex", human: false, via: "oauth-google",
  sponsoring_human_slug: "joe", human_slug: "joe",
});

// --- the mock database and tool harness -------------------------------------

/**
 * A statement recorder. Responses are matched on a substring of the SQL, so a
 * test declares which function it expects to be called; a statement with no
 * declared response throws, which is how an unexpected query fails a test
 * rather than passing silently.
 */
function mockDatabase(responses = {}) {
  const calls = [];
  const events = [];
  return {
    calls, events,
    query: async (sql, params = []) => {
      calls.push({ sql, params });
      for (const [needle, rows] of Object.entries(responses)) {
        if (sql.includes(needle)) return { rows };
      }
      throw new Error(`mock database has no declared response for: ${sql}`);
    },
  };
}

class ToolError extends Error {
  constructor(payload) {
    super(payload.error);
    this.name = "ToolError";
    this.payload = payload;
  }
}

// The real withEnvelope opens a transaction and records a tool-call envelope.
// Here it only invokes the body: these tests are about what the handler does,
// and a mock pretending to be transactional would be claiming durability this
// file explicitly does not claim.
const withEnvelope = async (_c, _actor, _verb, _args, fn) => fn();
// The audit helper is POSITIONAL — writeEvent(client, actor, verb, subjectType,
// subjectId, fields) — and this double records every argument so a handler that
// passes an options object fails here instead of at the event.actor_id NOT NULL
// constraint in production. Defect 65ed5e3e-db28-498a-a0e7-84ae53658dea.
const writeEvent = async (c, actor, verb, subjectType, subjectId, fields) => {
  c.events.push({ actor, verb, subjectType, subjectId, fields });
};

const tools = modelRoleStoreTools({ withEnvelope, writeEvent, ToolError });

const toolError = async (promise) => {
  try { await promise; } catch (error) {
    assert.equal(error.name, "ToolError", `expected a ToolError, got ${error?.name}: ${error?.message}`);
    return error.payload;
  }
  assert.fail("expected a refusal");
};

/** The declared mock for a successful current-pointer move. */
const pointerDatabase = (current = currentEntry()) => mockDatabase({
  "model_role_server_instant": [{ now: NOW }],
  "model_role_set_current_revision": [{ id: POINTER_ID }],
  "model_role_readback": [{ readback: readback({ current }) }],
});

// ---------------------------------------------------------------------------
// The role contract is the kernel's, and is not restated here.
// ---------------------------------------------------------------------------

test("the stored content fields are exactly the fields defineRole hashes", () => {
  const derived = ["effects", "occupant_bound", "occupants_replaceable", "role_digest",
    "role_confers_no_authority_by_itself"];
  const contentKeys = Object.keys(SEALED).filter(key => !derived.includes(key)).sort();
  // A live equality rather than a copied list: if defineRole ever gains or
  // loses a content field, this fails here instead of silently storing a role
  // the record layer could not rebuild.
  assert.deepEqual(contentKeys, [...MODEL_ROLE_CONTENT_FIELDS]);
});

test("every content field is reachable from a scalar, a text list, a ref set or a constant", () => {
  const persisted = new Set([
    ...MODEL_ROLE_SCALAR_FIELDS, ...MODEL_ROLE_TEXT_FIELDS, ...MODEL_ROLE_REF_FIELDS,
    "authority", "role_key", "schema_version", "tenant",
  ]);
  assert.deepEqual(MODEL_ROLE_CONTENT_FIELDS.filter(field => !persisted.has(field)), []);
});

test("validateRoleDescription reuses the kernel's refusals rather than restating them", () => {
  assert.throws(() => validateRoleDescription(role({ role_key: "chief_of_staff" })),
    error => error.name === "V5RoutingError" && error.code === "unknown_role_key");
  assert.throws(() => validateRoleDescription(role({ quality_floor_refs: [] })),
    error => error.name === "V5RoutingError" && error.code === "invalid_shape");
  assert.throws(() => validateRoleDescription(role({ task_classes: [] })),
    error => error.name === "V5RoutingError" && error.code === "invalid_shape");
  // A quality floor is a NAMED REFERENCE. A number is not one, and this rail
  // declares no numeric business floor of its own to fall back on.
  assert.throws(() => validateRoleDescription(role({ quality_floor_refs: [0.95] })),
    error => error.name === "V5RoutingError" && error.code === "invalid_shape");
});

test("a role occupied by a replaceable model may not declare retained system authority", () => {
  assert.throws(
    () => validateRoleDescription(role({
      authority: { authority_class: "system_authority", capability_refs: [] },
    })),
    error => error.name === "V5RoutingError" &&
      error.code === "role_authority_class_not_occupiable");
});

// ---------------------------------------------------------------------------
// The non-empty-text rule, which the candidate SQL has to match code point for
// code point. If it does not, a direct writer-bundle call can store text
// defineRole refuses — and because reads revalidate every revision and this rail
// has no repair path, that role's reads would refuse permanently.
// ---------------------------------------------------------------------------

test("the exported trim set is EXACTLY what String.prototype.trim strips, and nothing else", () => {
  // Both directions, and the second one is the one that matters. A list that is
  // merely a SUBSET would let the SQL side admit content the kernel refuses; a
  // list that is a SUPERSET would make the SQL side refuse content the kernel
  // accepts. Neither is "safer" — both are a second, disagreeing contract.
  for (const cp of MODEL_ROLE_ECMASCRIPT_TRIM_CODE_POINTS) {
    assert.equal(String.fromCodePoint(cp).trim(), "",
      `${hex(cp)} is in the exported set but String.prototype.trim does not strip it`);
  }
  // Exhaustive over the Basic Multilingual Plane, because "and nothing else" is
  // not checkable by reading. Every trimmed code point is a BMP one, so this
  // enumeration is complete rather than a sample.
  const stripped = [];
  for (let cp = 0; cp <= 0xffff; cp += 1) {
    if (String.fromCharCode(cp).trim() === "") stripped.push(cp);
  }
  assert.deepEqual(stripped, [...MODEL_ROLE_ECMASCRIPT_TRIM_CODE_POINTS],
    "the exported set is not exactly the set this engine's String.prototype.trim strips");
  assert.equal(MODEL_ROLE_ECMASCRIPT_TRIM_CODE_POINTS.length, 25);
});

test("the code points ECMAScript does NOT strip are not treated as whitespace", () => {
  // U+0085 NEL is a control, not a Space_Separator; U+180E stopped being a
  // Space_Separator in Unicode 6.3. A SQL rule that trimmed either would refuse
  // role text the kernel accepts, so both must stay out of the set — and the
  // kernel must keep accepting them, which is what is asserted here.
  for (const cp of [0x0085, 0x180e]) {
    assert.notEqual(String.fromCodePoint(cp).trim(), "",
      `${hex(cp)} must not be trimmed`);
    assert.equal(MODEL_ROLE_ECMASCRIPT_TRIM_CODE_POINTS.includes(cp), false);
    assert.doesNotThrow(() => validateRoleDescription(role({ title: String.fromCodePoint(cp) })));
  }
});

test("a whitespace-only title, mission, skill, rule or evidence requirement is refused", () => {
  // Every code point in the set, in every text position the record layer stores.
  // These are the values a copy-paste actually produces: a stray tab, a bare
  // newline, an NBSP from a word processor, a BOM from a UTF-8 file.
  for (const cp of MODEL_ROLE_ECMASCRIPT_TRIM_CODE_POINTS) {
    const ws = String.fromCodePoint(cp);
    for (const override of [
      { title: ws }, { mission: ws },
      { skills: [ws, "an ordinary second skill"] },
      { rules: [ws, "an ordinary second rule"] },
      { evidence_requirements: [ws] },
    ]) {
      assert.throws(() => modelRoleRevisionRows(role(override)),
        error => error.name === "V5RoutingError" && error.code === "invalid_shape",
        `${hex(cp)} alone in ${Object.keys(override)[0]} must be refused as empty`);
    }
  }
});

test("a RUN of whitespace code points is still empty, and still refused", () => {
  const everything = MODEL_ROLE_ECMASCRIPT_TRIM_CODE_POINTS
    .map(cp => String.fromCodePoint(cp)).join("");
  // Built from code points wherever possible: a test about invisible characters
  // that itself contains invisible characters is one no reviewer can check. The
  // two literal entries at the end of the list are kept on purpose -- they are
  // what a paste from a UTF-8 file and from a CJK editor actually look like --
  // and every entry is asserted to trim to empty before it is used, so a
  // mistyped one fails loudly here rather than passing silently.
  const ch = cp => String.fromCodePoint(cp);
  for (const value of [everything, everything + everything,
    ch(0x00a0) + ch(0x00a0), ch(0x000d) + ch(0x000a), ch(0x2028), ch(0x205f),
"\t\n", " ﻿", "　 "]) {
    assert.equal(value.trim(), "");
    assert.throws(() => modelRoleRevisionRows(role({ skills: [value, "second"] })),
      error => error.code === "invalid_shape");
  }
});

test("legitimate embedded whitespace is accepted and stored VERBATIM", () => {
  // The other half of the rule, and the one a too-eager gate would break. A tab
  // inside a sentence, a real newline in a mission, an NBSP between words and a
  // trailing space are all legitimate role text.
  //
  // THE STRINGS BELOW CARRY LITERAL INVISIBLE CHARACTERS ON PURPOSE: an NBSP
  // (U+00A0) inside the first skill and a line separator (U+2028) inside the
  // first rule, beside the escaped \t and \n. They are literal rather than
  // built from code points because the point of this case is that content
  // arriving from a real paste survives storage unchanged. `nbsp` and `lineSep`
  // above name the same two code points, and the equality assertions below
  // compare against the fixture itself, so an editor that normalized either one
  // fails the VERBATIM assertions rather than quietly weakening the test.
  const nbsp = String.fromCodePoint(0x00a0);
  const lineSep = String.fromCodePoint(0x2028);
  const embedded = role({
    title: "  Reviewer (embedded whitespace fixture)  ",
    mission: "Mission line one.\nMission line two after a real newline.",
    skills: [
      "a skill with a\ttab, a no-break space and a trailing space ",
      "an ordinary second skill",
    ],
    rules: ["a rule split across a Unicode line separator", "an ordinary second rule"],
  });
  // THE FIXTURE GUARD, and it runs before anything is asserted about storage.
  // If an editor, a formatter or a careless paste ever replaced the NBSP or the
  // line separator with an ordinary space, every assertion below would still
  // pass while testing nothing this case is about. These two lines make that
  // failure loud.
  assert.ok(embedded.skills[0].includes(nbsp),
    "the embedded-whitespace fixture lost its U+00A0; this case no longer tests an NBSP");
  assert.ok(embedded.rules[0].includes(lineSep),
    "the embedded-whitespace fixture lost its U+2028; this case no longer tests a line separator");
  assert.ok(embedded.skills[0].includes("\t") && embedded.mission.includes("\n"));
  // Each is non-empty to the kernel even though each CONTAINS trimmed code
  // points, which is the distinction the whole rule turns on.
  assert.notEqual(embedded.skills[0].trim(), "");
  assert.notEqual(embedded.rules[0].trim(), "");

  const rows = modelRoleRevisionRows(embedded);

  // VERBATIM, not normalized. defineRole hashes the value as supplied, so a
  // store that trimmed on the way in would hash to something no proposer
  // computed and every readback would then refuse.
  assert.equal(rows.scalars.title, embedded.title);
  assert.equal(rows.scalars.mission, embedded.mission);
  assert.equal(rows.texts.find(r => r.field === "skills" && r.ordinal === 0).value,
    embedded.skills[0]);
  assert.equal(rows.texts.find(r => r.field === "rules" && r.ordinal === 0).value,
    embedded.rules[0]);
  assert.deepEqual(modelRolePreimage(embedded).skills, embedded.skills);

  // And it round-trips to the identical digest.
  assert.equal(modelRoleFromRows(rows).role_digest, defineRole(embedded).role_digest);

  // TRIMMING WOULD BE A DIFFERENT ROLE, which is why the store must not do it.
  const trimmed = role({
    ...embedded,
    title: embedded.title.trim(),
    skills: embedded.skills.map(s => s.trim()),
  });
  assert.notEqual(defineRole(trimmed).role_digest, defineRole(embedded).role_digest);
});

test("the projection carries the exact trim rule the record layer must match", () => {
  const p = modelRoleStorePrerequisites();
  assert.equal(p.nonempty_text_rule, "ECMAScript String.prototype.trim, code point for code point");
  assert.deepEqual(p.nonempty_text_trim_code_points, [...MODEL_ROLE_ECMASCRIPT_TRIM_CODE_POINTS]);
});

// ---------------------------------------------------------------------------
// Round trip, order, and the digest binding.
// ---------------------------------------------------------------------------

test("rows round-trip to the identical role digest", () => {
  const rows = modelRoleRevisionRows(role());
  assert.equal(rows.schema_version, MODEL_ROLE_REVISION_ROWS_SCHEMA);
  assert.equal(rows.role_digest, SEALED.role_digest);
  const rebuilt = modelRoleFromRows(rows);
  assert.equal(rebuilt.role_digest, SEALED.role_digest);
  assert.deepEqual(rebuilt.skills, SEALED.skills);
  assert.deepEqual(rebuilt.rules, SEALED.rules);
  assert.deepEqual(rebuilt.task_classes, SEALED.task_classes);
  assert.equal(rebuilt.authority.authority_class, SEALED.authority.authority_class);
  // The Q106 properties survive storage, because they are properties of the
  // kernel's own product rather than of anything recorded beside it.
  assert.equal(rebuilt.occupant_bound, false);
  assert.equal(rebuilt.occupants_replaceable, true);
  assert.equal(rebuilt.role_confers_no_authority_by_itself, true);
});

test("the digest covers the WHOLE content: changing any stored field changes it", () => {
  // Field by field rather than "a digest exists". A decomposition that dropped
  // one field on the way to storage would still round-trip and still hash
  // consistently — it would simply be hashing a role that is not the one
  // supplied. Every field a caller can set is mutated here in turn.
  const mutations = {
    role_key: { role_key: "builder" },
    title: { title: "Reviewer, renamed" },
    mission: { mission: "Approve whatever the builder produced." },
    skills: { skills: [...role().skills, "a third skill"] },
    rules: { rules: [...role().rules, "a third rule"] },
    authority_class: { authority: { ...role().authority, authority_class: "release_admin" } },
    capability_refs: {
      authority: { ...role().authority, capability_refs: [...role().authority.capability_refs, "plan.read"] },
    },
    evidence_requirements: { evidence_requirements: [...role().evidence_requirements, "a third requirement"] },
    quality_floor_refs: { quality_floor_refs: [...role().quality_floor_refs, "floor.third"] },
    minimum_strength_ref: { minimum_strength_ref: "strength.ordinary" },
    task_classes: { task_classes: [...role().task_classes, "review.third"] },
  };
  const seen = new Map();
  for (const [name, override] of Object.entries(mutations)) {
    const mutated = modelRoleRevisionRows(role(override)).role_digest;
    assert.notEqual(mutated, SEALED.role_digest, `${name} does not participate in the role digest`);
    // And no two mutations collide, so each field is distinguished from the
    // others rather than merely from the baseline.
    assert.equal(seen.has(mutated), false,
      `${name} and ${seen.get(mutated)} hash to the same digest`);
    seen.set(mutated, name);
  }
  // The two EMITTED constants participate too, checked where they are reachable:
  // on the preimage, since no caller can set them through defineRole.
  const preimage = copy(modelRolePreimage(role()));
  preimage.tenant = "someone-else";
  assert.notEqual(digest(copy(preimage)), SEALED.role_digest);
});

test("the preimage is the exact object defineRole hashes", () => {
  const preimage = modelRolePreimage(role());
  assert.deepEqual(Object.keys(preimage).sort(), [...MODEL_ROLE_CONTENT_FIELDS]);
  assert.equal(preimage.schema_version, V5_ROLE_DESCRIPTION_SCHEMA_VERSION);
  assert.equal(preimage.tenant, ORGANIZATION_TENANT_ID);
  assert.equal(digest(copy(preimage)), SEALED.role_digest);
  // No domain tag is wrapped around it, so the canonical bytes begin with the
  // first C-sorted key of the object rather than with an array. The candidate
  // SQL asserts the same shape from its own side.
  assert.ok(modelRoleCanonicalBytes(role()).startsWith('{"authority":'));
});

test("text order participates in the digest and reference order does not", () => {
  const reorderedText = modelRoleRevisionRows(role({ skills: [...role().skills].reverse() }));
  assert.notEqual(reorderedText.role_digest, SEALED.role_digest);

  const reorderedRefs = modelRoleRevisionRows(role({
    task_classes: [...role().task_classes].reverse(),
    quality_floor_refs: [...role().quality_floor_refs].reverse(),
  }));
  assert.equal(reorderedRefs.role_digest, SEALED.role_digest);
});

test("a gap in a stored list refuses rather than rebuilding a shorter role", () => {
  const rows = copy(modelRoleRevisionRows(role()));
  const skill = rows.texts.find(row => row.field === "skills" && row.ordinal === 1);
  skill.ordinal = 2;
  assert.throws(() => modelRoleFromRows(rows),
    error => error instanceof ModelRoleStoreError && error.code === "model_role_row_ordinal_gap");
});

test("a stored digest that drifts from the stored body refuses", () => {
  const rows = copy(modelRoleRevisionRows(role()));
  rows.role_digest = `sha256:${"a".repeat(64)}`;
  assert.throws(() => modelRoleFromRows(rows),
    error => error instanceof ModelRoleStoreError && error.code === "model_role_digest_mismatch");
});

test("an edited body no longer hashes to its recorded digest", () => {
  const rows = copy(modelRoleRevisionRows(role()));
  rows.scalars.mission = "Approve whatever the builder produced.";
  assert.throws(() => modelRoleFromRows(rows),
    error => error instanceof ModelRoleStoreError && error.code === "model_role_digest_mismatch");
});

test("rows filed under a role they do not describe refuse", () => {
  const rows = copy(modelRoleRevisionRows(role()));
  rows.role_key = "builder";
  // The rebuilt role still reseals — its own role_key is inside the hashed
  // bytes — so the refusal is about the FILING, and it is named as such.
  assert.throws(() => modelRoleFromRows(rows),
    error => error instanceof ModelRoleStoreError &&
      (error.code === "model_role_key_mismatch" || error.code === "model_role_digest_mismatch"));
});

// ---------------------------------------------------------------------------
// Readback revalidation: a forged preimage cannot buy authenticity with a hash.
// ---------------------------------------------------------------------------

test("a preimage is revalidated through defineRole and rehashed", () => {
  const sealed = modelRoleFromPreimage(copy(modelRolePreimage(role())));
  assert.equal(sealed.role_digest, SEALED.role_digest);
});

test("a REHASHED invalid role is still refused by the kernel", () => {
  // The forgery a digest check alone would admit: an impossible role whose
  // digest has been honestly recomputed over its own bytes. defineRole runs
  // before any hash is compared, so the refusal is about the CONTRACT.
  const forged = copy(modelRolePreimage(role()));
  forged.authority.authority_class = "system_authority";
  const consistentDigest = digest(copy(forged));
  assert.notEqual(consistentDigest, SEALED.role_digest);
  assert.throws(() => modelRoleFromPreimage(forged),
    error => error.name === "V5RoutingError" &&
      error.code === "role_authority_class_not_occupiable");
});

test("a rehashed role with no quality floor is refused too", () => {
  const forged = copy(modelRolePreimage(role()));
  forged.quality_floor_refs = [];
  assert.throws(() => modelRoleFromPreimage(forged),
    error => error.name === "V5RoutingError" && error.code === "invalid_shape");
});

test("bytes that reseal but are not the canonical serialization refuse", () => {
  // An emitter that returned a reference set in row order rather than sorted
  // would produce exactly this: a valid role whose supplied bytes are not the
  // ones it hashes to. Re-canonicalizing it silently would hide an emitter bug.
  const unsorted = copy(modelRolePreimage(role()));
  unsorted.task_classes = [...unsorted.task_classes].reverse();
  assert.throws(() => modelRoleFromPreimage(unsorted),
    error => error instanceof ModelRoleStoreError &&
      error.code === "model_role_preimage_not_canonical");
});

test("that canonicality check cannot see key ORDER, and does not claim to", () => {
  // canonicalJson sorts object keys, so a key-order difference is normalized
  // away before either digest is taken. Asserting the limit keeps the comment
  // beside it honest: what the check catches is an unsorted reference set and
  // value drift, never key order.
  const preimage = modelRolePreimage(role());
  const reversedKeys = {};
  for (const key of Object.keys(preimage).sort().reverse()) reversedKeys[key] = copy(preimage[key]);
  assert.notDeepEqual(Object.keys(reversedKeys), Object.keys(preimage));
  assert.equal(digest(copy(reversedKeys)), SEALED.role_digest);
  assert.doesNotThrow(() => modelRoleFromPreimage(reversedKeys));
  // An absent or extra field is caught EARLIER and by name, not here.
  const extra = copy(preimage);
  extra.unexpected = "x";
  assert.throws(() => modelRoleFromPreimage(extra), error => error.code === "unknown_field");
  const missing = copy(preimage);
  delete missing.rules;
  assert.throws(() => modelRoleFromPreimage(missing), error => error.code === "missing_field");
});

test("a preimage from another schema version or another tenant refuses", () => {
  const wrongSchema = copy(modelRolePreimage(role()));
  wrongSchema.schema_version = "role-description.v2";
  assert.throws(() => modelRoleFromPreimage(wrongSchema),
    error => error.code === "model_role_schema_version_invalid");

  const wrongTenant = copy(modelRolePreimage(role()));
  wrongTenant.tenant = "someone-else";
  assert.throws(() => modelRoleFromPreimage(wrongTenant),
    error => error.code === "model_role_tenant_mismatch");
});

// ---------------------------------------------------------------------------
// The authority derivation for a current-pointer change.
// ---------------------------------------------------------------------------

test("the current pointer is a retained, non-delegable system-authority act", () => {
  const derived = deriveRoleCurrentPointerAuthority({ actor: JOE, now: NOW });
  assert.equal(derived.partner_slug, V5_SYSTEM_AUTHORITY_PARTNER);
  assert.equal(derived.authority_class, "system_authority");
  assert.equal(derived.grant_kind, "retained_system_authority");
  assert.equal(derived.action, MODEL_ROLE_CURRENT_POINTER_ACTION);
  assert.equal(derived.delegable, false);
  assert.equal(derived.permanent_privilege_granted, false);
  assert.equal(derived.authority_source, "global-boundaries.v5.evaluateActorAuthority");
  // The scope of the claim rides on the result rather than being assumed.
  assert.equal(derived.principal_binding.cross_derivation_binding_recorded, false);
  assert.equal(derived.principal_binding.accepts_actor_from_payload, false);
});

test("the other verified partner is refused: system authority is not shared", () => {
  assert.throws(() => deriveRoleCurrentPointerAuthority({ actor: DELL, now: NOW }),
    error => error instanceof ModelRoleStoreError &&
      error.code === "role_current_pointer_authority_refused" &&
      error.detail.boundary_reason_id === "system_authority_reserved_to_joe" &&
      error.detail.delegable === false);
});

test("a sponsored agent is refused before any authority class is considered", () => {
  assert.throws(() => deriveRoleCurrentPointerAuthority({ actor: SPONSORED_AGENT, now: NOW }),
    error => error.code === "role_current_pointer_authority_refused" &&
      error.detail.boundary_reason_id === "actor_not_verified_partner" &&
      error.detail.derived_authorization_class === "sponsored_agent");
});

test("an actor that merely claims the right slug is not the partner", () => {
  // human:false makes identity.js's own predicate answer something other than
  // verified_partner, and S01 refuses on that rather than on the slug.
  assert.throws(
    () => deriveRoleCurrentPointerAuthority({ actor: { slug: "joe", human: false }, now: NOW }),
    error => error.code === "role_current_pointer_authority_refused");
});

test("the authority evaluation refuses a clock this module was handed loosely", () => {
  assert.throws(() => deriveRoleCurrentPointerAuthority({ actor: JOE, now: "yesterday" }),
    error => error.code === "server_instant_unavailable");
  assert.throws(() => deriveRoleCurrentPointerAuthority({ actor: JOE }),
    error => error.code === "server_instant_unavailable");
});

// ---------------------------------------------------------------------------
// The payload boundary.
// ---------------------------------------------------------------------------

test("a self-asserted actor, approval or verification is refused by name", () => {
  for (const injected of [
    { actor: "joe" }, { actor_slug: "joe" }, { acting_actor: "joe" },
    { approved: true }, { approved_by: "joe" }, { verified: true },
    { verified_human: true }, { human_approved: true }, { authority_granted: true },
    { principal: "carr_authority_joe" }, { session_user: "carr_authority_joe" },
    { set_by_partner_slug: "joe" }, { signed: true }, { system_authority: true },
    // The acting principal is DERIVED in the record layer, so a caller naming
    // one is asserting the very fact this rail exists to establish.
    { acting_actor_id: ACTOR_ID }, { set_by_acting_actor_slug: "joe" },
  ]) {
    assert.throws(() => assertNoSelfAssertedAuthority({ role_key: "reviewer", ...injected }, "args"),
      error => error.code === "self_asserted_authority_refused",
      `expected ${Object.keys(injected)[0]} to be refused`);
  }
});

test("a legitimate role payload is not caught by the injection sweep", () => {
  // `authority`, `authority_class` and `capability_refs` are role CONTENT, and
  // a rule or mission may legitimately contain the words this sweep looks for.
  // The sweep is over KEYS, so free text about occupants and qualification
  // survives it — which is the whole point of a durable job description.
  const legitimate = role({
    mission: "Decide whether a qualified occupant met the role's evidence bar.",
    rules: ["an occupant is replaceable; the job description is not"],
  });
  assert.doesNotThrow(() => assertNoSelfAssertedAuthority({ role: legitimate }, "args"));
  assert.equal(modelRoleRevisionRows(legitimate).role_key, "reviewer");
});

test("an occupancy or qualification claim meets the fail-closed gap, not a generic refusal", () => {
  const db = mockDatabase();
  return (async () => {
    for (const injected of [
      { occupant: { model_key: "claude-opus-5" } },
      { occupant_key: "claude-opus-5@1/high" },
      { qualified: true },
      { qualification_id: "q-1" },
    ]) {
      const payload = await toolError(tools["record-model-role-revision"].handler(
        db, JOE, { idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
          role_digest: SEALED.role_digest, role: role(), ...injected }));
      assert.equal(payload.error, "model_role_occupancy_unbound");
      assert.equal(payload.detail.resolved, false);
      assert.equal(payload.detail.binding_ref, "binding:model-role-occupant-qualification");
    }
    // The strongest assertion here is the smallest: nothing reached the
    // database. A refusal after a query is one that can be mistaken for a write
    // that nearly worked.
    assert.equal(db.calls.length, 0);
  })();
});

test("an occupant assigned in the routing kernel is never persisted by this rail", () => {
  // The kernel's own answer, reused rather than restated: assigning an occupant
  // leaves the role digest untouched and grants the occupant nothing. This rail
  // stores the role and has no relation, argument or verb for the assignment.
  const assignment = assignOccupant(SEALED, {
    model_key: "claude-opus-5", model_version: "2026-05", effort: "high", backend_key: "cloud-anthropic",
  });
  assert.equal(assignment.role_digest, SEALED.role_digest);
  assert.equal(assignment.occupant_grants_no_authority, true);
  assert.equal(Object.keys(tools).some(name => /occupan/i.test(name)), false);
  const prerequisites = modelRoleStorePrerequisites();
  assert.equal(prerequisites.occupancy_binding.resolved, false);
  assert.equal(prerequisites.occupancy_bound, false);
  assert.equal(prerequisites.measured_qualification_bound, false);
  assert.equal(prerequisites.role_confers_authority, false);
});

// ---------------------------------------------------------------------------
// record-model-role-revision.
// ---------------------------------------------------------------------------

test("recording a revision sends the rows, then revalidates the readback", async () => {
  const db = mockDatabase({
    "model_role_record_revision": [{ id: REVISION_ID }],
    "model_role_revision_readback": [{ revision: entry() }],
  });
  const result = await tools["record-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, role: role(),
  });
  assert.equal(result.ok, true);
  assert.equal(result.revision_id, REVISION_ID);
  assert.equal(result.role_digest, SEALED.role_digest);
  assert.equal(result.is_current, false);
  assert.equal(result.confers_authority, false);
  assert.equal(result.occupant_bound, false);
  assert.equal(result.measured_qualification_bound, false);
  assert.equal(result.revision.revalidated_through_define_role, true);
  assert.equal(result.effects.grants_authority, false);
  assert.equal(result.effects.binds_occupant, false);
  assert.equal(result.effects.clock_started, false);

  const [write, readbackCall] = db.calls;
  assert.ok(write.sql.includes("ops.model_role_record_revision"));
  // The parameters that bind the write: role, version, key, and the digest this
  // module computed rather than the one the caller sent.
  assert.equal(write.params[0], "reviewer");
  assert.equal(write.params[1], 1);
  assert.equal(write.params[2], KEY);
  assert.equal(write.params[3], SEALED.role_digest);
  const texts = JSON.parse(write.params[5]);
  const refs = JSON.parse(write.params[6]);
  assert.deepEqual([...new Set(texts.map(row => row.field))].sort(), [...MODEL_ROLE_TEXT_FIELDS]);
  assert.deepEqual([...new Set(refs.map(row => row.field))].sort(), [...MODEL_ROLE_REF_FIELDS]);
  // No actor, tenant, instant or authority crosses the boundary.
  assert.equal(write.params.some(p => typeof p === "string" && p.includes("joe")), false);
  assert.ok(readbackCall.sql.includes("ops.model_role_revision_readback"));
  assert.equal(db.events.length, 1);
  // THE FOUR VALUES THE OBJECT CALL SHAPE LOST, each NOT NULL in event.
  const [recorded] = db.events;
  assert.equal(recorded.actor, JOE, "the actor must be forwarded, not replaced by an options object");
  assert.equal(recorded.verb, "record-model-role-revision");
  assert.equal(recorded.subjectType, "model_role");
  assert.equal(recorded.subjectId, REVISION_ID);
  assert.equal(recorded.fields.idempotency_key, KEY);
  assert.equal(recorded.fields.new.role_key, "reviewer");
  assert.equal(recorded.fields.new.revision_no, 1);
  // The digest audited is the one this module computed, never the caller's.
  assert.equal(recorded.fields.new.role_digest, SEALED.role_digest);
});

test("a caller's digest is compared, never used, and a mismatch never reaches the database", async () => {
  const db = mockDatabase();
  const payload = await toolError(tools["record-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: `sha256:${"b".repeat(64)}`, role: role(),
  }));
  assert.equal(payload.error, "model_role_digest_mismatch");
  assert.equal(payload.expected, SEALED.role_digest);
  assert.equal(db.calls.length, 0);
});

test("a role filed under the wrong role key never reaches the database", async () => {
  const db = mockDatabase();
  const payload = await toolError(tools["record-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "builder", revision_no: 1,
    role_digest: SEALED.role_digest, role: role(),
  }));
  assert.equal(payload.error, "model_role_key_mismatch");
  assert.equal(db.calls.length, 0);
});

test("a whitespace-only role field never reaches the database either", async () => {
  // The module refuses before the writer is called, so the SQL non-empty gate is
  // a second line rather than the only one. Both are needed: this one is the
  // only one on the module's path, and the SQL one is the only one on a direct
  // writer-bundle call.
  const db = mockDatabase();
  const payload = await toolError(tools["record-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, role: role({ skills: [" ", "second"] }),
  }));
  assert.equal(payload.error, "invalid_shape");
  assert.equal(db.calls.length, 0);
});

test("a malformed idempotency key or version is refused before any statement", async () => {
  const db = mockDatabase();
  assert.equal((await toolError(tools["record-model-role-revision"].handler(db, JOE, {
    idempotency_key: "not-a-uuid", role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, role: role(),
  }))).error, "invalid_uuid");
  assert.equal((await toolError(tools["record-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 0,
    role_digest: SEALED.role_digest, role: role(),
  }))).error, "invalid_revision_no");
  assert.equal(db.calls.length, 0);
});

test("an idempotency mismatch raised by the record layer is surfaced, not swallowed", async () => {
  // The database owns the replay comparison — content, role, version and the
  // derived writer — because only it holds the earlier row. What is asserted
  // here is that the module does not turn that refusal into a success.
  const db = {
    calls: [], events: [],
    query: async (sql) => {
      if (sql.includes("model_role_record_revision")) {
        throw new Error("model role idempotency key was already used for a different revision, role, version or writer");
      }
      throw new Error(`unexpected: ${sql}`);
    },
  };
  await assert.rejects(() => tools["record-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, role: role(),
  }), /idempotency key/);
});

test("a readback that does not revalidate refuses after the write", async () => {
  const drifted = entry(role(), { recomputed_role_digest: `sha256:${"c".repeat(64)}` });
  const db = mockDatabase({
    "model_role_record_revision": [{ id: REVISION_ID }],
    "model_role_revision_readback": [{ revision: drifted }],
  });
  const payload = await toolError(tools["record-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, role: role(),
  }));
  assert.equal(payload.error, "model_role_stored_digest_drift");
});

test("a readback carrying another reader's shape refuses", async () => {
  const db = mockDatabase({
    "model_role_record_revision": [{ id: REVISION_ID }],
    "model_role_revision_readback": [{ revision: { ...entry(), schema_version: "something-else.v1" } }],
  });
  const payload = await toolError(tools["record-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, role: role(),
  }));
  assert.equal(payload.error, "model_role_revision_entry_schema_invalid");
});

// ---------------------------------------------------------------------------
// read-model-role.
// ---------------------------------------------------------------------------

test("a read rebuilds and revalidates every revision it returns", async () => {
  const second = role({ mission: "Establish independently that delivered work meets its contract, and say what it does not." });
  const history = [
    entry(role()),
    { ...entry(second), revision_id: REVISION_ID_2, revision_no: 2 },
  ];
  const db = mockDatabase({
    "model_role_readback": [{ readback: readback({
      history,
      current: currentEntry(second, { revision_id: REVISION_ID_2, revision_no: 2, pointer_no: 2, expected_prior_revision_no: 1 }),
      pointer_history: [
        { pointer_no: 1, revision_no: 1, expected_prior_revision_no: null,
          set_by_partner_slug: "joe", set_by_acting_actor_slug: "joe" },
        { pointer_no: 2, revision_no: 2, expected_prior_revision_no: 1,
          set_by_partner_slug: "joe", set_by_acting_actor_slug: "joe" },
      ],
    }) }],
  });
  const result = await tools["read-model-role"].handler(db, JOE, { role_key: "reviewer" });
  assert.equal(result.ok, true);
  assert.equal(result.history.length, 2);
  // HISTORY IS PRESERVED AND EACH ENTRY IS RE-DERIVED HERE. Both revisions are
  // rebuilt through defineRole, and the superseded one is still readable.
  assert.equal(result.history[0].revalidated_through_define_role, true);
  assert.equal(result.history[1].revalidated_through_define_role, true);
  assert.notEqual(result.history[0].role_digest, result.history[1].role_digest);
  assert.equal(result.history[0].role.role_digest, result.history[0].role_digest);
  assert.equal(result.current.revision_no, 2);
  assert.equal(result.pointer_history.length, 2);
  assert.equal(result.pointer_history[1].expected_prior_revision_no, 1);
  // The authenticity claim is scoped on the result rather than assumed.
  assert.equal(result.current.authenticity.claim,
    "re_derived_from_committed_rows_and_resealed_here_through_defineRole");
  assert.ok(result.current.authenticity.does_not_prove.some(line => line.includes("signed")));
  assert.ok(result.current.authenticity.does_not_prove.some(line => line.includes("qualified")));
});

test("the current readback names the POINTER's authority, never the role's", async () => {
  // On a rail whose central rule is that no ROLE may declare system_authority, a
  // bare top-level `authority_class: "system_authority"` on this readback is a
  // misread waiting to happen. The pointer's own class is pointer-prefixed; the
  // role's declared class stays inside the preimage and is occupiable.
  const db = mockDatabase({
    "model_role_readback": [{ readback: readback({ current: currentEntry() }) }],
  });
  const result = await tools["read-model-role"].handler(db, JOE, { role_key: "reviewer" });
  assert.equal(result.current.pointer_authority_class, "system_authority");
  assert.equal(result.current.pointer_authority_grant_kind, "retained_system_authority");
  assert.equal("authority_class" in result.current, false);
  assert.equal("authority_grant_kind" in result.current, false);
  assert.equal(result.current.preimage.authority.authority_class, "developer");
  assert.equal(result.current.role.authority.authority_class, "developer");
});

test("a role nobody has recorded reads back as absent rather than as a default", async () => {
  const db = mockDatabase({
    "model_role_readback": [{ readback: readback({ current: null, history: [] }) }],
  });
  const result = await tools["read-model-role"].handler(db, JOE, { role_key: "reviewer" });
  assert.equal(result.current, null);
  assert.deepEqual(result.history, []);
});

test("a read refuses a tampered persisted body instead of reporting it", async () => {
  const tampered = entry();
  tampered.preimage = copy(tampered.preimage);
  tampered.preimage.mission = "Approve whatever the builder produced.";
  const db = mockDatabase({ "model_role_readback": [{ readback: readback({ history: [tampered] }) }] });
  const payload = await toolError(tools["read-model-role"].handler(db, JOE, { role_key: "reviewer" }));
  assert.equal(payload.error, "model_role_readback_digest_mismatch");
});

test("one unrevalidatable revision fails the WHOLE read, and names what to look at", async () => {
  // THE POISONED-ROW CASE, which is what the SQL non-empty gate exists to make
  // unreachable. If a revision ever does get stored with text defineRole
  // refuses, the read must not skip it, must not repair it and must not return
  // the rest of the history as though it were complete — a history with a hole
  // silently removed is not the history. What the refusal owes the reader is
  // enough to act on WITHOUT a rewrite.
  const poisoned = entry();
  poisoned.revision_no = 3;
  poisoned.preimage = copy(poisoned.preimage);
  poisoned.preimage.skills = ["\t", ...poisoned.preimage.skills.slice(1)];
  poisoned.role_digest = digest(copy(poisoned.preimage));
  poisoned.recomputed_role_digest = poisoned.role_digest;
  const db = mockDatabase({
    "model_role_readback": [{ readback: readback({ history: [entry(), poisoned] }) }],
  });
  const payload = await toolError(tools["read-model-role"].handler(db, JOE, { role_key: "reviewer" }));
  assert.equal(payload.error, "model_role_readback_revision_unrevalidatable");
  assert.equal(payload.role_key, "reviewer");
  assert.equal(payload.revision_no, 3);
  assert.equal(payload.role_schema_version, V5_ROLE_DESCRIPTION_SCHEMA_VERSION);
  // The kernel's own finding is carried underneath rather than discarded.
  assert.equal(payload.contract_refusal.code, "invalid_shape");
  assert.ok(payload.contract_refusal.detail.path.includes("skills"));
  // And the remedy named on the refusal is a reader, not a rewrite.
  assert.equal(payload.read_compatibility.skips_unrevalidatable_revisions, false);
  assert.equal(payload.read_compatibility.repairs_or_rewrites_stored_revisions, false);
  assert.equal(payload.read_compatibility.returns_partial_history_on_corruption, false);
});

test("a revision stored under a role contract this reader cannot revalidate refuses by name", async () => {
  // FUTURE-SCHEMA DRIFT, scoped as a versioned-reader problem. The candidate SQL
  // pins the stored column to role-description.v1 with a CHECK, so this cannot
  // fire today — which is exactly why it is asserted rather than assumed, and
  // why the remedy on the refusal is "write a versioned reader" rather than
  // "edit the rows", which append-only history forbids.
  const future = entry(role(), { role_schema_version: "role-description.v2" });
  const db = mockDatabase({
    "model_role_readback": [{ readback: readback({ history: [future] }) }],
  });
  const payload = await toolError(tools["read-model-role"].handler(db, JOE, { role_key: "reviewer" }));
  assert.equal(payload.error, "model_role_revision_schema_version_unsupported");
  assert.equal(payload.expected, V5_ROLE_DESCRIPTION_SCHEMA_VERSION);
  assert.equal(payload.stored, "role-description.v2");
  assert.equal(payload.role_key, "reviewer");
  assert.ok(payload.read_compatibility.drift_remedy.includes("VERSIONED READER"));
  assert.ok(payload.read_compatibility.drift_remedy_is_not
    .some(line => line.includes("retroactive mutation")));
});

test("an entry that omits the stored role contract version refuses rather than assuming it", async () => {
  const noVersion = entry();
  delete noVersion.role_schema_version;
  const db = mockDatabase({
    "model_role_readback": [{ readback: readback({ history: [noVersion] }) }],
  });
  const payload = await toolError(tools["read-model-role"].handler(db, JOE, { role_key: "reviewer" }));
  assert.equal(payload.error, "model_role_revision_schema_version_unsupported");
  assert.equal(payload.stored, null);
});

test("a read refuses an unsettled role key before any statement", async () => {
  const db = mockDatabase();
  const payload = await toolError(tools["read-model-role"].handler(db, JOE, { role_key: "chief_of_staff" }));
  assert.equal(payload.error, "unknown_role_key");
  assert.deepEqual(payload.detail.registered, [...V5_ROLE_KEYS]);
  assert.equal(db.calls.length, 0);
});

// ---------------------------------------------------------------------------
// set-current-model-role-revision.
// ---------------------------------------------------------------------------

test("the current-pointer verb declares the LIVE gate and not the retired label", () => {
  // `authorityOnly` is read by mcp.js's callTool and routes this verb onto the
  // per-partner authority DSN. `humanOnly` was retired by Joe's 2026-08-26
  // ruling; callTool no longer reads it and mutation-registry.js compares
  // `human_only: tool?.humanOnly === true` against the registered contract, so a
  // fresh declaration would misreport this verb's protection the moment it is
  // registered. Its absence is the accurate statement, not an omission.
  assert.equal(tools["set-current-model-role-revision"].authorityOnly, true);
  assert.equal(tools["set-current-model-role-revision"].humanOnly, undefined);
  assert.equal(tools["record-model-role-revision"].humanOnly, undefined);
  assert.equal(tools["record-model-role-revision"].authorityOnly, undefined);
  // The description must not advertise a protection the label no longer carries.
  assert.equal(/HUMAN-ONLY/i.test(tools["set-current-model-role-revision"].description), false);

  // And the human requirement is carried by the two places that enforce it.
  const p = modelRoleStorePrerequisites();
  assert.equal(p.current_pointer_authority_only, true);
  assert.equal(p.current_pointer_human_only_label_declared, false);
  assert.equal(p.current_pointer_human_requirement_enforced_by.length, 2);
  assert.ok(p.current_pointer_human_requirement_enforced_by[0].includes("actor.human"));
  assert.ok(p.current_pointer_human_requirement_enforced_by[1]
    .includes("portfolio_writer_actor_id"));
});

test("a creation states its expectation as an explicit null and its shape as a parameter", async () => {
  const db = pointerDatabase();
  const result = await tools["set-current-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, expected_current_revision_no: null,
  });
  assert.equal(result.ok, true);
  assert.equal(result.pointer_id, POINTER_ID);
  assert.equal(result.history_preserved, true);
  assert.equal(result.confers_authority, false);
  assert.equal(result.effects.current_role_description_changed, true);
  assert.equal(result.effects.grants_authority, false);
  assert.equal(result.effects.binds_occupant, false);
  assert.equal(result.effects.dispatches_model, false);
  assert.equal(result.authority.grant_kind, "retained_system_authority");
  assert.equal(result.principal_binding.cross_derivation_binding_recorded, false);

  const write = db.calls.find(call => call.sql.includes("model_role_set_current_revision"));
  // p_expect_creation is its OWN parameter, so the database never has to infer
  // "create" from a null somebody may simply have omitted.
  assert.equal(write.params[4], true);
  assert.equal(write.params[5], null);
});

test("the current-pointer move writes its audit event with a real actor, verb and subject", async () => {
  // The second half of the class fix: this call site carried the same options
  // object as record-model-role-revision, and a pointer move that rolled back
  // on the audit insert would leave the role with no current revision while
  // reporting one. Driven through the same recording double.
  const db = pointerDatabase();
  await tools["set-current-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, expected_current_revision_no: null,
  });

  assert.equal(db.events.length, 1);
  const [moved] = db.events;
  assert.equal(moved.actor, JOE, "the actor must be forwarded, not replaced by an options object");
  assert.equal(moved.verb, "set-current-model-role-revision");
  assert.equal(moved.subjectType, "model_role");
  assert.equal(moved.subjectId, POINTER_ID);
  assert.equal(moved.fields.idempotency_key, KEY);
  assert.equal(moved.fields.new.role_key, "reviewer");
  assert.equal(moved.fields.new.revision_no, 1);
  assert.equal(moved.fields.new.role_digest, SEALED.role_digest);
  assert.equal(moved.fields.new.expected_current_revision_no, null);
});

test("the two record-layer principals are reported as two facts, not one", async () => {
  // The authority LOGIN scope is shared with the partner's sponsored agents by
  // design; who ACTED is not. A result that reported only the first would be the
  // flattening this rail exists to stop recording, so both are named and neither
  // stands in for the other.
  const db = pointerDatabase();
  const result = await tools["set-current-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, expected_current_revision_no: null,
  });
  assert.equal(result.record_layer_authority_login_partner, V5_SYSTEM_AUTHORITY_PARTNER);
  assert.equal(result.record_layer_acting_principal, "joe");
  // The old single field is gone, so nothing can read a login scope as an
  // attribution by accident.
  assert.equal("record_layer_principal" in result, false);

  const binding = result.principal_binding;
  assert.equal(binding.authority_login_is_shared_with_sponsored_agents, true);
  assert.equal(binding.acting_principal_recorded_separately, true);
  assert.equal(binding.acting_principal_required_to_be_partner_human_actor, true);
  // The residual gap is still stated, and is stated as what it is.
  assert.ok(binding.unbound_seam.includes("TRUSTED RUNTIME CONTEXT"));
  assert.ok(binding.unbound_seam.includes("carr.acting_actor_slug"));
  assert.equal(binding.cross_derivation_binding_recorded, false);
});

test("a recorded acting principal that is not the actor this verb evaluated refuses", async () => {
  // The record layer refuses a sponsored agent on the shared partner login on
  // its own; this is the second, independent statement — and it is what makes
  // the recorded principal COMPARABLE to the actor this process evaluated
  // instead of two facts nobody ever put side by side.
  const db = pointerDatabase(currentEntry(role(), { set_by_acting_actor_slug: "codex" }));
  const payload = await toolError(tools["set-current-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, expected_current_revision_no: null,
  }));
  assert.equal(payload.error, "model_role_acting_principal_drift");
  assert.equal(payload.evaluated_actor_slug, "joe");
  assert.equal(payload.recorded_acting_actor_slug, "codex");
  assert.equal(payload.recorded_authority_login_partner, "joe");
});

test("a recorded authority login scope that is not the allowed partner refuses", async () => {
  const db = pointerDatabase(currentEntry(role(), { set_by_partner_slug: "dell" }));
  const payload = await toolError(tools["set-current-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, expected_current_revision_no: null,
  }));
  assert.equal(payload.error, "model_role_authority_login_drift");
  assert.equal(payload.evaluated_partner_slug, "joe");
  assert.equal(payload.recorded_authority_login_partner, "dell");
});

test("a pointer readback that records no acting principal at all refuses", async () => {
  // A record layer that predates the acting-principal binding would return this
  // shape, and it is precisely the one that must not be read as "Joe did it".
  const stale = currentEntry();
  delete stale.set_by_acting_actor_slug;
  const db = pointerDatabase(stale);
  const payload = await toolError(tools["set-current-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, expected_current_revision_no: null,
  }));
  assert.equal(payload.error, "model_role_acting_principal_drift");
  assert.equal(payload.recorded_acting_actor_slug, null);
});

test("a replacement carries the exact expected version through to the database", async () => {
  const db = pointerDatabase(
    currentEntry(role(), { revision_no: 1, pointer_no: 2, expected_prior_revision_no: 3 }));
  await tools["set-current-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, expected_current_revision_no: 3,
  });
  const write = db.calls.find(call => call.sql.includes("model_role_set_current_revision"));
  assert.equal(write.params[0], "reviewer");
  assert.equal(write.params[1], KEY);
  assert.equal(write.params[2], 1);
  assert.equal(write.params[3], SEALED.role_digest);
  assert.equal(write.params[4], false);
  assert.equal(write.params[5], 3);
  // SIX PARAMETERS AND NO SEVENTH. There is no argument through which an actor,
  // an approval or an authority could arrive; both principals are derived inside
  // the record layer.
  assert.equal(write.params.length, 6);
  assert.equal(write.params.some(p => typeof p === "string" && p.includes("joe")), false);
});

test("omitting the expected version refuses; it is never inferred", async () => {
  const db = mockDatabase({ "model_role_server_instant": [{ now: NOW }] });
  const payload = await toolError(tools["set-current-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 2, role_digest: SEALED.role_digest,
  }));
  assert.equal(payload.error, "model_role_expected_revision_required");
  assert.equal(db.calls.length, 0);
});

test("a stale compare-and-swap refused by the record layer is surfaced", async () => {
  const db = {
    calls: [], events: [],
    query: async (sql) => {
      if (sql.includes("model_role_server_instant")) return { rows: [{ now: NOW }] };
      if (sql.includes("model_role_set_current_revision")) {
        throw new Error("model role reviewer current revision is 4, not the expected 3");
      }
      throw new Error(`unexpected: ${sql}`);
    },
  };
  await assert.rejects(() => tools["set-current-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 5,
    role_digest: SEALED.role_digest, expected_current_revision_no: 3,
  }), /current revision is 4, not the expected 3/);
});

test("a creation colliding with an existing pointer is surfaced", async () => {
  const db = {
    calls: [], events: [],
    query: async (sql) => {
      if (sql.includes("model_role_server_instant")) return { rows: [{ now: NOW }] };
      if (sql.includes("model_role_set_current_revision")) {
        throw new Error("model role reviewer already has current revision 1; a creation compare-and-swap cannot move it");
      }
      throw new Error(`unexpected: ${sql}`);
    },
  };
  await assert.rejects(() => tools["set-current-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 2,
    role_digest: SEALED.role_digest, expected_current_revision_no: null,
  }), /creation compare-and-swap cannot move it/);
});

test("the record layer's acting-principal refusal is surfaced, not swallowed", async () => {
  // The shape of a sponsored agent acting on the partner's shared authority
  // login. In production S01 refuses it here first; this asserts that if it ever
  // reached the writer, the writer's refusal reaches the caller intact.
  const db = {
    calls: [], events: [],
    query: async (sql) => {
      if (sql.includes("model_role_server_instant")) return { rows: [{ now: NOW }] };
      if (sql.includes("model_role_set_current_revision")) {
        throw new Error("naming the current revision of a durable role is non-delegable: the joe authority login is shared with that partner's sponsored agents, and this transaction's acting actor is codex, not the partner's own active human actor");
      }
      throw new Error(`unexpected: ${sql}`);
    },
  };
  await assert.rejects(() => tools["set-current-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, expected_current_revision_no: null,
  }), /non-delegable.*acting actor is codex/);
});

test("a non-system-authority partner reaches no writer", async () => {
  for (const actor of [DELL, SPONSORED_AGENT]) {
    const db = mockDatabase({ "model_role_server_instant": [{ now: NOW }] });
    const payload = await toolError(tools["set-current-model-role-revision"].handler(db, actor, {
      idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
      role_digest: SEALED.role_digest, expected_current_revision_no: null,
    }));
    assert.equal(payload.error, "role_current_pointer_authority_refused");
    // One read-only statement precedes the derivation, because the instant is
    // the record layer's rather than this module's. No writer is reached.
    assert.equal(db.calls.length, 1);
    assert.ok(db.calls[0].sql.includes("model_role_server_instant"));
    assert.equal(db.events.length, 0);
  }
});

test("the instant comes from the record layer, and an unusable one refuses", async () => {
  const db = mockDatabase({ "model_role_server_instant": [{ now: "whenever" }] });
  const payload = await toolError(tools["set-current-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, expected_current_revision_no: null,
  }));
  assert.equal(payload.error, "server_instant_unavailable");
});

test("a pointer readback that does not name the requested revision refuses", async () => {
  const db = pointerDatabase(currentEntry(role(), { revision_no: 9 }));
  const payload = await toolError(tools["set-current-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, expected_current_revision_no: null,
  }));
  assert.equal(payload.error, "model_role_current_pointer_drift");
});

// ---------------------------------------------------------------------------
// The projection: what is claimed, and what is named as missing.
// ---------------------------------------------------------------------------

test("the projection names the gaps it does not fill and claims nothing it cannot", () => {
  const p = modelRoleStorePrerequisites();
  assert.equal(p.second_role_contract_declared, false);
  assert.equal(p.second_quality_floor_declared, false);
  assert.equal(p.numeric_policy_default_declared, false);
  assert.equal(p.signed_capability_token_issued, false);
  assert.equal(p.provenance_survives_serialization, false);
  assert.equal(p.candidate_sql_applied_as_migration, true);
  assert.equal(p.candidate_install.numbered_migration, "migrations/0542_model_role_store.sql");
  assert.equal(p.tools_registered, true);
  assert.equal(p.current_pointer_authority_class, "system_authority");
  assert.equal(p.current_pointer_delegable, false);
  assert.deepEqual(p.role_keys, [...V5_ROLE_KEYS]);
  assert.equal(p.occupiable_authority_classes.includes("system_authority"), false);
  // The routing kernel's own gap list is carried rather than restated, so the
  // qualification producer, the dispatch path and the backend health source
  // stay named in exactly one place — and stay named.
  const kernelGaps = p.routing_kernel_unimplemented_dependencies.join(" ");
  assert.ok(kernelGaps.includes("route-qualification.v1 producer"));
  assert.ok(kernelGaps.includes("model dispatch"));
  assert.ok(kernelGaps.includes("live backend health source"));
  assert.equal(p.effects.database_writes, 0);
  // Source promotion does not imply the current database has applied it.
  assert.ok(p.integration_still_open.some(line =>
    line.includes("checked at runtime")));
});

test("the principal binding names both record-layer derivations and the seam that remains", () => {
  const scope = modelRoleStorePrerequisites().principal_binding;
  assert.ok(scope.in_process_derivation.includes("evaluateActorAuthority"));
  assert.ok(scope.record_layer_authority_login_derivation.includes("ops.authority_actor_slug()"));
  assert.ok(scope.record_layer_authority_login_derivation.includes("sponsored agents"));
  assert.ok(scope.record_layer_acting_principal_derivation.includes("ops.portfolio_writer_actor_id()"));
  assert.ok(scope.record_layer_acting_principal_derivation.includes("carr.verified_human_actor_slug"));
  assert.equal(scope.both_are_server_derived, true);
  assert.equal(scope.accepts_actor_from_payload, false);
  assert.equal(scope.cross_derivation_binding_recorded, false);
  assert.ok(scope.unbound_seam.includes("session_user") ||
            scope.unbound_seam.includes("carr_authority_joe"));
  // The gap is stated as a limit on what is proved, not as a missing feature —
  // and what the binding DID close is stated beside it rather than implied.
  assert.ok(scope.what_it_now_refuses_that_it_previously_recorded.includes("sponsored agent"));
});

test("the read-compatibility scope refuses every form of quiet repair", () => {
  const scope = modelRoleStorePrerequisites().read_compatibility;
  assert.equal(scope, MODEL_ROLE_READ_COMPATIBILITY_SCOPE);
  assert.equal(scope.revalidates_role_schema_version, V5_ROLE_DESCRIPTION_SCHEMA_VERSION);
  assert.equal(scope.revalidates_every_revision_in_history, true);
  assert.equal(scope.fails_closed_on_readback_corruption, true);
  assert.equal(scope.skips_unrevalidatable_revisions, false);
  assert.equal(scope.repairs_or_rewrites_stored_revisions, false);
  assert.equal(scope.returns_partial_history_on_corruption, false);
});

test("this rail exposes role descriptions without activating a route", () => {
  assert.deepEqual(Object.keys(tools).sort(),
    ["read-model-role", "record-model-role-revision", "set-current-model-role-revision"]);
  for (const name of Object.keys(tools)) {
    assert.equal(/register|activate|deploy|dispatch|retire|delete|repair/i.test(name), false);
  }
});

// --- the candidate SQL, read as TEXT ----------------------------------------
//
// THESE ARE STATIC SOURCE ASSERTIONS AND NOTHING MORE. They read
// ops/model-role-store.candidate.sql off disk and assert what it does and does
// not CONTAIN. No SQL is parsed, planned or executed by them, and a passing case
// here is not evidence that the file installs, that it installs correctly, or
// that any database has ever seen it — mcp-server/test/model-role-store-postgres.sql
// is where the installed schema is exercised, and it has not been run either.
//
// WHAT THEY ARE WORTH. The property they guard is one an executed test cannot
// reach at all: that this candidate holds NO MIGRATION PROGRAM. A forward-fix
// block — `alter table ... add constraint`, `add column`, `drop constraint` —
// applied from candidate source to a database whose rows it has never seen is a
// decision about persisted rows taken by a file that is not a migration and has
// no ledger entry. It is easy to add back, it looks helpful, and nothing else in
// this suite would notice. So it is pinned here.

/**
 * The candidate with its `--` comment tails removed, lowercased.
 *
 * HONEST ABOUT THE STRIPPING: it also truncates any line whose string literal
 * contains `--` (several refusal messages do, as an em-dash substitute). That can
 * only REMOVE text, never invent a statement — so a false PASS below would need a
 * real `alter table` sitting after a `--` on its own line, and a false FAIL is
 * impossible. It is a text check, and it is described as one.
 */
const CANDIDATE_SQL = readFileSync(
  new URL("../../ops/model-role-store.candidate.sql", import.meta.url), "utf8");
const CANDIDATE_CODE = CANDIDATE_SQL
  .split("\n")
  .map(line => {
    const at = line.indexOf("--");
    return at === -1 ? line : line.slice(0, at);
  })
  .join("\n")
  .toLowerCase();

test("the candidate SQL carries NO migration program: no ALTER, no ADD COLUMN, no backfill", () => {
  for (const forbidden of [
    "alter table", "add constraint", "drop constraint", "add column",
    "alter column", "drop column", "drop table", "rename to",
  ]) {
    assert.equal(CANDIDATE_CODE.includes(forbidden), false,
      `ops/model-role-store.candidate.sql contains "${forbidden}". This file installs fresh and refuses an existing installation; changing one is a numbered migration with a ledger entry and a review, and it is not this file's act.`);
  }
  // No stored row is rewritten or removed either. (`insert into ops.model_role…`
  // is NOT forbidden: it is what the revision writer does with the rows a caller
  // supplied. What an append-only rail must never contain is the other two.)
  for (const forbidden of ["update ops.model_role", "delete from ops.model_role"]) {
    assert.equal(CANDIDATE_CODE.includes(forbidden), false,
      `ops/model-role-store.candidate.sql contains "${forbidden}"; this history is append-only and has no repair path.`);
  }
  // `execute format(...)` is how a forward fix would be smuggled past the checks
  // above, since the statement it builds is a string. The candidate legitimately
  // uses dynamic DDL for its triggers, so the assertion is on WHICH statements it
  // may build: trigger statements, and nothing else.
  const dynamic = [...CANDIDATE_CODE.matchAll(/execute\s+format\(\s*'([a-z_ ]*)/g)];
  assert.ok(dynamic.length >= 6, "the trigger installers are gone from the candidate");
  for (const [, verb] of dynamic) {
    assert.ok(verb.startsWith("create trigger ") || verb.startsWith("drop trigger "),
      `the candidate builds a dynamic statement that is not a trigger statement: "${verb}"`);
  }
});

test("the candidate refuses an existing installation BEFORE it creates anything", () => {
  const refusal = CANDIDATE_CODE.indexOf("$fresh_install_only$");
  assert.notEqual(refusal, -1, "the fresh-install refusal block is gone");
  // Every DDL verb in the file must come after it. `create or replace function`
  // in section 1 counts: replacing somebody else's function is exactly as much a
  // change to a database this file did not build as creating a table is.
  for (const ddl of ["create or replace function", "create table", "create trigger"]) {
    const first = CANDIDATE_CODE.indexOf(ddl);
    assert.notEqual(first, -1, `the candidate no longer contains "${ddl}"`);
    assert.ok(first > refusal,
      `"${ddl}" appears before the fresh-install refusal, so a database that already carries this store would be altered before it is refused`);
  }
  // The refusal covers the relations AND the functions, and it is a presence
  // check that says so rather than a compatibility claim it cannot support.
  const block = CANDIDATE_CODE.slice(refusal, CANDIDATE_CODE.indexOf("$fresh_install_only$", refusal + 1));
  for (const relation of ["ops.model_role_revision", "ops.model_role_revision_text",
    "ops.model_role_revision_ref", "ops.model_role_current_pointer"]) {
    assert.ok(block.includes(relation), `the refusal does not look for ${relation}`);
  }
  assert.ok(block.includes("pg_proc"), "the refusal looks at relations but not at functions");
  // And `create table if not exists` would quietly adopt one anyway.
  assert.equal(CANDIDATE_CODE.includes("create table if not exists"), false,
    "`create table if not exists` would adopt a relation this file did not create, keeping its columns, its constraints and its rows");
});

test("every column and constraint the writers depend on is in the CREATE TABLE shape", () => {
  // The pointer's acting principal is the one this rail added last and the one a
  // forward fix would have been reached for. It has to be in the fresh shape, NOT
  // NULL, and inside the CREATE TABLE rather than anywhere after it.
  const pointer = CANDIDATE_CODE.slice(
    CANDIDATE_CODE.indexOf("create table ops.model_role_current_pointer"));
  const body = pointer.slice(0, pointer.indexOf("\n);"));
  assert.ok(/acting_actor_id\s+uuid not null/.test(body),
    "acting_actor_id is not a NOT NULL column of the fresh current-pointer relation");
  for (const named of [
    "model_role_pointer_acting_actor_fkey", "model_role_pointer_creation_shape",
    "model_role_pointer_authority_class", "model_role_pointer_ledger_unique",
  ]) {
    assert.ok(body.includes(named), `${named} is not declared in the CREATE TABLE shape`);
  }
  // The three ECMAScript non-empty gates, in the fresh shape of their own
  // relations, and never as btrim().
  const revision = CANDIDATE_CODE.slice(
    CANDIDATE_CODE.indexOf("create table ops.model_role_revision ("));
  const revisionBody = revision.slice(0, revision.indexOf("\n);"));
  for (const named of ["model_role_revision_title_nonempty", "model_role_revision_mission_nonempty"]) {
    assert.ok(revisionBody.includes(named), `${named} is not declared in the CREATE TABLE shape`);
  }
  assert.equal(/check\s*\(\s*btrim\(/.test(CANDIDATE_CODE), false,
    "a text gate is written with btrim(), which strips space only — the divergence that lets a lone tab be stored and then refuse that role's reads forever");
  // And the shape assertion that runs after the relations are created issues no
  // DDL: it is the whole reason a forward fix is not needed to state the
  // dependency. (The `alter table` sweep above already proves it repairs nothing.)
  assert.ok(CANDIDATE_CODE.includes("$installed_shape$"),
    "the installed-shape assertion is gone, so nothing checks that the CREATE TABLE shape and the writers agree");
});

test("the module's install scope says what the candidate file actually does", () => {
  const scope = modelRoleStorePrerequisites().candidate_install;
  assert.equal(scope, MODEL_ROLE_CANDIDATE_INSTALL_SCOPE);
  assert.equal(scope.applied_as_migration, true);
  assert.equal(scope.installs_fresh_only, true);
  assert.equal(scope.refuses_preexisting_installation_before_any_ddl, true);
  assert.equal(scope.migrates_or_repairs_a_preexisting_installation, false);
  assert.equal(scope.contains_alter_table_add_column_or_drop_constraint, false);
  assert.equal(scope.backfills_or_rewrites_stored_rows, false);
  // THE CLAIM IS NARROWED WHERE IT HAS TO BE. The refusal fires on presence; it
  // does not read a pre-existing relation and pronounce it incompatible, and the
  // projection must not say otherwise.
  assert.equal(scope.refusal_is_on_presence_not_on_verified_incompatibility, true);
  assert.equal(scope.verifies_that_a_preexisting_relation_is_compatible, false);
  // The three claims above are claims ABOUT A FILE, so they are checked against
  // that file rather than left as prose agreeing with itself.
  assert.equal(CANDIDATE_CODE.includes("alter table"), false);
  assert.ok(CANDIDATE_CODE.includes("$fresh_install_only$"));
  assert.ok(scope.asserts_its_own_installed_shape.includes("2b"));
  assert.ok(scope.forward_migration_is.includes("0542_model_role_store.sql"));
});
