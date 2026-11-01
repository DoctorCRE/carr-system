-- WR-000668: team-memory section keys are slash-scoped (`lessons/...`), and
-- report-problem ranks them as situation sources. The origin-ref shape allowed
-- no `/`, so every such capture raised a bare P0001 that reached callers as
-- unhandled_verb_failure. Admit slash-separated key segments in both checks;
-- the function still requires the ref to name its exact section.
-- rollback: forward-only — slash-scoped references may already be stored and cannot satisfy the old constraint.
-- lock-review: ops.work_request size is unmeasured; replacing its CHECK takes ACCESS EXCLUSIVE and scans all rows.
set local lock_timeout = '5s';
set local statement_timeout = '60s';

do $origin_ref$
declare
  narrow constant text := '#[a-z0-9][a-z0-9-]*$';
  wide constant text := '#[a-z0-9][a-z0-9-]*(/[a-z0-9][a-z0-9-]*)*$';
  definition text;
begin
  definition := pg_get_functiondef('ops.capture_sourced_work_request(text,text,text,jsonb,uuid,uuid,uuid)'::regprocedure);
  if position(narrow in definition) = 0 then raise exception 'capture origin-ref shape drifted'; end if;
  execute replace(definition, narrow, wide);

  select pg_get_constraintdef(oid) into definition from pg_constraint
   where conrelid = 'ops.work_request'::regclass and conname = 'work_request_sourced_capture_shape';
  if position(narrow in definition) = 0 then raise exception 'work_request_sourced_capture_shape origin-ref shape drifted'; end if;
  alter table ops.work_request drop constraint work_request_sourced_capture_shape;
  execute 'alter table ops.work_request add constraint work_request_sourced_capture_shape ' || replace(definition, narrow, wide);
end $origin_ref$;
