# Stable message identity research for WR-000502

Status is inconclusive. The repeated local object key is a candidate, not an
established logical message identity. Audit [#2196](https://github.com/DoctorCRE/carr-system/pull/2196)
slice B2 remains blocked. Intake and send retain their existing identity behavior.

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

## Remaining experiment

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
