#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

REPO=Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location("cutover",REPO/"ops/rule-delivery-cutover.py")
assert spec and spec.loader
cutover=importlib.util.module_from_spec(spec); spec.loader.exec_module(cutover)
py=(REPO/"ops/rule-delivery-cutover.py").read_text()
sh=(REPO/"bin/rule-delivery-cutover-prod.sh").read_text()
sql=(REPO/"migrations/0317_atomic_rule_delivery_cutover.sql").read_text()
successor=(REPO/"migrations/0452_siep02_rule_delivery_authority.sql").read_text()
current_sql=(REPO/"migrations/0363_rule_delivery_activation_digest_repin.sql").read_text()
checks={
 "current activation contract is authoritative":len(cutover.EXPECTED_IDS)==8 and "EXPECTED_IDS, load_validated" in py,
 "exact current target ids are queried":"array_agg(short_id order by short_id)" in py,
 "target cardinality follows current contract":"expected_target_count = len(expected_target_ids)" in py and "result[1] != expected_target_count" in py,
 "retired nine-target literals are absent":"target_count != 9" not in py and "result[1] != 9" not in py,
 "current database function returns eight":"activation target set is not exactly eight" in current_sql and "return query select p_mode,8::bigint,v_receipt" in current_sql,
 "exact 38 reviewed proposal ids":len(cutover.curation_ids())==38,
 "human reviewer is checked":"rp.status='approved' and a.kind='human'" in successor,
 "partner-directed cutover no longer requires a shadow window":"shadow_eligible" not in py and "locked_read" not in py,
 "all delivery tags must use the reviewed map":"count(distinct map_digest),min(map_digest),count(*)" in py and "tag_coherent" in py,
 "policy and tag identity are rebound before write":"where singleton for update" in py and "final_policy != row" in py and "delivery tag identity changed" in py,
 "live hook parity runs before write":"if not live_hook_config_parity(installed_repo=args.installed_repo)" in py,
 "production requires the Joe authority login":"CARR_DB_AUTHORITY_JOE_URL" in sh
    and "neondb_owner" not in sh and "--changed-by" not in sh,
 "runtime verifies the exact Joe login":"select session_user,current_user" in py
    and "carr_authority_joe" in py and "--changed-by" not in py,
 "dry run is default":"--apply" in sh and "APPLY=0" in sh,
 "direct policy update is guarded":"rule_delivery_policy_cutover_only" in sql,
 "cutover and controls share one function":"set_rule_delivery_mode" in sql and "delete from ops.rule_enforcement_point" in sql,
 "receipt is append-only":"rule_delivery_activation_receipt_append_only" in sql,
 "successor derives attribution and revokes legacy authority":
    "'joe',btrim(p_reason)" in successor
    and "revoke all on function ops.set_rule_delivery_mode(text,text,text,text)" in successor
    and "session_user <> 'carr_authority_joe'" in successor,
 "Joe preflight is typed and aggregate-only":"rule_delivery_cutover_preflight" in py
    and "from retrieval_proposal" not in py
    and "cardinality(p_curation_proposal_ids) <> 38" in successor,
}
with tempfile.TemporaryDirectory() as directory:
 home=Path(directory); (home/".claude").mkdir(); (home/".codex").mkdir()
 command=cutover.HOOK_TEMPLATE.replace("{{REPO}}",str(REPO))
 document={"hooks":{"Stop":[{"hooks":[{"command":command}]}]}}
 (home/".claude/settings.json").write_text(json.dumps(document))
 (home/".codex/hooks.json").write_text(json.dumps(document))
 checks["exact Claude and Codex live readback passes"]=cutover.live_hook_config_parity(
     REPO,home,REPO)
 (home/".codex/hooks.json").write_text(json.dumps(
     {"hooks":{"Stop":[{"hooks":[{"command":command+".wrong"}]}]}}))
 checks["one trigger mismatch refuses cutover"]=not cutover.live_hook_config_parity(
     REPO,home,REPO)
bad=[name for name,passed in checks.items() if not passed]
if bad:
 print("rule-delivery-cutover-selftest: FAIL")
 for name in bad: print("  "+name)
 raise SystemExit(1)
print(f"rule-delivery-cutover-selftest: {len(checks)} cases passed")
