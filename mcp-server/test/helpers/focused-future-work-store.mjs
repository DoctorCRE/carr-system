// A SQL-backed substitute for capture storage in narrow, preexisting fixtures.
// Full capture, provenance, grants and rollback use the actual migration in
// future-work-links-postgres.test.mjs, rather than this substitute.
export async function installFocusedFutureWorkStore(c) {
  await c.query(`create schema if not exists ops;
      create sequence ops.fixture_wr_sequence;
      create table ops.work_request(id uuid primary key default gen_random_uuid(),
        ref text unique default ('WR-'||lpad(nextval('ops.fixture_wr_sequence')::text,6,'0')),
        organization_tenant_id text not null default 'carr-internal');
      create table ops.future_work_link(organization_tenant_id text,source_ref text,
        work_request_id uuid references ops.work_request(id),source_verb text,source_key text,
        primary key(organization_tenant_id,source_ref));
      create function ops.capture_sourced_work_request(text,text,text,jsonb,uuid,uuid,uuid)
        returns table(id uuid,ref text,state text,version integer)
        language sql security definer set search_path=pg_catalog,ops as $capture$
          insert into ops.work_request default values returning id,ref,'captured'::text,1;
        $capture$;
      revoke all on function ops.capture_sourced_work_request(text,text,text,jsonb,uuid,uuid,uuid) from public;
      do $grants$ begin
        if to_regrole('carr_writer') is not null then
          grant execute on function ops.capture_sourced_work_request(text,text,text,jsonb,uuid,uuid,uuid) to carr_writer;
          grant select on ops.work_request to carr_writer;
          grant select,insert,update on ops.future_work_link to carr_writer;
        end if;
      end $grants$;
`);
}
