-- Read support for the system-work census. No mutation door or parallel board.
CREATE VIEW ops.system_work_actor_scope AS SELECT id,slug FROM public.actor;
CREATE VIEW ops.system_work_rule_scope AS SELECT id,personal_to FROM public.rule;
CREATE VIEW ops.system_work_plan_scope AS SELECT id,plan_hash FROM ops.sourced_work_request_plan;
REVOKE ALL ON ops.system_work_actor_scope,ops.system_work_rule_scope,ops.system_work_plan_scope FROM PUBLIC;
GRANT SELECT ON ops.system_work_actor_scope,ops.system_work_rule_scope,ops.system_work_plan_scope TO carr_reader;
GRANT SELECT ON TABLE public.investigation_run TO carr_reader;
GRANT SELECT ON TABLE public.retrieval_proposal TO carr_reader;
GRANT SELECT ON TABLE ops.ready_plan_amendment TO carr_reader;
GRANT SELECT ON TABLE ops.ready_plan_amendment_acceptance_receipt TO carr_reader;
