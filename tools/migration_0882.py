"""Retain the two retired bindings frozen by historical approval receipts."""
import hashlib

FILENAME = '0882_retire_reply_gate_controls.sql'
SOURCE_SHA256 = 'fbf4467f57cefb743da016d72cdb987e1bf0ddcc814029bfe531f73c49462c5f'

RETENTION = """
    if key in ('completion_evidence','conduct_stop') then
      select count(*) into claimed
        from ops.rule_approval_receipt ar
        join public.rule r on r.id=ar.rule_id and r.status='active'
       where key=any(ar.requested_control_keys);
      if claimed<>0 then
        raise exception '0882 recovery REFUSED: % backs an active approval',key;
      end if;
      select count(*) into bound from ops.rule_control_binding where control_key=key;
      if bound<>0 then
        if bound<>1 or not exists (
          select 1 from ops.rule_control_binding b
          join public.rule r on r.id=b.rule_id
           where b.control_key=key and r.status='retired' and r.version=3
             and r.id=case key
               when 'completion_evidence' then '937252fb-f230-444a-9fe6-dee8885fa237'::uuid
               when 'conduct_stop' then '3fa422b7-7c99-49fc-8e22-1e551a975c6f'::uuid end
        ) then
          raise exception '0882 recovery REFUSED: unexpected binding or rule state for %',key;
        end if;
        continue;
      end if;
    end if;
"""


def retirement_sql(cur, sql, digest):
    """Adapt only the frozen 0882 artifact, inside its runner-owned transaction."""
    if digest != SOURCE_SHA256 or hashlib.sha256(sql.encode()).hexdigest() != SOURCE_SHA256:
        raise ValueError('0882 recovery REFUSED: source checksum changed')
    cur.execute('lock table public.rule, ops.rule_approval_receipt, '
                'ops.rule_control_binding, ops.enforcement_control_catalog in share row exclusive mode')
    cur.execute("select exists(select 1 from ops.rule_control_binding "
                "where control_key in ('completion_evidence','conduct_stop'))")
    if not cur.fetchone()[0]:
        return sql
    # The immutable approval freezes these bindings even after retirement.
    # Keep their FK targets; every other key still runs the original guards.
    return sql.replace('  ] loop\n', '  ] loop\n' + RETENTION, 1)
