-- WR-000111 — the measured grant and admission proof for the producer cost
-- ledger. Run by ops/ci.sh's migration class against the disposable cluster.
--
-- WHAT ONLY A DATABASE CAN SHOW: that the five relations really do carry no
-- row-changing privilege for any runtime bundle, that the column-scoped SELECT
-- grants really do exclude argument_digest and correlation_id, that the three
-- definer functions really are authority-only, and that the version-and-digest
-- predicate really does refuse a second caller standing on a moved cell.

\set ON_ERROR_STOP on

do $wr111_grants$
declare v_bad text;
begin
  -- 1. EXECUTE on the three definer functions: carr_authority and nobody else.
  for v_bad in
    select fn from unnest(array[
      'ops.initialize_cost_scope_tree(text,jsonb,text)',
      'ops.lock_cost_ledger_cell(text)',
      'ops.commit_cost_ledger_operation(text,integer,text,text,text,text,jsonb,text,integer,text,jsonb)'
    ]) fn
    where not has_function_privilege('carr_authority', fn, 'execute')
       or has_function_privilege('carr_writer', fn, 'execute')
       or has_function_privilege('carr_reader', fn, 'execute')
       or has_function_privilege('carr_jobs', fn, 'execute')
       or has_function_privilege('public', fn, 'execute')
  loop
    raise exception 'WR-000111 grant boundary: % is not authority-only', v_bad;
  end loop;

  -- 2. No runtime bundle holds a row-changing privilege on any of the five.
  for v_bad in
    select rel from unnest(array['ops.cost_scope_tree','ops.cost_scope_node',
      'ops.cost_ledger_operation','ops.cost_ledger_entry','ops.cost_ledger_cell']) rel
    cross join unnest(array['carr_reader','carr_writer','carr_jobs','carr_authority','public']) grantee
    cross join unnest(array['insert','update','delete','truncate']) priv
    where has_table_privilege(grantee, rel, priv)
  loop
    raise exception 'WR-000111 write boundary: % is writable by a runtime bundle', v_bad;
  end loop;

  -- 3. The column grant is column-SCOPED: argument_digest and correlation_id
  --    are excluded, which is the whole difference between a scoped grant and a
  --    decorative one.
  if not has_column_privilege('carr_reader','ops.cost_ledger_operation','outcome','select')
     or has_column_privilege('carr_reader','ops.cost_ledger_operation','argument_digest','select')
     or has_column_privilege('carr_reader','ops.cost_ledger_operation','correlation_id','select') then
    raise exception 'WR-000111 column boundary: carr_reader''s grant on the operation log is not scoped';
  end if;
  if not has_column_privilege('carr_reader','ops.cost_ledger_entry','amount_units','select')
     or not has_column_privilege('carr_reader','ops.cost_ledger_entry','vendor_reference','select')
     or not has_column_privilege('carr_reader','ops.cost_scope_node','authorization_ceiling_units','select') then
    raise exception 'WR-000111 column boundary: the module''s frozen field names are not readable';
  end if;
end $wr111_grants$;

-- The admission point, exercised end to end as the migration owner. Two DIRECT
-- callers from ONE base: exactly one lands and the other is refused on zero
-- rows matched, which is what the in-function lock and the version-and-digest
-- predicate exist for.
do $wr111_admission$
declare v_ref text := 'wr111:postgres-proof';
        v_first jsonb; v_second jsonb; v_replay jsonb; v_entries integer; v_ops integer; v_version integer;
begin
  perform ops.initialize_cost_scope_tree(v_ref,
    jsonb_build_object('tree_id','proof','tree_version','1','node_rows', jsonb_build_array(
      jsonb_build_object('node_id','root','parent_node_id',null,'scope_kind','portfolio',
        'authorization_ceiling_units',1000,'depth',0),
      jsonb_build_object('node_id','slice','parent_node_id','root','scope_kind','child',
        'authorization_ceiling_units',100,'depth',1))),
    'digest:empty');

  v_first := ops.commit_cost_ledger_operation(v_ref, 0, 'digest:empty', 'op-1',
    'sha256:' || repeat('1',64), 'reserve', '{"accepted":true}'::jsonb, null, 1, 'digest:one',
    jsonb_build_array(jsonb_build_object('sequence',0,'entry_id','entry:00000000','kind','reservation',
      'node_id','slice','amount_units',10,'operation_id','op-1','occurred_at','2026-09-01T00:00:00Z')));
  if not (v_first->>'ok')::boolean then
    raise exception 'WR-000111 admission: the first caller did not land';
  end if;

  -- The SECOND caller, standing on the SAME base the first one used.
  v_second := ops.commit_cost_ledger_operation(v_ref, 0, 'digest:empty', 'op-2',
    'sha256:' || repeat('2',64), 'reserve', '{"accepted":true}'::jsonb, null, 1, 'digest:two',
    '[]'::jsonb);
  if (v_second->>'ok')::boolean then
    raise exception 'WR-000111 admission: a lost update was admitted';
  end if;
  -- THE MODULE'S OWN FOUR NAMES. An alias fails here.
  if v_second->>'reason_id' <> 'version_conflict'
     or v_second->'base_version' is null or v_second->'current_version' is null
     or v_second->'base_state_digest' is null or v_second->'current_state_digest' is null then
    raise exception 'WR-000111 admission: the refusal does not speak the module''s four names: %', v_second;
  end if;
  if v_second ? 'expected_version' or v_second ? 'actual_version' then
    raise exception 'WR-000111 admission: the refusal speaks an alias';
  end if;

  -- The refusal wrote NOTHING.
  select count(*) into v_ops from ops.cost_ledger_operation where tree_ref = v_ref;
  select count(*) into v_entries from ops.cost_ledger_entry where tree_ref = v_ref;
  if v_ops <> 1 or v_entries <> 1 then
    raise exception 'WR-000111 admission: the refusal wrote rows (% ops, % entries)', v_ops, v_entries;
  end if;

  -- A replay offered from its ORIGINAL base, after the cell has moved.
  v_replay := ops.commit_cost_ledger_operation(v_ref, 0, 'digest:empty', 'op-1',
    'sha256:' || repeat('1',64), 'reserve', '{"accepted":true}'::jsonb, null, 1, 'ignored', '[]'::jsonb);
  if not (v_replay->>'ok')::boolean or not (v_replay->>'replayed')::boolean then
    raise exception 'WR-000111 replay: an original-base replay was answered as a conflict: %', v_replay;
  end if;
  select ledger_version into v_version from ops.cost_ledger_cell where tree_ref = v_ref;
  if v_version <> 1 then
    raise exception 'WR-000111 replay: a replay advanced the version to %', v_version;
  end if;
end $wr111_admission$;

-- The entry and operation logs are immutable, and the cell is not.
do $wr111_immutability$
begin
  begin
    update ops.cost_ledger_entry set amount_units = 99 where tree_ref = 'wr111:postgres-proof';
    raise exception 'WR-000111 immutability: an entry row was updated';
  exception when raise_exception then
    if position('immutable' in sqlerrm) = 0 then raise; end if;
  end;
  begin
    delete from ops.cost_ledger_operation where tree_ref = 'wr111:postgres-proof';
    raise exception 'WR-000111 immutability: an operation row was deleted';
  exception when raise_exception then
    if position('immutable' in sqlerrm) = 0 then raise; end if;
  end;
end $wr111_immutability$;

select 'WR-000111 producer cost ledger: grants scoped, admission serialized, log immutable' as proof;
