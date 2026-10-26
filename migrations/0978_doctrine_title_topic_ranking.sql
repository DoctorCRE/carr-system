-- rollback: restore search_doctrine_situations from 0955 and search_doctrine_semantic from 0958 in a new migration.
-- expand-contract: prioritize complete section-title topics through the existing search interface.
-- lock-review: replace stable functions without changing signatures, owners, grants, or stored records.

CREATE OR REPLACE FUNCTION public.search_doctrine_situations(p_query text, p_actor_id uuid, p_content_classes text[] DEFAULT NULL::text[], p_limit integer DEFAULT 20, p_policy_id text DEFAULT NULL::text, p_allow_fallback boolean DEFAULT false)
 RETURNS TABLE(section_id uuid, section_key text, title text, doc_slug text, content_class text, rank double precision, snippet text, lexical_score double precision, concept_score double precision, final_score double precision, provenance jsonb)
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
with
  normalized as materialized (
    select public.normalize_retrieval_phrase(p_query) as q
  ),
  policy as materialized (
    select * from public.retrieval_ranking_policy
     where policy_id = coalesce(
       p_policy_id,
       (select policy_id from public.retrieval_ranking_policy where is_default and status='active'))
       and status in ('candidate','active')
  ),
  query_terms as materialized (
    select websearch_to_tsquery('english', q) as tsq from normalized
  ),
  current_set as materialized (
    select c.*, n.q ~ '^[[:alpha:][:space:]]+$'
       and n.q !~ '(^|[[:space:]])or([[:space:]]|$)'
       and numnode(plainto_tsquery('english', n.q)) > 0
       and strip(to_tsvector('english',coalesce(c.section_title,''))) = strip(to_tsvector('english', n.q)) as title_topic_match
      from public.current_retrievable_doctrine(p_actor_id, p_content_classes) c
      cross join normalized n
  ),
  lexical_raw as (
    select c.section_id,
           ts_rank_cd(
             setweight(c.section_title_vector, 'A') ||
             setweight(c.document_title_vector, 'A') ||
             setweight(c.body_search_vector, 'B'), q.tsq) as raw_score,
           ts_headline('english', c.plain_text, q.tsq, 'MaxWords=25, MinWords=10') as snippet
      from current_set c cross join query_terms q
     where c.section_title_vector @@ q.tsq
        or c.document_title_vector @@ q.tsq
        or c.body_search_vector @@ q.tsq
  ),
  phrase_match as (
    select p.id as phrase_id, p.concept_id, p.weight as phrase_weight,
           case p.match_mode
             when 'exact' then case when p.normalized_phrase = n.q then 1.0 else 0.0 end
             when 'fts' then case
               when to_tsvector('english', p.normalized_phrase) @@ websearch_to_tsquery('english', n.q)
               then least(1.0, ts_rank_cd(to_tsvector('english', p.normalized_phrase),
                                         websearch_to_tsquery('english', n.q))::double precision)
               when n.q !~ '"'
                and websearch_to_tsquery('english', n.q)::text !~ '!'
                and n.q !~ '(^|[[:space:]])or([[:space:]]|$)'
                and to_tsvector('english', n.q) @@ plainto_tsquery('english', p.normalized_phrase)
               then least(1.0, ts_rank_cd(to_tsvector('english', n.q),
                                         plainto_tsquery('english', p.normalized_phrase))::double precision)
               else 0.0 end
             when 'trgm' then case when public.similarity(p.normalized_phrase, n.q) >= p.min_similarity
               then public.similarity(p.normalized_phrase, n.q) else 0.0 end
           end::double precision as phrase_strength
      from public.retrieval_phrase p cross join normalized n
     where p.status = 'approved'
  ),
  concept_evidence as (
    select m.section_id, pm.phrase_id, pm.concept_id, m.id as mapping_id,
           pm.phrase_strength, pm.phrase_weight::double precision,
           m.weight::double precision as mapping_weight,
           (pm.phrase_strength * pm.phrase_weight * m.weight)::double precision as contribution
      from phrase_match pm
      join public.retrieval_concept c on c.id = pm.concept_id and c.status = 'approved'
      join public.doctrine_concept_mapping m on m.concept_id = c.id and m.status = 'approved'
      join current_set visible on visible.section_id = m.section_id
     where pm.phrase_strength > 0
  ),
  concept_by_section as (
    select section_id, max(contribution)::double precision as concept_score,
           array_agg(distinct phrase_id order by phrase_id) as phrase_ids,
           array_agg(distinct concept_id order by concept_id) as concept_ids,
           array_agg(distinct mapping_id order by mapping_id) as mapping_ids
      from concept_evidence group by section_id
  ),
  unioned as materialized (
    select c.*, least(1.0, coalesce(l.raw_score, 0))::double precision as lexical_score,
           least(1.0, coalesce(ce.concept_score, 0))::double precision as concept_score,
           l.snippet, coalesce(ce.phrase_ids, '{}') as phrase_ids,
           coalesce(ce.concept_ids, '{}') as concept_ids,
           coalesce(ce.mapping_ids, '{}') as mapping_ids,
           false as used_fallback
      from current_set c
      left join lexical_raw l on l.section_id = c.section_id
      left join concept_by_section ce on ce.section_id = c.section_id
     where l.section_id is not null or ce.section_id is not null
  ),
  fallback_terms as materialized (
    select websearch_to_tsquery('english', regexp_replace(q, ' ', ' OR ', 'g')) as tsq
      from normalized
  ),
  fallback_raw as (
    select c.section_id,
           ts_rank_cd(
             setweight(c.section_title_vector, 'A') ||
             setweight(c.document_title_vector, 'A') ||
             setweight(c.body_search_vector, 'B'), q.tsq) as raw_score,
           ts_headline('english', c.plain_text, q.tsq, 'MaxWords=25, MinWords=10') as snippet
      from current_set c cross join fallback_terms q
     where p_allow_fallback
       and not exists (select 1 from unioned)
       and (c.section_title_vector @@ q.tsq
         or c.document_title_vector @@ q.tsq
         or c.body_search_vector @@ q.tsq)
  ),
  fallback_unioned as (
    select c.*, least(1.0, coalesce(f.raw_score, 0))::double precision as lexical_score,
           0::double precision as concept_score,
           f.snippet, '{}'::uuid[] as phrase_ids,
           '{}'::uuid[] as concept_ids,
           '{}'::uuid[] as mapping_ids,
           true as used_fallback
      from current_set c
      join fallback_raw f on f.section_id = c.section_id
  ),
  combined as (
    select * from unioned
    union all
    select * from fallback_unioned
  ),
  maxima as (
    select greatest(max(lexical_score), 0) as max_lexical,
           greatest(max(concept_score), 0) as max_concept from combined
  ),
  scored as (
    select u.*,
           case p.formula
             when 'weighted_sum' then
               ((p.config->>'lexical_weight')::double precision * u.lexical_score +
                (p.config->>'concept_weight')::double precision *
                  case when coalesce((p.config->>'concept_enabled')::boolean,true)
                       then u.concept_score else 0 end)
             when 'coequal_normalized' then
               (case when x.max_lexical > 0 then u.lexical_score / x.max_lexical else 0 end) +
               (case when coalesce((p.config->>'concept_enabled')::boolean,true) and x.max_concept > 0
                     then u.concept_score / x.max_concept else 0 end) +
               (case when coalesce((p.config->>'concept_enabled')::boolean,true)
                           and u.lexical_score > 0 and u.concept_score > 0
                     then (p.config->>'dual_evidence_bonus')::double precision else 0 end)
           end::double precision as final_score,
           p.policy_id, p.version as policy_version
      from combined u cross join maxima x cross join policy p
  ),
  limited as materialized (
    select * from scored
     where final_score > 0
     order by title_topic_match desc, final_score desc, concept_score desc, lexical_score desc, section_key asc, section_id asc
     limit greatest(1, least(coalesce(p_limit, 20), 100))
  )
select l.section_id, l.section_key, l.section_title, l.doc_slug, l.content_class,
       l.final_score as rank,
       coalesce(l.snippet, left(l.plain_text, 240)) as snippet,
       l.lexical_score, l.concept_score, l.final_score,
       jsonb_build_object(
         'title_topic_match', l.title_topic_match,
         'complete', true, 'policy_id', l.policy_id, 'policy_version', l.policy_version,
         'lexical_score', l.lexical_score, 'concept_score', l.concept_score,
         'final_score', l.final_score, 'phrase_ids', to_jsonb(l.phrase_ids),
         'concept_ids', to_jsonb(l.concept_ids), 'mapping_ids', to_jsonb(l.mapping_ids))
       || case when l.used_fallback then jsonb_build_object('fallback', true)
               else '{}'::jsonb end
  from limited l
 order by l.title_topic_match desc, l.final_score desc, l.concept_score desc, l.lexical_score desc, l.section_key asc, l.section_id asc
$function$;


create or replace function public.search_doctrine_semantic(
  p_query text, p_actor_id uuid, p_content_classes text[], p_limit integer,
  p_policy_id text, p_embeddings public.vector[]
) returns jsonb
language sql stable security definer
set search_path = pg_catalog, pg_temp
as $function$
with policy as materialized (
  select policy_id,version from public.retrieval_ranking_policy
   where policy_id=coalesce(p_policy_id,
     (select policy_id from public.retrieval_ranking_policy where is_default and status='active'))
     and status in ('candidate','active')
),
visible as materialized (
  select c.*, s.current_revision_id as revision_id,
         p_query ~ '^[[:alpha:][:space:]]+$'
           and lower(p_query) !~ '(^|[[:space:]])or([[:space:]]|$)'
           and pg_catalog.numnode(pg_catalog.plainto_tsquery('english',p_query)) > 0
           and pg_catalog.strip(pg_catalog.to_tsvector('english',coalesce(c.section_title,''))) = pg_catalog.strip(pg_catalog.to_tsvector('english',p_query)) as title_topic_match
    from public.current_retrievable_doctrine(p_actor_id,p_content_classes) c
    join public.doctrine_section s on s.id=c.section_id
),
coverage as materialized (
  select count(distinct v.section_id)::integer as visible,
         count(distinct e.revision_id)::integer as embedded
    from visible v
    left join public.doctrine_revision r on r.id=v.revision_id and r.section_id=v.section_id
    left join public.doctrine_embedding e on e.revision_id=r.id
      and e.model='@cf/baai/bge-small-en-v1.5:cls:chunks-v1'
),
lexical as materialized (
  select l.*, row_number() over (
    order by (l.provenance->>'title_topic_match')::boolean desc,l.final_score desc,l.concept_score desc,l.lexical_score desc,l.section_key,l.section_id) as position
    from public.search_doctrine_situations(p_query,p_actor_id,p_content_classes,100,p_policy_id,true) l
),
chunk_scores as materialized (
  select v.section_id,
         max(1 - (e.embedding OPERATOR(public.<=>) q.embedding))::double precision as similarity
    from visible v
    join public.doctrine_revision r on r.id=v.revision_id and r.section_id=v.section_id
    join public.doctrine_embedding e on e.revision_id=r.id
      and e.model='@cf/baai/bge-small-en-v1.5:cls:chunks-v1'
    cross join unnest(p_embeddings) as q(embedding)
   group by v.section_id
),
nearest as materialized (
  select v.*,c.similarity from visible v join chunk_scores c on c.section_id=v.section_id
   order by c.similarity desc,v.section_id limit 100
),
semantic as (
  select n.*, row_number() over (order by similarity desc,section_id) as position
    from nearest n where similarity > 0
),
blended as (
  select v.section_id,v.section_key,v.section_title as title,v.doc_slug,v.content_class,v.title_topic_match,
         coalesce(l.snippet,left(v.plain_text,240)) as snippet,
         coalesce(l.lexical_score,0) as lexical_score,
         coalesce(l.concept_score,0) as concept_score,
         (30.5 * (coalesce(1.0/(60+l.position),0) + coalesce(1.0/(60+s.position),0)))::double precision as final_score,
         coalesce(l.provenance,'{}'::jsonb) || jsonb_build_object(
           'title_topic_match',v.title_topic_match,
           'complete',true,'policy_id',p.policy_id,'policy_version',p.version,
           'blend_id','rrf-60-v1','rrf_k',60,'rrf_scale',30.5,
           'lexical_position',l.position,'semantic_position',s.position,
           'lexical_final_score',l.final_score,
           'semantic_similarity',s.similarity,
           'embedding_model','@cf/baai/bge-small-en-v1.5:cls:chunks-v1') as provenance,
         coalesce(s.similarity,0) as similarity
    from visible v cross join policy p
    left join lexical l on l.section_id=v.section_id
    left join semantic s on s.section_id=v.section_id
   where l.section_id is not null or s.section_id is not null
),
limited as (
  select b.*,b.final_score as rank from blended b
   order by title_topic_match desc,final_score desc,similarity desc,concept_score desc,lexical_score desc,section_key,section_id
   limit greatest(1,least(coalesce(p_limit,20),100))
),
hits as (
  select coalesce(jsonb_agg((to_jsonb(l)-'similarity'-'title_topic_match') || jsonb_build_object(
    'provenance',l.provenance || jsonb_build_object('final_score',l.final_score))
    order by l.title_topic_match desc,l.final_score desc,l.similarity desc,l.concept_score desc,l.lexical_score desc,l.section_key,l.section_id),'[]'::jsonb) as value
  from limited l
)
select jsonb_build_object('visible',c.visible,'embedded',c.embedded,'hits',
  case when c.embedded=0 then
    (select coalesce(jsonb_agg(to_jsonb(l)),'[]'::jsonb)
       from public.search_doctrine_situations(p_query,p_actor_id,p_content_classes,p_limit,p_policy_id,true) l)
  else h.value end)
from coverage c cross join hits h
$function$;
