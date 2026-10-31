-- rollback: restore ops.adopt_rule from 1027 and public.v_rule_lookup without retirement; retire no rules on migration.
-- expand-contract: expand the existing proposal grammar and append retirement metadata to the reader view.
-- lock-review: no table rewrite; target rules lock in UUID order inside adoption's existing document lock.
-- WR562.S4.J1: use the existing authority and immutable retirement receipt chain.
create or replace function ops.adopt_rule(p_section_id uuid, p_base_version bigint, p_idempotency_key text, p_reason text)
returns jsonb language plpgsql volatile security definer
set search_path=pg_catalog,ops,public,pg_temp as $$
declare
  v_slug text := ops.authority_actor_slug();
  v_actor uuid;
  v_section record;
  v_text text;
  v_statement text;
  v_card_text text;
  v_card jsonb;
  v_rule uuid;
  v_layer text;
  v_packs_text text;
  v_packs text[];
  v_approval jsonb;
  v_generation bigint;
  v_attach uuid;
  v_change_set uuid;
  v_retires jsonb := '[]'::jsonb;
  v_target uuid;
  v_target_status text;
begin
  select id into v_actor from public.actor where slug=v_slug and kind='human' and active;
  if v_actor is null then
    raise exception 'adopt_rule_requires_human_authority';
  end if;
  if btrim(coalesce(p_idempotency_key,''))='' or btrim(coalesce(p_reason,''))='' then
    raise exception 'adopt_rule_reason_required';
  end if;
  perform 1 from public.doctrine_document d join public.doctrine_section s on s.document_id=d.id
   where s.id=p_section_id for update of d;
  select s.id,s.document_id,s.section_key,s.status,s.current_version,d.slug,r.body
    into v_section
    from public.doctrine_section s
    join public.doctrine_document d on d.id=s.document_id
    left join public.doctrine_revision r on r.id=s.current_revision_id
   where s.id=p_section_id for update of s;
  if not found or v_section.slug<>'team-memory' or v_section.status<>'active'
     or v_section.section_key not like 'conventions/proposed/%' then
    raise exception 'convention_proposal_not_found';
  end if;
  if v_section.current_version<>p_base_version then
    raise exception 'version_conflict';
  end if;

  -- Retires alone removes rules; Convention with Card or Packs can replace them.
  v_text := v_section.body->>'text';
  v_statement := btrim(substring(v_text from '^Convention: ([^\n]+)'));
  begin
    if (select count(*) from regexp_matches(v_text,'(^|\n)Retires:','g'))>1 then
      raise exception 'convention_proposal_malformed';
    end if;
    if v_text ~ '(^|\n)Retires:' then
      v_retires := substring(v_text from '(?:^|\n)Retires: ([^\n]+)')::jsonb;
      if jsonb_typeof(v_retires) is distinct from 'array' or jsonb_array_length(v_retires)=0 then
        raise exception 'convention_proposal_malformed';
      end if;
      if exists (select 1 from jsonb_array_elements(v_retires) e
                  where jsonb_typeof(e)<>'string' or e#>>'{}' !~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$') then
        raise exception 'convention_proposal_malformed';
      end if;
      select jsonb_agg(id order by id) into v_retires
        from (select distinct value::uuid as id from jsonb_array_elements_text(v_retires)) ids;
    end if;
  exception when others then
    raise exception 'convention_proposal_malformed';
  end;
  if coalesce(v_statement,'')='' and (jsonb_array_length(v_retires)=0 or v_text !~ '^Retires: ') then
    raise exception 'convention_proposal_malformed';
  end if;
  for v_target in select value::uuid from jsonb_array_elements_text(v_retires) loop
    select status into v_target_status from public.rule where id=v_target for update;
    if not found then raise exception 'convention_retirement_rule_not_found'; end if;
    if v_target_status<>'active' then raise exception 'convention_retirement_rule_not_active'; end if;
  end loop;
  if coalesce(v_statement,'')<>'' then
    v_card_text := substring(v_text from '\nCard: (\{[^\n]*\})[ \t]*(?:\n|$)');
    if v_card_text is not null then
      begin
        v_card := v_card_text::jsonb;
      exception when others then
        raise exception 'convention_proposal_malformed';
      end;
      if jsonb_typeof(v_card)<>'object' or coalesce(v_card->>'id','')='' or v_card ? 'wording'
         or coalesce(v_card->>'placement','') not in ('CORE','PROMPT','TOOL','PLANNING') then
        raise exception 'convention_proposal_malformed';
      end if;
      v_card := v_card - 'word_count';
      if exists (select 1 from public.rule where status='active' and card->>'id'=v_card->>'id'
                 and not v_retires @> jsonb_build_array(id)) then
        raise exception 'convention_card_already_active';
      end if;
    end if;

    -- A card is delivered by the generated core block or the rule-card hook, so
    -- it names no packs. A convention without a card names the existing packs
    -- that deliver it; there is no default pack and no new pack.
    v_packs_text := substring(v_text from '\nPacks: ([^\n]*)');
    if v_card is not null then
      if v_packs_text is not null then
        raise exception 'convention_proposal_malformed';
      end if;
    else
      select array_agg(distinct btrim(p) order by btrim(p)) into v_packs
        from regexp_split_to_table(coalesce(v_packs_text,''), ',') p
       where btrim(p)<>'';
      if v_packs is null then
        raise exception 'convention_proposal_malformed';
      end if;
      if exists (select 1 from unnest(v_packs) n where not exists
                   (select 1 from ops.rule_pack rp where rp.pack=n)) then
        raise exception 'convention_pack_unknown';
      end if;
    end if;

  end if;
  if coalesce(v_statement,'')='' and (v_text ~ '(^|\n)(Card|Packs|Convention):') then
    raise exception 'convention_proposal_malformed';
  end if;
  -- The receipt references an existing replacement; assign its card after
  -- retirement so the active-card uniqueness check permits reusing the id.
  if coalesce(v_statement,'')<>'' then
    insert into public.rule(statement,taught_by,status)
    values (v_statement,v_actor,'proposed') returning id into v_rule;
  end if;
  for v_target in select value::uuid from jsonb_array_elements_text(v_retires) loop
    perform ops.retire_rule(v_target,
      btrim(p_reason)||E'\nProposal: '||p_section_id::text||'@'||p_base_version::text,
      v_rule, 'adopt-rule:'||p_idempotency_key||':'||v_target::text);
  end loop;
  if coalesce(v_statement,'')<>'' then
    update public.rule set card=v_card where id=v_rule;
    -- Delivery, never the capped shared layer0 boot recitation: a card on the
    -- control layer, a card-less convention on the pack layer of its named packs.
    v_layer := case when v_card is null then 'pack' else 'control' end;
    insert into ops.rule_load_layer(rule_id,short_id,load_layer,packs,scope,why,source,map_digest)
    values (v_rule,left(v_rule::text,8),v_layer,
            coalesce(v_packs,'{}'::text[]),'shared',
            case when v_card is null then null
                 else 'adopted rule card; delivered by the generated core block or the rule-card hook' end,
            'ops.adopt_rule',encode(public.digest(coalesce(v_card::text,array_to_string(v_packs,','))||v_statement,'sha256'),'hex'));
    v_approval := ops.approve_rule(v_rule,'human_only',array['human_authority_runtime'],
                                   p_idempotency_key,p_reason);
  end if;

  -- Retire the proposal as the approval surface's own attach (surface 'app').
  select generation into v_generation from public.doctrine_meta where id=1;
  insert into public.memory_session(surface,actor_id,access,attached_generation,attached_index_digest)
  values ('app',v_actor,'read_write',v_generation,
          encode(public.digest(p_section_id::text||':'||p_base_version::text,'sha256'),'hex'))
  returning attach_id into v_attach;
  insert into public.doctrine_change_set(actor_id,session_key,idempotency_key,state,committed_at)
  values (v_actor,v_attach::text,'adopt-rule:'||p_idempotency_key,'committed',now())
  returning id into v_change_set;
  insert into public.doctrine_change_item(change_set_id,section_id,op,expected_version)
  values (v_change_set,p_section_id,'retire',p_base_version);
  update public.doctrine_section set status='retired',updated_at=now(),current_version=current_version+1
   where id=p_section_id;
  update public.doctrine_meta set generation=generation+1,updated_at=now() where id=1
  returning generation into v_generation;
  -- Same snapshot shape as rebuildSnapshot in mcp-server/src/doctrine.js.
  insert into public.doctrine_snapshot(document_id,generation,snapshot_json,content_hash,built_at)
  select d.id,v_generation,
         jsonb_build_object(
           'document',jsonb_build_object('id',d.id,'slug',d.slug,'title',d.title,
             'content_class',d.content_class,'visibility',d.visibility),
           'sections',coalesce((
             select jsonb_agg(jsonb_build_object(
                      'section_id',s.id,'section_key',s.section_key,'title',s.title,
                      'ordinal',s.ordinal,'version',s.current_version,
                      'status',s.status,'review_after',s.review_after,
                      'body',r.body,'content_hash',r.content_hash) order by s.ordinal)
               from public.doctrine_section s
               left join public.doctrine_revision r on r.id=s.current_revision_id
              where s.document_id=d.id and s.status='active'),'[]'::jsonb),
           'edges_out',coalesce((
             select jsonb_agg(jsonb_build_object('edge_type',e.edge_type,
                      'source_section_id',e.source_section_id,
                      'target_section_id',e.target_section_id,'scope',e.scope))
               from public.doctrine_edge e
               join public.doctrine_section s on s.id=e.source_section_id
              where s.document_id=d.id and e.retired_by_revision_id is null),'[]'::jsonb)),
         md5((select coalesce(string_agg(s.body_hash,'' order by s.ordinal),'')
                from public.doctrine_section s where s.document_id=d.id)),
         now()
    from public.doctrine_document d where d.id=v_section.document_id
  on conflict (document_id) do update
    set generation=excluded.generation,snapshot_json=excluded.snapshot_json,
        content_hash=excluded.content_hash,built_at=excluded.built_at;

  return jsonb_build_object('ok',true,'rule_id',v_rule,'card_id',v_card->>'id',
    'load_layer',v_layer,'packs',to_jsonb(coalesce(v_packs,'{}'::text[])),'approval',v_approval,'retired_rule_ids',v_retires,'retired_section_id',p_section_id,
    'attach_id',v_attach,'generation',v_generation);
end $$;

create or replace view public.v_rule_lookup as
  select r.id,r.status,r.version,r.created_at,r.scope,r.statement,a.slug as personal_to,r.card,
    case when rr.id is not null then jsonb_build_object(
      'retired_by',ra.slug,'retired_at',rr.retired_at,'reason',rr.reason,
      'superseded_by',rr.superseded_by,'receipt_id',rr.id) end as retirement
  from public.rule r
  left join public.actor a on a.id=r.personal_to
  left join ops.rule_retirement_receipt rr on rr.rule_id=r.id and r.status='retired'
  left join public.actor ra on ra.id=rr.actor_id;
