-- The watchdog is a launchd service, independent of the sealed MCP frontier.
-- Register its live physical authority without changing historical seals.
insert into ops.service
  (key,name,purpose,family,criticality,owner_actor,repo_path,runtime,retired_at)
values
  ('job-watchdog','Job watchdog',
   'Scan registered jobs, head-bound pull requests, merge-queue failures and release logs; report findings to the orchestrator and dispatch deterministic recovery actions.',
   'Local Mac edge','medium','joe','tools/job-watchdog.py','launchd',null)
on conflict (key) do update set
  name=excluded.name,purpose=excluded.purpose,family=excluded.family,
  criticality=excluded.criticality,owner_actor=excluded.owner_actor,
  repo_path=excluded.repo_path,runtime=excluded.runtime,retired_at=null,updated_at=now();

insert into ops.service_environment
  (service_id,environment,deploy_mechanism,expected_cadence_seconds,cadence_grace_seconds,notes)
select id,'production','ops/launchd/com.carr.job-watchdog.plist',120,300,
  'Primary-only schedule. Cadence is a checked projection of ops/config/job-watchdog.json. This draft ships source definitions; the orchestrator owns activation and replacement of the scratch auto-enqueue process. Findings and dated scan completion ledger are durable; scanner makes no model judgments.'
from ops.service where key='job-watchdog'
on conflict (service_id,environment) do update set
  deploy_mechanism=excluded.deploy_mechanism,
  expected_cadence_seconds=excluded.expected_cadence_seconds,
  cadence_grace_seconds=excluded.cadence_grace_seconds,notes=excluded.notes,updated_at=now();
