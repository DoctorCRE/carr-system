-- 0425_disable_legacy_schedule_readback_grant.sql
--
-- THE VERB THAT RETIRES A LEGACY SCHEDULE COULD NOT READ ITS OWN RECEIPT.
-- disable-legacy-schedule is the authority readback that finishes a scheduler
-- cutover: it calls ops.disable_legacy_schedule (security definer, so the
-- INSERT itself was always fine) and then reads the stored row back so the
-- event it writes names that row rather than a workflow key. 0176 granted
-- select on ops.legacy_schedule_disable_receipt to carr_jobs and carr_reader
-- and to nobody else, so the authority connection — the only role permitted to
-- execute the function — was refused the very row it had just created:
--   permission denied for table legacy_schedule_disable_receipt (42501)
--
-- Measured 2026-08-28 on the first real switch-off ever attempted, retiring the
-- duplicate com.carr.calendar-eventkit agent after calendar-fetch-daily earned
-- accepted shadow and canary evidence and had been dispatching live for a day.
-- The readback was added when the verb's event subject was corrected; the grant
-- that the new read requires was not added with it, which is the shape rule
-- 5409731b names: a change to what a verb touches changes the permission
-- surface of every table it now reads, and each one has to be checked.
--
-- SELECT ONLY, AND ONLY FOR THE ROLE THAT ALREADY OWNS THE ACT. carr_authority
-- may already execute the function that writes this table; letting it read the
-- receipt back grants no new power and is exactly what a readback verb is for.
-- Insert, update and delete stay revoked from every runtime role: the row is
-- still written only through the definer function, and the append-only trigger
-- from 0176 still governs it.

begin;

grant select on ops.legacy_schedule_disable_receipt to carr_authority;

do $$
begin
  if not has_table_privilege('carr_authority','ops.legacy_schedule_disable_receipt','select') then
    raise exception '0425 FAILED: the authority role still cannot read the disable receipt';
  end if;
  if has_table_privilege('carr_authority','ops.legacy_schedule_disable_receipt','insert')
     or has_table_privilege('carr_authority','ops.legacy_schedule_disable_receipt','update')
     or has_table_privilege('carr_authority','ops.legacy_schedule_disable_receipt','delete') then
    raise exception '0425 FAILED: the readback grant widened beyond select';
  end if;
  if has_table_privilege('carr_writer','ops.legacy_schedule_disable_receipt','select') then
    raise exception '0425 FAILED: the routine writer gained a read it never had';
  end if;
end $$;

commit;
