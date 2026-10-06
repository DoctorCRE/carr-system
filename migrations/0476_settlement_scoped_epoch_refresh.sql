-- 0476 — one epoch computation per settlement, not one per queued row.
--
-- ops.scac_policy_epoch_refresh() is wired as a DEFERRABLE INITIALLY DEFERRED
-- constraint trigger FOR EACH ROW on 18 doctrine/rule/schema tables (0455
-- lines 447-464; Postgres does not allow per-statement constraint triggers,
-- which is why they are per-row). Bulk writes therefore queue one firing per
-- row, and every firing recomputes the full policy snapshot — including the
-- v10 mutation-catalog scans over pg_proc/pg_class/pg_attribute with the
-- recursive carr_* role closure — before discovering the digest is unchanged
-- and exiting. That is quadratic work at settlement, measured at 322s of
-- "set constraints all immediate" in one CI migration-class run (out/ci-profile,
-- 2026-09-01) and responsible for the class growing 77s -> 652s in one day.
--
-- This replaces ONLY the trigger function body with a settlement-scoped
-- dedupe marker (transaction-local GUC keyed on txid + statement_timestamp).
-- Signature, security definer, search_path, volatility, grants and the
-- epoch-chain semantics are unchanged, so no sealed catalog digest moves.
-- One epoch row is still appended per settlement that changed the source —
-- exactly what the per-row queue already produced, minus the redundant
-- recomputations.

create or replace function ops.scac_policy_epoch_refresh() returns trigger
language plpgsql security definer set search_path=pg_catalog,public,ops as $fn$
declare source jsonb; source_digest text; prior ops.scac_policy_epoch%rowtype; next_epoch bigint; created timestamptz:=clock_timestamp(); epoch_digest text; prefix_digest text; bootstrap_health record; settlement_key text;
begin
  -- SETTLEMENT DEDUPE (0476). These are per-row deferred constraint triggers on
  -- 18 tables, so one settlement ("set constraints all immediate", or commit)
  -- fires this function once per queued row. Every firing in a settlement sees
  -- the same final table state: the snapshot below is identical each time, and
  -- every firing after the first exits at the source_digest early-return — but
  -- only AFTER paying for ops.scac_policy_epoch_snapshot(), whose catalog scans
  -- cost ~0.7s each since the v10 registry. Seeding the reviewed rule
  -- projection queues ~480 rows: 480 firings x 0.7s took the CI migration
  -- class from 77s to 652s on 2026-09-01 (hosted runs 33451887858 vs
  -- 33572556612; the db-gate-timing line names siep12/siep18 at ~165s each).
  -- The key is txid + statement_timestamp: constant across one settlement's
  -- firings, different for any later settlement in the same transaction, and
  -- set_config(...,true) is transaction-local so nothing leaks across
  -- transactions. Known edge, accepted and fail-closed: code that settles
  -- twice INSIDE one statement with relevant writes between the settlements
  -- would skip the second append; the chain-state check then reports the
  -- epoch stale/incompatible (visible refusal, not silent corruption) and the
  -- next settlement in any later statement appends the missed epoch. No such
  -- caller exists in this repository today.
  settlement_key:=txid_current()::text||':'||extract(epoch from statement_timestamp())::text;
  if current_setting('carr.scac_epoch_settled',true)=settlement_key then return null; end if;
  perform pg_advisory_xact_lock(hashtextextended('carr-internal:scac-core:policy-epoch',0));
  select * into prior from ops.scac_policy_epoch order by epoch desc limit 1 for update;
  if prior.epoch is null then
    select * into bootstrap_health from ops.rule_delivery_audit_counts(35);
    if bootstrap_health.total=0 and bootstrap_health.packs=0 and bootstrap_health.untagged=0
       and bootstrap_health.orphaned=0 and bootstrap_health.wildcarded=0
       and bootstrap_health.packless=0 and bootstrap_health.emptypack=0
       and bootstrap_health.scope_mismatch=0 and bootstrap_health.mode='shadow' then
      -- A freshly reconstructed database has no rule source until the canonical
      -- control-plane sync.  Keep the epoch absent (therefore incompatible)
      -- rather than blessing an empty policy.  The sync's deferred triggers
      -- bootstrap epoch 1 from its final coherent projection.
      perform set_config('carr.scac_epoch_settled',settlement_key,true);
      return null;
    end if;
  end if;
  source:=ops.scac_policy_epoch_snapshot();
  source_digest:='sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(source),'UTF8'),'sha256'),'hex');
  if prior.epoch is not null and prior.source_digest=source_digest then
    perform set_config('carr.scac_epoch_settled',settlement_key,true);
    return null;
  end if;
  if prior.epoch is not null then
    if (source->>'doctrine_generation')::bigint<prior.doctrine_generation then raise exception 'doctrine generation rollback refused'; end if;
    if (source->>'schema_applied_count')::integer<prior.schema_applied_count then raise exception 'schema ledger rollback refused'; end if;
    select 'sha256:'||encode(public.digest(coalesce(string_agg(convert_to(filename,'UTF8')||decode('00','hex')||convert_to(sha256,'UTF8')||decode('0a','hex'),''::bytea order by filename collate "C"),''::bytea),'sha256'),'hex') into prefix_digest
      from (select filename,sha256 from public.schema_migrations order by filename collate "C" limit prior.schema_applied_count) prefix;
    if prefix_digest is distinct from prior.schema_ledger_digest then raise exception 'schema ledger prefix rewrite refused'; end if;
  end if;
  next_epoch:=coalesce(prior.epoch,0)+1;
  epoch_digest:=ops.scac_policy_epoch_digest(next_epoch,coalesce(prior.epoch,0),coalesce(prior.epoch_digest,'bootstrap'),source_digest,created);
  insert into ops.scac_policy_epoch(epoch,epoch_digest,previous_epoch,previous_epoch_digest,program_key,tenant_scope,policy_domain,
    registry_version,registry_digest,doctrine_generation,doctrine_projection_digest,rule_projection_digest,schema_applied_count,schema_highest_migration,
    schema_ledger_digest,source_digest,source_session_user,source_relation,created_at)
  values(next_epoch,epoch_digest,prior.epoch,prior.epoch_digest,'carr-system-integrity-elimination-v1','carr-internal','scac-core',
    source->>'registry_version',source->>'registry_digest',(source->>'doctrine_generation')::bigint,source->>'doctrine_projection_digest',source->>'rule_projection_digest',
    (source->>'schema_applied_count')::integer,source->>'schema_highest_migration',source->>'schema_ledger_digest',source_digest,
    session_user,TG_TABLE_SCHEMA||'.'||TG_TABLE_NAME,created);
  perform set_config('carr.scac_epoch_settled',settlement_key,true);
  return null;
end $fn$;
