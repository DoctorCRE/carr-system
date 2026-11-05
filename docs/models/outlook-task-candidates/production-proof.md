# Prove the installed held task census

Run this procedure after deployment and installation. It does not install,
refresh, schedule, send, or admit anything. A failed command means BLOCKED.
The observer checks a diagnostic receipt, not trusted receipt ingestion.

The proof operator supplies these inputs from the enrolled observation job.
Do not discover another profile or substitute another account.

- `INSTALLED_RUNTIME` is the installed carr-system checkout.
- `MERGED_CHANGE_SHA` is the full merge commit for this PR. A descendant installed release is valid.
- `IMMUTABLE_COPY` is the explicit supported immutable HxStore copy.
- `ACCOUNT_KEY`, `STORE_GENERATION`, and `OUTLOOK_BUILD` identify that enrolled copy.
- `PROOF_DIR` is a new private directory outside the checkout. It must not exist yet.

Execute the entire block with bash. Every command is required. Keep the database,
source paths, and local logs private. Export only the count-only service receipt.

```bash
set -euo pipefail
: "${INSTALLED_RUNTIME:?}" "${MERGED_CHANGE_SHA:?}" "${IMMUTABLE_COPY:?}"
: "${ACCOUNT_KEY:?}" "${STORE_GENERATION:?}" "${OUTLOOK_BUILD:?}" "${PROOF_DIR:?}"
cd "$INSTALLED_RUNTIME"
test -z "$(git status --porcelain --untracked-files=no)"
RUNTIME_SHA=$(git rev-parse HEAD)
git merge-base --is-ancestor "$MERGED_CHANGE_SHA" "$RUNTIME_SHA"
mkdir "$PROOF_DIR"
PROOF_DIR=$(cd "$PROOF_DIR" && pwd)
cargo run --locked --release --manifest-path vendor/hxprobe/Cargo.toml -- task-candidates --immutable-copy "$IMMUTABLE_COPY" "$PROOF_DIR/task-census.db" "$ACCOUNT_KEY" "$STORE_GENERATION" "$OUTLOOK_BUILD" "$RUNTIME_SHA" > "$PROOF_DIR/decoder-counts.json"
python3 tools/outlook-task-evidence.py "$PROOF_DIR/task-census.db" "$ACCOUNT_KEY" "$STORE_GENERATION" --studio-receipt > "$PROOF_DIR/task-census.receipt"
git show f699e1859dc87e4122501bbaba35a312607452a7:ops/studio-proof-observe.py > "$PROOF_DIR/studio-proof-observe.py"
python3 "$PROOF_DIR/studio-proof-observe.py" --receipt-file "$PROOF_DIR/task-census.receipt" --service outlook-task-evidence --runtime-sha "$RUNTIME_SHA" --count blocks_verified --minimum 1
```

Use the exact reader revision registered for `studio-run-receipt.v1`.
Do not fall back if that revision is unavailable. The final command must print
`Studio proof OBSERVED: expected runtime and count predicate matched; no trusted receipt ingested`
and exit zero. Bound the entire execution to the declaration's 300 seconds.

The producer validates the existing database through `intake_evidence` before
emitting the service envelope. That independently enforces exact zero for
admissions, supported candidates, pending reviews, and acknowledged writes.
The receipt's runtime SHA comes from the producer's checkout, not a CLI claim.
The producer rejects changed decoder or validator sources and candidate revision
mismatches. The fresh `cargo run --locked` command binds the decoder execution
to that checkout even when a cached installed binary is stale. The producer
alone cannot authenticate an arbitrary preexisting database. The fresh run and
independent enrollment checks are required, including when no candidates exist.

Read `decoder_incomplete`, `block_failures`, `unreadable_candidates`,
`account_unknown`, and `candidates_deferred` in the count-only receipt.
Nonzero values keep absence and full coverage unproven. No task may enter CARR.
One verified block proves a census ran over readable bytes, not that a natural
task format was identified. Keep the product landed-unproven until this procedure
runs on Studio. Synthetic tests prove only the interface.
