# HxStore atlas

Generated from [hxstore-atlas.json](hxstore-atlas.json).
Regenerate with `python3 ops/hxstore-atlas.py render spec/outlook/hxstore-atlas.json`.

Catalog of pinned decoder contracts. Research observations below do not establish empirical mailbox coverage.

## Stable message identity research, WR-000502

Status, 2026-10-26: the `00ca` local object key is a CANDIDATE stable email ID, scoped to one
store item in one store. See the comparisons below. It survived a reboot, an Outlook restart, five days
of sync and eight moves made outside this study, and no key was reused. One possible key change is
unexplained. Controlled moves, deletes and a store rebuild are untested. Audit PR
[#2196](https://github.com/DoctorCRE/carr-system/pull/2196), slice B2, may record the key but must
not rely on it yet. The sections that follow preserve the earlier, blocked experiments.
The observations below were made on Studio on 2026-10-25 at 08:03 CDT.
Outlook's AppleScript `version` returned `16.114.1`.

### First experiment, preserved from PR #2229

Paths below are relative to
`/Users/booko/Library/Group Containers/UBF8T346G9.Office/Outlook/Outlook 15 Profiles/`.
Only file-system metadata was obtained. Directory sizes are not content sizes.

| Path | Observation | File bytes |
| --- | --- | --- |
| `Main Identity/` | Directory exists. Listing denied. | Not applicable |
| `Main Identity/HxStore.hxd` | File exists. Header-only read denied. | 29,392,384 |
| `Main Identity/Files/` | Directory exists. | Not applicable |
| `Main Identity/Files/S0/` | Directory exists. Listing not obtained. | Not applicable |
| `Main Identity/HxStore.hxi` | Exact-path stat returned ENOENT. | Not applicable |
| `Main Identity/Outlook.sqlite` | Exact-path stat returned ENOENT. | Not applicable |

Account separation is NOT VERIFIED. These observations do not establish that this
is the only profile or that either account owns a separate store file.
`ls` of the Outlook directory returned `Operation not permitted`. A second runtime's
`fs.readdir` returned `EPERM`. Opening only the first 64 store bytes also returned
`EPERM`. The System Events directory query returned no usable inventory.
The human-only dependency is macOS permission for the job host to access Outlook's
protected directory. No protected bytes were read, and no store copy was created.

The existing [copy helper](../../tools/outlook-intake-copy.c) copies `HxStore.hxd`
and every account's `Files/S0/*/EFMData/*.dat`. It was inspected but not executed.
That whole-profile operation cannot satisfy this job's account-isolation rule.
The harness allows caller-held shared snapshots, but Joe's narrower instruction
for WR-000502 controls this experiment. Work-account files must not be copied.

### Candidate evidence table

A0 means draft A saved, A1 and A2 mean its two edits, and B0 means a separate draft
with identical text. U is a separately captured unchanged control. Every cell says
not captured because the required account-isolation check could not complete.
No draft was created or edited, no mail was sent, and no mailbox values were decoded.

| Field or selector | A0 | A1 | A2 | B0 | U | Stability evidence and verdict |
| --- | --- | --- | --- | --- | --- | --- |
| Local message key, classes `00ca` and `00bf`, object offsets 10, 30, 38 | Not captured | Not captured | Not captured | Not captured | Not captured | `folder::message_key` checks equal nonzero u64 values. Candidate only. Revision stability and distinct-message uniqueness untested. |
| 51-byte metadata tail before final folder key, class `00bf` | Not captured | Not captured | Not captured | Not captured | Not captured | `folder::Index::observe` checks the five-byte prefix and calls the tail an immutable message ID in a comment. It does not expose or prove that identity. Candidate only. |
| `ImmId` string run | Not captured | Not captured | Not captured | Not captured | Not captured | `record::is_opaque_blob` names it alongside other opaque IDs. No independent `ImmId` selector is established. |
| Current `ConversationId` selector | Not captured | Not captured | Not captured | Not captured | Not captured | `schema::build_bounded` groups long hex and hyphen runs together, including possible revision and conversation IDs. Shape cannot establish message identity. |
| Current `InternetMessageId` selector | Not captured | Not captured | Not captured | Not captured | Not captured | `main::cmd_db` excludes it from identity because pinned recovery observations show conversation collisions and revision splits. Not a verified logical selector. |
| Physical `record_id` | Not captured | Not captured | Not captured | Not captured | Not captured | The [harness contract](hxstore-harness.json) defines snapshot-scoped block and payload coordinates. It makes no logical identity guarantee. |
| Sender plus earliest timestamp, or content `copy_key` | Not captured | Not captured | Not captured | Not captured | Not captured | Existing recovery heuristics have no cross-revision uniqueness contract. They cannot substitute for independently established identity. |

The inspected code is revision `cfba29a073eff9d99b3bc601a35a5643e9d52b1f`.
Source anchors are [folder.rs](../../vendor/hxprobe/src/folder.rs), functions
`message_key` and `Index::observe`; [schema.rs](../../vendor/hxprobe/src/schema.rs),
`build_bounded`; [record.rs](../../vendor/hxprobe/src/record.rs), `is_opaque_blob`;
and [main.rs](../../vendor/hxprobe/src/main.rs), `cmd_db`.
The [pinned upstream SPEC section 4.7](https://github.com/ukd1/hxstore-reverse-engineering/blob/191b54e42757687806202f8f696e9066f0514e11/SPEC.md#47-record-identity-and-deduplication-verified)
reports recovery limitations. It does not rule out a stable local ID.

### Original experiment plan

The falsifiable check is equality for A0, A1, and A2, inequality for B0 despite
identical text, and an unchanged control. Each transition needs its own private
immutable snapshot and comparison before the next change. This can support the
observed lifecycle operations, not universal stability across every operation.
Snapshots belong under `~/carr-system/scratchpad/outlook-snapshots/`, outside git.
If the store is shared, the work must remain within Joe's structure-and-field-
metadata restriction without copying work-account files. That constraint must be
resolved before applying the harness's shared-copy workflow.

The next source is the test account's Outlook data, decoded with the existing
Rust hxprobe reader. Joe's direction is to use Rust and the test account to decode
all Outlook data. The employer forbids Microsoft Graph, so it is not an option
or fallback for this work.
First establish account isolation and obtain the required macOS access. Then
capture the test-account snapshots above and compare the candidate identity
fields across each transition. Extend the Rust decoder only from observed
format evidence. Preserve the distinction between a message identity, a revision
identifier, and a conversation identifier until the experiment proves each role.
No hxprobe identity decoder or synthetic identity claim was added in that experiment.

### Copied-store candidate comparison

The follow-up [research record](stable-message-id-research.md) records a Rust
metadata study of existing private TEST-account campaign copies and the
`identity-compare --immutable-copies` command. It compares caller-selected
repeated local keys and framed metadata tails without emitting their values.
The repeated key agreed near one synthetic draft marker across seven snapshots.
Cached revisions and provisional marker association prevent a stable-ID claim.
WR-000502 AC-1 still needs two independently revised, distinct messages.
The earlier matrix remains the uncaptured first experiment, not this follow-up.
No identity field is promoted to proven, and intake/send behavior is unchanged.

### Controlled TEST-account comparison, 2026-10-26

[The research record](stable-message-id-research.md#controlled-test-account-comparison-2026-10-26)
compares the repeated `00ca` key across all twelve private TEST-account campaign copies.
One draft kept one key through subject entry, an attachment and edits. One received
message kept one key through two flag revisions and a reply. Two received messages with
identical sender, subject and preview had different keys, and so did a reply and its
parent. Across the shared store, no key's revisions disagreed on Message-ID, and 777 of
777 keys kept the same Message-ID set from the first copy to the last. The key identifies
a store item, not one logical email across folders or accounts. The two Message-IDs whose key
set changed were an item leaving the store and a new item arriving with an existing Message-ID;
no surviving item changed key. Keys share one increasing space across both accounts.

`hxprobe db` exports the keys as `messages.local_keys`, and the intake records them per
email as `mail_keys`. Rows without a key keep the copy-hash plus send-time ID as the
fallback, which was observed to merge the identical-template pair. Moves, an Outlook
restart, delete-then-reuse, a store rebuild and identical drafts to different recipients
remain untested.
The snapshot helper regained Full Disk Access on 2026-10-26; Outlook control was still denied.

### Five-day comparison, 2026-10-21 to 2026-10-26

[The research record](stable-message-id-research.md#five-day-comparison-2026-10-21-to-2026-10-26)
compares `012-control` with a 2026-10-26 snapshot, across the 2026-10-25 reboot and Outlook
restart. Three TEST items kept key, Message-ID and folder. 438 of 438 surviving keys kept their
Message-ID set. Eight Inbox-to-folder moves made outside this study kept their keys. All 63 new
keys were above every earlier key, so none was reused. No key was linked to both accounts. One
email appeared under new keys in Sent Items and a custom folder after its Inbox key left; that may
be a key change and is unexplained.

## Catalog confidence

## Attachment metadata intake slice

The attachment catalog binds the maintained decoder source revision below.
Structure-only research on an immutable helper snapshot found 4,682 physical
attachment objects whose delimiter-framed filename was followed by a string
matching an HTML cid reference. This supports the inferred selector; it does not
establish an exhaustive grammar. Unsupported framing stays unknown.

The release decoder and intake loader were run against that immutable copy.
The eligible population is dated, sender-bearing email naming the configured
address exactly as sender or parsed recipient, after copy collapse. It yielded
756 email records, 744 with a resolved folder path, and 81 attachment metadata
rows across 8 emails. None of those eligible attachment rows had a content ID.
Message keys mapping to multiple decoded rows are excluded. These are whole-copy
counts, not a recent-mail window or complete-mailbox coverage claim. The source
bytes still matched the helper manifest SHA-256 afterward.

Intake records carry id, name, size, content_type and nullable content_id. Paths
and bytes stay outside those records. The synthetic actual-CLI test verifies
folder parents, message membership, metadata export and unchanged source bytes.
The activity write contract does not persist this metadata yet. Attachment bytes
remain outside this slice.


Null means the first Outlook version is unknown. The pinned source revision establishes catalog provenance, not an Outlook build or introduction version.

## Store header (store)

```json
{
  "parent": null,
  "identifier": {
    "kind": "container",
    "value": "Nostromo"
  },
  "wire_type": "container",
  "decoder": "header::check",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/header.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/header.rs), `check`.

## Magic (store.offset.0)

```json
{
  "parent": "store",
  "identifier": {
    "kind": "byte_offset",
    "value": 0
  },
  "wire_type": "ascii[8]",
  "decoder": "header::check",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/header.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/header.rs), `check`.

## Version (store.offset.8)

```json
{
  "parent": "store",
  "identifier": {
    "kind": "byte_offset",
    "value": 8
  },
  "wire_type": "u8",
  "decoder": "header::check",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/header.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/header.rs), `check`.

## Page size (store.offset.56)

```json
{
  "parent": "store",
  "identifier": {
    "kind": "byte_offset",
    "value": 56
  },
  "wire_type": "u64le",
  "decoder": "header::check",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/header.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/header.rs), `check`.

## Guard pattern (store.offset.156)

```json
{
  "parent": "store",
  "identifier": {
    "kind": "byte_offset",
    "value": 156
  },
  "wire_type": "bytes[36]",
  "decoder": null,
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/header.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/header.rs), `module documentation`.

## Block container (block)

```json
{
  "parent": null,
  "identifier": {
    "kind": "container",
    "value": null
  },
  "wire_type": "container",
  "decoder": "block::parse",
  "confidence": "unknown",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/block.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/block.rs), `parse`.

## Block type 8 (block.type.8)

```json
{
  "parent": "block",
  "identifier": {
    "kind": "block_type",
    "value": 8
  },
  "wire_type": "lz4-block",
  "decoder": "block::parse",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/block.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/block.rs), `module documentation`.

## Block type 16 (block.type.16)

```json
{
  "parent": "block",
  "identifier": {
    "kind": "block_type",
    "value": 16
  },
  "wire_type": "lz4-block",
  "decoder": "block::parse",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/block.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/block.rs), `module documentation`.

## Unknown block type (block.type.unknown)

```json
{
  "parent": "block",
  "identifier": {
    "kind": "block_type",
    "value": null
  },
  "wire_type": "unknown",
  "decoder": null,
  "confidence": "unknown",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/block.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/block.rs), `Block::kind`.

## Header CRC (block.offset.0)

```json
{
  "parent": "block",
  "identifier": {
    "kind": "byte_offset",
    "value": 0
  },
  "wire_type": "u32le",
  "decoder": "block::crc32",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/block.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/block.rs), `module documentation`.

## Body CRC (block.offset.4)

```json
{
  "parent": "block",
  "identifier": {
    "kind": "byte_offset",
    "value": 4
  },
  "wire_type": "u32le",
  "decoder": "block::crc32",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/block.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/block.rs), `module documentation`.

## Magic (block.offset.8)

```json
{
  "parent": "block",
  "identifier": {
    "kind": "byte_offset",
    "value": 8
  },
  "wire_type": "u64le",
  "decoder": "block::find_all",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/block.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/block.rs), `module documentation`.

## Block type (block.offset.16)

```json
{
  "parent": "block",
  "identifier": {
    "kind": "byte_offset",
    "value": 16
  },
  "wire_type": "u32le",
  "decoder": "block::read_header",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/block.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/block.rs), `module documentation`.

## Payload length (block.offset.20)

```json
{
  "parent": "block",
  "identifier": {
    "kind": "byte_offset",
    "value": 20
  },
  "wire_type": "u32le",
  "decoder": "block::read_header",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/block.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/block.rs), `module documentation`.

## Inflated length (block.offset.24)

```json
{
  "parent": "block",
  "identifier": {
    "kind": "byte_offset",
    "value": 24
  },
  "wire_type": "u32le",
  "decoder": "block::read_header",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/block.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/block.rs), `module documentation`.

## Observed constant 4 (block.offset.28)

```json
{
  "parent": "block",
  "identifier": {
    "kind": "byte_offset",
    "value": 28
  },
  "wire_type": "u32le",
  "decoder": null,
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/block.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/block.rs), `module documentation`.

## Unknown reserved header bytes (block.offset.32)

```json
{
  "parent": "block",
  "identifier": {
    "kind": "byte_offset",
    "value": 32
  },
  "wire_type": "unknown",
  "decoder": null,
  "confidence": "unknown",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/block.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/block.rs), `module documentation`.

## Compressed payload (block.offset.40)

```json
{
  "parent": "block",
  "identifier": {
    "kind": "byte_offset",
    "value": 40
  },
  "wire_type": "lz4-block",
  "decoder": "lz::decode_exact",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/block.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/block.rs), `module documentation`.

## Serialized object envelope (envelope)

```json
{
  "parent": null,
  "identifier": {
    "kind": "container",
    "value": null
  },
  "wire_type": "container",
  "decoder": "folder::envelopes",
  "confidence": "unknown",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `envelopes`.

## Envelope marker 5 (envelope.offset.0)

```json
{
  "parent": "envelope",
  "identifier": {
    "kind": "byte_offset",
    "value": 0
  },
  "wire_type": "u16le",
  "decoder": "folder::envelopes",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `envelopes`.

## Envelope tag (envelope.offset.2)

```json
{
  "parent": "envelope",
  "identifier": {
    "kind": "byte_offset",
    "value": 2
  },
  "wire_type": "u16le",
  "decoder": "folder::envelopes",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `envelopes`.

## Length including envelope (envelope.offset.4)

```json
{
  "parent": "envelope",
  "identifier": {
    "kind": "byte_offset",
    "value": 4
  },
  "wire_type": "u32le",
  "decoder": "folder::envelopes",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `envelopes`.

## Zero padding (envelope.offset.8)

```json
{
  "parent": "envelope",
  "identifier": {
    "kind": "byte_offset",
    "value": 8
  },
  "wire_type": "bytes[2]",
  "decoder": "folder::envelopes",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `envelopes`.

## Object body (envelope.offset.10)

```json
{
  "parent": "envelope",
  "identifier": {
    "kind": "byte_offset",
    "value": 10
  },
  "wire_type": "serialized-object",
  "decoder": "folder::envelopes",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `envelopes`.

## Folder envelope (envelope.tag.04c2)

```json
{
  "parent": "envelope",
  "identifier": {
    "kind": "envelope_tag",
    "value": 1218
  },
  "wire_type": "u16le",
  "decoder": "folder::Index::observe",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `Index::observe`.

## Folder envelope, 2026-10 build (envelope.tag.04d8)

```json
{
  "parent": "envelope",
  "identifier": {
    "kind": "envelope_tag",
    "value": 1240
  },
  "wire_type": "u16le",
  "decoder": "folder::Index::observe",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Observed on 2026-10-21 Studio TEST-campaign store copies, where every object of this class used this tag."
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `Index::observe`.

## Folder (object.class.004d)

```json
{
  "parent": "envelope",
  "identifier": {
    "kind": "record_type",
    "value": 77
  },
  "wire_type": "serialized-object",
  "decoder": "folder::Index::observe",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Class starts at object +0, ten bytes after envelope start."
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `Index::observe`.

## View membership envelope (envelope.tag.0430)

```json
{
  "parent": "envelope",
  "identifier": {
    "kind": "envelope_tag",
    "value": 1072
  },
  "wire_type": "u16le",
  "decoder": "folder::Index::links",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `Index::links`.

## View membership (object.class.004f)

```json
{
  "parent": "envelope",
  "identifier": {
    "kind": "record_type",
    "value": 79
  },
  "wire_type": "serialized-object",
  "decoder": "folder::Index::links",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Class starts at object +0, ten bytes after envelope start."
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `Index::links`.

## Compact message envelope (envelope.tag.0740)

```json
{
  "parent": "envelope",
  "identifier": {
    "kind": "envelope_tag",
    "value": 1856
  },
  "wire_type": "u16le",
  "decoder": "folder::Index::links",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `Index::links`.

## Compact message envelope, 2026-10 build (envelope.tag.074e)

```json
{
  "parent": "envelope",
  "identifier": {
    "kind": "envelope_tag",
    "value": 1870
  },
  "wire_type": "u16le",
  "decoder": "folder::Index::links",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Observed on 2026-10-21 Studio TEST-campaign store copies, where every object of this class used this tag."
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `Index::links`.

## Compact message (object.class.00ca)

```json
{
  "parent": "envelope",
  "identifier": {
    "kind": "record_type",
    "value": 202
  },
  "wire_type": "serialized-object",
  "decoder": "folder::Index::links",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Class starts at object +0, ten bytes after envelope start."
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `Index::links`.

## Message metadata envelope (envelope.tag.02c8)

```json
{
  "parent": "envelope",
  "identifier": {
    "kind": "envelope_tag",
    "value": 712
  },
  "wire_type": "u16le",
  "decoder": "folder::Index::observe",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `Index::observe`.

## Message metadata envelope, 2026-10 build (envelope.tag.02b8)

```json
{
  "parent": "envelope",
  "identifier": {
    "kind": "envelope_tag",
    "value": 696
  },
  "wire_type": "u16le",
  "decoder": "folder::Index::observe",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Observed on 2026-10-21 Studio TEST-campaign store copies, where every object of this class used this tag."
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `Index::observe`.

## Message metadata (object.class.00bf)

```json
{
  "parent": "envelope",
  "identifier": {
    "kind": "record_type",
    "value": 191
  },
  "wire_type": "serialized-object",
  "decoder": "folder::Index::observe",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Class starts at object +0, ten bytes after envelope start."
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `Index::observe`.

## Calendar or working hours envelope (envelope.tag.0455)

```json
{
  "parent": "envelope",
  "identifier": {
    "kind": "envelope_tag",
    "value": 1109
  },
  "wire_type": "u16le",
  "decoder": "calendar::Index::observe",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/calendar.rs` at `55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d`](https://github.com/DoctorCRE/carr-system/blob/55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d/vendor/hxprobe/src/calendar.rs), `objects`.

## Calendar or working hours (object.class.006b)

```json
{
  "parent": "envelope",
  "identifier": {
    "kind": "record_type",
    "value": 107
  },
  "wire_type": "serialized-object",
  "decoder": "calendar::Index::observe",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Class starts at object +0, ten bytes after envelope start."
}
```

- [`vendor/hxprobe/src/calendar.rs` at `55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d`](https://github.com/DoctorCRE/carr-system/blob/55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d/vendor/hxprobe/src/calendar.rs), `objects`.

## Unknown envelope tag (envelope.tag.unknown)

```json
{
  "parent": "envelope",
  "identifier": {
    "kind": "envelope_tag",
    "value": null
  },
  "wire_type": "unknown",
  "decoder": null,
  "confidence": "unknown",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `envelopes`.

## View collection reference (object.class.0004)

```json
{
  "parent": "envelope",
  "identifier": {
    "kind": "record_type",
    "value": 4
  },
  "wire_type": "serialized-object",
  "decoder": "folder::Index::observe",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `Index::observe`.

## Unknown object class (object.class.unknown)

```json
{
  "parent": "envelope",
  "identifier": {
    "kind": "record_type",
    "value": null
  },
  "wire_type": "unknown",
  "decoder": null,
  "confidence": "unknown",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `envelopes`.

## Object class (object.class.004d.offset.0)

```json
{
  "parent": "object.class.004d",
  "identifier": {
    "kind": "byte_offset",
    "value": 0
  },
  "wire_type": "u16le",
  "decoder": "folder::signature",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `reference`.

## Signature zero bytes (object.class.004d.offset.2)

```json
{
  "parent": "object.class.004d",
  "identifier": {
    "kind": "byte_offset",
    "value": 2
  },
  "wire_type": "bytes[8]",
  "decoder": "folder::signature",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `reference`.

## Local object key (object.class.004d.offset.10)

```json
{
  "parent": "object.class.004d",
  "identifier": {
    "kind": "byte_offset",
    "value": 10
  },
  "wire_type": "u64le",
  "decoder": "folder::reference",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `reference`.

## Reference kind (object.class.004d.offset.18)

```json
{
  "parent": "object.class.004d",
  "identifier": {
    "kind": "byte_offset",
    "value": 18
  },
  "wire_type": "u32le",
  "decoder": "folder::reference",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `reference`.

## Owner key (object.class.004d.offset.22)

```json
{
  "parent": "object.class.004d",
  "identifier": {
    "kind": "byte_offset",
    "value": 22
  },
  "wire_type": "u64le",
  "decoder": "folder::reference",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `reference`.

## Repeated local key (object.class.004d.offset.30)

```json
{
  "parent": "object.class.004d",
  "identifier": {
    "kind": "byte_offset",
    "value": 30
  },
  "wire_type": "u64le",
  "decoder": "folder::reference",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `reference`.

## Repeated owner or local key (object.class.004d.offset.38)

```json
{
  "parent": "object.class.004d",
  "identifier": {
    "kind": "byte_offset",
    "value": 38
  },
  "wire_type": "u64le",
  "decoder": "folder::reference",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `reference`.

## Object class (object.class.004f.offset.0)

```json
{
  "parent": "object.class.004f",
  "identifier": {
    "kind": "byte_offset",
    "value": 0
  },
  "wire_type": "u16le",
  "decoder": "folder::signature",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `reference`.

## Signature zero bytes (object.class.004f.offset.2)

```json
{
  "parent": "object.class.004f",
  "identifier": {
    "kind": "byte_offset",
    "value": 2
  },
  "wire_type": "bytes[8]",
  "decoder": "folder::signature",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `reference`.

## Local object key (object.class.004f.offset.10)

```json
{
  "parent": "object.class.004f",
  "identifier": {
    "kind": "byte_offset",
    "value": 10
  },
  "wire_type": "u64le",
  "decoder": "folder::reference",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `reference`.

## Reference kind (object.class.004f.offset.18)

```json
{
  "parent": "object.class.004f",
  "identifier": {
    "kind": "byte_offset",
    "value": 18
  },
  "wire_type": "u32le",
  "decoder": "folder::reference",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `reference`.

## Owner key (object.class.004f.offset.22)

```json
{
  "parent": "object.class.004f",
  "identifier": {
    "kind": "byte_offset",
    "value": 22
  },
  "wire_type": "u64le",
  "decoder": "folder::reference",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `reference`.

## Repeated local key (object.class.004f.offset.30)

```json
{
  "parent": "object.class.004f",
  "identifier": {
    "kind": "byte_offset",
    "value": 30
  },
  "wire_type": "u64le",
  "decoder": "folder::reference",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `reference`.

## Repeated owner or local key (object.class.004f.offset.38)

```json
{
  "parent": "object.class.004f",
  "identifier": {
    "kind": "byte_offset",
    "value": 38
  },
  "wire_type": "u64le",
  "decoder": "folder::reference",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `reference`.

## Object class (object.class.00ca.offset.0)

```json
{
  "parent": "object.class.00ca",
  "identifier": {
    "kind": "byte_offset",
    "value": 0
  },
  "wire_type": "u16le",
  "decoder": "folder::signature",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `message_key`.

## Signature zero bytes (object.class.00ca.offset.2)

```json
{
  "parent": "object.class.00ca",
  "identifier": {
    "kind": "byte_offset",
    "value": 2
  },
  "wire_type": "bytes[8]",
  "decoder": "folder::signature",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `message_key`.

## Local object key (object.class.00ca.offset.10)

```json
{
  "parent": "object.class.00ca",
  "identifier": {
    "kind": "byte_offset",
    "value": 10
  },
  "wire_type": "u64le",
  "decoder": "folder::message_key",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "WR-000502 candidate stable email ID. TEST-account copies (2026-10-21 campaign) and a 2026-10-26 snapshot: stable across draft edits, attachment, flag revisions, reply-to, read-state, a reboot and Outlook restart, five days of sync and eight moves made outside this study; distinct for identical-template messages and for a reply; no reuse observed; one store-wide space across accounts. One possible key change is unexplained. Controlled moves, deletes and a store rebuild are untested. Exported by db as messages.local_keys."
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `message_key`.

## Repeated local key (object.class.00ca.offset.30)

```json
{
  "parent": "object.class.00ca",
  "identifier": {
    "kind": "byte_offset",
    "value": 30
  },
  "wire_type": "u64le",
  "decoder": "folder::reference",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `message_key`.

## Repeated local key (object.class.00ca.offset.38)

```json
{
  "parent": "object.class.00ca",
  "identifier": {
    "kind": "byte_offset",
    "value": 38
  },
  "wire_type": "u64le",
  "decoder": "folder::reference",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `message_key`.

## Object class (object.class.00bf.offset.0)

```json
{
  "parent": "object.class.00bf",
  "identifier": {
    "kind": "byte_offset",
    "value": 0
  },
  "wire_type": "u16le",
  "decoder": "folder::signature",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `message_key`.

## Signature zero bytes (object.class.00bf.offset.2)

```json
{
  "parent": "object.class.00bf",
  "identifier": {
    "kind": "byte_offset",
    "value": 2
  },
  "wire_type": "bytes[8]",
  "decoder": "folder::signature",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `message_key`.

## Local object key (object.class.00bf.offset.10)

```json
{
  "parent": "object.class.00bf",
  "identifier": {
    "kind": "byte_offset",
    "value": 10
  },
  "wire_type": "u64le",
  "decoder": "folder::message_key",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `message_key`.

## Repeated local key (object.class.00bf.offset.30)

```json
{
  "parent": "object.class.00bf",
  "identifier": {
    "kind": "byte_offset",
    "value": 30
  },
  "wire_type": "u64le",
  "decoder": "folder::reference",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `message_key`.

## Repeated local key (object.class.00bf.offset.38)

```json
{
  "parent": "object.class.00bf",
  "identifier": {
    "kind": "byte_offset",
    "value": 38
  },
  "wire_type": "u64le",
  "decoder": "folder::reference",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `message_key`.

## Object class (object.class.0004.offset.0)

```json
{
  "parent": "object.class.0004",
  "identifier": {
    "kind": "byte_offset",
    "value": 0
  },
  "wire_type": "u16le",
  "decoder": "folder::signature",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `Index::observe`.

## Signature zero bytes (object.class.0004.offset.2)

```json
{
  "parent": "object.class.0004",
  "identifier": {
    "kind": "byte_offset",
    "value": 2
  },
  "wire_type": "bytes[8]",
  "decoder": "folder::signature",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `Index::observe`.

## Local object key (object.class.0004.offset.10)

```json
{
  "parent": "object.class.0004",
  "identifier": {
    "kind": "byte_offset",
    "value": 10
  },
  "wire_type": "u64le",
  "decoder": "folder::message_key",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `Index::observe`.

## Repeated collection key (object.class.0004.offset.38)

```json
{
  "parent": "object.class.0004",
  "identifier": {
    "kind": "byte_offset",
    "value": 38
  },
  "wire_type": "u64le",
  "decoder": "folder::reference",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `Index::observe`.

## View collection reference (object.class.004d.offset.160)

```json
{
  "parent": "object.class.004d",
  "identifier": {
    "kind": "byte_offset",
    "value": 160
  },
  "wire_type": "serialized-object",
  "decoder": "folder::Index::observe",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `Index::observe`.

## View collection reference (object.class.004d.offset.360)

```json
{
  "parent": "object.class.004d",
  "identifier": {
    "kind": "byte_offset",
    "value": 360
  },
  "wire_type": "serialized-object",
  "decoder": "folder::Index::observe",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `Index::observe`.

## Parent folder reference (object.class.004d.offset.792)

```json
{
  "parent": "object.class.004d",
  "identifier": {
    "kind": "byte_offset",
    "value": 792
  },
  "wire_type": "typed-reference",
  "decoder": "folder::parent",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Ten zero bytes mean a top-level folder. Otherwise a typed class 004d reference in the same account. On the 2026-10-21 copies 208 of 236 named folders resolved to a listed parent and 28 were top-level."
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `parent`.

## Expanded folder reference (object.class.00ca.offset.408)

```json
{
  "parent": "object.class.00ca",
  "identifier": {
    "kind": "byte_offset",
    "value": 408
  },
  "wire_type": "serialized-object",
  "decoder": "folder::Index::links",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `Index::links`.

## Folder name (folder.name)

```json
{
  "parent": "object.class.004d",
  "identifier": {
    "kind": "string_run_selector",
    "value": "trailing UTF-16 run after sort-key NUL"
  },
  "wire_type": "utf16le-z",
  "decoder": "folder::trailing_name",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `trailing_name`.

## Local folder key (metadata.folder)

```json
{
  "parent": "object.class.00bf",
  "identifier": {
    "kind": "byte_offset",
    "value": "object length - 8"
  },
  "wire_type": "u64le",
  "decoder": "folder::Index::observe",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Accepted only after the 51-byte immutable-ID tail framing check."
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `Index::observe`.

## Calendar occurrence start (object.class.006b.offset.2)

```json
{
  "parent": "object.class.006b",
  "identifier": {
    "kind": "byte_offset",
    "value": 2
  },
  "wire_type": "u64le-dotnet-ticks",
  "decoder": "calendar::parse",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Equals the start at +574 on recurring occurrences (whose Global Object ID carries an instance date) and is zero otherwise; exported as events.recurrence."
}
```

- [`vendor/hxprobe/src/calendar.rs` at `55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d`](https://github.com/DoctorCRE/carr-system/blob/55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d/vendor/hxprobe/src/calendar.rs), `OCCURRENCE`.

## Calendar start (object.class.006b.offset.574)

```json
{
  "parent": "object.class.006b",
  "identifier": {
    "kind": "byte_offset",
    "value": 574
  },
  "wire_type": "u64le-dotnet-ticks",
  "decoder": "calendar::tick",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Offset relative to object body, not envelope start."
}
```

- [`vendor/hxprobe/src/calendar.rs` at `55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d`](https://github.com/DoctorCRE/carr-system/blob/55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d/vendor/hxprobe/src/calendar.rs), `tick`.

## Calendar end (object.class.006b.offset.582)

```json
{
  "parent": "object.class.006b",
  "identifier": {
    "kind": "byte_offset",
    "value": 582
  },
  "wire_type": "u64le-dotnet-ticks",
  "decoder": "calendar::tick",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Offset relative to object body, not envelope start."
}
```

- [`vendor/hxprobe/src/calendar.rs` at `55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d`](https://github.com/DoctorCRE/carr-system/blob/55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d/vendor/hxprobe/src/calendar.rs), `tick`.

## Global object ID (calendar.goid)

```json
{
  "parent": "object.class.006b",
  "identifier": {
    "kind": "string_run_selector",
    "value": "hex UTF-16 run beginning with GOID_PREFIX"
  },
  "wire_type": "utf16le",
  "decoder": "calendar::goid",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/calendar.rs` at `55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d`](https://github.com/DoctorCRE/carr-system/blob/55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d/vendor/hxprobe/src/calendar.rs), `goid`.

## Calendar subject (calendar.subject)

```json
{
  "parent": "object.class.006b",
  "identifier": {
    "kind": "string_run_selector",
    "value": "repeated subject run"
  },
  "wire_type": "utf16le-z",
  "decoder": "calendar::parse",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/calendar.rs` at `55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d`](https://github.com/DoctorCRE/carr-system/blob/55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d/vendor/hxprobe/src/calendar.rs), `parse`.

## Calendar location (calendar.location)

```json
{
  "parent": "object.class.006b",
  "identifier": {
    "kind": "string_run_selector",
    "value": "run following selected subject"
  },
  "wire_type": "utf16le-z",
  "decoder": "calendar::parse",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/calendar.rs` at `55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d`](https://github.com/DoctorCRE/carr-system/blob/55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d/vendor/hxprobe/src/calendar.rs), `parse`.

## Organizer name (calendar.organizer_name)

```json
{
  "parent": "object.class.006b",
  "identifier": {
    "kind": "string_run_selector",
    "value": "run adjacent to organizer address"
  },
  "wire_type": "utf16le-z",
  "decoder": "calendar::parse",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/calendar.rs` at `55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d`](https://github.com/DoctorCRE/carr-system/blob/55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d/vendor/hxprobe/src/calendar.rs), `parse`.

## Organizer address (calendar.organizer_email)

```json
{
  "parent": "object.class.006b",
  "identifier": {
    "kind": "string_run_selector",
    "value": "email-shaped run"
  },
  "wire_type": "utf16le-z",
  "decoder": "calendar::parse",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/calendar.rs` at `55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d`](https://github.com/DoctorCRE/carr-system/blob/55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d/vendor/hxprobe/src/calendar.rs), `parse`.

## Calendar notes (calendar.body)

```json
{
  "parent": "object.class.006b",
  "identifier": {
    "kind": "string_run_selector",
    "value": "longest non-subject run containing CRLF or more than 60 characters"
  },
  "wire_type": "utf16le-z",
  "decoder": "calendar::parse",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/calendar.rs` at `55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d`](https://github.com/DoctorCRE/carr-system/blob/55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d/vendor/hxprobe/src/calendar.rs), `parse`.

## Attendees (calendar.attendees)

```json
{
  "parent": "object.class.006b",
  "identifier": {
    "kind": "string_run_selector",
    "value": "u32 count and length-prefixed pairs ending at object end"
  },
  "wire_type": "count+u8-length-utf16le+bytes[12]",
  "decoder": "calendar::attendees_at",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/calendar.rs` at `55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d`](https://github.com/DoctorCRE/carr-system/blob/55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d/vendor/hxprobe/src/calendar.rs), `attendees_at`.

## Attendee kind and response (calendar.attendee_status)

```json
{
  "parent": "calendar.attendees",
  "identifier": {
    "kind": "string_run_selector",
    "value": "12 status bytes after each attendee address"
  },
  "wire_type": "u32le[3]",
  "decoder": "calendar::Attendee::kind",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Word 0 is the attendee kind (0 required, 1 optional) and word 1 the response (4 no response yet), pinned by one TEST-account invitation on 2026-10-28. Response codes 0, 1 and 2 have no ground truth and are exported raw without a label."
}
```

- [`vendor/hxprobe/src/calendar.rs` at `55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d`](https://github.com/DoctorCRE/carr-system/blob/55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d/vendor/hxprobe/src/calendar.rs), `Attendee`.

## Canceled subject (calendar.canceled)

```json
{
  "parent": "calendar.subject",
  "identifier": {
    "kind": "string_run_selector",
    "value": "first subject copy prefixed Canceled: or Cancelled:"
  },
  "wire_type": "utf16le-nul-terminated",
  "decoder": "calendar::parse",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Outlook stores the cancellation prefix on the first subject copy while the repeated copy keeps the plain subject. Exported as lifecycle_status canceled; no binary cancellation flag is established."
}
```

- [`vendor/hxprobe/src/calendar.rs` at `55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d`](https://github.com/DoctorCRE/carr-system/blob/55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d/vendor/hxprobe/src/calendar.rs), `CANCELED`.

## Mail anchor region (mail)

```json
{
  "parent": null,
  "identifier": {
    "kind": "record_anchor",
    "value": "IPM.Note"
  },
  "wire_type": "utf16le-anchor",
  "decoder": "record::anchor_needle",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "A heuristic region bounded by neighboring anchors. Not a numeric serialized class or object envelope."
}
```

- [`vendor/hxprobe/src/record.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/record.rs), `anchor_needle`.

## SenderAddress (mail.selector.SenderAddress)

```json
{
  "parent": "mail",
  "identifier": {
    "kind": "string_run_selector",
    "value": "nearest email-shaped run near anchor"
  },
  "wire_type": "utf16le-run",
  "decoder": "schema::build_bounded",
  "confidence": "inferred",
  "first_seen_version": null,
  "decoder_key": "SenderAddress"
}
```

- [`vendor/hxprobe/src/schema.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/schema.rs), `build_bounded`.

## SenderName (mail.selector.SenderName)

```json
{
  "parent": "mail",
  "identifier": {
    "kind": "string_run_selector",
    "value": "person-like neighbor of selected sender address"
  },
  "wire_type": "utf16le-run",
  "decoder": "schema::build_bounded",
  "confidence": "inferred",
  "first_seen_version": null,
  "decoder_key": "SenderName"
}
```

- [`vendor/hxprobe/src/schema.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/schema.rs), `build_bounded`.

## ItemClass (mail.selector.ItemClass)

```json
{
  "parent": "mail",
  "identifier": {
    "kind": "string_run_selector",
    "value": "anchor at relative offset 0"
  },
  "wire_type": "utf16le-run",
  "decoder": "schema::build_bounded",
  "confidence": "inferred",
  "first_seen_version": null,
  "decoder_key": "ItemClass"
}
```

- [`vendor/hxprobe/src/schema.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/schema.rs), `build_bounded`.

## InternetMessageId (mail.selector.InternetMessageId)

```json
{
  "parent": "mail",
  "identifier": {
    "kind": "string_run_selector",
    "value": "angle-bracketed message ID after anchor"
  },
  "wire_type": "utf16le-run",
  "decoder": "schema::build_bounded",
  "confidence": "inferred",
  "first_seen_version": null,
  "decoder_key": "InternetMessageId"
}
```

- [`vendor/hxprobe/src/schema.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/schema.rs), `build_bounded`.

## Preview (mail.selector.Preview)

```json
{
  "parent": "mail",
  "identifier": {
    "kind": "string_run_selector",
    "value": "longest unclaimed non-markup run after anchor"
  },
  "wire_type": "utf16le-run",
  "decoder": "schema::build_bounded",
  "confidence": "inferred",
  "first_seen_version": null,
  "decoder_key": "Preview"
}
```

- [`vendor/hxprobe/src/schema.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/schema.rs), `build_bounded`.

## NormalizedSubject (mail.selector.NormalizedSubject)

```json
{
  "parent": "mail",
  "identifier": {
    "kind": "string_run_selector",
    "value": "duplicate normalized subject or bounded solo fallback"
  },
  "wire_type": "utf16le-run",
  "decoder": "schema::build_bounded",
  "confidence": "inferred",
  "first_seen_version": null,
  "decoder_key": "NormalizedSubject"
}
```

- [`vendor/hxprobe/src/schema.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/schema.rs), `build_bounded`.

## Recipient (mail.selector.Recipient)

```json
{
  "parent": "mail",
  "identifier": {
    "kind": "string_run_selector",
    "value": "email-shaped runs after anchor"
  },
  "wire_type": "utf16le-run",
  "decoder": "schema::build_bounded",
  "confidence": "inferred",
  "first_seen_version": null,
  "decoder_key": "Recipient"
}
```

- [`vendor/hxprobe/src/schema.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/schema.rs), `build_bounded`.

## ConversationId (mail.selector.ConversationId)

```json
{
  "parent": "mail",
  "identifier": {
    "kind": "string_run_selector",
    "value": "hex or GUID-shaped run"
  },
  "wire_type": "utf16le-run",
  "decoder": "schema::build_bounded",
  "confidence": "inferred",
  "first_seen_version": null,
  "decoder_key": "ConversationId"
}
```

- [`vendor/hxprobe/src/schema.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/schema.rs), `build_bounded`.

## Send time (mail.sent)

```json
{
  "parent": "mail",
  "identifier": {
    "kind": "string_run_selector",
    "value": "earliest plausible tick in anchor region"
  },
  "wire_type": "u64le-dotnet-ticks",
  "decoder": "record::read_time",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/record.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/record.rs), `read_time`.

## Message body (mail.body)

```json
{
  "parent": "mail",
  "identifier": {
    "kind": "string_run_selector",
    "value": "inline markup or text after anchor"
  },
  "wire_type": "utf16le-run",
  "decoder": "record::parse",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/record.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/record.rs), `parse`.

## Sidecar reference (mail.sidecar)

```json
{
  "parent": "mail",
  "identifier": {
    "kind": "string_run_selector",
    "value": "EFMData/ basename run"
  },
  "wire_type": "utf16le-run",
  "decoder": "body::sidecar_names",
  "confidence": "inferred",
  "first_seen_version": null
}
```

- [`vendor/hxprobe/src/body.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/body.rs), `sidecar_names`.

## Unknown property IDs (mail.wire-fields.unknown)

```json
{
  "parent": "mail",
  "identifier": {
    "kind": "wire_field_id",
    "value": null
  },
  "wire_type": "unknown",
  "decoder": null,
  "confidence": "unknown",
  "first_seen_version": null,
  "notes": "No numeric property-ID framing established by the pinned reader. Offsets and selectors above are not property IDs."
}
```

- [`vendor/hxprobe/src/schema.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/schema.rs), `build_bounded`.

## Unknown property IDs (object.class.004d.wire-fields.unknown)

```json
{
  "parent": "object.class.004d",
  "identifier": {
    "kind": "wire_field_id",
    "value": null
  },
  "wire_type": "unknown",
  "decoder": null,
  "confidence": "unknown",
  "first_seen_version": null,
  "notes": "No numeric property-ID framing established by the pinned reader. Offsets and selectors above are not property IDs."
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `envelopes`.

## Unknown property IDs (object.class.004f.wire-fields.unknown)

```json
{
  "parent": "object.class.004f",
  "identifier": {
    "kind": "wire_field_id",
    "value": null
  },
  "wire_type": "unknown",
  "decoder": null,
  "confidence": "unknown",
  "first_seen_version": null,
  "notes": "No numeric property-ID framing established by the pinned reader. Offsets and selectors above are not property IDs."
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `envelopes`.

## Unknown property IDs (object.class.00ca.wire-fields.unknown)

```json
{
  "parent": "object.class.00ca",
  "identifier": {
    "kind": "wire_field_id",
    "value": null
  },
  "wire_type": "unknown",
  "decoder": null,
  "confidence": "unknown",
  "first_seen_version": null,
  "notes": "No numeric property-ID framing established by the pinned reader. Offsets and selectors above are not property IDs."
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `envelopes`.

## Unknown property IDs (object.class.00bf.wire-fields.unknown)

```json
{
  "parent": "object.class.00bf",
  "identifier": {
    "kind": "wire_field_id",
    "value": null
  },
  "wire_type": "unknown",
  "decoder": null,
  "confidence": "unknown",
  "first_seen_version": null,
  "notes": "No numeric property-ID framing established by the pinned reader. Offsets and selectors above are not property IDs."
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `envelopes`.

## Unknown property IDs (object.class.006b.wire-fields.unknown)

```json
{
  "parent": "object.class.006b",
  "identifier": {
    "kind": "wire_field_id",
    "value": null
  },
  "wire_type": "unknown",
  "decoder": null,
  "confidence": "unknown",
  "first_seen_version": null,
  "notes": "No numeric property-ID framing established by the pinned reader. Offsets and selectors above are not property IDs."
}
```

- [`vendor/hxprobe/src/calendar.rs` at `55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d`](https://github.com/DoctorCRE/carr-system/blob/55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d/vendor/hxprobe/src/calendar.rs), `objects`.

## Unknown property IDs (object.class.0004.wire-fields.unknown)

```json
{
  "parent": "object.class.0004",
  "identifier": {
    "kind": "wire_field_id",
    "value": null
  },
  "wire_type": "unknown",
  "decoder": null,
  "confidence": "unknown",
  "first_seen_version": null,
  "notes": "No numeric property-ID framing established by the pinned reader. Offsets and selectors above are not property IDs."
}
```

- [`vendor/hxprobe/src/folder.rs` at `b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d`](https://github.com/DoctorCRE/carr-system/blob/b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d/vendor/hxprobe/src/folder.rs), `envelopes`.

## Attachment object (object.class.016a)

```json
{
  "parent": "store",
  "identifier": {
    "kind": "record_type",
    "value": 362
  },
  "wire_type": "serialized_object",
  "decoder": "attachment::parse",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Bounded tag 0318 envelope; no attachment bytes are read."
}
```

- [`vendor/hxprobe/src/attachment.rs` at `bc8538ac6845ddd716efa18791361ab04e9c5694`](https://github.com/DoctorCRE/carr-system/blob/bc8538ac6845ddd716efa18791361ab04e9c5694/vendor/hxprobe/src/attachment.rs), `parse`.

## Attachment envelope (attachment.envelope)

```json
{
  "parent": "object.class.016a",
  "identifier": {
    "kind": "envelope_tag",
    "value": 792
  },
  "wire_type": "u16le",
  "decoder": "attachment::Index::observe",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Ten-byte envelope length includes its header. Typed class signature is checked."
}
```

- [`vendor/hxprobe/src/attachment.rs` at `bc8538ac6845ddd716efa18791361ab04e9c5694`](https://github.com/DoctorCRE/carr-system/blob/bc8538ac6845ddd716efa18791361ab04e9c5694/vendor/hxprobe/src/attachment.rs), `observe`.

## Attachment owning message (attachment.message)

```json
{
  "parent": "object.class.016a",
  "identifier": {
    "kind": "byte_offset",
    "value": 360
  },
  "wire_type": "typed_reference",
  "decoder": "attachment::parse",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Class 00ca reference with nonzero repeated key at reference offsets 10, 30 and 38. Conflicting owners quarantine the attachment ID. Conflicting filenames also quarantine the ID."
}
```

- [`vendor/hxprobe/src/attachment.rs` at `bc8538ac6845ddd716efa18791361ab04e9c5694`](https://github.com/DoctorCRE/carr-system/blob/bc8538ac6845ddd716efa18791361ab04e9c5694/vendor/hxprobe/src/attachment.rs), `parse`.

## Attachment declared byte length (attachment.size)

```json
{
  "parent": "object.class.016a",
  "identifier": {
    "kind": "byte_offset",
    "value": 558
  },
  "wire_type": "u64le",
  "decoder": "attachment::parse",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Stored declaration, not a measured byte length. Cached revisions retain the maximum."
}
```

- [`vendor/hxprobe/src/attachment.rs` at `bc8538ac6845ddd716efa18791361ab04e9c5694`](https://github.com/DoctorCRE/carr-system/blob/bc8538ac6845ddd716efa18791361ab04e9c5694/vendor/hxprobe/src/attachment.rs), `parse`.

## Attachment display filename (attachment.name)

```json
{
  "parent": "object.class.016a",
  "identifier": {
    "kind": "string_run_selector",
    "value": "cache_basename"
  },
  "wire_type": "utf16le_z",
  "decoder": "attachment::parse",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Cache basename with numeric bracket suffix removed. name_verified requires a standalone filename after the cache path and sort-key delimiter 01 01 01 01 00."
}
```

- [`vendor/hxprobe/src/attachment.rs` at `bc8538ac6845ddd716efa18791361ab04e9c5694`](https://github.com/DoctorCRE/carr-system/blob/bc8538ac6845ddd716efa18791361ab04e9c5694/vendor/hxprobe/src/attachment.rs), `parse`.

## Attachment declared MIME type (attachment.type)

```json
{
  "parent": "object.class.016a",
  "identifier": {
    "kind": "string_run_selector",
    "value": "mime_before_cache_path"
  },
  "wire_type": "utf16le_z",
  "decoder": "attachment::declared_type",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "First short ASCII type/subtype between the fixed header and cache path. Revision disagreement stays unknown. No byte-type detection."
}
```

- [`vendor/hxprobe/src/attachment.rs` at `bc8538ac6845ddd716efa18791361ab04e9c5694`](https://github.com/DoctorCRE/carr-system/blob/bc8538ac6845ddd716efa18791361ab04e9c5694/vendor/hxprobe/src/attachment.rs), `declared_type`.

## Attachment MIME content ID (attachment.content-id)

```json
{
  "parent": "object.class.016a",
  "identifier": {
    "kind": "string_run_selector",
    "value": "after_standalone_filename"
  },
  "wire_type": "utf16le_z",
  "decoder": "attachment::parse",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Immediate bounded printable string after the standalone filename. Empty, truncated, whitespace-containing and revision-conflicting values remain NULL. This selector is inferred, not a wire field ID."
}
```

- [`vendor/hxprobe/src/attachment.rs` at `bc8538ac6845ddd716efa18791361ab04e9c5694`](https://github.com/DoctorCRE/carr-system/blob/bc8538ac6845ddd716efa18791361ab04e9c5694/vendor/hxprobe/src/attachment.rs), `parse`.

## Category definition object (object.class.0069)

```json
{
  "parent": "store",
  "identifier": {
    "kind": "record_type",
    "value": 105
  },
  "wire_type": "serialized_object",
  "decoder": "category::Index::observe",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Own key at +10/+30 and owner key at +22/+38, reference kind 0x2c at +18. Observed only as the six default categories, once per owner (four owners in the 2026-10-28 Studio research copies). Message assignments are stored by name on the detail object; see detail.categories."
}
```

- [`vendor/hxprobe/src/category.rs` at `4b9f1a53c9742cab8e77a22ab64ed58a392e4e41`](https://github.com/DoctorCRE/carr-system/blob/4b9f1a53c9742cab8e77a22ab64ed58a392e4e41/vendor/hxprobe/src/category.rs), `observe`.

## Category envelope (category.envelope)

```json
{
  "parent": "object.class.0069",
  "identifier": {
    "kind": "envelope_tag",
    "value": 180
  },
  "wire_type": "u16le",
  "decoder": "category::Index::observe",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Ten-byte envelope length includes its header. Typed class signature is checked."
}
```

- [`vendor/hxprobe/src/category.rs` at `4b9f1a53c9742cab8e77a22ab64ed58a392e4e41`](https://github.com/DoctorCRE/carr-system/blob/4b9f1a53c9742cab8e77a22ab64ed58a392e4e41/vendor/hxprobe/src/category.rs), `observe`.

## Category owning list object (category.owner)

```json
{
  "parent": "object.class.0069",
  "identifier": {
    "kind": "byte_offset",
    "value": 22
  },
  "wire_type": "u64le",
  "decoder": "category::Index::observe",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Repeated at +38. Its class 0049 object is not yet linked to a folder account_id. An owner conflict across revisions drops the category."
}
```

- [`vendor/hxprobe/src/category.rs` at `4b9f1a53c9742cab8e77a22ab64ed58a392e4e41`](https://github.com/DoctorCRE/carr-system/blob/4b9f1a53c9742cab8e77a22ab64ed58a392e4e41/vendor/hxprobe/src/category.rs), `observe`.

## Category colour index (category.color)

```json
{
  "parent": "object.class.0069",
  "identifier": {
    "kind": "byte_offset",
    "value": 126
  },
  "wire_type": "u32le",
  "decoder": "category::Index::observe",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "OlCategoryColor numbering, 0 to 25. All 24 observed definitions carry Outlook's default colour for their default name (red 1, orange 2, yellow 4, green 5, blue 8, purple 9). Revision disagreement stays unknown."
}
```

- [`vendor/hxprobe/src/category.rs` at `4b9f1a53c9742cab8e77a22ab64ed58a392e4e41`](https://github.com/DoctorCRE/carr-system/blob/4b9f1a53c9742cab8e77a22ab64ed58a392e4e41/vendor/hxprobe/src/category.rs), `observe`.

## Category display name (category.name)

```json
{
  "parent": "object.class.0069",
  "identifier": {
    "kind": "string_run_selector",
    "value": "trailing_name"
  },
  "wire_type": "utf16le_z",
  "decoder": "category::Index::observe",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Same trailing layout as folder names: binary sort key, single-byte NUL, then the UTF-16 name. Revision disagreement stays unknown."
}
```

- [`vendor/hxprobe/src/category.rs` at `4b9f1a53c9742cab8e77a22ab64ed58a392e4e41`](https://github.com/DoctorCRE/carr-system/blob/4b9f1a53c9742cab8e77a22ab64ed58a392e4e41/vendor/hxprobe/src/category.rs), `observe`.

## Message detail object (object.class.00c9.060f)

```json
{
  "parent": "store",
  "identifier": {
    "kind": "record_type",
    "value": 201
  },
  "wire_type": "serialized_object",
  "decoder": "detail::Index::observe",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Envelope tag 060f. Typed reference kind 0xd4: message local key at +10/+30 (the 00ca key), owner at +22/+38. Fixed layout through +1508; the byte at +1491 is 3 in all 6,827 objects across three copies and is checked. Present for every keyed message row (1,538 of 1,980 rows in the 13:23Z copy)."
}
```

- [`vendor/hxprobe/src/detail.rs` at `4b9f1a53c9742cab8e77a22ab64ed58a392e4e41`](https://github.com/DoctorCRE/carr-system/blob/4b9f1a53c9742cab8e77a22ab64ed58a392e4e41/vendor/hxprobe/src/detail.rs), `Index::observe`.

## Message detail revision counter (detail.revision)

```json
{
  "parent": "object.class.00c9.060f",
  "identifier": {
    "kind": "byte_offset",
    "value": 1418
  },
  "wire_type": "u32le",
  "decoder": "detail::parse",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Rises with each change in every labelled sequence. The newest revision wins; disagreeing fields at the same revision are unknown."
}
```

- [`vendor/hxprobe/src/detail.rs` at `4b9f1a53c9742cab8e77a22ab64ed58a392e4e41`](https://github.com/DoctorCRE/carr-system/blob/4b9f1a53c9742cab8e77a22ab64ed58a392e4e41/vendor/hxprobe/src/detail.rs), `parse`.

## Follow-up flag status (detail.flag)

```json
{
  "parent": "object.class.00c9.060f",
  "identifier": {
    "kind": "byte_offset",
    "value": 1474
  },
  "wire_type": "u8",
  "decoder": "detail::parse",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Labelled TEST-account sample, 2026-10-28 13:14:58Z and 13:23:16Z research copies, changes made in Outlook on the web: 0 none, 1 complete, 2 flagged matched 4 of 4 labelled messages plus the 2026-10-21 campaign flag. Other values are unknown."
}
```

- [`vendor/hxprobe/src/detail.rs` at `4b9f1a53c9742cab8e77a22ab64ed58a392e4e41`](https://github.com/DoctorCRE/carr-system/blob/4b9f1a53c9742cab8e77a22ab64ed58a392e4e41/vendor/hxprobe/src/detail.rs), `parse`.

## Follow-up flag due date (detail.flag-due)

```json
{
  "parent": "object.class.00c9.060f",
  "identifier": {
    "kind": "byte_offset",
    "value": 294
  },
  "wire_type": "dotnet_ticks_u64le",
  "decoder": "detail::parse",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Labelled TEST-account sample, 2026-10-28 13:14:58Z and 13:23:16Z research copies, changes made in Outlook on the web: 2026-10-29 00:00 on the one dated flag; DateTime.MaxValue means no date (3 of 3 undated). +302 held the same value in every sample, so which of +294 and +302 is start and which is due is unresolved."
}
```

- [`vendor/hxprobe/src/detail.rs` at `4b9f1a53c9742cab8e77a22ab64ed58a392e4e41`](https://github.com/DoctorCRE/carr-system/blob/4b9f1a53c9742cab8e77a22ab64ed58a392e4e41/vendor/hxprobe/src/detail.rs), `parse`.

## Follow-up flag completion date (detail.flag-completed)

```json
{
  "parent": "object.class.00c9.060f",
  "identifier": {
    "kind": "byte_offset",
    "value": 286
  },
  "wire_type": "dotnet_ticks_u64le",
  "decoder": "detail::parse",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Labelled TEST-account sample, 2026-10-28 13:14:58Z and 13:23:16Z research copies, changes made in Outlook on the web: 2026-10-28 00:00 on the one completed flag; DateTime.MaxValue on the 3 others."
}
```

- [`vendor/hxprobe/src/detail.rs` at `4b9f1a53c9742cab8e77a22ab64ed58a392e4e41`](https://github.com/DoctorCRE/carr-system/blob/4b9f1a53c9742cab8e77a22ab64ed58a392e4e41/vendor/hxprobe/src/detail.rs), `parse`.

## One-time move to Other (detail.moved-to-other)

```json
{
  "parent": "object.class.00c9.060f",
  "identifier": {
    "kind": "byte_offset",
    "value": 1406
  },
  "wire_type": "u8",
  "decoder": "detail::parse",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Labelled TEST-account sample, 2026-10-28 13:14:58Z and 13:23:16Z research copies, changes made in Outlook on the web: 1 on the one message moved to Other, 0 on the 3 Focused messages. Only 1 to 2 of about 620 detail objects hold 1 although the work Inbox uses Focused Inbox, so server-assigned Other classification is probably stored elsewhere and is not decoded."
}
```

- [`vendor/hxprobe/src/detail.rs` at `4b9f1a53c9742cab8e77a22ab64ed58a392e4e41`](https://github.com/DoctorCRE/carr-system/blob/4b9f1a53c9742cab8e77a22ab64ed58a392e4e41/vendor/hxprobe/src/detail.rs), `parse`.

## Pinned (detail.pinned)

```json
{
  "parent": "object.class.00c9.060f",
  "identifier": {
    "kind": "byte_offset",
    "value": 1504
  },
  "wire_type": "u8_bit0",
  "decoder": "detail::parse",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Labelled TEST-account sample, 2026-10-28 13:14:58Z and 13:23:16Z research copies, changes made in Outlook on the web: bit 0 set on the one pinned message, clear on the 3 others; no other object in three copies has it set. Bits 0x02 and 0x80 change with read state and are not decoded."
}
```

- [`vendor/hxprobe/src/detail.rs` at `4b9f1a53c9742cab8e77a22ab64ed58a392e4e41`](https://github.com/DoctorCRE/carr-system/blob/4b9f1a53c9742cab8e77a22ab64ed58a392e4e41/vendor/hxprobe/src/detail.rs), `parse`.

## Assigned categories (detail.categories)

```json
{
  "parent": "object.class.00c9.060f",
  "identifier": {
    "kind": "string_run_selector",
    "value": "trailing_category_list"
  },
  "wire_type": "u32le_count_then_u8len_utf16le",
  "decoder": "detail::parse",
  "confidence": "inferred",
  "first_seen_version": null,
  "notes": "Labelled TEST-account sample, 2026-10-28 13:14:58Z and 13:23:16Z research copies, changes made in Outlook on the web: the one categorized message appended u32 count 1, byte length 24 and \"Red category\" at the object end. Parsed only when the list ends the object exactly; names are kept only when a category definition has that name."
}
```

- [`vendor/hxprobe/src/detail.rs` at `4b9f1a53c9742cab8e77a22ab64ed58a392e4e41`](https://github.com/DoctorCRE/carr-system/blob/4b9f1a53c9742cab8e77a22ab64ed58a392e4e41/vendor/hxprobe/src/detail.rs), `parse`.

## Source bindings

```json
{
  "vendor/hxprobe/src/block.rs": {
    "revision": "b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d",
    "sha256": "093617bc8e5aaa9f3597b873dc93539850c202b4531209db69a39de9c8a316d6"
  },
  "vendor/hxprobe/src/header.rs": {
    "revision": "b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d",
    "sha256": "6e774497fed3d077d89437525e0106d2975954675f4db763079991a0556eb3a3"
  },
  "vendor/hxprobe/src/folder.rs": {
    "revision": "b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d",
    "sha256": "af4abd79a95f36a325e0d12e9994df80add60851e9c54bc88cd7a4c6e1c9b6d8"
  },
  "vendor/hxprobe/src/calendar.rs": {
    "revision": "55e33ca4807d082e6bb3f8a3124d8e6aedf58c2d",
    "sha256": "d2f3c52c428ff2e8bd471b848c15f26af1725d620392048078d1b81b3f781c3c"
  },
  "vendor/hxprobe/src/record.rs": {
    "revision": "b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d",
    "sha256": "327cfc0658b26279fc33385e72f01e3f65ae036a35e2822f34a87f30f5968d41"
  },
  "vendor/hxprobe/src/schema.rs": {
    "revision": "b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d",
    "sha256": "a76bd8449dd21c14f987f5d303ec0932e70dcc084c4e672c76721ecc4d963102"
  },
  "vendor/hxprobe/src/body.rs": {
    "revision": "b901f049e28cd3ff57bf4221ee1c0737f3f1cd7d",
    "sha256": "ac7130b31bc06e000598c5acfe019942316900010d8ccb4f95a9be4a651bc499"
  },
  "vendor/hxprobe/src/attachment.rs": {
    "revision": "bc8538ac6845ddd716efa18791361ab04e9c5694",
    "sha256": "796850d32e1169eecd7e70094072b438ccb0530a4b7fa7d5dd70c65edee3461b"
  },
  "vendor/hxprobe/src/category.rs": {
    "revision": "4b9f1a53c9742cab8e77a22ab64ed58a392e4e41",
    "sha256": "1a244e42d2f4ed4a4fc9a3141061ca187aea90c51874bf5e442a88a63d1c9e41"
  },
  "vendor/hxprobe/src/detail.rs": {
    "revision": "4b9f1a53c9742cab8e77a22ab64ed58a392e4e41",
    "sha256": "b7f10a2112bbb8a3d7a4a10fc97f04ff757e2e514caf1fef10aa0929d54c7d58"
  }
}
```
