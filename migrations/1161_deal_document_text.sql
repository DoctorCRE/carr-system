-- rollback: retire text verbs before dropping derivatives; originals remain untouched.
-- lock-review: empty derivative table and indexes; no attachment rewrites.
create table ops.deal_document_text (
 document_ref uuid not null references public.attachment(id),
 entity_ref uuid not null,
 content_hash text not null check(content_hash ~ '^[0-9a-f]{64}$'),
 text_format_version text not null check(text_format_version='page-text.v1'),
 status text not null check(status in ('produced','no_text','unavailable')),
 pages jsonb not null check(jsonb_typeof(pages)='array' and jsonb_array_length(pages)<=1000),
 text_hash text not null,
 created_at timestamptz not null default now(),
 primary key(document_ref,content_hash,text_format_version)
);
revoke all on ops.deal_document_text from public;
grant select on ops.deal_document_text to carr_reader;

create function ops.record_deal_document_text(p_actor uuid,p_args jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare doc public.attachment%rowtype; existing ops.deal_document_text%rowtype; page jsonb;
begin
 if not exists(select 1 from public.actor where id=p_actor and kind='automation' and active) then
   return jsonb_build_object('ok',false,'error','not_in_profile');
 end if;
 select * into doc from public.attachment where id=(p_args->>'document_ref')::uuid
   and subject_type='deal' and subject_id=(p_args->>'entity_ref')::uuid and deleted_at is null for share;
 if not found then return jsonb_build_object('ok',false,'error','document_not_found'); end if;
 if doc.sha256 is distinct from p_args->>'content_hash' then
   return jsonb_build_object('ok',false,'error','source_changed');
 end if;
 if p_args->>'text_format_version' is distinct from 'page-text.v1'
   or p_args->>'status' not in ('produced','no_text','unavailable')
   or jsonb_typeof(p_args->'pages') is distinct from 'array' then
   return jsonb_build_object('ok',false,'error','validation_error');
 end if;
 if jsonb_array_length(p_args->'pages')>1000 then return jsonb_build_object('ok',false,'error','validation_error'); end if;
 for page in select value from jsonb_array_elements(p_args->'pages') loop
   if jsonb_typeof(page)<>'string' then return jsonb_build_object('ok',false,'error','validation_error'); end if;
 end loop;
 if (select coalesce(sum(length(value)),0) from jsonb_array_elements_text(p_args->'pages'))>500000
   or (p_args->>'status'='unavailable' and p_args->'pages'<>'[]'::jsonb)
   or (p_args->>'status'='produced') is distinct from exists(
     select 1 from jsonb_array_elements_text(p_args->'pages') where value ~ '[^[:space:]]') then
   return jsonb_build_object('ok',false,'error','validation_error');
 end if;
 insert into ops.deal_document_text(document_ref,entity_ref,content_hash,text_format_version,status,pages,text_hash)
 values(doc.id,doc.subject_id,doc.sha256,'page-text.v1',p_args->>'status',p_args->'pages',
   encode(sha256(convert_to((p_args->'pages')::text,'UTF8')),'hex')) on conflict do nothing;
 select * into existing from ops.deal_document_text where document_ref=doc.id and content_hash=doc.sha256
   and text_format_version='page-text.v1';
 if existing.pages is distinct from p_args->'pages' or existing.status is distinct from p_args->>'status'
   or existing.entity_ref<>doc.subject_id then
   return jsonb_build_object('ok',false,'error','derivative_conflict');
 end if;
 return jsonb_build_object('ok',true,'content_hash',doc.sha256,'text_hash',existing.text_hash);
end $$;
revoke all on function ops.record_deal_document_text(uuid,jsonb) from public;
grant execute on function ops.record_deal_document_text(uuid,jsonb) to carr_writer;
do $append_grant$
declare contract jsonb := ops.scac_current_grant_contract();
begin
 contract:=jsonb_set(contract,'{secdef_execute}',contract->'secdef_execute'||jsonb_build_array(jsonb_build_object(
 'grantee','carr_writer','signature','ops.record_deal_document_text(p_actor uuid, p_args jsonb)',
 'grantable',false,'privilege','execute',
 'ingress_key','db-function-acl:ops.record_deal_document_text(p_actor uuid, p_args jsonb):carr_writer:execute',
 'ingress_kind','db_function_acl','function_kind','f','security_definer',true,
 'volatility','v','parallel','u','config',jsonb_build_array('search_path=pg_catalog, pg_temp'))));
 execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L',
 'select '||quote_literal(contract::text)||'::jsonb');
end $append_grant$;
