-- rollback: forward-only; restore prior function bodies in a new migration.
-- expand-contract: expand; preserve command and projection interfaces.
-- lock-review: replace functions only; no table rewrite or historical row mutation.
set local lock_timeout='5s';
set local statement_timeout='30s';

create or replace function ops.change_home_inbox(p_key uuid,p_command text,p_items jsonb,p_until timestamptz)
returns jsonb language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare a uuid:=ops.portfolio_writer_actor_id(); request jsonb; prior ops.home_inbox_write%rowtype;
  entry jsonb; current_item jsonb; result jsonb; results jsonb:='[]'; source_material text;
begin
  if a is null then raise insufficient_privilege; end if;
  if p_command in ('settle_reversible','snooze_reversible','settle_undo','snooze_undo') then
    return ops.reversible_home_inbox(p_key,p_command,p_items,p_until);
  end if;
  if p_key is null or p_command not in ('snooze','settle','unsettle') or p_command is null
    or jsonb_typeof(p_items) is distinct from 'array' or jsonb_array_length(p_items) not between 1 and 100
    or (p_command='snooze' and p_until is null) or (p_command<>'snooze' and p_until is not null) then
    return jsonb_build_object('ok',false,'error','invalid_inbox_command');
  end if;
  request:=jsonb_build_array(p_command,p_items,p_until);
  perform pg_advisory_xact_lock(hashtextextended('home-inbox:'||a::text,0));
  select * into prior from ops.home_inbox_write where actor_id=a and idempotency_key=p_key;
  if found then
    if prior.request<>request then return jsonb_build_object('ok',false,'error','key_reuse'); end if;
    return prior.result;
  end if;
  if p_command='snooze' and (p_until<=now() or not isfinite(p_until)) then
    return jsonb_build_object('ok',false,'error','invalid_snooze_time');
  end if;
  if exists(select 1 from jsonb_array_elements(p_items) i group by i->>'item_id' having count(*)>1) then
    return jsonb_build_object('ok',false,'error','duplicate_inbox_item');
  end if;
  for entry in select value from jsonb_array_elements(p_items) loop
    select i,s.material_version into current_item,source_material
      from ops.home_inbox_items_v3(a) i
      join ops.home_inbox_sources_v3(a) s on s.item_id=i->>'item_id'
      where i->>'item_id'=entry->>'item_id';
    if current_item is null or current_item->>'source_type'='overflow' then
      result:=jsonb_build_object('ok',false,'error','inbox_item_not_found');
    elsif entry->>'base_version' is distinct from current_item->>'base_version' then
      result:=jsonb_build_object('ok',false,'error','version_conflict','current_version',current_item->>'base_version');
    else
      perform ops.write_home_inbox_disposition(a,current_item,source_material,p_command,p_until);
      select jsonb_build_object('ok',true,'item',i) into result from ops.home_inbox_items_v3(a) i where i->>'item_id'=entry->>'item_id';
    end if;
    results:=results||jsonb_build_array(result||jsonb_build_object('item_id',entry->>'item_id'));
  end loop;
  result:=jsonb_build_object('ok',true,'schema_version','home-inbox-command.v1','results',results);
  insert into ops.home_inbox_write(actor_id,idempotency_key,request,result) values(a,p_key,request,result);
  return result;
end $$;

do $wording$
declare definition text:=pg_get_functiondef('ops.home_inbox_sources_v2(uuid)'::regprocedure);
begin
  if position(' older draft' in definition)=0 then raise exception 'expected reply count wording missing'; end if;
  execute replace(definition,' older draft',' earlier email');
end $wording$;
