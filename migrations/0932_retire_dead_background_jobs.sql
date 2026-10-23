-- rollback: forward-only; restore source declarations before a successor clears retired_at.
-- expand-contract: contract; Joe approved removing these four background jobs on 2026-10-22.
-- lock-review: bounded updates to four services and three production environments; retain all run history.
update ops.service
   set retired_at = now(), updated_at = now()
 where key in ('calendar-prebrief-joe', 'fix-train', 'keepalive-probe', 'cc-version-sentinel')
   and retired_at is null;

update ops.service_environment se
   set expected_cadence_seconds = null,
       cadence_grace_seconds = 0,
       notes = 'launchd KeepAlive restarts the server. Scheduled-job health reads launchd registration and last exit status.',
       updated_at = now()
  from ops.service s
 where se.service_id = s.id
   and se.environment = 'production'
   and s.key in ('call-mode', 'doc-engine', 'quill-dictate');
