-- rollback: forward-only; remove history cursors from the source contract before retiring these views.
-- lock-review: create two reader views and grants; no business rows are changed or locked.

create view v_lead_workspace_correspondence as
select a.id,a.lead_id,a.kind,a.occurred_at,a.summary,a.detail,a.source,a.connected
from activity a join v_lead_workspace b on b.id=a.lead_id;

create view v_lead_workspace_stage_history as
select e.* from v_lead_stage_transition e join v_lead_workspace b on b.id=e.lead_id;

grant select on v_lead_workspace_correspondence,v_lead_workspace_stage_history
  to carr_reader,carr_writer,carr_authority;
