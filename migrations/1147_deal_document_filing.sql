-- rollback: forward-only; retain canonical attachments and filing receipts.
-- lock-review: metadata columns and an empty partial expiry index.
alter table ops.deal_document_upload drop constraint deal_document_upload_state_check;
alter table ops.deal_document_upload add constraint deal_document_upload_state_check
 check(state in ('reserved','receiving','staged','rejected','retryable_unknown','cancelled','filed','expired'));
alter table ops.deal_document_upload
 add column attachment_id uuid references public.attachment(id),
 add column commit_key uuid,
 add column cleanup_attempts integer not null default 0 check(cleanup_attempts between 0 and 2),
 add column cleanup_error text,
 add column cleaned_at timestamptz,
 add constraint deal_document_upload_commit_key unique(organization_tenant_id,actor_slug,commit_key),
 add constraint deal_document_upload_filed check((state='filed')=(attachment_id is not null));
create index deal_document_upload_expiry on ops.deal_document_upload(expires_at,id)
 where cleaned_at is null and cleanup_attempts<2;
