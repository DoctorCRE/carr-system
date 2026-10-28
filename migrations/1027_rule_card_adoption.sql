-- rollback: revoke and drop ops.adopt_rule, restore ops.read_governance_queue from 0856 (drop the pending_convention_proposals lane), restore public.v_rule_lookup without card, drop index rule_active_card_id, then drop column public.rule.card (nullable and only written by ops.adopt_rule, so nothing else reads it).
-- expand-contract: expand — one nullable column, one view column appended, one partial unique index, one new function, and a fourth governance-queue lane added to ops.read_governance_queue; existing lanes and columns are unchanged.
-- lock-review: public.rule holds a few hundred rows; the non-concurrent partial index builds in milliseconds.
-- WR-000562 slice S4 (docs/memory/team-memory-design.md D1, D16): card wording
-- lives on the rule, and adopt-rule is the one door from a team-memory
-- convention proposal to an active rule.

-- D16: a card's wording is the rule's statement; card holds the rest of the
-- card (id, title, placement, core_rank, acknowledgement, triggers, ...).
alter table public.rule add column card jsonb
  check (card is null or (jsonb_typeof(card) = 'object' and card ? 'id' and not card ? 'wording'));
comment on column public.rule.card is
  'Rule card metadata (WR-000562 D16) without its wording, which is the statement. Rendered into ops/config/rule-cards.v1.json and the CLAUDE.md/AGENTS.md block by ops/sync-core-rule-cards.py --from-store.';
create unique index rule_active_card_id on public.rule ((card->>'id'))
  where status = 'active' and card is not null;

-- The renderer reads cards through the reader view (rule-cards verb).
create or replace view public.v_rule_lookup as
  select r.id,r.status,r.version,r.created_at,r.scope,r.statement,a.slug as personal_to,r.card
    from public.rule r left join public.actor a on a.id=r.personal_to;

-- governance-queue gains its fourth lane through the same one definer read:
-- team-memory convention proposals waiting on adopt-rule. The other three
-- lanes are unchanged from migration 0856.
create or replace function ops.read_governance_queue() returns jsonb
language sql stable security definer set search_path to 'ops', 'public', 'pg_temp' as $$
  select jsonb_build_object(
    'pending_rule_approvals', coalesce((
      select jsonb_agg(jsonb_build_object(
        'rule_id', r.id, 'statement', r.statement, 'human_quote', r.human_quote,
        'scope', r.scope, 'taught_at', r.created_at,
        'enforcement_class', ra.enforcement_class, 'binding_moment', ra.binding_moment,
        'admission_reason', ra.reason, 'enforcement_status', ra.enforcement_status,
        'fixture_refs', ra.fixture_refs, 'admitted_at', ra.admitted_at
      ) order by ra.admitted_at asc, r.id)
      from public.rule r join ops.rule_admission ra on ra.rule_id = r.id
      where r.status = 'proposed' and ra.state = 'admitted'
    ), '[]'::jsonb),
    'pending_guidance_import_batches', coalesce((
      select jsonb_agg(jsonb_build_object(
        'batch_id', b.id, 'manifest_digest', b.manifest_digest, 'reason', b.reason,
        'staging_key', b.staging_key, 'staged_at', b.created_at,
        'entry_count', (select count(*) from ops.guidance_import_entry e where e.batch_id = b.id)
      ) order by b.created_at asc, b.id)
      from ops.guidance_import_batch b
      where not exists (select 1 from ops.guidance_import_decision_event d where d.batch_id = b.id)
    ), '[]'::jsonb),
    'pending_retrieval_proposals', coalesce((
      select jsonb_agg(jsonb_build_object(
        'proposal_id', p.id, 'proposal_type', p.proposal_type, 'payload', p.payload,
        'reason', p.reason, 'proposer_actor_id', p.proposer_id, 'version', p.version,
        'proposed_at', p.created_at
      ) order by p.created_at asc, p.id)
      from public.retrieval_proposal p
      where p.status = 'pending'
    ), '[]'::jsonb),
    'pending_convention_proposals', coalesce((
      select jsonb_agg(jsonb_build_object(
        'section_id', s.id, 'section_key', s.section_key, 'base_version', s.current_version,
        'updated_at', s.updated_at, 'body_text', rv.body->>'text'
      ) order by s.section_key)
      from public.doctrine_section s
      join public.doctrine_document d on d.id = s.document_id
      left join public.doctrine_revision rv on rv.id = s.current_revision_id
      where d.slug = 'team-memory' and s.status = 'active'
        and s.section_key like 'conventions/proposed/%'
    ), '[]'::jsonb)
  )
$$;

-- One transaction: the proposal section becomes a proposed rule, the existing
-- Joe-authority approval chain (ops.approve_rule, policy human_only, control
-- human_authority_runtime) activates it, and the proposal section retires
-- under an 'app' attach. Callable only on a human authority login; the
-- adopt-rule verb is humanCallerOnly in front of it.
create function ops.adopt_rule(p_section_id uuid, p_base_version bigint, p_idempotency_key text, p_reason text)
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
  v_approval jsonb;
  v_generation bigint;
  v_attach uuid;
  v_change_set uuid;
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

  -- Proposal body: "Convention: <statement>" on the first line, an optional
  -- "Card: <json object>" line, then free notes that are never adopted.
  v_text := v_section.body->>'text';
  v_statement := btrim(substring(v_text from '^Convention: ([^\n]+)'));
  if coalesce(v_statement,'')='' then
    raise exception 'convention_proposal_malformed';
  end if;
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
    if exists (select 1 from public.rule where status='active' and card->>'id'=v_card->>'id') then
      raise exception 'convention_card_already_active';
    end if;
  end if;

  insert into public.rule(statement,taught_by,status,card)
  values (v_statement,v_actor,'proposed',v_card) returning id into v_rule;
  -- Delivery, never the capped shared layer0 boot recitation:
  --  * a card is carried by the generated CLAUDE.md/AGENTS.md block (CORE)
  --    or by the rule-card hook when its triggers fire;
  --  * a convention without a card joins the existing engineering-git pack,
  --    which the per-call pack hook loads on the git, commit, PR, CI and test
  --    work every agent session in this system does. The pack must exist
  --    (rule_load_layer_packs_exist), so this never invents a pack.
  v_layer := case when v_card is null then 'pack' else 'control' end;
  insert into ops.rule_load_layer(rule_id,short_id,load_layer,packs,scope,why,source,map_digest)
  values (v_rule,left(v_rule::text,8),v_layer,
          case when v_card is null then array['engineering-git'] else '{}'::text[] end,'shared',
          case when v_card is null then null
               else 'adopted rule card; delivered by the generated core block or the rule-card hook' end,
          'ops.adopt_rule',encode(public.digest(coalesce(v_card::text,'')||v_statement,'sha256'),'hex'));
  v_approval := ops.approve_rule(v_rule,'human_only',array['human_authority_runtime'],
                                 p_idempotency_key,p_reason);

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
    'load_layer',v_layer,'approval',v_approval,'retired_section_id',p_section_id,
    'attach_id',v_attach,'generation',v_generation);
end $$;
revoke all on function ops.adopt_rule(uuid,bigint,text,text) from public;
grant execute on function ops.adopt_rule(uuid,bigint,text,text) to carr_authority;

do $contract$
declare contract jsonb := ops.scac_current_grant_contract();
begin
  contract := jsonb_set(contract, '{secdef_execute}', (contract->'secdef_execute') || jsonb_build_array(jsonb_build_object(
    'config', jsonb_build_array('search_path=pg_catalog, ops, public, pg_temp'), 'grantee', 'carr_authority', 'parallel', 'u',
    'grantable', false, 'privilege', 'execute',
    'signature', 'ops.adopt_rule(p_section_id uuid, p_base_version bigint, p_idempotency_key text, p_reason text)', 'volatility', 'v',
    'ingress_key', 'db-function-acl:ops.adopt_rule(p_section_id uuid, p_base_version bigint, p_idempotency_key text, p_reason text):carr_authority:execute',
    'ingress_kind', 'db_function_acl', 'function_kind', 'f', 'security_definer', true)));
  execute format(
    'CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT ' || quote_literal(contract::text) || '::jsonb');
end $contract$;
