-- 0583: tour_pdf_render_result.storage_ref's pattern used an upper repetition
-- bound of 400 -- '^tour-pdf/[A-Za-z0-9._/-]{16,400}\.pdf$' -- which exceeds
-- PostgreSQL's regex engine repetition-count ceiling (RE_DUP_MAX, 255; error
-- 2201B invalid_regular_expression: "invalid repetition count(s)"). Every
-- attempt to validate or insert a REAL (non-null) storage_ref against this
-- pattern raised that engine error instead of matching or rejecting, in both
-- the ops.record_tour_pdf_render_result guard and the table's own CHECK
-- constraint. A NULL storage_ref (the "failed" status path) short-circuits
-- past the check and was never affected, which is why failure receipts kept
-- writing while every real render result failed.
--
-- Root-caused 2026-09-24 against render job b87f59da-a915-4d5d-97fd-ad4171db0938:
-- its persisted qc_run_digest, sha256("tour-pdf-render-failure:v1:error"),
-- decodes to error.name === "error" -- the literal PostgreSQL wire-protocol
-- ErrorResponse tag pg-protocol's DatabaseError carries, not a "raise
-- exception" from application validation. Reproduced deterministically on a
-- disposable local PostgreSQL 17: `select 'x' !~ '^a{16,400}$'` raises
-- SQLSTATE 2201B on this engine.
--
-- FIX: lower the bound to 255 (the engine's own ceiling) on both the CHECK
-- constraint and the function's guard. 255 remains generous for the actual
-- shape produced by mcp-server/src/tour-pdf-service.js:
-- tour-pdf/<sanitized-tenant>/<uuid:36>/<sha256-hex:64>.pdf -- the tenant
-- would need to run past roughly 150 characters to approach the new ceiling.
-- mcp-server/src/tour-artifacts.js's STORAGE_REF regex is lowered to match in
-- the same commit, so the app-layer and database-layer bounds cannot drift
-- apart again.

alter table ops.tour_pdf_render_result
  drop constraint tour_pdf_render_result_storage_ref_check,
  add constraint tour_pdf_render_result_storage_ref_check
    check (storage_ref is null or storage_ref ~ '^tour-pdf/[A-Za-z0-9._/-]{16,255}\.pdf$');

create or replace function ops.record_tour_pdf_render_result(p_tenant text,p_render_job_id uuid,p_status text,p_artifact_ref text,p_artifact_digest text,p_storage_ref text,p_content_length integer,p_page_count integer,p_blocking_finding_count integer,p_qc_run_digest text,p_actor_id text)
returns uuid language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_job ops.tour_pdf_render_job%rowtype; v_attempt integer; v_id uuid;
begin
  perform pg_advisory_xact_lock(hashtextextended(p_tenant||':'||p_render_job_id::text||':pdf-result',403));
  select * into v_job from ops.tour_pdf_render_job where organization_tenant_id=p_tenant and id=p_render_job_id for share;
  if not found or p_status not in ('review_ready','qc_blocked','failed') or nullif(btrim(p_actor_id),'') is null
     or p_qc_run_digest !~ '^sha256:[a-f0-9]{64}$' or p_blocking_finding_count<0
     or exists(select 1 from ops.tour_pdf_human_review h where h.organization_tenant_id=p_tenant and h.render_job_id=p_render_job_id)
     or (p_status='failed' and (p_artifact_ref is not null or p_artifact_digest is not null or p_storage_ref is not null
       or p_content_length is not null or p_page_count is not null or p_blocking_finding_count<>0))
     or (p_status<>'failed' and (p_artifact_digest !~ '^sha256:[a-f0-9]{64}$'
       or p_page_count<>v_job.expected_property_count or p_content_length<=0
       or (p_status='review_ready' and p_blocking_finding_count<>0)
       or p_artifact_ref !~ '^artifact:tour-pdf:[A-Za-z0-9_-]{16,128}$'
       or p_storage_ref !~ '^tour-pdf/[A-Za-z0-9._/-]{16,255}\.pdf$')) then
    raise exception 'tour PDF render result is invalid';
  end if;
  select coalesce(max(attempt_count),0)+1 into v_attempt from ops.tour_pdf_render_result where organization_tenant_id=p_tenant and render_job_id=p_render_job_id;
  insert into ops.tour_pdf_render_result(organization_tenant_id,render_job_id,status,artifact_ref,artifact_digest,storage_ref,content_length,page_count,blocking_finding_count,qc_run_digest,attempt_count,completed_at)
  values(p_tenant,p_render_job_id,p_status,p_artifact_ref,p_artifact_digest,p_storage_ref,p_content_length,p_page_count,p_blocking_finding_count,p_qc_run_digest,v_attempt,now()) returning id into v_id;
  return v_id;
end $$;
