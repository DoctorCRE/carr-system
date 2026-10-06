-- WR-000112 — the measured grant and attribution proof for the Doc
-- conversation store. Run by ops/ci.sh's migration class.
--
-- WHAT ONLY A DATABASE CAN SHOW: that the append really is authority-only and
-- the read really does reach carr_writer (a flag that named the wrong
-- connection would be a run-time permission error that reads like a missing
-- grant), that carr_reader holds NOTHING on the four relations because the
-- projection function is the only door, and that origin_actor is derived from
-- the session rather than from any argument -- the function has no parameter
-- for it to take one from.

\set ON_ERROR_STOP on

do $wr112_grants$
declare v_bad text;
begin
  -- The append: carr_authority ONLY.
  if not has_function_privilege('carr_authority','ops.append_doc_conversation_turn(uuid,text,text,uuid,uuid)','execute')
     or has_function_privilege('carr_writer','ops.append_doc_conversation_turn(uuid,text,text,uuid,uuid)','execute')
     or has_function_privilege('carr_reader','ops.append_doc_conversation_turn(uuid,text,text,uuid,uuid)','execute')
     or has_function_privilege('public','ops.append_doc_conversation_turn(uuid,text,text,uuid,uuid)','execute') then
    raise exception 'WR-000112: the append function is not authority-only';
  end if;

  -- The read: carr_writer AND carr_authority, because the verb declares
  -- writerConnection and that is the identity it arrives on.
  if not has_function_privilege('carr_writer','ops.doc_conversation_facts(uuid,text,integer,integer)','execute')
     or not has_function_privilege('carr_authority','ops.doc_conversation_facts(uuid,text,integer,integer)','execute')
     or has_function_privilege('carr_reader','ops.doc_conversation_facts(uuid,text,integer,integer)','execute')
     or has_function_privilege('public','ops.doc_conversation_facts(uuid,text,integer,integer)','execute') then
    raise exception 'WR-000112: the read function does not reach exactly the writer and authority bundles';
  end if;

  -- carr_reader holds NOTHING on the four relations: the projection function is
  -- the only door, exactly as public.v_partner_room_turn is for the partner room.
  for v_bad in
    select rel from unnest(array['ops.doc_conversation','ops.doc_conversation_title_revision',
      'ops.doc_conversation_turn','ops.doc_conversation_grant']) rel
    where has_table_privilege('carr_reader', rel, 'select')
  loop
    raise exception 'WR-000112: carr_reader can read % around the feed function', v_bad;
  end loop;

  -- And no runtime bundle may change a row directly.
  for v_bad in
    select rel from unnest(array['ops.doc_conversation','ops.doc_conversation_title_revision',
      'ops.doc_conversation_turn','ops.doc_conversation_grant']) rel
    cross join unnest(array['carr_reader','carr_writer','carr_jobs','carr_authority','public']) grantee
    cross join unnest(array['insert','update','delete','truncate']) priv
    where has_table_privilege(grantee, rel, priv)
  loop
    raise exception 'WR-000112: % is directly writable by a runtime bundle', v_bad;
  end loop;
end $wr112_grants$;

do $wr112_attribution$
declare v_actor uuid; v_conversation uuid; v_first jsonb; v_replay jsonb; v_reuse jsonb;
        v_msg uuid := gen_random_uuid(); v_turns integer;
begin
  perform set_config('carr.acting_actor_slug', 'joe', true);
  perform set_config('carr.verified_human_actor_slug', 'joe', true);
  select id into v_actor from public.actor where slug = 'joe';
  if v_actor is null then
    raise exception 'WR-000112: this proof needs the partner actor row db/schema.sql seeds';
  end if;

  insert into ops.doc_conversation(title, created_by_actor)
  values ('WR-000112 postgres proof', v_actor) returning id into v_conversation;

  -- THE FUNCTION TAKES NO ORIGIN ARGUMENT. It is derived here or not at all.
  v_first := ops.append_doc_conversation_turn(v_conversation, 'human', 'first', v_msg, gen_random_uuid());
  if v_first->>'origin_actor' <> 'joe' or v_first->>'origin_channel' <> 'mcp' then
    raise exception 'WR-000112: attribution was not server-derived: %', v_first;
  end if;
  if (v_first->>'sequence')::integer <> 0 then
    raise exception 'WR-000112: the first sequence is not 0: %', v_first;
  end if;

  -- The same msg_id again, identical content: deduplicated, ONE stored turn.
  v_replay := ops.append_doc_conversation_turn(v_conversation, 'human', 'first', v_msg, gen_random_uuid());
  if not (v_replay->>'deduplicated')::boolean then
    raise exception 'WR-000112: an identical msg_id was not deduplicated: %', v_replay;
  end if;

  -- The same msg_id with a different body is a REUSE, not a duplicate.
  v_reuse := ops.append_doc_conversation_turn(v_conversation, 'human', 'changed', v_msg, gen_random_uuid());
  if (v_reuse->>'ok')::boolean or v_reuse->>'reason_id' <> 'doc_conversation_msg_id_reuse' then
    raise exception 'WR-000112: a changed body under a reused msg_id was accepted: %', v_reuse;
  end if;

  select count(*) into v_turns from ops.doc_conversation_turn where conversation_id = v_conversation;
  if v_turns <> 1 then
    raise exception 'WR-000112: % turns stored where one was offered three ways', v_turns;
  end if;

  -- The access list is enforced by the function, not by the caller. A second
  -- actor that is neither the creator nor an unrevoked grantee gets the SAME
  -- answer an absent conversation gets.
  if (ops.doc_conversation_facts(v_conversation, gen_random_uuid()::text, 0, 200)->>'reason_id')
     <> 'doc_conversation_not_found' then
    raise exception 'WR-000112: a non-member was answered differently from an absent conversation';
  end if;
end $wr112_attribution$;

do $wr112_immutability$
begin
  begin
    update ops.doc_conversation_turn set body = 'rewritten';
    raise exception 'WR-000112: a stored turn was rewritten';
  exception when raise_exception then
    if position('immutable' in sqlerrm) = 0 then raise; end if;
  end;
end $wr112_immutability$;

select 'WR-000112 Doc conversation store: append authority-only, read writer-bound, attribution server-derived' as proof;

-- B08: two obligations may share a sentence, never a suggestion identity.
do $b08_suggestion_flow$
declare v_actor uuid; v_conversation uuid; v_rent uuid; v_insurance uuid;
        v_result jsonb; v_cards jsonb; v_draft text := 'Insurance is due next month.';
        v_key uuid := gen_random_uuid(); v_empty uuid; v_scan_key uuid := gen_random_uuid();
begin
  perform set_config('carr.acting_actor_slug', 'joe', true);
  perform set_config('carr.verified_human_actor_slug', 'joe', true);
  select id into v_actor from public.actor where slug='joe';
  insert into ops.doc_conversation(title,created_by_actor)
    values('B08 coverage proof',v_actor) returning id into v_empty;
  if ops.list_doc_suggestions(v_empty,false)->'coverage'->>'state'<>'unknown' then
    raise exception 'B08 unscanned empty conversation claimed verified coverage'; end if;
  perform ops.append_doc_conversation_turn(v_empty,'human','No follow-up is needed.',gen_random_uuid(),gen_random_uuid());
  v_result:=ops.complete_doc_suggestion_scan(v_empty,0,v_scan_key);
  if v_result->>'ok'<>'true' or
     ops.list_doc_suggestions(v_empty,false)->'coverage'->>'state'<>'complete' or
     ops.list_doc_suggestions(v_empty,false)->'coverage'->>'empty_state'<>'verified_empty' or
     jsonb_array_length(ops.list_doc_suggestions(v_empty,false)->'suggestions')<>0 then
    raise exception 'B08 producer verified-empty coverage missing: %',v_result; end if;
  if ops.complete_doc_suggestion_scan(v_empty,0,v_scan_key)->>'deduplicated'<>'true' then
    raise exception 'B08 exact scan replay was not deduplicated'; end if;
  perform ops.append_doc_conversation_turn(v_empty,'human','Check this too.',gen_random_uuid(),gen_random_uuid());
  if ops.list_doc_suggestions(v_empty,false)->'coverage'->>'state'<>'unknown' then
    raise exception 'B08 new turn did not stale producer coverage'; end if;
  if ops.list_doc_suggestions(v_empty,false)->'coverage'->>'empty_state'<>'unknown' then
    raise exception 'B08 stale empty state was reported as verified'; end if;
  if ops.complete_doc_suggestion_scan(v_empty,0,gen_random_uuid())->>'reason_id'<>'scan_head_conflict' then
    raise exception 'B08 stale producer scan was accepted'; end if;
  if ops.complete_doc_suggestion_scan(v_empty,1,v_scan_key)->>'reason_id'<>'idempotency_key_reuse' then
    raise exception 'B08 changed scan replay was accepted'; end if;
  insert into ops.doc_conversation(title,created_by_actor)
    values('B08 distinct obligations proof',v_actor) returning id into v_conversation;
  perform ops.append_doc_conversation_turn(v_conversation,'human',
    'Rent and insurance each need review.',gen_random_uuid(),gen_random_uuid());
  v_result:=ops.suggest_doc_work(v_conversation,0,'rent','Review the terms',null,
    '{"term":"rent"}'::jsonb,v_key);
  v_rent:=(v_result->>'suggestion_id')::uuid;
  if ops.suggest_doc_work(v_conversation,0,'rent','Review the terms',null,
      '{"term":"rent"}'::jsonb,v_key)->>'deduplicated'<>'true' then
    raise exception 'B08 exact suggestion replay was not deduplicated'; end if;
  if ops.suggest_doc_work(v_conversation,0,'rent','Changed polish',null,
      '{"term":"rent"}'::jsonb,v_key)->>'reason_id'<>'idempotency_key_reuse' or
     ops.suggest_doc_work(v_conversation,0,'rent','Review the terms','uncertain',
      '{"term":"rent"}'::jsonb,v_key)->>'reason_id'<>'idempotency_key_reuse' or
     ops.suggest_doc_work(v_conversation,0,'rent','Review the terms',null,
      '{"term":"other"}'::jsonb,v_key)->>'reason_id'<>'idempotency_key_reuse' then
    raise exception 'B08 changed suggestion replay was accepted'; end if;
  v_result:=ops.suggest_doc_work(v_conversation,0,'insurance','Review the terms',null,
    '{"term":"insurance"}'::jsonb,gen_random_uuid());
  v_insurance:=(v_result->>'suggestion_id')::uuid;
  if v_rent=v_insurance then raise exception 'B08 combined distinct obligations'; end if;
  v_cards:=ops.list_doc_suggestions(v_conversation,false)->'suggestions';
  if jsonb_array_length(v_cards)<>2 or
     (select count(distinct x->>'obligation_key') from jsonb_array_elements(v_cards) x)<>2 or
     (select count(*) from jsonb_array_elements(v_cards) x where x->>'original_text'=
       'Rent and insurance each need review.' and x->>'contributor'='joe')<>2 then
    raise exception 'B08 lost distinct obligations or source provenance: %',v_cards; end if;
  v_result:=ops.decide_doc_suggestion(v_rent,1,'dismiss',null,null,gen_random_uuid());
  if not (v_result->>'ok')::boolean then raise exception 'B08 dismiss refused: %',v_result; end if;
  if jsonb_array_length(ops.list_doc_suggestions(v_conversation,false)->'suggestions')<>1 then
    raise exception 'B08 dismissal did not persist'; end if;
  perform ops.append_doc_conversation_turn(v_conversation,'human',
    'Rent still needs review.',gen_random_uuid(),gen_random_uuid());
  perform ops.suggest_doc_work(v_conversation,1,'rent','Review the terms',null,
    '{"term":"rent"}'::jsonb,gen_random_uuid());
  if jsonb_array_length(ops.list_doc_suggestions(v_conversation,false)->'suggestions')<>1 then
    raise exception 'B08 same material reopened dismissal'; end if;
  perform ops.append_doc_conversation_turn(v_conversation,'human',
    'Rent review now has a new deadline.',gen_random_uuid(),gen_random_uuid());
  perform ops.suggest_doc_work(v_conversation,2,'rent','Review the new deadline',null,
    '{"term":"rent","deadline":"new"}'::jsonb,gen_random_uuid());
  if jsonb_array_length(ops.list_doc_suggestions(v_conversation,false)->'suggestions')<>2 then
    raise exception 'B08 material change did not reopen dismissal'; end if;
  if ops.suggest_doc_work(v_conversation,0,'rent','Review the terms',null,
      '{"term":"rent"}'::jsonb,v_key)->>'deduplicated'<>'true' or
     ops.suggest_doc_work(v_conversation,0,'rent','Review the new deadline',null,
      '{"term":"rent","deadline":"new"}'::jsonb,v_key)->>'reason_id'<>'idempotency_key_reuse' then
    raise exception 'B08 replay did not bind the original request after a later version'; end if;
  v_result:=ops.propose_doc_correction(v_rent,1,v_draft,v_conversation,2,gen_random_uuid());
  if v_result->>'reason_id'<>'version_conflict' or v_result->'current'->>'polished_text'<>'Review the new deadline' then
    raise exception 'B08 stale correction lost current record: %',v_result; end if;
  v_result:=ops.propose_doc_correction(v_rent,4,v_draft,v_conversation,2,gen_random_uuid());
  if v_result->>'status'<>'pending' or
     (select polished_text from ops.doc_suggestion where id=v_rent)<>'Review the new deadline' then
    raise exception 'B08 correction silently overwrote the suggestion: %',v_result; end if;
  v_cards:=ops.list_doc_suggestions(v_conversation,false)->'suggestions';
  if (select count(*) from jsonb_array_elements(v_cards) x,
      jsonb_array_elements(x->'corrections') c
      where x->>'id'=v_rent::text and c->>'proposed_text'=v_draft
        and c->>'status'='pending' and (c->>'base_version')::integer=4)<>1 then
    raise exception 'B08 pending correction is absent from the suggestion read: %',v_cards; end if;
end $b08_suggestion_flow$;

select 'B08 Doc suggestions: obligations, provenance, dismissal, material change and conflict' as proof;
