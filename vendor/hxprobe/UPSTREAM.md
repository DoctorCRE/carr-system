# hxprobe source and maintenance

This is CARR's maintained copy of the MIT-licensed hxprobe crate from
[ukd1/hxstore-reverse-engineering](https://github.com/ukd1/hxstore-reverse-engineering),
commit `191b54e42757687806202f8f696e9066f0514e11`.
The upstream MIT license is retained in [LICENSE](LICENSE).

The first import commit copies upstream `hxprobe/` verbatim into this directory
and copies the repository's LICENSE. The next commit applies
`vendor-patches/hxprobe-calendar-and-bodies.patch` from CARR commit
`66ac4cc709af5265819e2612aab62d746a9faf85` in PR #2023, with only the path prefix
changed from `hxprobe/` to `vendor/hxprobe/`.

The patch adds calendar events, attendees, body previews, EFMData gzip body
recovery, copy keys, SQLite export columns, and synthetic tests.
The maintenance commit adds CI, this provenance record, a compiler pin, the
build receipt, a target-directory ignore, and example-address normalization
in three source comments. A separate digest-bound leak-guard qualification retains
the public example addresses only in the verbatim historical import. Final
source examples use `@example.test`. No runtime extraction logic changes beyond
the patch.

## CARR changes and security review

SECURITY REVIEW (2026-10-20): Rust, MIT. Dependencies are memmap2, memchr and
rusqlite with bundled SQLite; CARR adds flate2 on its pure-Rust backend.
No networking and no subprocesses anywhere in `src`. Apart from tests, the only
file it writes is the SQLite database named on its command line. Its one
`unsafe` block is the read-only mmap of the input file, which is why the intake
job decodes a copy and never the live store Outlook rewrites. Blocks are
CRC-checked before they are decompressed. Build with `cargo build --release
--locked` so Cargo.lock pins every crate.

WHAT CARR ADDS to `hxprobe db <file> [out.db] [files-dir]`:
- Calendar events (`src/calendar.rs`) in `events` and `event_attendees`: GOID,
  start and end, subject, location, organizer and attendees. Byte-distinct
  candidates remain separate; only exact serialized copies collapse. GOID and
  start are observed grouping fields. Every row exports unsupported lifecycle
  status. Current occurrence state and attendance remain unverified. See
  [calendar lifecycle limits](#calendar-lifecycle-limits).
- Full bodies from EFMData sidecars (`src/body.rs`). These are gzip files at
  `Files/S0/<N>/EFMData/<id>.dat`. `<N>` is 845 on Joe's machine, so every
  `S0/*` folder is read rather than one hard-coded number. A sidecar is
  accepted only when its text contains the record's own preview opening.
- Copy collapse. Revisions of one message are joined by sender, normalized
  subject and preview opening within a day. A preview-only copy then joins the
  one full copy with the same sender and opening. The full body always wins,
  and `copy_key` is stored as the identity the copies share.
- Folders for the 2026-10 Outlook build (WR-000469). Its folder, compact-message
  and metadata envelopes use new tags (`04d8`, `074e`, `02b8`); the old tags stay
  accepted. `folders.parent_status` and `parent_id` come from the parent reference
  at folder object +792, so paths such as Inbox/Active Deals/<deal> resolve. On the
  2026-10-21 TEST-campaign copies: 0 folders before, 236 after (208 with a parent,
  28 top-level), and 2,104 of 2,121 messages linked to a folder.
- Attachment metadata (WR-000469 AC-ATTACH, `src/attachment.rs`). Class `016a`
  objects (tag `0318`) give `attachments`: message key (typed reference at +360),
  name (the cache file name without Outlook's `[<digits>]` suffix), declared MIME
  type, size (+558) and the profile-relative cache path
  `~/Files/S0/<n>/Attachments/<k>/<file>`. `message_keys` maps message rows to the
  local keys of their message objects. No bytes are read; `lib/outlook_attachments.py`
  reads, type-checks by magic bytes, caps and hashes cached files from a private
  copy. On the 2026-10-21 copies: 15,130 objects, 7,017 attachments, 566 linked to
  exactly one message row.
- A `meta` table with block verification, message, collapse, sidecar and event
  counts, which the intake job checks before it trusts a run (at least 95% of
  blocks verified).

Measured on a fresh copy, email from the last seven days:
- Before: 253 unique emails, 79 with a full body (31%).
- After: 164 emails, 80 with a full body (49%), 9 of them from EFMData.
- The other emails are preview-only because Outlook never downloaded their
  body. The evening triage job opens those, so Outlook downloads them.

## Supply chain

Source archive:
`https://codeload.github.com/ukd1/hxstore-reverse-engineering/tar.gz/191b54e42757687806202f8f696e9066f0514e11`

Archive SHA-256:
`bbffd167a42081abc7de2f6ef6d44e14e7af2a7a004d3f060c62eb3c1a45e21c`

This tree is the only decoder source. The import patch was retired after it
drifted from the tree, so the upstream commit, archive hash and git history
here carry the provenance.

Cargo.lock retains all 31 registry package checksums. The crate is copied into
the repository as editable source. Transitive dependencies stay on crates.io;
this is not a `cargo vendor` directory or an offline build.
All maintained test and release build commands use `--locked`.
The compiler is pinned in `rust-toolchain.toml` for rustup installations.
[BUILD-SHA256](BUILD-SHA256) identifies the local release binary and toolchain.
The hash is a build receipt, not a cross-platform reproducibility guarantee.

`cargo-audit 0.22.2 audit --file vendor/hxprobe/Cargo.lock --json` returned
zero vulnerabilities and zero warnings across 32 packages. The RustSec database
contained 1,348 advisories at commit
`e6f94f3760d5e6c54c1b05f204002263bc025524`.
This result covers the checked lockfile against that advisory revision.

## Checks and updates

The `hxprobe` class in `ops/ci.sh` runs `cargo test --locked` followed by
`cargo build --release --locked` from this directory. The PR and main-canary
workflows register that class. A test failure stops the build and fails CI.
The crate's 22 tests use synthetic data. Every example email address uses
`@example.test`. No private Outlook store or profile is needed.

For updates, retain a separate upstream import and CARR change commit, review
the lockfile changes and license, rerun the CI class and cargo-audit, and
refresh BUILD-SHA256 for the recorded target and toolchain.

`bin/install-outlook-intake.sh` builds the installed decoder from this tree with
the same `cargo build --release --locked`, using a target directory under the
settings `vendor` path. It installs the binary only when its SHA-256 equals
line 1 of BUILD-SHA256, so every change under this directory must refresh that
line from a build on the Studio toolchain. `.carr-pin` records the git object
IDs of this directory and the two atlas files; an unchanged pin skips the build.
`ops/hxprobe-install-selftest.py` runs the installer and checks that its binary
equals the in-tree build.

## Source study

- [Upstream README](https://github.com/ukd1/hxstore-reverse-engineering/blob/191b54e42757687806202f8f696e9066f0514e11/README.md).
- [Upstream SPEC](https://github.com/ukd1/hxstore-reverse-engineering/blob/191b54e42757687806202f8f696e9066f0514e11/SPEC.md).
- [Cargo --locked](https://doc.rust-lang.org/cargo/commands/cargo-build.html#option-cargo-build---locked).
- [Cargo vendoring](https://doc.rust-lang.org/cargo/commands/cargo-vendor.html).
- [Cargo test](https://doc.rust-lang.org/cargo/commands/cargo-test.html).
- [RustSec cargo-audit](https://github.com/rustsec/rustsec/tree/main/cargo-audit).
- [Rustup toolchain files](https://rust-lang.github.io/rustup/overrides.html#the-toolchain-file).

The upstream README and SPEC contain inconsistent summaries of deduplication
and version acceptance. This copy preserves the pinned implementation and the
specified CARR patch. Tests establish synthetic behavior, not recovery coverage
for a real Outlook cache or compatibility with future Outlook formats.

## Atlas-backed names

The WR-000439 store-source extension replaces the hand-maintained schema key enum,
label mapping, and tally iteration list with generated definitions from
`spec/outlook/hxstore-atlas.json`. `build.rs` invokes `ops/hxstore-atlas.py` using
Python 3. Python is a build dependency only. No registry dependency was added.
Copy this crate together with those two repository files when building outside this tree.

The extraction selectors and SQLite contracts retain their existing behavior.
`python3 ops/hxstore-atlas-selftest.py` rebuilds a temporary copy after an atlas
rename and checks the actual `map` output using a synthetic block.
The repository gates class discovers this test alongside the existing surface tests.

## Calendar lifecycle limits

The maintained decoder preserves byte-distinct calendar
candidates instead of merging the longest body and largest attendee list.
Exact byte copies collapse only inside this decode. GOID and start remain
observed schedule grouping fields, not an authoritative occurrence identity.

Every exported candidate has `lifecycle_status=unsupported` and
`lifecycle_reason=hxstore_lifecycle_unverified`. The meta field
`calendar_lifecycle_unsupported` counts those candidates. The decoder does not
establish cancellation, deletion, revision order, recurrence exception identity,
attendee response semantics, current schedule state, or attendance. Candidate
bytes stay in decoder memory and are not exported to SQLite.

Synthetic tests prove candidate separation and fail-closed labeling only.
Native decoder validation and controlled test-account format research are still
required before current calendar lifecycle or completeness can be claimed.

Intake stores opaque candidate hashes and provisional schedule timestamps in its
private state. It does not create reminders or contact activities from these
rows. Existing unacknowledged calendar activities and next actions without
established mail provenance remain quarantined with their frozen payloads.
Mail continues independently. Status reasons remain `processed`, `nothing_new`,
or `store_unchanged`. Calendar status appears only in the
`calendar_lifecycle_unsupported` and `calendar_requests_quarantined` counts.
