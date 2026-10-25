-- rollback: forward-only; portal credentials must never change client or deal ownership.
-- lock-review: brief trigger installation on the new, empty-by-default portal table.

create function ops.client_portal_binding_immutable() returns trigger
language plpgsql set search_path=pg_catalog,pg_temp as $$
begin
  if new.client_id is distinct from old.client_id or new.deal_id is distinct from old.deal_id then
    raise exception 'client portal ownership is immutable' using errcode='23514';
  end if;
  return new;
end $$;

create trigger client_portal_binding_immutable before update on ops.client_portal
for each row execute function ops.client_portal_binding_immutable();

revoke select on ops.client_portal,ops.client_portal_material from dot_reader;
