# Reviewed vendor market registry

## System design

The app calls `read-vendor-markets-v1` through authenticated `/mcp` JSON-RPC `tools/call`, with `{market: "reviewed alias"}` or `{}`. The existing signed-in session proxy supplies the actor. `vendor-markets.v1` returns a canonical `selected_market`, markets, memberships, Regional references, and unique vendor records with existing directory people. The app can retain that canonical selection across refresh. This producer makes no app changes.

PostgreSQL owns market definitions, the canonical and alias key namespace, exact vendor memberships, and explicit local-to-Regional links. Canonical keys are installed with the market. Primary keys reject ambiguous aliases and duplicate market/vendor pairs. Foreign keys restrict Regional links to the declared kinds. Office presence is a reviewed market declaration. The existing person office finding remains the street-address evidence. Service coverage is separately reviewed text. Territory has no part in this query.

Two designs were considered. A single JSON registry would make publication atomic but move uniqueness, alias ambiguity and membership identity checks into every publisher. Normalized tables put those invariants in PostgreSQL. The selected design provides one read, with the join and count policy hidden from consumers. Existing directory and meeting contracts remain their own shapes. Their office projection is reused.

The read uses one SQL statement and its PostgreSQL snapshot. There is no cache, stale fallback, queue, external API call, retry or write. Database failure refuses the read through the existing MCP error path. No external-call circuit breaker or write idempotency applies. One request runs one statement; there is no application fan-out or N+1 request loop. SQL projects people through the existing lateral office lookup, which is the first cost to watch as the directory grows.

Capacity is bounded at 100 markets and 5000 live memberships, including at 10x growth. The query reads one extra row to detect either overflow and refuses the entire result. The initial registry has no supplied population, so no actual load or latency is claimed. Markets are expected to remain small. Alias and reference storage are indexed by their market. Future publishing must enforce a reviewed population appropriate to this bounded read. PostgreSQL spreads concurrent reads; the producer does not start a scheduler.

## Read states and verification

This is a stateless snapshot read, not a persisted workflow. No autosave, cursor or lifecycle state is introduced. `empty` is returned with empty arrays and null selection when no market exists. A populated registry returns `ready`; unknown selectors refuse. Authority, tenant and capacity failures refuse rather than returning partial data. The PostgreSQL test exercises each outcome, canonical alias selection, counts, people without channels, opaque territory, multi-market references, and distinct Regional coverage. Unit tests refuse unauthorized callers before SQL.

The recorded fixture in `mcp-server/contracts/fixtures/vendor-markets.v1.json` comes from the registered verb on disposable PostgreSQL with synthetic data. It is an app integration fixture, not market research or production data. The contract file owns the response field meanings.

## Required reviewed data

Joe or an authorized data job must supply the canonical market list, all required aliases, exact boundaries, explicit vendor UUID memberships, office declarations, service coverage and review provenance. That review must settle Sandestin's boundary, distinguish Florida statewide service from Florida Panhandle coverage, and name each local market's Regional references. This migration supplies none of those business facts. A future authorized publisher must load the registry transactionally, keeping canonical keys and market identities intact. No deployed registry writer is added here.

## Delivery scope

DoctorCRE/carr-system owns the migration and read. jbookout/doctorcre-app consumes the versioned contract in a separate slice. jbookout/software-factory needs no runtime or workflow change.

PRODUCTION PROOF after a containing release is deployed: `./run.sh call read-vendor-markets-v1 '{}'` returns `vendor-markets.v1` and an explicit empty registry until reviewed data is supplied. Once supplied, compare exact membership UUIDs and counts with that reviewed dataset. This unit does not populate production.
