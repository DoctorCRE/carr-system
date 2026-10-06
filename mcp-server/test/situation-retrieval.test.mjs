import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  normalizeSituationPhrase,
  rankSituationCandidates,
  situationRetrievalTools,
  searchDoctrineSituations,
} from "../src/situation-retrieval.js";


test("normalization is shared, deterministic, and deliberately small", () => {
  assert.equal(normalizeSituationPhrase("  Record\n layer\tOUTAGE  "), "record layer outage");
  assert.equal(normalizeSituationPhrase("Landlord—went quiet"), "landlord—went quiet");
  assert.equal(normalizeSituationPhrase("   "), "");
});


test("lexical-dominant-v1 preserves lexical hits and lets a concept-only miss win", () => {
  const ranked = rankSituationCandidates([
    { section_key: "lexical", lexical_score: 0.2, concept_matches: [] },
    { section_key: "concept", lexical_score: 0, concept_matches: [
      { phrase_id: "p1", concept_id: "c1", mapping_id: "m1", phrase_strength: 1,
        phrase_weight: 1, mapping_weight: 1 },
    ] },
  ], "lexical-dominant-v1");
  assert.equal(ranked[0].section_key, "concept");
  assert.equal(ranked[0].concept_score, 1);
  assert.ok(ranked.find(hit => hit.section_key === "lexical").final_score > 0,
            "concept curation must never shadow a raw FTS hit to zero");
});


test("synonyms use MAX rather than SUM and provenance reconstructs the arithmetic", () => {
  const [hit] = rankSituationCandidates([{
    section_key: "diagnosis-checklist",
    lexical_score: 0,
    concept_matches: [
      { phrase_id: "p-weak", concept_id: "c1", mapping_id: "m1", phrase_strength: 0.5,
        phrase_weight: 1, mapping_weight: 1 },
      { phrase_id: "p-strong", concept_id: "c1", mapping_id: "m1", phrase_strength: 1,
        phrase_weight: 0.8, mapping_weight: 1 },
    ],
  }], "lexical-dominant-v1");
  assert.equal(hit.concept_score, 0.8);
  assert.deepEqual(hit.provenance.contributors.map(row => row.phrase_id), ["p-strong", "p-weak"]);
  assert.equal(hit.provenance.policy_id, "lexical-dominant-v1");
  assert.equal(hit.final_score, 0.2);
});


test("coequal policy normalizes per query, awards dual evidence, and replays byte-identically", () => {
  const input = [
    { section_key: "b", lexical_score: 0.5, concept_matches: [
      { phrase_id: "p2", concept_id: "c2", mapping_id: "m2", phrase_strength: 1,
        phrase_weight: 0.5, mapping_weight: 1 },
    ] },
    { section_key: "a", lexical_score: 1, concept_matches: [] },
  ];
  const first = rankSituationCandidates(input, "coequal-normalized-v1");
  const second = rankSituationCandidates(structuredClone(input), "coequal-normalized-v1");
  assert.deepEqual(first, second);
  assert.equal(JSON.stringify(first), JSON.stringify(second));
  assert.equal(first.find(hit => hit.section_key === "b").final_score, 1.65);
});


test("stable tie order is final, concept, lexical, then section key", () => {
  const ranked = rankSituationCandidates([
    { section_key: "z", lexical_score: 0, concept_matches: [] },
    { section_key: "a", lexical_score: 0, concept_matches: [] },
  ], "lexical-dominant-v1");
  assert.deepEqual(ranked.map(hit => hit.section_key), ["a", "z"]);
});


test("registry exposes machine proposals and human-only approval/retirement", () => {
  class ToolError extends Error { constructor(payload) { super(payload.error); this.payload = payload; } }
  const tools = situationRetrievalTools({
    withEnvelope: async (_c, _a, _n, _args, fn) => fn(),
    writeEvent: async () => {},
    ToolError,
  });
  for (const name of [
    "propose-retrieval-concept", "propose-retrieval-phrase",
    "propose-retrieval-mapping", "propose-retrieval-retirement",
  ]) {
    assert.equal(tools[name].write, true, name);
    assert.notEqual(tools[name].humanOnly, true, name);
  }
  // humanOnly LABEL RETIRED (WR-000019 slice S1, 2026-08-27): dead since
  // executeRegisteredTool stopped reading it 2026-08-26 (decision dc57f62d);
  // this slice drops the stale declaration from situation-retrieval.js. Both
  // verbs are still write:true, human-governed approval/retirement acts.
  for (const name of ["approve-retrieval-proposals", "retire-retrieval-curation"])
    assert.equal(tools[name].humanOnly, undefined, name);
});


test("one-word phrases are refused before any database write", async () => {
  class ToolError extends Error { constructor(payload) { super(payload.error); this.payload = payload; } }
  const tools = situationRetrievalTools({
    withEnvelope: async (_c, _a, _n, _args, fn) => fn(),
    writeEvent: async () => {},
    ToolError,
  });
  let queried = false;
  const client = { query: async () => { queried = true; return { rows: [] }; } };
  await assert.rejects(
    tools["propose-retrieval-phrase"].handler(client, { id: "actor" }, {
      idempotency_key: "00000000-0000-4000-8000-000000000001",
      concept_key: "record-layer-outage", phrase: "outage", reason: "RET-002",
      source: "golden_miss", source_ref: "RET-002", match_mode: "exact",
      weight: 1,
    }),
    error => error.payload?.error === "retrieval_phrase_too_short",
  );
  assert.equal(queried, false);
});


test("migration preserves revision bytes and installs every governed database contract", () => {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const migration = fs.readFileSync(path.join(here, "../../migrations/0135_situation_retrieval.sql"), "utf8");
  for (const table of [
    "retrieval_concept", "retrieval_phrase", "doctrine_concept_mapping",
    "retrieval_proposal", "retrieval_ranking_policy", "retrieval_query_log",
  ]) assert.match(migration, new RegExp(`create table ${table}\\b`, "i"), table);
  assert.match(migration, /content_hash[\s\S]+is distinct from/i);
  assert.match(migration, /setweight\([^)]*title_vector[^)]*,\s*'A'/i);
  assert.match(migration, /setweight\([^)]*search_vector[^)]*,\s*'B'/i);
  assert.match(migration, /max\s*\(/i, "synonym contribution must not SUM");
  assert.match(migration, /digest\([^)]*sha256/i);
  assert.doesNotMatch(migration, /raw_query/i);
  assert.match(migration, /needs_repair/i);
  assert.match(migration, /grant select, insert, update[\s\S]+retrieval_proposal/i);
  assert.match(migration, /p_policy_id[\s\S]+is_default[\s\S]+status='active'/i,
               "normal retrieval must select the default policy row");
  assert.match(migration, /RET-001[\s\S]+RET-AMB-001/i,
               "approval reruns the complete grown doctrine suite");
  assert.match(migration,
               /review cycle after a record layer outage diagnosis[\s\S]+review cycle after a record layer outage playbook/i,
               "seed proposals must make both governed targets reachable for the ambiguous golden");
  assert.match(migration,
               /v_retrieval_concepts_without_targets[\s\S]+s\.status\s*=\s*'active'[\s\S]{0,160}s\.current_revision_id\s+is\s+not\s+null/i,
               "zero-target health must use the same active/current lifecycle predicate as retrieval");
  assert.match(migration, /revoke all on function promote_retrieval_proposal/i);
});


test("batch approval orders dependencies before promotion", async () => {
  class ToolError extends Error { constructor(payload) { super(payload.error); this.payload = payload; } }
  let selectSql = "";
  const client = { query: async sql => {
    if (/from retrieval_proposal/.test(sql)) { selectSql = sql; return { rows: [] }; }
    return { rows: [] };
  } };
  const tools = situationRetrievalTools({
    withEnvelope: async (_c, _a, _n, _args, fn) => fn(), writeEvent: async () => {}, ToolError,
  });
  await tools["approve-retrieval-proposals"].handler(client, { id: "human" }, {
    idempotency_key: "00000000-0000-4000-8000-000000000002",
    proposal_ids: [], base_versions: {}, golden_suite_digest: "a".repeat(64),
  });
  assert.match(selectSql, /when 'concept' then 1[\s\S]+when 'phrase' then 2[\s\S]+when 'mapping' then 3/i);
});


// THE ENTRY POINT EVERY SESSION USES HAD NO TEST (found 2026-08-21, defect
// 86403252). search-doctrine returned a bare "internal error" for every live
// query while the full golden suite stayed green, because the suite exercises
// the ranker through the in-database gate, which passes a NULL actor
// (db/schema.sql ~line 5429). The real path resolves a sponsor first, and
// nothing ever walked it. These two tests walk it.

test("search resolves the SPONSOR row and hands that id to the ranker", async () => {
  const calls = [];
  const client = {
    query: async (sql, params) => {
      calls.push({ sql, params });
      if (/retrieval_visibility_actor_id/.test(sql)) return { rows: [{ id: "sponsor-uuid" }] };
      return { rows: [] };
    },
  };
  const out = await searchDoctrineSituations(
    client, { slug: "joe", human: true }, { q: "diagnosis checklist", limit: 3 });

  assert.equal(calls.length, 2, "expected the sponsor lookup then the ranker call");
  assert.deepEqual(calls[0].params, ["joe"], "sponsor is resolved by slug, never from tool args");
  assert.equal(calls[1].params[0], "diagnosis checklist");
  assert.equal(calls[1].params[1], "sponsor-uuid",
    "the ranker must receive the resolved HUMAN row id, not the runtime principal");
  assert.equal(out.ok, true);
  assert.equal(out.generated_text, false);
});

test("a refused visibility scope names itself instead of becoming internal error", async () => {
  // mcp.js maps any non-ToolError to the literal string "internal_error", so an
  // untyped throw here is indistinguishable from a crash and tells no operator
  // which of the two conditions fired.
  const client = { query: async () => ({ rows: [] }) };  // sponsor row absent
  await assert.rejects(
    searchDoctrineSituations(client, { slug: "joe", human: true }, { q: "runbook" }),
    error => {
      assert.ok(error.payload, "the failure must carry a typed payload, not a bare Error");
      assert.equal(error.payload.error, "retrieval_scope_refused");
      assert.equal(error.payload.reason, "sponsor_not_active_human");
      return true;
    });
});


// THE OUTAGE, as a test (found 2026-08-21 from ops.incident_fact, not from
// reading code). Commit 4abafd3b put "select id from actor where slug=$1 and
// kind='human' and active=true" on the READ path. carr_reader has column SELECT
// on actor.id and actor.slug and none on kind or active, and Postgres refuses a
// predicate over a column you cannot read — so every doctrine search died with
// "permission denied for table actor", flattened to a bare "internal error".
// Migration 0223 moves the lookup behind a definer function. This test fails if
// anyone puts the raw table back on the read path.
test("sponsor resolution never touches the actor table from the read path", async () => {
  const statements = [];
  const client = {
    query: async (sql, params) => {
      statements.push(sql);
      if (/retrieval_visibility_actor_id/.test(sql)) return { rows: [{ id: "sponsor-uuid" }] };
      return { rows: [] };
    },
  };
  await searchDoctrineSituations(client, { slug: "joe", human: true }, { q: "runbook", limit: 3 });

  const lookup = statements.find(sql => /actor/.test(sql));
  assert.ok(lookup, "the sponsor must still be resolved, not skipped");
  assert.match(lookup, /retrieval_visibility_actor_id/,
    "sponsor resolution must go through the definer function");
  assert.doesNotMatch(lookup, /\bfrom\s+actor\b/,
    "the read path must never select from the actor table directly");
  assert.doesNotMatch(lookup, /kind\s*=|active\s*=/,
    "the read path must not filter on actor columns carr_reader cannot read");
});

test("a logged query rides the side-write channel, never the read connection", async () => {
  // The ranker used to carry `insert into retrieval_query_log` inside its own
  // statement, which made this read a write. The log is worth keeping and worth
  // strictly less than the answer, so it moves to the write credential and is
  // never awaited: losing it must cost a log row, never the reply.
  const readStatements = [], sideWrites = [];
  const client = {
    query: async (sql) => {
      readStatements.push(sql);
      if (/retrieval_visibility_actor_id/.test(sql)) return { rows: [{ id: "sponsor-uuid" }] };
      return { rows: [{ section_id: "sec-1", final_score: 0.9,
                        provenance: { policy_id: "coequal-normalized-v1", policy_version: 1 } }] };
    },
    sideWrite: (sql, params) => { sideWrites.push({ sql, params }); },
  };
  const out = await searchDoctrineSituations(client, { slug: "joe", human: true }, { q: "runbook" });

  assert.equal(readStatements.some(sql => /insert\s+into/i.test(sql)), false,
    "no write may be issued on the read connection");
  assert.equal(sideWrites.length, 1, "the query log write goes out exactly once");
  assert.match(sideWrites[0].sql, /log_retrieval_query/);
  assert.deepEqual(sideWrites[0].params[3], JSON.stringify({ high: 1, medium: 0, low: 0 }),
    "score bands are computed from the returned rows");
  assert.equal(out.total, 1, "the answer is returned regardless of logging");
});

// THE ZERO-HIT FALLBACK (loop 518, built 2026-08-22). Doctrine search requires
// every query word, so a natural question naming one word the right section
// lacks returned nothing at all, with no second try. The verb now asks the
// ranker for a fallback pass; the ranker only uses it when the strict pass is
// empty, and marks every fallback row in its provenance. Deliberate-negative
// golden cases stay on the strict path — the gate calls the ranker without
// the fallback argument, and the default is off.

test("the verb opts into the ranker's fallback and reports a fallback answer", async () => {
  const calls = [];
  const sideWrites = [];
  const client = {
    query: async (sql, params) => {
      calls.push({ sql, params });
      if (/retrieval_visibility_actor_id/.test(sql)) return { rows: [{ id: "sponsor-uuid" }] };
      return { rows: [{ section_id: "sec-1", final_score: 0.4,
                        provenance: { policy_id: "coequal-normalized-v1", policy_version: 1,
                                      fallback: true } }] };
    },
    sideWrite: (sql, params) => { sideWrites.push({ sql, params }); },
  };
  const out = await searchDoctrineSituations(client, { slug: "joe", human: true }, { q: "renewals for dental clients" });

  assert.equal(calls[1].params.length, 6, "the ranker call carries the fallback argument");
  assert.equal(calls[1].params[5], true, "the live verb always allows the fallback pass");
  assert.equal(out.fallback, true, "a fallback answer says so at the top level");
  assert.equal(sideWrites.length, 1);
  assert.equal(sideWrites[0].params[6], false,
    "a query only answered by fallback is still logged as a miss for curation");
});

test("a strict answer reports no fallback and logs a hit", async () => {
  const sideWrites = [];
  const client = {
    query: async (sql) => {
      if (/retrieval_visibility_actor_id/.test(sql)) return { rows: [{ id: "sponsor-uuid" }] };
      return { rows: [{ section_id: "sec-1", final_score: 0.9,
                        provenance: { policy_id: "coequal-normalized-v1", policy_version: 1 } }] };
    },
    sideWrite: (sql, params) => { sideWrites.push({ sql, params }); },
  };
  const out = await searchDoctrineSituations(client, { slug: "joe", human: true }, { q: "runbook" });
  assert.equal(out.fallback, false);
  assert.equal(sideWrites[0].params[6], true, "a strict answer logs as an explicit hit");
});

test("an empty answer reports no fallback and logs a miss", async () => {
  const sideWrites = [];
  const client = {
    query: async (sql) => {
      if (/retrieval_visibility_actor_id/.test(sql)) return { rows: [{ id: "sponsor-uuid" }] };
      return { rows: [] };
    },
    sideWrite: (sql, params) => { sideWrites.push({ sql, params }); },
  };
  const out = await searchDoctrineSituations(client, { slug: "joe", human: true }, { q: "no such thing" });
  assert.equal(out.fallback, false);
  assert.equal(out.total, 0);
  assert.equal(sideWrites[0].params[6], false);
});

test("search still answers when the side-write channel is absent", async () => {
  const client = {
    query: async (sql) => {
      if (/retrieval_visibility_actor_id/.test(sql)) return { rows: [{ id: "sponsor-uuid" }] };
      return { rows: [{ section_id: "sec-1", final_score: 0.5, provenance: {} }] };
    },
    sideWrite: null,   // no write credential configured
  };
  const out = await searchDoctrineSituations(client, { slug: "joe", human: true }, { q: "runbook" });
  assert.equal(out.ok, true);
  assert.equal(out.total, 1);
});
