"""Retain pack_delivery metadata frozen by an active approval."""
import hashlib

FILENAME = '0884_refresh_pack_delivery_catalog_after_shadow_retirement.sql'
SOURCE_SHA256 = 'f894c45f77bbfbf74c97ba3debc39ee9c273ae8bc272a66461b6e44bd3223625'
PREIMAGE = (
    'hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py',
    'ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py',
    'stop_gate', True,
)


def catalog_sql(cur, sql, digest):
    """Adapt only frozen 0884, with approval and catalog writes locked out."""
    if digest != SOURCE_SHA256 or hashlib.sha256(sql.encode()).hexdigest() != SOURCE_SHA256:
        raise ValueError('0884 recovery REFUSED: source checksum changed')
    cur.execute('lock table public.rule, ops.rule_approval_receipt, '
                'ops.enforcement_control_catalog in share row exclusive mode')
    cur.execute("select exists(select 1 from ops.rule_approval_receipt ar "
                "join public.rule r on r.id=ar.rule_id and r.status='active' "
                "where 'pack_delivery'=any(ar.requested_control_keys))")
    if not cur.fetchone()[0]:
        return sql
    cur.execute("select implementation_ref,test_ref,enforcement_class,installed "
                "from ops.enforcement_control_catalog where control_key='pack_delivery'")
    if cur.fetchone() != PREIMAGE:
        raise ValueError('0884 recovery REFUSED: expected exact frozen pack_delivery catalog')
    # Approval and binding history remain authoritative, including the stale paths.
    # The original artifact and checksum also remain authoritative on staging.
    return "do $$ begin raise notice '0884 retained immutable pack_delivery catalog; stale references preserved'; end $$;"
