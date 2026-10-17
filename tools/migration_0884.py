"""Retain pack_delivery metadata frozen by an active approval."""
import hashlib

from retained_pack_delivery import (
    ACTIVE_CLAIM_SQL, EXPECTED_APPROVAL_SQL, RETAINED_PACK_DELIVERY,
)

FILENAME = '0884_refresh_pack_delivery_catalog_after_shadow_retirement.sql'
SOURCE_SHA256 = 'f894c45f77bbfbf74c97ba3debc39ee9c273ae8bc272a66461b6e44bd3223625'
PREIMAGE = RETAINED_PACK_DELIVERY[1:]


def catalog_sql(cur, sql, digest):
    """Adapt only frozen 0884, with approval and catalog writes locked out."""
    if digest != SOURCE_SHA256 or hashlib.sha256(sql.encode()).hexdigest() != SOURCE_SHA256:
        raise ValueError('0884 recovery REFUSED: source checksum changed')
    cur.execute('lock table public.rule, ops.rule_approval_receipt, '
                'ops.rule_control_binding, ops.enforcement_control_catalog in share row exclusive mode')
    cur.execute(f"select ({ACTIVE_CLAIM_SQL}), ({EXPECTED_APPROVAL_SQL})")
    claimed, expected = cur.fetchone()
    if not claimed:
        return sql
    if not expected:
        raise ValueError('0884 recovery REFUSED: unexpected approval or rule state for pack_delivery')
    cur.execute("select implementation_ref,test_ref,enforcement_class,installed "
                "from ops.enforcement_control_catalog where control_key='pack_delivery'")
    if cur.fetchone() != PREIMAGE:
        raise ValueError('0884 recovery REFUSED: expected exact frozen pack_delivery catalog')
    # Approval and binding history remain authoritative, including the stale paths.
    # The original artifact and checksum also remain authoritative on staging.
    return "do $$ begin raise notice '0884 retained immutable pack_delivery catalog; stale references preserved'; end $$;"
