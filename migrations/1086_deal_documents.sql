-- rollback: remove the view after retiring list-deal-documents.
-- lock-review: creates a metadata-only view; no attachment rows are changed.
create view public.v_deal_documents as
select 'carr-internal'::text as organization_tenant_id,
  a.subject_id as deal_id, a.id, a.filename as display_name,
  a.mime as document_type, null::integer as page_count,
  a.sha256 as content_hash, a.created_at as stored_at
from public.attachment a
where a.subject_type='deal' and a.deleted_at is null
  and exists(select 1 from public.v_ref_index d
    where d.subject_type='deal' and d.subject_id=a.subject_id);
grant select on public.v_deal_documents to carr_reader;
