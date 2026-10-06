-- 0331_a_batch_close_must_not_outrank_a_live_ruling.sql
--
-- Restores ONE row of the AI engineering suite that was closed as a decline
-- against the owner's own live ruling: WR-AI-028, the interpretability and
-- sparse-autoencoder tooling item.
--
-- WHAT HAPPENED, stated precisely because the fix looks like an override of a
-- governed close and is not one.
--
-- On 2026-08-23 Joe commissioned a walkthrough of the seventeen proposed
-- declines in this programme, one item at a time, with each Work Request body
-- read to him in full before he ruled. The interpretability row was item 3. He
-- ruled KEEP IT BUILDABLE. That ruling is decision
-- 93d2ae2e-4471-4803-9742-8196c00215d7, author joe, human_quote 'keep'.
--
-- Minutes later a second session closed the same row as declined, at
-- 2026-08-24T03:14:50Z, on the strength of a batch confirmation
-- (f1ff43ad-cf9a-439e-9226-95da765eeac7) whose stated premise was that eight
-- rows "never needed a fresh decision — their disposition was settled when Joe
-- drafted the suite; what was missing was the written reason."
--
-- That premise was true of seven rows and FALSE of this one. This row had just
-- received a fresh decision, and it went the other way. The batch session read
-- the row's own desired_outcome and non_goals, which is what its provenance
-- states, and never asked the decision log whether anyone had already ruled on
-- the row it was about to close. One find-precedent call would have returned
-- Joe's ruling, dated the same day. Filed as defect
-- d0535bff-05d8-4664-8ec7-2a88b3c74188, first of its class.
--
-- WHY A MIGRATION AND NOT A VERB. confirmed_closed is terminal in the deployed
-- completion contract, deliberately: nothing in mcp-server/src/
-- capability-program.js moves a row back out of it, and a behaviour check
-- across all 183 deployed verbs found none that reopens a closed capability
-- project. The alternative considered and rejected was a new reopen verb. That
-- would widen the write surface of the completion contract permanently, to
-- repair a single row, and every future caller would inherit the ability to
-- un-close governed work.
--
-- ═══════════════════════════════════════════════════════════════════════════
-- THE FOUR TRIGGERS THIS UPDATE MEETS, and why the statements are shaped the
-- way they are. Every one of these was found by running the repair against a
-- disposable PostgreSQL rather than by reading the contract and hoping.
-- ═══════════════════════════════════════════════════════════════════════════
--
-- 1. ops.capability_program_closed_immutable — THE ONE THAT ACTUALLY BLOCKS.
--    It raises 'closed capability programme evidence is immutable' on ANY
--    change to a confirmed_closed row whose program_key is this suite. Its only
--    existing escape hatch is a tenant-id backfill guarded by a session
--    setting, which cannot express this repair.
--
--    Two ways round it were considered. Widening the trigger with a second
--    escape hatch was rejected: the hatch would outlive the repair and become a
--    standing way to edit closed evidence, which is the property the trigger
--    exists to deny. Instead this migration DISABLES exactly that one trigger,
--    makes exactly one row's repair, and re-enables it inside the same
--    transaction. PostgreSQL DDL is transactional, so a failure anywhere below
--    rolls the guard back on with the data. The disable takes an ACCESS
--    EXCLUSIVE lock on ops.work_request, so no other session can slip a write
--    past the lowered guard while it is down. The proof at the bottom asserts
--    the trigger is enabled again before this file commits.
--
--    The other three triggers stay ARMED throughout. This is not a blanket
--    'disable triggers and update' — it lowers one guard, the only one that
--    refuses, and leaves every substantive check in force.
--
-- 2. ops.enforce_completion_capsule — stays armed and passes. Its first
--    statement returns early when new.state is not confirmed_closed, so a row
--    LEAVING the closed state is not asked for evidence it no longer claims.
--
-- 3. ops.capability_program_close_guard — stays armed and passes, for the same
--    reason: it returns early unless new.state is confirmed_closed.
--
-- 4. ops.work_request_shape_gate — stays armed, and it is why the repair is
--    TWO statements rather than one. It enforces two rules that pull against
--    each other here:
--      * shape columns are FROZEN once old.state is outside
--        (captured, triaged, ready) — so a single statement that moved the row
--        to ready and cleared its shape at the same time is refused, because
--        the old state is still confirmed_closed when the rule is evaluated;
--      * a row ENTERING ready must carry a non-null shape_disposition.
--    So statement one moves the row to ready and leaves the shape untouched,
--    satisfying both. Statement two then clears the shape, legally, because by
--    then old.state is ready and the freeze no longer applies.
--
--    ops.enforce_work_in_progress_limit and ops.heavy_build_ready_plan_gate
--    were also checked and neither fires: the first returns unless the new
--    state is claimed or in_progress, the second only on triaged to ready.
--
-- WHY THE SHAPE IS CLEARED AT ALL. The recorded rationale reads "Declined;
-- nothing will be implemented, so there is no shape to choose between." That
-- sentence is now false. Leaving it would hand the next builder a fixed-surface
-- claim derived from a withdrawn decline. Cleared, the row matches its sixteen
-- undecided siblings, which all sit ready with a null shape disposition, and
-- start-capability-project will refuse it until a shape decision is made afresh.
--
-- WHAT IS DELIBERATELY NOT TOUCHED. The evidence trail stays exactly as it is:
--   * ops.capability_verification keeps its attestation row. Migration 0127
--     makes that table append-only and this migration does not fight it. The
--     attestation genuinely happened; erasing it would replace one false record
--     with another.
--   * The capability_agent_session that ran keeps state 'completed'. It also
--     genuinely ran. The partial unique index capability_one_open_session_per_
--     request excludes completed and cancelled sessions, so leaving it closed
--     is what allows a future session to claim this row again.
--   * Every event row written by the close stays on the timeline.
--   * `disposition` stays 'decline'. The row was seeded that way and will read
--     as a proposed decline again, still counting in
--     proposed_declines_awaiting_a_decision. That is honest: Joe's keep ruling
--     lives in the decision log, and no deployed verb can express "the owner
--     decided this the other way" on a programme row. Repairing THAT is a
--     separate design question about the programme's own vocabulary and is not
--     smuggled in here.
--
-- NOT A RULING ON THE OTHER ROWS. Seven other rows from that batch remain
-- closed and are untouched. Joe confirmed the batch, its premise held for them,
-- and their rationales are on the record and reviewable. On 2026-08-26 he ruled
-- separately that the whole fifty-one-item suite is to be left in place and
-- revisited by a fresh session once the web-app system work is done; that
-- ruling stops new declines being recorded and does not ask for past ones to be
-- undone.
--
-- RE-RUN SAFE, and safe where this row was never closed. Both updates are
-- guarded on the state they expect, and the proof measures a DELTA rather than
-- an absolute count, so it holds on a disposable PostgreSQL loaded from
-- db/schema.sql as well as on Production.

begin;

alter table ops.work_request disable trigger capability_program_closed_immutable_before_update;

do $$
declare
  wr             ops.work_request%rowtype;
  programme_rows integer;
  closed_before  integer;
  closed_after   integer;
  reopened       integer;
  audit_rows     integer;
  session_rows   integer;
begin
  select count(*) into programme_rows
    from ops.work_request
   where program_key = 'carr-ai-engineering-suite-v1';

  -- THE SNAPSHOT IS STRUCTURE PLUS REFERENCE DATA, NOT PROGRAMME ROWS.
  -- db/schema.sql carries the tables and the actor rows but not the fifty-one
  -- seeded items, so a disposable local database has nothing to repair and must
  -- say so rather than fail. The skip is guarded on the programme being
  -- entirely absent, never on this row alone: if the suite has rows and
  -- WR-AI-028 is not among them, that is a mistyped reference and it raises.
  if programme_rows = 0 then
    raise notice 'AI engineering suite has no rows on this database; nothing to repair';
    return;
  end if;

  if not exists (select 1 from ops.work_request
                  where program_key = 'carr-ai-engineering-suite-v1' and ref = 'WR-AI-028') then
    raise exception 'the AI engineering suite has % row(s) but WR-AI-028 is not among them', programme_rows;
  end if;

  select count(*) into closed_before
    from ops.work_request
   where program_key = 'carr-ai-engineering-suite-v1'
     and state = 'confirmed_closed';

  -- STATEMENT ONE. Withdraw the close. Shape columns are untouched here on
  -- purpose; see note 4 in the header.
  update ops.work_request
     set state                     = 'ready',
         completion_kind           = null,
         completion_evidence       = null,
         verification_accepted_at  = null,
         verification_evidence_ref = null,
         executor_actor            = null,
         claimed_at                = null,
         started_at                = null,
         closed_at                 = null,
         version                   = version + 1,
         updated_at                = now()
   where program_key = 'carr-ai-engineering-suite-v1'
     and ref         = 'WR-AI-028'
     and state       = 'confirmed_closed';

  -- STATEMENT TWO. Now that the row is ready the shape freeze no longer
  -- applies, so the disposition derived from the withdrawn decline can go.
  update ops.work_request
     set shape_disposition         = null,
         shape_fixed_surface_ref   = null,
         shape_rationale           = null,
         shape_decided_by_actor_id = null,
         shape_decided_at          = null,
         version                   = version + 1,
         updated_at                = now()
   where program_key = 'carr-ai-engineering-suite-v1'
     and ref         = 'WR-AI-028'
     and state       = 'ready'
     and shape_disposition is not null;

  select count(*) into closed_after
    from ops.work_request
   where program_key = 'carr-ai-engineering-suite-v1'
     and state = 'confirmed_closed';

  -- ═══════════════════════════════════════════════════════════════════════
  -- BLAST RADIUS. THIS IS THE LOAD-BEARING ASSERTION and it is measured, not
  -- described. An earlier draft of this file merely counted the closed rows
  -- and printed the number in a notice. A mutation test — the ref filter
  -- deleted from both predicates, so the repair matched every closed row in
  -- the programme — reopened the whole suite and the proof still passed. The
  -- notice was decoration. This is the check that kills that mutant.
  -- ═══════════════════════════════════════════════════════════════════════
  reopened := closed_before - closed_after;
  if reopened > 1 then
    raise exception
      'blast radius: % rows left the closed set, expected at most 1 (% -> %)',
      reopened, closed_before, closed_after;
  end if;
  if reopened < 0 then
    raise exception 'this migration closed % row(s); it must never close anything', -reopened;
  end if;

  select * into wr
    from ops.work_request
   where program_key = 'carr-ai-engineering-suite-v1' and ref = 'WR-AI-028';

  -- END STATE, asserted whether or not this run changed anything, so a second
  -- application still proves the row is where it belongs.
  if wr.state <> 'ready' then
    raise exception 'expected WR-AI-028 in state ready, found %', wr.state;
  end if;
  if wr.completion_kind is not null or wr.completion_evidence is not null then
    raise exception 'WR-AI-028 still carries a completion bundle';
  end if;
  if wr.verification_accepted_at is not null or wr.verification_evidence_ref is not null then
    raise exception 'WR-AI-028 still carries an accepted verification';
  end if;
  if wr.executor_actor is not null or wr.closed_at is not null
     or wr.claimed_at is not null or wr.started_at is not null then
    raise exception 'WR-AI-028 still reads as claimed or closed';
  end if;
  if wr.shape_disposition is not null or wr.shape_rationale is not null then
    raise exception 'WR-AI-028 still carries a shape disposition derived from the withdrawn decline';
  end if;

  -- The seeded disposition is untouched. A migration that quietly rewrote it
  -- would make Joe's ruling unfalsifiable instead of recording it.
  if wr.disposition <> 'decline' then
    raise exception 'WR-AI-028 disposition changed to %, which this migration must not do', wr.disposition;
  end if;

  -- THE AUDIT TRAIL SURVIVED. Only asserted where a close actually happened; a
  -- database that never closed this row has neither row and must not fail for
  -- their absence.
  select count(*) into audit_rows
    from ops.capability_verification where work_request_id = wr.id;
  select count(*) into session_rows
    from ops.capability_agent_session where work_request_id = wr.id and state = 'completed';

  if audit_rows > 0 and session_rows < 1 then
    raise exception 'the completed build session for WR-AI-028 was destroyed';
  end if;

  raise notice 'WR-AI-028 state=% disposition=% version=%; % attestation row(s), % completed session(s); closed rows % -> % (reopened %)',
    wr.state, wr.disposition, wr.version, audit_rows, session_rows, closed_before, closed_after, reopened;
end $$;

alter table ops.work_request enable trigger capability_program_closed_immutable_before_update;

-- THE GUARD IS BACK UP. Asserted separately, after the enable, because leaving
-- the closed-evidence immutability guard lowered would be a far worse outcome
-- than the defect this file repairs.
do $$
declare guard_state "char";
begin
  select t.tgenabled into guard_state
    from pg_trigger t
    join pg_class c on c.oid = t.tgrelid
    join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'ops' and c.relname = 'work_request'
     and t.tgname = 'capability_program_closed_immutable_before_update';

  if guard_state is null then
    raise exception 'the closed-evidence immutability trigger is missing entirely';
  end if;
  if guard_state <> 'O' then
    raise exception 'the closed-evidence immutability trigger was left disabled (tgenabled=%)', guard_state;
  end if;
  raise notice 'closed-evidence immutability guard re-armed';
end $$;

commit;
