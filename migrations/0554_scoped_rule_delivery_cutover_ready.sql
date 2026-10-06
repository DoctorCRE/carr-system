-- 0553: refresh the exact rule-delivery activation preimage and allow the
-- designated Joe authority login to read the metadata its cutover command
-- checks. This grants no direct policy write or rule-text access.
do $rule_delivery_0553$
declare
  v_old constant text := '784e05273341f5f7c16f96d1f0fb1516d8c605cb3287dec32aa37a1211dd0cb8';
  v_new constant text := 'c6e89d64de575b9c6e39c8c88cd6a32e97e494b381a7ac4433026c4a3fe63c2a';
  v_count integer;
begin
  select count(*) into v_count from ops.rule_delivery_activation_target
   where map_digest=v_old;
  if v_count<>8 or (select count(*) from ops.rule_delivery_activation_target)<>8 then
    raise exception '0553 REFUSED: exact eight-target preimage is absent';
  end if;
  update ops.rule_delivery_activation_target set map_digest=v_new
   where map_digest=v_old;
  get diagnostics v_count=row_count;
  if v_count<>8 then
    raise exception '0553 REFUSED: changed % targets, expected eight',v_count;
  end if;
end $rule_delivery_0553$;

do $rule_delivery_0553_grant$
begin
  if exists (select 1 from pg_roles where rolname='carr_authority_joe') then
    grant select on ops.rule_delivery_policy,
                    ops.rule_delivery_activation_target,
                    ops.rule_load_layer
      to carr_authority_joe;
  end if;
end $rule_delivery_0553_grant$;
