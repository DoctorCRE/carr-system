-- DoctorCRE v5 portfolio hierarchy: transaction-scoped PostgreSQL acceptance.
--
-- Every fixture row is rolled back and no portfolio is accepted by running this
-- file. The graph is SYNTHETIC on purpose: the real node budgets and model
-- floors do not exist in any authenticated source, and this proof is about the
-- mechanism, not about the real portfolio.
--
-- What it proves, none of which can be shown by reading SQL text:
--   * a proposal is inert and creates no job, envelope or capability session
--   * the stored graph, child and accepted digests must each equal the digest
--     recomputed from the persisted rows
--   * the accepted digest moves when a child version moves; the graph does not
--   * update and delete are refused, and after acceptance insert is too
--   * review refuses a stale digest and a proposer's self-pass
--
-- Digests are LEARNED in throwaway subtransactions. A PL/pgSQL exception block
-- is a subtransaction: its database writes roll back while the variables it
-- assigned survive, so the learned digest describes exactly the rows the real
-- proposal then carries.

\set ON_ERROR_STOP on

begin;

do $proof$
declare
  v_rev uuid; v_rev2 uuid;
  v_children jsonb; v_bindings jsonb;
  v_graph text; v_accepted text;
  v_graph2 text; v_graph3 text; v_accepted2 text; v_accepted3 text;
  v_jobs bigint; v_envelopes bigint; v_sessions bigint;
  v_nodes constant jsonb := '[{"node_ref":"step:synthetic-milestone-01","node_kind":"milestone","ordinal":1,"parent_ref":"WR-SYNTHETIC","child_ref":"foundation-and-control-plane","authority_class":"synthetic_authority","effect_class":"synthetic_no_effect","data_class":"synthetic_record_layer","budget_identity":"synthetic:budget-1","budget_ceiling":1000,"model_floor":{"provider":"synthetic","model":"synthetic","version":"1","effort":"high"},"recovery_ref":"recovery:synthetic-1","terminal_predicate":"synthetic terminal"},{"node_ref":"step:synthetic-milestone-02","node_kind":"milestone","ordinal":2,"parent_ref":"WR-SYNTHETIC","child_ref":"assurance-fabric","authority_class":"synthetic_authority","effect_class":"synthetic_no_effect","data_class":"synthetic_record_layer","budget_identity":"synthetic:budget-2","budget_ceiling":2000,"model_floor":{"provider":"synthetic","model":"synthetic","version":"1","effort":"high"},"recovery_ref":"recovery:synthetic-2","terminal_predicate":"synthetic terminal"},{"node_ref":"step:synthetic-milestone-03","node_kind":"milestone","ordinal":3,"parent_ref":"WR-SYNTHETIC","child_ref":"product-journeys","authority_class":"synthetic_authority","effect_class":"synthetic_no_effect","data_class":"synthetic_record_layer","budget_identity":"synthetic:budget-3","budget_ceiling":12.5,"model_floor":{"provider":"synthetic","model":"synthetic","version":"1","effort":"high"},"recovery_ref":"recovery:synthetic-3","terminal_predicate":"synthetic terminal"},{"node_ref":"step:synthetic-milestone-04","node_kind":"milestone","ordinal":4,"parent_ref":"WR-SYNTHETIC","child_ref":"rollout-and-retirement","authority_class":"synthetic_authority","effect_class":"synthetic_no_effect","data_class":"synthetic_record_layer","budget_identity":"synthetic:budget-4","budget_ceiling":4000,"model_floor":{"provider":"synthetic","model":"synthetic","version":"1","effort":"high"},"recovery_ref":"recovery:synthetic-4","terminal_predicate":"synthetic terminal"},{"node_ref":"step:synthetic-milestone-05","node_kind":"milestone","ordinal":5,"parent_ref":"WR-SYNTHETIC","child_ref":"foundation-and-control-plane","authority_class":"synthetic_authority","effect_class":"synthetic_no_effect","data_class":"synthetic_record_layer","budget_identity":"synthetic:budget-5","budget_ceiling":0.0001,"model_floor":{"provider":"synthetic","model":"synthetic","version":"1","effort":"high"},"recovery_ref":"recovery:synthetic-5","terminal_predicate":"synthetic terminal"},{"node_ref":"step:synthetic-milestone-06","node_kind":"milestone","ordinal":6,"parent_ref":"WR-SYNTHETIC","child_ref":"assurance-fabric","authority_class":"synthetic_authority","effect_class":"synthetic_no_effect","data_class":"synthetic_record_layer","budget_identity":"synthetic:budget-6","budget_ceiling":6000,"model_floor":{"provider":"synthetic","model":"synthetic","version":"1","effort":"high"},"recovery_ref":"recovery:synthetic-6","terminal_predicate":"synthetic terminal"},{"node_ref":"step:synthetic-milestone-07","node_kind":"milestone","ordinal":7,"parent_ref":"WR-SYNTHETIC","child_ref":"product-journeys","authority_class":"synthetic_authority","effect_class":"synthetic_no_effect","data_class":"synthetic_record_layer","budget_identity":"synthetic:budget-7","budget_ceiling":7000,"model_floor":{"provider":"synthetic","model":"synthetic","version":"1","effort":"high"},"recovery_ref":"recovery:synthetic-7","terminal_predicate":"synthetic terminal"},{"node_ref":"step:synthetic-milestone-08","node_kind":"milestone","ordinal":8,"parent_ref":"WR-SYNTHETIC","child_ref":"rollout-and-retirement","authority_class":"synthetic_authority","effect_class":"synthetic_no_effect","data_class":"synthetic_record_layer","budget_identity":"synthetic:budget-8","budget_ceiling":8000,"model_floor":{"provider":"synthetic","model":"synthetic","version":"1","effort":"high"},"recovery_ref":"recovery:synthetic-8","terminal_predicate":"synthetic terminal"},{"node_ref":"step:synthetic-milestone-09","node_kind":"milestone","ordinal":9,"parent_ref":"WR-SYNTHETIC","child_ref":"foundation-and-control-plane","authority_class":"synthetic_authority","effect_class":"synthetic_no_effect","data_class":"synthetic_record_layer","budget_identity":"synthetic:budget-9","budget_ceiling":9000,"model_floor":{"provider":"synthetic","model":"synthetic","version":"1","effort":"high"},"recovery_ref":"recovery:synthetic-9","terminal_predicate":"synthetic terminal"},{"node_ref":"step:synthetic-milestone-10","node_kind":"milestone","ordinal":10,"parent_ref":"WR-SYNTHETIC","child_ref":"assurance-fabric","authority_class":"synthetic_authority","effect_class":"synthetic_no_effect","data_class":"synthetic_record_layer","budget_identity":"synthetic:budget-10","budget_ceiling":10000,"model_floor":{"provider":"synthetic","model":"synthetic","version":"1","effort":"high"},"recovery_ref":"recovery:synthetic-10","terminal_predicate":"synthetic terminal"},{"node_ref":"step:synthetic-milestone-11","node_kind":"milestone","ordinal":11,"parent_ref":"WR-SYNTHETIC","child_ref":"product-journeys","authority_class":"synthetic_authority","effect_class":"synthetic_no_effect","data_class":"synthetic_record_layer","budget_identity":"synthetic:budget-11","budget_ceiling":11000,"model_floor":{"provider":"synthetic","model":"synthetic","version":"1","effort":"high"},"recovery_ref":"recovery:synthetic-11","terminal_predicate":"synthetic terminal"},{"node_ref":"step:synthetic-milestone-12","node_kind":"milestone","ordinal":12,"parent_ref":"WR-SYNTHETIC","child_ref":"rollout-and-retirement","authority_class":"synthetic_authority","effect_class":"synthetic_no_effect","data_class":"synthetic_record_layer","budget_identity":"synthetic:budget-12","budget_ceiling":12000,"model_floor":{"provider":"synthetic","model":"synthetic","version":"1","effort":"high"},"recovery_ref":"recovery:synthetic-12","terminal_predicate":"synthetic terminal"},{"node_ref":"step:synthetic-milestone-13","node_kind":"milestone","ordinal":13,"parent_ref":"WR-SYNTHETIC","child_ref":"foundation-and-control-plane","authority_class":"synthetic_authority","effect_class":"synthetic_no_effect","data_class":"synthetic_record_layer","budget_identity":"synthetic:budget-13","budget_ceiling":13000,"model_floor":{"provider":"synthetic","model":"synthetic","version":"1","effort":"high"},"recovery_ref":"recovery:synthetic-13","terminal_predicate":"synthetic terminal"},{"node_ref":"step:synthetic-milestone-14","node_kind":"milestone","ordinal":14,"parent_ref":"WR-SYNTHETIC","child_ref":"assurance-fabric","authority_class":"synthetic_authority","effect_class":"synthetic_no_effect","data_class":"synthetic_record_layer","budget_identity":"synthetic:budget-14","budget_ceiling":14000,"model_floor":{"provider":"synthetic","model":"synthetic","version":"1","effort":"high"},"recovery_ref":"recovery:synthetic-14","terminal_predicate":"synthetic terminal"},{"node_ref":"step:synthetic-milestone-15","node_kind":"milestone","ordinal":15,"parent_ref":"WR-SYNTHETIC","child_ref":"product-journeys","authority_class":"synthetic_authority","effect_class":"synthetic_no_effect","data_class":"synthetic_record_layer","budget_identity":"synthetic:budget-15","budget_ceiling":15000,"model_floor":{"provider":"synthetic","model":"synthetic","version":"1","effort":"high"},"recovery_ref":"recovery:synthetic-15","terminal_predicate":"synthetic terminal"},{"node_ref":"step:synthetic-milestone-16","node_kind":"milestone","ordinal":16,"parent_ref":"WR-SYNTHETIC","child_ref":"rollout-and-retirement","authority_class":"synthetic_authority","effect_class":"synthetic_no_effect","data_class":"synthetic_record_layer","budget_identity":"synthetic:budget-16","budget_ceiling":16000,"model_floor":{"provider":"synthetic","model":"synthetic","version":"1","effort":"high"},"recovery_ref":"recovery:synthetic-16","terminal_predicate":"synthetic terminal"},{"node_ref":"step:synthetic-milestone-17","node_kind":"milestone","ordinal":17,"parent_ref":"WR-SYNTHETIC","child_ref":"foundation-and-control-plane","authority_class":"synthetic_authority","effect_class":"synthetic_no_effect","data_class":"synthetic_record_layer","budget_identity":"synthetic:budget-17","budget_ceiling":17000,"model_floor":{"provider":"synthetic","model":"synthetic","version":"1","effort":"high"},"recovery_ref":"recovery:synthetic-17","terminal_predicate":"synthetic terminal"},{"node_ref":"step:synthetic-milestone-18","node_kind":"milestone","ordinal":18,"parent_ref":"WR-SYNTHETIC","child_ref":"assurance-fabric","authority_class":"synthetic_authority","effect_class":"synthetic_no_effect","data_class":"synthetic_record_layer","budget_identity":"synthetic:budget-18","budget_ceiling":18000,"model_floor":{"provider":"synthetic","model":"synthetic","version":"1","effort":"high"},"recovery_ref":"recovery:synthetic-18","terminal_predicate":"synthetic terminal"},{"node_ref":"step:synthetic-milestone-19","node_kind":"milestone","ordinal":19,"parent_ref":"WR-SYNTHETIC","child_ref":"product-journeys","authority_class":"synthetic_authority","effect_class":"synthetic_no_effect","data_class":"synthetic_record_layer","budget_identity":"synthetic:budget-19","budget_ceiling":19000,"model_floor":{"provider":"synthetic","model":"synthetic","version":"1","effort":"high"},"recovery_ref":"recovery:synthetic-19","terminal_predicate":"synthetic terminal"},{"node_ref":"step:synthetic-milestone-20","node_kind":"milestone","ordinal":20,"parent_ref":"WR-SYNTHETIC","child_ref":"rollout-and-retirement","authority_class":"synthetic_authority","effect_class":"synthetic_no_effect","data_class":"synthetic_record_layer","budget_identity":"synthetic:budget-20","budget_ceiling":20000,"model_floor":{"provider":"synthetic","model":"synthetic","version":"1","effort":"high"},"recovery_ref":"recovery:synthetic-20","terminal_predicate":"synthetic terminal"},{"node_ref":"step:synthetic-milestone-21","node_kind":"milestone","ordinal":21,"parent_ref":"WR-SYNTHETIC","child_ref":"foundation-and-control-plane","authority_class":"synthetic_authority","effect_class":"synthetic_no_effect","data_class":"synthetic_record_layer","budget_identity":"synthetic:budget-21","budget_ceiling":21000,"model_floor":{"provider":"synthetic","model":"synthetic","version":"1","effort":"high"},"recovery_ref":"recovery:synthetic-21","terminal_predicate":"synthetic terminal"}]'::jsonb;
  v_edges constant jsonb := '[{"from_node_ref":"step:synthetic-milestone-01","to_node_ref":"step:synthetic-milestone-02"},{"from_node_ref":"step:synthetic-milestone-02","to_node_ref":"step:synthetic-milestone-03"},{"from_node_ref":"step:synthetic-milestone-03","to_node_ref":"step:synthetic-milestone-04"},{"from_node_ref":"step:synthetic-milestone-04","to_node_ref":"step:synthetic-milestone-05"},{"from_node_ref":"step:synthetic-milestone-05","to_node_ref":"step:synthetic-milestone-06"},{"from_node_ref":"step:synthetic-milestone-06","to_node_ref":"step:synthetic-milestone-07"},{"from_node_ref":"step:synthetic-milestone-07","to_node_ref":"step:synthetic-milestone-08"},{"from_node_ref":"step:synthetic-milestone-08","to_node_ref":"step:synthetic-milestone-09"},{"from_node_ref":"step:synthetic-milestone-09","to_node_ref":"step:synthetic-milestone-10"},{"from_node_ref":"step:synthetic-milestone-10","to_node_ref":"step:synthetic-milestone-11"},{"from_node_ref":"step:synthetic-milestone-11","to_node_ref":"step:synthetic-milestone-12"},{"from_node_ref":"step:synthetic-milestone-12","to_node_ref":"step:synthetic-milestone-13"},{"from_node_ref":"step:synthetic-milestone-13","to_node_ref":"step:synthetic-milestone-14"},{"from_node_ref":"step:synthetic-milestone-14","to_node_ref":"step:synthetic-milestone-15"},{"from_node_ref":"step:synthetic-milestone-15","to_node_ref":"step:synthetic-milestone-16"},{"from_node_ref":"step:synthetic-milestone-16","to_node_ref":"step:synthetic-milestone-17"},{"from_node_ref":"step:synthetic-milestone-17","to_node_ref":"step:synthetic-milestone-18"},{"from_node_ref":"step:synthetic-milestone-18","to_node_ref":"step:synthetic-milestone-19"},{"from_node_ref":"step:synthetic-milestone-19","to_node_ref":"step:synthetic-milestone-20"},{"from_node_ref":"step:synthetic-milestone-20","to_node_ref":"step:synthetic-milestone-21"}]'::jsonb;
  v_sources constant jsonb := '{"constitution":"sha256:1111111111111111111111111111111111111111111111111111111111111111","design":"sha256:2222222222222222222222222222222222222222222222222222222222222222","integration":"sha256:3333333333333333333333333333333333333333333333333333333333333333","requirements":"sha256:4444444444444444444444444444444444444444444444444444444444444444"}'::jsonb;
  v_placeholder constant text := 'sha256:0000000000000000000000000000000000000000000000000000000000000000';
  v_learn constant text := 'work-portfolio-proof-learn-rollback';
  c text; v_err text; v_case record;
begin
  if not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                  where n.nspname = 'ops' and p.proname = 'portfolio_propose_revision') then
    raise notice 'SKIPPED: ops.portfolio_propose_revision is absent; migration 0496 has not been applied here yet.';
    return;
  end if;

  -- NUMBER CANONICALIZATION. These are the exact spellings JavaScript produces;
  -- an earlier draft refused the two exponent magnitudes outright, which
  -- narrowed the accepted budget domain instead of rendering it.
  for v_case in
    select * from (values
      ('0'::numeric, '0'), ('0.0000001', '1e-7'), ('0.00000015', '1.5e-7'),
      ('0.000001', '0.000001'), ('0.00001', '0.00001'), ('12.5', '12.5'),
      ('1000000000000', '1000000000000'), ('0.0001', '0.0001'), ('1', '1'),
      ('12.50', '12.5'), ('3.141592653589793', '3.141592653589793')
    ) as t(value, expected)
  loop
    if ops.portfolio_canonical_number_text(v_case.value) is distinct from v_case.expected then
      raise exception 'canonical number text for % is %, expected %',
        v_case.value, ops.portfolio_canonical_number_text(v_case.value), v_case.expected;
    end if;
    if not ops.portfolio_canonical_number_valid(v_case.value) then
      raise exception 'a value JavaScript can express was refused: %', v_case.value;
    end if;
  end loop;
  -- Precision a double cannot hold is still refused; that is a real boundary.
  if ops.portfolio_canonical_number_valid('3.14159265358979311599796346854'::numeric) then
    raise exception 'a value carrying more precision than a double was accepted';
  end if;

  select count(*) into v_jobs from ops.job;
  select count(*) into v_envelopes from ops.engineering_execution_envelope;
  select count(*) into v_sessions from ops.capability_agent_session;
  perform set_config('carr.acting_actor_slug', 'codex', true);

  v_children := '[]'::jsonb;
  foreach c in array array['foundation-and-control-plane','assurance-fabric','product-journeys','rollout-and-retirement'] loop
    v_children := v_children || jsonb_build_array(jsonb_build_object(
      'child_ref', c, 'child_ordinal', array_position(array['foundation-and-control-plane','assurance-fabric','product-journeys','rollout-and-retirement'], c) - 1,
      'child_version', 1, 'child_digest', v_placeholder, 'accepted_plan_ref', null));
  end loop;

  -- LEARN the graph digest and the four child digests.
  begin
    v_rev := ops.portfolio_propose_revision('WR-SYNTHETIC', 1, gen_random_uuid(), v_sources,
      v_placeholder, v_placeholder, v_children, v_nodes, v_edges);
    v_graph := ops.portfolio_graph_digest(v_rev);
    v_bindings := '[]'::jsonb;
    foreach c in array array['foundation-and-control-plane','assurance-fabric','product-journeys','rollout-and-retirement'] loop
      v_bindings := v_bindings || jsonb_build_array(jsonb_build_object(
        'child_ref', c, 'child_ordinal', array_position(array['foundation-and-control-plane','assurance-fabric','product-journeys','rollout-and-retirement'], c) - 1,
        'child_version', 1, 'child_digest', ops.portfolio_child_digest(v_rev, c),
        'accepted_plan_ref', null));
    end loop;
    raise exception '%', v_learn;
  exception when others then
    if sqlerrm <> v_learn then raise; end if;
  end;
  v_children := v_bindings;

  -- LEARN the accepted digest the completed rows produce.
  begin
    v_rev := ops.portfolio_propose_revision('WR-SYNTHETIC', 1, gen_random_uuid(), v_sources,
      v_graph, v_placeholder, v_children, v_nodes, v_edges);
    v_accepted := ops.portfolio_accepted_digest(v_rev);
    raise exception '%', v_learn;
  exception when others then
    if sqlerrm <> v_learn then raise; end if;
  end;

  -- A WRONG STORED GRAPH DIGEST IS REFUSED AT THE COMMIT CHECK.
  begin
    v_rev := ops.portfolio_propose_revision('WR-SYNTHETIC', 1, gen_random_uuid(), v_sources,
      v_placeholder, v_accepted, v_children, v_nodes, v_edges);
    set constraints all immediate;
    raise exception 'a proposal storing the wrong graph digest was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'graph digest does not match its rows' then raise; end if;
  end;
  set constraints all deferred;

  -- A WRONG STORED CHILD DIGEST IS REFUSED AT THE COMMIT CHECK.
  begin
    v_bindings := '[]'::jsonb;
    foreach c in array array['foundation-and-control-plane','assurance-fabric','product-journeys','rollout-and-retirement'] loop
      v_bindings := v_bindings || jsonb_build_array(jsonb_build_object(
        'child_ref', c,
        'child_ordinal', array_position(array['foundation-and-control-plane','assurance-fabric','product-journeys','rollout-and-retirement'], c) - 1,
        'child_version', 1,
        'child_digest', case when c = 'product-journeys' then v_placeholder
                        else (v_children -> (array_position(array['foundation-and-control-plane','assurance-fabric','product-journeys','rollout-and-retirement'], c) - 1) ->> 'child_digest') end,
        'accepted_plan_ref', null));
    end loop;
    v_rev := ops.portfolio_propose_revision('WR-SYNTHETIC', 1, gen_random_uuid(), v_sources,
      v_graph, v_accepted, v_bindings, v_nodes, v_edges);
    set constraints all immediate;
    raise exception 'a proposal storing a wrong child digest was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'digest does not match its content' then raise; end if;
  end;
  set constraints all deferred;

  -- A WRONG STORED ACCEPTED DIGEST IS REFUSED AT THE COMMIT CHECK.
  begin
    v_rev := ops.portfolio_propose_revision('WR-SYNTHETIC', 1, gen_random_uuid(), v_sources,
      v_graph, v_placeholder, v_children, v_nodes, v_edges);
    set constraints all immediate;
    raise exception 'a proposal storing the wrong accepted digest was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'accepted digest does not match its rows' then raise; end if;
  end;

  -- THE REAL PROPOSAL.
  v_rev := ops.portfolio_propose_revision('WR-SYNTHETIC', 1, gen_random_uuid(), v_sources,
    v_graph, v_accepted, v_children, v_nodes, v_edges);
  set constraints all immediate;
  -- Back to deferred: the completeness check is meant to run at commit, and a
  -- later proposal in this proof would otherwise be judged before its own
  -- children and nodes exist.
  set constraints all deferred;

  if ops.portfolio_graph_digest(v_rev) <> v_graph then
    raise exception 'the graph digest is not stable across identical rows';
  end if;
  if ops.portfolio_accepted_digest(v_rev) <> v_accepted then
    raise exception 'the accepted digest is not stable across identical rows';
  end if;
  if v_accepted = v_graph then
    raise exception 'the accepted digest must cover more than the graph digest';
  end if;
  if not ops.portfolio_revision_structure_valid(v_rev) then
    raise exception 'the synthetic 21-node graph did not validate';
  end if;

  -- A CHILD MAY ONLY BIND AN ACCEPTED PLAN. A sourced plan row is a proposal
  -- until a human acceptance receipt exists for it; binding a child to a merely
  -- proposed plan would let unaccepted source inherit governing authority
  -- through the portfolio. Both shapes refuse the same way: a plan reference
  -- with no acceptance receipt, and one naming no plan at all.
  for v_case in
    select * from (values
      (coalesce((select p.plan_ref from ops.sourced_work_request_plan p
                  where not exists (select 1 from ops.sourced_work_request_plan_acceptance_receipt a
                                     where a.plan_id = p.id) limit 1),
                'PLAN-000000000000-v1')),
      ('PLAN-ffffffffffff-v9')
    ) as t(plan_ref)
  loop
    begin
      v_bindings := '[]'::jsonb;
      foreach c in array array['foundation-and-control-plane','assurance-fabric','product-journeys','rollout-and-retirement'] loop
        v_bindings := v_bindings || jsonb_build_array(jsonb_build_object(
          'child_ref', c,
          'child_ordinal', array_position(array['foundation-and-control-plane','assurance-fabric','product-journeys','rollout-and-retirement'], c) - 1,
          'child_version', 1, 'child_digest', v_placeholder,
          'accepted_plan_ref', case when c = 'assurance-fabric' then v_case.plan_ref else null end));
      end loop;
      v_rev2 := ops.portfolio_propose_revision('WR-SYNTHETIC', 4, gen_random_uuid(), v_sources,
        v_placeholder, v_placeholder, v_bindings, v_nodes, v_edges);
      raise exception 'a child bound to the unaccepted plan % was not refused', v_case.plan_ref;
    exception when others then
      get stacked diagnostics v_err = message_text;
      if v_err !~ 'which is not an accepted plan' then raise; end if;
    end;
  end loop;

  -- A STRUCTURALLY INVALID GRAPH IS REFUSED even when every count is right.
  -- The extra edge closes a cycle back to the first node: 21 nodes, four
  -- children, all references resolve, and the dependency set is no longer
  -- acyclic. Nothing but the structure check can catch this.
  begin
    -- Digest values are free here: the completeness check tests structure
    -- before it compares any digest, so the cycle is what this can refuse on.
    v_rev2 := ops.portfolio_propose_revision('WR-SYNTHETIC', 3, gen_random_uuid(), v_sources,
      v_placeholder, v_placeholder, v_children, v_nodes,
      v_edges || '[{"from_node_ref":"step:synthetic-milestone-21","to_node_ref":"step:synthetic-milestone-01"}]'::jsonb);
    set constraints all immediate;
    raise exception 'a proposal with a dependency cycle was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'closed acyclic 21-node four-child graph' then raise; end if;
  end;
  set constraints all deferred;

  -- A PROPOSAL IS INERT.
  if (select count(*) from ops.job) <> v_jobs
     or (select count(*) from ops.engineering_execution_envelope) <> v_envelopes
     or (select count(*) from ops.capability_agent_session) <> v_sessions then
    raise exception 'a portfolio proposal created an executable effect';
  end if;

  -- APPEND-ONLY.
  begin
    update ops.portfolio_revision set revision_version = 2 where id = v_rev;
    raise exception 'update of a portfolio revision was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'append-only' then raise; end if;
  end;
  begin
    delete from ops.portfolio_node where portfolio_revision_id = v_rev;
    raise exception 'delete of a portfolio node was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'append-only' then raise; end if;
  end;

  -- REVIEW GUARDS.
  -- A 'fail' verdict is deliberately used here: the self-pass rule only governs
  -- a 'pass', so staleness is the only reason this can be refused.
  begin
    perform ops.portfolio_review_revision(v_rev, gen_random_uuid(), v_placeholder, 'fail', 'stale');
    raise exception 'a review against a stale digest was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'review digest is stale' then raise; end if;
  end;
  begin
    perform ops.portfolio_review_revision(v_rev, gen_random_uuid(), v_accepted, 'pass', 'self');
    raise exception 'a proposer self-review pass was not refused';
  exception when others then
    get stacked diagnostics v_err = message_text;
    if v_err !~ 'own portfolio revision' then raise; end if;
  end;

  -- A CHILD VERSION CHANGE MOVES THE ACCEPTED DIGEST AND NOT THE GRAPH.
  -- Both variants use the SAME portfolio reference and revision version, so the
  -- only difference between them is one child's version. Each is proposed in a
  -- throwaway subtransaction, so neither collides with the real proposal above.
  begin
    v_bindings := '[]'::jsonb;
    foreach c in array array['foundation-and-control-plane','assurance-fabric','product-journeys','rollout-and-retirement'] loop
      v_bindings := v_bindings || jsonb_build_array(jsonb_build_object(
        'child_ref', c, 'child_ordinal', array_position(array['foundation-and-control-plane','assurance-fabric','product-journeys','rollout-and-retirement'], c) - 1,
        'child_version', case when c = 'assurance-fabric' then 2 else 1 end,
        'child_digest', v_placeholder, 'accepted_plan_ref', null));
    end loop;
    v_rev2 := ops.portfolio_propose_revision('WR-SYNTHETIC', 2, gen_random_uuid(), v_sources,
      v_placeholder, v_placeholder, v_bindings, v_nodes, v_edges);
    v_graph2 := ops.portfolio_graph_digest(v_rev2);
    v_accepted2 := ops.portfolio_accepted_digest(v_rev2);
    raise exception '%', v_learn;
  exception when others then
    if sqlerrm <> v_learn then raise; end if;
  end;
  -- Learn the same-shaped baseline at revision 2 so the ONLY difference from
  -- the variant is the child version.
  begin
    v_bindings := '[]'::jsonb;
    foreach c in array array['foundation-and-control-plane','assurance-fabric','product-journeys','rollout-and-retirement'] loop
      v_bindings := v_bindings || jsonb_build_array(jsonb_build_object(
        'child_ref', c, 'child_ordinal', array_position(array['foundation-and-control-plane','assurance-fabric','product-journeys','rollout-and-retirement'], c) - 1,
        'child_version', 1, 'child_digest', v_placeholder, 'accepted_plan_ref', null));
    end loop;
    v_rev2 := ops.portfolio_propose_revision('WR-SYNTHETIC', 2, gen_random_uuid(), v_sources,
      v_placeholder, v_placeholder, v_bindings, v_nodes, v_edges);
    v_graph3 := ops.portfolio_graph_digest(v_rev2);
    v_accepted3 := ops.portfolio_accepted_digest(v_rev2);
    raise exception '%', v_learn;
  exception when others then
    if sqlerrm <> v_learn then raise; end if;
  end;
  if v_graph2 <> v_graph3 then
    raise exception 'a child version change must not move the graph digest';
  end if;
  if v_accepted2 = v_accepted3 then
    raise exception 'a child version change must move the accepted digest';
  end if;

  raise notice 'work-portfolio PostgreSQL acceptance passed: proposal inert, digests recomputed and stable, append-only enforced, review guards refuse, child version moves only the accepted digest.';
end $proof$;

rollback;

