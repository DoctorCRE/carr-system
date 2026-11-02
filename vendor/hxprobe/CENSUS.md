# Count-only coverage census

`hxprobe census --immutable-copy <copy.hxd> <copied-Files-directory> <account-key>`
returns JSON with `totals` and `messages`. Each message entry uses its opaque
store-local key. Every value is an integer count. The output contains no subjects,
addresses, display names, body text, folder names, sidecar paths, or account keys.
Errors use the fixed text `census input rejected` and exit code 2.

The caller must supply a fresh, immutable profile copy in a private directory,
including its EFMData sidecars. Rename the copied store to `copy.hxd`. The command
rejects the live store filename, a neighboring HxStore.lock, corrupt blocks, and
unrecognized headers. It does not create a snapshot or contact a mail server.
Delete the copied store and sidecars after the census. Retain only count output.

The cohort contains decoded `IPM.Note*` messages with the selected typed account.
It includes unresolved folder references so their shapes remain visible. This is
an account census, not intake's admission verdict or proof of server-side folder
completeness. Rejected message layouts and unlinked child objects have global
counters because their account cannot safely be inferred.

`body_objects` counts linked body envelopes, including rejected revisions.
`body_absent` counts messages with no linked body envelope. `body_rejected` counts
linked envelopes the body parser rejects. Inline and sidecar counters count unique
accepted candidates. Sidecars are checked even when an inline candidate wins.
Resolution failures distinguish an unindexed path, an open failure, and gzip/read
failure. Body completeness uses the same resolution and preview fallback as the
normal export. The normal decoder's 32 MiB sidecar read limit still applies.

`recipient_objects` counts distinct linked child identities, including rejected
ones. `recipient_children` counts distinct children with at least one decoded
revision. `recipient_rejected` counts rejected linked revisions. The none, listed,
and conflict counters partition messages. Kind counters count exported recipients;
a conflicting child prevents export of that message's recipient list.

Missing subjects distinguish unset/conflicting slots from empty decoded values.
Unknown folder counters distinguish unset/conflicting current references,
uncatalogued targets, account mismatches, and unnamed catalog entries.

Do not infer missing local bytes from a preview alone. First rule out rejected
layouts, unlinked children, rejected body envelopes, and failed sidecar reads.
A zero absence count cannot establish full mailbox hydration on the mail server.

Synthetic regression coverage lives in `ops/hxstore-local-key-selftest.py`.
