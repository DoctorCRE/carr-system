-- WR-000095: PostgreSQL's regular-expression engine caps finite repetition
-- bounds at 255. The Journey One JavaScript contracts legitimately allow
-- references up to 295/300 characters, but their literal {m,n} translations
-- therefore raise SQLSTATE 2201B before evaluating any value. Preserve the
-- exact domains with an unbounded character class plus an explicit length
-- comparison. Applied functions are replaced from their catalog definitions
-- only when every expected prior expression is present. The same forward fix
-- also binds the minimum receipt's observation to the transaction timestamp
-- used by its admission, avoiding impossible future-observed admissions.

do $repair$
declare
  v_scope text;
  v_guard text;
  v_material text;
  v_old text;
  v_new text;
begin
  select pg_get_functiondef('ops.j1_clock_scope_digest(jsonb)'::regprocedure)
    into v_scope;
  v_old := 'if coalesce(p_scope ->> ''scope_ref'', '''') !~ ''^safe:[A-Za-z0-9:._/-]{3,290}$'' then';
  v_new := 'if coalesce(p_scope ->> ''scope_ref'', '''') !~ ''^safe:[A-Za-z0-9:._/-]+$'''
    || E'\n     or char_length(coalesce(p_scope ->> ''scope_ref'', '''')) not between 8 and 295 then';
  if strpos(v_scope, v_old) = 0 then
    raise exception '0515 expected Journey One scope reference guard is unavailable';
  end if;
  v_scope := replace(v_scope, v_old, v_new);
  execute v_scope;

  select pg_get_functiondef('ops.j1_minimum_append_guard()'::regprocedure)
    into v_guard;
  v_old := 'or coalesce(new.receipt ->> ''evidence_ref'', '''') !~ ''^safe:[A-Za-z0-9:._/-]{0,295}$'' then';
  v_new := 'or coalesce(new.receipt ->> ''evidence_ref'', '''') !~ ''^safe:[A-Za-z0-9:._/-]*$'''
    || E'\n     or char_length(coalesce(new.receipt ->> ''evidence_ref'', '''')) not between 5 and 300 then';
  if strpos(v_guard, v_old) = 0 then
    raise exception '0515 expected minimum evidence reference guard is unavailable';
  end if;
  v_guard := replace(v_guard, v_old, v_new);

  v_old := 'if (new.receipt -> v_field ->> ''session_ref'') !~ ''^session:[A-Za-z0-9:._/-]{0,292}$'' then';
  v_new := 'if (new.receipt -> v_field ->> ''session_ref'') !~ ''^session:[A-Za-z0-9:._/-]*$'''
    || E'\n       or char_length(new.receipt -> v_field ->> ''session_ref'') not between 8 and 300 then';
  if strpos(v_guard, v_old) = 0 then
    raise exception '0515 expected minimum session reference guard is unavailable';
  end if;
  v_guard := replace(v_guard, v_old, v_new);
  execute v_guard;

  -- The minimum receipt is produced after this transaction begins. Its
  -- observed_at must use the same transaction timestamp as the Journey One
  -- admission instant; clock_timestamp() can be milliseconds later and makes
  -- the append-only guard correctly reject the receipt as future-observed.
  select pg_get_functiondef(
    'ops.foundation_assurance_producer_material(text,jsonb,jsonb)'::regprocedure)
    into v_material;
  v_old := '''gate_zero'',ops.benchmark_gate_zero_outcome(),''observed_at'',to_jsonb(clock_timestamp()));';
  v_new := '''gate_zero'',ops.benchmark_gate_zero_outcome(),''observed_at'',to_jsonb(now()));';
  if strpos(v_material, v_old) = 0 then
    raise exception '0515 expected minimum observation timestamp is unavailable';
  end if;
  v_material := replace(v_material, v_old, v_new);
  execute v_material;
end
$repair$;

alter table ops.j1_clock_scope_binding
  drop constraint j1_clock_scope_binding_clock_scope_ref_check,
  add constraint j1_clock_scope_binding_clock_scope_ref_check
    check (clock_scope_ref ~ '^safe:[A-Za-z0-9:._/-]+$'
           and char_length(clock_scope_ref) between 8 and 295);

alter table ops.j1_clock_revision
  drop constraint j1_clock_revision_verifier_ref_check,
  add constraint j1_clock_revision_verifier_ref_check
    check (verifier_ref ~ '^safe:[A-Za-z0-9:._/-]+$'
           and char_length(verifier_ref) between 8 and 295);

alter table ops.j1_minimum_inventory
  drop constraint j1_minimum_inventory_clock_scope_ref_check,
  add constraint j1_minimum_inventory_clock_scope_ref_check
    check (clock_scope_ref ~ '^safe:[A-Za-z0-9:._/-]+$'
           and char_length(clock_scope_ref) between 8 and 295);

alter table ops.j1_minimum_admission
  drop constraint j1_minimum_admission_source_ref_check,
  add constraint j1_minimum_admission_source_ref_check
    check (source_ref ~ '^safe:[A-Za-z0-9:._/-]+$'
           and char_length(source_ref) between 8 and 295);
