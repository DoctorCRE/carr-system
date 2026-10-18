-- 0772: repin the eight rule-delivery activation targets after rule ede4b241
-- (cloud model choice, taught 2026-09-29) entered the reviewed
-- ops/config/rule-enforcement-map.json.
--
-- The map digest is part of each cutover target's identity and the reviewed
-- overlay (ops/config/rule-delivery-activation-overlay.v1.json) is hard-checked
-- against it, so adding a rule to the map moves the digest. The eight pack
-- cutover contracts are unchanged; only their base-map identity moves forward.
-- Same guarded shape as 0478, 0483 and 0554: exact eight-row preimage, and the
-- update must change exactly eight rows. It touches no rule text and grants
-- nothing; the rule itself still needs one approve-rule act by Joe.
do $rule_delivery_0772$
declare
  v_old constant text := 'c6e89d64de575b9c6e39c8c88cd6a32e97e494b381a7ac4433026c4a3fe63c2a';
  v_new constant text := '43ac7f513c173114b1723a886baf56a83ec40e7fef8b187ed3dead7d16d90ada';
  v_count integer;
begin
  select count(*) into v_count from ops.rule_delivery_activation_target
   where map_digest=v_old;
  if v_count<>8 or (select count(*) from ops.rule_delivery_activation_target)<>8 then
    raise exception '0772 REFUSED: exact eight-target preimage is absent';
  end if;
  update ops.rule_delivery_activation_target set map_digest=v_new
   where map_digest=v_old;
  get diagnostics v_count=row_count;
  if v_count<>8 then
    raise exception '0772 REFUSED: changed % targets, expected eight',v_count;
  end if;
end $rule_delivery_0772$;
