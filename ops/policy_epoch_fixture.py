"""Planner statistics for rollback-only policy-epoch acceptance fixtures."""
from typing import Any


def analyze_rule_projection(cur: Any) -> None:
    # Autovacuum cannot see these uncommitted synthetic rows. Without estimates,
    # the deferred epoch observer and subsequent status reads plan for empty
    # doctrine/rule tables despite the fixture's complete reviewed projection.
    cur.execute("analyze public.rule, public.doctrine_document, public.doctrine_snapshot, "
                "ops.rule_pack, ops.rule_load_layer, ops.rule_delivery_policy")
