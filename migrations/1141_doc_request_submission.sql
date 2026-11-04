-- rollback: forward-only; retain accepted asks and receipts, revoke execute to disable submission.
-- expand-contract: expand; partner-only submission beside the existing authority append.
-- lock-review: new empty receipt table; locks one actor and conversation per request.
create table ops.doc_request_receipt (
  actor_id uuid not null references public.actor(id),
  idempotency_key uuid not null,
  conversation_id uuid not null references ops.doc_conversation(id),
  body text not null,
  response jsonb not null,
  primary key (actor_id, idempotency_key),
  foreign key (idempotency_key) references ops.doc_conversation_turn(msg_id)
);
revoke all on ops.doc_request_receipt from public, carr_reader, carr_writer, carr_authority, carr_jobs, dot_reader;
create trigger doc_request_receipt_immutable before update or delete on ops.doc_request_receipt
  for each row execute function ops.doc_conversation_rows_immutable();
create trigger doc_request_receipt_no_truncate before truncate on ops.doc_request_receipt
  for each statement execute function ops.doc_conversation_rows_immutable();

create function ops.submit_doc_request(p_key text, p_conversation text, p_body text)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,pg_temp as $$
declare
  a public.actor%rowtype;
  r ops.doc_request_receipt%rowtype;
  conversation ops.doc_conversation%rowtype;
  result jsonb := jsonb_build_object('ok',true,'contract','doc-request.v1',
    'status','refused','conversation_id',p_conversation,'idempotency_key',p_key,'request_msg_id',p_key);
  granted boolean;
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
    if r.conversation_id is distinct from p_conversation::uuid or r.body is distinct from p_body
      or r.response->>'conversation_id' is distinct from p_conversation
      or r.response->>'idempotency_key' is distinct from p_key then
      return result || jsonb_build_object('reason','doc_request_key_reuse');
    end if;
    return r.response;
  end if;
  insert into ops.doc_conversation_turn(conversation_id,sequence,role,body,msg_id,origin_channel,origin_actor)
    select p_conversation::uuid,coalesce(max(sequence)+1,0),'human',p_body,p_key::uuid,'browser-human',a.slug
      from ops.doc_conversation_turn where conversation_id=p_conversation::uuid
    on conflict(msg_id) do nothing;
  if not found then
    return result || jsonb_build_object('reason','doc_request_key_reuse');
  end if;
  result := result || jsonb_build_object('status','accepted');
  insert into ops.doc_request_receipt(actor_id,idempotency_key,conversation_id,body,response)
    values(a.id,p_key::uuid,p_conversation::uuid,p_body,result);
  update ops.doc_conversation set updated_at=now() where id=p_conversation::uuid;
  return result;
end $$;
revoke all on function ops.submit_doc_request(text,text,text) from public, carr_reader, carr_authority, carr_jobs, dot_reader;
grant execute on function ops.submit_doc_request(text,text,text) to carr_writer;

do $grants$
declare contract jsonb := ops.scac_current_grant_contract(); entry jsonb;
begin
  select jsonb_build_object('config',to_jsonb(p.proconfig),'grantee','carr_writer',
    'parallel',p.proparallel::text,'grantable',false,'privilege','execute',
    'signature','ops.submit_doc_request('||pg_get_function_identity_arguments(p.oid)||')',
    'volatility',p.provolatile::text,'ingress_key','db-function-acl:ops.submit_doc_request('
      ||pg_get_function_identity_arguments(p.oid)||'):carr_writer:execute',
    'ingress_kind','db_function_acl','function_kind',p.prokind::text,'security_definer',p.prosecdef)
    into strict entry from pg_proc p where p.oid='ops.submit_doc_request(text,text,text)'::regprocedure;
  contract := jsonb_set(contract,'{secdef_execute}',contract->'secdef_execute'||jsonb_build_array(entry));
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb '
    'language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $grants$;

create or replace function ops.append_doc_conversation_turn(
  p_conversation uuid, p_role text, p_body text, p_msg_id uuid, p_idempotency_key uuid, p_answer_blocks jsonb default '[]'::jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp
as $$
declare v_actor uuid; v_slug text; v_id bigint; v_sequence integer; v_prior ops.doc_conversation_turn%rowtype;
begin
  if p_answer_blocks is null or jsonb_typeof(p_answer_blocks) <> 'array'
     or octet_length(p_answer_blocks::text) > 131072 then
    return jsonb_build_object('ok', false, 'reason_id', 'answer_blocks_invalid');
  end if;
  if jsonb_array_length(p_answer_blocks) > 8
     or (p_role <> 'assistant' and p_answer_blocks <> '[]'::jsonb) then
    return jsonb_build_object('ok', false, 'reason_id', 'answer_blocks_invalid');
  end if;
  -- Both turn writers hold this lock before reading the next sequence.
  perform 1 from ops.doc_conversation where id = p_conversation for update;
  if not found then
    return jsonb_build_object('ok', false, 'reason_id', 'doc_conversation_not_found');
  end if;
  -- The acting actor, resolved from the server-installed transaction context.
  -- A caller cannot name one: there is no parameter for it.
  v_actor := ops.portfolio_writer_actor_id();
  select slug into v_slug from public.actor where id = v_actor;
  if v_slug is null then
    raise exception 'doc conversation append could not resolve the acting actor';
  end if;

  insert into ops.doc_conversation_turn(
    conversation_id, sequence, role, body, msg_id, origin_channel, origin_actor, answer_blocks)
  select p_conversation,
         coalesce((select max(sequence) + 1 from ops.doc_conversation_turn
                    where conversation_id = p_conversation), 0),
         p_role, p_body, p_msg_id, 'mcp', v_slug, p_answer_blocks
  on conflict (msg_id) do nothing
  returning id, sequence into v_id, v_sequence;

  if v_id is null then
    select * into v_prior from ops.doc_conversation_turn where msg_id = p_msg_id;
    if v_prior.id is null then
      return jsonb_build_object('ok', false, 'reason_id', 'dedup_row_vanished');
    end if;
    -- The record-signal full-field replay comparison: the same msg_id offered
    -- with different content is a REUSE, not a duplicate.
    if v_prior.conversation_id is distinct from p_conversation
       or v_prior.role is distinct from p_role
       or v_prior.body is distinct from p_body
       or v_prior.answer_blocks is distinct from p_answer_blocks then
      return jsonb_build_object('ok', false, 'reason_id', 'doc_conversation_msg_id_reuse',
        'msg_id', p_msg_id);
    end if;
    return jsonb_build_object('ok', true, 'deduplicated', true, 'id', v_prior.id,
      'sequence', v_prior.sequence, 'msg_id', p_msg_id, 'origin_actor', v_prior.origin_actor,
      'idempotency_key', p_idempotency_key);
  end if;

  update ops.doc_conversation set updated_at = now() where id = p_conversation;
  return jsonb_build_object('ok', true, 'deduplicated', false, 'id', v_id,
    'sequence', v_sequence, 'msg_id', p_msg_id, 'origin_actor', v_slug,
    'origin_channel', 'mcp', 'idempotency_key', p_idempotency_key);
end $$;
