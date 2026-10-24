-- rollback: restore public.search_doctrine_situations from 0856 in a new migration.
-- expand-contract: extend approved FTS phrase containment without changing stored curation or the search interface.
-- lock-review: replace one stable SQL function; retain its signature, owner, grants, and visibility joins.
-- Approved situation phrases can occur inside a longer natural question.
-- Existing query-subset matches retain their score. The additional containment
-- direction is disabled for websearch operators, whose exclusions and quoted
-- phrases must retain the original query semantics. No curation is changed.


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
    select * from public.current_retrievable_doctrine(p_actor_id, p_content_classes)
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
     order by final_score desc, concept_score desc, lexical_score desc, section_key asc
     limit greatest(1, least(coalesce(p_limit, 20), 100))
  )
select l.section_id, l.section_key, l.section_title, l.doc_slug, l.content_class,
       l.final_score as rank,
       coalesce(l.snippet, left(l.plain_text, 240)) as snippet,
       l.lexical_score, l.concept_score, l.final_score,
       jsonb_build_object(
         'complete', true, 'policy_id', l.policy_id, 'policy_version', l.policy_version,
         'lexical_score', l.lexical_score, 'concept_score', l.concept_score,
         'final_score', l.final_score, 'phrase_ids', to_jsonb(l.phrase_ids),
         'concept_ids', to_jsonb(l.concept_ids), 'mapping_ids', to_jsonb(l.mapping_ids))
       || case when l.used_fallback then jsonb_build_object('fallback', true)
               else '{}'::jsonb end
  from limited l
 order by l.final_score desc, l.concept_score desc, l.lexical_score desc, l.section_key asc
$function$;

