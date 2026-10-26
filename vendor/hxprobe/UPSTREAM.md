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

## Supply chain

Source archive:
`https://codeload.github.com/ukd1/hxstore-reverse-engineering/tar.gz/191b54e42757687806202f8f696e9066f0514e11`

Archive SHA-256:
`bbffd167a42081abc7de2f6ef6d44e14e7af2a7a004d3f060c62eb3c1a45e21c`

Patch SHA-256:
`a1ca5a3dd93a46216c9a88b42c917f83b87f23a4d1152bfc2bab36279416293a`

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

For updates, retain a separate upstream import and CARR patch commit, review
the lockfile changes and license, rerun the CI class and cargo-audit, and
refresh BUILD-SHA256 for the recorded target and toolchain.
The Outlook intake installer remains outside this slice; its source switch
follows PR #2023.

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

The maintained decoder and installer patch preserve byte-distinct calendar
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

An object whose NUL-terminated fields run to its end, with no attendee count,
decodes with no attendees. The 2026-10-21 TEST-account study pair showed such an
appointment added one GOID/start identity and, before this, no decoded candidate.

Intake stores opaque candidate hashes and provisional schedule timestamps in its
private state. Separately it publishes the next fourteen days of meetings that
name the configured account in every stored copy as a display snapshot (board
`calendar-agenda`) for the morning brief, with no notes or bodies.

Observed on the 2026-10-21 study copies (Joe's shared profile, not yet the TEST
account): each recurring occurrence is a separate object whose Global Object ID
carries its local instance date in bytes 16-19 (111 of 111 rows matched their
start date); the series master keeps zero there. Only one ID appeared at two
starts, which reads as a moved meeting. Not decoded yet, so the snapshot always
warns about them: attendee responses (declines), deleted-items or folder
membership, the owning account, revision order, and whether every occurrence of
a series is materialized. It does not create reminders or contact activities from these
rows. Existing unacknowledged calendar activities and next actions without
established mail provenance remain quarantined with their frozen payloads.
Mail continues independently. Status reasons remain `processed`, `nothing_new`,
or `store_unchanged`. Calendar status appears only in the
`calendar_lifecycle_unsupported` and `calendar_requests_quarantined` counts.
