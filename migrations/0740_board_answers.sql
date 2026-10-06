-- 0740: typed progress boards and durable question, answer, receipt and application state.
-- 0741 seals this migration in the same atomic group.

create table public.board_snapshot (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null check (length(btrim(organization_tenant_id)) > 0),
  sponsoring_human_slug text not null check (sponsoring_human_slug in ('joe','dell')),
  board_id text not null check (board_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$'),
  version bigint not null default 1 check (version > 0),
  snapshot_json jsonb not null check (jsonb_typeof(snapshot_json) = 'object'),
  updated_by_actor_id uuid not null references public.actor(id),
  updated_at timestamptz not null default now(),
  unique (organization_tenant_id, sponsoring_human_slug, board_id)
);

create table public.board_question (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  sponsoring_human_slug text not null check (sponsoring_human_slug in ('joe','dell')),
  board_id text not null,
  question_id text not null check (question_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$'),
  revision bigint not null check (revision > 0),
  current boolean not null default true,
  prompt text not null check (length(btrim(prompt)) > 0 and length(prompt) <= 4000),
  choices jsonb not null default '[]'::jsonb check (jsonb_typeof(choices) = 'array'),
  allow_free_text boolean not null default true,
  default_answer text check (default_answer is null or length(default_answer) <= 500),
  asker_ref text not null check (asker_ref ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$'),
  asked_by_actor_id uuid not null references public.actor(id),
  asked_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (organization_tenant_id,sponsoring_human_slug,board_id)
    references public.board_snapshot (organization_tenant_id,sponsoring_human_slug,board_id),
  unique (organization_tenant_id,sponsoring_human_slug,board_id,question_id,revision)
);

create unique index board_question_one_current
  on public.board_question (organization_tenant_id,sponsoring_human_slug,board_id,question_id) where current;
create index board_question_current_board
  on public.board_question (organization_tenant_id,sponsoring_human_slug,board_id,asked_at,question_id) where current;

create table public.board_answer (
  id uuid primary key default gen_random_uuid(),
  cursor bigint generated always as identity unique,
  organization_tenant_id text not null,
  sponsoring_human_slug text not null check (sponsoring_human_slug in ('joe','dell')),
  board_id text not null,
  question_id text not null,
  question_revision bigint not null,
  asker_ref text not null,
  answer_text text not null check (length(btrim(answer_text)) > 0 and length(answer_text) <= 4000),
  answered_by text not null check (answered_by in ('joe','dell')),
  answered_by_actor_id uuid not null references public.actor(id),
  sent_at timestamptz not null default now(),
  received_at timestamptz,
  received_by_actor_id uuid references public.actor(id),
  received_for_ref text,
  applied_at timestamptz,
  applied_by_actor_id uuid references public.actor(id),
  effect_ref text check (effect_ref is null or length(btrim(effect_ref)) > 0),
  default_overridden boolean not null default false,
  version bigint not null default 1 check (version between 1 and 3),
  foreign key (organization_tenant_id,sponsoring_human_slug,board_id,question_id,question_revision)
    references public.board_question (organization_tenant_id,sponsoring_human_slug,board_id,question_id,revision),
  unique (organization_tenant_id,sponsoring_human_slug,board_id,question_id,question_revision),
  check ((received_at is null and received_by_actor_id is null and received_for_ref is null and version=1)
      or (received_at is not null and received_by_actor_id is not null and received_for_ref is not null and version>=2)),
  check ((applied_at is null and applied_by_actor_id is null and effect_ref is null and version<=2)
      or (applied_at is not null and applied_by_actor_id is not null and effect_ref is not null and received_at is not null and version=3))
);

create index board_answer_asker_cursor
  on public.board_answer (organization_tenant_id,sponsoring_human_slug,asker_ref,cursor);

revoke all on public.board_snapshot,public.board_question,public.board_answer
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant select on public.board_snapshot,public.board_question,public.board_answer to carr_reader,carr_writer;
grant insert,update on public.board_snapshot,public.board_question,public.board_answer to carr_writer;
grant usage,select on sequence public.board_answer_cursor_seq to carr_writer;

create trigger scac_reference_monitor_guard_row before insert or update or delete
on public.board_snapshot for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on public.board_snapshot for each statement execute function ops.scac_reference_monitor_guard();

create trigger scac_reference_monitor_guard_row before insert or update or delete
on public.board_question for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on public.board_question for each statement execute function ops.scac_reference_monitor_guard();

create trigger scac_reference_monitor_guard_row before insert or update or delete
on public.board_answer for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on public.board_answer for each statement execute function ops.scac_reference_monitor_guard();
