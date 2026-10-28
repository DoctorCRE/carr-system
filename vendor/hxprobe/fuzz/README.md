# Synthetic parser fuzzing

The `block`, `lz4`, and `record` cargo-fuzz targets accept arbitrary bytes.
Returning a decode error is allowed. A panic fails the run.
They import the production parser files and share their byte drivers with
`tests/parser_regressions.rs`. No mailbox, profile, credentials, or live ports
are needed. Use synthetic corpus inputs only.

The block driver checks arbitrary offsets, offset zero, and scanned block
signatures. The LZ4 driver varies the declared output length independently
of the available input size. The record driver checks arbitrary anchors,
field windows, located message anchors, HTML, and timestamps.

## Stable checks

From `vendor/hxprobe`, run all required tests with `cargo test --locked`.
The default fuzz test feeds 32,768 inputs to each parser from a fixed seed.
It mixes random bytes with mutated synthetic records, literal LZ4 blocks,
back-references, and continuation lengths. Half of the mutated block cases
refresh their CRCs to exercise decoding beyond checksum validation.
The adjacent valid-input test checks literal decoded bytes and message output.

For a longer run with a different fixed seed, run
`cargo test --locked --test parser_regressions sustained_inputs_do_not_panic -- --ignored --nocapture`.
This feeds 1,048,576 inputs to each parser.
The focused regression tests contain minimal synthetic inputs for offset,
capacity, anchor, and field-window overflows.

## Cargo-fuzz checks

With cargo-fuzz and a nightly toolchain installed, run `cargo fuzz list` from
`vendor/hxprobe`. The targets are `block`, `lz4`, and `record`.
Run each with `cargo +nightly fuzz run block`, replacing `block` with the
other target names for separate runs. Commit a minimized synthetic regression
and a failing test before fixing any newly discovered crash.

Build artifacts, coverage, corpora, and crash artifacts are generated data.
Keep them outside the source tree when refreshing the source receipt.
A compile-only check on stable is
`cargo check --locked --manifest-path fuzz/Cargo.toml --target-dir target/fuzz --bins`.
This checks the targets but does not replace a coverage-guided fuzz run.

## Delivery

The stable fallback ran because cargo-fuzz and rustup were unavailable in the
job sandbox. The normal test suite retains the fallback for CI.
The installed decoder changes only after the orchestrator re-runs
`bin/install-outlook-intake.sh` from the merged source and verifies the next
scheduled intake run. Parser changes do not rebuild the Full Disk Access helper.
