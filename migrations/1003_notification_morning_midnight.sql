-- rollback: forward-only — restore the previous function body with a new migration if needed.
-- Replaces one stable function body in place; no table, lock or data change.
create or replace function ops.notification_morning_release_at(p_actor uuid)
returns timestamptz language plpgsql stable security definer
set search_path = pg_catalog, ops, public, pg_temp
as $$
declare v_pref ops.notification_preference%rowtype; v_tz text; v_open time;
        v_local timestamp; v_today_open timestamp;
begin
  select * into v_pref from ops.notification_preference where actor = p_actor;
  v_tz := coalesce(v_pref.timezone, 'UTC');
  v_open := case when v_pref.quiet_hours_start is not null and v_pref.quiet_hours_end is not null
                 then v_pref.quiet_hours_end else time '07:00' end;
  v_local := now() at time zone v_tz;
  v_today_open := date_trunc('day', v_local) + v_open;
  if (v_local >= v_today_open and v_local < v_today_open + interval '4 hours')
     or (v_local >= v_today_open - interval '1 day' and v_local < v_today_open - interval '20 hours') then
    return now();
  end if;
  if v_local < v_today_open then
    return v_today_open at time zone v_tz;
  end if;
  return (v_today_open + interval '1 day') at time zone v_tz;
end;
$$;
