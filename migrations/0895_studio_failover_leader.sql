-- Source only: manual host failover is not enabled by applying this schema.
create table ops.studio_leader (
    singleton boolean primary key default true check (singleton),
    host text not null check (host in ('studio','macbook')),
    epoch bigint not null default 1 check (epoch > 0),
    fence_evidence jsonb not null default '{}'::jsonb,
    changed_at timestamptz not null default clock_timestamp()
);
insert into ops.studio_leader(singleton,host) values (true,'studio');
revoke all on ops.studio_leader from public;
grant usage on schema ops to carr_jobs,carr_authority;
grant select on ops.studio_leader to carr_jobs,carr_authority;

create function ops.transfer_studio_leader(
    p_source text, p_target text, p_epoch bigint, p_evidence jsonb
) returns bigint
language plpgsql security definer set search_path=pg_catalog,ops as $$
declare
    current_host text;
    current_epoch bigint;
    verified_at timestamptz;
begin
    perform ops.authority_actor_slug();
    if p_source is null or p_target is null or p_source = p_target
       or p_source not in ('studio','macbook') or p_target not in ('studio','macbook') then
        raise exception 'invalid_transfer_hosts';
    end if;
    begin
        verified_at := (p_evidence->>'verified_at')::timestamptz;
    exception when others then
        raise exception 'invalid_fence_evidence';
    end;
    if p_evidence is null or jsonb_typeof(p_evidence) <> 'object'
       or (p_evidence->>'source') is distinct from p_source
       or (p_evidence->>'target') is distinct from p_target
       or coalesce(p_evidence->>'target_sha','') !~ '^[0-9a-f]{40}$'
       or verified_at is null or verified_at > clock_timestamp()
       or verified_at < clock_timestamp() - interval '1 hour'
       or not coalesce(
           (p_evidence->>'kind' = 'powered-off' and p_evidence->'keep_off_until_failback' = 'true'::jsonb)
           or (p_evidence->>'kind' = 'demoted' and p_evidence->'armed' = 'false'::jsonb
               and jsonb_typeof(p_evidence->'unregistered') = 'array'), false) then
        raise exception 'invalid_fence_evidence';
    end if;
    if not pg_try_advisory_xact_lock(638148226000001) then
        raise exception 'running_jobs_hold_transfer_lock';
    end if;
    select host,epoch into strict current_host,current_epoch
      from ops.studio_leader where singleton for update;
    if p_epoch is distinct from current_epoch then
        raise exception 'leader_epoch_conflict';
    end if;
    if current_host = p_target then return current_epoch; end if;
    if current_host <> p_source then raise exception 'leader_owner_conflict'; end if;
    update ops.studio_leader set host=p_target,epoch=epoch+1,
        fence_evidence=p_evidence,changed_at=clock_timestamp()
      where singleton and host=p_source and epoch=p_epoch returning epoch into current_epoch;
    return current_epoch;
end $$;
revoke all on function ops.transfer_studio_leader(text,text,bigint,jsonb) from public;
grant execute on function ops.transfer_studio_leader(text,text,bigint,jsonb) to carr_authority;
comment on table ops.studio_leader is
  'Durable Studio/MacBook owner. Never expires. Manual transfer requires source fencing '
  'and exclusive advisory lock 638148226000001; guarded jobs hold its shared form.';
