-- rollback: forward-only; revoke download grants before restoring the prior scope contract.
-- expand-contract: adds a separately authorized client download path; existing scopes retain their meaning.
-- lock-review: bounded constraint validation on grant/session tables under a five-second lock timeout.
set local lock_timeout='5s';
set local statement_timeout='30s';
alter table ops.tour_share_grant drop constraint if exists tour_share_grant_permission_scopes_check;
alter table ops.tour_share_grant add constraint tour_share_grant_permission_scopes_check
  check (jsonb_typeof(permission_scopes)='array' and jsonb_array_length(permission_scopes)>0
    and permission_scopes <@ '["view_packet","view_map","shortlist","comment","download"]'::jsonb
    and (not (permission_scopes ? 'shortlist' or permission_scopes ? 'comment') or permission_scopes ? 'view_packet'));
alter table ops.tour_share_session drop constraint if exists tour_share_session_permission_scopes_check;
alter table ops.tour_share_session add constraint tour_share_session_permission_scopes_check
  check (jsonb_typeof(permission_scopes)='array' and jsonb_array_length(permission_scopes)>0
    and permission_scopes <@ '["view_packet","view_map","shortlist","comment","download"]'::jsonb
    and (not (permission_scopes ? 'shortlist' or permission_scopes ? 'comment') or permission_scopes ? 'view_packet'));


do $scope_upgrade$
declare f regprocedure; definition text;
begin
 foreach f in array array['ops.issue_tour_share_grant(text,uuid,text,jsonb,timestamptz,text,text)'::regprocedure,
 'ops.rotate_tour_share_grant(text,uuid,uuid,text,jsonb,timestamptz,text,text)'::regprocedure]
 loop
  definition:=pg_get_functiondef(f);
  if position('["view_packet","view_map","shortlist","comment"]' in definition)=0 then
   raise exception 'tour share scope contract changed';
  end if;
  execute replace(definition,'["view_packet","view_map","shortlist","comment"]',
   '["view_packet","view_map","shortlist","comment","download"]');
 end loop;
end $scope_upgrade$;

create function ops.read_tour_share_download(p_session_digest text)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
 with g as (select (ops.tour_share_session_grant(p_session_digest,'download')).*)
 select jsonb_build_object('grant_id',g.id,'storage_ref',r.storage_ref,'artifact_digest',r.artifact_digest,
  'content_length',r.content_length)
 from g join ops.tour_public_projection p on p.organization_tenant_id=g.organization_tenant_id and p.id=g.projection_id
 join ops.tour_pdf_render_job j on j.organization_tenant_id=p.organization_tenant_id and j.projection_id=p.id
  and j.projection_digest=p.projection_digest
 join ops.tour_pdf_render_result r on r.organization_tenant_id=j.organization_tenant_id and r.render_job_id=j.id
  and r.status='review_ready' and r.blocking_finding_count=0
 join ops.tour_pdf_human_review h on h.organization_tenant_id=j.organization_tenant_id and h.render_job_id=j.id
  and h.render_result_id=r.id and h.decision='accept'
 where p.status='approved' and ops.read_tour_public_projection(p.organization_tenant_id,p.id) is not null
  and ops.tour_public_projection_client_safe(p.organization_tenant_id,p.id)
  and exists(select 1 from ops.tour_public_projection_seal_receipt s where s.organization_tenant_id=p.organization_tenant_id
   and s.projection_id=p.id and s.canonical_projection_digest=p.projection_digest)
 order by r.created_at desc,r.id desc limit 1;
$$;
revoke all on function ops.read_tour_share_download(text) from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.read_tour_share_download(text) to carr_writer;

do $download_grant_contract$
declare contract jsonb:=ops.scac_current_grant_contract(); entry jsonb;
begin
 entry:=jsonb_build_object('grantee','carr_writer','signature','ops.read_tour_share_download(p_session_digest text)',
  'grantable',false,'privilege','execute',
  'ingress_key','db-function-acl:ops.read_tour_share_download(p_session_digest text):carr_writer:execute',
  'ingress_kind','db_function_acl','function_kind','f','security_definer',true,'volatility','s','parallel','u',
  'config',jsonb_build_array('search_path=pg_catalog, ops, public, pg_temp'));
 contract:=jsonb_set(contract,'{secdef_execute}',(contract->'secdef_execute')||jsonb_build_array(entry));
 execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L',
  'select '||quote_literal(contract::text)||'::jsonb');
end $download_grant_contract$;

-- Feedback capabilities reflect the live session, including a narrowed session.
create or replace function ops.read_tour_share_feedback(p_session_digest text)
returns jsonb language sql stable security definer set search_path=pg_catalog,ops,public,pg_temp as $$
  with g as (select grant_row.* from ops.tour_share_session_grant(p_session_digest,'shortlist') grant_row where grant_row.id is not null
            union all select grant_row.* from ops.tour_share_session_grant(p_session_digest,'comment') grant_row where grant_row.id is not null limit 1),
  p as (select p.* ,g.id grant_id,s.permission_scopes from g join ops.tour_public_projection p
       on p.organization_tenant_id=g.organization_tenant_id and p.id=g.projection_id
       join ops.tour_share_session s on s.session_digest=p_session_digest and s.share_grant_id=g.id
         and s.organization_tenant_id=g.organization_tenant_id
       join ops.tour t on t.organization_tenant_id=p.organization_tenant_id and t.id=p.tour_id
       where p.status='approved' and p.route_version=t.route_version
         and exists(select 1 from ops.tour_public_projection_seal_receipt seal
           where seal.organization_tenant_id=p.organization_tenant_id and seal.projection_id=p.id and seal.canonical_projection_digest=p.projection_digest)
         and not exists(select 1 from ops.tour_public_projection newer where newer.organization_tenant_id=p.organization_tenant_id
           and newer.tour_id=p.tour_id and newer.projection_version>p.projection_version and newer.status in ('approved','published'))
         and ops.tour_public_projection_client_safe(p.organization_tenant_id,p.id)
         and ops.read_tour_public_projection(p.organization_tenant_id,p.id) is not null),
  items as (select p.organization_tenant_id,p.id projection_id,m.property_id,
    'property:public:'||substr(encode(public.digest(p.organization_tenant_id||':'||p.id::text||':'||m.property_id::text,'sha256'),'hex'),1,32) property_ref
    from p join ops.tour_property_membership m on m.organization_tenant_id=p.organization_tenant_id and m.tour_id=p.tour_id and m.route_version=p.route_version)
  select jsonb_build_object('projection_ref','projection:public:'||substr(encode(public.digest(p.organization_tenant_id||':'||p.id::text||':'||p.projection_digest,'sha256'),'hex'),1,32),
    'permission_scopes',p.permission_scopes,
    'items',coalesce((select jsonb_agg(jsonb_build_object('property_ref',i.property_ref) order by i.property_ref) from items i),'[]'::jsonb)) from p;
$$;
