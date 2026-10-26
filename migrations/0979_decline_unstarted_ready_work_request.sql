-- rollback: forward-only; declined ready rows retain their accepted plans and receipts
-- lock-review: replace two functions without rewriting or locking business tables

do $ready_decline$
declare
  v_definition text;
  v_marker constant text := $marker$  if old.state = 'triaged'
     and new.state = 'declined'$marker$;
  v_branch constant text := $branch$  if old.state = 'ready' and new.state = 'declined' and (
       old.claimed_at is not null or old.executor_actor is not null or old.started_at is not null
       or exists (select 1 from ops.work_request_execution_assignment a where a.work_request_id = old.id)
       or current_setting('transaction_isolation') <> 'read committed'
     ) then
    raise exception 'work_request_not_declinable: % is ready with downstream execution or outside read committed', old.ref;
  end if;

  if old.state in ('triaged','ready')
     and new.state = 'declined'$branch$;
begin
  select pg_get_functiondef('ops.sourced_work_request_is_immutable()'::regprocedure)
    into v_definition;
  if (length(v_definition)-length(replace(v_definition,v_marker,'')))/length(v_marker)<>1 then
    raise exception '0974 refused: sourced Work Request lifecycle predecessor shape drifted';
  end if;
  v_definition := replace(v_definition,v_marker,v_branch);
  execute replace(v_definition,
    'or triaged-to-withdrawn transitions', 'or triaged-to-withdrawn or ready-to-declined transitions');

  select pg_get_functiondef('ops.decline_sourced_work_request(text,integer,text,text,uuid)'::regprocedure)
    into v_definition;
  if (length(v_definition)-length(replace(v_definition,$old$v_state not in ('captured','triaged')$old$,'')))
      /length($old$v_state not in ('captured','triaged')$old$)<>1 then
    raise exception '0974 refused: decline Work Request predecessor shape drifted';
  end if;
  v_definition := replace(v_definition,
    $old$v_state not in ('captured','triaged')$old$, $new$v_state not in ('captured','triaged','ready')$new$);
  execute replace(v_definition,
    'only captured or triaged sourced requests may be declined',
    'only captured, triaged, or unstarted ready sourced requests may be declined');
end $ready_decline$;
