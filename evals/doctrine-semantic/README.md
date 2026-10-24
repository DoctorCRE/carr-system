# Meaning-based doctrine retrieval

The live baseline is 10/10 frozen questions and 3/10 paraphrases with a gold section at rank one. The second live read gives the same ranks. Neither run includes this source change. Real embedding quality remains unmeasured until a separately authorized deployment and backfill. Deterministic fake embeddings prove retrieval mechanics, visibility, and failure behavior only.

## Live question ranks

Baseline observed at 2026-10-24T10:28:27.748498-05:00 in America/Chicago. Readback observed at 2026-10-24T10:30:40.050425-05:00. Every request uses the unchanged query, limit 10, and no filters. NR means absent from the first ten. Paired ranks follow gold-ID order in [questions.json](questions.json). The embedding-after column is pending, not a fake-embedding estimate.

| Case | Question | Live before | Live readback | Embedding after |
| --- | --- | --- | --- | --- |
| B1 | What should I include in a tenant tour packet? | 1 | 1 | Pending |
| B2 | What should Dr. CRE's voice sound like? | 1 | 1 | Pending |
| B3 | What facts should I extract when abstracting a medical office lease? | 1 | 1 | Pending |
| B4 | When should I use an RFP versus an LOI? | 1 | 1 | Pending |
| B5 | How do I choose which vendor introductions to make? | 1 | 1 | Pending |
| E1 | How do I safely test and ship a database migration? | 1 / 2 | 1 / 2 | Pending |
| E2 | How do I ship a code change from branch through PR and deployment? | 1 / 3 | 1 / 3 | Pending |
| E3 | Which checks should I run before pushing my branch? | 1 | 1 | Pending |
| E4 | Which repository owns the DoctorCRE app and software factory? | 1 | 1 | Pending |
| E5 | How should I shape a pull request so it is independently reviewable? | 1 | 1 | Pending |
| PB1 | How do I assemble materials for a tenant tour? | 1 | 1 | Pending |
| PB2 | How should Doc speak in product dialogue? | NR | NR | Pending |
| PB3 | Which fields belong in a medical lease abstraction? | NR | NR | Pending |
| PB4 | Should I select a letter of intent or a request for proposal? | NR | NR | Pending |
| PB5 | How do I decide whether a vendor introduction is ripe? | NR | NR | Pending |
| PE1 | How do I validate database schema changes before release? | 2 / 1 | 2 / 1 | Pending |
| PE2 | How do I deliver a pull request to production? | 2 / 1 | 2 / 1 | Pending |
| PE3 | What verification belongs before a branch push? | NR | NR | Pending |
| PE4 | Where does application code live compared with factory tooling? | NR | NR | Pending |
| PE5 | How do I split work into reviewable PRs? | NR | NR | Pending |

The questions and gold IDs are fixed from the supplied K8 report. The paraphrases are a task-informed diagnostic set, not a blind benchmark. The saved [before.json](before.json) contains ranks and references, with no section bodies. No task phrases, gold labels, or concept mappings were tuned in this change.

## Rerun after deployment and backfill

From the repository root, run this exact read-only command. It calls the deployed Worker through the existing authenticated wrapper and compares all twenty ranks with the committed baseline.

```sh
python3 evals/doctrine-semantic/run.py --baseline evals/doctrine-semantic/before.json --output /tmp/doctrine-semantic-after.json --require-embeddings
```

The command saves every gold rank and semantic metadata. It exits nonzero for a read error or any response whose semantic status is not `ready`. That guard was exercised against the unchanged deployed Worker in this session. All twenty reads succeeded, their ranks matched the baseline, and the command exited 1 because semantic metadata is absent before deployment. A successful guard establishes complete visible embedding coverage, not improved relevance. Inspect the resulting comparison to assess relevance.

## Design decisions

- Store vectors by immutable section revision and pinned embedding model. A section edit cannot use an earlier revision's vector.
- Compute embeddings through the Worker's AI binding. No third-party API key or new secret is required.
- Keep visibility and ranking in PostgreSQL. Retrieve vector candidates independently of lexical candidates, then blend their ranks. Reranking a lexical-only shortlist would preserve lexical misses.
- Preserve the existing lexical/concept ranker and its policy provenance. Expose the separate blend policy and semantic similarity in hit provenance.
- Treat model availability and backfill coverage as response metadata. AI failure preserves the existing lexical results.
- Keep backfill explicit, bounded, and idempotent. `backfill-doctrine-embeddings` accepts `idempotency_key` and `limit` from 1 through 16. Repeated calls with fresh keys fill missing current revisions until `remaining` is zero. A repeated key replays its receipt. Personal sections are backfilled only in their authenticated owner scope. This session does not run backfill against production.

Each lane contributes up to 100 candidates. The blend sums reciprocal ranks with k = 60 and scales by 30.5, so dual rank-one evidence scores 1. These values are recorded in hit provenance. Cosine similarity orders the vector lane and is not a confidence probability.

The revision side table was chosen over embedding columns on append-only revisions because model state has a separate lifecycle. An independent design review agreed with that choice. The alternative would require rewriting revision storage and the baseline ranker for each model change.

## Vendor sources

[Neon pgvector](https://neon.com/docs/extensions/pgvector) documents the vector extension, cosine distance, and HNSW indexes. [Cloudflare Workers AI bindings](https://developers.cloudflare.com/workers-ai/configuration/bindings/) documents direct calls through `env.AI`. [Cloudflare's small BGE model](https://developers.cloudflare.com/workers-ai/models/bge-small-en-v1.5/) documents 384 dimensions, a 512-token input limit, batch input, and pooling. [The model card](https://huggingface.co/BAAI/bge-small-en-v1.5) explains query instructions and warns that similarity is not a calibrated relevance probability. [pgvector hybrid search](https://github.com/pgvector/pgvector#hybrid-search) names reciprocal rank fusion as one way to combine lexical and vector retrieval.

## Verification

The focused suite passes 73/73 tests with zero skips, including 21 semantic cases. Typecheck and current mutation contracts pass. The new migration applies over the current main schema in disposable PostgreSQL 18. Canonical regeneration restores and verifies the resulting schema. The Worker boots in local workerd with `/healthz` returning 200.

The CI control selftest passes 7/7 after adding pgvector to disposable PostgreSQL 17 and 18 servers. CI service containers use a digest-pinned pgvector image. Hosted execution of that image remains pending.

## Limits

The existing visibility predicate excludes inactive sections and selects only their current revision, personal owner scope, and requested content classes. The same predicate governs vector retrieval. The baseline has no detector for an instruction withdrawn only in prose while its section remains active. Search results do not establish operative authority.

Revision text and queries use overlapping chunks of at most 480 UTF-8 bytes with up to 96 bytes of overlap and CLS pooling. Every chunk reaches the model in batches of at most 16. Sections score by the best chunk similarity and appear once. The `chunks-v1` preprocessing identity prevents reuse of vectors from a different input policy. Migration 0956 creates this new side table; backfill builds the chunk vectors from current revision text. Blend settings remain uncalibrated until the real post-backfill evaluation. No general paraphrase improvement, latency improvement, green full CI, deployment, or production backfill is claimed here.

The focused test command is:

```sh
node --test mcp-server/test/doctrine-embeddings.test.mjs mcp-server/test/situation-retrieval.test.mjs mcp-server/test/situation-retrieval-integration.test.mjs mcp-server/test/doctrine-bigint-versions.test.mjs mcp-server/test/situation-retrieval-bigint.test.mjs mcp-server/test/change-doctrine-sections-validation.test.mjs
```

Initial failed-test output is retained in the session tool transcript. A separately retained baseline replay also fails the meaning-only fixture against the main retrieval source. It is a fresh baseline replay, not the original failed-test log. The green suite uses fake deterministic embeddings with real PostgreSQL ranking. Independent source review caught and corrected the CI unit-lane pgvector prerequisite. Comment review recommended zero deletions. The separate deslop skill was not available in the searched skill catalog and cache; manual cleanup was used.
