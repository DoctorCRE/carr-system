# Lead opportunity timing v1

The registered `lead-board` read with `workspace: "leads"` adds
`opportunity_timing` to each board lead and the selected detail. The outer
`lead-workspace.v1` contract stays compatible. Consumers pin the nested
`contract: "lead-opportunity-timing.v1"` and the producer Git revision.

`record_added_at` is the lead's `created_at` instant. `status` is `supported`
when at least one current exactly linked source event exists, otherwise
`unknown`. `events` is an empty array for unknown timing. Record creation,
update, search and stage dates never supply a source date.

Each event carries these fields.

| Field | Meaning |
| --- | --- |
| date | Canonical evidence calendar day, YYYY-MM-DD |
| date_type | `date`; retain the calendar day across time zones |
| date_basis | `enumeration`, `registry_update`, or `publication` |
| meaning | Exact event label, never a verified opening |
| source | `nppes` or `pecos` |
| source_url, record_key | Canonical source reference |
| revision | Canonical event ID as a decimal string |
| published_on | Source publication calendar day |
| observed_at | Canonical event `recorded_at` instant |
| uncertainty | Practice opening and real-estate decision remain unverified |

The read selects the latest revision of each source record before matching
its NPI to exactly one live canonical party, linked by the lead's party ID.
Duplicate live NPIs, absent NPIs and name/location similarities do not establish
support. Merged or deleted parties do not supply identity. Blocked or unavailable
feeds do not supply current support. All supported source records remain
separate, including differing dates. There is no inferred earliest opening date.
Florida formation evidence has no supported exact identity link here and is
excluded. No intake, source backfill or storage change occurs.

The board and detail share one PostgreSQL snapshot. A corrected revision replaces
its predecessor on the next read. Removing exact identity or feed availability
returns timing to unknown. Database failures propagate instead of serving cached
evidence. Existing stage evidence, history and unavailable search time remain intact.

## Runtime boundary

`parseLeadOpportunityTiming` in `lead-opportunity-timing.ts` returns the typed
`LeadOpportunityTiming` shape. Both workspace board and detail timing values
pass through it. The canonical registered fixture response uses the same parser.
The parser validates the exact contract version, calendar dates, timezone-bearing
instants and source/date-basis pairs. Supported timing requires nonempty valid
events; unknown timing requires an empty events array.

A malformed row refuses the entire read with `invalid_lead_opportunity_timing`.
It does not fall back to unknown, which means absent source support rather than
corrupt evidence. `test/lead-opportunity-timing.test.ts` replays the reviewer's
fault-injected board and detail rows and tests each malformed field separately.

## State transitions and tests

`test/lead-opportunity-timing-postgres.test.ts` exercises the registered read and
registered evidence importer on a canonical disposable PostgreSQL fixture.
It covers unknown to supported, supported to corrected, supported to ambiguous,
ambiguous to supported, supported to unavailable, and supported to unlinked.
The test compares source references and observation instants to independent SQL.

## Production proof

On a containing CARR release, compare an existing exactly linked source's event
date, basis and revision and the lead's created_at against independent canonical
reads. Observe an unlinked or ambiguous lead returning unknown. A dataset without
a supported source cannot prove a positive opportunity-date case. Do not backfill
it during this read-only proof. Production verification belongs to the coordinator
after merge and deployment; this builder does not call production.
