// Evidence-backed implementation-shape decisions linked to canonical Work
// Requests. Revisions are append-only: the current recommendation may change,
// but the assumption and fact that changed the choice remain inspectable.

const textPresent = value => typeof value === "string" && value.trim().length > 0;
const score = value => Number.isInteger(value) && value >= 1 && value <= 5;
const githubUrl = value => textPresent(value) && /^https:\/\/github\.com\/[^/]+\/[^/]+\/?(?:[?#].*)?$/i.test(value.trim());
const words = value => textPresent(value) ? value.trim().split(/\s+/).length : 0;
const repoRoot = value => typeof value === "string" ? value.trim().replace(/\/$/, "").toLowerCase() : "";
const PREBUILD_STATES = new Set(["captured", "triaged", "ready"]);

export const SHAPE_DISPOSITIONS = Object.freeze(["required", "not_required"]);

function plainObject(value) {
  return value && typeof value === "object" && !Array.isArray(value);
}

function stringList(value, minimum = 1) {
  return Array.isArray(value) && value.length >= minimum && value.every(textPresent);
}

export function shapeDecisionError(value) {
  const s = plainObject(value) ? value : {};
  const invalid = [];
  const trinity = plainObject(s.trinity) ? s.trinity : {};
  for (const key of ["workflow_trigger", "output_user", "runtime"])
    if (!textPresent(trinity[key])) invalid.push(`trinity.${key}`);

  if (!textPresent(s.hidden_assumption)) invalid.push("hidden_assumption");
  if (!Array.isArray(s.repo_searches) || s.repo_searches.length < 2 || s.repo_searches.length > 3 || !s.repo_searches.every(textPresent))
    invalid.push("repo_searches (exactly 2-3 non-empty searches)");

  if (!Array.isArray(s.maintained_repos) || s.maintained_repos.length < 5 ||
      !s.maintained_repos.every(repo => plainObject(repo) && githubUrl(repo.url) && textPresent(repo.maintenance_evidence)))
    invalid.push("maintained_repos (at least 5 GitHub repositories with maintenance evidence)");
  else if (new Set(s.maintained_repos.map(repo => repoRoot(repo.url))).size < 5)
    invalid.push("maintained_repos (at least 5 distinct repositories)");

  if (!Array.isArray(s.archetypes) || s.archetypes.length !== 3) {
    invalid.push("archetypes (exactly 3)");
  } else {
    const keys = new Set(); const assumptions = new Set();
    for (const archetype of s.archetypes) {
      if (!plainObject(archetype) || !textPresent(archetype.key) || !textPresent(archetype.label) || !textPresent(archetype.core_assumption)) {
        invalid.push("archetypes fields");
        continue;
      }
      keys.add(archetype.key.trim()); assumptions.add(archetype.core_assumption.trim().toLowerCase());
      const scores = plainObject(archetype.scores) ? archetype.scores : {};
      if (![scores.trinity_fit, scores.useful_v1_effort, scores.extension_effort].every(score)) invalid.push(`archetypes.${archetype.key}.scores (integers 1-5)`);
    }
    if (keys.size !== 3) invalid.push("archetypes.key (3 unique keys)");
    if (assumptions.size !== 3) invalid.push("archetypes.core_assumption (3 genuinely different assumptions)");
    if (!textPresent(s.chosen_key) || !keys.has(s.chosen_key.trim())) invalid.push("chosen_key");
  }

  if (!textPresent(s.mind_changing_fact)) invalid.push("mind_changing_fact");
  const brief = plainObject(s.builder_brief) ? s.builder_brief : {};
  if (!textPresent(brief.chosen_shape)) invalid.push("builder_brief.chosen_shape");
  const repoUrls = new Set((s.maintained_repos || []).map(repo => repo?.url));
  if (!githubUrl(brief.repo_url) || !repoUrls.has(brief.repo_url)) invalid.push("builder_brief.repo_url (must cite researched repository)");
  if (!stringList(brief.must_have_integrations)) invalid.push("builder_brief.must_have_integrations");
  if (!stringList(brief.v1_non_goals)) invalid.push("builder_brief.v1_non_goals");
  const briefTrinity = plainObject(brief.trinity) ? brief.trinity : {};
  if (!["workflow_trigger", "output_user", "runtime"].every(key => textPresent(briefTrinity[key]) && briefTrinity[key].trim() === trinity[key]?.trim()))
    invalid.push("builder_brief.trinity (must repeat the decision trinity exactly)");
  const briefWords = words(brief.text);
  if (briefWords < 80 || briefWords > 160) invalid.push("builder_brief.text (80-160 words; target about 120)");

  return invalid.length ? { error: "work_shape_invalid", invalid } : null;
}

export function shapeDispositionError(value) {
  const v = plainObject(value) ? value : {};
  if (!SHAPE_DISPOSITIONS.includes(v.disposition))
    return { error: "work_shape_disposition_invalid", allowed: SHAPE_DISPOSITIONS };
  if (!textPresent(v.rationale))
    return { error: "work_shape_disposition_invalid", invalid: ["rationale"] };
  if (v.disposition === "required" && textPresent(v.fixed_surface_ref))
    return { error: "work_shape_disposition_invalid", invalid: ["fixed_surface_ref (must be absent when analysis is required)"] };
  if (v.disposition === "not_required" && !textPresent(v.fixed_surface_ref))
    return { error: "work_shape_disposition_invalid", invalid: ["fixed_surface_ref"] };
  return null;
}

export function implementationShapeError(work, currentShape) {
  const dispositionError = shapeDispositionError({
    disposition: work?.shape_disposition,
    fixed_surface_ref: work?.shape_fixed_surface_ref,
    rationale: work?.shape_rationale,
  });
  if (dispositionError) return {
    error: "work_shape_disposition_required",
    work_request: work?.ref,
    work_request_version: Number(work?.version),
    resolution: "set an explicit required or not_required shape disposition before implementation; not_required must cite the already-fixed surface",
  };
  if (work.shape_disposition === "not_required") return null;
  const shapeWorkVersion = currentShape ? Number(currentShape.work_request_version) : null;
  if (shapeWorkVersion !== Number(work.version)) return {
    error: "work_shape_required",
    reason: shapeWorkVersion === null ? "missing" : "stale_after_work_request_change",
    work_request: work.ref,
    work_request_version: Number(work.version),
    shape_work_request_version: shapeWorkVersion,
    resolution: "read the current Work Request and shape stream, then append a shape revision bound to the fresh Work Request version",
  };
  return null;
}

async function sourcedDispositionLineage(c, workRequestId) {
  const row = (await c.query(
    `select ops.read_sourced_work_request_shape_disposition_lineage($1::uuid) as lineage /* work-shape:disposition-lineage */`,
    [workRequestId],
  )).rows[0];
  return row?.lineage ?? null;
}

function shapeRow(row) {
  if (!row) return null;
  return {
    id: row.id,
    work_request_id: row.work_request_id,
    work_request_version: Number(row.work_request_version),
    version: Number(row.version),
    trinity: row.trinity,
    hidden_assumption: row.hidden_assumption,
    repo_searches: row.repo_searches,
    maintained_repos: row.maintained_repos,
    archetypes: row.archetypes,
    chosen_key: row.chosen_key,
    mind_changing_fact: row.mind_changing_fact,
    builder_brief: row.builder_brief,
    source_url: row.source_url || null,
    created_by_actor_id: row.created_by_actor_id,
    created_at: row.created_at,
  };
}

const trinitySchema = {
  type: "object", additionalProperties: false,
  properties: { workflow_trigger: { type: "string" }, output_user: { type: "string" }, runtime: { type: "string" } },
  required: ["workflow_trigger", "output_user", "runtime"],
};
const repoSchema = {
  type: "object", additionalProperties: false,
  properties: { url: { type: "string" }, maintenance_evidence: { type: "string" } },
  required: ["url", "maintenance_evidence"],
};
const archetypeSchema = {
  type: "object", additionalProperties: false,
  properties: {
    key: { type: "string" }, label: { type: "string" }, core_assumption: { type: "string" },
    scores: {
      type: "object", additionalProperties: false,
      properties: { trinity_fit: { type: "integer", minimum: 1, maximum: 5 }, useful_v1_effort: { type: "integer", minimum: 1, maximum: 5 }, extension_effort: { type: "integer", minimum: 1, maximum: 5 } },
      required: ["trinity_fit", "useful_v1_effort", "extension_effort"],
    },
  },
  required: ["key", "label", "core_assumption", "scores"],
};
const builderBriefSchema = {
  type: "object", additionalProperties: false,
  properties: {
    chosen_shape: { type: "string" }, repo_url: { type: "string" },
    trinity: trinitySchema,
    must_have_integrations: { type: "array", items: { type: "string" } },
    v1_non_goals: { type: "array", items: { type: "string" } }, text: { type: "string" },
  },
  required: ["chosen_shape", "repo_url", "trinity", "must_have_integrations", "v1_non_goals", "text"],
};

export function workShapeTools({ withEnvelope, writeEvent, ToolError }) {
  const decisionFields = {
    trinity: trinitySchema,
    hidden_assumption: { type: "string" },
    repo_searches: { type: "array", minItems: 2, maxItems: 3, items: { type: "string" } },
    maintained_repos: { type: "array", minItems: 5, items: repoSchema },
    archetypes: { type: "array", minItems: 3, maxItems: 3, items: archetypeSchema },
    chosen_key: { type: "string" }, mind_changing_fact: { type: "string" },
    builder_brief: builderBriefSchema, source_url: { type: "string" },
  };
  const requiredDecisionFields = ["trinity", "hidden_assumption", "repo_searches", "maintained_repos", "archetypes", "chosen_key", "mind_changing_fact", "builder_brief"];

  return {
    "read-work-shape": {
      write: false, fullOnly: true,
      description: "Read the current append-only implementation-shape decision for a canonical Work Request. This exposes why the work should be a verb, UI, scheduled lane, CLI, view, or other form before implementation begins.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { work_request: { type: "string" }, include_history: { type: "boolean" } }, required: ["work_request"],
      },
      handler: async (c, _actor, args) => {
        const work = (await c.query(`select id, ref, title, state, version, capture_idempotency_key, shape_disposition, shape_fixed_surface_ref, shape_rationale, shape_decided_by_actor_id, shape_decided_at from ops.work_request where ref=$1 or id::text=$1 limit 1`, [args.work_request])).rows[0];
        if (!work) throw new ToolError({ error: "work_request_not_found", work_request: args.work_request });
        const revisions = (await c.query(`select * from ops.work_shape_revision where work_request_id=$1 order by version desc`, [work.id])).rows;
        // Sourced requests carry receipt-backed lineage (original receipt plus at
        // most one forward correction) behind a narrow definer projection; the
        // receipt tables themselves stay denied. Unsourced requests have none.
        const dispositionLineage = work.capture_idempotency_key ? await sourcedDispositionLineage(c, work.id) : null;
        return {
          ok: true,
          work_request: { id: work.id, ref: work.ref, title: work.title, state: work.state, version: Number(work.version), shape_disposition: work.shape_disposition || null, shape_fixed_surface_ref: work.shape_fixed_surface_ref || null, shape_rationale: work.shape_rationale || null, shape_decided_by_actor_id: work.shape_decided_by_actor_id || null, shape_decided_at: work.shape_decided_at || null },
          disposition_lineage: dispositionLineage,
          current: shapeRow(revisions[0]), revision_count: revisions.length,
          history: args.include_history ? revisions.map(shapeRow) : undefined,
        };
      },
    },
    "set-work-shape-disposition": {
      write: true,
      description: "Explicitly decide whether a Work Request needs shape analysis before implementation. required means a current work-shape revision must exist at claim time. not_required is allowed only when the implementation surface is already fixed and names that surface. Any qualified seat may record this operational disposition; it grants no human-only authority.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" }, work_request: { type: "string" }, base_version: { type: "integer", minimum: 1 },
          disposition: { type: "string", enum: SHAPE_DISPOSITIONS }, fixed_surface_ref: { type: "string" }, rationale: { type: "string" }, human_quote: { type: "string" },
        },
        required: ["idempotency_key", "work_request", "base_version", "disposition", "rationale"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "set-work-shape-disposition", args, async () => {
        const validation = shapeDispositionError(args);
        if (validation) throw new ToolError(validation);
        const work = (await c.query(`select id, ref, title, state, version, capture_idempotency_key, shape_disposition, shape_fixed_surface_ref, shape_rationale from ops.work_request where ref=$1 or id::text=$1 limit 1 for update`, [args.work_request])).rows[0];
        if (!work) throw new ToolError({ error: "work_request_not_found", work_request: args.work_request });
        if (!PREBUILD_STATES.has(work.state))
          throw new ToolError({ error: "work_shape_disposition_frozen", work_request: work.ref, state: work.state, allowed_states: [...PREBUILD_STATES] });
        if (Number(args.base_version) !== Number(work.version))
          throw new ToolError({ error: "version_conflict", current_version: Number(work.version), base_version: Number(args.base_version), resolution: "re-read the Work Request and reconsider its shape disposition; never overwrite blind" });
        const fixedSurface = args.disposition === "not_required" ? args.fixed_surface_ref.trim() : null;
        let updated;
        if (work.capture_idempotency_key) {
          // Sourced branch only. An INITIAL not_required is preflighted through
          // the server classifier with empty dependencies and minimal caps: an
          // intrinsically heavy request needs a Work Shape, so not_required is
          // refused here before any receipt is written. A zero-row wrapper result
          // is not "standard"; it only defers to the sourced setter, whose own
          // ops.heavy_build_classification call is authoritative and fail-closed.
          // A correction (an existing disposition moving to required) and an
          // initial required never consult the classifier.
          if (args.disposition === "not_required" && !work.shape_disposition) {
            const classified = (await c.query(
              `select * from ops.classify_sourced_work_request_build($1::text,$2::integer,$3::text,$4::jsonb,$5::jsonb)
                 /* work-shape:classify-initial-not-required */`,
              [work.ref, Number(args.base_version), "", "[]", "{}"],
            )).rows[0];
            if (classified && classified.tier !== "standard")
              throw new ToolError({ error: "heavy_build_shape_required", work_request: work.ref, work_request_version: Number(work.version),
                classification_reasons: Array.isArray(classified.reasons) ? classified.reasons : [],
                resolution: "an intrinsically heavy sourced Work Request needs a Work Shape; set the disposition to required, then write the evidence-backed Work Shape" });
          }
          updated = (await c.query(
            `select * from ops.set_sourced_work_request_shape_disposition($1,$2,$3,$4,$5,$6,$7)`,
            [work.ref, Number(args.base_version), args.disposition, fixedSurface, args.rationale.trim(), actor.id, args.idempotency_key],
          )).rows[0];
        } else {
          updated = (await c.query(
            `update ops.work_request set shape_disposition=$2, shape_fixed_surface_ref=$3, shape_rationale=$4, shape_decided_by_actor_id=$5, shape_decided_at=now(), updated_at=now(), version=version+1 where id=$1 returning *`,
            [work.id, args.disposition, fixedSurface, args.rationale.trim(), actor.id],
          )).rows[0];
        }
        await writeEvent(c, actor, "set-work-shape-disposition", "ops_work_request", work.id, {
          field: "implementation_shape_disposition",
          old: { disposition: work.shape_disposition || null, fixed_surface_ref: work.shape_fixed_surface_ref || null, rationale: work.shape_rationale || null },
          new: { disposition: updated.shape_disposition, fixed_surface_ref: updated.shape_fixed_surface_ref, rationale: updated.shape_rationale },
          human_quote: args.human_quote || null, idempotency_key: args.idempotency_key,
        });
        return { ok: true, work_request: { id: updated.id, ref: updated.ref, title: updated.title, state: updated.state, version: Number(updated.version), shape_disposition: updated.shape_disposition, shape_fixed_surface_ref: updated.shape_fixed_surface_ref, shape_rationale: updated.shape_rationale, shape_decided_by_actor_id: updated.shape_decided_by_actor_id, shape_decided_at: updated.shape_decided_at } };
      }),
    },
    "write-work-shape": {
      write: true,
      description: "Append an evidence-backed implementation-shape decision to a canonical Work Request. Any qualified seat may write it. base_version is the current shape revision (0 for the first); work_request_base_version binds the exact request analyzed. Exactly three assumption-distinct archetypes, 2-3 searches, 5+ distinct maintained repositories, a falsifier, and an approximately 120-word builder brief are hard preconditions. This proposes form; it grants no human-only authority.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" }, work_request: { type: "string" }, base_version: { type: "integer", minimum: 0 }, work_request_base_version: { type: "integer", minimum: 1 },
          ...decisionFields, human_quote: { type: "string" },
        },
        required: ["idempotency_key", "work_request", "base_version", "work_request_base_version", ...requiredDecisionFields],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "write-work-shape", args, async () => {
        const validation = shapeDecisionError(args);
        if (validation) throw new ToolError(validation);
        const work = (await c.query(`select id, ref, title, state, version, capture_idempotency_key, shape_disposition, shape_fixed_surface_ref, shape_rationale from ops.work_request where ref=$1 or id::text=$1 limit 1 for update`, [args.work_request])).rows[0];
        if (!work) throw new ToolError({ error: "work_request_not_found", work_request: args.work_request });
        if (!PREBUILD_STATES.has(work.state))
          throw new ToolError({ error: "work_shape_frozen", work_request: work.ref, state: work.state, allowed_states: [...PREBUILD_STATES] });
        if (work.shape_disposition !== "required")
          throw new ToolError({ error: "work_shape_not_required", work_request: work.ref, shape_disposition: work.shape_disposition || null, resolution: "set the Work Request shape disposition to required against a fresh base version before writing analysis" });
        // A sourced Shape needs the EFFECTIVE receipt-backed required disposition
        // (original receipt or its forward correction) exactly current for this
        // version, not merely the column; the database trigger enforces the same
        // rule on insert. Unsourced requests keep the column check above.
        if (work.capture_idempotency_key) {
          const lineage = await sourcedDispositionLineage(c, work.id);
          if (!lineage || lineage.backs_current_version !== true || lineage.effective?.disposition !== "required")
            throw new ToolError({ error: "work_shape_not_required", work_request: work.ref, shape_disposition: work.shape_disposition || null, disposition_lineage: lineage,
              resolution: "the sourced required disposition must be receipt-backed for the current Work Request version; record or correct it through set-work-shape-disposition first" });
        }
        if (Number(args.work_request_base_version) !== Number(work.version))
          throw new ToolError({ error: "work_request_version_conflict", current_version: Number(work.version), work_request_base_version: Number(args.work_request_base_version), resolution: "re-read the Work Request and reassess its implementation shape; never attach reasoning based on stale requirements" });
        const current = (await c.query(`select * from ops.work_shape_revision where work_request_id=$1 order by version desc limit 1`, [work.id])).rows[0];
        const currentVersion = current ? Number(current.version) : 0;
        if (Number(args.base_version) !== currentVersion)
          throw new ToolError({ error: "version_conflict", current_version: currentVersion, base_version: Number(args.base_version), resolution: "re-read work shape and merge against the current revision; never retry blind" });
        const next = currentVersion + 1;
        const inserted = (await c.query(
          `insert into ops.work_shape_revision
             (work_request_id, work_request_version, version, trinity, hidden_assumption, repo_searches, maintained_repos, archetypes, chosen_key, mind_changing_fact, builder_brief, source_url, created_by_actor_id)
           values ($1,$2,$3,$4::jsonb,$5,$6::jsonb,$7::jsonb,$8::jsonb,$9,$10,$11::jsonb,$12,$13)
           returning *`,
          [work.id, Number(work.version), next, JSON.stringify(args.trinity), args.hidden_assumption, JSON.stringify(args.repo_searches), JSON.stringify(args.maintained_repos), JSON.stringify(args.archetypes), args.chosen_key, args.mind_changing_fact, JSON.stringify(args.builder_brief), args.source_url || null, actor.id],
        )).rows[0];
        await writeEvent(c, actor, "write-work-shape", "ops_work_request", work.id, {
          field: "implementation_shape", old: current ? { revision_id: current.id, version: currentVersion, chosen_key: current.chosen_key } : null,
          new: { revision_id: inserted.id, version: next, chosen_key: inserted.chosen_key },
          human_quote: args.human_quote || null, idempotency_key: args.idempotency_key,
        });
        return { ok: true, work_request: { id: work.id, ref: work.ref, title: work.title, state: work.state, version: Number(work.version), shape_disposition: work.shape_disposition }, shape: shapeRow(inserted) };
      }),
    },
  };
}
