"""The single approved historical pack_delivery catalog state.

Remove this acceptance and refresh the row when heavy-build is re-approved.
"""
RULE_ID = '1fcaa63a-73d7-498d-9f76-e99bcd821a89'
APPROVAL_ID = '691779e7-d1bc-43f0-adea-c5a97ef72c94'
RETAINED_PACK_DELIVERY = (
    'pack_delivery',
    'hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py',
    'ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py',
    'stop_gate', True,
)
ACCEPTED_STATE_LABEL = (
    f"accepted historical pack_delivery: implementation_ref={RETAINED_PACK_DELIVERY[1]}; "
    f"test_ref={RETAINED_PACK_DELIVERY[2]}; approval={APPROVAL_ID}"
)

# Approval receipts have no status column; their rule's active status is the
# immutable-catalog trigger's definition of an active approval.
EXPECTED_APPROVAL_SQL = f"""
    exists (
      select 1 from ops.rule_approval_receipt ar
      join public.rule r on r.id=ar.rule_id
      where ar.id='{APPROVAL_ID}'::uuid
        and r.id='{RULE_ID}'::uuid and r.status='active' and r.version=2
        and ar.rule_version=2 and ar.requested_control_keys=array['pack_delivery']::text[]
    )
    and not exists (
      select 1 from ops.rule_approval_receipt ar
      join public.rule r on r.id=ar.rule_id and r.status='active'
      where 'pack_delivery'=any(ar.requested_control_keys)
        and ar.id<>'{APPROVAL_ID}'::uuid
    )
    and not exists (
      select 1 from ops.rule_control_binding b
      join public.rule r on r.id=b.rule_id and r.status='active'
      where b.control_key='pack_delivery' and r.id<>'{RULE_ID}'::uuid
    )
"""

ACTIVE_CLAIM_SQL = f"""
    exists (
      select 1 from ops.rule_approval_receipt ar
      join public.rule r on r.id=ar.rule_id and r.status='active'
      where 'pack_delivery'=any(ar.requested_control_keys) or ar.id='{APPROVAL_ID}'::uuid
    )
    or exists (
      select 1 from ops.rule_control_binding b
      join public.rule r on r.id=b.rule_id and r.status='active'
      where b.control_key='pack_delivery'
    )
"""


RETAINED_PACK_DELIVERY_SQL = f"""(
    c.control_key='{RETAINED_PACK_DELIVERY[0]}'
    and c.implementation_ref='{RETAINED_PACK_DELIVERY[1]}'
    and c.test_ref='{RETAINED_PACK_DELIVERY[2]}'
    and c.enforcement_class='{RETAINED_PACK_DELIVERY[3]}'
    and c.installed and c.verified_at is not null
    and ({EXPECTED_APPROVAL_SQL})
)"""
