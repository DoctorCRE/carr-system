# Vendor fit context source-contract assessment

Card: WR648.S2.J1

Status is partial. This change records a source-contract blocker. It does not register a read, expose a Doc tool, or mount a route.

## Observed source contracts

The inspected base is `d2c47c2aa2cc861d3fff5ea1b896a9142e52a1dd` in DoctorCRE/carr-system. These are source observations, not a production-data audit.

- `db/schema.sql`, table `public.deal`, defines city, segment, deal type and version. It has no typed service requirement set or deal state column.
- `public.deal_note` records attributed text with an ID and creation time. Its kinds are `note` and `next_step`. It does not identify which statements are current service requirements.
- `public.client` carries vertical, subtype and notes. Client preferences cannot silently become requirements for every deal.
- `public.vendor` defines category slug, free-text territory, free-text offers, loan programs and version. Category and trust do not prove an explicit service capability.
- `mcp-server/src/fact-observations.ts`, `CONTRACTS`, accepts base rent, landlord TI, term, rent schedule and free rent. It does not accept service requirements.
- `mcp-server/src/requirement-requests.js` manages doctrine checklist document requests. A requested document is not a requested vendor service.
- `public.record_flag` can store arbitrary JSON with source and observation time. `record-finding` does not define a service requirement kind, capability vocabulary, supersession rule or requirement comparison semantics. Generic storage is available; an established typed contract was not found in the inspected source.
- `get-deal-room` reads premises through `v_deal_room_premises`, including state. A deal can have multiple premises. A vendor office state is an office location, not evidence of service coverage.

`vendor-meetings.js` already supplies current contact checks through `vendorPeopleJoin` and excludes held, avoided and suppressed subjects. `vendor-relationship.ts` keeps trust separate from service capabilities. `relationship-network.ts` uses exact associations. `shared/vendor_intro_for_stage.py` ranks categories by stage, not by explicit deal requirements.

## Exact missing contract

Acceptance 1 and 2 require a canonical, versioned association from one exact deal and category to explicit service requirement keys. That association needs the recorded requirement value, source locator, current revision and a rule for withdrawal or supersession. It also needs a supported mapping from those keys to vendor capability evidence. Neither arbitrary note text nor arbitrary JSON establishes those semantics.

Territory comparison additionally needs an authoritative interpretation of recorded vendor territory and a rule for choosing the relevant deal premises when multiple premises disagree. An office address must not imply service coverage. Unknown or ambiguous territory must remain a gap.

The lightest next step is to identify an existing canonical requirement contract in the record layer, if one exists outside this source checkout. Otherwise a separately scoped contract slice can define these semantics using existing storage. This report does not claim that a new table or migration is necessary. This worker cannot query business records under its no-production restriction or define a new persisted requirement model under this brief.

## System design

The intended interface is `read-vendor-fit-context({deal, category, limit?})`, with an exact deal reference, category slug and integer limit from 1 through 20. The authenticated actor supplies authority. The fixed authenticated GET adapter and Doc tool use the same parser and registered implementation.

Two shapes were considered. A read that interprets arbitrary notes and offers would require unsupported semantic inference. A read over a versioned requirement contract can compare recorded evidence deterministically. The second is the intended end state, but its input contract is unresolved. No always-unknown placeholder endpoint is added.

- Architecture and data ownership. CARR owns requirements, capability evidence, source versions, authorization and projection. The app consumes the read. Software-factory owns none of the runtime data.
- Capacity at current load and ten times load. Proposed output is at most 20 vendors. A single snapshot query avoids per-vendor calls. Candidate query plan and scan bounds cannot be established until requirement joins are known. Database scan cost would break first.
- Load spread and scaling. Use the existing reader connection and request budget. No new worker or partitioning.
- Cache, invalidation and eviction. No cache. Each call reads current evidence in one statement snapshot.
- Availability. Read sequentially after authorization and validation. Any required source failure refuses with unavailable. Empty and unknown remain distinct. No stale fallback.
- Storage access and N+1. One bounded Postgres read is intended. No fan-out or per-vendor reads. The unresolved requirement source prevents specifying the final query.
- Consistency. Current requirement and vendor versions must share a statement snapshot. Refuse unavailable sources. Unknown evidence cannot pass.
- Async. Not applicable because this is a synchronous read without a producer or consumer.
- External calls and circuit breakers. No new external service call. One database attempt, no retries, with the existing request budget. Failure must produce a sanitized unavailable response. No separate alert mechanism is introduced.
- Rate limits. The existing authenticated request budget applies. The output cap is 20 and there is no loop calling an external API.
- Idempotency. Not applicable because no write or send is permitted.

These are design constraints, not implemented guarantees. No failure-test coverage is claimed.

## Statechart and acceptance status

The companion statechart is proposed behavior only. Every transition remains unimplemented and untested. Authorization and request validation precede any read. No state assigns a vendor or sends an introduction.

Acceptance 1 and 2 are blocked by the requirement and coverage contracts above. Acceptance 3 through 6 remain unimplemented. Existing contact checks are reusable evidence, not verification of this new feature.

The original absence remains at the proposed head. There is no completed vertical slice, runtime parser, proof fixture, Postgres suite, new CI duration measurement or production proof. No generated operation inventory changes are needed for a documentation-only partial result.

## Design and debt pass

Codebase design places parsing, authorization and comparison behind one registered read, shared by both adapters. The assessment rejects a second comparison implementation in the route.

Zero tech debt rejects a placeholder endpoint that would always report unknown, fabricated requirement keys, and a compatibility path for a feature with no existing callers. No runtime code is introduced.
