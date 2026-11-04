-- rollback: forward-only; preserve accepted turn snapshots and receipts.
-- expand-contract: optional deal and selected quote IDs; existing submissions remain valid.
-- lock-review: additive nullable/default columns; no historical content rewrite.
alter table ops.doc_request_receipt add column deal_id uuid;
alter table ops.doc_request_receipt add column quote_ids jsonb not null default '[]';
alter table ops.doc_conversation_turn add column deal_id uuid;
alter table ops.doc_conversation_turn add column line_quotes jsonb not null default '[]';
create function ops.submit_doc_request(p_key text, p_conversation text, p_body text, p_deal uuid, p_quotes jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,pg_temp as $$
declare
  a public.actor%rowtype;
  r ops.doc_request_receipt%rowtype;
  conversation ops.doc_conversation%rowtype;
  result jsonb := jsonb_build_object('ok',true,'contract','doc-request.v1',
    'status','refused','conversation_id',p_conversation,'idempotency_key',p_key,'request_msg_id',p_key);
  granted boolean;
  frozen jsonb := '[]';
  rendered text;
begin
  select * into a from public.actor where slug = nullif(current_setting('carr.acting_actor_slug',true),'') for share;
  if a.id is null or not a.active or a.kind <> 'human' or a.slug not in ('joe','dell')
    or a.slug is distinct from nullif(current_setting('carr.verified_human_actor_slug',true),'') then
    return result || jsonb_build_object('reason','doc_request_partner_required');
  end if;
  if p_key is null or p_key !~* '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' then
    return result || jsonb_build_object('reason','doc_request_key_invalid');
  end if;
  if p_conversation is null or p_conversation !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' then
    return result || jsonb_build_object('reason','doc_conversation_id_invalid');
  end if;
  if p_body is null or p_body !~ '[^[:space:]]' or length(p_body) > 20000 then
    return result || jsonb_build_object('reason','doc_conversation_body_invalid');
  end if;
  if p_quotes is null or jsonb_typeof(p_quotes)<>'array' then
    return result || jsonb_build_object('reason','doc_request_quotes_invalid');
  end if;
  if jsonb_array_length(p_quotes)>20 or (p_deal is null and p_quotes<>'[]'::jsonb)
    or exists(select 1 from jsonb_array_elements_text(p_quotes) q(id)
      where id is null or id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') then
    return result || jsonb_build_object('reason','doc_request_quotes_invalid');
  end if;
  if (select count(distinct lower(id)) from jsonb_array_elements_text(p_quotes) q(id))<>jsonb_array_length(p_quotes) then
    return result || jsonb_build_object('reason','doc_request_quotes_invalid');
  end if;
  if p_deal is not null and ops.deal_line_quote_actor(p_deal) is null then
    return result || jsonb_build_object('reason','doc_request_deal_not_found');
  end if;
  perform pg_advisory_xact_lock(hashtextextended('doc-request:' || a.id || ':' || p_key::uuid::text,0));
  select * into conversation from ops.doc_conversation where id=p_conversation::uuid for update;
  select true into granted from ops.doc_conversation_grant
    where conversation_id=p_conversation::uuid and grantee_actor=a.id and revoked_at is null
    limit 1 for share;
  if conversation.id is null or not (conversation.created_by_actor=a.id or coalesce(granted,false)) then
    return result || jsonb_build_object('reason','doc_conversation_not_found');
  end if;
  select * into r from ops.doc_request_receipt where actor_id=a.id and idempotency_key=p_key::uuid;
  if found then
    if r.deal_id is distinct from p_deal or r.quote_ids is distinct from p_quotes
      or r.conversation_id is distinct from p_conversation::uuid or r.body is distinct from p_body
      or r.response->>'conversation_id' is distinct from p_conversation
      or r.response->>'idempotency_key' is distinct from p_key then
      return result || jsonb_build_object('reason','doc_request_key_reuse');
    end if;
    return r.response;
  end if;
  select coalesce(jsonb_agg(jsonb_build_object('quote_id',q.id,'quoted_text',q.quoted_text,
      'comment',q.comment) order by selected.ordinality),'[]'::jsonb) into frozen
    from jsonb_array_elements_text(p_quotes) with ordinality selected(id,ordinality)
    join ops.deal_line_quote q on q.id=selected.id::uuid and q.deal_id=p_deal
    join ops.deal_quote_text s on s.id=q.snapshot_id
    join public.attachment doc on doc.id=s.document_id and doc.subject_type='deal'
      and doc.subject_id=p_deal and doc.deleted_at is null
    where not exists(select 1 from ops.deal_line_quote_detach d where d.quote_id=q.id);
  if jsonb_array_length(frozen)<>jsonb_array_length(p_quotes) then
    return result || jsonb_build_object('reason','doc_request_quotes_invalid');
  end if;
  rendered := p_body;
  if frozen<>'[]'::jsonb then
    select p_body || E'\n\nSelected line quotes\n' || string_agg(
      (q->>'quoted_text') || E'\nComment: ' || (q->>'comment'),E'\n\n' order by ordinality)
      into rendered from jsonb_array_elements(frozen) with ordinality x(q,ordinality);
  end if;
  if length(rendered)>20000 then
    return result || jsonb_build_object('reason','doc_conversation_body_invalid');
  end if;
  insert into ops.doc_conversation_turn(conversation_id,sequence,role,body,msg_id,origin_channel,origin_actor,deal_id,line_quotes)
    select p_conversation::uuid,coalesce(max(sequence)+1,0),'human',rendered,p_key::uuid,'browser-human',a.slug,p_deal,frozen
      from ops.doc_conversation_turn where conversation_id=p_conversation::uuid
    on conflict(msg_id) do nothing;
  if not found then
    return result || jsonb_build_object('reason','doc_request_key_reuse');
  end if;
  result := result || jsonb_build_object('status','accepted');
  if p_deal is not null then
    result := result || jsonb_build_object('deal_id',p_deal,'line_quotes',frozen);
  end if;
  insert into ops.doc_request_receipt(actor_id,idempotency_key,conversation_id,body,response,deal_id,quote_ids)
    values(a.id,p_key::uuid,p_conversation::uuid,p_body,result,p_deal,p_quotes);
  update ops.doc_conversation set updated_at=now() where id=p_conversation::uuid;
  return result;
end $$;
revoke all on function ops.submit_doc_request(text,text,text,uuid,jsonb) from public,carr_reader,carr_authority,carr_jobs,dot_reader;
grant execute on function ops.submit_doc_request(text,text,text,uuid,jsonb) to carr_writer;
create or replace function ops.submit_doc_request(p_key text,p_conversation text,p_body text)
returns jsonb language sql security definer set search_path=pg_catalog,ops,pg_temp as $$
 select ops.submit_doc_request(p_key,p_conversation,p_body,null,'[]'::jsonb)
$$;
create or replace function ops.doc_conversation_facts(
  p_conversation_id uuid, p_actor_id text, p_after_sequence integer, p_limit integer)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,ops,public,pg_temp
as $$
declare v_actor uuid; v_row ops.doc_conversation%rowtype; v_limit integer; v_after integer;
        v_turns jsonb; v_latest integer; v_more boolean; v_grants jsonb; v_visible integer;
begin
  begin
    v_actor := p_actor_id::uuid;
  exception when others then
    return jsonb_build_object('ok', false, 'reason_id', 'doc_conversation_not_found');
  end;
  v_limit := least(greatest(coalesce(p_limit, 200), 1), 200);
  v_after := greatest(coalesce(p_after_sequence, 0), 0);

  select * into v_row from ops.doc_conversation where id = p_conversation_id;
  if v_row.id is null
     or not (v_row.created_by_actor = v_actor
             or exists (select 1 from ops.doc_conversation_grant g
                         where g.conversation_id = v_row.id and g.grantee_actor = v_actor
                           and g.revoked_at is null)) then
    -- One answer for absent and for not-permitted. A distinguishable refusal
    -- would itself disclose that a private conversation exists.
    return jsonb_build_object('ok', false, 'reason_id', 'doc_conversation_not_found');
  end if;

  if exists(select 1 from ops.doc_conversation_turn t where t.conversation_id=p_conversation_id
    and t.deal_id is not null and ops.deal_line_quote_actor(t.deal_id) is null) then
    return jsonb_build_object('ok',false,'reason_id','doc_conversation_not_found');
  end if;
  select coalesce(jsonb_agg(to_jsonb(t) order by t.sequence), '[]'::jsonb) into v_turns
    from (select sequence, role, body, msg_id, origin_channel, origin_actor, at, answer_blocks, deal_id, line_quotes
            from ops.doc_conversation_turn
           where conversation_id = v_row.id and sequence >= v_after
           order by sequence limit v_limit) t;
  select coalesce(max(sequence), -1) into v_latest
    from ops.doc_conversation_turn where conversation_id = v_row.id;
  v_more := exists (select 1 from ops.doc_conversation_turn
                     where conversation_id = v_row.id
                       and sequence >= v_after + jsonb_array_length(v_turns));
  select coalesce(jsonb_agg(to_jsonb(g) order by g.granted_at), '[]'::jsonb) into v_grants
    from (select grantee_actor, granted_at, granted_by_actor
            from ops.doc_conversation_grant
           where conversation_id = v_row.id and revoked_at is null) g;
  -- The count this actor is entitled to see, computed here and nowhere else.
  select count(*) into v_visible from ops.doc_conversation c
   where c.created_by_actor = v_actor
      or exists (select 1 from ops.doc_conversation_grant g
                  where g.conversation_id = c.id and g.grantee_actor = v_actor
                    and g.revoked_at is null);

  return jsonb_build_object('ok', true,
    'identity', jsonb_build_object('id', v_row.id, 'title', v_row.title,
      'visibility', v_row.visibility, 'pinned_at', v_row.pinned_at,
      'archived_at', v_row.archived_at, 'version', v_row.version,
      'created_by', v_row.created_by_actor),
    'turns', v_turns, 'latest_sequence', v_latest, 'more', v_more,
    'effective_grants', v_grants, 'visible_conversation_count', v_visible);
end $$;

do $grants$
declare contract jsonb := ops.scac_current_grant_contract(); entry jsonb;
begin
  select jsonb_build_object('config',to_jsonb(p.proconfig),'grantee','carr_writer',
    'parallel',p.proparallel::text,'grantable',false,'privilege','execute',
    'signature','ops.submit_doc_request('||pg_get_function_identity_arguments(p.oid)||')',
    'volatility',p.provolatile::text,'ingress_key','db-function-acl:ops.submit_doc_request('
      ||pg_get_function_identity_arguments(p.oid)||'):carr_writer:execute',
    'ingress_kind','db_function_acl','function_kind',p.prokind::text,'security_definer',p.prosecdef)
    into strict entry from pg_proc p where p.oid='ops.submit_doc_request(text,text,text,uuid,jsonb)'::regprocedure;
  contract := jsonb_set(contract,'{secdef_execute}',contract->'secdef_execute'||jsonb_build_array(entry));
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb '
    'language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $grants$;

