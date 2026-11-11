-- rollback: forward-only; restore the prior page-count guard in a new migration if template 2.0.0 is retired.
-- Packet template 2.0.0 has one overview followed by the ordered property sheets.
do $overview$
declare definition text; updated text;
begin
  select pg_get_functiondef('ops.record_tour_pdf_render_result(text,uuid,text,text,text,text,integer,integer,integer,text,text)'::regprocedure)
    into definition;
  updated:=replace(definition,'p_page_count<>v_job.expected_property_count or','p_page_count<>v_job.expected_property_count+1 or');
  if updated=definition then
    raise exception 'tour PDF page-count guard was not found';
  end if;
  execute updated;
end $overview$;
