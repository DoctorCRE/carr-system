-- W15: explicit invoice dates and the existing commission ledger, no provider effect.
alter table commission add column due_on date;
comment on column commission.due_on is 'Contractual invoice due date; null means unknown. Never inferred from deal close or a payment promise.';

create view v_invoice_tracker as
select d.id as deal_id, d.name, d.owner, d.phase, d.lane, d.outcome, d.closed_on,
       d.invoiced_on as deal_invoiced_on,
       cm.id as commission_id, cm.gross_amount, cm.status, cm.version as base_version,
       cm.invoiced_on as commission_invoiced_on, cm.received_on, cm.due_on
  from deal d
  join client cl on cl.id=d.client_id and cl.merged_into is null
  join party p on p.id=cl.party_id and p.deleted_at is null and p.merged_into is null
  left join commission cm on cm.deal_id=d.id
 where d.invoiced_on is not null
    or (d.phase='closed' and (d.outcome is null or d.outcome='won'))
    or cm.status in ('invoiced','received');
grant select on v_invoice_tracker to carr_reader, carr_writer;
