-- Synthetic, transaction-scoped financial separation and board/tracker continuity.
\set ON_ERROR_STOP on
begin;
do $$
declare a uuid; p uuid; c uuid; d uuid; cm uuid;
begin
  select id into a from actor where slug='joe';
  insert into party(kind,name,created_by,updated_by) values('org','W15 Demo Practice',a,a) returning id into p;
  insert into client(party_id,created_by,updated_by) values(p,a,a) returning id into c;
  insert into deal(client_id,name,deal_type,phase,outcome,closed_on,lane,won_value,created_by,updated_by)
    values(c,'W15 Demo Assignment','renewal','closed','won','2026-09-01','territory',987654,a,a) returning id into d;
  if not exists(select 1 from v_invoice_tracker where deal_id=d and deal_invoiced_on is null and commission_id is null) then raise exception 'Awaiting invoice hidden'; end if;
  if not exists(select 1 from v_deal_room_board where id=d and closed_on='2026-09-01' and outcome='won' and lane='territory') then raise exception 'Board lifecycle fields lost'; end if;
  insert into commission(deal_id,gross_amount,status,invoiced_on,due_on,created_by)
    values(d,1000,'invoiced','2026-09-02','2026-09-30',a) returning id into cm;
  insert into commission(deal_id,gross_amount,status,created_by) values(d,2000,'expected',a);
  update deal set invoiced_on='2026-09-02' where id=d;
  if exists(select 1 from v_deal_room_board where id=d) then raise exception 'Invoiced deal stayed on board'; end if;
  if (select count(*) from v_invoice_tracker where deal_id=d)<>2 then raise exception 'Installments duplicated or dropped'; end if;
  if not exists(select 1 from v_invoice_tracker where commission_id=cm and gross_amount=1000 and commission_invoiced_on='2026-09-02' and due_on='2026-09-30') then raise exception 'Invoice facts lost'; end if;
  if not exists(select 1 from v_deal_reconciliation_read where id=d and invoiced_on='2026-09-02' and closed_on='2026-09-01' and outcome='won' and lane='territory') then raise exception 'Reconciliation lifecycle fields lost'; end if;
  update commission set status='received',received_on='2026-10-01',updated_by=a where id=cm;
  if not exists(select 1 from v_invoice_tracker where commission_id=cm and received_on='2026-10-01' and status='received') then raise exception 'Paid receipt hidden'; end if;
  if (select won_value from deal where id=d)<>987654 then raise exception 'Receipt changed client benefits'; end if;
  if (select count(*) from commission where deal_id=d and status='expected')<>1 then raise exception 'Paid one installment settled another'; end if;
end $$;
set local role carr_reader;
select deal_id,closed_on,lane,outcome,deal_invoiced_on,commission_id,gross_amount,received_on,due_on from v_invoice_tracker limit 1;
select id,closed_on,lane,outcome,invoiced_on from v_deal_room_board limit 1;
select id,closed_on,lane,outcome,invoiced_on from v_deal_reconciliation_read limit 1;
rollback;
