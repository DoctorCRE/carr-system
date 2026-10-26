-- rollback: forward-only — restore 0531's ops.acknowledge_dispatch body with another create or replace; no row or grant changes.
-- expand-contract: contract — an acknowledgement from an identity that is neither the dispatch's delivering desk nor its target session's recorded owner is now refused.
-- lock-review: replaces one function body; no table is touched, so no lock beyond the catalog row.
--
-- WR-000528 (audit F7, PR #2183): bind dispatch acknowledgements to their
-- authorized writers INSIDE THE SQL DOOR, before the insert and before the
-- deduplication that used to hand any caller the first acknowledgement.
--
-- 0531 checked only that the dispatch existed, so an authenticated identity
-- that knew a dispatch_ref could reserve another session's stage, and a later
-- legitimate writer received the impostor's row back as "deduplicated". The
-- signature, grants and first-hand attribution are unchanged; what is new is
-- who may write each stage, derived from server-recorded facts only:
--
--   the dispatch's DESK    the identity that minted the link: the room bridge
--                          delivers the turn into the desk window, so it alone
--                          observes `received` from outside the session.
--   the TARGET SESSION     an identity a session book records as owning
--                          link.session_id, by the same ownership columns
--                          0529's session_identity_facts uses to decide who may
--                          see that session: the Claude continuity leaf's
--                          owner or surface principal, the Codex checkpoint's
--                          owner, or the capability session's executor or
--                          creator.
--
-- `received` may be written by the desk or the target session (the session's
-- own window observes the landing too). `acknowledged` is the acting session's
-- statement that it took the turn up, so only the target session may write it;
-- the bridge cannot speak for the session. Everyone else is refused with
-- dispatch_ack_not_authorized and no row, no receipt and no other actor's name.

create or replace function ops.acknowledge_dispatch(
  p_dispatch_ref uuid, p_stage text, p_evidence text)
returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare v_actor uuid; v_slug text; v_id bigint; v_stage text;
        v_link public.room_dispatch_link%rowtype;
        v_prior public.room_dispatch_ack%rowtype;
        v_session_party boolean;
begin
  v_actor := ops.portfolio_writer_actor_id();
  select a.slug into v_slug from public.actor a where a.id = v_actor;
  if v_slug is null then
    return jsonb_build_object('ok', false, 'reason_id', 'dispatch_actor_underived');
  end if;
  if p_dispatch_ref is null then
    return jsonb_build_object('ok', false, 'reason_id', 'dispatch_ref_required');
  end if;
  v_stage := nullif(btrim(coalesce(p_stage, '')), '');
  if v_stage is null or v_stage not in ('received', 'acknowledged') then
    return jsonb_build_object('ok', false, 'reason_id', 'dispatch_stage_invalid');
  end if;
  select * into v_link from public.room_dispatch_link l
   where l.dispatch_ref = p_dispatch_ref;
  if v_link.id is null then
    return jsonb_build_object('ok', false, 'reason_id', 'dispatch_link_not_found');
  end if;

  -- WHO MAY WRITE THIS STAGE, before anything is inserted or read back.
  v_session_party :=
       exists (select 1 from public.claude_continuity_leaf l
                where l.session_id = v_link.session_id
                  and (l.owner_actor_id = v_actor or l.surface_principal_actor_id = v_actor))
    or exists (select 1 from public.codex_continuity_checkpoint c
                where c.native_task_id = v_link.session_id and c.owner_actor_id = v_actor)
    or exists (select 1 from ops.capability_agent_session s
                where s.id::text = v_link.session_id
                  and (s.executor_actor_id = v_actor or s.created_by_actor_id = v_actor));
  if not (v_session_party
          or (v_stage = 'received' and v_slug = v_link.written_by)) then
    return jsonb_build_object('ok', false, 'reason_id', 'dispatch_ack_not_authorized');
  end if;

  insert into public.room_dispatch_ack(dispatch_ref, stage, by_actor, evidence)
  values (p_dispatch_ref, v_stage, v_slug, p_evidence)
  on conflict (dispatch_ref, stage) do nothing
  returning id into v_id;

  if v_id is null then
    select * into v_prior from public.room_dispatch_ack
     where dispatch_ref = p_dispatch_ref and stage = v_stage;
    if v_prior.id is null then
      return jsonb_build_object('ok', false, 'reason_id', 'dedup_row_vanished');
    end if;
    return jsonb_build_object('ok', true, 'deduplicated', true, 'id', v_prior.id,
      'dispatch_ref', v_prior.dispatch_ref, 'stage', v_prior.stage,
      'by_actor', v_prior.by_actor);
  end if;

  return jsonb_build_object('ok', true, 'deduplicated', false, 'id', v_id,
    'dispatch_ref', p_dispatch_ref, 'stage', v_stage, 'by_actor', v_slug);
end $$;

comment on function ops.acknowledge_dispatch(uuid,text,text) is
  'WR-000119/WR-000528: appends one public.room_dispatch_ack row for a dispatch and a stage. by_actor is the DERIVED caller. Only the link''s minting desk (received) or an identity a session book records as owning the target session (received or acknowledged) may write; anyone else is refused with dispatch_ack_not_authorized before the insert and before deduplication. The unique (dispatch_ref, stage) constraint makes an authorized restatement one row.';
