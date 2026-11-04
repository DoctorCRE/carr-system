# Production proof receipt lifecycle

`chart.mmd` scopes the design lifecycle to P3 receipt ingestion. P4 owns closure, and P5 through P7 own execution adapters. The Postgres test uses registered tools and synthetic keys. It exercises rejection without writes, trusted deployment, reservation, pass, fail, block, retry, delayed observation, release replacement, rollback, durable replay and immutable history.

The status evaluator selects the newest server-issued reservation for each assertion in the current deployment. A pending reservation removes the prior pass. An old receipt can persist for audit but cannot supersede a newer reservation. A new deployment always starts unproven, including a rollback without merge membership.

The server trusts an operator-provisioned collector to attest merge membership and the P1 manifest binding. It authenticates proof writers by CARR actor ID plus Ed25519 signature. There is no enrollment tool. Runner provisioning and revocation require a separate privileged operation. Database administrators remain privileged; immutable triggers and runtime grants prevent ordinary updates and deletion. Artifact descriptors are signed metadata, not a claim that CARR fetched their contents.

The chart command generated a TLA+ skeleton, then exited 2 because TLC was unavailable. No model-checking or PNG result is claimed. The sketch omits cryptographic algorithms and database isolation; executable Postgres tests cover the implemented transitions.

Run the executable check with `node --test mcp-server/test/production-proof-postgres.test.mjs`.
