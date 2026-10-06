-- 0571_tour_writer_login_membership.sql
--
-- Let the Worker's writer login reach the Tour mutation functions.
--
-- WHY (2026-09-23, feeding the Sapala pre-tour, Joe: "turn the hosted tour
-- surface on and feed it sapala"). ops.tour_server_actor_id() is the one actor
-- gate that 12 Tour mutation functions call (create_tour_domain,
-- append_tour_route_version, append_tour_route_stop, the cheat-sheet writes,
-- and the rest). Since 0429 it accepted a writer session only when
-- session_user was LITERALLY 'carr_writer' or 'carr_authority'. Those are
-- NOLOGIN bundle roles: nothing ever authenticates as them. The deployed
-- Worker authenticates as app_writer, a member of carr_writer, so every
-- non-authority Tour mutation in Production raised "tour mutation requires an
-- authority connection or sponsored writer session". create-tour-domain from a
-- sponsored Claude session was the first real call and it failed exactly so.
--
-- WHAT CHANGES. One more accepted session shape, by MEMBERSHIP rather than by
-- name: a login that is a member of carr_writer or carr_authority resolves its
-- actor from carr.acting_actor_slug, which the Worker sets server-side for every
-- sponsored write (mcp.js). This is the same membership test 0517 and 0532a
-- already use for writer sessions. Unchanged: the carr_authority_* branch still
-- derives the actor from the login name and is checked first; the actor must
-- still be a non-empty bounded slug; any other session is still refused.
-- EXECUTE grants are untouched, so who may call each Tour function is exactly
-- what 0429 and later migrations granted.


create or replace function ops.tour_server_actor_id() returns text
  language plpgsql security definer
  set search_path=pg_catalog,ops,public,pg_temp as $$
declare v_actor text;
begin
  if session_user ~ '^carr_authority_' then
    v_actor:=regexp_replace(session_user,'^carr_authority_','');
  elsif session_user in ('carr_writer','carr_authority')
     or pg_has_role(session_user,'carr_writer','member')
     or pg_has_role(session_user,'carr_authority','member') then
    v_actor:=nullif(btrim(current_setting('carr.acting_actor_slug', true)), '');
  else
    raise exception 'tour mutation requires an authority connection or sponsored writer session';
  end if;
  if v_actor is null or v_actor !~ '^[A-Za-z0-9._:-]{1,160}$' then
    raise exception 'tour mutation has no server-derived actor';
  end if;
  return v_actor;
end $$;

revoke all on function ops.tour_server_actor_id() from public,carr_reader,carr_writer,carr_jobs,carr_authority;

-- Proof: the gate now admits a writer-bundle member and still refuses a
-- session outside both bundles.
do $proof$
declare def text;
begin
  def:=pg_get_functiondef('ops.tour_server_actor_id()'::regprocedure);
  if def not like '%pg_has_role(session_user,''carr_writer'',''member'')%'
     or def not like '%acting_actor_slug%'
     or def not like '%^carr_authority_%' then
    raise exception '0571 proof: tour_server_actor_id does not carry the membership branch';
  end if;
  if exists (select 1 from pg_roles where rolname='app_writer')
     and not pg_has_role('app_writer','carr_writer','member') then
    raise exception '0571 proof: app_writer is not a carr_writer member on this database';
  end if;
end $proof$;

