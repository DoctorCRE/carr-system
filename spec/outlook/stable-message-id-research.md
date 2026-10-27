# Stable message identity research for WR-000502

Status, 2026-10-26 10:08 CDT: the `00ca` local object key, scoped to one store
item in one `HxStore.hxd`, is safe to RECORD as the email identity for slice B2,
but not yet safe to RELY on for undo, deduplication or proposal links. It stayed
the same through edits, flags, an attachment, a reply, an Outlook restart and
five days of sync. It differed between distinct messages with identical sender,
subject and preview, and between a reply and its parent. Eight moves made
outside this study kept their keys, and no key was ever reused. Controlled
moves, deletes and a store rebuild are still untested: the request to control
Outlook was denied a third time, and New Outlook's AppleScript cannot reach
account mail (see [the controlled-test attempt](#controlled-move-delete-and-rebuild-attempt-2026-10-26)
and [what is still open](#still-open)). One natural case may be a key change on
move or delete and remains unexplained.
Intake records the key on every email record that has one. Audit
[#2196](https://github.com/DoctorCRE/carr-system/pull/2196) slice B2 may record
it but must not rely on it for undo, deduplication or proposal links until those
cases pass. Send keeps its existing behavior.

## Candidate comparison

The Rust command accepts two caller-selected coordinates in immutable store copies.
Each coordinate is `block_file_offset:object_payload_offset`, using decimal byte
offsets. Object offsets exclude the ten-byte serialization envelope.

```text
hxprobe identity-compare --immutable-copies before.hxd 64:10 after.hxd 64:10
```

The command uses the existing checked block parser. It accepts a selected block
only if the verified-block traversal accepts it. Unrelated rejected blocks do
not invalidate a selected verified block. Unsupported headers, missing selected
blocks, invalid coordinates, mismatched classes, and malformed candidates fail
with exit code 2 and empty stdout. Stderr contains one fixed refusal message.
The command reads no sidecars and writes no inputs. File identity, size, mtime,
and ctime must remain unchanged during each read. The flag asserts caller custody
and immutability; it does not establish that Outlook was closed during capture.

`hxstore-identity-comparison/v1` emits class, selector descriptions, and equality
booleans. It emits no paths, raw keys, metadata tails, marker text, or mail content.
The interpretation is `candidates_only_no_logical_identity_claim`.

The shared [`folder::message_key`](../../vendor/hxprobe/src/folder.rs) selector
requires class `00ca` or `00bf`, an eight-byte zero class-signature suffix, and
equal nonzero little-endian u64 values at object offsets 10, 30, and 38.
For class `00bf`, the command also requires the bounded `02c8` envelope.
[`folder::metadata_tail`](../../vendor/hxprobe/src/folder.rs) accepts a 51-byte
span starting 59 bytes before that object's end only when its prefix is
`00 09 00 2e 00`. The last eight bytes are outside the compared span.
`metadata_tail_equal` is null unless both selected metadata objects have that
framing. Null does not mean equal, different, or absent from the whole store.
A `00ca` header needs no individual envelope because the existing reader also
recognizes expanded objects inside transaction wrappers.

A matching structural signature does not establish the selected object's account,
currentness, relationship to a nearby marker, or message ownership. The caller
must establish those facts before interpreting an equality as lifecycle evidence.

## Copied-store observation

The following observation ran on Studio at 20:39 CDT on 2026-10-25. No native
mailbox changes or new captures ran. The original private campaign is
`outlook-decode-study-2026-10-21-01a15e6e`, under `~/carr-local/`.
Its campaign receipt records TEST-account draft, attachment, flag, and unsent
reply operations. It also records substantial shared-store background churn and
incomplete reply-marker readback. Those qualifications remain binding.

The seven listed stores each matched the SHA-256 in their existing snapshot
receipt. Their device, inode, size, mtime, and ctime were unchanged during that
verification. Each file is 29,785,088 bytes. No store bytes or raw identity values
are included here.

A temporary Rust investigation used the maintained block parser and searched only
for the known synthetic UTF-16 marker prefix. Repeated-key `00ca` candidates
preceding the synthetic draft marker within 4096 bytes all had one common key
in each snapshot. That nearest-header association is provisional. It does not
prove marker ownership or that a cached revision is current.

The release-built CLI compared `003-draft` against each of the six later
selected headers below. All six returned `local_key_equal=true` and
`metadata_tail_equal=null`, with class `00ca` and the candidate-only
interpretation. Coordinates are private campaign metadata, not message identifiers.

| Snapshot | Selected coordinate | SHA-256 of copied store |
| --- | --- | --- |
| `003-draft` | `2757120:764` | `40ee046c2ae5b9023134b9fcfd0326d377e1e8643ded78bf075d5f9bed77ffd5` |
| `007-flag` | `2881024:764` | `4576158429275425f9909d43a579692919a63a437881975e9e99f0cdddc0ae2c` |
| `008-attachment` | `3774464:764` | `0a7e17249b61a980a371b893b772417293fca1985b8bd0392661f5c72af5f4d6` |
| `009-flag` | `3774464:764` | `ab8cb44f710813aa0ac0036d0a059ed8634430e23a9919dcaae17c44a6171158` |
| `010-reply` | `3774464:764` | `63d78246ffc427be05bb6462e7ea70171549f534ad57d7ea749c2b7ddac2078e` |
| `011-control` | `3774464:764` | `d024621a0f38ce50086a2456dea94b302528c8d036a070513df3b99a4f45ad03` |
| `012-control` | `3774464:764` | `16f5f1bd39f17284651e48a6771d80f28e159c166044540c06bcd66e424aa0eb` |

These observations support continued investigation of the repeated local key.
They do not meet WR-000502 AC-1. No two independently revised messages were
established, no identical-content distinct-message comparison was established,
and no metadata-tail lifecycle comparison was established. No second message
identity is inferred from a changed physical location or the reply operation.

## Controlled TEST-account comparison, 2026-10-26

This study ran on Studio between 06:50 and 07:10 CDT on 2026-10-26. It reused
the twelve private copies of campaign `outlook-decode-study-2026-10-21-01a15e6e`
(`001-control` through `012-control`). Their TEST-account operations were a new
draft, a calendar event, read-state preparation, two flag attempts, an attached
file and an unsent reply. No mail was sent, and no new mailbox change was made.

A fresh capture was not possible. `com.carr.outlook-research-copy` exited with
EPERM (errno 1) at 06:51, so its helper lacks Full Disk Access. The request to
drive Outlook for a move and a restart was denied. Both are human-only steps.

A temporary, uncommitted hxprobe command wrote the verified blocks that
contained the TEST address into private scratch files. For each repeated-key
`00ca` header, the header was bounded by the next header or 4096 bytes, and the
IPM.Note anchor inside that span was read. Five TEST-account store items were
found. The labels below are local to this study. Raw keys stay private.

| Item | What it is | Snapshots present | Observed revisions | Key across snapshots |
| --- | --- | --- | --- | --- |
| D | Synthetic draft `HX-study-...-draft-001` | 002 to 012 | Last-modified tick changed at 003, 007 and 008; new header variants at 003, 005 and 008 | One key, unchanged |
| W1 | Received vendor welcome email | 001 to 012 | Header fields at +104 and +312 changed at 008 and 009 (flag); replied to at 010 | One key, unchanged |
| W2 | Second welcome email, same sender, subject and preview as W1 | 001 to 012 | None observed | One key, unchanged, different from W1 |
| R | Unsent reply to W1, carrying W1's Message-ID string | 010 to 011 | None observed | One key, different from W1 and D |
| X | Unchanged TEST control item | 002 to 012 | None | One key, unchanged |

W1 and W2 are distinct messages: their InternetMessageId values and send times
differ, while sender, subject and preview are identical. The existing copy join
(sender, normalized subject and opening, within a day) merges them into one
row. That is the B2 duplicate-template case. R shows why a Message-ID string
cannot serve: the reply's record contains its parent's ID.

Whole-store consistency was measured on the shared copies with hashed field
values only. No content was printed or kept.

| Check, decoder-level | `001-control` | `012-control` |
| --- | --- | --- |
| IPM.Note anchors with a key | 3,275 of 4,232 | 3,309 of 4,297 |
| Distinct keys | 779 | 786 |
| Keys whose revisions disagree on Message-ID | 0 | 0 |
| Keys with the same Message-ID set in both snapshots | 777 of 777 | |
| Message-IDs whose key set changed between the snapshots | 2 of 656 | |
| Content-identical groups spanning more than one key | 16 | 16 |

### The two Message-IDs whose key set changed

Neither case is an existing item changing its key.

- One Message-ID was held by three keys in `001-control` and two in
  `012-control`. The missing key had no anchors left in `012-control`, so that
  item left the cached store. Its key did not reappear on any other item.
- One Message-ID gained a key. The new key did not exist in `001-control`, and
  its rows carry only that one Message-ID. A new store item arrived with an
  existing Message-ID, such as a second copy of the same email. The original
  key kept its rows.

Neither item belongs to the TEST account, so their content was not examined.

### Key space

The keys form one numeric space across both accounts in the shared store. TEST
keys sit between work-account keys, with work-account neighbours on both sides.
Every key new in `012-control` was larger than every key in `001-control`. Two
keys left the store in that hour and neither was reused. This fits a store-wide
increasing counter, but one hour of copies cannot show that a deleted item's key
is never reused.

Sender and subject extraction disagree inside 411 keys. The TEST items show
the cause: quoted headers in replies feed the heuristic field map. Those fields
are not identity evidence either way. Ten of the 16 content-identical groups
share a Message-ID across keys. That fits one email held as two store items, such
as a sent copy and a received copy, but this study did not establish it. The key
identifies a store item, not one logical email across folders or accounts.

### Decoder and intake change

`hxprobe db` now writes `messages.local_keys`: the comma-separated keys of every
message object merged into the row, and NULL when none was linked. On
`012-control`, the installer-equivalent build linked 1,825 of 2,133 rows; 283
of them held more than one key. Through the intake loader, 1,465 of 1,772 intake
rows carried at least one key and 284 carried two or more. The TEST welcome row
carried both W1 and W2, which exposes the merge for B2 to split.

The intake records the keys per intake ID in its private state as `mail_keys`.
They expire with the mail they describe. Mail the intake finished before this
change ships is skipped on later runs, so it gets no keys. A decoder without the column still
runs, and its mail simply records no keys. Merging and idempotency keys are
unchanged; B2 decides how to split rows.

### Fallback when no key is linked

Rows without a key keep the existing intake ID: a 20-hex hash of `copy_key`
(sender, normalized subject, opening) plus the first send time. That composite
was observed to merge W1 and W2, so B2 must not treat it as unique. It remains
the fallback for the rows the key does not reach.

## Five-day comparison, 2026-10-21 to 2026-10-26

At 09:19 CDT on 2026-10-26 the research snapshot helper succeeded after Joe's
Full Disk Access grant, writing `snap-20261026T141948-152240000Z`. The request
to control Outlook for scripted moves and deletes was denied again, so no
controlled TEST-account change ran. This comparison instead uses what happened
naturally between `012-control` (2026-10-21) and that snapshot. Studio rebooted
at 07:28 CDT on 2026-10-25, and Outlook has run since 07:29 CDT that day, so the
interval covers a restart and five days of sync. The decoder build includes the
WR-000469 folder catalog. A temporary, uncommitted command emitted hashed
fields, folder IDs and account IDs. Only counts and the standard folder names
below were read.

| Check | Result |
| --- | --- |
| TEST draft D, welcome W1 and welcome W2 | Same key, same Message-ID and same folder (Drafts, Inbox, Inbox) after the restart |
| TEST reply R and control X | Left the store; their keys did not reappear |
| Surviving keys with Message-IDs in both copies | 438 of 438 kept the same Message-ID set |
| Keys whose folder changed | 13 of 435; all 13 kept their Message-ID set |
| Moves to a disjoint folder | 8, all from the work Inbox to custom folders; key unchanged |
| Keys that left the store | 348 |
| Keys that arrived | 63, all above every 2026-10-21 key, so none reused |
| Accounts | 2; no key linked to folders of both; the TEST and work key ranges interleave |

Two Message-IDs lost all of their 2026-10-21 keys and appeared under new keys:

- **Not a key change.** One is held by new messages with a different subject
  and later send times. It is a thread reference, not the same email.
- **Unexplained.** The other matches sender, subject and send time. Its old key
  was in the work Inbox. Its two new keys are in Sent Items and in a custom
  folder that also received one of the eight key-preserving moves. A sent copy
  and a filed copy fit, but this was not established. Treat it as a possible key
  change on move or delete.

These moves and deletes were made outside this study, so their exact operation
is unknown. The result is consistent with one increasing, store-wide counter
that survives restart and resync.

## Controlled move, delete and rebuild attempt, 2026-10-26

This run started at 10:07 CDT on Studio to make one controlled TEST-account
move, then a delete and restore, each between two copies with an unchanged
control. None of the three changes ran.

- **AppleScript cannot reach account mail.** Microsoft Outlook 16.114.1 in New
  Outlook mode answers AppleScript, but `every account` fails with error -1728,
  `count every message` returns 0, and the ten scriptable mail folders are the
  empty legacy "On My Computer" set. Make and send still work, as the lane notes
  record, but no script can select, move or delete a TEST message.
- **UI control was denied.** The request to control Microsoft Outlook for a
  TEST-account move, delete and restore was denied (`user_denied`), the third
  denial on 2026-10-26. No other route was tried: a browser or second automation
  tool would work around that denial.
- **Rebuild was not attempted.** Removing and re-adding the TEST account needs
  Joe's sign-in. Resetting one account is a UI action, so it shares the denial.
  No rebuild route that spares carr.us and needs no sign-in was found.

A natural comparison ran instead, between the 09:19 copy
(`snap-20261026T141948-152240000Z`) and a new 10:07 copy
(`snap-20261026T150715-001993000Z`), both taken by the research helper with
Outlook running. Both were decoded by `hxprobe db` from this branch into private
temporary databases, which were deleted after the counts below. Only hashed
Message-IDs, counts and standard folder names were read.

| Check, 48 minutes of normal use | Result |
| --- | --- |
| Keys in the 09:19 and 10:07 copies | 501 and 498; 493 common |
| Keys that left the store | 8 |
| Keys that arrived | 5, all above every 09:19 key; none reused |
| Message-IDs present in both copies with no key in common | 0 |
| Keys whose decoded row changed folder set | 3; all gained or lost a custom folder beside the Inbox |

This adds nothing about controlled moves or deletes. The row-level Message-ID
and folder fields come from merged `hxprobe db` rows, which can join store items
and quoted headers, so 18 common keys showed a changed Message-ID set at row
level. Fifteen were subset changes. The other three were disjoint, and all
three sat in rows that merged several keys. No Message-ID moved to a new key.
The five-day study's per-object method remains the stronger evidence.

### Verdict for slice B2

The key is the right field to record and the best local email identity found.
It does not change on edits, flags, attachments, replies, restarts or sync. A
move or delete may change it; that is unproven either way. A rebuild or account
re-add may renumber every key; that is untested. B2 should store the key beside
the existing intake ID, use it to split rows such as W1 and W2, and keep undo,
deduplication and proposal links on the existing behavior until a controlled
move and delete pass.

## B2 decoder split

The decoder partitions all three message joins by the linked local-key set.
Distinct keyed items remain separate even when sender, send time, subject and
opening match. Revisions with the same key keep the existing sender/time,
copy-window and preview-to-full joins. Unkeyed rows retain those joins in a
separate partition, so an unkeyed revision cannot bridge two keyed items.
A keyed preview therefore cannot borrow an unkeyed full body.

The exported copy key and send time keep their existing meanings. Intake still
uses its existing content-based deduplication, intake IDs and proposal links;
two exported rows do not promise two activities. No lifecycle identity claim
or undo contract changes. The real-decoder regression suite uses generated
synthetic bytes reproducing the TEST-account W1/W2 collision described above.
It establishes decoder behavior, not controlled move, delete or rebuild safety.

## Still open

The snapshot helper works as of 2026-10-26. Each item below needs a capture
before and after one TEST-account change. AppleScript cannot make those
changes in New Outlook, so they need Joe to approve UI control of Outlook, or
to make each change himself between two captures. That approval was denied
three times on 2026-10-26.

- A controlled move between folders, to settle the unexplained case above.
- Deleting a message, then checking that no later item takes its key.
- A store rebuild or account removal, which may renumber every key.
- Two drafts with identical text and different recipients, as the original plan
  specified. W1 and W2 cover identical content with the same recipient.

## Original remaining experiment, superseded

The current work-request card requires at least two revised and two distinct
messages. Establish TEST-account ownership and independent current revision
selection, then capture A0, A1, A2 and B0, B1, B2 plus an unchanged control.
Use identical content and different recipients in A and B so content heuristics
cannot pass by construction. Compare the selected keys within each message and
between messages, and compare metadata tails separately. Preserve each private
immutable snapshot and its receipt before the next TEST-account change.

Only that evidence can support a lifecycle claim. Synthetic CLI tests establish
extraction, comparison, malformed-input refusal, and output privacy. They cannot
establish Outlook semantics or satisfy B2's cross-path deduplication tests.
Microsoft Graph is forbidden. The live intake checkout and installed helpers
remain untouched. Parser rollout still requires the lane's merged-source,
installer, Full Disk Access, and scheduled-run verification procedure.
