# Tour coordinate review 1.0.0

Producer `DoctorCRE/carr-system`. Migration `1180_tour_coordinate_review.sql`.
Consumer type `mcp-server/src/tour-coordinate-review-contract.ts`.

Authenticated `GET /api/tours/detail?tour_id=<uuid>` returns an additive
`coordinate_review` on each `routes[].stops[]` and on the projected latest
`stops[]`. Existing session, tenant and deal access apply. No new public endpoint
or database execute grant exists. The response uses `cache-control: no-store`.

`schema` is `tour-coordinate-review.v1`. `property_id` is the exact stop property.
`as_of` is the transaction snapshot time. `property_evidence` contains the existing
rights-bound, effective-dated property evidence rows, including site address and
building. Empty rows mean unknown evidence. Review-local `property_name` and
`property_address` contain rights-bound, effective reviewed display facts or
explicit nulls when unknown. Use these fields for coordinate review.

`status=unknown`, `reason=no_observed_candidates`, and `candidates=[]` mean no
authorized observed candidates. Otherwise `status=candidates` and `reason=null`.
All candidates observed and recorded by `as_of` appear in stable observed-time,
ID order. Each carries identity, role, precision, review state, source and rights.
Unusable, revoked, expired or superseded rights return `position=null`,
`access_notes=null`, `source.stable_locator=null`, and
`approval.status=rights_unavailable`. Metadata remains visible for internal review.

Approval is one of `ineligible_role`, `candidate_not_reviewed`, `unverified`,
`rights_unavailable`, or `verified_entrance`. Only the last includes a human
receipt. Centroids cannot qualify. Historical reads exclude future observations
and receipts. Entrance approval does not grant route or map promotion. Consumers
continue to use `tour-navigation-evidence.v1` for navigation eligibility.

Review uses the existing registered operation
`append-tour-entrance-verification-receipt`, through authenticated CARR tool
dispatch. It requires `idempotency_key`, `property_id`, `coordinate_candidate_id`,
`verified_at`, `evidence_reference`, `native_navigation_proof`, and `receipt_digest`.
Proof fields are `platform` (`google_maps` or `apple_maps`), `tested_at`,
`travel_mode` (`driving` or `walking`), and `evidence_digest`.
Authority, tenant and verifier are server-derived. The operation requires a
verified human partner, rejects caller authority fields and centroids, and retains
its existing idempotency envelope. Agents cannot manufacture this receipt.

After a write, refresh Tour detail. A consumer binds each pending read and write
to its authenticated session, Tour, route and property and discards late responses
after retirement. An unknown write outcome retries the exact key and payload.

Read-only production proof after a containing release:

```sh
python3 tools/prove-tour-navigation.py --merge-sha "$MERGE_SHA" --tour-id "$TOUR_ID" --route-id "$ROUTE_ID" --cookie-jar "$COOKIE_JAR" --coordinate-review
```

The existing accepted Tour must already have eligible navigation evidence. The
proof compares candidate identity, exact coordinates, source, rights and human
receipt fields against its independently sealed navigation evidence. Exit 0 means
all comparisons pass, 1 means an assertion failed on a containing release, and 78
means evidence is insufficient. It writes no production records.
