-- rollback: forward-only; appended criteria are scope the partners asked for, and the arm admits no other change.
-- expand-contract: expand the sourced Work Request guard with one append-only scope arm.
-- lock-review: replaces one trigger function body; no table rewrite or scan.
-- WR663.S1.J1: append-work-request-scope adds criteria after the recorded ones and may extend desired_outcome.
do $scope$
declare definition text; marker text := '  if old.state = ''captured''';
begin
  definition := pg_get_functiondef('ops.sourced_work_request_is_immutable()'::regprocedure);
  if position('-- scope append' in definition) = 0 then
    if position(marker in definition) = 0 then raise exception 'Sourced lifecycle insertion point drifted'; end if;
    execute regexp_replace(definition, marker, $arm$  -- scope append keeps every recorded criterion and the outcome text as an exact prefix.
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
                                   or coalesce(x.e->>'id','') !~ '^[A-Z][A-Z0-9-]{1,63}$'
                                   or coalesce(btrim(x.e->>'text'),'')='' or char_length(x.e->>'text')>500 end)
     and (select count(distinct e->>'id') from jsonb_array_elements(new.acceptance_criteria) e)=jsonb_array_length(new.acceptance_criteria)
     and (new.desired_outcome is not distinct from old.desired_outcome
          or (old.desired_outcome is not null and char_length(new.desired_outcome)<=4000
              and left(new.desired_outcome,char_length(old.desired_outcome))=old.desired_outcome)) then
    return new;
  end if;
$arm$ || marker);
  end if;
end $scope$;
