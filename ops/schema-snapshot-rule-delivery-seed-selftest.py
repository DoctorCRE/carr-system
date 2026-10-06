#!/usr/bin/env python3
"""Pin scoped rule-delivery reconstruction to bounded, exact source data."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = (ROOT / "bin" / "schema-snapshot.sh").read_text(encoding="utf-8")
SNAPSHOT = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
MIGRATION_SOURCE = (ROOT / "migrations" / "0317_atomic_rule_delivery_cutover.sql").read_text(
    encoding="utf-8"
)
REFRESH_MIGRATION_SOURCE = (
    ROOT / "migrations" / "0332_refresh_rule_delivery_activation_preimage.sql"
).read_text(encoding="utf-8")
CURRENT_MIGRATION_SOURCE = (
    ROOT / "migrations" / "0363_rule_delivery_activation_digest_repin.sql"
).read_text(encoding="utf-8")
POLICY_MIGRATION = "0291_rule_delivery_layers.sql"
CUTOVER_MIGRATION = "0317_atomic_rule_delivery_cutover.sql"
REFRESH_MIGRATION = "0332_refresh_rule_delivery_activation_preimage.sql"
SECOND_REFRESH_MIGRATION = "0348_pr_only_main_ruleset_control.sql"
SECOND_REFRESH_MIGRATION_SOURCE = (
    ROOT / "migrations" / "0348_pr_only_main_ruleset_control.sql"
).read_text(encoding="utf-8")
CURRENT_MIGRATION = "0363_rule_delivery_activation_digest_repin.sql"
SOURCE_MERGE_MIGRATION = "0471_source_merge_catalog_registry_successor.sql"
TAIL_REPIN_MIGRATION = "0478_repin_rule_delivery_activation_after_heavy_build_tag.sql"
FINAL_REPIN_MIGRATION = "0483_rule_delivery_activation_digest_repin.sql"
# 0554 re-pins all eight targets once more (784e0527 -> c6e89d64). A snapshot
# refreshed past it carries the new digest, so the chain must know it.
CUTOVER_READY_MIGRATION = "0554_scoped_rule_delivery_cutover_ready.sql"
# 0772 re-pins all eight targets once more (c6e89d64 -> the map digest after rule
# ede4b241 entered the reviewed map, 2026-09-29).
MODEL_CHOICE_REPIN_MIGRATION = "0772_repin_rule_delivery_activation_after_model_choice_rule.sql"
CONTROL_RETIREMENT_REPIN_MIGRATION = "0837_repin_rule_delivery_activation_after_control_retirement.sql"
CONTROL_RETIREMENT_REPIN_MIGRATION_SOURCE = (
    ROOT / "migrations" / CONTROL_RETIREMENT_REPIN_MIGRATION
).read_text(encoding="utf-8")
SOURCE_MERGE_MIGRATION_SOURCE = (
    ROOT / "migrations" / SOURCE_MERGE_MIGRATION
).read_text(encoding="utf-8")
TAIL_REPIN_MIGRATION_SOURCE = (
    ROOT / "migrations" / TAIL_REPIN_MIGRATION
).read_text(encoding="utf-8")
FINAL_REPIN_MIGRATION_SOURCE = (
    ROOT / "migrations" / FINAL_REPIN_MIGRATION
).read_text(encoding="utf-8")
MODEL_CHOICE_REPIN_MIGRATION_SOURCE = (
    ROOT / "migrations" / MODEL_CHOICE_REPIN_MIGRATION
).read_text(encoding="utf-8")
CUTOVER_READY_MIGRATION_SOURCE = (
    ROOT / "migrations" / CUTOVER_READY_MIGRATION
).read_text(encoding="utf-8")
POLICY_MARKER = "-- CARR RULE DELIVERY POLICY (bin/schema-snapshot.sh)"
TARGET_POST_MARKER = (
    "-- CARR RULE DELIVERY ACTIVATION TARGETS POST-0332 (bin/schema-snapshot.sh)"
)
TARGET_PRE_MARKER = (
    "-- CARR RULE DELIVERY ACTIVATION TARGETS PRE-0332 (bin/schema-snapshot.sh)"
)
TARGET_POST_0348_MARKER = (
    "-- CARR RULE DELIVERY ACTIVATION TARGETS POST-0348 (bin/schema-snapshot.sh)"
)
TARGET_POST_0363_MARKER = (
    "-- CARR RULE DELIVERY ACTIVATION TARGETS POST-0363 (bin/schema-snapshot.sh)"
)
OLD_DIGEST = "266ebb98076361b74cc2e22e5ea96380b2d3d1946b2d5d06b23ff349a5c98d9a"
DIGEST = "c0f3a9cc4fd407b346f44f09d7f05885051cfcc6c14c3f6c077e54a2a5448997"
THIRD_DIGEST = "4038e097f571f73499aee79b8c9e7b5bd3cea4ca0ba0f3847873e2f720106218"
CURRENT_DIGEST = "f7bf5726d329dd240434e51f7401fac9a977a3fb710636738f379f60f565f904"
SOURCE_MERGE_DIGEST = "6d21c37d533a5d98debfe4991c902164cf3c1fee88e7f42a3112468268e3335c"
TAIL_REPIN_DIGEST = "eebfa2d627dfbbc65ae06e623724487158b940c9376cd30dbb067aec2779e8bb"
FINAL_REPIN_DIGEST = "784e05273341f5f7c16f96d1f0fb1516d8c605cb3287dec32aa37a1211dd0cb8"
CUTOVER_READY_DIGEST = "c6e89d64de575b9c6e39c8c88cd6a32e97e494b381a7ac4433026c4a3fe63c2a"
MODEL_CHOICE_REPIN_DIGEST = "43ac7f513c173114b1723a886baf56a83ec40e7fef8b187ed3dead7d16d90ada"
CONTROL_RETIREMENT_REPIN_DIGEST = "b4e0d6689df3d96be24fb0cb888587f545bef8ffeba79c3ee6757b447f3fb308"
EXPECTED_TARGETS = [
    "25fcddee", "3fa17fa0", "72e06bdf", "581cb3fe", "113b3833",
    "57d13061", "c66dc739", "49533583", "557838a5",
]
CURRENT_EXPECTED_TARGETS = [
    short_id for short_id in EXPECTED_TARGETS if short_id != "581cb3fe"
]


def target_ids(source: str, marker: str) -> list[str]:
    block = source.split(marker, 1)[1].split(
        "on conflict (short_id) do nothing;", 1
    )[0]
    return re.findall(r"^\s*\('([0-9a-f]{8})'", block, re.M)


def target_digests(source: str, marker: str) -> list[str]:
    block = source.split(marker, 1)[1].split(
        "on conflict (short_id) do nothing;", 1
    )[0]
    return re.findall(r"'([0-9a-f]{64})'\)\s*,?$", block, re.M)


def snapshot_target_rows(source: str) -> list[list[str]]:
    marker = "COPY ops.rule_delivery_activation_target "
    if marker not in source:
        return []
    block = source.split(marker, 1)[1].split("\n", 1)[1].split("\n\\.\n", 1)[0]
    return [line.split("\t") for line in block.splitlines() if line]

match = re.search(r'VOCAB_TABLES="(?P<body>.*?)"', GENERATOR, re.S)
assert match, "schema snapshot generator has no bounded vocabulary declaration"
tables = set(match.group("body").replace("\\\n", " ").split())

required = {"ops.rule_delivery_policy", "ops.rule_delivery_activation_target"}
assert required <= tables, (
    "the scoped rule-delivery policy and exact activation target map must ride "
    "in the bounded snapshot vocabulary"
)
for forbidden in (
    "ops.rule_delivery_observation",
    "ops.rule_delivery_activation_receipt",
):
    assert forbidden not in tables, f"runtime/evidence table leaked into snapshot vocabulary: {forbidden}"

# A checked-in snapshot may legitimately predate the repair migration. Once its
# ledger says 0321 has been absorbed, however, the rows must be present because
# neither 0291, 0317, nor 0321 will replay on a fresh database.
ledger_applied = "0321_rule_delivery_policy_seed_repair.sql" in SNAPSHOT
policy_seeded = bool(re.search(
    r"COPY ops\.rule_delivery_policy\b|INSERT INTO ops\.rule_delivery_policy\b",
    SNAPSHOT,
    re.I,
))
targets_seeded = bool(re.search(
    r"COPY ops\.rule_delivery_activation_target\b|INSERT INTO ops\.rule_delivery_activation_target\b",
    SNAPSHOT,
    re.I,
))
assert not ledger_applied or (policy_seeded and targets_seeded), (
    "a snapshot whose ledger includes 0321 must also carry the scoped "
    "rule-delivery policy and activation targets"
)

policy_ledger_applied = bool(
    re.search(rf"^{re.escape(POLICY_MIGRATION)}\t", SNAPSHOT, re.M)
)
cutover_ledger_applied = bool(
    re.search(rf"^{re.escape(CUTOVER_MIGRATION)}\t", SNAPSHOT, re.M)
)
refresh_ledger_applied = bool(
    re.search(rf"^{re.escape(REFRESH_MIGRATION)}\t", SNAPSHOT, re.M)
)
second_refresh_ledger_applied = bool(
    re.search(rf"^{re.escape(SECOND_REFRESH_MIGRATION)}\t", SNAPSHOT, re.M)
)
current_ledger_applied = bool(
    re.search(rf"^{re.escape(CURRENT_MIGRATION)}\t", SNAPSHOT, re.M)
)
source_merge_ledger_applied = bool(
    re.search(rf"^{re.escape(SOURCE_MERGE_MIGRATION)}\t", SNAPSHOT, re.M)
)
tail_repin_ledger_applied = bool(
    re.search(rf"^{re.escape(TAIL_REPIN_MIGRATION)}\t", SNAPSHOT, re.M)
)
final_repin_ledger_applied = bool(
    re.search(rf"^{re.escape(FINAL_REPIN_MIGRATION)}\t", SNAPSHOT, re.M)
)
model_choice_repin_ledger_applied = bool(
    re.search(rf"^{re.escape(MODEL_CHOICE_REPIN_MIGRATION)}\t", SNAPSHOT, re.M)
)
control_retirement_repin_ledger_applied = bool(
    re.search(rf"^{re.escape(CONTROL_RETIREMENT_REPIN_MIGRATION)}\t", SNAPSHOT, re.M)
)
cutover_ready_ledger_applied = bool(
    re.search(rf"^{re.escape(CUTOVER_READY_MIGRATION)}\t", SNAPSHOT, re.M)
)
snapshot_targets = snapshot_target_rows(SNAPSHOT)

assert "RULE_DELIVERY_APPLIED" in GENERATOR
assert f"filename='{POLICY_MIGRATION}'" in GENERATOR
assert 'if [ "$RULE_DELIVERY_APPLIED" = t ]; then' in GENERATOR
assert "RULE_DELIVERY_CUTOVER_APPLIED" in GENERATOR
assert f"filename='{CUTOVER_MIGRATION}'" in GENERATOR
assert 'if [ "$RULE_DELIVERY_CUTOVER_APPLIED" = t ]; then' in GENERATOR
assert "RULE_DELIVERY_REFRESH_APPLIED" in GENERATOR
assert f"filename='{REFRESH_MIGRATION}'" in GENERATOR
assert f"filename='{SECOND_REFRESH_MIGRATION}'" in GENERATOR
assert 'if [ "$RULE_DELIVERY_REFRESH_APPLIED" = t ]; then' in GENERATOR
assert "RULE_DELIVERY_DIGEST_REPIN_APPLIED" in GENERATOR
assert f"filename='{CURRENT_MIGRATION}'" in GENERATOR
assert "SOURCE_MERGE_REGISTRY_APPLIED" in GENERATOR
assert f"filename='{SOURCE_MERGE_MIGRATION}'" in GENERATOR
assert 'if [ "$RULE_DELIVERY_DIGEST_REPIN_APPLIED" = t ]; then' in GENERATOR
assert 'elif [ "$RULE_DELIVERY_REFRESH_APPLIED" = t ]; then' in GENERATOR
assert "insert into ops.rule_delivery_policy (singleton,mode,changed_by,reason)" in GENERATOR
assert "values (true,'shadow','schema-snapshot'," in GENERATOR
assert "on conflict (singleton) do nothing;" in GENERATOR
assert target_ids(GENERATOR, TARGET_POST_MARKER) == EXPECTED_TARGETS
assert target_digests(GENERATOR, TARGET_POST_MARKER) == [DIGEST] * 9
assert target_ids(GENERATOR, TARGET_PRE_MARKER) == EXPECTED_TARGETS
assert target_digests(GENERATOR, TARGET_PRE_MARKER) == [OLD_DIGEST] * 9
assert target_ids(GENERATOR, TARGET_POST_0348_MARKER) == EXPECTED_TARGETS
assert target_digests(GENERATOR, TARGET_POST_0348_MARKER) == [THIRD_DIGEST] * 9
assert target_ids(GENERATOR, TARGET_POST_0363_MARKER) == CURRENT_EXPECTED_TARGETS
assert target_digests(GENERATOR, TARGET_POST_0363_MARKER) == [CURRENT_DIGEST] * 8
current_block = GENERATOR.split(TARGET_POST_0363_MARKER, 1)[1].split(
    "on conflict (short_id) do nothing;", 1
)[0]
assert "581cb3fe" not in current_block
post_block = GENERATOR.split(TARGET_POST_MARKER, 1)[1].split(
    "on conflict (short_id) do nothing;", 1
)[0]
pre_block = GENERATOR.split(TARGET_PRE_MARKER, 1)[1].split(
    "on conflict (short_id) do nothing;", 1
)[0]
assert "hooks/rule-pack-preuse-reselection.py" in post_block
assert "ops/rule-pack-preuse-reselection-selftest.py" in post_block
assert "hooks/rule-pack-preuse-reselection.py" not in pre_block
assert "ops/rule-pack-preuse-reselection-selftest.py" not in pre_block
expected_snapshot_targets = (
    CURRENT_EXPECTED_TARGETS if current_ledger_applied else EXPECTED_TARGETS
)
assert [row[0] for row in snapshot_targets] == expected_snapshot_targets
expected_snapshot_digest = (
    CONTROL_RETIREMENT_REPIN_DIGEST if control_retirement_repin_ledger_applied
    else MODEL_CHOICE_REPIN_DIGEST if model_choice_repin_ledger_applied
    else CUTOVER_READY_DIGEST if cutover_ready_ledger_applied
    else FINAL_REPIN_DIGEST if final_repin_ledger_applied
    else TAIL_REPIN_DIGEST if tail_repin_ledger_applied
    else SOURCE_MERGE_DIGEST if source_merge_ledger_applied
    else CURRENT_DIGEST if current_ledger_applied
    else THIRD_DIGEST if second_refresh_ledger_applied
    else DIGEST if refresh_ledger_applied
    else OLD_DIGEST
)
assert [row[-1] for row in snapshot_targets] == (
    [expected_snapshot_digest] * len(expected_snapshot_targets)
)
assert re.findall(r"^\s*\('([0-9a-f]{8})'", MIGRATION_SOURCE, re.M)[:9] == EXPECTED_TARGETS
assert CONTROL_RETIREMENT_REPIN_DIGEST in CONTROL_RETIREMENT_REPIN_MIGRATION_SOURCE
assert MODEL_CHOICE_REPIN_DIGEST in CONTROL_RETIREMENT_REPIN_MIGRATION_SOURCE
assert all(short_id in CONTROL_RETIREMENT_REPIN_MIGRATION_SOURCE for short_id in CURRENT_EXPECTED_TARGETS)
assert OLD_DIGEST in REFRESH_MIGRATION_SOURCE and DIGEST in REFRESH_MIGRATION_SOURCE
assert THIRD_DIGEST in SECOND_REFRESH_MIGRATION_SOURCE
assert all(short_id in SECOND_REFRESH_MIGRATION_SOURCE for short_id in EXPECTED_TARGETS)
assert all(short_id in REFRESH_MIGRATION_SOURCE for short_id in EXPECTED_TARGETS)
assert "requires shadow mode" in REFRESH_MIGRATION_SOURCE
assert CURRENT_DIGEST in CURRENT_MIGRATION_SOURCE
assert THIRD_DIGEST in CURRENT_MIGRATION_SOURCE
assert all(short_id in CURRENT_MIGRATION_SOURCE for short_id in EXPECTED_TARGETS)
assert "delete from ops.rule_delivery_activation_target" in CURRENT_MIGRATION_SOURCE
assert CURRENT_MIGRATION_SOURCE.count("581cb3fe") >= 2
assert "create or replace function ops.set_rule_delivery_mode(" in CURRENT_MIGRATION_SOURCE
assert "activation target set is not exactly eight" in CURRENT_MIGRATION_SOURCE
assert "return query select p_mode,8::bigint,v_receipt" in CURRENT_MIGRATION_SOURCE
assert "activation target set is not exactly nine" not in CURRENT_MIGRATION_SOURCE
assert "return query select p_mode,9::bigint,v_receipt" not in CURRENT_MIGRATION_SOURCE
assert "revoke all on function ops.set_rule_delivery_mode" in CURRENT_MIGRATION_SOURCE
assert "historical activation receipt cardinality constraint differs" in CURRENT_MIGRATION_SOURCE
assert "drop constraint rule_delivery_activation_receipt_target_short_ids_check" in CURRENT_MIGRATION_SOURCE
assert "check (cardinality(target_short_ids) in (8,9))" in CURRENT_MIGRATION_SOURCE
assert "requires shadow mode" in CURRENT_MIGRATION_SOURCE
assert CURRENT_DIGEST in SOURCE_MERGE_MIGRATION_SOURCE
assert SOURCE_MERGE_DIGEST in SOURCE_MERGE_MIGRATION_SOURCE
assert SOURCE_MERGE_DIGEST in TAIL_REPIN_MIGRATION_SOURCE
assert TAIL_REPIN_DIGEST in TAIL_REPIN_MIGRATION_SOURCE
assert TAIL_REPIN_DIGEST in FINAL_REPIN_MIGRATION_SOURCE
assert FINAL_REPIN_DIGEST in FINAL_REPIN_MIGRATION_SOURCE
assert FINAL_REPIN_DIGEST in CUTOVER_READY_MIGRATION_SOURCE
assert CUTOVER_READY_DIGEST in CUTOVER_READY_MIGRATION_SOURCE
assert CUTOVER_READY_DIGEST in MODEL_CHOICE_REPIN_MIGRATION_SOURCE
assert MODEL_CHOICE_REPIN_DIGEST in MODEL_CHOICE_REPIN_MIGRATION_SOURCE
policy_lock = CURRENT_MIGRATION_SOURCE.index("select p.mode into v_policy_mode")
rule_lock = CURRENT_MIGRATION_SOURCE.index("perform r.id")
target_delete = CURRENT_MIGRATION_SOURCE.index(
    "delete from ops.rule_delivery_activation_target"
)
assert policy_lock < rule_lock < target_delete
for retirement_guard in (
    "for update;",
    "join ops.rule_retirement_receipt rr",
    "join public.actor retirement_actor",
    "join ops.authority_receipt ar",
    "rr.previous_status='active'",
    "retirement_actor.slug='joe'",
    "retirement_actor.kind='human'",
    "retirement_actor.active",
    "rr.created_at=rr.retired_at",
    "from ops.rule_approval_receipt approval",
    "from ops.rule_approval_lifecycle_anchor anchor",
    "ops.legacy_rule_admission_note(r.id,'active',r.activated_at)",
    "rr.contract_hash=encode(public.digest(jsonb_build_object(",
    "'approval_receipt_id',rr.approval_receipt_id",
    "ar.decision='retired by Joe authority: '||rr.reason",
    "ar.evidence_refs=case",
    "ar.created_at=rr.retired_at",
    "left(successor.id::text,8)='aa411351'",
    "left(named_successor.id::text,8)='aa411351')=1",
    "where all_rr.rule_id=r.id)=1",
    "not absent or exactly retired to aa411351",
):
    assert retirement_guard in CURRENT_MIGRATION_SOURCE, retirement_guard
assert "ops.enforcement_control_catalog" in REFRESH_MIGRATION_SOURCE
assert "active approved rule and is immutable" in REFRESH_MIGRATION_SOURCE
assert "hooks/rule-pack-preuse-reselection.py" in REFRESH_MIGRATION_SOURCE
assert "ops/rule-pack-preuse-reselection-selftest.py" in REFRESH_MIGRATION_SOURCE
for guarded_field in (
    "t.expected_scope", "t.expected_pack", "t.from_control",
    "t.from_enforcement_class", "t.from_implementation_ref", "t.from_test_ref",
    "t.to_control", "t.to_enforcement_class", "t.to_implementation_ref",
    "t.to_test_ref", "t.map_digest",
):
    assert REFRESH_MIGRATION_SOURCE.count(guarded_field) >= 2, (
        f"0332 must bind and post-assert the complete activation preimage: {guarded_field}"
    )
for guarded_field in (
    "t.expected_scope", "t.expected_pack", "t.from_control",
    "t.from_enforcement_class", "t.from_implementation_ref", "t.from_test_ref",
    "t.to_control", "t.to_enforcement_class", "t.to_implementation_ref",
    "t.to_test_ref", "t.map_digest",
):
    assert CURRENT_MIGRATION_SOURCE.count(guarded_field) >= 2, (
        f"0363 must bind and post-assert the complete activation preimage: {guarded_field}"
    )
assert policy_ledger_applied == policy_seeded, (
    "the shadow rule-delivery bootstrap must appear exactly when 0291 is in "
    "the source snapshot ledger"
)
assert cutover_ledger_applied == targets_seeded, (
    "the ledger-appropriate rule-delivery activation targets must appear exactly when 0317 "
    "is in the source snapshot ledger"
)

print("schema snapshot rule-delivery seeds selftest: bounded exact reconstruction pinned")
