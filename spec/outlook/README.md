# Outlook format atlas

The surface baseline records static application declarations in [surface-atlas.json](surface-atlas.json).
The [generated reference](surface-atlas.md) renders that JSON without consulting Outlook.
The [WR-000439 criteria](work-request-criteria.json) record the four slices, dependencies,
autonomous design decisions, source study, and remaining acceptance criteria.

The research atlas generator is retired. Saved inventories and findings remain below.

## Data contract

`outlook-surface-atlas/v1` contains these fields.

| Field | Meaning |
| --- | --- |
| `outlook` | Bundle identifier, release version, and build version. |
| `surfaces` | Stable identities mapped to kind, owner, value, confidence, claim, first-seen build, and evidence. |
| `sources` | Relative source paths mapped to SHA-256 and byte length. |
| `skipped_symlinks` | Paths skipped without following their targets. |
| `scope` | The limit on what the inventory establishes. |

Kinds are `url_scheme`, `document_type`, `user_activity`, `supported_intent`, `plugin`,
`app_intent`, and `url_literal`. Plist values retain their declarations, including document roles.
App Intent values retain action metadata and parameters.
Pointers use JSON Pointer escaping. Binary evidence uses file-relative byte offsets and lengths.
The source manifest binds those pointers and offsets to exact files.

`proven` means the declaration or literal exists in those source bytes.
It does not establish that New Outlook implements the route, accepts a parameter, or performs an action.
First seen means the first observation in atlas history, not the Microsoft introduction version.

`outlook-surface-diff/v1` reports `new`, `removed`, and `changed` identities.
Changed entries name the changed fields with before and after values.
`source_changes` reports hashes or sizes separately. `evidence_changed` reports moved pointers or offsets.
Version and skipped-symlink lists are explicit.
Source changes warrant review even when semantic changes are empty.

## Extraction limits

The extractor inventories regular `Info.plist` files and `Metadata.appintents/extract.actionsdata`
files inside the bundle. It scans identified Mach-O files in `MacOS` directories, framework
executables, and `.dylib` and `.so` files. It skips symlink aliases, including framework aliases;
their regular versioned files remain eligible. The skipped paths are part of the atlas.

Binary recovery covers contiguous printable ASCII `ms-outlook://` literals.
Entries group by host and path, union query keys, and retain every matching offset.
The union records observed vocabulary; it does not assert that every key combination is valid.
Query values are omitted. Other encodings, dynamically assembled URLs, and symbol-based reconstruction
remain outside slice 1. The scanner does not invoke any URL.

The criteria file tracks the store atlas, hxprobe name migration, count-only store command,
coverage, combined drift, and launchd trigger design.
The atlas tools do not locate profiles, read stores, install launchd jobs, or write production records.

## Documentation studied

The vendor study binds PR #2038, upstream revision `191b54e42757687806202f8f696e9066f0514e11`,
and the calendar/body patch in PR #2023. The criteria file records their exact revisions.
PR #2040 supplies a static survey to reconcile when it lands.

The extractor uses Python's [plist parser](https://docs.python.org/3/library/plistlib.html)
for XML and binary plists. Declaration meanings follow Apple's
[Core Foundation key reference](https://developer.apple.com/library/archive/documentation/General/Reference/InfoPlistKeyReference/Articles/CoreFoundationKeys.html).

## Store source catalog

[The store atlas](hxstore-atlas.json) catalogs the maintained reader's known layouts.
[The generated store reference](hxstore-atlas.md) includes each entry and its source binding.
`ATLAS-STORE-SOURCE` and `ATLAS-STORE-PROBE` have synthetic verification. Grammar reconciliation remains a separate session.

| Command | Result |
| --- | --- |
| `python3 ops/hxstore-atlas.py render spec/outlook/hxstore-atlas.json` | Generated Markdown reference on stdout. |
| `python3 ops/hxstore-atlas.py rust spec/outlook/hxstore-atlas.json` | Generated Rust decoder keys and display names on stdout. |
| `python3 ops/hxstore-atlas-selftest.py` | Seven synthetic contract and CLI tests, including rebuild after an atlas rename. |
| `./ops/ci.sh --only hxprobe` | Existing synthetic Rust tests and locked release build. |

`outlook-hxstore-atlas/v1` stores stable entry IDs with typed identifiers, names, wire types,
decoders, confidence, evidence, and first-seen Outlook versions. Evidence binds a symbol to a
source file at an exact commit and SHA-256. These bindings establish the catalog's provenance.
They do not establish an Outlook version. A null `first_seen_version` means unknown.

Containers, anchor regions, block types, serialized object classes, envelope tags, byte offsets,
and string selectors are separate identifier kinds. No numeric property-ID framing is established
by the pinned reader. Each relevant record type retains an explicit unknown wire-field entry.
Calendar offsets 574 and 582 are relative to the object body, ten bytes after the envelope starts.
Inferred confidence describes implemented interpretations and heuristics. Unknown means unresolved.
No entry claims empirical proof from a store observation performed by this desk.

Cargo invokes the Python 3 standard-library generator at build time. It tracks the atlas and
generator as build inputs. The generated enum, label mapping, and tally iteration replace their
handwritten definitions in `schema.rs`. `decoder_key` is a stable extraction identity;
`name` is the label shown by `hxprobe map` and `find`. Editing `name` requires rebuilding hxprobe.
There is no runtime override or fallback name table. Invalid atlas input fails the build.
Existing SQLite table and column names are separate derived outputs and retain their contracts.

The synthetic CLI test creates its store bytes in a temporary directory. It does not read an
Outlook app, profile, store, sidecar, or preference. It proves that an atlas rename changes both
the real hxprobe field map and tally. It does not measure decoded-byte or wire-field-ID coverage.
The count-only observation command below supplies the next criterion.

## Count-only store observation

`vendor/hxprobe/target/release/hxprobe observe <immutable-store-copy>` emits
`hxprobe-observation/v1` JSON. The orchestrator supplies the private copy.
This desk uses only generated synthetic fixtures. The command opens one file read-only,
never reads sidecars, and writes only aggregate JSON to stdout.
`python3 ops/hxstore-observe-selftest.py` tests the actual executable.

The report contains block-type counts and decompressed byte counts, record-type counts,
structural field identifiers and wire types, decoded-byte coverage, and unknown remainder groups.
It contains no store values, file paths, record identities, content hashes, or error excerpts.
Unusable input produces empty stdout and exit code 2. Stderr contains only
`{stage, reason_code, block_ordinal}` with fixed codes and a null ordinal for input errors.
The command validates the header, known version, 4096-byte page size, both block CRCs,
exact LZ4 input and output lengths, nonoverlapping blocks, and envelope bounds before emitting JSON.

Record bytes partition each decompressed payload without overlap. Outermost envelope candidates
require marker 5, a zero guard, a bounded length of at least 20, and an eight-byte zero class-signature
suffix. Their denominator includes the envelope. Their decoded spans are the ten-byte envelope
header and ten-byte object-class signature. Nested objects remain opaque.
Mail regions outside envelopes run from an `IPM.Note` anchor to the next anchor or envelope boundary.
Only their sixteen-byte anchor is credited. Other bytes form unframed gaps with zero decoded bytes.
These structural counts are a conservative lower bound. They do not measure the existing heuristic
content decoders or message recovery. Unverified blocks are skipped with a local diagnostic.
A verified block with invalid object bounds contributes its entire payload as unframed bytes.
No partial record interpretations from that block survive.

`coverage` counts signatures, verified blocks, unverified blocks, and verified but undecodable blocks.
Its `decoded_byte_percent` is `100 * decoded_bytes / verified_payload_bytes`. CRC or LZ4 failures
contribute no denominator bytes. Undecodable verified payloads remain in the denominator.
`issues` lists fixed stage/reason codes with one-based signature ordinals; `issue_counts` groups them
by stage and reason. A nested signature inside a verified compressed span is an overlapping candidate,
not a second verified block.

`records[].decoded_byte_percent` is `100 * decoded_bytes / total_bytes` for each observed record type.
Absent record types are omitted. An unavailable denominator is not reported as zero percent.
`wire_field_ids` and `decoded_field_id_percent` are null because numeric property framing remains
unknown. Structural `field_id` labels carry `identifier_kind` to distinguish offsets and selectors
from wire property IDs. No field value reaches output.

`unknown_fields` groups undecoded remainder by record type with a null `field_id` and unknown wire type.
Its occurrence count is the number of records with remainder, not the number of unidentified properties.
Groups sort by descending occurrence count, then ascending record-type ID. Block and record types sort
by ID. The criteria JSON records the exact contract and autonomous design decisions.
`ATLAS-STORE-PROBE` is verified with synthetic fixtures.


## Audited reconstructed grammar

The saved reconciled atlas records the pinned audit evidence.
The manifest records exact PR revisions and observed merge status. PR #2040 is merged.
PRs #2041 and #2042 were open at the pinned heads, so their integration remains provisional.

`url_reconstructed` entries extract routes and action query keys from audited arm64 constants and
traced dispatch. `url_query_vocabulary` lists each host's recognized getter keys separately.
Evidence carries original universal-binary offsets, lengths, VM addresses, range hashes, audit IDs,
and symbols where the audit provides them. Query values and instruction excerpts are omitted.
This is audit-guided reconstruction, not a general disassembler for unexamined builds.

The literal `emails/message/open` and reconstructed `emails/open/message` remain separate entries.
The canonical `emails/send` branch returns false without consuming query keys. IDs belong to
`open/message`; the account keys are `account` and fallback `from`, not `accountID`.
Recognized keys do not establish visible effects. All reconstructed entries have inferred
confidence and untested runtime acceptance. No URL or send probe runs.

Extraction with a reconciled baseline retains the audited grammar only when the exact build and
referenced source hashes match. Changed binaries require a new audit instead of stale grammar.
Diffs separate route/key changes from audit evidence and reconciliation provenance changes.

## Research-only store snapshots on Studio

The research helper copies `HxStore.hxd` and the intake helper's body sidecars
(`Files/S0/*/EFMData/*.dat`) from the fixed Main Identity profile. It accepts no
arguments, runs no other command, and performs no networking or intake. Snapshots
land in `~/carr-local/outlook-research/snap-<UTC timestamp>/`, owned by the user
with mode 0700. Files have mode 0600. `manifest.json` records each source-relative
path, source size and nanosecond mtime, and SHA-256 of the copied bytes. Keep these
private. The shared profile can contain accounts beyond the designated TEST account.
Only TEST-account changes are authorized research experiments. Microsoft Graph
is forbidden for this workflow.

All steps below are required on Studio. The installer renders the job and prints
the two load commands. It does not load or run the helper.

1. Run `./bin/install-outlook-research-copy.sh` from the checkout containing this change.
2. Open System Settings > Privacy & Security > Full Disk Access > +.
3. Select `~/carr-local/bin/outlook-research-copy` using the file dialog's Go to Folder shortcut, then enable its grant.
4. Run the printed `mkdir -p ... && install -m 644 ...` command to place the rendered plist in `~/Library/LaunchAgents/`.
5. Run the printed `launchctl bootstrap gui/$(id -u) ...` command to load the on-demand job.
6. Quit Outlook after the intended TEST-account change has synced, before taking a research snapshot.
7. Run `launchctl kickstart gui/$(id -u)/com.carr.outlook-research-copy` for each snapshot.
8. Check `launchctl print gui/$(id -u)/com.carr.outlook-research-copy` for exit code 0 and a new snapshot containing `manifest.json`.

The job has no schedule and no RunAtLoad. Rebuilding or replacing the binary
requires re-granting Full Disk Access. An unchanged pinned build preserves the
existing executable. The helper publishes a snapshot only after copying succeeds
and retains at most 20 completed snapshots. It prunes only its recognized private
snapshot directories. Unrelated directories and symlinks remain untouched.
Ordinary failures remove staging copies. Forced termination can leave a private
hidden `.snap-*.pending` directory. Prune failures can leave hidden
`.snap-*.retired` directories. Neither is a published study input. A clock rollback
that would place a new snapshot before an existing snapshot is refused.
Copies are sequential, not a transactional backup of a running Outlook profile.
A source file changing during its copy is rejected; changes between separate
file copies cannot be ruled out. Keep Outlook closed throughout capture.

Use the Rust decoder in `vendor/hxprobe` and the private immutable copies described
in [the harness contract](hxstore-harness.json). Compare consecutive copies after
one TEST-account action and capture an unchanged control. The retention limit can
remove a study baseline, so preserve required study inputs separately before
20 newer captures. Store bytes and sidecars remain local; snapshot creation never
writes into Outlook or produces production records.
