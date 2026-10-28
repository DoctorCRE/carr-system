-- rollback: restore search_doctrine_semantic from 0978 in a new migration (same signature, so callers and grants are unaffected).
-- expand-contract: expand — replaces one stable function body in place; no object is dropped and the signature, owner, grants and result shape are unchanged.
-- lock-review: create or replace function takes a brief catalog lock on that one function only; no table or index is touched.
-- Card: WR573.S2.J4 (database-audit P9 / F8): lets doctrine_embedding_cosine_idx serve semantic search.
-- Per query chunk, the 1000 nearest chunks come from an index-ordered scan (order by <=> limit 1000,
-- hnsw.ef_search raised to its maximum (1000) inside the call so the index can return that many), and only
-- then join to visible sections. Coverage counts use a per-section exists probe instead of reading every
-- chunk. The body is plpgsql only so the setting is applied before the scan; a function-level SET would
-- change proconfig, which ops.scac_current_grant_contract() pins to search_path alone.

create or replace function public.search_doctrine_semantic(
  p_query text, p_actor_id uuid, p_content_classes text[], p_limit integer,
  p_policy_id text, p_embeddings public.vector[]
) returns jsonb
language plpgsql stable security definer
set search_path = pg_catalog, pg_temp
as $function$
declare
  v_prev_ef text := pg_catalog.current_setting('hnsw.ef_search', true);
  v_result jsonb;
begin
  -- The index returns at most hnsw.ef_search rows per scan (default 40), so raise it for this
  -- call only. The setting is transaction-local and restored below.
  perform pg_catalog.set_config('hnsw.ef_search', '1000', true);
  v_result := (
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
  select count(*)::integer as visible,
         count(*) filter (where exists (
           select 1 from public.doctrine_revision r
             join public.doctrine_embedding e on e.revision_id=r.id
            where r.id=v.revision_id and r.section_id=v.section_id
              and e.model='@cf/baai/bge-small-en-v1.5:cls:chunks-v1')
         )::integer as embedded
    from visible v
),
lexical as materialized (
  select l.*, row_number() over (
    order by (l.provenance->>'title_topic_match')::boolean desc,l.final_score desc,l.concept_score desc,l.lexical_score desc,l.section_key,l.section_id) as position
    from public.search_doctrine_situations(p_query,p_actor_id,p_content_classes,100,p_policy_id,true) l
),
chunk_scores as materialized (
  select v.section_id,
         max(1 - c.distance)::double precision as similarity
    from unnest(p_embeddings) as q(embedding)
    cross join lateral (
      select e.revision_id, e.embedding OPERATOR(public.<=>) q.embedding as distance
        from public.doctrine_embedding e
       where e.model='@cf/baai/bge-small-en-v1.5:cls:chunks-v1'
       order by e.embedding OPERATOR(public.<=>) q.embedding
       limit 1000
    ) c
    join public.doctrine_revision r on r.id=c.revision_id
    join visible v on v.revision_id=r.id and v.section_id=r.section_id
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
  );
  perform pg_catalog.set_config('hnsw.ef_search', coalesce(nullif(v_prev_ef, ''), '40'), true);
  return v_result;
end
$function$;
