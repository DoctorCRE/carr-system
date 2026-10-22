# Outlook format atlas

Slice 1 records static application declarations in [surface-atlas.json](surface-atlas.json).
The [generated reference](surface-atlas.md) renders that JSON without consulting Outlook.
The [WR-000439 criteria](work-request-criteria.json) record the four slices, dependencies,
autonomous design decisions, source study, and remaining acceptance criteria.

## Commands

Run these commands from the repository root with Python 3.10 or newer.
Every command writes only to stdout. Shell redirection chooses the output file.
The extractor opens bundle inputs read-only and never launches an application.

| Command | Result |
| --- | --- |
| `python3 ops/outlook-atlas.py extract --version 16.114.26101123` | JSON inventory of the installed bundle. |
| `python3 ops/outlook-atlas.py drift --version 16.114.26101123 --baseline spec/outlook/surface-atlas.json` | Regenerated inventory compared with the baseline as structured JSON. |
| `python3 ops/outlook-atlas.py diff old.json new.json` | Comparison of two saved inventories. |
| `python3 ops/outlook-atlas.py render spec/outlook/surface-atlas.json` | Generated Markdown reference. |
| `python3 ops/outlook-atlas-selftest.py` | Synthetic module and CLI behavior tests. |

`--app` selects another static app bundle. `--version` must match its full `CFBundleVersion`.
On `extract`, optional `--baseline` preserves first-seen versions for existing identities.
New identities receive the observed build. Without a baseline, extraction starts a new history.
The version is an expectation check, not a request to download an older Outlook build.

Exit code 0 means the command completed, including when drift exists.
Consumers inspect the diff rather than interpreting code 0 as no drift.
Invalid versions, metadata, schemas, or unreadable inputs produce code 2 and a fixed JSON error
on stderr. They emit no partial atlas or source excerpts.

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

The store atlas, hxprobe name migration, count-only store command, decoded-byte and field-ID
coverage, combined drift, and launchd trigger design remain the named WR-000439 criteria.
No store reader, profile locator, launchd installation, or production writer is present here.

## Documentation studied

The vendor study binds PR #2038, upstream revision `191b54e42757687806202f8f696e9066f0514e11`,
and the calendar/body patch in PR #2023. The criteria file records their exact revisions.
PR #2040 supplies a static survey to reconcile when it lands.

The extractor uses Python's [plist parser](https://docs.python.org/3/library/plistlib.html)
for XML and binary plists. Declaration meanings follow Apple's
[Core Foundation key reference](https://developer.apple.com/library/archive/documentation/General/Reference/InfoPlistKeyReference/Articles/CoreFoundationKeys.html).
