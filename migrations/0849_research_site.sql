-- rollback: forward-only — take a source out with remove-research-site; nothing outside the three research-site verbs reads this table, so it can be left in place unused.
-- lock-review: creates one new table and its indexes, then seeds a handful of rows; no existing table is altered or locked beyond the actor FK share lock.
-- 0849: the research-site index — useful sources research checks first.
--
-- Joe, 2026-10-07: "the database of research sites is not really meant to
-- restrict you, its meant to point you at sites to go to for research, but you
-- can go to any site not already in the database. in fact, you should use the
-- full internet and if you find a new resource you would add it to the database
-- for future reference. its more of an index of useful sites."
--
-- An INDEX, not a permission list: the egress guard never reads it. Written
-- through add-research-site and remove-research-site, both open to every caller
-- (Joe does not want to be involved in adding sites); read through
-- list-research-sites. Removal is soft so the history survives.

create table public.research_site (
  id uuid primary key default gen_random_uuid(),
  host text not null check (
    length(host) <= 253
    and host = lower(host)
    and host ~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'
    and host !~ '^[0-9.]+$'),
  url text check (url is null or (length(url) <= 2000 and url ~ '^https?://')),
  topics text[] not null check (cardinality(topics) between 1 and 12),
  note text check (note is null or length(note) <= 2000),
  added_by_actor_id uuid not null references public.actor(id),
  added_at timestamptz not null default now(),
  removed_at timestamptz,
  removed_by_actor_id uuid references public.actor(id),
  removal_reason text check (removal_reason is null or length(removal_reason) <= 1000),
  check ((removed_at is null) = (removed_by_actor_id is null)),
  check ((removed_at is null) = (removal_reason is null))
);

-- One ACTIVE row per host; removed rows keep their history beside a re-add.
create unique index research_site_active_host_uidx
  on public.research_site (host) where removed_at is null;
create index research_site_topics_idx on public.research_site using gin (topics);

revoke all on public.research_site from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant select on public.research_site to carr_reader;
grant select, insert on public.research_site to carr_writer;
grant update (removed_at, removed_by_actor_id, removal_reason) on public.research_site to carr_writer;

create trigger scac_reference_monitor_guard_row before insert or update or delete
on public.research_site for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on public.research_site for each statement execute function ops.scac_reference_monitor_guard();

-- Seed: the four hosts the weekly social batch already cites (PR #1631), plus
-- the primary sources the research task files name for provider and market work.
insert into public.research_site (host, url, topics, note, added_by_actor_id)
select v.host, v.url, v.topics, v.note, a.id
  from (values
    ('www.cbre.com', 'https://www.cbre.com/insights', array['cre-market','medical-office','research-reports'],
     'CBRE market research reports cited by the weekly social batch'),
    ('carr.us', 'https://carr.us/', array['carr','healthcare-cre'],
     'CARR''s own site, cited as a primary source by the weekly social batch'),
    ('aavmc.org', 'https://aavmc.org/', array['veterinary','workforce'],
     'AAVMC veterinary-profession data cited by the weekly social batch'),
    ('help.blotato.com', 'https://help.blotato.com/', array['social-tooling'],
     'Blotato documentation for the social batch''s posting connector'),
    ('npiregistry.cms.hhs.gov', 'https://npiregistry.cms.hhs.gov/', array['npi','provider-verification'],
     'NPPES NPI registry: verify a clinician or practice NPI, taxonomy and practice address'),
    ('download.cms.gov', 'https://download.cms.gov/nppes/NPI_Files.html', array['npi','new-providers'],
     'NPPES full and weekly NPI files used by the new-provider sweep'),
    ('search.sunbiz.org', 'https://search.sunbiz.org/', array['entity-filings','florida'],
     'Florida Division of Corporations: practice entity filings and officers'),
    ('www.healthgrades.com', 'https://www.healthgrades.com/', array['provider-directory','provider-verification'],
     'Provider directory: practitioners, specialties and locations at a practice'),
    ('data.census.gov', 'https://data.census.gov/', array['demographics','market-analysis'],
     'Census population, age and income tables for a trade area'),
    ('www.bls.gov', 'https://www.bls.gov/', array['labor-market','economics','healthcare-employment'],
     'BLS employment, wage and price data, including healthcare occupations')
  ) as v(host, url, topics, note)
  join public.actor a on a.slug = 'joe';

do $$
begin
  if (select count(*) from public.research_site where removed_at is null) <> 10 then
    raise exception '0849: research_site seed did not land every row (actor joe missing?)';
  end if;
end $$;
