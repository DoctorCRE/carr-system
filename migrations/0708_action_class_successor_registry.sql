-- 0708_action_class_successor_registry.sql — V5-D01: inactive action-specific
-- autonomy successors (doctrine `doctorcre-v5-astra-integration-review`,
-- section v5-reviewed-implementation-slice-catalog-and-parallel-groups-2026-09-09,
-- proposed_id V5-D01).
--
-- THE SLICE. goal: "Register future unattended Salesforce/email/action
-- classes inactive, each with its own evidence and activation predicate."
-- concrete_output: "Typed successor entries only; no capability issuance or
-- activation." excluded_scope: blanket trust, current activation, policy
-- bypass, authority from registration. checkable_done: entries stored
-- inactive; missing future gate denies action but not unrelated work;
-- registration cannot mint effect capability.
--
-- WHY A NEW TABLE, APPEND-ONLY. Same discipline as loop_amendment (migration
-- 0706): a registration is a fact about the future ("this action class will
-- need this owner, these requirements, this activation predicate"), never an
-- editable configuration row. Nothing in this slice's scope ever turns a row
-- active -- there is no activation door here, on purpose (item_kind
-- deferred_successor; activation is a separate, not-yet-built, human-gated
-- surface). The status column is CHECK-locked to the single value 'inactive'
-- so "current activation" is not a policy choice this migration makes and
-- then trusts callers to respect -- it is a constraint the database itself
-- enforces. Activating an action class later requires its own migration to
-- widen this constraint and add whatever verified-authority door decides it;
-- it can never happen through this table as shipped.
--
-- THE GATE. action_class_successor_gate(text) is the "future gate" the
-- catalog names: given an action_class, it reports whether that class is
-- currently permitted to act unattended. Because every row this migration can
-- ever produce is 'inactive', and because a class with NO row at all is
-- equally ungated, the function returns denied for every input, unconditionally,
-- as shipped -- there is no code path in this migration that can return
-- allowed. That is "missing future gate denies action": the absence (or
-- inactivity) of a successor entry is a deny, not a silent allow. It is scoped
-- to the one action_class argument the caller passes -- a deny for
-- 'salesforce_unattended_write' says nothing about any other class or about
-- any other verb, so it denies the one action asked about and never touches
-- unrelated work.

create table public.action_class_successor (
  id uuid primary key default gen_random_uuid(),
  action_class text not null,
  title text not null,
  goal text not null,
  owner text not null,
  policy_requirements jsonb not null default '{}'::jsonb,
  data_requirements jsonb not null default '{}'::jsonb,
  model_requirements jsonb not null default '{}'::jsonb,
  activation_predicate jsonb not null,
  status text not null default 'inactive',
  actor_id uuid not null references public.actor(id),
  idempotency_key text,
  created_at timestamp with time zone not null default now(),
  constraint action_class_successor_action_class_format
    check (action_class ~ '^[a-z][a-z0-9_]{2,63}$'),
  constraint action_class_successor_action_class_unique unique (action_class),
  constraint action_class_successor_status_inactive_only
    check (status = 'inactive'),
  constraint action_class_successor_title_present check (length(btrim(title)) >= 1),
  constraint action_class_successor_goal_present check (length(btrim(goal)) >= 1),
  constraint action_class_successor_owner_present check (length(btrim(owner)) >= 1),
  constraint action_class_successor_requirements_are_objects
    check (jsonb_typeof(policy_requirements) = 'object'
       and jsonb_typeof(data_requirements) = 'object'
       and jsonb_typeof(model_requirements) = 'object'),
  constraint action_class_successor_predicate_is_object
    check (jsonb_typeof(activation_predicate) = 'object')
);

comment on table public.action_class_successor is
  'V5-D01: typed, inactive successor entries for a future unattended action class (e.g. Salesforce/email writes) -- owner, policy/data/model requirements and the activation predicate that would have to be met before anyone could activate it. Append-only: no update, no delete, no activation door in this migration. status is CHECK-locked to ''inactive'' so registration can never mint an effect capability by itself.';
comment on column public.action_class_successor.action_class is 'Stable slug naming the future action class this entry gates, e.g. salesforce_unattended_write, email_unattended_send. Unique -- one registration per class.';
comment on column public.action_class_successor.activation_predicate is 'What would have to become true before a future, separate activation door could even consider this class. Structured, not prose; never evaluated by this migration -- there is no code here that reads it and returns allowed.';
comment on column public.action_class_successor.status is 'Always ''inactive'' -- the CHECK constraint admits no other value. Widening this is a future migration''s decision, not a runtime one.';
comment on column public.action_class_successor.actor_id is 'Server-derived from the authenticated actor, never a caller-supplied field.';

create index action_class_successor_action_class_idx on public.action_class_successor (action_class);

-- Append-only enforcement, the loop_amendment (0706) / cost_ledger (0519)
-- idiom: the grant below already withholds UPDATE/DELETE from carr_writer,
-- but a grant is a door a later migration could reopen without anyone
-- noticing this table's own rule. The trigger makes the rule a property of
-- the TABLE.
create or replace function public.action_class_successor_rows_immutable()
returns trigger language plpgsql as $$ begin
  raise exception 'action_class_successor rows are append-only -- a correction is a new migration, never a rewrite of one';
end $$;

create trigger action_class_successor_immutable before update or delete
on public.action_class_successor for each row execute function public.action_class_successor_rows_immutable();
create trigger action_class_successor_no_truncate before truncate
on public.action_class_successor for each statement execute function public.action_class_successor_rows_immutable();

-- carr_writer gets SELECT + INSERT and nothing else -- never UPDATE, never
-- DELETE. carr_reader gets NO base-table grant (0024's guard: views-only is
-- the leak guard); read-action-class-successors reaches this table through
-- the SECURITY DEFINER read function below, never through a grant.
grant select, insert on public.action_class_successor to carr_writer;

-- READ DOOR, NOT A GRANT -- same shape as loop_amendment_history (0706) and
-- search_doctrine_situations (0223): EXECUTE is grantable to a role with no
-- SELECT on the table it reads, because the function runs as its owner.
create or replace function public.read_action_class_successors(p_action_class text default null)
returns table (
  id uuid, action_class text, title text, goal text, owner text,
  policy_requirements jsonb, data_requirements jsonb, model_requirements jsonb,
  activation_predicate jsonb, status text, actor text, created_at timestamptz
) language sql stable security definer set search_path = public, pg_temp as $$
  select s.id, s.action_class, s.title, s.goal, s.owner,
         s.policy_requirements, s.data_requirements, s.model_requirements,
         s.activation_predicate, s.status, act.slug, s.created_at
    from public.action_class_successor s
    join public.actor act on act.id = s.actor_id
   where p_action_class is null or s.action_class = p_action_class
   order by s.created_at;
$$;

revoke all on function public.read_action_class_successors(text) from public;
grant execute on function public.read_action_class_successors(text) to carr_reader, carr_writer;

-- THE GATE. Deterministic, and -- as shipped -- unconditional: every input
-- denies. It reads the table (there could, in principle, be an 'active' row
-- one day; there cannot be one yet, because the CHECK constraint above admits
-- only 'inactive') so the shape of the future gate is already in place, but
-- the outcome today never depends on what it finds -- absence and inactivity
-- both deny. That is the point: "missing future gate denies action" holds
-- whether or not anyone ever registered the class.
create or replace function public.action_class_successor_gate(p_action_class text)
returns table (action_class text, allowed boolean, reason text, registered boolean)
language sql stable security definer set search_path = public, pg_temp as $$
  select p_action_class, false,
         case when exists (select 1 from public.action_class_successor where action_class = p_action_class)
              then 'registered but inactive -- no activation door exists for this action class'
              else 'no successor registered for this action class' end,
         exists (select 1 from public.action_class_successor where action_class = p_action_class);
$$;

revoke all on function public.action_class_successor_gate(text) from public;
grant execute on function public.action_class_successor_gate(text) to carr_reader, carr_writer;

-- The SIEP-18 reference monitor (0467) requires every relation carrying a
-- direct INSERT/UPDATE/DELETE/TRUNCATE grant to carr_writer/carr_jobs/
-- carr_authority to also carry its guard trigger pair (see 0706's identical
-- comment for loop_amendment).
create trigger scac_reference_monitor_guard_row before insert or update or delete
on public.action_class_successor for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on public.action_class_successor for each statement execute function ops.scac_reference_monitor_guard();
