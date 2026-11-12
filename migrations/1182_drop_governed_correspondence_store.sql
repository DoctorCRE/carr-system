-- Retire the unused V5-J103 correspondence store after app caller removal.
-- rollback: forward-only; restore from 0700 and a verified backup if required.
-- expand-contract: contract - app callers removed in doctorcre-app PR 872.
-- lock-review: empty store tables only; refuse after five seconds of lock wait.
set local lock_timeout='5s';
set local statement_timeout='30s';

lock table ops.correspondence_adapter_consent,
  ops.correspondence_adapter_consent_revocation,
  ops.correspondence_adapter_read_receipt,
  ops.correspondence_draft in access exclusive mode;

do $empty_store$
begin
  if exists(select 1 from ops.correspondence_adapter_consent)
    or exists(select 1 from ops.correspondence_adapter_consent_revocation)
    or exists(select 1 from ops.correspondence_adapter_read_receipt)
    or exists(select 1 from ops.correspondence_draft) then
    raise exception 'correspondence store is no longer empty; retirement requires investigation';
  end if;
end $empty_store$;

do $retire_correspondence_grants$
declare
  original jsonb := ops.scac_current_grant_contract();
  retired jsonb := $reviewed$[{"ingress_key":"db-function-acl:ops.correspondence_readiness():carr_authority:execute","ingress_kind":"db_function_acl","signature":"ops.correspondence_readiness()","security_definer":true,"function_kind":"f","volatility":"s","parallel":"u","config":["search_path=pg_catalog, ops, pg_temp"],"grantee":"carr_authority","privilege":"execute","grantable":false},{"ingress_key":"db-function-acl:ops.correspondence_readiness():carr_reader:execute","ingress_kind":"db_function_acl","signature":"ops.correspondence_readiness()","security_definer":true,"function_kind":"f","volatility":"s","parallel":"u","config":["search_path=pg_catalog, ops, pg_temp"],"grantee":"carr_reader","privilege":"execute","grantable":false},{"ingress_key":"db-function-acl:ops.correspondence_readiness():carr_writer:execute","ingress_kind":"db_function_acl","signature":"ops.correspondence_readiness()","security_definer":true,"function_kind":"f","volatility":"s","parallel":"u","config":["search_path=pg_catalog, ops, pg_temp"],"grantee":"carr_writer","privilege":"execute","grantable":false},{"ingress_key":"db-function-acl:ops.correspondence_record_adapter_consent(p_partner_slug text, p_adapter_kind text, p_account_digest text, p_read_operations text[], p_human_quote text, p_idempotency_key uuid):carr_writer:execute","ingress_kind":"db_function_acl","signature":"ops.correspondence_record_adapter_consent(p_partner_slug text, p_adapter_kind text, p_account_digest text, p_read_operations text[], p_human_quote text, p_idempotency_key uuid)","security_definer":true,"function_kind":"f","volatility":"v","parallel":"u","config":["search_path=pg_catalog, ops, public, pg_temp"],"grantee":"carr_writer","privilege":"execute","grantable":false},{"ingress_key":"db-function-acl:ops.correspondence_record_draft(p_read_receipt_id uuid, p_draft_kind text, p_intended_participant_refs text[], p_draft_body text, p_idempotency_key uuid):carr_writer:execute","ingress_kind":"db_function_acl","signature":"ops.correspondence_record_draft(p_read_receipt_id uuid, p_draft_kind text, p_intended_participant_refs text[], p_draft_body text, p_idempotency_key uuid)","security_definer":true,"function_kind":"f","volatility":"v","parallel":"u","config":["search_path=pg_catalog, ops, public, pg_temp"],"grantee":"carr_writer","privilege":"execute","grantable":false},{"ingress_key":"db-function-acl:ops.correspondence_revoke_adapter_consent(p_consent_id uuid, p_human_quote text, p_idempotency_key uuid):carr_writer:execute","ingress_kind":"db_function_acl","signature":"ops.correspondence_revoke_adapter_consent(p_consent_id uuid, p_human_quote text, p_idempotency_key uuid)","security_definer":true,"function_kind":"f","volatility":"v","parallel":"u","config":["search_path=pg_catalog, ops, public, pg_temp"],"grantee":"carr_writer","privilege":"execute","grantable":false},{"ingress_key":"db-function-acl:ops.correspondence_thread_readback(p_source_system text, p_native_id text, p_native_id_epoch integer):carr_authority:execute","ingress_kind":"db_function_acl","signature":"ops.correspondence_thread_readback(p_source_system text, p_native_id text, p_native_id_epoch integer)","security_definer":true,"function_kind":"f","volatility":"s","parallel":"u","config":["search_path=pg_catalog, ops, pg_temp"],"grantee":"carr_authority","privilege":"execute","grantable":false},{"ingress_key":"db-function-acl:ops.correspondence_thread_readback(p_source_system text, p_native_id text, p_native_id_epoch integer):carr_reader:execute","ingress_kind":"db_function_acl","signature":"ops.correspondence_thread_readback(p_source_system text, p_native_id text, p_native_id_epoch integer)","security_definer":true,"function_kind":"f","volatility":"s","parallel":"u","config":["search_path=pg_catalog, ops, pg_temp"],"grantee":"carr_reader","privilege":"execute","grantable":false},{"ingress_key":"db-function-acl:ops.correspondence_thread_readback(p_source_system text, p_native_id text, p_native_id_epoch integer):carr_writer:execute","ingress_kind":"db_function_acl","signature":"ops.correspondence_thread_readback(p_source_system text, p_native_id text, p_native_id_epoch integer)","security_definer":true,"function_kind":"f","volatility":"s","parallel":"u","config":["search_path=pg_catalog, ops, pg_temp"],"grantee":"carr_writer","privilege":"execute","grantable":false}]$reviewed$::jsonb;
  live jsonb;
begin
  if ops.scac_mutation_catalog_v118_current() is not true then
    raise exception 'SCAC catalog is not current before correspondence retirement';
  end if;
  with recursive connected(oid) as (
    select oid from pg_roles where rolname ~ '^carr_' and rolname <> 'carr_ci'
    union select other.oid from connected c
    join pg_auth_members m on m.roleid=c.oid or m.member=c.oid
    join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end
    where other.rolname <> 'carr_ci' and not other.rolsuper
  ), runtime_roles as (
    select r.oid from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper
  ), functions as (
    select p.*,n.nspname,pg_get_function_identity_arguments(p.oid) args
    from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='ops' and p.proname in (
      'correspondence_readiness','correspondence_thread_readback',
      'correspondence_record_adapter_consent','correspondence_revoke_adapter_consent',
      'correspondence_record_draft')
  ), observed as (
    select 'db-function-acl:'||f.nspname||'.'||f.proname||'('||f.args||'):'||coalesce(r.rolname,'public')||':execute' ingress_key,
      jsonb_build_object(
        'ingress_key','db-function-acl:'||f.nspname||'.'||f.proname||'('||f.args||'):'||coalesce(r.rolname,'public')||':execute',
        'ingress_kind','db_function_acl','signature',f.nspname||'.'||f.proname||'('||f.args||')',
        'security_definer',f.prosecdef,'function_kind',f.prokind,'volatility',f.provolatile,
        'parallel',f.proparallel,'config',coalesce(to_jsonb(f.proconfig),'[]'::jsonb),
        'grantee',coalesce(r.rolname,'public'),'privilege','execute','grantable',acl.is_grantable) row
    from functions f cross join lateral aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) acl
    left join pg_roles r on r.oid=acl.grantee
    where f.prosecdef and acl.privilege_type='EXECUTE' and acl.grantee<>f.proowner
      and (acl.grantee=0 or r.oid in(select oid from runtime_roles))
  ) select coalesce(jsonb_agg(row order by ingress_key collate "C"),'[]'::jsonb) into live from observed;
  if live is distinct from retired then
    raise exception 'Correspondence grants differ from the reviewed retirement list';
  end if;
  if exists(select 1 from jsonb_array_elements(coalesce(original->'retired_secdef_execute','[]'::jsonb)) old,
      jsonb_array_elements(retired) new where old->>'ingress_key'=new->>'ingress_key') then
    raise exception 'A correspondence grant is already retired';
  end if;
  execute format('CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb '
    'LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'select '||quote_literal(jsonb_set(original,'{retired_secdef_execute}',
      coalesce(original->'retired_secdef_execute','[]'::jsonb)||retired)::text)||'::jsonb');
end $retire_correspondence_grants$;

drop function ops.correspondence_consent_in_force(uuid),
  ops.correspondence_record_adapter_consent(text,text,text,text[],text,uuid),
  ops.correspondence_revoke_adapter_consent(uuid,text,uuid),
  ops.correspondence_record_read_receipt(uuid,text,text,integer,jsonb,uuid),
  ops.correspondence_record_draft(uuid,text,text[],text,uuid),
  ops.correspondence_readiness(),
  ops.correspondence_thread_readback(text,text,integer);

drop table ops.correspondence_draft,
  ops.correspondence_adapter_read_receipt,
  ops.correspondence_adapter_consent_revocation,
  ops.correspondence_adapter_consent;

drop function ops.correspondence_tenant(), ops.correspondence_partners(),
  ops.correspondence_adapter_kinds(), ops.correspondence_read_operations(),
  ops.correspondence_draft_kinds(), ops.correspondence_is_sha256_ref(text),
  ops.correspondence_is_ref(text), ops.correspondence_refs_valid(text[]),
  ops.correspondence_has_routable_address(text),
  ops.correspondence_has_dialable_number(text), ops.correspondence_server_instant(),
  ops.correspondence_digest(jsonb), ops.correspondence_partner_owns_account(text,text),
  ops.correspondence_metadata_without_message_ids(jsonb),
  ops.correspondence_rows_immutable();

-- Historical SCAC successor seals remain immutable audit evidence.
-- Current source contracts are regenerated without the retired verbs.

do $catalog_proof$
begin
  if ops.scac_mutation_catalog_v118_current() is not true then
    raise exception 'SCAC catalog is not current after correspondence retirement';
  end if;
end $catalog_proof$;
