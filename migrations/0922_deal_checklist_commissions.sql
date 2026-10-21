-- Rollback: restore the checklist reader before dropping v_deal_room_commission.
create view v_deal_room_commission as
select deal_id, id as commission_id, status, received_on
  from commission;
grant select on v_deal_room_commission to carr_reader, carr_writer;
