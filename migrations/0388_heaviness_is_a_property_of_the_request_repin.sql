-- 0388 — heaviness stops reading the plan's wording.
--
-- ops.heavy_build_classification() built its signal text from the Work Request's
-- title, desired outcome and acceptance criteria AND from the proposed plan's
-- scope_summary. The last of those is the defect: the tier could be changed by
-- rewording the summary while the actual change stayed identical.
--
-- Observed 2026-08-27 on the snapshot seed-coverage guard. The same four-step
-- additive check classified heavy while its summary said "schema", and standard
-- once that word was replaced with "required-field contract". Same work, same
-- caps, same dependency refs, different tier. A session that learns this learns
-- to write around the gate instead of through it, which is worse than no gate.
--
-- NOTHING IS RELAXED. Every keyword signal still fires, on the same words, read
-- from the request itself. Every scale signal still reads the plan, because
-- acceptance-criteria count, dependency count and max_steps are facts about the
-- work's shape rather than about its prose. A request that was heavy before this
-- migration is heavy after it, whatever its plan says.
--
-- The parameter is kept in the signature so no caller changes.

CREATE OR REPLACE FUNCTION ops.heavy_build_classification(p_work_request_id uuid, p_scope_summary text, p_dependency_refs jsonb, p_caps jsonb) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'ops'
    AS $_$
declare
  w ops.work_request%rowtype;
  signal_text text;
  reasons jsonb := '[]'::jsonb;
  criteria_count integer;
  dependency_count integer;
  step_count integer;
  shape_ready boolean;
begin
  select x.* into w from ops.work_request x where x.id = p_work_request_id;
  if not found then return null; end if;
  if jsonb_typeof(coalesce(p_dependency_refs, '[]'::jsonb)) is distinct from 'array'
     or jsonb_typeof(coalesce(p_caps, '{}'::jsonb)) is distinct from 'object' then
    raise exception 'heavy-build classification requires typed dependency refs and caps';
  end if;

  -- THE PLAN'S WORDING IS NOT EVIDENCE ABOUT THE WORK. p_scope_summary used to
  -- join this signal text, which meant the tier could be flipped by rewording the
  -- summary while the change itself stayed identical. Observed 2026-08-27: the
  -- same four-step additive guard classified heavy with the word "schema" in its
  -- summary and standard without it, and the plan was accepted on the second
  -- wording. A gate that a rewrite can pass teaches sessions to write around it
  -- rather than through it. Heaviness is now a property of the REQUEST -- its
  -- title, its desired outcome, its acceptance criteria -- plus the plan's SHAPE,
  -- which the scale signals below still read from caps and dependency refs.
  -- Nothing is relaxed: every signal that fired before still fires, and a request
  -- that was heavy is heavy no matter how its plan is phrased.
  signal_text := lower(concat_ws(' ', w.title, w.desired_outcome,
    coalesce(w.acceptance_criteria::text, '')));
  criteria_count := case when jsonb_typeof(w.acceptance_criteria) = 'array' then jsonb_array_length(w.acceptance_criteria) else 0 end;
  dependency_count := jsonb_array_length(coalesce(p_dependency_refs, '[]'::jsonb));
  step_count := case when coalesce(p_caps->>'max_steps','') ~ '^[0-9]+$' then (p_caps->>'max_steps')::integer else 0 end;

  if signal_text ~ '\m(heavy[ -]build|first[ -]of[ -](its|the)[ -]kind|first[ -]of[ -]kind)\M' then
    reasons := reasons || jsonb_build_array('signal:first_of_kind_or_explicit_heavy');
  end if;
  if signal_text ~ '\m(build|create|extend|implement|ship|design)\M.{0,80}\m(new |complete |governed )?(capability|system|platform|engine|service|workflow|kernel)\M' then
    reasons := reasons || jsonb_build_array('signal:new_capability');
  end if;
  if signal_text ~ '\m(architecture|architectural|system design|multi[ -]surface|end[ -]to[ -]end)\M' then
    reasons := reasons || jsonb_build_array('signal:architecture_or_multi_surface');
  end if;
  if signal_text ~ '\m(migration|schema|deploy|deployment|rebuild|refactor|integration)\M' then
    reasons := reasons || jsonb_build_array('signal:structural_change');
  end if;
  if signal_text ~ '\m(agent learning|memory kernel|learning system|memory engine)\M' then
    reasons := reasons || jsonb_build_array('signal:agent_learning');
  end if;
  if criteria_count >= 5 then reasons := reasons || jsonb_build_array('scale:acceptance_criteria'); end if;
  if dependency_count >= 3 then reasons := reasons || jsonb_build_array('scale:dependency_refs'); end if;
  if step_count >= 5 then reasons := reasons || jsonb_build_array('scale:max_steps'); end if;

  shape_ready := w.shape_disposition = 'required'
    and exists (
      select 1 from ops.work_shape_revision sr
       where sr.work_request_id = w.id and sr.work_request_version = w.version
         and sr.version = (select max(current_sr.version) from ops.work_shape_revision current_sr where current_sr.work_request_id = w.id)
    );
  return jsonb_build_object(
    'tier', case when jsonb_array_length(reasons) > 0 then 'heavy' else 'standard' end,
    'reasons', reasons,
    'shape_disposition', w.shape_disposition,
    'shape_ready', shape_ready,
    'acceptance_criteria_count', criteria_count,
    'dependency_ref_count', dependency_count,
    'max_steps', step_count
  );
end;
$_$;
