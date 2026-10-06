-- 0478: repin the eight rule-delivery activation targets onto the map digest
-- that carries rule 1fcaa63a, the heavy-build-protocol rule.
--
-- WHY THIS EXISTS. Tagging rule 1fcaa63a into ops/config/rule-enforcement-map.json
-- moved that file's sha256 from 6d21c37d... to eebfa2d6....
-- lib/rule_delivery_activation.py:validate_overlay hard-fails unless the
-- activation overlay's base_map_sha256 equals sha256 of the live map bytes, so
-- the overlay had to move with it. migrations/0471 repins the eight
-- ops.rule_delivery_activation_target rows to the OLD digest as a hardcoded
-- literal, and 0471 is a sealed generated artifact inside the 0454-0471
-- generation chain -- editing it halts that chain at its own link. So the
-- overlay and 0471 disagree, and ops.set_rule_delivery_mode refuses the
-- enforced-mode cutover with "activation map digest preimage differs".
--
-- This is the forward half, following the 0363 -> 0471 precedent: when the
-- reviewed map moves, a later migration repins the targets rather than the
-- sealed earlier one being rewritten.
--
-- SEQUENCING: must run after 0471, which is what puts the eight rows on
-- 6d21c37d in the first place. The guard below makes that ordering checkable
-- rather than assumed -- on a database where 0471 has not run, the preimage
-- count is not eight and this migration refuses instead of silently doing
-- nothing.

do $rule_map_repin_after_heavy_build_tag$
declare updated integer; preimage integer;
begin
  select count(*) into preimage
    from ops.rule_delivery_activation_target
   where map_digest='6d21c37d533a5d98debfe4991c902164cf3c1fee88e7f42a3112468268e3335c';
  if preimage<>8 then
    raise exception '0478 REFUSED: expected eight activation targets on the post-0471 map digest, found %',preimage;
  end if;

  update ops.rule_delivery_activation_target
     set map_digest='eebfa2d627dfbbc65ae06e623724487158b940c9376cd30dbb067aec2779e8bb'
   where map_digest='6d21c37d533a5d98debfe4991c902164cf3c1fee88e7f42a3112468268e3335c';
  get diagnostics updated=row_count;
  if updated<>8 then
    raise exception '0478 REFUSED: expected eight exact rule-delivery target repins, changed %',updated;
  end if;
end $rule_map_repin_after_heavy_build_tag$;
