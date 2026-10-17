-- rollback: drop function public.party_reference_counts(uuid, uuid); drop view public.v_party_identity_correction; nothing else depends on them and no row is written.
-- correct-party-identity (rule 578fdd91) writes a party's name, org and state
-- from a verified finding, and each changed field records its prior value on an
-- event. This view is the read surface for those corrections: what changed,
-- from what, to what, on whose authority and from which source, so a wrong
-- correction can be found and undone from the record. It reads the event table
-- the verb already writes; it adds no table and no write path.
create view public.v_party_identity_correction as
select e.id as event_id,
       e.mutation_order,
       e.occurred_at,
       e.subject_id as party_id,
       p.ref as party_ref,
       p.kind as party_kind,
       p.name as party_name_now,
       e.field,
       e.old_value -> e.field as prior_value,
       e.new_value -> e.field as corrected_value,
       e.new_value ->> 'mode' as org_mode,
       nullif(regexp_replace(coalesce(e.agent_rationale, ''), '^source: ', ''), '') as source,
       a.slug as corrected_by,
       e.idempotency_key
  from public.event e
  join public.party p on p.id = e.subject_id
  join public.actor a on a.id = e.actor_id
 where e.verb = 'correct-party-identity'
   and e.subject_type = 'party';

comment on view public.v_party_identity_correction is
  'Every correct-party-identity change with its prior value and source, newest by mutation_order; the undo surface for identity corrections.';

revoke all on public.v_party_identity_correction from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant select on public.v_party_identity_correction to carr_reader, carr_writer;

-- WHO ELSE REFERS TO THIS PARTY ROW (rule 8cddc6ad). correct-party-identity must
-- never rename a shared org row, and "shared" means any foreign key into it, not
-- only other people's party.org_id: a deal participant, a client or vendor row,
-- a party link or a building owner on the org row would all be re-labelled by a
-- rename. The verb's role cannot read most of those tables, and a hand-kept list
-- drifts as tables are added, so this reads the live FK catalogue and counts each
-- single-column reference. party.org_id counts live parties only and skips
-- p_exclude (the party being corrected). History is skipped: merged tombstones
-- (party.merged_into) and org_merge_log rows describe the past, not attachments.
-- Read-only; returns one row per referencing column with a non-zero count.
create function public.party_reference_counts(p_party uuid, p_exclude uuid)
returns table(source text, n bigint)
language plpgsql stable security definer set search_path = pg_catalog, public, pg_temp as $fn$
declare r record; cnt bigint;
begin
  for r in
    select c.conrelid::regclass::text as tbl, a.attname::text as col
      from pg_constraint c
      join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
     where c.contype = 'f' and c.confrelid = 'public.party'::regclass and cardinality(c.conkey) = 1
     order by 1, 2
  loop
    if (r.tbl = 'party' and r.col = 'merged_into') or r.tbl in ('org_merge_log', 'public.org_merge_log') then
      continue;
    end if;
    if r.tbl = 'party' then
      execute format('select count(*) from public.party where %I = $1 and id is distinct from $2
                         and merged_into is null and deleted_at is null', r.col)
         into cnt using p_party, p_exclude;
    else
      execute format('select count(*) from %s where %I = $1', r.tbl, r.col) into cnt using p_party;
    end if;
    if cnt > 0 then source := r.tbl || '.' || r.col; n := cnt; return next; end if;
  end loop;
end $fn$;

comment on function public.party_reference_counts(uuid, uuid) is
  'Every foreign-key reference into one party row, counted per column (live parties only for party.org_id, excluding p_exclude; merge history skipped). correct-party-identity reads it to decide whether an org row is shared before any rename (rule 8cddc6ad).';

revoke all on function public.party_reference_counts(uuid, uuid) from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant execute on function public.party_reference_counts(uuid, uuid) to carr_writer;
