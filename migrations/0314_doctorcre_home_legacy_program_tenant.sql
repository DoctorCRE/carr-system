-- Bind the fixed 0125 AI capability program to CARR's organization tenant.
--
-- These rows predate sourced Program 6 capture. They remain legacy program
-- rows: this repair adds only tenant provenance and must not manufacture a
-- capture receipt, doctrine source, triage receipt, new timestamp, or version.
-- The closed-row exception is deliberately local, exact-program, NULL-to-CARR,
-- and byte-for-byte identical after removing organization_tenant_id.

begin;

do $$
begin
  if exists (
    select 1 from ops.work_request
     where program_key = 'carr-ai-engineering-suite-v1'
       and (
         program_ordinal is null or program_ordinal <= 0
         or requester_actor is distinct from 'joe'
         or owner_actor is distinct from 'joe'
         or capture_idempotency_key is not null
         or doctrine_section_id is not null
         or doctrine_revision_id is not null
         or sourced_capture_sequence is not null
         or triage_classification is not null
         or triaged_by_actor_id is not null
         or triaged_at is not null
         or organization_tenant_id not in ('carr-internal')
       )
  ) then
    raise exception '0314 FAILED: exact legacy capability program provenance drifted';
  end if;

  if exists (
    select 1 from ops.work_request
     where capture_idempotency_key is null
       and program_key is not null
       and program_key <> 'carr-ai-engineering-suite-v1'
  ) then
    raise exception '0314 FAILED: an unreviewed legacy program identity exists';
  end if;
end;
$$;

create temporary table legacy_program_tenant_backfill_before
on commit drop
as
select id, to_jsonb(w) - 'organization_tenant_id' as immutable_row
  from ops.work_request w
 where program_key = 'carr-ai-engineering-suite-v1';

create unique index legacy_program_tenant_backfill_before_id
  on legacy_program_tenant_backfill_before(id);

create or replace function ops.capability_program_closed_immutable()
returns trigger language plpgsql as $$
begin
  if old.program_key = 'carr-ai-engineering-suite-v1'
     and old.state = 'confirmed_closed'
     and new is distinct from old then
    if coalesce(current_setting('carr.legacy_program_tenant_backfill', true), '') = 'on'
       and old.organization_tenant_id is null
       and new.organization_tenant_id = 'carr-internal'
       and (to_jsonb(new) - 'organization_tenant_id')
           is not distinct from
           (to_jsonb(old) - 'organization_tenant_id') then
      return new;
    end if;
    raise exception 'closed capability programme evidence is immutable';
  end if;
  return new;
end;
$$;

alter table ops.work_request
  drop constraint if exists work_request_sourced_capture_shape;

alter table ops.work_request
  add constraint work_request_sourced_capture_shape check (
    (
      capture_idempotency_key is null
      and organization_tenant_id is null
      and doctrine_section_id is null
      and doctrine_revision_id is null
      and sourced_capture_sequence is null
      and triage_classification is null
      and triaged_by_actor_id is null
      and triaged_at is null
      and program_key is null
      and program_ordinal is null
    )
    or
    (
      capture_idempotency_key is null
      and organization_tenant_id is not distinct from 'carr-internal'
      and doctrine_section_id is null
      and doctrine_revision_id is null
      and sourced_capture_sequence is null
      and triage_classification is null
      and triaged_by_actor_id is null
      and triaged_at is null
      and program_key is not distinct from 'carr-ai-engineering-suite-v1'
      and program_ordinal is not null
      and program_ordinal > 0
      and requester_actor is not distinct from 'joe'
      and owner_actor is not distinct from 'joe'
    )
    or
    (
      capture_idempotency_key is not null
      and organization_tenant_id is not distinct from 'carr-internal'
      and doctrine_section_id is not null
      and doctrine_revision_id is not null
      and sourced_capture_sequence is not null
      and program_key is null
      and program_ordinal is null
      and origin_ref is not null
      and origin_ref ~ '^doctrine:[a-z0-9][a-z0-9-]*#[a-z0-9][a-z0-9-]*$'
      and (
        (
          state = 'captured'
          and triage_classification is null
          and triaged_by_actor_id is null
          and triaged_at is null
        )
        or
        (
          state in ('triaged', 'ready')
          and triage_classification in ('operational', 'needs_judgment', 'safety_review')
          and triaged_by_actor_id is not null
          and triaged_at is not null
        )
      )
    )
  ) not valid;

set local carr.legacy_program_tenant_backfill = 'on';

update ops.work_request
   set organization_tenant_id = 'carr-internal'
 where program_key = 'carr-ai-engineering-suite-v1'
   and organization_tenant_id is null;

set local carr.legacy_program_tenant_backfill = 'off';

do $$
begin
  if exists (
    select 1 from ops.work_request
     where program_key = 'carr-ai-engineering-suite-v1'
       and (
         organization_tenant_id is distinct from 'carr-internal'
         or program_ordinal is null or program_ordinal <= 0
         or requester_actor is distinct from 'joe'
         or owner_actor is distinct from 'joe'
         or capture_idempotency_key is not null
         or doctrine_section_id is not null
         or doctrine_revision_id is not null
         or sourced_capture_sequence is not null
         or triage_classification is not null
         or triaged_by_actor_id is not null
         or triaged_at is not null
       )
  ) then
    raise exception '0314 FAILED: exact legacy capability program repair is incomplete';
  end if;

  if exists (
    select 1
      from legacy_program_tenant_backfill_before b
      join ops.work_request w on w.id = b.id
     where (to_jsonb(w) - 'organization_tenant_id')
           is distinct from b.immutable_row
  ) then
    raise exception '0314 FAILED: tenant repair changed immutable program evidence';
  end if;

  if (
    select count(*) from legacy_program_tenant_backfill_before
  ) is distinct from (
    select count(*) from ops.work_request
     where program_key = 'carr-ai-engineering-suite-v1'
  ) then
    raise exception '0314 FAILED: tenant repair added or removed a program row';
  end if;
end;
$$;

alter table ops.work_request
  validate constraint work_request_sourced_capture_shape;

comment on column ops.work_request.organization_tenant_id is
  'Server-derived CARR organization tenant for sourced Program 6 capture and the exact fixed carr-ai-engineering-suite-v1 legacy program. Other historical rows remain null.';

commit;
