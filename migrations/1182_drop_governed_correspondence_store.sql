-- Retire the unused V5-J103 correspondence store after app caller removal.
-- rollback: forward-only; restore from 0700 and a verified backup if required.
-- expand-contract: contract - app callers removed in doctorcre-app PR 872.
-- lock-review: empty store tables only; refuse after five seconds of lock wait.
set local lock_timeout='5s';
set local statement_timeout='30s';

do $empty_store$
begin
  if exists(select 1 from ops.correspondence_adapter_consent)
    or exists(select 1 from ops.correspondence_adapter_consent_revocation)
    or exists(select 1 from ops.correspondence_adapter_read_receipt)
    or exists(select 1 from ops.correspondence_draft) then
    raise exception 'correspondence store is no longer empty; retirement requires investigation';
  end if;
end $empty_store$;

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
