# Watch v2 design

The data shape is an actor, canonical subject, subscription generation, immutable baseline, replaceable policy, state and lifecycle version. Each write has a UUID replay key.

Two designs were considered. Extending the v1 table would make its whole-row responses and existing producer observe new policy state, requiring a v1 change. A separate v2 subscription table isolates the contract until a later producer slice adopts it. The chosen table retains one current generation or removed tombstone per actor and subject. This bounds storage by watched subjects rather than command count.

The registered verb interface owns input validation and replay through the existing envelope. One database function owns server actor derivation, visibility, baseline capture, exact-generation checks and lifecycle transitions. An actor-subject advisory transaction lock serializes writes. Read committed reads return committed state. Stale writes refuse instead of merging. Replay returns its historical receipt without applying another transition.

Current and 10x workloads use a primary key lookup per command and an actor/UUID index for pages capped at 100. Visibility checks are bounded by 101 candidates per page. No cache, fan-out, queue, external calls, or new retries exist. Database failure rolls back the envelope and subscription together. Existing transaction timeouts bound lock waits. Idempotency and actor-subject locks spread unrelated subjects across transactions. First pressure is the shared database connection pool, not subscription storage. No external-call circuit breaker applies.

Policies express reply arrivals, lease date thresholds in days, and listing status changes. Self changes default to included. Listings still refuse because there is no canonical identity/visibility contract. This slice persists policies and publishes contracts; it does not connect v2 to alert production. V1 readers, writers, producers and tests remain unchanged.

The chart was compiled with the installed pstack statechart command before the SQL implementation. Initial model checks caught a missing rewatch edge. Final rules are RemovalIsExact and WatchExists, plus generated TypeOK. The chart abstracts database serialization, replay storage and actor identity, which registered PostgreSQL tests exercise separately. It is a lifecycle model, not a concurrency proof.

Regenerate in a scratch directory with `TLA2TOOLS=/path/to/tla2tools.jar node /Users/booko/.claude/plugins/marketplaces/cursor-plugins/pstack/skills/statechart/scripts/statechart.mjs from-chart watch.mmd`.

CARR owns the versioned contract and database state. doctorcre-app needs no change because this PR has no consumer rollout and v1 is intact. software-factory needs no change because this PR changes no orchestration or shared delivery conventions.
