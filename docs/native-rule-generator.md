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

The snapshot contains the store's placement prose and active UUID/hash inventory,
never raw historical examples, human quotes or contact data. Source versions
come from the placement table; full statement SHA-256 equality is checked against
the live catalog before capture. An amendment requires updating placement in
the store before regeneration. The renderer has no local placement override.

CORE loads from `.claude/rules/carr-core.md` for Claude and an additive marked
block in root `AGENTS.md` for Codex. The printed count covers the complete new
CORE chain including markers, notice, UUIDs, versions and hashes. It excludes
unchanged legacy startup guidance and separately discovered skill listings.
The reference tokenizer is cl100k_base; no Claude-native count is claimed.
Offline checks validate the measurement's exact content digest; live refresh
remeasures with tiktoken. The store snapshot is a committed input, not a second
authoring home.

Each UUID has exactly one logical primary home in the manifest. Claude/Codex
copies are native adapters of that home, not additional census entries.
MECHANICAL and MERGE/RETIRE entries are explicit proposal registers, not deployed
enforcement or enacted retirement. All existing guidance remains operative.
Procedure skills use a carr- prefix to avoid replacing existing skills.

PATH frontmatter retains the store's proposed globs. The manifest records actual
matching files and every unmatched glob. Fourteen proposed globs match nothing
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
file reads. Checkpoint body invocation and compaction remain manual for Codex.

These opt-in CLI probes use provider accounts and are not run by offline CI.
They test delivery, not a general rule-adherence improvement.

## Required manual compaction test

Automated headless Claude cannot invoke /compact through Skill (the documented
CLI excludes that built-in command). Avoid spending a full context window to
force auto-compaction. The following interactive acceptance procedure remains
unexecuted and is required before claiming compaction coverage. All steps apply.

1. Start `claude` in the checkout and record its version.
2. Run `/context` and confirm `carr-core.md` under Memory files.
3. Ask Claude to read `tools/room-bridge/brief-footer.md` with Read.
4. Confirm the loaded rule names UUID `6da539b5-84e9-49d1-84d8-28f05e151b20`.
5. Run `/carr-before-send` for a synthetic draft and confirm the skill body loads.
6. Run `/compact`.
7. Run `/context` and confirm CORE is present again.
8. Ask Claude to read `tools/room-bridge/brief-footer.md` again.
9. Confirm the PATH rule reloads and preserves its doctorcre-app exception.
10. Record version, CORE/PATH load events and assertions without saving private conversation text.

For Codex, all steps apply separately.

1. Start `codex` in `tools/room-bridge` and record its version.
2. Ask for the already-loaded CORE and room-bridge UUIDs without reading guidance files.
3. Invoke `$carr-before-send` on a synthetic draft and confirm the skill body loads.
4. Run `/compact`.
5. Ask for both UUIDs and the room-bridge exception again without reading guidance files.
6. Restart Codex at the repository root and confirm the room-bridge rule is absent.
7. Record the assertions and CLI version, marking unavailable UI commands as unverified.

Claude formats and lifecycle are documented in
[Memory](https://code.claude.com/docs/en/memory),
[Skills](https://code.claude.com/docs/en/skills), and
[InstructionsLoaded](https://code.claude.com/docs/en/hooks#instructionsloaded).
PATH rules reload on matching access after compaction; skill bodies must be
invoked again. Neither a file-presence test nor a model's agreement proves this.

## Later removal slice

A later separately authorized slice can replace the legacy rule-lookup delivery,
the old generated core cards and duplicated CLAUDE.md/AGENTS.md prose after
behavior comparisons and missing mechanical controls are complete. adopt-rule
continues to own store adoption. This PR leaves it and all existing hooks intact.
