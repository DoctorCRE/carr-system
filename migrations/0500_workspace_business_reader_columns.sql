-- Clients and Vendors workspace: exact column-scoped reader access.
-- Approved by Joe, native user highwater 108923745, 2026-09-10.
-- SELECT only; app audience and live-record predicates remain mandatory.
-- The migration runner owns the transaction.

GRANT SELECT (id, party_id, roster_ref, client_type, status, etl_status, vertical, subtype, acquisition_source, acquisition_detail, contact_label, deal_type_label, specialty_type_label, possible_duplicate_label, notes, owner_id, owner_label, merged_into, version, created_at, updated_at) ON TABLE public.client TO carr_reader;
GRANT SELECT (id, party_id, vendor_ref, category, category_slug, stage, disposition, relationship_level, verticals, territory, offers, seeking, rivalry_group, originated, referral_active, is_target, out_of_market, last_touch, intro_notes, links_label, owner_id, owner_label, merged_into, version, created_at, updated_at) ON TABLE public.vendor TO carr_reader;
GRANT SELECT (id, name, kind, ref, city, state, county, title, specialty, npi, phone, cell, email, contact_state, contact_state_reason, contact_state_until, contact_state_cadence, merged_into, deleted_at) ON TABLE public.party TO carr_reader;
GRANT SELECT (slug, label, sort, is_active_pipeline, note) ON TABLE public.client_status TO carr_reader;
GRANT SELECT (slug, label) ON TABLE public.client_type TO carr_reader;
GRANT SELECT (slug, label, sort) ON TABLE public.vendor_category TO carr_reader;
GRANT SELECT (slug, label, sort) ON TABLE public.vendor_stage TO carr_reader;
GRANT SELECT (slug, label, sort, workable) ON TABLE public.vendor_disposition TO carr_reader;
GRANT SELECT (level, label, note) ON TABLE public.vendor_relationship_level TO carr_reader;
