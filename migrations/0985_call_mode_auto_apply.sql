-- rollback: forward-only; the new columns are nullable or defaulted and unread by older code.
-- lock-review: capture_candidate and capture_session hold a few hundred rows; adding defaulted columns does not rewrite them.
--
-- WR-000433 AC4 (Joe, 2026-10-26): a weekly pipeline call updates the cards it
-- discussed plainly, with no per-item confirm. Each automatic change stays
-- listed on the call's outcome, with its transcript evidence, until a partner
-- has reviewed it, and can be undone. Anything the model could not place
-- stays an ordinary pending card.

alter table public.capture_candidate
  add column applied_automatically boolean not null default false,
  add column undone_at timestamptz,
  add column undone_by uuid references public.actor(id),
  add constraint capture_candidate_automatic_check
    check (not applied_automatically or status = 'confirmed'),
  add constraint capture_candidate_undo_check
    check ((undone_at is null) = (undone_by is null)
           and (undone_at is null or applied_automatically));

alter table public.capture_session
  add column outcome_reviewed_at timestamptz,
  add column outcome_reviewed_by uuid references public.actor(id),
  add constraint capture_session_outcome_review_check
    check ((outcome_reviewed_at is null) = (outcome_reviewed_by is null));
