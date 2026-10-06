-- 0702_amend_closed_loop.sql — an append-only door to correct a CLOSED loop's
-- outcome, without rewriting history.
--
-- THE DEFECT. close-loop refuses with loop_not_open on anything already
-- closed, on purpose (rule c53beeaa-adjacent: a closed loop is history, open a
-- new one rather than editing the record of what happened). But "the outcome
-- text itself was typed wrong" is not a request to reopen work — it is a
-- request to CORRECT THE RECORD OF WHAT WAS SAID, and until now nothing could
-- do that. Loop c7265238-effe-4166-bc9a-eccc5f389763 was closed with outcome
-- "x" by mistake (defect a2c04ffa-92d0-4428-b175-32fa3cfb0802) and no verb
-- could fix it — the only paths were a raw UPDATE (exactly what the record
-- layer exists to prevent) or living with a nonsense outcome forever.
--
-- WHY A NEW TABLE AND NOT AN UPDATE-IN-PLACE ON loop_item. Rule: append-only
-- correction, never overwrite history. loop_item.close_outcome/outcome stay
-- the CURRENT projection (same pattern close-loop itself already uses: the
-- row's columns are current state, the event log is history) — but a
-- correction needs its own durable, queryable trail of every prior outcome, the
-- new one, WHY, and WHO, independent of the generic event log's json blob.
-- loop_amendment is that trail: one row per amend-closed-loop call, appended,
-- never updated, never deleted.
--
-- kind-agnostic and scoped to loop_item(id) with ON DELETE RESTRICT — an
-- amendment record must never survive the loop it corrects being removed,
-- and loop_item rows are never deleted in the first place (closing moves,
-- never deletes).

create table public.loop_amendment (
  id uuid primary key default gen_random_uuid(),
  loop_id uuid not null references public.loop_item(id) on delete restrict,
  prior_outcome text not null,
  new_outcome text not null,
  prior_resolution text not null,
  new_resolution text not null,
  reason text not null,
  actor_id uuid not null references public.actor(id),
  idempotency_key text,
  created_at timestamp with time zone not null default now(),
  constraint loop_amendment_new_outcome_meaningful check (length(btrim(new_outcome)) >= 10),
  constraint loop_amendment_reason_present check (length(btrim(reason)) >= 1),
  constraint loop_amendment_resolution_known
    check (prior_resolution = any (array['done','dropped'])
       and new_resolution = any (array['done','dropped']))
);

comment on table public.loop_amendment is
  'Append-only correction trail for a CLOSED loop''s outcome (amend-closed-loop, defect a2c04ffa). One row per correction: the prior outcome, the new one, why, and the server-derived actor. Never updated, never deleted. loop_item.close_outcome/outcome is the current projection and is set to the latest amendment''s new_outcome in the same transaction; this table is what makes that projection auditable rather than a silent overwrite.';
comment on column public.loop_amendment.prior_outcome is 'What close_outcome (== outcome) read on loop_item immediately before this amendment.';
comment on column public.loop_amendment.new_outcome is 'The corrected outcome text. Refused under ~10 characters by the verb — a placeholder correction is not a correction.';
comment on column public.loop_amendment.reason is 'REQUIRED: why the recorded outcome is being corrected. Never inferred, never defaulted.';
comment on column public.loop_amendment.actor_id is 'Server-derived from the authenticated actor, exactly like every other write verb. Never a caller-supplied field — there is no actor input on amend-closed-loop''s schema for a caller to supply.';

create index loop_amendment_loop_id_idx on public.loop_amendment (loop_id, created_at);

-- Append-only enforcement, the 0511:254-265 / 0519 ops.cost_ledger_rows_
-- immutable idiom: the grant below already withholds UPDATE/DELETE from
-- carr_writer, but a grant is a door that can be reopened by a later
-- migration without anyone noticing this table's own rule. The trigger makes
-- the rule a property of the TABLE, independent of whatever a future grant
-- says.
create or replace function public.loop_amendment_rows_immutable()
returns trigger language plpgsql as $$ begin
  raise exception 'loop_amendment rows are append-only — a correction is a new row, never a rewrite of one';
end $$;

create trigger loop_amendment_immutable before update or delete
on public.loop_amendment for each row execute function public.loop_amendment_rows_immutable();
create trigger loop_amendment_no_truncate before truncate
on public.loop_amendment for each statement execute function public.loop_amendment_rows_immutable();

-- carr_writer gets SELECT + INSERT and nothing else — never UPDATE, never
-- DELETE: this table is append-only by grant, not merely by convention, the
-- same discipline 0024 applies to loop_item's own DELETE (never granted,
-- "a loop is closed, never erased"). carr_reader gets NO base-table grant —
-- 0024's own guard raises if carr_reader ever gets one ("views-only is the
-- leak guard"); read-loop returns amendment history through the read door
-- instead (see tools.js), never through a grant on this table.
grant select, insert on public.loop_amendment to carr_writer;

-- READ DOOR, NOT A GRANT. carr_reader gets no base-table access to
-- loop_amendment (see the grant comment below); read-loop reaches the
-- amendment trail through this SECURITY DEFINER function instead, the same
-- shape as search_doctrine_situations (0223) and the program6 sourced-read
-- functions: EXECUTE is grantable to a role with no SELECT on the table it
-- reads, because the function runs as its owner, not as the caller. Returns
-- oldest first — the order a correction history reads in.
create or replace function public.loop_amendment_history(p_loop_id uuid)
returns table (
  id uuid, prior_outcome text, new_outcome text,
  prior_resolution text, new_resolution text,
  reason text, actor text, created_at timestamptz
) language sql stable security definer set search_path = public, pg_temp as $$
  select a.id, a.prior_outcome, a.new_outcome, a.prior_resolution, a.new_resolution,
         a.reason, act.slug, a.created_at
    from public.loop_amendment a
    join public.actor act on act.id = a.actor_id
   where a.loop_id = p_loop_id
   order by a.created_at;
$$;

-- Default PostgreSQL behavior grants EXECUTE to PUBLIC on a newly created
-- function unless revoked; explicit revoke-then-grant, exactly the shape
-- retrieval_visibility_actor_id (0223) uses for its own definer function,
-- so the two roles named below are the WHOLE access list, not an addition
-- to an implicit public one.
revoke all on function public.loop_amendment_history(uuid) from public;
grant execute on function public.loop_amendment_history(uuid) to carr_reader, carr_writer;

-- The SIEP-18 reference monitor (0467) requires every relation carrying a
-- direct INSERT/UPDATE/DELETE/TRUNCATE grant to carr_writer/carr_jobs/
-- carr_authority to also carry its guard trigger, or ops.scac_reference_
-- monitor_state() reports guard_state=incomplete and monitor_state=
-- unavailable. loop_amendment is the first table this PR grants directly, so
-- it needs both guard triggers exactly like every other direct-grant table
-- (see ops.v5_a05_cadence_receipt in migration 0617 for the same pair).
create trigger scac_reference_monitor_guard_row before insert or update or delete
on public.loop_amendment for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on public.loop_amendment for each statement execute function ops.scac_reference_monitor_guard();
