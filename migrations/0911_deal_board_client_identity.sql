-- rollback: forward-only — additive read identity; remove consumers before narrowing the view.
-- Preserve the exact client identity for closed and invoiced detail reads.
CREATE OR REPLACE VIEW public.v_deal_board AS
 SELECT d.id,
    d.name,
    c.roster_ref AS client_ref,
    pc.name AS client_name,
    d.deal_type,
    d.phase,
    ph.sort AS phase_sort,
    d.segment,
    d.outcome,
    lead_actor.slug AS lead_owner,
    lt.last_touch,
    d.notes_path,
    d.invoiced_on,
    d.closed_on,
    d.lane,
    d.client_id
   FROM ((((((public.deal d
     JOIN public.client c ON ((c.id = d.client_id)))
     JOIN public.party pc ON ((pc.id = c.party_id)))
     JOIN public.deal_phase ph ON ((ph.slug = d.phase)))
     LEFT JOIN public.deal_participant dp ON (((dp.deal_id = d.id) AND (dp.role = 'lead'::text) AND (dp.to_at IS NULL))))
     LEFT JOIN public.actor lead_actor ON ((lead_actor.id = dp.actor_id)))
     LEFT JOIN public.v_last_touch lt ON (((lt.subject_type = 'deal'::text) AND (lt.subject_id = d.id))));
