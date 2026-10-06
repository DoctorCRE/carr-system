-- 0382_standing_guidance_reader_boundary.sql
--
-- Guidance Registry activation made ops.standing_guidance the authoritative
-- reader projection, but 0168/0170 left it as SECURITY INVOKER.  carr_reader
-- may execute the function and read the sanctioned projections, while it is
-- intentionally denied the public.rule statement and public.actor display_name
-- columns that the function joins.  The active path therefore failed with
-- SQLSTATE 42501 even though its EXECUTE grant was present.
--
-- Converge that single read boundary without widening any base-table grant.
-- Every relation/function reference is schema-qualified and the lookup path is
-- pinned, following the existing reader-boundary precedent in 0188 and 0291.

begin;

create or replace function ops.standing_guidance(
  p_actor text,
  p_workflow text default null,
  p_surface text default null,
  p_tier text default null
) returns table(
  source_rule_id uuid,
  statement text,
  human_quote text,
  taught_by text,
  personal_to text,
  scope jsonb,
  guidance_type text,
  is_constitution boolean
)
language sql
stable
security definer
set search_path = pg_catalog, ops, public, pg_temp
as $function$
  select r.id,r.statement,r.human_quote,teacher.display_name,owner.slug,g.scope,
         g.guidance_type,g.is_constitution
    from ops.v_guidance_current g
    join public.rule r on r.id=g.source_rule_id and r.status='active'
    join public.actor teacher on teacher.id=r.taught_by
    left join public.actor owner on owner.id=r.personal_to
   where exists (
           select 1
             from ops.v_guidance_registry_state s
             join ops.guidance_registry registry
               on registry.id=s.registry_id and registry.singleton
            where s.state='active'
         )
     and (r.personal_to is null or owner.slug=p_actor)
     and (
       g.is_constitution
       or (g.guidance_type='constraint' and exists (
         select 1 from ops.applicable_rules(p_workflow,p_surface,p_tier) ar
          where ar.rule_id=r.id))
     )
   order by g.is_constitution desc,r.personal_to nulls first,r.created_at,r.id
$function$;

comment on function ops.standing_guidance(text, text, text, text) is
  'Reader-facing active Guidance Registry projection. SECURITY DEFINER with a '
  'fixed search_path so carr_reader can consume the sanctioned projection '
  'without receiving direct public.rule or public.actor table access.';

revoke all on function ops.standing_guidance(text,text,text,text) from public;
grant execute on function ops.standing_guidance(text,text,text,text)
  to carr_reader,carr_writer;

commit;
