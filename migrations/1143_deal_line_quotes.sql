-- rollback: retire quote tools before removing these forward-only evidence tables.
-- lock-review: new tables and functions only; snapshot numbering locks one attachment.
create table ops.deal_quote_text (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null references public.attachment(id),
  deal_id uuid not null,
  content_hash text not null check(content_hash ~ '^[0-9a-f]{64}$'),
  version integer not null check(version>0),
  body text not null check(char_length(body) between 1 and 200000),
  actor_slug text not null,
  idempotency_key uuid not null,
  unique(document_id,version), unique(document_id,content_hash), unique(actor_slug,idempotency_key)
);
create table ops.deal_line_quote (
  id uuid primary key default gen_random_uuid(),
  deal_id uuid not null,
  snapshot_id uuid not null references ops.deal_quote_text(id),
  start_offset integer not null,
  end_offset integer not null,
  quoted_text text not null check(char_length(quoted_text) between 1 and 4000),
  comment text not null check(char_length(btrim(comment)) between 1 and 4000),
  author_id uuid not null references public.actor(id),
  idempotency_key uuid not null,
  request jsonb not null,
  result jsonb not null,
  created_at timestamptz not null default now(),
  unique(author_id,idempotency_key),
  check(start_offset>=0 and end_offset>start_offset)
);
create index deal_line_quote_deal on ops.deal_line_quote(deal_id,id);
create table ops.deal_line_quote_detach (
  author_id uuid not null references public.actor(id),
  idempotency_key uuid not null,
  quote_id uuid not null references ops.deal_line_quote(id),
  result jsonb not null,
  primary key(author_id,idempotency_key)
);
create index deal_line_quote_detach_quote on ops.deal_line_quote_detach(quote_id);
revoke all on ops.deal_quote_text,ops.deal_line_quote,ops.deal_line_quote_detach
  from public,carr_reader,carr_writer,carr_authority,dot_reader;

create function ops.deal_line_quote_actor(p_deal uuid) returns uuid
language sql stable security definer set search_path=pg_catalog,pg_temp as $$
  select a.id from public.actor a where a.active and a.kind='human'
    and a.slug=current_setting('carr.acting_actor_slug',true)
    and a.slug=current_setting('carr.verified_human_actor_slug',true)
    and ops.tour_deal_visible('carr-internal',p_deal,a.slug) and p_deal is not null
$$;
revoke all on function ops.deal_line_quote_actor(uuid) from public;

create function ops.register_deal_quote_text(p_document uuid,p_key uuid,p_hash text,p_text text) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare doc public.attachment%rowtype; prior ops.deal_quote_text%rowtype;
  actor text := nullif(current_setting('carr.acting_actor_slug',true),'');
begin
  if actor is null or p_key is null or p_hash is null or p_hash !~ '^[0-9a-f]{64}$'
    or p_document is null or p_text is null or char_length(p_text) not between 1 and 200000 then
    return jsonb_build_object('ok',false,'error','invalid_text_snapshot');
  end if;
  perform pg_advisory_xact_lock(hashtextextended('quote-text:'||actor||':'||p_key,0));
  select * into prior from ops.deal_quote_text where actor_slug=actor and idempotency_key=p_key;
  if found then
    if prior.document_id<>p_document or prior.content_hash<>p_hash or prior.body<>p_text then
      return jsonb_build_object('ok',false,'error','key_reuse');
    end if;
    return jsonb_build_object('ok',true,'snapshot_id',prior.id,'version',prior.version);
  end if;
  select * into doc from public.attachment where id=p_document and subject_type='deal'
    and deleted_at is null and sha256=p_hash for update;
  if not found then return jsonb_build_object('ok',false,'error','document_not_found'); end if;
  select * into prior from ops.deal_quote_text where document_id=p_document and content_hash=p_hash;
  if found then
    if prior.body<>p_text then return jsonb_build_object('ok',false,'error','text_snapshot_conflict'); end if;
    -- A different key is not silently consumed by another registration's receipt.
    return jsonb_build_object('ok',false,'error','text_snapshot_exists');
  end if;
  insert into ops.deal_quote_text(document_id,deal_id,content_hash,version,body,actor_slug,idempotency_key)
    select p_document,doc.subject_id,p_hash,coalesce(max(version),0)+1,p_text,actor,p_key
    from ops.deal_quote_text where document_id=p_document returning * into prior;
  return jsonb_build_object('ok',true,'snapshot_id',prior.id,'version',prior.version);
end $$;
revoke all on function ops.register_deal_quote_text(uuid,uuid,text,text) from public;
grant execute on function ops.register_deal_quote_text(uuid,uuid,text,text) to carr_authority;

create function ops.save_deal_line_quote(p_deal uuid,p_key uuid,p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare actor uuid; source ops.deal_quote_text%rowtype; prior ops.deal_line_quote%rowtype;
  start_pos integer; end_pos integer; out jsonb; quote_id uuid := gen_random_uuid();
begin
  actor:=ops.deal_line_quote_actor(p_deal);
  if actor is null then return jsonb_build_object('ok',false,'error','deal_not_found'); end if;
  if p_key is null or jsonb_typeof(p_request) is distinct from 'object' then
    return jsonb_build_object('ok',false,'error','invalid_quote'); end if;
  perform pg_advisory_xact_lock(hashtextextended('line-quote:'||actor||':'||p_key,0));
  select * into prior from ops.deal_line_quote where author_id=actor and idempotency_key=p_key;
  if found then
    if prior.deal_id<>p_deal or prior.request<>p_request then return jsonb_build_object('ok',false,'error','key_reuse'); end if;
    return prior.result;
  end if;
  if (select count(*) from jsonb_object_keys(p_request))<>5
    or jsonb_typeof(p_request->'snapshot_id') is distinct from 'string'
    or jsonb_typeof(p_request->'start') is distinct from 'number'
    or jsonb_typeof(p_request->'end') is distinct from 'number'
    or jsonb_typeof(p_request->'quoted_text') is distinct from 'string'
    or jsonb_typeof(p_request->'comment') is distinct from 'string'
    or p_request->>'start' !~ '^[0-9]{1,6}$' or p_request->>'end' !~ '^[0-9]{1,6}$'
    or char_length(p_request->>'quoted_text') not between 1 and 4000
    or char_length(btrim(p_request->>'comment')) not between 1 and 4000
    or char_length(p_request->>'comment')>4000 then
    return jsonb_build_object('ok',false,'error','invalid_quote');
  end if;
  start_pos:=(p_request->>'start')::integer; end_pos:=(p_request->>'end')::integer;
  select s.* into source from ops.deal_quote_text s join public.attachment a on a.id=s.document_id
    where s.id=(p_request->>'snapshot_id')::uuid and s.deal_id=p_deal
      and a.subject_type='deal' and a.subject_id=p_deal and a.deleted_at is null;
  if not found then return jsonb_build_object('ok',false,'error','document_not_found'); end if;
  if start_pos>=end_pos or end_pos>char_length(source.body)
    or substring(source.body from start_pos+1 for end_pos-start_pos)<>p_request->>'quoted_text' then
    return jsonb_build_object('ok',false,'error','invalid_quote'); end if;
  out:=jsonb_build_object('ok',true,'quote_id',quote_id,'snapshot_id',source.id,'version',source.version);
  insert into ops.deal_line_quote(id,deal_id,snapshot_id,start_offset,end_offset,quoted_text,comment,author_id,idempotency_key,request,result)
    values(quote_id,p_deal,source.id,start_pos,end_pos,p_request->>'quoted_text',p_request->>'comment',actor,p_key,p_request,out);
  return out;
end $$;
revoke all on function ops.save_deal_line_quote(uuid,uuid,jsonb) from public;
grant execute on function ops.save_deal_line_quote(uuid,uuid,jsonb) to carr_writer;

create function ops.detach_deal_line_quote(p_deal uuid,p_quote uuid,p_key uuid) returns jsonb
language plpgsql security definer set search_path=pg_catalog,pg_temp as $$
declare actor uuid:=ops.deal_line_quote_actor(p_deal); prior ops.deal_line_quote_detach%rowtype; out jsonb;
begin
  if actor is null then return jsonb_build_object('ok',false,'error','deal_not_found'); end if;
  if p_key is null then return jsonb_build_object('ok',false,'error','invalid_quote'); end if;
  perform pg_advisory_xact_lock(hashtextextended('line-quote-detach:'||actor||':'||p_key,0));
  select * into prior from ops.deal_line_quote_detach where author_id=actor and idempotency_key=p_key;
  if found then
    if prior.quote_id<>p_quote or not exists(select 1 from ops.deal_line_quote where id=p_quote and deal_id=p_deal) then
      return jsonb_build_object('ok',false,'error','key_reuse'); end if;
    return prior.result;
  end if;
  if not exists(select 1 from ops.deal_line_quote where id=p_quote and deal_id=p_deal and author_id=actor) then
    return jsonb_build_object('ok',false,'error','quote_not_found'); end if;
  out:=jsonb_build_object('ok',true,'quote_id',p_quote,'state','detached');
  insert into ops.deal_line_quote_detach values(actor,p_key,p_quote,out);
  return out;
end $$;
revoke all on function ops.detach_deal_line_quote(uuid,uuid,uuid) from public;
grant execute on function ops.detach_deal_line_quote(uuid,uuid,uuid) to carr_writer;

create function ops.list_deal_line_quotes(p_deal uuid,p_after uuid default null) returns jsonb
language plpgsql stable security definer set search_path=pg_catalog,pg_temp as $$
declare items jsonb;
begin
  if ops.deal_line_quote_actor(p_deal) is null then return jsonb_build_object('ok',false,'error','deal_not_found'); end if;
  select coalesce(jsonb_agg(row),'[]'::jsonb) into items from (
    select q.id,q.snapshot_id,q.start_offset,q.end_offset,q.quoted_text,q.comment,q.author_id,
      s.version,'from version '||s.version as version_label,
      (a.sha256<>s.content_hash or exists(select 1 from ops.deal_quote_text newer
        where newer.document_id=s.document_id and newer.version>s.version)) as version_changed,
      case when exists(select 1 from ops.deal_line_quote_detach d where d.quote_id=q.id)
        then 'detached' else 'saved' end as state
    from ops.deal_line_quote q join ops.deal_quote_text s on s.id=q.snapshot_id
      join public.attachment a on a.id=s.document_id and a.subject_type='deal' and a.subject_id=p_deal and a.deleted_at is null
    where q.deal_id=p_deal and (p_after is null or q.id>p_after) order by q.id limit 101
  ) row;
  return jsonb_build_object('ok',true,'quotes',jsonb_path_query_array(items,'$[0 to 99]'),
    'more',jsonb_array_length(items)>100,
    'next_cursor',case when jsonb_array_length(items)>100 then items->99->>'id' else null end);
end $$;
revoke all on function ops.list_deal_line_quotes(uuid,uuid) from public;
grant execute on function ops.list_deal_line_quotes(uuid,uuid) to carr_writer;

create function ops.read_deal_quote_text(p_deal uuid,p_document uuid,p_hash text) returns jsonb
language plpgsql stable security definer set search_path=pg_catalog,pg_temp as $$
declare source ops.deal_quote_text%rowtype;
begin
  if ops.deal_line_quote_actor(p_deal) is null then return jsonb_build_object('ok',false,'error','deal_not_found'); end if;
  select s.* into source from ops.deal_quote_text s join public.attachment a on a.id=s.document_id
    where s.document_id=p_document and s.content_hash=p_hash and s.deal_id=p_deal
      and a.subject_type='deal' and a.subject_id=p_deal and a.deleted_at is null;
  if not found then return jsonb_build_object('ok',false,'error','document_not_found'); end if;
  return jsonb_build_object('ok',true,'snapshot_id',source.id,'version',source.version,
    'body',source.body,'text_hash',encode(sha256(convert_to(source.body,'UTF8')),'hex'),'content_hash',source.content_hash,'offset_unit','unicode_code_point');
end $$;
revoke all on function ops.read_deal_quote_text(uuid,uuid,text) from public;
grant execute on function ops.read_deal_quote_text(uuid,uuid,text) to carr_writer;

do $quote_grants$
declare contract jsonb := ops.scac_current_grant_contract();
begin
  contract := jsonb_set(contract,'{secdef_execute}',(contract->'secdef_execute')||(
    select jsonb_agg(jsonb_build_object('config',jsonb_build_array('search_path=pg_catalog, pg_temp'),
      'grantee',grantee,'parallel','u','grantable',false,'privilege','execute','signature',signature,
      'volatility',volatility,'ingress_key','db-function-acl:'||signature||':'||grantee||':execute',
      'ingress_kind','db_function_acl','function_kind','f','security_definer',true) order by signature)
    from (values
      ('ops.register_deal_quote_text(p_document uuid, p_key uuid, p_hash text, p_text text)','carr_authority','v'),
      ('ops.read_deal_quote_text(p_deal uuid, p_document uuid, p_hash text)','carr_writer','s'),
      ('ops.save_deal_line_quote(p_deal uuid, p_key uuid, p_request jsonb)','carr_writer','v'),
      ('ops.detach_deal_line_quote(p_deal uuid, p_quote uuid, p_key uuid)','carr_writer','v'),
      ('ops.list_deal_line_quotes(p_deal uuid, p_after uuid)','carr_writer','s')
    ) as grants(signature,grantee,volatility)));
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb '
    'language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $quote_grants$;
