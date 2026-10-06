-- 0452_siep02_rule_delivery_authority.sql
-- SIEP-02 successor to 0317: selecting scoped rules is source-only until later
-- enforcement nodes, and the shadow/enforced switch remains Joe's alone.  The
-- legacy four-argument implementation is retained as a private implementation
-- detail so this migration does not duplicate its exact nine-control cutover.

create or replace function ops.refuse_direct_rule_delivery_policy_update()
returns trigger language plpgsql security definer
set search_path=pg_catalog,ops as $$
begin
  if session_user <> 'carr_authority_joe' then
    raise exception using errcode='42501',
      message='rule-delivery policy mutation requires the Joe authority login';
  end if;
  if current_setting('carr.rule_delivery_cutover',true) is distinct from 'on' then
    raise exception 'direct rule-delivery policy update refused; use ops.set_rule_delivery_mode';
  end if;
  return new;
end $$;

revoke all on function ops.set_rule_delivery_mode(text,text,text,text)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

create or replace function ops.rule_delivery_cutover_preflight(
  p_curation_proposal_ids uuid[]
)
returns table(
  current_mode text,
  target_count bigint,
  receipt_count bigint,
  curation_found bigint,
  curation_approved bigint,
  curation_human_reviewed bigint
)
language plpgsql stable security definer
set search_path=pg_catalog,public,ops as $$
begin
  if session_user <> 'carr_authority_joe'
     or ops.authority_actor_slug() <> 'joe' then
    raise exception using errcode='42501',
      message='rule-delivery cutover preflight requires the Joe authority login';
  end if;
  if cardinality(p_curation_proposal_ids) <> 38
     or (select count(distinct proposal_id) from unnest(p_curation_proposal_ids) proposal_id) <> 38 then
    raise exception 'rule-delivery cutover preflight requires the exact 38-item curation batch';
  end if;
  return query
    select (select p.mode from ops.rule_delivery_policy p where p.singleton),
           (select count(*) from ops.rule_delivery_activation_target),
           (select count(*) from ops.rule_delivery_activation_receipt),
           count(rp.id),
           count(*) filter(where rp.status='approved'),
           count(*) filter(where rp.status='approved' and a.kind='human')
      from public.retrieval_proposal rp
      left join public.actor a on a.id=rp.reviewer_id
     where rp.id=any(p_curation_proposal_ids);
end $$;

create or replace function ops.set_rule_delivery_mode(
  p_mode text,
  p_reason text,
  p_expected_map_digest text
)
returns table(mode text,changed_controls bigint,receipt_id uuid)
language plpgsql security definer
set search_path=pg_catalog,public,ops as $$
begin
  if session_user <> 'carr_authority_joe'
     or ops.authority_actor_slug() <> 'joe' then
    raise exception using errcode='42501',
      message='rule-delivery cutover requires the Joe authority login';
  end if;
  if coalesce(btrim(p_reason),'')='' then
    raise exception 'rule-delivery cutover reason is required';
  end if;
  return query
    select result.mode,result.changed_controls,result.receipt_id
      from ops.set_rule_delivery_mode(
        p_mode,'joe',btrim(p_reason),p_expected_map_digest
      ) result;
end $$;

comment on function ops.set_rule_delivery_mode(text,text,text) is
  'Joe-only atomic rule-delivery cutover. The authenticated session supplies attribution; no caller actor label is accepted.';

revoke all on function ops.set_rule_delivery_mode(text,text,text)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.rule_delivery_cutover_preflight(uuid[])
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.set_rule_delivery_mode(text,text,text)
  to carr_authority;
grant execute on function ops.rule_delivery_cutover_preflight(uuid[])
  to carr_authority;

do $$
declare definition text;
begin
  if has_function_privilege('carr_writer',
       'ops.set_rule_delivery_mode(text,text,text)'::regprocedure,'execute')
     or has_function_privilege('carr_jobs',
       'ops.set_rule_delivery_mode(text,text,text)'::regprocedure,'execute')
     or has_function_privilege('carr_authority',
       'ops.set_rule_delivery_mode(text,text,text,text)'::regprocedure,'execute')
     or has_function_privilege('carr_writer',
       'ops.rule_delivery_cutover_preflight(uuid[])'::regprocedure,'execute') then
    raise exception '0336 FAILED: routine or legacy cutover authority remains';
  end if;
  select pg_get_functiondef('ops.set_rule_delivery_mode(text,text,text)'::regprocedure)
    into definition;
  if definition not like '%session_user <> ''carr_authority_joe''%'
     or definition like '%p_changed_by%' then
    raise exception '0336 FAILED: cutover does not derive Joe authority from the login';
  end if;
end $$;
