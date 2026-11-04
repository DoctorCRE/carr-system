-- rollback: restore ops.change_home_inbox from migration 1140 in a forward migration; retain private batch receipts for audit.
-- Exact undo receipts remain private to the actor and never complete domain work.
create table ops.home_inbox_batch (
  actor_id uuid not null references public.actor(id),
  batch_key uuid not null,
  command text not null,
  receipts jsonb not null,
  primary key(actor_id,batch_key)
);
revoke all on ops.home_inbox_batch from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create function ops.write_home_inbox_disposition(p_actor uuid,p_item jsonb,p_material text,p_command text,p_until timestamptz)
returns jsonb language sql set search_path=pg_catalog,pg_temp as $$
  insert into ops.home_inbox_disposition(actor_id,item_id,disposition,snoozed_until,source_version,material_version,explicit_unsettle)
        values(p_actor,p_item->>'item_id',case p_command when 'snooze' then 'snoozed' when 'settle' then 'settled' else 'inbox' end,
          p_until,p_item->>'source_version',p_material,p_command='unsettle')
      on conflict(actor_id,item_id) do update set disposition=excluded.disposition,snoozed_until=excluded.snoozed_until,
        source_version=excluded.source_version,material_version=excluded.material_version,
        explicit_unsettle=excluded.explicit_unsettle,version=ops.home_inbox_disposition.version+1,updated_at=now()
  returning to_jsonb(home_inbox_disposition.*);
$$;
revoke all on function ops.write_home_inbox_disposition(uuid,jsonb,text,text,timestamptz) from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

create function ops.reversible_home_inbox(p_key uuid,p_command text,p_items jsonb,p_until timestamptz)
returns jsonb language plpgsql set search_path=pg_catalog,pg_temp as $$
declare a uuid:=ops.portfolio_writer_actor_id(); request jsonb; previous ops.home_inbox_write%rowtype;
  undo boolean:=p_command like '%_undo'; v_command text:=split_part(p_command,'_',1);
  batch ops.home_inbox_batch%rowtype; entries jsonb; entry jsonb; current_item jsonb;
  before_row jsonb; after_row jsonb; receipt jsonb; results jsonb:='[]'; saved jsonb:='[]';
  result jsonb; item_key text; projected jsonb; materials jsonb; dispositions jsonb;
begin
  if a is null then raise insufficient_privilege; end if;
  if p_key is null then return jsonb_build_object('ok',false,'error','invalid_inbox_command'); end if;
  perform pg_advisory_xact_lock(hashtextextended('home-inbox:'||a::text,0));
  request:=jsonb_build_array(p_command,p_items,p_until);
  select * into previous from ops.home_inbox_write where actor_id=a and idempotency_key=p_key;
  if found then
    if previous.request<>request then return jsonb_build_object('ok',false,'error','key_reuse'); end if;
    return previous.result;
  end if;
  if undo and p_until is not null then return jsonb_build_object('ok',false,'error','invalid_inbox_command'); end if;
  if undo then
    select * into batch from ops.home_inbox_batch where actor_id=a and batch_key=(p_items->>'batch_key')::uuid and home_inbox_batch.command=v_command;
    if not found then return jsonb_build_object('ok',false,'error','inbox_batch_not_found'); end if;
    entries:=batch.receipts;
    if p_items->'items' is not null and p_items->'items'<>'null'::jsonb then
      if jsonb_typeof(p_items->'items')<>'array' or jsonb_array_length(p_items->'items') not between 1 and 100 then
        return jsonb_build_object('ok',false,'error','invalid_inbox_command');
      end if;
      if exists(select 1 from jsonb_array_elements(p_items->'items') i where not exists(
        select 1 from jsonb_array_elements(entries) e where e->>'item_id'=i->>'item_id')) then
        return jsonb_build_object('ok',false,'error','inbox_item_not_found');
      end if;
    end if;
  else
    if jsonb_typeof(p_items) is distinct from 'array' or jsonb_array_length(p_items) not between 1 and 100
      or (v_command='snooze' and (p_until is null or p_until<=now() or not isfinite(p_until)))
      or (v_command='settle' and p_until is not null) then
      return jsonb_build_object('ok',false,'error','invalid_inbox_command');
    end if;
    if exists(select 1 from jsonb_array_elements(p_items) i group by i->>'item_id' having count(*)>1) then
      return jsonb_build_object('ok',false,'error','duplicate_inbox_item');
    end if;
    entries:=p_items;
  end if;
  select jsonb_object_agg(i->>'item_id',i) into projected from ops.home_inbox_items_v3(a) i;
  select jsonb_object_agg(d.item_id,to_jsonb(d)) into dispositions from ops.home_inbox_disposition d
    where d.actor_id=a and projected ? d.item_id;
  if not undo then
    select jsonb_object_agg(s.item_id,s.material_version) into materials from ops.home_inbox_sources_v3(a) s;
  end if;
  for entry in select value from jsonb_array_elements(entries) loop
    item_key:=entry->>'item_id';
    if undo and p_items->'items'<>'null'::jsonb and not exists(
      select 1 from jsonb_array_elements(p_items->'items') i where i->>'item_id'=item_key) then
      saved:=saved||jsonb_build_array(entry); continue;
    end if;
    begin
      current_item:=projected->item_key;
      receipt:=jsonb_build_object('item_id',item_key);
      if current_item is null or current_item->>'source_type'='overflow' then
        receipt:=receipt||jsonb_build_object('status','not_found');
      elsif undo then
        if entry->>'status'<>'applied' or coalesce((entry->>'undone')::boolean,false) then
          receipt:=receipt||jsonb_build_object('status','already');
        else
          after_row:=dispositions->item_key;
          if after_row is distinct from entry->'after' or current_item->>'base_version' is distinct from entry->>'after_version' then
            receipt:=receipt||jsonb_build_object('status','not_allowed','error','version_conflict');
          else
            delete from ops.home_inbox_disposition where actor_id=a and item_id=item_key;
            if entry->'before'<>'null'::jsonb then
              insert into ops.home_inbox_disposition select * from jsonb_populate_record(null::ops.home_inbox_disposition,entry->'before');
            end if;
            entry:=entry||jsonb_build_object('undone',true);
            receipt:=receipt||jsonb_build_object('status','applied');
          end if;
        end if;
      elsif entry->>'base_version' is distinct from current_item->>'base_version' then
        receipt:=receipt||jsonb_build_object('status','not_allowed','error','version_conflict');
      elsif (v_command='settle' and (current_item->>'disposition'='settled' or
          (current_item->>'disposition'='inbox' and current_item->>'source_state'='done'
           and not (current_item->>'explicit_unsettle')::boolean
           and current_item->>'source_type' in ('next_action','capture_post_call_action','email_proposal','fact_review','tour_feedback'))))
        or (v_command='snooze' and current_item->>'disposition'='snoozed' and (current_item->>'snoozed_until')::timestamptz=p_until) then
        receipt:=receipt||jsonb_build_object('status','already');
      else
        before_row:=dispositions->item_key;
        after_row:=ops.write_home_inbox_disposition(a,current_item,materials->>item_key,v_command,p_until);
        receipt:=receipt||jsonb_build_object('status','applied');
        entry:=entry||jsonb_build_object('before',before_row,'after',after_row);
      end if;
    exception when insufficient_privilege then
      receipt:=jsonb_build_object('item_id',item_key,'status','not_allowed');
    when others then
      receipt:=jsonb_build_object('item_id',item_key,'status','not_allowed','error','item_failed');
    end;
    if not undo then entry:=entry||receipt; end if;
    saved:=saved||jsonb_build_array(entry);
    results:=results||jsonb_build_array(receipt);
  end loop;
  if not undo then
    select jsonb_object_agg(i->>'item_id',i) into projected from ops.home_inbox_items_v3(a) i;
    select jsonb_agg(case when e->>'status'='applied' then
      e||jsonb_build_object('after_version',projected->(e->>'item_id')->>'base_version') else e end order by n)
      into saved from jsonb_array_elements(saved) with ordinality r(e,n);
  end if;
  if undo then update ops.home_inbox_batch set receipts=saved where actor_id=a and batch_key=batch.batch_key;
  else insert into ops.home_inbox_batch values(a,p_key,v_command,saved); end if;
  result:=jsonb_build_object('ok',true,'schema_version','home-inbox-batch.v1','batch_key',case when undo then batch.batch_key else p_key end,'results',results);
  insert into ops.home_inbox_write(actor_id,idempotency_key,request,result) values(a,p_key,request,result);
  return result;
end $$;
revoke all on function ops.reversible_home_inbox(uuid,text,jsonb,timestamptz) from public,carr_reader,carr_writer,carr_authority,carr_jobs,dot_reader;

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
      from ops.home_inbox_items(a) i
      join ops.home_inbox_sources(a) s on s.item_id=i->>'item_id'
      where i->>'item_id'=entry->>'item_id';
    if entry->>'item_id' like 'fact_review:%' or entry->>'item_id' like 'tour_feedback:%' then
      select i,s.material_version into current_item,source_material
        from ops.home_inbox_items_v3(a) i join ops.home_inbox_sources_v3(a) s on s.item_id=i->>'item_id'
        where i->>'item_id'=entry->>'item_id';
    end if;
    if current_item is null or current_item->>'source_type'='overflow' then
      result:=jsonb_build_object('ok',false,'error','inbox_item_not_found');
    elsif entry->>'base_version' is distinct from current_item->>'base_version' then
      result:=jsonb_build_object('ok',false,'error','version_conflict','current_version',current_item->>'base_version');
    else
      perform ops.write_home_inbox_disposition(a,current_item,source_material,p_command,p_until);
      select jsonb_build_object('ok',true,'item',i) into result from ops.home_inbox_items(a) i where i->>'item_id'=entry->>'item_id';
      if entry->>'item_id' like 'fact_review:%' or entry->>'item_id' like 'tour_feedback:%' then
        select jsonb_build_object('ok',true,'item',i) into result from ops.home_inbox_items_v3(a) i where i->>'item_id'=entry->>'item_id';
      end if;
    end if;
    results:=results||jsonb_build_array(result||jsonb_build_object('item_id',entry->>'item_id'));
  end loop;
  result:=jsonb_build_object('ok',true,'schema_version','home-inbox-command.v1','results',results);
  insert into ops.home_inbox_write(actor_id,idempotency_key,request,result) values(a,p_key,request,result);
  return result;
end $$;
