-- rollback: forward-only — retiring the service keeps its row and observations as history; a successor migration can clear retired_at.
-- Retain the service and its observations as history, outside active health.
update ops.service
   set retired_at = coalesce(retired_at, now()), updated_at = now()
 where key = 'rules-refresh';
