-- 0319_engineering_envelope_writer_successor.sql
--
-- The Engineering Passport successor trigger runs for the constrained MCP
-- writer.  0311 correctly keeps engineering envelopes append-only: carr_writer
-- has INSERT and SELECT, but never UPDATE.  Its predecessor lookup used
-- `FOR KEY SHARE`, which nevertheless needs UPDATE privilege and made every
-- expired-envelope replacement fail with SQLSTATE 42501.  The keyed advisory
-- lock already serializes this exact lineage, so the row lock adds no safety.

begin;

create or replace function ops.guard_engineering_envelope_supersession()
returns trigger language plpgsql
set search_path=pg_catalog,ops,public
as $$
declare prior ops.engineering_execution_envelope%rowtype;
        prior_count integer;
begin
  perform pg_advisory_xact_lock(hashtextextended(
    'engineering-envelope:' || new.slice_plan_id::text || ':' || new.slice_ref, 0));
  select count(*) into prior_count
    from ops.engineering_execution_envelope
   where slice_plan_id=new.slice_plan_id and slice_ref=new.slice_ref;
  if prior_count=0 then
    if new.supersedes_envelope_id is not null then
      raise exception 'first engineering envelope cannot supersede another envelope';
    end if;
    return new;
  end if;
  if new.supersedes_envelope_id is null then
    raise exception 'later engineering envelope must name its immutable predecessor';
  end if;
  select * into prior from ops.engineering_execution_envelope
   where id=new.supersedes_envelope_id;
  if not found or prior.slice_plan_id<>new.slice_plan_id or prior.slice_ref<>new.slice_ref
     or prior.accepted_plan_id<>new.accepted_plan_id or prior.work_request_id<>new.work_request_id then
    raise exception 'engineering envelope predecessor is outside the exact slice binding';
  end if;
  if exists (select 1 from ops.engineering_execution_envelope
              where supersedes_envelope_id=prior.id) then
    raise exception 'engineering envelope predecessor already has a successor';
  end if;
  if prior.expires_at>now()
     and coalesce((prior.envelope->'server_binding'->'authority'->>'read_only')::boolean,true)=false
     and not exists (select 1 from ops.engineering_slice_receipt r
                      where r.envelope_id=prior.id and r.outcome in ('failed','blocked','reopened')) then
    raise exception 'current executable engineering envelope cannot be superseded';
  end if;
  return new;
end $$;

-- The safe repair is removal of the unnecessary row lock, not a wider grant.
do $$
begin
  if has_table_privilege('carr_writer', 'ops.engineering_execution_envelope', 'update') then
    raise exception '0319 FAILED: carr_writer may update append-only engineering envelopes';
  end if;
  if not has_table_privilege('carr_writer', 'ops.engineering_execution_envelope', 'insert')
     or not has_table_privilege('carr_writer', 'ops.engineering_execution_envelope', 'select') then
    raise exception '0319 FAILED: carr_writer cannot create or read engineering envelopes';
  end if;
end $$;

commit;
