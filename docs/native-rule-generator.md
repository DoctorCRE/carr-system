# Native rule projection

WR675.S11.J6 adds native guidance beside existing delivery. The record-layer
store remains authoritative. No rule adoption, retirement, hook removal or model
checker is performed.

Run all three commands to refresh and verify. Live refresh needs the existing
record-verb authentication and Python with `tiktoken==0.14.0` installed.

1. Run `./run.sh native-rules --from-store` to capture current placement and active statement hashes.
2. Run `./run.sh native-rules --check` to verify deterministic output against the committed snapshot.
3. Run `python3 tools/test_native_rules.py` to verify coverage and false-success cases.

`--from-store --check` also checks live snapshot freshness without writing.
CI's artifact class runs the offline check and behavioral tests. It does not
contact production or claim the snapshot remains current forever.

The snapshot contains the live statements verbatim alongside placement prose and
UUID/hash inventory. Every statement hash must agree with the placement table.
212 generated bodies contain their full statement, including examples and
exceptions. Lossless export of rules 3b0cd546, 49533583, 54e2bcb9,
5d44d3f3, 83b9a362, 9f15f21a, f47a8fe9, 5f8c661d, 76a1ae2b and 8cddc6ad is explicitly not doable
under the task's no-client-PII instruction and existing identity guard: their historical examples identify
clients. Their statements are withheld from the snapshot, the manifest names
the exceptions, and generated bodies are visibly marked incomplete with placement
text only. No all-222 lossless pass is claimed. A store-authorized de-identification
is required before those ten bodies can be exported verbatim. No local placement or wording override is provided.

CORE loads from `.claude/rules/carr-core.md` for Claude and the marked block in
root `AGENTS.md` for Codex. Loaded headings contain only eight-character IDs;
UUIDs, versions and full hashes are indexed in the manifest. UUIDs occurring
inside a live statement remain verbatim. Startup measurements use tiktoken
0.14.0 and cl100k_base, with exact content hashes. These are file counts, not
provider context totals or an adherence benchmark.

WR675.S11.J10 measured the reviewed head 62b28f926 and the repaired files with
the same tokenizer. Marked CORE was 1,160 tokens and is now 3,772. Lossless
wording increases the total despite removing metadata. With the same full
wording and old long metadata, CORE would be 4,467 tokens; compact metadata
saves 695 tokens. The old 1,500-token ceiling cannot coexist with the required
twelve full statements. The snapshot explicitly records the over-budget result;
no claim of meeting that ceiling remains.

The full chain is the newline-joined files in this order. It deliberately counts
both CORE copies because the requested chain includes both, without asserting
that either provider loads all three files.

| File | Reviewed head tokens | Repaired tokens |
|---|---:|---:|
| Root AGENTS.md | 5,652 | 8,264 |
| CLAUDE.md | 1,927 | 1,927 |
| CORE file | 1,146 | 3,758 |
| Full joined chain | 8,725 | 13,949 |

Offline checks validate the exact startup file hashes against the manifest.
Live refresh remeasures with tiktoken.

All write/delete destinations are preflighted before mutation. Paths must be
relative, contain no traversal segments, remain inside the repository after
resolution, and contain no symlink component. Allowed destinations are native
rules, carr-prefixed skills for both adapters, manifest-named nested AGENTS
blocks, the root CORE block, the two proposal registers, snapshot and manifest.
Existing skills require both a generated notice and prior manifest ownership.
Directory-relative mutation uses O_NOFOLLOW, including every parent directory.
Foreign guidance outside marked AGENTS blocks is preserved.

CI continues to run offline `--check`. Inspection of all `.github/workflows`
files found GitHub, deployment and database credentials, but no authenticated
record-layer read identity usable by `--from-store`. Database read credentials
are not record-layer authorization. Follow-up for WR675.S11 store freshness:
connect live checking to an existing authenticated record-layer CI lane once
one exists. This slice adds no secret or workflow. Live freshness was checked
locally using the existing machine actor.

Each UUID has exactly one logical primary home in the manifest. Claude/Codex
copies are native adapters of that home, not additional census entries.
MECHANICAL and MERGE/RETIRE entries are explicit proposal registers, not deployed
enforcement or enacted retirement. All existing guidance remains operative.
Procedure skills use a carr- prefix to avoid replacing existing skills.

PATH frontmatter retains the store's proposed globs. The manifest records actual
matching files and every unmatched glob, explicitly marked dormant and excluded
from active_glob_count. Fourteen proposed globs match nothing
in carr-system; their native rules remain dormant until matching files exist.
Nested Codex sections preserve proposed folders and add an existing literal
directory scope where another proposed glob matches (the .agents folder).
Nested AGENTS scopes are directory-wide and cannot express extension globs.
Every nested rule still states its condition. No doctorcre-app or software-factory
projection is claimed. Creating dormant instruction folders does not prove
that a product surface exists.

## Loading verification

Run `python3 tools/test_native_rule_loading.py` for two disposable Claude
projects. It copies the generated CORE, room-bridge PATH rule and before-send
skill, creates synthetic text fixtures, and records InstructionsLoaded events.
It requires actual Read and Skill tool calls, CORE at startup, PATH absent
for the unrelated file, and PATH present for the matching file. It uses project
settings only in a temporary repository; production hooks are untouched.
It never sends a message. Authentication and model access must already work.

Run `python3 tools/test_native_codex_loading.py` for root/nested Codex cwd
probes. Codex has no InstructionsLoaded hook in the inspected CLI interface;
these probes check the response's UUIDs and discovered skill name, a weaker
observation than Claude's native events. Codex directory loading is tested
by starting in that directory, not by claiming lazy injection on arbitrary
file reads. Checkpoint body invocation and compaction remain unverified for Codex.

These opt-in CLI probes use provider accounts and are not run by offline CI.
They test delivery, not a general rule-adherence improvement.

## Required manual compaction test

The following interactive procedure remains the full acceptance target. Headless
attempt results are recorded below; an unavailable command or missing load event
is not a pass. Avoid filling an entire context window merely to force compaction.
All steps apply.

1. Start `claude` in the checkout and record its version.
2. Run `/context` and confirm `carr-core.md` under Memory files.
3. Ask Claude to read `tools/room-bridge/brief-footer.md` with Read.
4. Confirm the loaded rule names short ID `6da539b5`.
5. Run `/carr-before-send` for a synthetic draft and confirm the skill body loads.
6. Run `/compact`.
7. Run `/context` and confirm CORE is present again.
8. Ask Claude to read `tools/room-bridge/brief-footer.md` again.
9. Confirm the PATH rule reloads and preserves its doctorcre-app exception.
10. Record version, CORE/PATH load events and assertions without saving private conversation text.

For Codex, all steps apply separately.

1. Start `codex` in `tools/room-bridge` and record its version.
2. Ask for the already-loaded CORE and room-bridge short IDs without reading guidance files.
3. Invoke `$carr-before-send` on a synthetic draft and confirm the skill body loads.
4. Run `/compact`.
5. Ask for both short IDs and the room-bridge exception again without reading guidance files.
6. Restart Codex at the repository root and confirm the room-bridge rule is absent.
7. Record the assertions and CLI version, marking unavailable UI commands as unverified.

Claude formats and lifecycle are documented in
[Memory](https://code.claude.com/docs/en/memory),
[Skills](https://code.claude.com/docs/en/skills), and
[InstructionsLoaded](https://code.claude.com/docs/en/hooks#instructionsloaded).
PATH rules reload on matching access after compaction; skill bodies must be
invoked again. Neither a file-presence test nor a model's agreement proves this.

## WR675.S11.J10 adapter acceptance results

Claude Code 2.1.316 passed both startup probes. The matching probe then resumed
its own synthetic session with `claude -p /compact --resume <session-id>`.
One `compact_boundary` event was observed. After requesting a fresh Read,
InstructionsLoaded reported `carr-core.md` with `session_start` and
`rule-6da539b5.md` with `path_glob_match`; the fixture was actually read and
`carr-before-send` invoked again. Both files were unchanged copies of generated
full statements, including the room-bridge doctorcre-app exception. These are
loader and tool observations, not proof of general model adherence. An earlier
attempt reused summarized fixture content without a new Read and therefore
failed reload acceptance; the final probe explicitly requires a fresh Read.
`tools/test_native_rule_loading.py` now reports these compaction assertions.

Codex CLI 0.166.0 passed root and nested cwd loading probes with short IDs and
checkpoint name discovery. Noninteractive `codex exec --json --sandbox read-only
/compact` treated the slash command as a model prompt and responded that it could
not manually compact. `codex exec --help` exposes resume/fork/review but no
compaction control. The interactive procedure above is not doable through that
exec interface; Codex compaction, post-compaction reload and checkpoint-body
invocation remain unverified. No successful acceptance is claimed for them.

Local verification passed 12 generator tests, offline checks, both loading scripts, and `ops/ci.sh --only artifact`. The first artifact
attempt failed because the new worktree lacked locked Node dependencies; after
`npm ci --prefix mcp-server --ignore-scripts`, the artifact class passed. No gate
was bypassed.

## Later removal slice

A later separately authorized slice can replace the legacy rule-lookup delivery,
the old generated core cards and duplicated CLAUDE.md/AGENTS.md prose after
behavior comparisons and missing mechanical controls are complete. adopt-rule
continues to own store adoption. This PR leaves it and all existing hooks intact.

Final live freshness recheck refused capture with `active rule coverage differs
from placement`. The store changed during the job: 202 active IDs now differ
from the 222-ID placement snapshot (13 added, 33 removed). The earlier live
check passed before that change. Current live freshness is FAILED, not passed.
Updating the authoritative placement tables for that new census is outside this
repair and cannot preserve the task's specified twelve CORE entries unchanged.
The committed snapshot retains the reviewed census; offline consistency passes.

The commit hook's existing identity guard detected five additional protected
identity statements beyond the two manually found examples. All ten are
withheld; no scanner exemption or hook bypass was introduced.

The pre-push client-data guard found three more source-restricted statements.
Ten statements are withheld in total; 212 are exported verbatim. The unpublished
repair commit was amended to remove them before any upload. No guard was bypassed.
