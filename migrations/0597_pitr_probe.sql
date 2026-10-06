-- 0597: ops.pitr_probe — the rows bin/pitr-restore-proof.sh writes so it can
-- PROVE how recent a point production can be restored to (V5-F08, RPO cell).
--
-- WHY A DEDICATED TABLE. The provider exposes history retention but no "latest
-- restorable point", so the proof creates a disposable branch of production at
-- an instant T and checks what is on it. Reading a business row there is weak
-- evidence: business rows are rewritten, and nothing says one was committed at
-- a known instant. So the proof writes its OWN rows here: a POSITIVE probe at
-- least 60 s before T that must be on the branch, and a NEGATIVE probe at least
-- 5 s after T that must NOT be — the negative control that proves the branch
-- really is production at T rather than at some later point. No business table
-- is written by the proof; these rows are the only thing it writes.
--
-- THE DOOR. The writer is ops.write_pitr_probe(role). The id, the nonce and the
-- write instant are made HERE, server-side, by a BEFORE INSERT trigger on the
-- table itself (gen_random_uuid, gen_random_bytes, clock_timestamp), so no
-- insert, through the function or straight into the table, can choose a nonce
-- it already knows is on some branch or backdate a write. Rows are
-- append-only: update, delete and truncate refuse.
--
-- NO RUNTIME ROLE GETS ANYTHING. The function is NOT security definer and is
-- granted to no runtime role; the table is granted to none either. The only
-- caller is the attended proof, on the owner connection it already holds to
-- read production and create the branch (the weekly restore rehearsal's
-- connection). A grant to carr_jobs would have widened a standing runtime
-- capability for a once-in-a-while attended check, and would have moved the
-- sealed capability census for nothing a runtime role needs. carr_backup reads
-- the table through the ops default privileges like every ops table, so probe
-- rows are in the nightly dump.


create table ops.pitr_probe (
  id          uuid        primary key default gen_random_uuid(),
  nonce       text        not null unique check (nonce ~ '^[0-9a-f]{32}$'),
  role        text        not null check (role in ('positive', 'negative')),
  written_at  timestamptz not null default clock_timestamp()
);

comment on table ops.pitr_probe is
  'Append-only probe rows written by bin/pitr-restore-proof.sh through ops.write_pitr_probe; never business data.';

create function ops.pitr_probe_rows_immutable() returns trigger
language plpgsql set search_path = pg_catalog, ops, public
as $$
begin
  raise exception '% is append-only: % refused', tg_table_name, tg_op;
end $$;
revoke all on function ops.pitr_probe_rows_immutable() from public;

create trigger pitr_probe_append_only before update or delete on ops.pitr_probe
  for each row execute function ops.pitr_probe_rows_immutable();
create trigger pitr_probe_no_truncate before truncate on ops.pitr_probe
  for each statement execute function ops.pitr_probe_rows_immutable();

-- EVERY insert, not only the function's, gets a server-made id, nonce and write
-- instant (review G5): an owner session inserting into the table directly can
-- neither backdate a row nor pick a nonce it knows is on some branch. Whatever
-- the caller supplied for those three columns is overwritten, never kept.
create function ops.pitr_probe_server_stamp() returns trigger
language plpgsql set search_path = pg_catalog, ops, public
as $$
begin
  new.id := gen_random_uuid();
  new.nonce := encode(public.gen_random_bytes(16), 'hex');
  new.written_at := clock_timestamp();
  return new;
end $$;
revoke all on function ops.pitr_probe_server_stamp() from public;

create trigger pitr_probe_server_stamp before insert on ops.pitr_probe
  for each row execute function ops.pitr_probe_server_stamp();

create function ops.write_pitr_probe(p_role text)
returns table (id uuid, nonce text, role text, written_at timestamptz)
language plpgsql security invoker set search_path = pg_catalog, ops, public
as $$
begin
  if p_role is null or p_role not in ('positive', 'negative') then
    raise exception 'write_pitr_probe: role must be positive or negative';
  end if;
  return query
    insert into ops.pitr_probe as p (role)
    values (p_role)
    returning p.id, p.nonce, p.role, p.written_at;
end $$;

revoke all on ops.pitr_probe from public;
revoke all on function ops.write_pitr_probe(text)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

