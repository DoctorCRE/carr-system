-- W9: exact current lease dates for the authenticated deal detail read.
-- Keep lease writes and historical/unverified rows outside the reader surface.
create view v_deal_room_current_lease as
select id,deal_id,version,status,executed_on,commencement_on,expiration_on,
       options_note,evidence_kind,evidence_ref,source
  from lease where status='current';
grant select on v_deal_room_current_lease to carr_reader,carr_writer;
