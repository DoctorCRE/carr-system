-- rollback: preserve job receipts; remove the natural-key index and restore UUID-only reads.
-- lock-review: extraction job table is new in this stack; existing field locks serialize writers.

create unique index deal_extraction_job_identity on ops.deal_extraction_job
  (organization_tenant_id,document_ref,content_hash,(contract->>'extractor_version'),(contract->>'model'));

create or replace function ops.read_deal_extraction_source(p_tenant text,p_actor_id uuid,p_contract jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare doc public.attachment%rowtype; prior ops.deal_extraction_job%rowtype;
begin
  if p_tenant is distinct from 'carr-internal' or not exists(select 1 from public.actor
    where id=p_actor_id and kind='automation' and active) then
    return jsonb_build_object('ok',false,'error','not_in_profile');
  end if;
  select * into prior from ops.deal_extraction_job where organization_tenant_id=p_tenant
    and id=(p_contract->>'idempotency_key')::uuid;
  if found then
    if prior.contract<>p_contract or prior.actor_id<>p_actor_id then
      return jsonb_build_object('ok',false,'error','key_reuse');
    end if;
    return jsonb_build_object('ok',true,'receipt',prior.receipt||jsonb_build_object('replayed',true));
  end if;
  select * into doc from public.attachment where id=(p_contract->>'document_ref')::uuid
    and subject_type='deal' and subject_id::text=p_contract->>'entity_ref' and deleted_at is null;
  if not found then return jsonb_build_object('ok',false,'error','document_not_found'); end if;
  if doc.mime<>'application/pdf' or doc.bytes not between 1 and 26214400 then
    return jsonb_build_object('ok',false,'error','unsupported_document');
  end if;
  select * into prior from ops.deal_extraction_job
    where organization_tenant_id=p_tenant and document_ref=doc.id and content_hash=doc.sha256
      and contract->>'extractor_version'=p_contract->>'extractor_version'
      and contract->>'model'=p_contract->>'model';
  if found then
    return jsonb_build_object('ok',true,'receipt',prior.receipt||jsonb_build_object('replayed',true));
  end if;
  return jsonb_build_object('ok',true,'receipt',null,'document',jsonb_build_object(
    'document_ref',doc.id,'entity_ref',doc.subject_id,'organization_tenant_id',p_tenant,
    'content_hash',doc.sha256,'document_family_id','sha256:'||doc.sha256,
    'lineage_verified',false,'r2_key',doc.r2_key,'mime',doc.mime,'bytes',doc.bytes));
end $$;
