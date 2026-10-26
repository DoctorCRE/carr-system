#!/usr/bin/env python3
"""Keep complete reviewed-map fixtures without a fingerprint per seed row."""
from __future__ import annotations

import hashlib
import sys
import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "ops"))
from rule_projection_fixture import seed_reviewed_rule_projection  # noqa: E402


class RecordingCursor:
    """Record observer work at the same SQL seam used by the DB gates."""

    def __init__(self):
        self.disabled = set()
        self.refreshes = []
        self.layers = {}
        self.packs = {}
        self.queries = []

    def execute(self, query, params=()):
        sql = " ".join(query.lower().split())
        self.queries.append(sql)
        toggle = re.fullmatch(r"alter table (\S+) (disable|enable) trigger (\S+)", sql)
        if toggle:
            table, action, trigger = toggle.groups()
            key = (table, trigger)
            if action == "disable":
                self.disabled.add(key)
            else:
                self.disabled.discard(key)
        for table, trigger in (
            ("ops.rule_pack", "scac_epoch_rule_pack"),
            ("ops.rule_load_layer", "scac_epoch_rule_load_layer"),
        ):
            if sql.startswith(f"insert into {table}"):
                if (table, trigger) not in self.disabled:
                    self.refreshes.append(table)
                if table == "ops.rule_pack":
                    self.packs[params[0]] = params
                else:
                    self.layers[params[1]] = params
        return self

    def fetchone(self):
        return (1,)


class ProjectionFixtureTests(unittest.TestCase):
    def test_complete_map_bootstraps_through_one_restored_observer(self):
        raw = (REPO / "ops/config/rule-enforcement-map.json").read_bytes()
        reviewed = json.loads(raw)
        scopes = {short: scope for scope, shorts in reviewed["active_rule_ids"].items()
                  for short in shorts}
        tail = "000000000001"
        cur = RecordingCursor()
        seed_reviewed_rule_projection(cur, slug="rule-projection-fixture", title="Rule projection fixture",
                                      rule_label="Reviewed projection fixture", uuid_tail=tail)
        self.assertEqual(set(cur.packs), set(reviewed["rule_packs"]))
        self.assertEqual(set(cur.layers), set(reviewed["rule_load_layers"]))
        for pack, contract in reviewed["rule_packs"].items():
            self.assertEqual(cur.packs[pack], (
                pack, contract["title"], contract["description"], contract["triggers"],
                "ops/config/rule-enforcement-map.json"))
        for short, contract in reviewed["rule_load_layers"].items():
            self.assertEqual(cur.layers[short], (
                f"{short}-0000-4000-8000-{tail}", short, contract["load_layer"],
                contract.get("packs", []), scopes[short], contract.get("why"),
                "ops/config/rule-enforcement-map.json", hashlib.sha256(raw).hexdigest()))
        self.assertEqual(cur.disabled, set(), "fixture must restore every observer")
        self.assertEqual(cur.refreshes, ["ops.rule_load_layer"],
                         "only the final real insert should queue the expensive fingerprint")
        self.assertIn("set constraints ops.scac_epoch_rule_load_layer immediate", cur.queries)
        self.assertEqual(cur.queries[-1], "set constraints all deferred")


if __name__ == "__main__":
    unittest.main()
