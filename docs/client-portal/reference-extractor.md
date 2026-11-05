# Reference folder extractor

`tools/portal-reference-extract.py` converts an explicitly trusted local source
folder into a `carr.market-study.v1` package and content-addressed files. This is
WR227 S7's one-off migration tool. It neither publishes nor contacts a site.
The coordinator must still prove extraction against both authorized Studio
reference copies. The synthetic fixtures prove no client parity.

## System design

The public interface is one CLI with absolute source and output paths. A mapping
inside the source folder names source bindings, source JSON pointers, package
destinations, and asset paths. Python owns extraction and file handling. The
repository's pinned Babel parser reads JavaScript syntax without evaluating it.
The existing `validateMarketStudyPackage` owns package validation. Its exported
`RECORD_LAYER_GROUPS` owns partial-mode waivers.

Two designs were considered before implementation. Fixed reference-specific
converters would encode private assumptions without access to the reference
copies. Explicit mappings make those assumptions inspectable on the Studio and
let missing paths fail. The extractor therefore uses explicit mappings, with no
fallback values or heuristic conversion. The inventory comes from the existing
parity probes, rather than a second copy of the inventory rules.

There is no persistent state machine. A run reads, checks, and stages output in
a private sibling directory. It renames that directory only after all checks
pass. Failed extraction leaves no package directory. Output must be new, outside
the repository, and separate from the source. Directory permissions are 0700;
output files are 0600. Repeat runs use separate directories and produce identical
bytes for identical inputs and the same repository contracts.

## Command reference

The command requires `--source` and `--output`, both absolute paths.
`--mapping` defaults to `extract.json`, relative to the trusted source.
`--mode` is `full` by default or `partial` for research packages.
The output parent directory must already exist. Existing output is refused.
Node and dependencies installed from `mcp-server/package-lock.json` are required.

A successful run prints only counts and SHA256 hashes of the two private JSON
artifacts. A failed run exits 2 with `error_count` and offending paths. Source
values and parser diagnostics are suppressed. No source file is imported,
executed, or fetched remotely. Source file paths must stay within the folder;
symlinks in a referenced path, including directory symlinks, are refused.

The output contains `market-study.json`, `manifest.json`, and `files/<sha256>`.
The manifest lists asset fields, original relative paths, hashes, and output
paths. It also lists inventory mappings and their source provenance, input
hashes, and hashes of the validator, schema, dependency lock, and probe inventory.
The manifest is private because its paths can identify client material.

## Mapping reference

The committed synthetic mappings are in
[`client-portal/fixtures/reference-extractor`](../../client-portal/fixtures/reference-extractor).
They illustrate the mapping format, not verified bindings for the private copies.
A Studio mapping must use the actual bindings and leaf destinations of each
reference. Whole-object mappings work only when the object's shape already
matches the destination. Array elements can instead be mapped individually with
numeric pointer segments. No array reordering or guessed field renaming occurs.

A mapping has these fields:

- `site` is `REF-A` or `REF-V` and selects the corresponding inventory.
- `sources` maps local names to `file` and, for JavaScript, `binding`.
  JSON sources omit `binding`. JavaScript bindings can be variable names or
  dotted assignments such as `window.referenceData`. For inline app values,
  `ast_pointer` can replace `binding`. It selects a literal AST node by JSON
  pointer within Babel's Program node, for example
  `/body/0/expression/arguments/0/properties/0/value`. Such mappings bind to the
  exact source shape and fail when that location is absent or nonliteral.
- `fields` maps destination JSON pointers to `{ "source": "name", "pointer": "/path" }`.
  An empty source pointer selects the entire literal. Parent mappings precede
  child overrides. A null entry omits that destination; it does not waive checks.
- `assets` lists `{ "target": "/package/field", "file": "assets/photo.jpg", "type": "image/jpeg" }`.
  Each target must already exist. The extractor replaces its descriptor with the
  actual hash, basename, and specified media type. Every final asset descriptor
  needs a source mapping. Identical bytes share one output file.
- `ignore` maps `file:binding` to a nonempty reason for ignoring a template
  binding. Every discovered binding in a parsed source needs either a source
  declaration or an explicit ignore reason. The coordinator must verify that
  ignored bindings contain no per-deal values.

The parser accepts strings, finite numbers, booleans, null, arrays, ordinary
object properties, and negative numeric literals. It refuses functions, calls,
spreads, computed properties, shorthand, template expressions, duplicate object
keys, and ambiguous repeated bindings. It parses all assignments and variable
declarations, including nested ones, so shadowed names fail rather than choosing
an arbitrary value. A mapped source is never executed, even when unrelated
executable statements appear in that file. JSON source objects use strict JSON.

Every applicable D or T+D probe field must resolve in the extracted package.
Wildcards require at least one match, allowing optional fields on only some
items. Retired rows and rows explicitly covered by AD4 are recorded as excluded.
Missing ordinal fields fail; small synthetic arrays cannot silently stand in for
larger inventories. In partial mode, only absent groups exported by the existing
validator can be waived. Present record-layer groups still validate. Ownership
remains optional in the schema; an inventory that requires ownership still needs
it. Flexible-page doctrine governs page composition; this tool imposes no new
page requirements.

## Synthetic verification and remaining production proof

The two synthetic source folders model local JavaScript data assignments and
app constants, with ownership present in REF-A and absent in REF-V. All names,
figures, links, and asset bytes are invented. Asset bytes are placeholders, not
renderable client media. The fixtures expand ordinal arrays to exercise the
existing inventory without asserting correspondence to the private sources.

Verification commands are `python3 tools/test_portal_reference_extract.py`,
`node --test mcp-server/test/market-study-package.test.mjs`, and
`./ops/ci.sh --only gates`. The Python tests invoke the real CLI, which calls the
existing validator. No second package schema is implemented in Python.

The coordinator's production proof runs this CLI read-only against the two
explicitly authorized Studio copies, with reviewed private mappings and separate
new private output directories. Both full packages must validate. Every
applicable inventory row must map, and excluded rows must match the existing
allowed differences. The coordinator retains private manifests and package
hashes and reports counts and hashes only. No publishing, migrations, live site
requests, or client data in Git are part of this proof.

The proof declaration is
[`wr227-s7-reference-extractor.json`](../../proofs/requirements/wr227-s7-reference-extractor.json).
Its registered Studio receipt observer can check a successful receipt for the
exact runtime SHA with `counts.extraction.validated_reference_packages` at least
2, using service `portal-reference-extract`. A receipt must represent both real
runs and their complete mappings. The observer alone cannot establish the
receipt's provenance or substitute for those runs. This worker does not create a
production receipt. Production extraction remains unproven until the coordinator
runs it on the Studio.
