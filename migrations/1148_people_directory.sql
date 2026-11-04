-- rollback: retire directory reads, then remove ops.people_directory_actors_v1 and public.party_directory_name_idx.
-- expand-contract: expand; read-only directory name index.
-- lock-review: additive partial index; statement timeout bounds build time.
set local lock_timeout='5s';
set local statement_timeout='30s';
create index party_directory_name_idx on public.party(lower(name) collate "C",id)
  where merged_into is null and deleted_at is null;
create view ops.people_directory_actors_v1 as
  select id,slug,display_name,kind from public.actor where active;
revoke all on ops.people_directory_actors_v1 from public;
grant select on ops.people_directory_actors_v1 to carr_reader;
