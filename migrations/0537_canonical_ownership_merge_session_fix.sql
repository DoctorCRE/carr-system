-- WR-000129: repair the ambiguous receipt column in the already-applied 0532a
-- function without changing the historical migration's bytes or authority guards.
-- CREATE OR REPLACE preserves the existing function identity, owner and grants.

do $wr129_merge_receipt_fix$
declare
  target regprocedure := to_regprocedure(
    'ops.mint_canonical_ownership_merge_session(uuid,uuid,text,integer,text,timestamptz,uuid)');
  before_proc pg_catalog.pg_proc%rowtype;
  after_proc pg_catalog.pg_proc%rowtype;
  definition text;
  old_relation constant text := 'from ops.engineering_slice_receipt where envelope_id=e.id';
  new_relation constant text := 'from ops.engineering_slice_receipt slice_receipt where slice_receipt.envelope_id=e.id';
  old_receipt constant text := 'and receipt#>>''{source_evidence,source_sha}''=p_head_sha';
  new_receipt constant text := 'and slice_receipt.receipt#>>''{source_evidence,source_sha}''=p_head_sha';
begin
  if target is null then
    raise exception 'WR129 predecessor merge-session function is absent';
  end if;
  select * into strict before_proc from pg_catalog.pg_proc where oid=target::oid;
  definition := pg_catalog.pg_get_functiondef(target::oid);
  if before_proc.prosecdef is not true
     or (length(definition)-length(replace(definition,old_relation,'')))/length(old_relation) <> 1
     or (length(definition)-length(replace(definition,old_receipt,'')))/length(old_receipt) <> 1 then
    raise exception 'WR129 predecessor merge-session body or security contract drifted';
  end if;
  definition := replace(definition,old_relation,new_relation);
  definition := replace(definition,old_receipt,new_receipt);
  execute definition;
  select * into strict after_proc from pg_catalog.pg_proc where oid=target::oid;
  if (after_proc.proowner,after_proc.proacl,after_proc.prosecdef,after_proc.proconfig,
      after_proc.proargtypes,after_proc.prorettype,after_proc.provolatile,after_proc.proparallel)
     is distinct from
     (before_proc.proowner,before_proc.proacl,before_proc.prosecdef,before_proc.proconfig,
      before_proc.proargtypes,before_proc.prorettype,before_proc.provolatile,before_proc.proparallel) then
    raise exception 'WR129 merge-session replacement changed function authority or signature';
  end if;
end;
$wr129_merge_receipt_fix$;
