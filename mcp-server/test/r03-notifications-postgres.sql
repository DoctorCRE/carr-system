-- WR-000113 — the measured grant, recipient and status proof for the R03
-- notification store. Run by ops/ci.sh's migration class.
--
-- WHAT ONLY A DATABASE CAN SHOW, and why each case exists:
--   * R03-MINT-GRANT: the mint really does execute as carr_writer -- the
--     identity the ONE production caller arrives on -- and really does refuse
--     every other bundle. An authority-only grant would raise permission denied
--     at record-signal and make three criteria unprovable.
--   * R03-RECIPIENT-RESOLVER: the ninth argument is a SLUG resolved here against
--     ACTIVE HUMAN rows only, so a caller cannot name an arbitrary actor, and a
--     slug that resolves to nothing is a returned NO-OP rather than a raise.
--   * R03-STATUS-DISTINCT: no runtime bundle holds UPDATE on ops.notification,
--     so an acknowledgement cannot move a notification or a task even by mistake.

\set ON_ERROR_STOP on

do $wr113_grants$
declare v_mint text := 'ops.mint_notification(text,uuid,text,text,text,text,text,text,text,boolean,boolean)';
        v_feed text := 'ops.notification_feed_facts(timestamptz,integer)';
        v_ack  text := 'ops.acknowledge_notification(uuid,uuid)';
        v_bad text;
begin
  -- NINE argument types. A stale eight-type signature would revoke and grant
  -- NOTHING and leave the real function carrying public's default execute, so
  -- this lookup failing at all is itself the finding.
  if to_regprocedure(v_mint) is null then
    raise exception 'WR-000113: ops.mint_notification does not exist with NINE arguments';
  end if;

  for v_bad in
    select fn from unnest(array[v_mint, v_feed, v_ack]) fn
    where not has_function_privilege('carr_writer', fn, 'execute')
       or not has_function_privilege('carr_authority', fn, 'execute')
       or has_function_privilege('carr_reader', fn, 'execute')
       or has_function_privilege('carr_jobs', fn, 'execute')
       or has_function_privilege('public', fn, 'execute')
  loop
    raise exception 'WR-000113 grant boundary: % does not reach exactly writer and authority', v_bad;
  end loop;

  -- carr_reader holds NOTHING on the four relations: the feed function is the
  -- only door, and a row a reader could select around it would defeat the
  -- per-recipient scoping computed inside it.
  for v_bad in
    select rel from unnest(array['ops.notification','ops.notification_delivery',
      'ops.notification_read','ops.notification_preference']) rel
    where has_table_privilege('carr_reader', rel, 'select')
  loop
    raise exception 'WR-000113: carr_reader can read % around the feed function', v_bad;
  end loop;

  -- R03-STATUS-DISTINCT, enforced by the ABSENCE of a grant.
  for v_bad in
    select rel from unnest(array['ops.notification','ops.notification_delivery',
      'ops.notification_read','ops.notification_preference']) rel
    cross join unnest(array['carr_reader','carr_writer','carr_jobs','carr_authority','public']) grantee
    cross join unnest(array['insert','update','delete','truncate']) priv
    where has_table_privilege(grantee, rel, priv)
  loop
    raise exception 'WR-000113: % is directly writable by a runtime bundle', v_bad;
  end loop;
end $wr113_grants$;

-- A source row the mint can name and verify, written by the owner before the
-- role switch below.
do $wr113_fixture$
begin
  insert into public.signal_event(producer, signal_key, signal_kind, subject_type, subject_ref,
    metric_name, observed_value, threshold_value, comparison, severity, detected_at, evidence_refs,
    payload, created_by)
  select 'wr113-postgres-proof', 'resolver-case', 'budget_threshold', 'deal', 'deal-proof',
    'spend_units', 120, 100, 'gt', 'critical', now(), '["evidence:one"]'::jsonb, '{}'::jsonb, a.id
    from public.actor a where a.slug = 'joe'
  on conflict (producer, signal_key) do nothing;
end $wr113_fixture$;

-- R03-MINT-GRANT and R03-RECIPIENT-RESOLVER, on the WRITER bundle -- the
-- identity mcp.js hands record-signal.
set role carr_writer;

do $wr113_resolver$
declare v_signal uuid; v_joe uuid; v_result jsonb; v_recipient uuid; v_count integer;
begin
  select id into v_signal from public.signal_event
   where producer = 'wr113-postgres-proof' and signal_key = 'resolver-case';
  select id into v_joe from public.actor where slug = 'joe' and kind = 'human' and active;

  -- The partner slug resolves to the partner row.
  v_result := ops.mint_notification('signal_event', v_signal, 'deal', 'deal-proof',
    'spend crossed its threshold', 'failure', '/signals/proof', 'signal:wr113:resolver', 'joe');
  if not (v_result->>'ok')::boolean or not (v_result->>'minted')::boolean then
    raise exception 'WR-000113 resolver: the writer bundle could not mint: %', v_result;
  end if;
  select recipient_actor into v_recipient from ops.notification
   where id = (v_result->>'notification_id')::uuid;
  if v_recipient is distinct from v_joe then
    raise exception 'WR-000113 resolver: the recipient is not the active human partner row';
  end if;

  -- A real slug that is NOT kind='human': a returned NO-OP, never a raise and
  -- never a substituted partner.
  v_result := ops.mint_notification('signal_event', v_signal, 'deal', 'deal-proof',
    'spend crossed its threshold', 'failure', '/signals/proof', 'signal:wr113:not-human', 'dell-local');
  if (v_result->>'minted')::boolean or v_result->>'reason_id' <> 'no_sponsoring_partner' then
    raise exception 'WR-000113 resolver: a non-human slug was resolved: %', v_result;
  end if;

  -- A slug that names nothing at all: the same answer.
  v_result := ops.mint_notification('signal_event', v_signal, 'deal', 'deal-proof',
    'spend crossed its threshold', 'failure', '/signals/proof', 'signal:wr113:nobody', 'nobody-at-all');
  if (v_result->>'minted')::boolean or v_result->>'reason_id' <> 'no_sponsoring_partner' then
    raise exception 'WR-000113 resolver: an unknown slug was resolved: %', v_result;
  end if;

  select count(*) into v_count from ops.notification
   where dedupe_key in ('signal:wr113:not-human', 'signal:wr113:nobody');
  if v_count <> 0 then
    raise exception 'WR-000113 resolver: % rows were written for an unresolved recipient', v_count;
  end if;

  -- A source row that does not exist is a RAISE, because the mint may never
  -- name an event it has not just proved to exist.
  begin
    perform ops.mint_notification('signal_event', gen_random_uuid(), 'deal', 'deal-proof',
      'invented', 'failure', '/signals/invented', 'signal:wr113:invented', 'joe');
    raise exception 'WR-000113 resolver: the mint accepted an event that does not exist';
  exception when foreign_key_violation then
    if position('notification_requires_an_existing_event' in sqlerrm) = 0 then raise; end if;
  end;
end $wr113_resolver$;

reset role;

-- The other side of R03-MINT-GRANT: every bundle the grant does not name is
-- refused, and the refusal is a permission error rather than a quiet no-op.
set role carr_reader;
do $wr113_reader_refused$
begin
  perform ops.mint_notification('signal_event', gen_random_uuid(), 'deal', 'd',
    'r', 'failure', '/x', 'k', 'joe');
  raise exception 'WR-000113: carr_reader executed the mint';
exception when insufficient_privilege then
  null;
end $wr113_reader_refused$;
reset role;

set role carr_jobs;
do $wr113_jobs_refused$
begin
  perform ops.mint_notification('signal_event', gen_random_uuid(), 'deal', 'd',
    'r', 'failure', '/x', 'k', 'joe');
  raise exception 'WR-000113: carr_jobs executed the mint';
exception when insufficient_privilege then
  null;
end $wr113_jobs_refused$;
reset role;

-- The severity vocabulary has no informational value, and the deep link is a
-- relative path only. Both are column checks, so neither depends on discipline.
do $wr113_columns$
declare v_joe uuid;
begin
  select id into v_joe from public.actor where slug = 'joe' and kind = 'human' and active;
  begin
    insert into ops.notification(subject_type, subject_ref, event_ref, event_source,
      recipient_actor, reason, severity, deep_link, dedupe_key)
    values ('deal','d',gen_random_uuid(),'signal_event',v_joe,'r','info','/x','wr113:info');
    raise exception 'WR-000113: an info notification was stored';
  exception when check_violation then null;
  end;
  for v_joe in select v_joe loop end loop;
end $wr113_columns$;

do $wr113_deep_link$
declare v_joe uuid; v_link text;
begin
  select id into v_joe from public.actor where slug = 'joe' and kind = 'human' and active;
  foreach v_link in array array['https://evil.example/x', '/x?token=abc', 'relative', '//host/x']
  loop
    begin
      insert into ops.notification(subject_type, subject_ref, event_ref, event_source,
        recipient_actor, reason, severity, deep_link, dedupe_key)
      values ('deal','d',gen_random_uuid(),'signal_event',v_joe,'r','failure',v_link,'wr113:link:'||v_link);
      raise exception 'WR-000113: deep_link % was stored', v_link;
    exception when check_violation then null;
    end;
  end loop;
end $wr113_deep_link$;

select 'WR-000113 R03 notifications: mint reaches the writer bundle, recipient resolves to an active human, status boundary held' as proof;
