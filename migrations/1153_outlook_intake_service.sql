-- rollback: forward-only; retain the service identity and its run history.
-- expand-contract: expand; register the existing intake recorder service.
-- Project ops/config/services.json through deployment, as 0788 does for watchdog.
insert into ops.service
  (key,name,purpose,family,criticality,owner_actor,repo_path,runtime,retired_at)
values
  ('outlook-intake',
   'Outlook mail and calendar intake',
   'Every 30 minutes from 07:00 to 19:00, decodes a private copy of the new Outlook''s local store with the pinned hxprobe fork and logs new email (full plain-text body), past meetings and next-seven-day meetings for exactly matched CARR records through log-activity and set-next-action, with one isolated haiku call per new matched email for its next step and one read per unresolved inbound email from an unknown sender proposing what it applies to, queued on the claim card for a partner to confirm. After intake releases its lock, the same invocation runs the WR-000441 email router (lib/email_router.py) under its own lock and receipt for at most 20 minutes, storing phase-move and next-step proposals for Joe to confirm. Never creates, merges or renames records; never sends anything.',
   'Local Mac edge',
   'medium',
   'joe',
   'lib/outlook_intake.py',
   'launchd',null)
on conflict (key) do update set
  name=excluded.name,
  purpose=excluded.purpose,
  family=excluded.family,
  criticality=excluded.criticality,
  owner_actor=excluded.owner_actor,
  repo_path=excluded.repo_path,
  runtime=excluded.runtime,
  retired_at=null,updated_at=now();

insert into ops.service_environment
  (service_id,environment,deploy_mechanism,expected_cadence_seconds,cadence_grace_seconds,notes)
select id,'production',
  'ops/launchd/com.carr.outlook-intake.plist',
  1800,
  45000,
  'The current deployment is primary; ~/.config/carr/outlook.json supplies the enabled services, profile, owner and credential file pointers. Fires every 30 minutes from 07:00 to 19:00 local and never overnight, so the record layer can sleep; the 07:00 run catches up from the high-water mark. The grace spans the honest worst case the way rules-refresh''s does: the twelve-hour overnight stand-down from 19:00 to 07:00 plus up to 1800s for the succeeded row to leave the local spool. An unchanged store with no hydration due copies and decodes nothing and makes no record-layer call; its succeeded or skipped row rides the local spool (tools/ops-spool.py) to the next com.carr.run-spool-flush, while a failed row is written directly. Run rows carry counts only; the report and state stay private in out/outlook-intake/. Installed by bin/install-outlook-intake.sh.'
from ops.service where key='outlook-intake'
on conflict (service_id,environment) do update set
  deploy_mechanism=excluded.deploy_mechanism,
  expected_cadence_seconds=excluded.expected_cadence_seconds,
  cadence_grace_seconds=excluded.cadence_grace_seconds,
  notes=excluded.notes,updated_at=now();
