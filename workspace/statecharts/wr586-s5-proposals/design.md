# Client Tour List proposal storage

Card: WR586.S5.J13

## System design

The data shape is an immutable proposal version, its exact request and receipt, and immutable canonical membership for a partner publication revision. Model the Domain led to immutable rows instead of mutable portal JSON. Make Operations Idempotent led to stored request receipts. Boundary Discipline keeps client scope validation in the SQL entrypoint. The proposal version stores its portal, publication revision and deal. Membership is private producer data. Portal scope is derived from the existing session digest and slug. The SQL function owns authorization, CAS and request replay atomically. The TypeScript adapter owns transport and status mapping. The app keeps its injected read/save ports and unwraps `selection`.

Size is medium, one coupled persistence slice. The lightest sufficient design reuses portal sessions and client publications. Reusing publication content itself was rejected because it contains partner-authored text, no canonical property membership, no isolated client versions and no frozen-request receipts. Migration 1183 adds these missing storage grains. The existing ops.tour_selection_cart_version is partner canonical work and cannot receive portal writes. ops.tour_share_feedback_event is per-property immutable packet feedback without whole-list CAS. ops.slice_completion_proposal concerns engineering slice completion. None is an isolated whole-list proposal store.

Two interfaces were compared. New MCP verbs with password/slug inputs would duplicate session entrypoints and expose this client workflow in the partner registry. Session-bound SQL with a typed adapter fits the already deployed session contract. The latter is selected. There are no new registered verbs. SQL execute grants and the grant-contract entries record authority. The trusted publication producer calls bind only after the existing partner deal authorization. It must not forward that operation through client transport. Partner reads similarly require existing deal authorization before calling the exact-version SQL function.

Current main contains migration 1152 and its SQL session functions, but not the held `portal-web.js` adapter. The retained adapter at cc16e75fdc219e7c471536a72c56a9657fd19e0d uses those functions and contains origin, session-cookie, rate and concurrency handling. Its historical projection omits publications and cannot be installed unchanged. This slice reconciles to the current session SQL seam. It does not restore that outdated HTTP handler or introduce another authentication system. Rank 6 owns host integration.

One portal row serializes reads, saves, session retirement and partner membership publication. A publication row is locked after the portal. Version reads use the composite primary key; request replay uses the portal/key unique index. No per-property query loop, queue, cache, external API, fan-out or retry loop exists. Storage and indexes are the first scaling constraint at tenfold load. Requests cap at 256 KiB, 100 selected properties, 2000 characters per note and 10000 versions per publication. Historical versions are retained, not evicted. SQL errors propagate. Database deadline, pool capacity and public HTTP abuse controls belong to the existing host, documented in the contract. Availability is synchronous and exact. An unavailable database refuses rather than serving stale success. External circuit breakers are n/a because the module makes no external calls.

Current publication membership is mandatory for each new save. Publishing a changed revision requires a fresh immutable binding. Previous notes remain in historical versions and the latest read identifies retired IDs. A retry first rechecks session and publication enablement, then returns its original receipt before checking current revision. Withdrawal refuses client reads and retries; authorized partner history remains available. Proposal state never writes carts, routes, contacts, deals or canonical properties.

## Throughput checkpoint

1. Blocking first steps were session, property identity, retained app ports and storage inspection.
2. Independent workstreams are n/a because one owner must keep SQL and contract coherent.
3. Shared mutable state is one portal's ordered publication and selection revisions, protected by a database row lock.
4. Smallest safe decomposition is one worker with tests across the typed adapter and actual SQL functions. No subagents.

## Statechart and tests

`Proposals.tla` bounds the model to two distinct requests and two committed versions. It models atomic database calls, two tabs with separate base versions, replay, stale conflicts, reload and session retirement. TLC checks NoUnauthorizedWrite, NoStaleWrite, ConfirmedVersionExists and ReceiptVersionsUnique. It omits PostgreSQL lock scheduling, payload validation, publication changes and transport timeouts. The database tests cover those concrete data guards; the retained app model owns browser uncertainty and disposal.

| Transition | Executed test |
| --- | --- |
| Save | empty read, save, independent reload and frozen retry |
| Retry, including after later versions | newer writes retain original retry and historical partner read |
| Conflict and Reload | empty read, save, independent reload and frozen retry; two tabs race on one version and reload from a new connection |
| Expire and Denied | session retirement refuses reads, saves and retained replay; disabled, expired and reassigned portals refuse before replay |
| Publication change and retirement | publication changes retire identities without rewriting their notes |
| Forged identity and invalid data | forged scope, malformed input and current publication membership |

To regenerate the model chart, copy the TLA/config files to scratch and run the installed statechart tool with `from-spec Proposals.tla`. Its first invocation creates a mapping. Set `state(v)` to `v.phase`, include Save(1), Save(2), Retry(1), Retry(2), Reload(1), Reload(2), Conflict, Expire and Denied in readerActions, and rerun with JAVA=/opt/homebrew/opt/openjdk@21/bin/java and TLA2TOOLS pointing to tla2tools.jar. The mapping stays in scratch because this repository requires authored executable code to be TypeScript. The committed Mermaid chart is the resulting readable projection.

## Production proof

After a containing release, use the shared release reader to verify merge ancestry. Through an existing authorized portal session, read an existing proposal with `portalProposalStorage.read`. Read the same exact version through `ops.read_portal_proposal_for_deal` after partner deal authorization. Compare version, publication identity, selected IDs and notes exactly. Use known foreign-portal canaries in staging with the same production SQL handler and require 401. Positive save evidence requires an existing authorized receipt. No cohort or receipt means exit 78. This worker performs no production calls or saves. HTTP host integration, partner acceptance and public release proof remain with the assigned downstream units.

## Design and debt pass

Codebase-design keeps session authorization, membership and version semantics behind one SQL operation and a small typed adapter. No new generic persistence framework or duplicate session implementation is introduced.

Zero-tech-debt keeps one current publication binding per immutable source revision. Historical proposals retain their original identities. No compatibility modes or dead aliases are added. No existing caller is replaced by this new storage capability.
