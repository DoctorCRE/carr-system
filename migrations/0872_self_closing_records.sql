-- rollback: forward-only — every row these objects hold is a closing receipt another record now stands on; stop writing by revoking execute on the two ops completion functions and insert on public.defect_resolution, and leave the rows in place.
-- lock-review: two new tables (one with a unique index on its receipt reference), nine new functions and three trigger functions; v_defect is replaced with the same columns plus four appended ones; ops.sourced_work_request_outcome_feedback_acceptance_receipt gains two columns with a constant default (metadata-only) and one CHECK validated over its 22 rows under a brief ACCESS EXCLUSIVE lock.
-- 0872: self-closing records (Joe, 2026-10-12, decision cbe91287: "this system
-- needs to be able to function without me micromanaging it").
--
-- Three kinds of record piled up because only a person could close them:
--
--   1. A defect had no close at all. public.defect keeps accumulating on
--      purpose (the class counts are the point), so a close is NOT a delete
--      and NOT a state on the defect row: it is one append-only resolution
--      receipt naming the fixing commit, pull request or server receipt.
--      v_defect gains the receipt's columns; v_defect_class is unchanged, so
--      a closed defect still counts toward its class.
--
--   2. An incident closes on its own evidence through close-incident (that is
--      JavaScript over rows carr_writer can already read, so nothing here).
--      A linked defect's resolution receipt is one of the remediation receipts
--      it accepts, which is why this table is readable by carr_writer.
--
--   3. A sourced Work Request reaches its done state, which is an accepted
--      outcome-feedback receipt (V5-F09: current_sourced_work_requests stops
--      offering it as work), without a human accept-outcome-feedback call, when
--      every acceptance criterion carries a bound evidence receipt the database
--      itself can resolve. The receipt says so: acceptance_basis is
--      'completed_on_bound_evidence' and bound_receipt_refs lists the receipts.
--      The human acceptance path is untouched and still stamps
--      'human_acceptance'.
--
-- WHAT COUNTS AS A RECEIPT is decided here, once, by
-- ops.work_request_evidence_receipt_kind, and nowhere else: a succeeded ops.run,
-- a complete ops.deployment, a defect resolution, or a resolved incident. A
-- caller's own words never count, which is the difference between this and the
-- outcome proposal's free-text evidence_refs.
--
-- THE CHECKS ARE TECHNICAL, AND THEY ARE THE AUTHORITY (decision cbe91287 took
-- the human out of these closes, so what replaces the human has to hold):
--
--   * a receipt must be EVIDENCE FOR THIS REQUEST, not merely exist
--     (ops.work_request_evidence_receipt_mismatch): a run of a release recorded
--     for the request or naming it, a deployment shipping a release slice member
--     that names it, the close of a defect the request names, an incident linked
--     to it. One receipt binds to one criterion, ever.
--   * only a human actor or a sponsored agent binds evidence or completes a
--     request (ops.work_request_completion_actor), whatever role calls.
--   * a defect closes only on evidence the server already holds
--     (ops.defect_resolution_evidence_receipt): a merged commit or pull request
--     recorded as a release slice member, a succeeded run, a complete deployment
--     or a resolved incident. A bare sha or a PR number nobody recorded is
--     refused by the table itself.
--   * and only on evidence ABOUT THAT DEFECT
--     (ops.defect_resolution_evidence_mismatch): the slice member's merged
--     commit subject, the run's run_key, source_ref or evidence_ref, or the
--     deployment's shipped subject names the defect's id or a Work Request that
--     names it (the defect bridge's filing), and an incident is linked to the
--     defect or to such a Work Request. A real shipped commit about something
--     else closes nothing, so a close cannot launder unrelated work into Work
--     Request or incident evidence.
--
-- STATED LIMIT: ops.release_slice_member is self-asserted. Its subject,
-- commit_sha and pr_number come from the slice-marker seat, and the database
-- cannot see git, so a commit or pull request close stands on that seat's
-- record of what shipped, not on proof that the commit is in the release.

-- ── 1. defect resolution ─────────────────────────────────────────────────────

create table public.defect_resolution (
  id uuid primary key default gen_random_uuid(),
  defect_id uuid not null references public.defect(id),
  evidence_kind text not null check (evidence_kind in ('commit', 'pull_request', 'receipt')),
  evidence_ref text not null,
  note text check (note is null or char_length(note) <= 1000),
  idempotency_key text not null unique check (btrim(idempotency_key) <> ''),
  resolved_by uuid not null references public.actor(id),
  created_at timestamptz not null default now(),
  -- One close per defect. A second close with the same evidence is the same
  -- act (close-defect returns the first receipt); different evidence is
  -- refused rather than overwriting what the defect was closed on.
  constraint defect_resolution_one_per_defect unique (defect_id),
  constraint defect_resolution_evidence_shape check (
    (evidence_kind = 'commit' and evidence_ref ~ '^[0-9a-f]{7,40}$')
    or (evidence_kind = 'pull_request' and evidence_ref ~ '^[1-9][0-9]{0,6}$')
    or (evidence_kind = 'receipt'
        and evidence_ref ~ '^[a-z][a-z0-9_.-]{0,40}:[A-Za-z0-9][A-Za-z0-9:_./-]{0,250}$'))
);

comment on table public.defect_resolution is
  'Append-only close receipt for one defect (0872, decision cbe91287): the fixing commit, pull request or server receipt, who bound it and when. The defect row itself never changes, so v_defect_class keeps counting it; v_defect shows the receipt beside it.';

create function public.defect_resolution_is_append_only() returns trigger
language plpgsql set search_path = pg_catalog, public, pg_temp as $fn$
begin
  raise exception 'defect_resolution is append-only: a closed defect keeps the evidence it was closed on';
end $fn$;

-- One spelling per receipt. A server receipt's uuid is matched lowercase
-- everywhere it is stored, so an uppercase uuid is the same receipt rather than
-- an unresolvable one; an incident ref keeps its own (uppercase) form.
create function ops.work_request_evidence_receipt_normalized(p_ref text) returns text
language sql immutable set search_path = pg_catalog, pg_temp as $fn$
  select case
    when p_ref is null then null
    when btrim(p_ref) ~* '^(ops\.run|ops\.deployment|defect-resolution):[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
      then lower(btrim(p_ref))
    else btrim(p_ref)
  end
$fn$;

comment on function ops.work_request_evidence_receipt_normalized(text) is
  'The stored spelling of a receipt reference (0872): a server receipt''s prefix and uuid lowercased, anything else trimmed.';

-- The server row a defect's fix evidence resolves to, or null. A commit or pull
-- request counts only when the server already holds it as merged and shipped
-- (ops.release_slice_member); a receipt only when it resolves to a succeeded
-- run, a complete deployment or a resolved incident. SECURITY DEFINER because
-- close-defect inserts as carr_writer, which holds no grant on the slice ledger.
create function ops.defect_resolution_evidence_receipt(p_kind text, p_ref text) returns text
language plpgsql stable security definer set search_path = pg_catalog, ops, public, pg_temp as $fn$
declare
  v_ref text := ops.work_request_evidence_receipt_normalized(p_ref);
  v_member uuid;
  v_shas integer;
begin
  if p_kind = 'commit' and v_ref ~ '^[0-9a-f]{7,40}$' then
    select count(distinct m.commit_sha) into v_shas
      from ops.release_slice_member m where m.commit_sha like v_ref || '%';
    if v_shas = 1 then
      select m.id into v_member from ops.release_slice_member m
       where m.commit_sha like v_ref || '%' order by m.created_at, m.id limit 1;
      return 'release-slice-member:' || v_member::text;
    end if;
  elsif p_kind = 'pull_request' and v_ref ~ '^[1-9][0-9]{0,6}$' then
    select m.id into v_member from ops.release_slice_member m
     where m.pr_number = v_ref::integer order by m.created_at, m.id limit 1;
    if v_member is not null then
      return 'release-slice-member:' || v_member::text;
    end if;
  elsif p_kind = 'receipt' and ops.work_request_evidence_receipt_kind(v_ref) in ('run', 'deployment', 'incident') then
    return v_ref;
  end if;
  return null;
end $fn$;

comment on function ops.defect_resolution_evidence_receipt(text, text) is
  'What a defect close stands on (0872): release-slice-member:<uuid> for a commit or pull request the server recorded as merged and shipped, or the receipt itself when it is a succeeded run, a complete deployment or a resolved incident. Null otherwise; public.defect_resolution refuses the insert on null.';

-- WHY RESOLVING EVIDENCE IS NOT YET EVIDENCE FOR THIS DEFECT, or null when it
-- is. Existence is not enough: a close's receipt is later bound as Work Request
-- and incident evidence, so the evidence must name the defect, the same way the
-- run and deployment arms of ops.work_request_evidence_receipt_mismatch name a
-- request. A name is the defect's full id, or the ref of a Work Request whose
-- title, desired outcome or acceptance criteria name that id (how the defect
-- bridge files one), each as a whole token in:
--   commit / pull request   a release slice member's merged commit subject
--   run                     run_key, source_ref or evidence_ref, or a release
--                           recorded for such a Work Request
--   deployment              a shipped release slice member's subject
--   incident                an incident_link to the defect, or to such a
--                           Work Request
-- SECURITY DEFINER for the same reason as the resolver: carr_writer holds no
-- grant on the slice ledger.
create function ops.defect_resolution_evidence_mismatch(p_defect_id uuid, p_kind text, p_ref text) returns text
language plpgsql stable security definer set search_path = pg_catalog, ops, public, pg_temp as $fn$
declare
  v_ref constant text := ops.work_request_evidence_receipt_normalized(p_ref);
  v_defect constant text := lower(p_defect_id::text);
  v_requests text[];
  v_names text;
begin
  if ops.defect_resolution_evidence_receipt(p_kind, p_ref) is null then
    return format('defect_resolution_evidence_unresolved: %s %s resolves to no merged commit or pull request the server recorded, succeeded run, complete deployment or resolved incident',
                  coalesce(p_kind, 'null'), coalesce(v_ref, 'null'));
  end if;
  select coalesce(array_agg(w.ref order by w.ref), '{}') into v_requests
    from ops.work_request w
   where position(v_defect in lower(coalesce(w.title, '') || ' ' || coalesce(w.desired_outcome, '')
                                    || ' ' || w.acceptance_criteria::text)) > 0;
  v_names := '(^|[^A-Za-z0-9])(' || array_to_string(v_defect || v_requests, '|') || ')([^A-Za-z0-9]|$)';
  if p_kind = 'commit' then
    if exists (select 1 from ops.release_slice_member m
                where m.commit_sha like v_ref || '%' and m.subject ~* v_names) then
      return null;
    end if;
  elsif p_kind = 'pull_request' then
    if exists (select 1 from ops.release_slice_member m
                where m.pr_number = v_ref::integer and m.subject ~* v_names) then
      return null;
    end if;
  elsif v_ref ~ '^ops\.run:' then
    if exists (select 1 from ops.run r left join ops.release rel on rel.id = r.release_id
                where r.id = substring(v_ref from 9)::uuid
                  and (rel.work_request_ref = any (v_requests) or coalesce(r.run_key, '') ~* v_names
                       or coalesce(r.source_ref, '') ~* v_names or coalesce(r.evidence_ref, '') ~* v_names)) then
      return null;
    end if;
  elsif v_ref ~ '^ops\.deployment:' then
    if exists (select 1 from ops.deployment d
                 join ops.release_slice_member m on m.release_id = d.release_id
                where d.id = substring(v_ref from 16)::uuid and m.subject ~* v_names) then
      return null;
    end if;
  elsif v_ref ~ '^incident:' then
    if exists (select 1 from ops.incident i
                 join ops.incident_link l on l.incident_id = i.id
                where i.ref = substring(v_ref from 10)
                  and ((l.kind = 'defect' and lower(btrim(l.ref)) = v_defect)
                       or (l.kind = 'work_request' and l.ref = any (v_requests)))) then
      return null;
    end if;
  end if;
  return format('defect_resolution_evidence_not_about_defect: %s %s names neither defect %s nor a Work Request that names it (%s)',
                p_kind, v_ref, v_defect, coalesce(nullif(array_to_string(v_requests, ', '), ''), 'none does'));
end $fn$;

comment on function ops.defect_resolution_evidence_mismatch(uuid, text, text) is
  'Why fix evidence may not close this defect (0872), or null when it may: the evidence must resolve (ops.defect_resolution_evidence_receipt) and name the defect''s id, or a Work Request naming it, in the slice member subject, the run''s key or references, the deployment''s shipped subject, or an incident link. public.defect_resolution refuses the insert on a non-null answer.';

create function public.defect_resolution_evidence_must_resolve() returns trigger
language plpgsql set search_path = pg_catalog, public, pg_temp as $fn$
declare
  v_mismatch constant text := ops.defect_resolution_evidence_mismatch(new.defect_id, new.evidence_kind, new.evidence_ref);
begin
  if v_mismatch is not null then
    raise exception '%', v_mismatch using errcode = '23514';
  end if;
  return new;
end $fn$;

create trigger defect_resolution_evidence_resolves before insert
on public.defect_resolution for each row execute function public.defect_resolution_evidence_must_resolve();
create trigger defect_resolution_append_only before update or delete
on public.defect_resolution for each row execute function public.defect_resolution_is_append_only();
create trigger defect_resolution_no_truncate before truncate
on public.defect_resolution for each statement execute function public.defect_resolution_is_append_only();
create trigger scac_reference_monitor_guard_row before insert or update or delete
on public.defect_resolution for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on public.defect_resolution for each statement execute function ops.scac_reference_monitor_guard();

revoke all on public.defect_resolution from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant select on public.defect_resolution to carr_writer, carr_exporter;
grant insert on public.defect_resolution to carr_writer;

-- carr_reader holds no grant on the base tables; v_defect is how a read
-- session sees the log, so the receipt joins it here. Same columns as before,
-- in the same order, with the four receipt columns appended.
create or replace view public.v_defect as
select d.id,
       d.occurred_on,
       d.defect_class,
       d.claimed,
       d.actual,
       d.source_unread,
       d.rule_violated,
       d.detected_by,
       d.session_key,
       d.cost_note,
       r.statement as rule_statement,
       a.slug as recorded_by,
       d.created_at,
       res.created_at as resolved_at,
       res.evidence_kind as resolution_kind,
       res.evidence_ref as resolution_ref,
       ra.slug as resolved_by
  from public.defect d
  left join public.actor a on a.id = d.created_by
  left join public.rule r on r.id::text = d.rule_violated
                          or left(r.id::text, 8) = lower(btrim(coalesce(d.rule_violated, '')))
  left join public.defect_resolution res on res.defect_id = d.id
  left join public.actor ra on ra.id = res.resolved_by;

-- ── 2. which receipts the database will stand on ─────────────────────────────

-- The kind of server record a receipt reference resolves to RIGHT NOW, or null.
-- Each branch matches the reference's exact shape before it casts, so a
-- malformed reference answers null instead of raising.
create function ops.work_request_evidence_receipt_kind(p_receipt text) returns text
language plpgsql stable set search_path = pg_catalog, ops, public, pg_temp as $fn$
declare
  uuid_shape constant text := '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
  p_ref constant text := ops.work_request_evidence_receipt_normalized(p_receipt);
begin
  if p_ref is null then
    return null;
  elsif p_ref ~ ('^ops\.run:' || uuid_shape || '$') then
    if exists (select 1 from ops.run r where r.id = substring(p_ref from 9)::uuid and r.state = 'succeeded') then
      return 'run';
    end if;
  elsif p_ref ~ ('^ops\.deployment:' || uuid_shape || '$') then
    if exists (select 1 from ops.deployment d where d.id = substring(p_ref from 16)::uuid and d.state = 'complete') then
      return 'deployment';
    end if;
  elsif p_ref ~ ('^defect-resolution:' || uuid_shape || '$') then
    if exists (select 1 from public.defect_resolution x where x.id = substring(p_ref from 19)::uuid) then
      return 'defect_resolution';
    end if;
  elsif p_ref ~ '^incident:INC-[0-9]{8}-(0[1-9]|[1-9][0-9]+)$' then
    if exists (select 1 from ops.incident i where i.ref = substring(p_ref from 10)
                                            and i.state in ('resolved', 'reviewed')) then
      return 'incident';
    end if;
  end if;
  return null;
end $fn$;

comment on function ops.work_request_evidence_receipt_kind(text) is
  'The one definition of a bound evidence receipt (0872): ops.run:<uuid> that succeeded, ops.deployment:<uuid> that completed, defect-resolution:<uuid>, or incident:<ref> that is resolved or reviewed. Null for anything else, including a malformed reference.';

-- WHY A RESOLVING RECEIPT IS NOT YET EVIDENCE FOR THIS REQUEST, or null when
-- it is. Each kind must be tied to the request by a row the request's own work
-- produced, never by the binder's say-so:
--   run          belongs to a release recorded for the request
--                (ops.release.work_request_ref), or names the request in its
--                run_key, source_ref or evidence_ref
--   deployment   ships a release slice member whose merged commit subject
--                names it
--   defect       the closed defect is one the request names (title, desired
--                outcome or acceptance criteria), as the defect bridge files it
--   incident     linked to the request (link-incident-work-request)
create function ops.work_request_evidence_receipt_mismatch(p_receipt text, p_work_request_id uuid) returns text
language plpgsql stable set search_path = pg_catalog, ops, public, pg_temp as $fn$
declare
  v_ref constant text := ops.work_request_evidence_receipt_normalized(p_receipt);
  v_kind constant text := ops.work_request_evidence_receipt_kind(p_receipt);
  v_names text;
  w ops.work_request%rowtype;
begin
  select x.* into w from ops.work_request x where x.id = p_work_request_id;
  if not found then
    return 'work_request_not_found';
  end if;
  if v_kind is null then
    return format('receipt_unresolved: %s', coalesce(v_ref, 'null'));
  end if;
  v_names := '(^|[^A-Za-z0-9])' || w.ref || '([^0-9]|$)';
  if v_kind = 'run' then
    if exists (select 1 from ops.run r left join ops.release rel on rel.id = r.release_id
                where r.id = substring(v_ref from 9)::uuid
                  and (rel.work_request_ref = w.ref or coalesce(r.run_key, '') ~ v_names
                       or coalesce(r.source_ref, '') ~ v_names or coalesce(r.evidence_ref, '') ~ v_names)) then
      return null;
    end if;
    return format('run_not_linked_to_work_request: %s belongs to no release recorded for %s and names %s in none of run_key, source_ref, evidence_ref',
                  v_ref, w.ref, w.ref);
  elsif v_kind = 'deployment' then
    if exists (select 1 from ops.deployment d
                 join ops.release_slice_member m on m.release_id = d.release_id
                where d.id = substring(v_ref from 16)::uuid
                  and m.subject ~ v_names) then
      return null;
    end if;
    return format('deployment_not_linked_to_work_request: %s ships no release slice member whose merged commit subject names %s',
                  v_ref, w.ref);
  elsif v_kind = 'defect_resolution' then
    if exists (select 1 from public.defect_resolution x
                where x.id = substring(v_ref from 19)::uuid
                  and position(x.defect_id::text in lower(coalesce(w.title, '') || ' ' || coalesce(w.desired_outcome, '')
                                                          || ' ' || w.acceptance_criteria::text)) > 0) then
      return null;
    end if;
    return format('defect_not_linked_to_work_request: %s closes a defect %s does not name in its title, desired outcome or acceptance criteria',
                  v_ref, w.ref);
  elsif v_kind = 'incident' then
    if exists (select 1 from ops.incident i
                 join ops.incident_link l on l.incident_id = i.id and l.kind = 'work_request'
                where i.ref = substring(v_ref from 10) and l.ref = w.ref) then
      return null;
    end if;
    return format('incident_not_linked_to_work_request: %s is not linked to %s (link-incident-work-request)', v_ref, w.ref);
  end if;
  return format('receipt_unresolved: %s', v_ref);
end $fn$;

comment on function ops.work_request_evidence_receipt_mismatch(text, uuid) is
  'Why a receipt is not evidence for this Work Request (0872), or null when it is: a run of the request''s release or naming it, a deployment shipping a slice member naming it, the close of a defect it names, or an incident linked to it.';

-- WHO MAY BIND OR COMPLETE. The acting actor is the server-set
-- carr.acting_actor_slug, resolved to an active actor row; it must be a human
-- actor, or an automation actor whose transaction carries a verified sponsoring
-- partner (carr.sponsoring_human_slug, the same server-set sponsor
-- ops.j102_sponsoring_partner stands on). An unsponsored runtime is refused here
-- whichever role or verb reached the function.
create function ops.work_request_completion_actor() returns public.actor
language plpgsql stable set search_path = pg_catalog, ops, public, pg_temp as $fn$
declare
  v_slug constant text := nullif(btrim(current_setting('carr.acting_actor_slug', true)), '');
  v_sponsor constant text := nullif(btrim(current_setting('carr.sponsoring_human_slug', true)), '');
  a public.actor%rowtype;
begin
  select x.* into a from public.actor x where x.slug = v_slug and x.active;
  if not found then
    raise exception 'work_request_completion_actor_unresolved: the server-set acting actor (%) is not an active actor',
      coalesce(v_slug, 'none') using errcode = '42501';
  end if;
  if a.kind = 'human' or (a.kind = 'automation' and v_sponsor in ('joe', 'dell')) then
    return a;
  end if;
  raise exception 'work_request_completion_requires_sponsored_actor: % is a % actor without a verified sponsoring partner; only a human actor or a sponsored agent binds evidence to or completes a Work Request',
    a.slug, a.kind using errcode = '42501';
end $fn$;

comment on function ops.work_request_completion_actor() is
  'The actor a Work Request evidence binding or completion is recorded under (0872): the server-set acting actor, refused with 42501 unless it is a human actor or a sponsored agent.';

-- THE PREDICATE. Pure: criteria are the Work Request''s acceptance_criteria
-- ([{id,text}]); bindings are [{criterion_id, receipt_ref, receipt_kind,
-- binding_id}] where receipt_kind is the CURRENT resolution (null when the
-- receipt no longer resolves). Complete only when every criterion id has a
-- binding with a non-null kind; an empty criteria list is never complete.
create function ops.work_request_bound_evidence_verdict(p_criteria jsonb, p_bindings jsonb) returns jsonb
language sql immutable set search_path = pg_catalog, pg_temp as $fn$
  with criteria as (
    select distinct c.value ->> 'id' as id
      from jsonb_array_elements(case when jsonb_typeof(p_criteria) = 'array' then p_criteria else '[]'::jsonb end) c
     where c.value ->> 'id' is not null
  ), bound as (
    select b.value ->> 'criterion_id' as id, b.value as binding
      from jsonb_array_elements(case when jsonb_typeof(p_bindings) = 'array' then p_bindings else '[]'::jsonb end) b
     where b.value ->> 'receipt_kind' is not null and b.value ->> 'criterion_id' is not null
  ), missing as (
    select criteria.id from criteria
     where not exists (select 1 from bound where bound.id = criteria.id)
  )
  select jsonb_build_object(
    'complete', exists (select 1 from criteria) and not exists (select 1 from missing),
    'missing', coalesce((select jsonb_agg(missing.id order by missing.id) from missing), '[]'::jsonb),
    'receipts', coalesce((select jsonb_agg(jsonb_build_object(
                    'criterion_id', bound.id,
                    'receipt_ref', bound.binding ->> 'receipt_ref',
                    'receipt_kind', bound.binding ->> 'receipt_kind',
                    'binding_id', bound.binding ->> 'binding_id') order by bound.id)
                  from bound where exists (select 1 from criteria where criteria.id = bound.id)), '[]'::jsonb))
$fn$;

comment on function ops.work_request_bound_evidence_verdict(jsonb, jsonb) is
  'Pure completion predicate (0872): {complete, missing, receipts}. Complete only when every acceptance criterion id carries a binding whose receipt currently resolves.';

-- ── 3. Work Request criterion evidence ───────────────────────────────────────

create table ops.work_request_criterion_evidence (
  id uuid primary key default gen_random_uuid(),
  work_request_id uuid not null references ops.work_request(id),
  criterion_id text not null check (criterion_id ~ '^[A-Z][A-Z0-9-]{1,63}$'),
  receipt_ref text not null check (char_length(receipt_ref) <= 300),
  receipt_kind text not null check (receipt_kind in ('run', 'deployment', 'defect_resolution', 'incident')),
  bound_by_actor_id uuid not null references public.actor(id),
  idempotency_key uuid not null unique,
  created_at timestamptz not null default now(),
  -- One receipt per criterion. Binding is a fact about evidence, not a vote,
  -- so a second, different receipt for the same criterion is refused.
  constraint work_request_criterion_evidence_once unique (work_request_id, criterion_id)
);

-- One receipt is evidence for one criterion, ever. Without this, one green run
-- could satisfy every criterion of a request.
create unique index work_request_criterion_evidence_receipt_once
  on ops.work_request_criterion_evidence (receipt_ref);

comment on table ops.work_request_criterion_evidence is
  'Append-only binding of one sourced Work Request acceptance criterion to one server-resolvable receipt (0872). Written only through ops.bind_work_request_criterion_evidence; when every criterion is bound and a criteria_met outcome proposal exists, the request completes on bound evidence.';

create function ops.work_request_criterion_evidence_is_append_only() returns trigger
language plpgsql set search_path = pg_catalog, ops, pg_temp as $fn$
begin
  raise exception 'work_request_criterion_evidence is append-only: a bound receipt is never rewritten';
end $fn$;

create trigger work_request_criterion_evidence_append_only before update or delete
on ops.work_request_criterion_evidence for each row execute function ops.work_request_criterion_evidence_is_append_only();
create trigger work_request_criterion_evidence_no_truncate before truncate
on ops.work_request_criterion_evidence for each statement execute function ops.work_request_criterion_evidence_is_append_only();
create trigger scac_reference_monitor_guard_row before insert or update or delete
on ops.work_request_criterion_evidence for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on ops.work_request_criterion_evidence for each statement execute function ops.scac_reference_monitor_guard();

revoke all on ops.work_request_criterion_evidence from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant select on ops.work_request_criterion_evidence to carr_reader, carr_writer;

-- ── 4. the acceptance receipt says how it was reached ────────────────────────

alter table ops.sourced_work_request_outcome_feedback_acceptance_receipt
  add column acceptance_basis text not null default 'human_acceptance',
  add column bound_receipt_refs jsonb,
  add constraint sourced_outcome_acceptance_basis_shape check (
    (acceptance_basis = 'human_acceptance' and bound_receipt_refs is null)
    or (acceptance_basis = 'completed_on_bound_evidence'
        and jsonb_typeof(bound_receipt_refs) = 'array'
        and jsonb_array_length(bound_receipt_refs) > 0));

comment on column ops.sourced_work_request_outcome_feedback_acceptance_receipt.acceptance_basis is
  'human_acceptance: a partner accepted the proposal (accept-outcome-feedback). completed_on_bound_evidence: every acceptance criterion carried a bound receipt the database resolved, and the request completed without a human call (0872); bound_receipt_refs lists them.';

-- ── 5. completion on bound evidence ──────────────────────────────────────────

-- Accept the latest criteria_met outcome proposal for the current ready plan
-- when every acceptance criterion carries a receipt that resolves NOW. Returns
-- completed=false with what is missing, and writes nothing, otherwise. It
-- re-derives everything it relies on; nothing a caller says is trusted.
create function ops.complete_sourced_work_request_on_bound_evidence(p_work_request text, p_idempotency_key uuid)
returns table(work_request_id uuid, ref text, state text, version integer, completed boolean, missing jsonb,
              receipt_id uuid, feedback_ref text, feedback_hash text, acceptance_basis text,
              bound_receipt_refs jsonb, accepted_by_actor_slug text, accepted_at timestamptz, replayed boolean)
language plpgsql security definer set search_path = pg_catalog, ops, public, pg_temp as $fn$
declare
  a public.actor%rowtype; w ops.work_request%rowtype;
  p ops.sourced_work_request_plan%rowtype;
  ar ops.sourced_work_request_plan_acceptance_receipt%rowtype;
  f ops.sourced_work_request_outcome_feedback%rowtype;
  fr ops.sourced_work_request_outcome_feedback_acceptance_receipt%rowtype;
  bindings jsonb; verdict jsonb; canonical_preimage jsonb; canonical_hash text;
begin
  if coalesce(btrim(p_work_request), '') !~ '^WR-[0-9]{1,12}$' or p_idempotency_key is null then
    raise exception 'completion on bound evidence requires an exact Work Request ref and a UUID idempotency key';
  end if;
  a := ops.work_request_completion_actor();

  perform pg_advisory_xact_lock(hashtextextended('program6-outcome-feedback-acceptance:' || p_idempotency_key, 0));
  select x.* into fr from ops.sourced_work_request_outcome_feedback_acceptance_receipt x
   where x.idempotency_key = p_idempotency_key;
  if found then
    select x.* into w from ops.work_request x where x.id = fr.work_request_id;
    select x.* into f from ops.sourced_work_request_outcome_feedback x where x.id = fr.feedback_id;
    if fr.acceptance_basis is distinct from 'completed_on_bound_evidence' or w.ref is distinct from p_work_request then
      raise exception 'idempotency key already names a different outcome-feedback acceptance';
    end if;
    return query select w.id, w.ref, w.state, fr.result_version, true, '[]'::jsonb, fr.id,
      f.feedback_ref, f.feedback_hash, fr.acceptance_basis, fr.bound_receipt_refs,
      (select y.slug from public.actor y where y.id = fr.accepted_by_actor_id), fr.accepted_at, true;
    return;
  end if;

  select x.* into w from ops.work_request x where x.ref = p_work_request for update;
  if not found then raise exception 'exact sourced Work Request not found'; end if;
  if w.capture_idempotency_key is null or w.organization_tenant_id is distinct from 'carr-internal'
     or w.state is distinct from 'ready' then
    return query select w.id, w.ref, w.state, w.version, false, '["ready_sourced_work_request"]'::jsonb,
      null::uuid, null::text, null::text, null::text, null::jsonb, null::text, null::timestamptz, false;
    return;
  end if;

  -- The latest proposal for the current version. An older criteria_met
  -- proposal never completes a request whose newest proposal says otherwise.
  select x.* into f from ops.sourced_work_request_outcome_feedback x
   where x.work_request_id = w.id and x.work_request_version = w.version
   order by x.feedback_version desc limit 1;
  if not found or f.outcome is distinct from 'criteria_met' or f.blocker_code is distinct from 'none' then
    return query select w.id, w.ref, w.state, w.version, false, '["criteria_met_outcome_proposal"]'::jsonb,
      null::uuid, f.feedback_ref, f.feedback_hash, null::text, null::jsonb, null::text, null::timestamptz, false;
    return;
  end if;
  if exists (select 1 from ops.sourced_work_request_outcome_feedback_acceptance_receipt x where x.feedback_id = f.id) then
    return query select w.id, w.ref, w.state, w.version, false, '["unaccepted_outcome_proposal"]'::jsonb,
      null::uuid, f.feedback_ref, f.feedback_hash, null::text, null::jsonb, null::text, null::timestamptz, false;
    return;
  end if;

  -- The same exactness accept_sourced_work_request_outcome_feedback demands.
  select x.* into p from ops.sourced_work_request_plan x where x.id = f.plan_id;
  select x.* into ar from ops.sourced_work_request_plan_acceptance_receipt x where x.id = f.plan_acceptance_receipt_id;
  if not found or ar.work_request_id is distinct from w.id or ar.plan_id is distinct from p.id
     or ar.result_version is distinct from w.version or ar.plan_hash is distinct from p.plan_hash
     or ar.accepted_by_actor_id is null
     or (w.shape_fixed_surface_ref is distinct from ar.shape_fixed_surface_ref
         and not exists (
           select 1 from ops.sourced_work_request_plan_shape_binding_receipt sb
            where sb.plan_acceptance_receipt_id = ar.id
              and sb.work_request_id = w.id
              and sb.disposition is not distinct from w.shape_disposition
              and sb.fixed_surface_ref is not distinct from w.shape_fixed_surface_ref
              and exists (select 1 from ops.sourced_work_request_shape_disposition_lineage(w.id) e
                           where (e.disposition, e.fixed_surface_ref, e.rationale, e.decided_by_actor_id, e.decided_at)
                             is not distinct from (sb.disposition, sb.fixed_surface_ref, sb.rationale, sb.decided_by_actor_id, sb.decided_at)))) then
    raise exception 'only an exact current ready sourced plan may complete on bound evidence';
  end if;
  canonical_preimage := ops.sourced_work_request_outcome_feedback_preimage(
    w.id, p.id, ar.id, f.criterion_results, f.evidence_refs, f.outcome, f.blocker_code,
    f.result_summary, f.observed_minutes, f.interaction_surface, f.heavy_session_used,
    f.manual_context_transfers);
  canonical_hash := ops.sourced_work_request_outcome_feedback_digest(canonical_preimage);
  if canonical_preimage is distinct from f.preimage or canonical_hash is distinct from f.feedback_hash then
    raise exception 'sourced outcome-feedback preimage is stale or does not match its exact hash';
  end if;

  select coalesce(jsonb_agg(jsonb_build_object(
           'criterion_id', e.criterion_id, 'receipt_ref', e.receipt_ref,
           'receipt_kind', case when ops.work_request_evidence_receipt_mismatch(e.receipt_ref, w.id) is null
                                then ops.work_request_evidence_receipt_kind(e.receipt_ref) end,
           'binding_id', e.id::text) order by e.criterion_id), '[]'::jsonb)
    into bindings
    from ops.work_request_criterion_evidence e where e.work_request_id = w.id;
  verdict := ops.work_request_bound_evidence_verdict(w.acceptance_criteria, bindings);
  if not (verdict ->> 'complete')::boolean then
    return query select w.id, w.ref, w.state, w.version, false, verdict -> 'missing',
      null::uuid, f.feedback_ref, f.feedback_hash, null::text, null::jsonb, null::text, null::timestamptz, false;
    return;
  end if;

  insert into ops.sourced_work_request_outcome_feedback_acceptance_receipt
    (work_request_id, feedback_id, idempotency_key, base_version, feedback_hash,
     accepted_by_actor_id, result_version, acceptance_basis, bound_receipt_refs)
  values (w.id, f.id, p_idempotency_key, w.version, f.feedback_hash, a.id, w.version,
          'completed_on_bound_evidence', verdict -> 'receipts')
  returning * into fr;
  return query select w.id, w.ref, w.state, w.version, true, '[]'::jsonb, fr.id, f.feedback_ref,
    f.feedback_hash, fr.acceptance_basis, fr.bound_receipt_refs, a.slug, fr.accepted_at, false;
end $fn$;

comment on function ops.complete_sourced_work_request_on_bound_evidence(text, uuid) is
  'Completion on bound evidence (0872): writes an outcome-feedback acceptance receipt with acceptance_basis completed_on_bound_evidence when the acting actor is a human or sponsored agent, the latest proposal for the current ready plan is criteria_met, and every acceptance criterion carries a receipt that resolves now and is evidence for this request. Otherwise writes nothing and returns what is missing.';

-- Bind one criterion of a ready sourced Work Request to one receipt, then try
-- to complete. Idempotent on the key; the same receipt twice is the same act.
create function ops.bind_work_request_criterion_evidence(p_work_request text, p_criterion_id text,
                                                         p_receipt_ref text, p_idempotency_key uuid)
returns table(binding_id uuid, work_request_id uuid, ref text, criterion_id text, receipt_ref text,
              receipt_kind text, bound_by_actor_slug text, created_at timestamptz, replayed boolean)
language plpgsql security definer set search_path = pg_catalog, ops, public, pg_temp as $fn$
declare
  a public.actor%rowtype; w ops.work_request%rowtype;
  e ops.work_request_criterion_evidence%rowtype; v_kind text; v_mismatch text;
  v_receipt constant text := ops.work_request_evidence_receipt_normalized(p_receipt_ref);
begin
  if coalesce(btrim(p_work_request), '') !~ '^WR-[0-9]{1,12}$'
     or coalesce(p_criterion_id, '') !~ '^[A-Z][A-Z0-9-]{1,63}$'
     or coalesce(p_receipt_ref, '') = '' or char_length(p_receipt_ref) > 300
     or p_idempotency_key is null then
    raise exception 'criterion evidence binding requires an exact Work Request ref, criterion id, receipt reference and UUID idempotency key';
  end if;
  a := ops.work_request_completion_actor();

  perform pg_advisory_xact_lock(hashtextextended('work-request-criterion-evidence:' || p_idempotency_key, 0));
  select x.* into e from ops.work_request_criterion_evidence x where x.idempotency_key = p_idempotency_key;
  if found then
    select x.* into w from ops.work_request x where x.id = e.work_request_id;
    if w.ref is distinct from p_work_request or e.criterion_id is distinct from p_criterion_id
       or e.receipt_ref is distinct from v_receipt then
      raise exception 'idempotency key already names a different criterion evidence binding';
    end if;
    return query select e.id, w.id, w.ref, e.criterion_id, e.receipt_ref, e.receipt_kind,
      (select y.slug from public.actor y where y.id = e.bound_by_actor_id), e.created_at, true;
    return;
  end if;

  select x.* into w from ops.work_request x where x.ref = p_work_request for update;
  if not found then raise exception 'exact sourced Work Request not found'; end if;
  if w.capture_idempotency_key is null or w.organization_tenant_id is distinct from 'carr-internal'
     or w.state is distinct from 'ready' then
    raise exception 'criterion evidence binds only to a ready sourced Work Request';
  end if;
  if not exists (select 1 from jsonb_array_elements(w.acceptance_criteria) c where c.value ->> 'id' = p_criterion_id) then
    raise exception 'criterion % is not one of this Work Request''s acceptance criteria', p_criterion_id;
  end if;
  v_kind := ops.work_request_evidence_receipt_kind(v_receipt);
  if v_kind is null then
    raise exception 'receipt_unresolved: receipt % does not resolve to a succeeded run, a complete deployment, a defect resolution or a resolved incident', v_receipt;
  end if;
  v_mismatch := ops.work_request_evidence_receipt_mismatch(v_receipt, w.id);
  if v_mismatch is not null then
    raise exception 'receipt_not_evidence_for_work_request: %', v_mismatch;
  end if;

  select x.* into e from ops.work_request_criterion_evidence x
   where x.work_request_id = w.id and x.criterion_id = p_criterion_id;
  if found then
    if e.receipt_ref is distinct from v_receipt then
      raise exception 'criterion_already_bound: criterion % is already bound to %', p_criterion_id, e.receipt_ref;
    end if;
    return query select e.id, w.id, w.ref, e.criterion_id, e.receipt_ref, e.receipt_kind,
      (select y.slug from public.actor y where y.id = e.bound_by_actor_id), e.created_at, true;
    return;
  end if;
  select x.* into e from ops.work_request_criterion_evidence x where x.receipt_ref = v_receipt;
  if found then
    raise exception 'receipt_already_bound: % is already evidence for % criterion %', v_receipt,
      (select y.ref from ops.work_request y where y.id = e.work_request_id), e.criterion_id;
  end if;

  insert into ops.work_request_criterion_evidence
    (work_request_id, criterion_id, receipt_ref, receipt_kind, bound_by_actor_id, idempotency_key)
  values (w.id, p_criterion_id, v_receipt, v_kind, a.id, p_idempotency_key)
  returning * into e;
  return query select e.id, w.id, w.ref, e.criterion_id, e.receipt_ref, e.receipt_kind, a.slug, e.created_at, false;
end $fn$;

comment on function ops.bind_work_request_criterion_evidence(text, text, text, uuid) is
  'Bind one acceptance criterion of a ready sourced Work Request to one receipt that resolves through ops.work_request_evidence_receipt_kind and is evidence for this request (ops.work_request_evidence_receipt_mismatch), as a human actor or sponsored agent (0872). Append-only; a different receipt for a bound criterion, and a receipt already bound anywhere, are refused.';

revoke all on function ops.work_request_evidence_receipt_normalized(text) from public;
revoke all on function ops.defect_resolution_evidence_receipt(text, text) from public;
revoke all on function ops.defect_resolution_evidence_mismatch(uuid, text, text) from public;
revoke all on function public.defect_resolution_evidence_must_resolve() from public;
revoke all on function ops.work_request_evidence_receipt_mismatch(text, uuid) from public;
revoke all on function ops.work_request_completion_actor() from public;
revoke all on function ops.work_request_evidence_receipt_kind(text) from public;
revoke all on function ops.work_request_bound_evidence_verdict(jsonb, jsonb) from public;
revoke all on function ops.complete_sourced_work_request_on_bound_evidence(text, uuid) from public;
revoke all on function ops.bind_work_request_criterion_evidence(text, text, text, uuid) from public;
grant execute on function ops.work_request_evidence_receipt_normalized(text) to carr_writer, carr_reader;
grant execute on function ops.defect_resolution_evidence_receipt(text, text) to carr_writer;
grant execute on function ops.defect_resolution_evidence_mismatch(uuid, text, text) to carr_writer;
grant execute on function ops.work_request_evidence_receipt_kind(text) to carr_writer, carr_reader;
grant execute on function ops.work_request_bound_evidence_verdict(jsonb, jsonb) to carr_writer, carr_reader;
grant execute on function ops.complete_sourced_work_request_on_bound_evidence(text, uuid) to carr_writer;
grant execute on function ops.bind_work_request_criterion_evidence(text, text, text, uuid) to carr_writer;
