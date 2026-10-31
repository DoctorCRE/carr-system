-- rollback: forward-only; appended criteria are scope the partners asked for, and the arm admits no other change.
-- expand-contract: expand the sourced Work Request guard with one receipt-backed, append-only scope arm.
-- lock-review: replaces one trigger function body; no table rewrite or scan.
-- WR663.S1.J1: append-work-request-scope adds criteria after the recorded ones and may extend desired_outcome.
do $scope$
declare definition text; marker text := '  if old.state = ''captured''';
begin
  definition := pg_get_functiondef('ops.sourced_work_request_is_immutable()'::regprocedure);
  if position('-- scope append' in definition) = 0 then
    if position(marker in definition) = 0 then raise exception 'Sourced lifecycle insertion point drifted'; end if;
    definition := regexp_replace(definition, marker, $arm$  -- scope append: recorded criteria stay an exact prefix and the verb's event is the receipt.
  if old.state in ('captured','triaged') and new.state=old.state and new.version=old.version+1
     and (to_jsonb(new)-array['acceptance_criteria','desired_outcome','version','updated_at'])
       is not distinct from
       (to_jsonb(old)-array['acceptance_criteria','desired_outcome','version','updated_at'])
     and jsonb_typeof(new.acceptance_criteria)='array'
     and jsonb_array_length(new.acceptance_criteria) between jsonb_array_length(old.acceptance_criteria)+1 and 12
     and coalesce((select jsonb_agg(x.e order by x.o) from jsonb_array_elements(new.acceptance_criteria) with ordinality x(e,o)
                    where x.o<=jsonb_array_length(old.acceptance_criteria)),'[]'::jsonb)=old.acceptance_criteria
     and not exists (select 1 from jsonb_array_elements(new.acceptance_criteria) with ordinality x(e,o)
                      where x.o>jsonb_array_length(old.acceptance_criteria)
                        and case when jsonb_typeof(x.e)<>'object' then true
                                 else (select array_agg(k order by k) from jsonb_object_keys(x.e) k) is distinct from array['id','text']
                                   or jsonb_typeof(x.e->'id') is distinct from 'string'
                                   or jsonb_typeof(x.e->'text') is distinct from 'string'
                                   or x.e->>'id' !~ '^[A-Z][A-Z0-9-]{1,63}$'
                                   or btrim(x.e->>'text')='' or char_length(x.e->>'text')>500 end)
     and (select count(distinct e->>'id') from jsonb_array_elements(new.acceptance_criteria) e)=jsonb_array_length(new.acceptance_criteria)
     and coalesce(char_length(new.desired_outcome),0)<=4000
     and exists (
       select 1 from public.event ev
        where ev.verb='append-work-request-scope' and ev.subject_type='ops_work_request' and ev.subject_id=old.id
          and ev.idempotency_key is not null
          and ev.new_value->'version'=to_jsonb(new.version)
          and ev.new_value->'appended'=(select jsonb_agg(x.e order by x.o) from jsonb_array_elements(new.acceptance_criteria) with ordinality x(e,o)
                                         where x.o>jsonb_array_length(old.acceptance_criteria))
          and case when ev.new_value->>'outcome_addendum' is null then new.desired_outcome is not distinct from old.desired_outcome
                   else new.desired_outcome=coalesce(nullif(old.desired_outcome,'')||chr(10)||chr(10),'')||(ev.new_value->>'outcome_addendum') end
     ) then
    return new;
  end if;
$arm$ || marker);
    definition := replace(definition, 'or ready-to-declined transitions', 'or ready-to-declined transitions, or receipt-backed scope appends');
    execute definition;
  end if;
end $scope$;
