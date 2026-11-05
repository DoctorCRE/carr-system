# Market study package composition

`composeMarketStudyPackage(research, reviewed, target)` in
`mcp-server/src/market-study-composer.js` is the pure S20 composition step.
It does not read records, authorize a review, publish, hash, or write anything.
The caller supplies a reviewed projection of records, never raw records.

`target` contains nonempty `deal_id` and `client_id` strings. `reviewed` contains
exactly the exported `RECORD_LAYER_GROUPS`. The `client` and `prepared` groups
are envelopes of `{ deal_id, client_id, data }`. `properties` is an array of
these envelopes, one per property. Every envelope must match the target.
Each `data` value uses the existing package schema, including canonical property
IDs in property `data.id`. Identity metadata never enters the package. The caller
must obtain these IDs and the target identity from the authenticated record
workflow. This pure function cannot authenticate caller assertions.

Success returns `{ ok: true, package, errors: [] }`. Failure returns
`{ ok: false, stage, errors: [{ path, message }] }` without a package.
Stages are `research`, `target`, `reviewed`, and `composed`, checked in that order.
Paths are JSON Pointers relative to the failed stage's input. A composed path
refers to the candidate output package. Error order is stable for identical JSON
inputs. The function accepts JSON data, as does the existing validator.

Research validates in partial mode before any replacement. Present record groups
must therefore already be valid. Reviewed groups replace those groups in full.
The result validates in full mode and rejects duplicate property IDs and an
ownership default outside the reviewed set. Unknown display fields fail schema
validation. Explicit internal-note, client-upload and stored-deal-financial keys
also fail recursively, including inside permissive GeoJSON metadata. Reviewed
public property notes and schema-defined ownership assumptions remain allowed.
The caller is responsible for reviewing free text for private content.

## System design

- Architecture and data ownership. One synchronous pure module reuses the package
  validator and its record group registry. Research owns all other groups.
  Tests compare the complete synthetic output and prove deep detachment.
- Capacity and scaling. Work and transient memory grow with JSON package size.
  At ten times the fixture property count, the synthetic test proves the same
  composition contract. Oversized JSON first costs validation CPU and clone
  memory. This module makes no latency or throughput guarantee.
- Load spread and availability. Calls share no mutable request state. Each call
  validates sequentially and returns the first failing stage without partial
  output. Tests repeat calls, interleave distinct inputs, and exercise failures.
- Consistency. Refuse invalid research or mismatched record identities. Never
  repair invalid research through an overlay. Tests exercise both cases.
- Caching. No request cache or invalidation. Schema validators are reused by the
  existing validator. Each call independently reads the supplied JSON values.
- Storage and N+1. Not applicable. No database, filesystem or object-store access.
- Async, queues, circuit breakers, API rate limits and write idempotency. Not
  applicable. No external call, queue, retry, fan-out, write or send exists.
  Iteration is linear over caller-supplied JSON and property envelopes.

## Design and debt pass

Direct unscoped groups would produce a smaller interface but cannot distinguish
mixed-deal properties because the published schema has no identity fields.
Identity envelopes keep the checks in one module without changing that schema or
introducing a second validator. The exported record group registry remains the
single source for overlay eligibility. No existing callers require a compatibility
wrapper. This module is additive because the prior contract only validates.

## Production proof

Composer-only source delivery remains landed-unproven after merge. After the
existing publication workflow integrates it, a separate authorized read-only
proof must inspect a naturally partner-published study and its publication
receipt. Confirm package hash, client/deal identity, canonical property IDs and
rendered Home/Purchases against the recorded inputs. This slice does not modify
publication or the app Client view and does not perform production calls.
