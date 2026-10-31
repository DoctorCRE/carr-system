-- rollback: forward-only; retain sourced blocks already appended to immutable turns.
-- expand-contract: migrate; the replacement default argument preserves every five-argument caller.
-- lock-review: a constant JSONB default avoids a rewrite; bound the metadata lock to five seconds.
set local lock_timeout = '5s';

grant select on public.deal, public.v_deal_room_negotiation to carr_authority;

-- WR617.S10.J2: sourced answer blocks stay on the canonical immutable turn.
alter table ops.doc_conversation_turn
  add column answer_blocks jsonb not null default '[]'::jsonb;

-- Replace the old arity. Its callers keep working through the default argument;
-- there is still exactly one function that inserts conversation turns.
drop function ops.append_doc_conversation_turn(uuid,text,text,uuid,uuid);

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
  if not exists (select 1 from ops.doc_conversation where id = p_conversation) then
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

revoke all on function ops.append_doc_conversation_turn(uuid,text,text,uuid,uuid,jsonb)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.append_doc_conversation_turn(uuid,text,text,uuid,uuid,jsonb)
  to carr_authority;

comment on function ops.append_doc_conversation_turn(uuid,text,text,uuid,uuid,jsonb) is
  'Sole Doc turn writer. Server-built answer_blocks are validated at the registered verb; replay compares the complete answer.';

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

  select coalesce(jsonb_agg(to_jsonb(t) order by t.sequence), '[]'::jsonb) into v_turns
    from (select sequence, role, body, msg_id, origin_channel, origin_actor, at, answer_blocks
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

-- Preserve the frozen catalog's old grant evidence and register the replacement.
do $answer_grants$
declare
  v_contract jsonb := ops.scac_current_grant_contract();
  retired jsonb;
  replacement jsonb;
  old_signature text := 'ops.append_doc_conversation_turn(p_conversation uuid, p_role text, p_body text, p_msg_id uuid, p_idempotency_key uuid)';
  new_signature text := 'ops.append_doc_conversation_turn(p_conversation uuid, p_role text, p_body text, p_msg_id uuid, p_idempotency_key uuid, p_answer_blocks jsonb)';
begin
  select e.contract - 'effect_class' - 'owner_package' - 'implementation_state'
    - 'classification_authorizing' - 'source_locator' into strict retired
    from ops.scac_mutation_registry_entry e
   where e.registry_version = v_contract->>'registry_version'
     and e.ingress_key = 'db-function-acl:' || old_signature || ':carr_authority:execute';
  replacement := retired || jsonb_build_object('signature', new_signature,
    'ingress_key', 'db-function-acl:' || new_signature || ':carr_authority:execute');
  v_contract := jsonb_set(v_contract, '{retired_secdef_execute}',
    (v_contract->'retired_secdef_execute') || jsonb_build_array(retired));
  v_contract := jsonb_set(v_contract, '{secdef_execute}',
    (v_contract->'secdef_execute') || jsonb_build_array(replacement));
  execute format('CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb '
    'LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT ' || quote_literal(v_contract::text) || '::jsonb');
  if ops.scac_mutation_catalog_v118_current() is not true then
    raise exception 'Doc answer append grant catalog is invalid';
  end if;
end $answer_grants$;
