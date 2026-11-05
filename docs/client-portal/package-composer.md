# Market study package composition

`composeMarketStudyPackage(research, reviewed, target, membership)` in
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

`membership` is required and contains exactly these fields:

- `research` is `{ deal_id, client_id }`. Both identities must match `target`.
- `assets` is an array of `{ deal_id, client_id, data: { sha256, name, type } }`
  envelopes. Every schema-declared Asset in the composed package must resolve to
  an envelope with the same hash, filename, MIME type, deal and client.

The caller obtains research scope and asset ownership from its authenticated
record workflow. Copying the target identity onto arbitrary research or assets
is not ownership evidence. The composer checks assertions but cannot authenticate
them. Membership evidence covers reports, documents, photos, logos and local
basemap assets, including assets nested in reviewed groups. An absent exact
asset match refuses composition. A foreign ownership assertion for a referenced
asset refuses composition even if another envelope claims matching ownership.
Envelope identity fields never enter the output package.

## Results and failures

Success returns `{ ok: true, package, errors: [] }`. Failure returns
`{ ok: false, stage, errors: [{ path, message, reason? }] }` without a package.
Stages are `research`, `target`, `membership`, `reviewed`, and `composed`, checked
in that order. Asset resolution and public geometry checks run in `composed`
after full-schema validation. Paths are JSON Pointers relative to the failed
stage's input. A composed path refers to the candidate output package.
Error order is stable for identical JSON inputs. The function accepts JSON data,
as does the existing validator. Shape errors can omit `reason`.

| Stage | Reason | Meaning |
| --- | --- | --- |
| `membership` | `membership_required` | Membership, research identity, or the asset array is absent or has the wrong container shape. |
| `membership` | `research_identity_mismatch` | Research deal or client identity differs from the target or is absent. |
| `membership` | `invalid_asset_envelope` | An asset envelope or its `data` is not an object. |
| `composed` | `asset_membership_missing` | A referenced asset has no exact hash, name and type match in the evidence. |
| `composed` | `asset_identity_mismatch` | At least one matching envelope asserts a different deal or client, or omits an identity. |
| `composed` | `invalid_public_geometry` | GeoJSON has an unsupported structure or nonnumeric coordinate value. |

Research validates in partial mode before any replacement. Present record groups
must therefore already be valid. Reviewed groups replace those groups in full.
The result validates in full mode and rejects duplicate property IDs and an
ownership default outside the reviewed set. Unknown fields in closed schema
objects fail validation, including private fields on properties or client data.

GeoJSON has open metadata in the schema. The composer strips that metadata
instead of rejecting it. FeatureCollection and GeometryCollection retain only
`type` and their child collections. Features retain only `type`, `geometry` and
empty `properties`. Geometries retain only `type` and numeric `coordinates`.
The projection drops every extra GeoJSON field at every nesting level, including
private keys, feature IDs, bounding boxes and custom display metadata.
Reviewed public property notes and schema-defined ownership assumptions remain
allowed. The caller is responsible for reviewing free text for private content.

## Executable synthetic example

The following JavaScript runs from the repository root with Node's
`--input-type=module` option after installing the MCP dependencies.
It prints the successful result whose package equals the complete fixture.
The synthetic fixture supplies all identities and asset evidence in this example
only. A production caller must obtain authenticated evidence as described above.

```js
const { readFileSync } = await import('node:fs');
const { composeMarketStudyPackage } = await import('./mcp-server/src/market-study-composer.js');
const { RECORD_LAYER_GROUPS } = await import('./mcp-server/src/market-study-package.js');
const fixture = JSON.parse(readFileSync('client-portal/fixtures/example-health.market-study.json', 'utf8'));
const target = { deal_id: 'synthetic-deal', client_id: 'synthetic-client' };
const envelope = data => ({ ...target, data: structuredClone(data) });
const research = structuredClone(fixture);
for (const group of RECORD_LAYER_GROUPS) delete research[group];
const reviewed = {
	client: envelope(fixture.client),
	prepared: envelope(fixture.prepared),
	properties: fixture.properties.map(envelope),
};
function fixtureAssets(value) {
	if (!value || typeof value !== 'object') return [];
	if (value.sha256 && value.name && value.type) return [envelope(value)];
	return Object.values(value).flatMap(fixtureAssets);
}
const membership = { research: { ...target }, assets: fixtureAssets(fixture) };
const result = composeMarketStudyPackage(research, reviewed, target, membership);
console.log(JSON.stringify(result));
```

`node --test --test-name-pattern='documented composer call' mcp-server/test/market-study-composer.test.mjs`
executes this exact example and compares the result with the complete fixture.

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
- Consistency. Refuse invalid research, mismatched identities or unresolved
  assets. Never repair invalid research through an overlay. Tests cover these
  refusals.
- Caching. No request cache or invalidation. Schema validators are reused by the
  existing validator. Each call independently reads the supplied JSON values.
- Storage and N+1. Not applicable. No database, filesystem or object-store access.
- Async, queues, circuit breakers, API rate limits and write idempotency. Not
  applicable. No external call, queue, retry, fan-out, write or send exists.
  Iteration is linear over caller-supplied JSON and property envelopes.

## Design and debt pass

Direct unscoped groups would produce a smaller interface but cannot distinguish
mixed-deal properties because the published schema has no identity fields.
Research identity and asset envelopes keep the checks in one module without
changing that schema or introducing a second validator. The exported record group registry remains the
single source for overlay eligibility. No existing callers require a compatibility
wrapper. This module is additive because the prior contract only validates.

## Production proof

Composer-only source delivery remains landed-unproven after merge. After the
existing publication workflow integrates it, a separate authorized read-only
proof must inspect a naturally partner-published study and its publication
receipt. Confirm package hash, client/deal identity, canonical property IDs and
rendered Home/Purchases against the recorded inputs. This slice does not modify
publication or the app Client view and does not perform production calls.
