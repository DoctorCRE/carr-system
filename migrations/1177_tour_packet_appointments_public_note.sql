-- rollback: forward-only; existing sealed packets retain their reviewed fields.
-- lock-review: function and check-constraint replacements; no stored values are changed.
create or replace function ops.tour_client_field_keys()
returns text[] language sql immutable as $$
  select array['display.name','display.address','suite','property_type','size','asking_economics','availability','parking','public_packet_note']::text[]
$$;
do $packet_fields$
declare f record; definition text; updated text; c record;
begin
  for f in select p.oid,p.proname from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='ops' and p.proname='tour_public_value_safe' loop
    definition:=pg_get_functiondef(f.oid);
    updated:=replace(definition,'''availability'',''parking''','''availability'',''parking'',''public_packet_note''');
    if updated=definition then raise exception 'packet note allowlist anchor missing for %',f.proname; end if;
    execute updated;
  end loop;
  for c in select conname,pg_get_constraintdef(oid) definition from pg_constraint
    where conrelid='ops.tour_public_projection_fact'::regclass and contype='c'
      and pg_get_constraintdef(oid) like '%display_field_key%parking%' loop
    execute format('alter table ops.tour_public_projection_fact drop constraint %I',c.conname);
    execute format('alter table ops.tour_public_projection_fact add constraint %I %s',c.conname,
      replace(c.definition,'''parking''::text','''parking''::text, ''public_packet_note''::text'));
  end loop;
  for f in select p.oid,p.proname from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='ops' and p.proname in ('read_tour_packet_for_render','read_tour_share_packet') loop
    definition:=pg_get_functiondef(f.oid);
    updated:=replace(definition,
      'max(a.value#>>''{}'') filter(where f.display_field_key=''parking'') parking',
      'max(a.value#>>''{}'') filter(where f.display_field_key=''parking'') parking,
      max(a.value#>>''{}'') filter(where f.display_field_key=''public_packet_note'') public_packet_note,
      max(appointment.appointment_start) appointment_start,max(appointment.appointment_end) appointment_end');
    updated:=replace(updated,'join ops.tour_public_projection_fact f on',
      'left join lateral (
        select s.appointment_start,s.appointment_end from ops.tour_route_version r
        join ops.tour_route_stop s on s.organization_tenant_id=r.organization_tenant_id and s.route_version_id=r.id
        where r.organization_tenant_id=p.organization_tenant_id and r.tour_id=p.tour_id and r.route_version=p.route_version
          and s.property_id=m.property_id and s.stop_state=''active''
      ) appointment on true
    join ops.tour_public_projection_fact f on');
    updated:=replace(updated,'''as_of'',p.as_of,''caveat'',null',
      '''contract_version'',''2.1.0'',''renderer_version'',''2.0.0'',''as_of'',p.as_of,''caveat'',null');
    if updated=definition or updated not like '%max(appointment.appointment_start)%' then
      raise exception 'packet read extension anchor missing for %',f.proname;
    end if;
    execute updated;
  end loop;
end $packet_fields$;
