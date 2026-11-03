# Home Inbox completion time

The app can group proven completion instants into Done today in America/Chicago. Unknown times remain null. The server returns UTC instants.

## Design

The data shape is a Home Inbox v4 item with nullable `completed_at`. `read-home-inbox-v4` is the public test seam. Existing v3 source selection, ordering, limits, disposition versions and settlement rules remain authoritative. SQL adds the source evidence to the bounded v3 snapshot in one query, then the existing JavaScript settlement projection applies.

Two interfaces were considered before implementation. Adding `completed_at` to v3 would avoid a new verb but change deployed v3 output. A successor v4 read preserves exact v1 through v3 output and gives the app an explicit adoption point. The successor was selected. No parallel design agents ran because this worker's brief forbids children.

Ordinary actions use the exact completion event for the same action, subject and tenant. Supported writers are complete-one-action, complete-action and complete-deal-item. A newer reopen-one-action supersedes an older completion, ordered by the event mutation sequence. The timestamp is the event's occurred_at. Call actions use completed_at only while canonically done. Replies use the observed send_result.sent_at only while sent. Invalid, nonfinite or absent evidence returns null. Other source types return null. Notification severity does not override canonical completion evidence.

## Statechart and tests

[chart.mmd](chart.mmd) was written before implementation. The PostgreSQL test runs the chart's open, legacy done, proven done, later notification, reopened, unproven recompletion and proven recompletion transitions through the registered v4 verb. It also exercises call completion, delayed Sent Items reporting, invalid legacy evidence, actor isolation, reader denial, and unchanged old snapshots. UTC instants on both sides of Chicago midnight are returned without server-side date grouping.

The statechart skill generated a TLA+ skeleton in /tmp. Mechanical model checking could not run because TLC was unavailable. The chart is a design artifact; the executable PostgreSQL transitions are the verification.

## Throughput checkpoint

- Blocking first steps. Read the brief, inspect the source and contracts, attach read-only memory, and prepare an isolated clone.
- Independent workstreams. One coupled projection and test fixture belong to one builder.
- Shared mutable state. The clone and branch belong to this job. Overlapping open PR files serialize PR creation.
- Smallest safe decomposition. One migration adds evidence to the existing snapshot; no domain writes or new state storage.

## Design and debt pass

Codebase-design keeps completion evidence behind the existing inbox read seam. Primary-key source joins avoid scanning source tables once per item.

Zero-tech-debt reuses the existing v3 projection and settlement implementation. Existing versions have live clients and remain required compatibility contracts. No generic completion service or parallel state column was introduced.

## Blast radius

The safety fact is that v4 adds completion evidence without changing old reads, disposition versions or settlement behavior. The PostgreSQL test compares v1, v2 and v3 snapshots before and after the migration, then compares v4 with v3 after removing only the new field and schema version. It runs with carr_writer and denies carr_reader.

The new read depends on existing source retention and snapshot caps. It does not recover completed items already excluded from v3. Unrecorded historical completion or reopening cannot be reconstructed; unsupported or unproven evidence remains unknown. No app behavior or production deployment is claimed by this builder.
