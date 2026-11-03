# Outlook sync-log agreement report

WR-000442 slice 7 requires Outlook's own sync logs as a second ground truth
for decoded fields, with agreement counts per field. This is distinct from
intake's run receipts and shared-record write counts.

## Evidence and scope

The pinned [upstream SPEC section 6.1](https://github.com/ukd1/hxstore-reverse-engineering/blob/191b54e42757687806202f8f696e9066f0514e11/SPEC.md#61-the-osa-logs-microsofts-own-field-list-verified)
describes gzip XML GetMessage responses under the profile's
`Osa/OutlookServiceApiLogs_*` directories. It establishes field names and types,
roughly ten days of retention, and redacted content. It explicitly limits these
logs to a schema oracle. No hash algorithm or safe way to recover redacted
content is established. The tool never treats redaction as a value agreement.

This implementation accepts explicit copies of gzip response files and a decoded
hxprobe SQLite research database. It does not discover profiles, copy live stores,
read mail automatically, change intake policy, or publish to the record layer.
Only synthetic data was used for development. Real log framing, redaction and
coverage on either partner's Outlook build remain unverified.

## Interface and data shape

`agreement_report(decoded, logs)` returns `outlook-sync-truth/v1` aggregate JSON.
The CLI is `tools/outlook-sync-truth.py`, with required `--decoded` and explicit
`--logs` paths. No files are written. Supply a stable, offline SQLite snapshot
that will not change during the report. SQLite uses `mode=ro&immutable=1` to
avoid creating WAL or SHM sidecars even for a cleanly closed WAL-mode database.
A snapshot with an adjacent `-wal` file is rejected as `decoded_wal_present`
before opening it, so uncheckpointed content is never silently ignored. Prepare
a checkpointed copy outside this tool; the report never checkpoints inputs.
The report contains no message values, identifiers, file paths or exception text.

The organising structure is a field selector table and an outcome histogram.
Each unique supplied header contributes exactly one outcome per field.

| Decoded field | Osa selector | Comparison |
| --- | --- | --- |
| subject | Topic | Exact text |
| preview | Preview | Exact text against the decoder's preview, never its full body |
| sender | From/Address | Exact text |
| sender_name | From/Name | Exact text |
| sent_unix | SentTime | UTC instant from an offset-bearing ISO timestamp or .NET ticks |

Exact text and InternetMessageId retain leading, trailing and internal whitespace.
XML parsing decodes character references and normalizes XML line endings; no
additional text trimming, case folding or Unicode normalization is performed.
Only SentTime strips surrounding whitespace before typed timestamp conversion.
Each selected scalar, including InternetMessageId, must have no child elements.
At most one From parent may supply Name and Address. Duplicate scalar selectors
fail as `duplicate_field`; nested scalars or multiple From parents fail as
`invalid_field_structure`. A structurally invalid file contributes no agreements
and exits nonzero. Absent fields remain explicit missing evidence.

The join requires an unredacted InternetMessageId present exactly once in both
input datasets. Repeated identical selected headers collapse. Conflicting selected
headers with the same ID and multiple decoded rows sharing an ID are ambiguous.
The upstream spec warns that IDs can be reused within conversations. Therefore
this join is evidence within the supplied copies, not a proven durable identity
or a completeness claim about the mailbox. Missing or redacted IDs never join.
No content or time heuristic fills that gap.

Outcomes are `agreed`, `disagreed`, `redacted`, `missing_log`, `missing_decoded`,
`invalid_log`, `unjoined` and `ambiguous`. Missing and redacted log fields are
counted before join failures, so redaction remains visible without an identity.
The report separately counts unreadable files, duplicate headers, unjoined and
ambiguous headers, and decoded rows that could not be compared.

`complete` requires at least one agreement, no other field outcomes, no input
failures and no decoded rows left unexamined. `disagreement` means at least one
comparable value differs. `incomplete` covers empty, redacted, missing or
ambiguous evidence. `failed` takes precedence when any input fails to parse.
The CLI exits zero only for `complete`. These are calculated report results,
not persisted run states. There is no retry lifecycle or shared mutable state.

## Design and verification

A standalone read-only comparison module was chosen over adding Osa discovery
and log processing to scheduled intake. The latter would tie a short-retention,
redacted research source to production scheduling without evidence that the
logs can establish value identity. The field table keeps parsing and comparison
rules local without adding a generic validation framework or compatibility path.

Run `.venv/bin/python ops/outlook-sync-truth-selftest.py`. It constructs all
SQLite rows and gzip XML independently of hxprobe and exercises the real CLI.
The acceptance suite first failed against origin/main because the comparison
module did not exist. Coverage includes field counts, redaction, ambiguity,
missing input, XML/gzip failures, timestamp conversion and output privacy.
CLI regressions cover nested scalar content, split From parents, exact-text
whitespace differences, and closed and uncheckpointed WAL inputs. Both WAL
cases assert the entire input directory's file names and bytes remain unchanged.
CI's gates class discovers the selftest automatically.

No statechart is required for this stateless report. No AI output changes or
new record-layer verbs are introduced. Native sample verification and any
future integration into health or intake require separate authorized work.
