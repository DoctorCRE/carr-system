#!/usr/bin/env python3
"""Guard and execute the atomic Production rule-delivery transition."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.rule_delivery_activation import EXPECTED_IDS, load_validated  # noqa:E402

CURATION_BATCH = REPO / "audits" / "guidance-situation-curation-approval-batch.v1.json"
# Runs through hooks/hook-meter-run.py on the repo interpreter (2026-09-23):
# the same pin every other wired hook carries, so this gate is metered like
# the rest and never resolves to whatever python3 PATH offers.
HOOK_TEMPLATE = "{{REPO}}/.venv/bin/python {{REPO}}/hooks/hook-meter-run.py {{REPO}}/hooks/rule-pack-drift-gate.py"


def _stop_commands(document: dict) -> list[str]:
    hooks = document.get("hooks", document)
    return [str(hook.get("command", ""))
            for group in hooks.get("Stop", [])
            for hook in group.get("hooks", []) if isinstance(hook, dict)]


def live_hook_config_parity(repo: Path = REPO, home: Path | None = None,
                            installed_repo: Path | None = None) -> bool:
    """Verify the installed Stop hooks against this reviewed source checkout."""
    home = home or Path.home()
    installed_repo = installed_repo or repo
    try:
        source_claude = (repo / "ops/config/hooks.json").read_bytes()
        source_codex = (repo / "ops/config/codex-hooks.json").read_bytes()
        if source_claude != (installed_repo / "ops/config/hooks.json").read_bytes():
            return False
        if source_codex != (installed_repo / "ops/config/codex-hooks.json").read_bytes():
            return False
        source_hook = (repo / "hooks/rule-pack-drift-gate.py").read_bytes()
        installed_hook = (installed_repo / "hooks/rule-pack-drift-gate.py").read_bytes()
        if hashlib.sha256(source_hook).digest() != hashlib.sha256(installed_hook).digest():
            return False
        if _stop_commands(json.loads(source_claude)).count(HOOK_TEMPLATE) != 1:
            return False
        if _stop_commands(json.loads(source_codex)).count(HOOK_TEMPLATE) != 1:
            return False
        expected_live = HOOK_TEMPLATE.replace("{{REPO}}", str(installed_repo))
        live_claude = json.loads((home / ".claude/settings.json").read_text())
        live_codex = json.loads((home / ".codex/hooks.json").read_text())
        return (_stop_commands(live_claude).count(expected_live) == 1
                and _stop_commands(live_codex).count(expected_live) == 1)
    except (OSError, ValueError, TypeError):
        return False


def curation_ids() -> set[str]:
    batch = json.loads(CURATION_BATCH.read_text(encoding="utf-8"))
    ids = set(batch.get("proposal_ids", []))
    excluded = set(batch.get("explicitly_excluded_pending_proposal_ids", []))
    if len(ids) != 38 or ids & excluded or len(excluded) != 2:
        raise RuntimeError("curation approval batch is not exact 38 plus two exclusions")
    if batch.get("golden_suite_digest") != \
            "b1a5a61945c5e5fc5f7c74f45c3403f2c5df3e61db29e58f281d49015f63dae3":
        raise RuntimeError("curation approval batch golden digest drifted")
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("shadow","enforced"))
    parser.add_argument("--reason", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--installed-repo", type=Path, default=REPO)
    args = parser.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("rule-delivery-cutover: DATABASE_URL required", file=sys.stderr)
        return 78
    _base, overlay = load_validated()
    digest = overlay["base_map_sha256"]

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        if not args.apply:
            cur.execute("set transaction read only")
        cur.execute("select session_user,current_user")
        if cur.fetchone() != ("carr_authority_joe", "carr_authority_joe"):
            raise RuntimeError("cutover requires the exact carr_authority_joe login")
        cur.execute("""select mode,changed_by,reason,changed_at
                         from ops.rule_delivery_policy where singleton""")
        row = cur.fetchone()
        current = row[0] if row else None
        cur.execute("select * from ops.rule_delivery_cutover_preflight(%s::uuid[])",
                    (sorted(curation_ids()),))
        preflight_row = cur.fetchone()
        if preflight_row is None:
            raise RuntimeError("typed cutover preflight returned no row")
        typed_mode, target_count, receipt_count, *curation = preflight_row
        if typed_mode != current:
            raise RuntimeError("typed preflight and policy row disagree")
        expected_target_ids = sorted(EXPECTED_IDS)
        expected_target_count = len(expected_target_ids)
        cur.execute("""select coalesce(array_agg(short_id order by short_id),
                                        array[]::text[])
                         from ops.rule_delivery_activation_target""")
        target_row = cur.fetchone()
        if target_row is None:
            raise RuntimeError("activation target query returned no row")
        target_ids = list(target_row[0] or [])
        if target_count != expected_target_count:
            raise RuntimeError("typed preflight target count and current contract disagree")
        cur.execute("""select count(distinct map_digest),min(map_digest),count(*)
                         from ops.rule_load_layer""")
        tag_row = cur.fetchone()
        if tag_row is None:
            raise RuntimeError("delivery tag identity query returned no row")
        map_versions,tag_digest,tag_count = tag_row
        tag_coherent = map_versions == 1 and tag_digest == digest and tag_count > 0
        preflight = {"current_mode": current,"requested_mode": args.mode,
                     "targets": target_count,"prior_receipts": receipt_count,
                     "curation":{"found":curation[0],"approved":curation[1],
                                  "human_reviewed":curation[2]},
                     "delivery_tags":{"map_versions":map_versions,
                                      "map_digest":tag_digest,"count":tag_count},
                     "map_digest":digest}
        print(json.dumps(preflight,sort_keys=True))
        if target_ids != expected_target_ids:
            print("rule-delivery-cutover: canonical exact activation target set is absent",
                  file=sys.stderr)
            return 1
        if args.mode == "enforced" and tuple(curation) != (38,38,38):
            print("rule-delivery-cutover: exact 38-item human curation approval is absent",file=sys.stderr)
            return 1
        if not tag_coherent:
            print("rule-delivery-cutover: delivery tags do not share the reviewed map digest",
                  file=sys.stderr)
            return 1
        if not args.apply:
            print("rule-delivery-cutover: dry run only; pass --apply after reading the preflight")
            return 0

        # Re-lock and re-read policy inside this write transaction immediately
        # before the atomic transition.
        cur.execute("""select mode,changed_by,reason,changed_at
                         from ops.rule_delivery_policy where singleton for update""")
        final_policy = cur.fetchone()
        if final_policy != row:
            print("rule-delivery-cutover: policy changed before write",file=sys.stderr)
            return 1
        cur.execute("""select count(distinct map_digest),min(map_digest),count(*)
                         from ops.rule_load_layer""")
        if cur.fetchone() != (1,digest,tag_count):
            print("rule-delivery-cutover: delivery tag identity changed before write",
                  file=sys.stderr)
            return 1
        if not live_hook_config_parity(installed_repo=args.installed_repo):
            print("rule-delivery-cutover: Claude/Codex hook config parity failed",
                  file=sys.stderr)
            return 1
        cur.execute("select * from ops.set_rule_delivery_mode(%s,%s,%s)",
                    (args.mode,args.reason,digest))
        result = cur.fetchone()
        if not result or result[0] != args.mode or result[1] != expected_target_count:
            raise RuntimeError(f"atomic cutover returned an invalid receipt: {result}")
        conn.commit()
    print(json.dumps({"mode":result[0],"changed_controls":result[1],
                      "receipt_id":str(result[2])},sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
