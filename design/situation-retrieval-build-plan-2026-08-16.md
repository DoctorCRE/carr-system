# Situation-Retrieval Build Plan — WR-AI-006 (for the Codex build session)

*Dated design memo, written 2026-08-16 from the frontier-council ruling of the same
date (decision a56ddb62, "Situation-language doctrine retrieval is a human-approved
concept bridge beside FTS"). The decision entry is the authority; if this memo and
the decision log disagree, the decision log wins. Full chair positions and host
synthesis: `out/council/20260816T023523Z-retrieval/` on Joe's Mac (session output,
not tracked). This plan is self-contained on purpose — the build session has no
other context.*

## What you are building, in one paragraph

Sessions and partners describe an operating situation in their own words
("record layer outage diagnosis", "the landlord went quiet") and must get back
the exact, current doctrine sections that govern it. Keyword FTS misses these;
the pgvector pilot (branch `vector-retrieval-pilot`) measured that semantic
search still missed concepts and was never promoted. The settled answer: fix a
title-indexing gap in FTS first, then add a small HUMAN-APPROVED concept bridge
(situation concepts → approved phrases → concept-to-section mappings) that runs
BESIDE FTS, unioned, with open arithmetic ranking, query-time staleness
rejection, and the golden scorecard gating every change. No model calls at
index or query time. No generated answer text. Ever.

## Governance (from the Work Request itself — WR-AI-006, base_version 3)

- Work in an isolated worktree (`./run.sh worktree <name>`). Never move the
  canonical tree's HEAD.
- WRITE THE TESTS FIRST — red, then green. Every gate/validator gets a selftest
  before the thing it gates exists (house rule: ops/ci.sh shipped without one
  and collected five defects in one afternoon).
- Commit your own work with NAMED PATHS (never `-A`/`-a`/`.`), push the branch,
  open a PR; automerge takes green PRs. Never merge, deploy, mutate Production,
  or mark the Work Request complete from the build session — return a candidate
  for independent attestation.
- Data class D2 (internal metadata). No client or personal data in any fixture,
  log, or test string.
- All record-layer mutations go through verbs with `idempotency_key` +
  `base_version`. Curation activation is HUMAN-ONLY (the verb refuses machine
  identities, same as `teach`/`activate-rule`).

## Phase 0 — index the titles (lexical fix BEFORE anything conceptual)

**The measured gap** (verified live 2026-08-16, doctrine generation 435):
`plain_text` on `doctrine_revision` is body text only — see the insert at
`mcp-server/src/doctrine.js` (~line 397 and ~796): `args.body_text` goes in
verbatim. Section titles and document titles never enter
`search_vector` (a STORED generated column, `to_tsvector('english',
plain_text)`, migration `0075_doctrine_store.sql` line ~128). Consequence: the
runbook section titled "Diagnosis checklist (in order, 2 minutes)" returns
ZERO for the query "diagnosis checklist".

**Requirement, not implementation mandate** (choose the cleanest mechanism and
prove it by test):
- Queries matching a section title or its document title rank that section,
  with title matches weighted ABOVE body matches (`setweight` A vs B).
- The append-only revision law is not violated: never rewrite an existing
  revision's `body`/`content_hash`. Candidate mechanisms: a trigger-maintained
  or generated title vector on `doctrine_section` OR'd into the search
  predicate and rank; or a title-inclusive `search_vector` redefinition IF it
  can be done without touching historical revision bodies. A backfill migration
  is expected; content hashes must be byte-identical before/after.
- `search-doctrine` (in `mcp-server/src/doctrine.js`) and the section-index
  scorer (`pipelines/build-section-index.py` + `tools/retrieve.py`) both see
  titles, or you document why one deliberately does not.

**Tests first:** extend `evals/retrieval/golden-queries.v1.json` conventions —
a new case "diagnosis checklist" → `runbook#diagnosis-checklist-in-order-2-minutes`
must go red before the change and green after.

**Then RE-BASELINE:** re-run the full golden suite, write a new dated baseline
file beside `evals/retrieval/baselines/doctrine-fts.2026-08-16.title-v1.json`. The
concept bridge (Phase 1) is credited only with misses that survive Phase 0 —
that ordering is part of the council ruling.

## Phase 1 — the concept bridge (schema + verbs)

One migration, following house patterns (UUID pks, verbs-only writes, status
vocabularies, partial indexes on active rows). Name tables in HUMAN words —
the council sketches used `retrieval_concept` / `doctrine_concept`; pick one
family and keep it plain. Four tables:

1. **Concept** — one row per named operating situation. Columns: stable
   `concept_key` (kebab), display label, 1–2 sentence definition
   (disambiguation only), status `proposed|approved|retired`, version
   (optimistic concurrency), review_after, proposer, approver (human),
   timestamps. Unique on concept_key.
2. **Phrase** — approved surface forms in real-world language. Columns:
   concept FK, display phrase, normalized phrase (one shared normalization
   function — lower/trim/collapse whitespace — implemented ONCE, used by both
   write and query paths), match_mode `exact|fts|trgm` (+ per-row
   min_similarity for trgm), weight (bounded), status/version/approval columns
   as above, source (`golden_miss|no_hit_log|session_proposal|manual`) +
   source_ref. Unique (concept, normalized phrase); enforce a global
   active-phrase uniqueness check across concepts (a collision is refused or
   requires explicit disambiguation). Reject one-word phrases at the verb.
   Indexes: partial GIN fts + trigram on normalized phrase WHERE active.
3. **Mapping** (concept → section) — concept FK, `doctrine_section` FK, role
   `governs|supports`, weight (bounded), rationale text (required), status/
   version/approval columns. Unique (concept, section, role). Partial index on
   section_id WHERE active (powers the repair view and retire hooks).
4. **Proposal trail** — append-only: proposal_type `concept|phrase|mapping|retire`,
   jsonb payload, reason (the miss id or the partner's paraphrase), proposer
   (machine OK), status `pending|approved|rejected|superseded`, reviewer,
   resulting row ids, idempotency_key unique.

**Explicitly NOT in v1:** a relation/edge table (no measured query needs it —
equivalent language is more phrases on one concept); workspace/tenant columns
(tenancy was deliberately deferred by the doctrine-store council; scope rides
the existing scope_ref discipline, applied before ranking); any model call.

**Verbs** (register + grant-check every table they write, per the
new-trigger/permission-surface rule):
- `propose-*` (machine-callable): validates, writes the proposal trail.
- `approve-*` / `retire-*` (HUMAN-ONLY): promotes proposal rows
  transactionally, records approver identity per row; approving a batch is one
  human action, many audited rows. Approval RE-RUNS the golden suite and
  REFUSES a row that breaks a passing case.
- Section retire/supersede integration: in the same transaction, dependent
  mappings flip to needs-repair; the query-time join (Phase 2) is the safety
  net if that write path is ever bypassed.
- A repair view: active mappings whose target section is no longer
  active/current; a concepts-with-zero-active-targets view. Health gets a count
  row with a bound action (nonzero → the repair queue is worked), per the
  no-metric-without-a-bound-action rule.

## Phase 2 — the retrieval verb (deterministic, explainable)

Extend `search-doctrine` or add a sibling read verb — either way ONE code
path, no duplicate ranking logic anywhere.

Pipeline (fixed order):
1. Normalize the question with the shared normalization function; keep the raw
   string only for the caller's echo, never in logs.
2. Materialize the currently-retrievable section set: document active ∧
   section active ∧ current revision ∧ the exact archive/superseded predicates
   `search-doctrine` uses today (one shared predicate, two lanes — they must
   never drift). Scope filter here, BEFORE any ranking.
3. Lane A: raw FTS, unchanged tsquery, over that set → ts_rank_cd scores.
4. Lane B: match ACTIVE phrases (exact → fts → trigram, strongest rule wins
   per phrase), resolve concepts, follow ACTIVE mappings into the same
   materialized set. Per-section concept score = MAX (never SUM — synonym
   accumulation must not compound) of phrase_strength × phrase.weight ×
   mapping.weight.
5. Union by section. Combined score from a VERSIONED RANKING-POLICY row —
   the constants live in data, not code. Two candidate policies ship, both
   implemented, selected by policy id:
   - `lexical-dominant-v1` (Sol chair): final = 0.75·L + 0.25·C, L and C each
     bounded [0,1].
   - `coequal-normalized-v1` (Grok chair): final = norm(L) + norm(C) + 0.15
     dual-evidence bonus (per-query max normalization).
   The golden suite picks the winner (Phase 3); the loser stays implemented
   and selectable so the comparison is reproducible.
6. Deterministic order (final DESC, concept-score DESC, lexical DESC,
   section_key ASC). Return: section address + current text/snippet +
   a provenance object per hit — lane scores, final, policy id, and the row
   ids of every contributing phrase/concept/mapping. NO generated prose.

Rules the arithmetic must satisfy (test these properties directly):
- The raw tsquery is never rewritten by the concept lane.
- A pure-FTS hit can never be removed or outscored to zero by curation
  (anti-shadow property).
- A concept-only hit CAN reach the top when FTS returns nothing (both measured
  misses are exactly this case).
- Same inputs → byte-identical output (deterministic replay test).

Query logging: normalized-query HASH, result count, score bands, selected row
ids, policy version, explicit hit/miss signal. NEVER the raw query text —
future questions may contain client language even though today's class is D2.

## Phase 3 — evaluation is the governor

- Keep the 11 existing cases immutable. Add, by failure class:
  situation-language positives (the two misses first: RET-002 "record layer
  outage diagnosis runbook" → `runbook#diagnosis-checklist-in-order-2-minutes`,
  RET-003 "playbook self improvement review cycle" → `playbook-review#preamble`);
  a paraphrase per approved phrase; near-miss negatives (e.g. "outage
  communication template" must NOT return the diagnosis checklist); structural
  negatives (retired / superseded / archive / old revision must return
  nothing); ambiguous duals (both allowed in top-k, ordered).
- Gates (release-blocking, wired into CI with a selftest for the gate script
  itself): labeled current section in top-3 on every case · zero
  stale/archive leakage at any rank · scope filter proven pre-rank ·
  provenance complete on every hit · deterministic replay mismatch = 0.
  Diagnostic-only: MRR, pure-concept vs dual-lane share.
- Run both ranking policies over the grown suite; record both scorecards as
  dated baseline files; the winner becomes the default policy row. Log the
  numbers in the completion evidence.
- Seed curation content (as PROPOSALS only — Joe or Dell must approve):
  concepts + phrases + mappings for the two golden misses, drawn from the
  worked examples in the council chairs' outputs.

## Acceptance (Work Request bar, restated)

RET-002 and RET-003 return their exact current addresses in top-3 with concept
provenance · all original cases still pass · negative and lifecycle fixtures
green · scope filtered before ranking · every hit carries reconstructible
arithmetic provenance · the eval gates subsequent schema/ranking/curation
changes · rollback path intact (lexical baseline still runnable; regenerate
index; the concept lane is additive and can be disabled by policy row).

## Do-not-build (settled by council; do not relitigate in the build session)

- Embeddings, pgvector revival, or any model at index/query time.
- Postgres thesaurus/synonym dictionary files.
- Concept-relation traversal in ranking; graph-as-authority.
- Generated answer text on the retrieval path.
- Raw production-query logging.
- Per-section hidden keyword-cue fields folded into the section tsvector.

Reopen condition (recorded in the decision): only if, AFTER Phase 0 + the
bridge are live and measured, the golden suite still shows a conceptual-miss
class no curated vocabulary closes — and any vector proposal must then beat
this build's measured scorecard, not argue against its idea.
