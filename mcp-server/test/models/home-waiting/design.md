Size: one-session feature. No child agents permitted by brief.
Ground: Home sources are canonical actor-scoped rows; project_items builds versioned cards; withEnvelope provides tool_call audit. Parties and actors are global carr-internal identities. Foreign tenant context must refuse before resolving either.
Design A: add waiting fields to dispositions. Rejected because ownership and disposition versions would be coupled to an independent relationship.
Design B (chosen): separate actor/item waiting state plus durable command receipts. New read-home-inbox-v4 adds waiting_on and waiting_version; v1-v3 stay unchanged. Commands bind both current item_version and waiting base_version. SQL owns atomic compare-and-set, receipt and completion cleanup; typed adapter owns schema and envelope.
Throughput checkpoint:
- Blocking first steps: dependencies, statechart, failing public command test.
- Independent workstreams: none; one coupled migration/command/projection.
- Shared mutable state: lock actor/item waiting state; canonical completion trigger updates same row, so completion wins racing sets.
- Smallest safe decomposition: one worker with red/green vertical slices; independent preminted reviewer follows.
Test seams: registered waiting command and registered Home v4 read, disposable synthetic PostgreSQL. SQL permissions tested directly.
