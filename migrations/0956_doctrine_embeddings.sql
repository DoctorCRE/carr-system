create extension if not exists vector with schema public;

create table public.doctrine_embedding (
  revision_id uuid primary key references public.doctrine_revision(id),
  model text not null check (model = '@cf/baai/bge-small-en-v1.5:cls'),
  embedding public.vector(384) not null check (public.vector_norm(embedding) > 0),
  embedded_at timestamptz not null default now()
);
create index doctrine_embedding_cosine_idx on public.doctrine_embedding
  using hnsw (embedding public.vector_cosine_ops);
revoke all on public.doctrine_embedding from public;
grant select, insert on public.doctrine_embedding to carr_writer;

create or replace function public.current_retrievable_doctrine(
  p_actor_id uuid, p_content_classes text[] default null
) returns table (
  section_id uuid, section_key text, section_title text, doc_slug text,
  doc_title text, content_class text, plain_text text,
  body_search_vector tsvector, section_title_vector tsvector,
  document_title_vector tsvector
) language sql stable security invoker set search_path = '' as $function$
  select s.id,s.section_key,s.title,d.slug,d.title,d.content_class,
         r.plain_text,r.search_vector,s.title_search_vector,d.title_search_vector
    from public.doctrine_section s
    join public.doctrine_document d on d.id=s.document_id
    join public.doctrine_revision r on r.id=s.current_revision_id
   where s.status='active'
     and (d.visibility <> 'personal' or d.owner_actor_id=p_actor_id)
     and (p_content_classes is null or d.content_class=any(p_content_classes))
$function$;

create function public.search_doctrine_semantic(
  p_query text, p_actor_id uuid, p_content_classes text[], p_limit integer,
  p_policy_id text, p_embedding public.vector
) returns jsonb
language sql stable security definer
set search_path = ''
as $function$
with policy as materialized (
  select policy_id,version from public.retrieval_ranking_policy
   where policy_id=coalesce(p_policy_id,
     (select policy_id from public.retrieval_ranking_policy where is_default and status='active'))
     and status in ('candidate','active')
),
visible as materialized (
  select c.*, s.current_revision_id as revision_id
    from public.current_retrievable_doctrine(p_actor_id,p_content_classes) c
    join public.doctrine_section s on s.id=c.section_id
),
coverage as materialized (
  select count(*)::integer as visible,
         count(e.revision_id)::integer as embedded
    from visible v
    left join public.doctrine_revision r on r.id=v.revision_id and r.section_id=v.section_id
    left join public.doctrine_embedding e on e.revision_id=r.id
      and e.model='@cf/baai/bge-small-en-v1.5:cls'
),
lexical as materialized (
  select l.*, row_number() over (
    order by l.final_score desc,l.concept_score desc,l.lexical_score desc,l.section_key,l.section_id) as position
    from public.search_doctrine_situations(p_query,p_actor_id,p_content_classes,100,p_policy_id,true) l
),
nearest as materialized (
  select v.*, (1 - (e.embedding OPERATOR(public.<=>) p_embedding))::double precision as similarity
    from visible v
    join public.doctrine_revision r on r.id=v.revision_id and r.section_id=v.section_id
    join public.doctrine_embedding e on e.revision_id=r.id
      and e.model='@cf/baai/bge-small-en-v1.5:cls'
   order by e.embedding OPERATOR(public.<=>) p_embedding,v.section_id
   limit 100
),
semantic as (
  select n.*, row_number() over (order by similarity desc,section_id) as position
    from nearest n where similarity > 0
),
blended as (
  select v.section_id,v.section_key,v.section_title as title,v.doc_slug,v.content_class,
         coalesce(l.snippet,left(v.plain_text,240)) as snippet,
         coalesce(l.lexical_score,0) as lexical_score,
         coalesce(l.concept_score,0) as concept_score,
         (30.5 * (coalesce(1.0/(60+l.position),0) + coalesce(1.0/(60+s.position),0)))::double precision as final_score,
         coalesce(l.provenance,'{}'::jsonb) || jsonb_build_object(
           'complete',true,'policy_id',p.policy_id,'policy_version',p.version,
           'blend_id','rrf-60-v1','rrf_k',60,'rrf_scale',30.5,
           'lexical_position',l.position,'semantic_position',s.position,
           'lexical_final_score',l.final_score,
           'semantic_similarity',s.similarity,
           'embedding_model','@cf/baai/bge-small-en-v1.5:cls') as provenance,
         coalesce(s.similarity,0) as similarity
    from visible v cross join policy p
    left join lexical l on l.section_id=v.section_id
    left join semantic s on s.section_id=v.section_id
   where l.section_id is not null or s.section_id is not null
),
limited as (
  select b.*,b.final_score as rank from blended b
   order by final_score desc,similarity desc,concept_score desc,lexical_score desc,section_key,section_id
   limit greatest(1,least(coalesce(p_limit,20),100))
),
hits as (
  select coalesce(jsonb_agg((to_jsonb(l)-'similarity') || jsonb_build_object(
    'provenance',l.provenance || jsonb_build_object('final_score',l.final_score))
    order by l.final_score desc,l.similarity desc,l.concept_score desc,l.lexical_score desc,l.section_key,l.section_id),'[]'::jsonb) as value
  from limited l
)
select jsonb_build_object('visible',c.visible,'embedded',c.embedded,'hits',
  case when c.embedded=0 then
    (select coalesce(jsonb_agg(to_jsonb(l)),'[]'::jsonb)
       from public.search_doctrine_situations(p_query,p_actor_id,p_content_classes,p_limit,p_policy_id,true) l)
  else h.value end)
from coverage c cross join hits h
$function$;
revoke all on function public.search_doctrine_semantic(text,uuid,text[],integer,text,public.vector) from public;
grant execute on function public.search_doctrine_semantic(text,uuid,text[],integer,text,public.vector) to carr_reader,carr_writer;
