# Native rule projection

The native-rule generator projects stored placement into agent guidance. The record-layer
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
208 generated bodies contain their full statement, including examples and
exceptions. Lossless export of rules 3b0cd546, 49533583, 54e2bcb9,
5d44d3f3, 83b9a362, 9f15f21a, f47a8fe9, 5f8c661d, 76a1ae2b and 8cddc6ad, 23ea0607, 38b15dc6, cff429f5, d7e6de46 is explicitly not doable
under the task's no-client-PII instruction and existing identity guard: their historical examples identify
clients. Their statements are withheld from the snapshot, the manifest names
the exceptions, and generated bodies are visibly marked incomplete with placement
text only. No all-222 lossless pass is claimed. A store-authorized de-identification
is required before those fourteen bodies can be exported verbatim. No local placement or wording override is provided.

CORE loads as a short index from `.claude/rules/carr-core.md` for Claude and the
marked block in root `AGENTS.md` for Codex. Ten rules reference the existing
native K cards without repeating their wording. Two additional short cards
cover authorization refusals and authorized access. Before applying a CORE
rule, agents read its linked section in `docs/generated-rules/core.md` for the
full statement, conditions, and exceptions. This reference is generated from
the same snapshot. UUIDs, versions, and hashes remain indexed in the manifest.

The snapshot records the generated root CORE block's cl100k_base token count
and hash. Offline checks validate that block. The Claude adapter uses the same
index with reference links relative to its directory.

The manifest's `startup_measurement` is the last optional whole-file measurement,
not a drift constraint. Maintained AGENTS.md and CLAUDE.md edits do not require
remeasurement. Run `./run.sh native-rules --measure` with `tiktoken==0.14.0`
installed to refresh it offline. The CLI labels the stored count informational.
The measurement joins root AGENTS.md, CLAUDE.md, and the CORE file in that order.
It counts both CORE copies and does not represent either provider's context.

Every check and regeneration enforces Codex's 32,768-byte project-doc budget
with at least 4,096 bytes of unused capacity in every chain. The generator
counts root AGENTS.md plus each possible nested AGENTS.md chain as UTF-8 bytes
and prints the byte count and headroom for each chain. Tracked maintained files
and newly generated files count. Joe's maintained guidance stays natively in
root AGENTS.md. CLAUDE.md links to its original policy headings.

All write/delete destinations are preflighted before mutation. Paths must be
relative, contain no traversal segments, remain inside the repository after
resolution, and contain no symlink component. Allowed destinations are native
rules, carr-prefixed skills for both adapters, manifest-named nested AGENTS
blocks, the root CORE block, the full CORE reference, the two proposal registers, snapshot and manifest.
Existing skills require both a generated notice and prior manifest ownership.
Directory-relative mutation uses O_NOFOLLOW, including every parent directory.
Foreign guidance outside marked AGENTS blocks is preserved.

Offline CI does not validate live store freshness. Refreshing the authoritative
placement against the current active census remains a credentialed follow-up.
The committed snapshot has 222 placements and 14 withheld statements. Those
counts describe the snapshot, not the current store.

Each UUID has exactly one logical primary home in the manifest. Claude/Codex
copies are native adapters of that home, not additional census entries.
MECHANICAL and MERGE/RETIRE entries are explicit proposal registers, not deployed
enforcement or enacted retirement. All existing guidance remains operative.
Procedure skills use a carr- prefix to avoid replacing existing skills.

PATH frontmatter retains the store's proposed globs. The generator matches them
against `git ls-files -z`, excluding generated AGENTS.md files and carr-prefixed
Codex skills. Each glob has an active or dormant status in the manifest. It stores
no matching file lists. Untracked and ignored files cannot activate a scope or appear as stale outputs.
Stale-output discovery uses tracked paths plus expected and previously owned outputs.
Adding or renaming a tracked file within an active scope requires no regeneration.
Nested Codex sections preserve proposed folders and add an existing literal
directory scope where another proposed glob matches (the .agents folder).
Nested AGENTS scopes are directory-wide and cannot express extension globs.
Every nested rule still states its condition. No doctorcre-app or software-factory
projection is claimed. Creating dormant instruction folders does not prove
that a product surface exists.

## Loading verification

Run `python3 tools/probe_native_rule_loading.py` for two disposable Claude
projects. It copies the generated CORE, room-bridge PATH rule and before-send
skill, creates synthetic text fixtures, and records InstructionsLoaded events.
It requires actual Read and Skill tool calls, CORE at startup, PATH absent
for the unrelated file, and PATH present for the matching file. It uses project
settings only in a temporary repository; production hooks are untouched.
It never sends a message. Authentication and model access must already work.

Run `python3 tools/probe_native_codex_loading.py` for root/nested Codex cwd
probes. It copies the real root AGENTS.md and the
nested room-bridge AGENTS.md. It requires the first CORE ID 179be4b8 and last
CORE ID fa217e48 in both responses, plus 6da539b5 only from the nested cwd.
Codex has no InstructionsLoaded hook in the inspected CLI interface;
these probes check the response's UUIDs and discovered skill name, a weaker
observation than Claude's native events. Codex directory loading is tested
by starting in that directory, not by claiming lazy injection on arbitrary
file reads. Checkpoint body invocation and compaction remain unverified for Codex.

These opt-in CLI probes use provider accounts and are not run by offline CI.
Their `probe_` names keep them outside the gate runner's `test_` discovery.
They test delivery, not a general rule-adherence improvement.

## Required manual compaction test

The following interactive procedure remains the full acceptance target. An unavailable command or missing load event is not a pass. Avoid filling an entire context window merely to force compaction.
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
