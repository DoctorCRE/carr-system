# Lead reachability v2

The registered `lead-board` workspace read owns Reachable and Research. The outer `lead-workspace.v1` remains unchanged. Consumers validate `mcp-server/contracts/lead-reachability.v2.json`. V2 replaces the research-only v1 schema because its classification enum and basis were constants and its object rejected additional fields.

Reachable requires all of the following in one snapshot.

- A successful contact on this exact lead, dated after lead creation, no later than the read clock, within 90 days. Manual call, text, inbound email, meeting or tour requires `connected=true`. The activity's lead relationship binds it to the current party. Any explicit conflicting party binding refuses it.
- For `mail_ingest` or `local_mail`, only inbound mail with `match=exact`, the same party ID, a nonempty `evidence_ref` and `automated=false` qualifies. Connected alone cannot bypass these machine-ingest checks. Outbound email never proves successful contact.
- A found, sourced, nonfuture email or office_phone finding with an explicit unexpired expiry and exact equality to the current party email or office phone. Email must have an email shape, phones at least seven digits. Bare contact values and identity verification are insufficient.
- Contact eligibility is true and no current discrepancy or proposed correction exists. Restrictions, research completeness and evidence conflicts remain separately visible. An expired or missing route or unsuccessful contact yields Research, never a bad-lead judgment.

`successful_contact` exposes activity ID, date, kind and source. `usable_route` identifies the finding and route kind. `source_inputs` contains the current party routes, lead creation date and at most 500 recent bound activities. Existing `evidence` retains finding provenance. The SHA-256 revision covers `[base_version, contact_eligible, evidence, source_inputs]`. Refresh can change classification through expiry without changing that revision.

## System design

The existing read module queries evidence and classifies it. It uses the existing reader-granted workspace detail and correspondence views. The detail view exposes email and office phone. Cell-only routes remain Research because no current cell value is exposed by that view; no base-table grants are widened. No new endpoint or migration. One PostgreSQL statement preserves board membership, ordering and board/detail equality. A correlated lookup reads up to 501 recent activities per lead and refuses above 500, matching the findings bound. At 10x volume the activity lookup and JSON payload grow first. No application N+1, external call, cache, queue, write, idempotency key or retry is introduced. Database failure refuses the snapshot. This uses the existing registered authentication and reader grants.

## Model and tests

The model abstracts a complete refreshed snapshot into current route, recent successful contact, eligibility and conflict. It does not model SQL parsing, timestamps or authentication. Those are exercised by registered tests. Run TLC with `JAVA=/opt/homebrew/opt/openjdk@21/bin/java`, then `$JAVA -cp /tmp/wr591-j3-tla2tools.jar tlc2.TLC -config Reachability.cfg Reachability.tla` in a scratch copy.

| Transition | Test |
| --- | --- |
| Successful contact plus current route | successful exact-lead contact and current matching route make the lead reachable |
| Unanswered attempt, expiry or changed route | registered board preserves identity, owns classification, and exposes exact source evidence under carr_reader |
| Suppression, restriction, conflict and renewal | registered board preserves identity, owns classification, and exposes exact source evidence under carr_reader |
| Future, stale or pre-lead contact | registered board preserves identity, owns classification, and exposes exact source evidence under carr_reader |
| Exact inbound reply and invalid machine bindings | registered board preserves identity, owns classification, and exposes exact source evidence under carr_reader |
| Source refresh and removal | source revision changes with source facts and preserves future evidence as unknown |

## Production proof

On a containing release, the coordinator reads the registered board and compares exact lead/party, activity/date, route finding and value, restrictions and revision with independent source reads. No qualifying positive record yields insufficient evidence, exit 78. Do not manufacture production findings. This worker performs no production calls.
