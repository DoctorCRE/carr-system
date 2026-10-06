// DoctorCRE V5-R02 (Workflow cutover, caller migration and retirement
// readiness). Store: migrations/0602_doctorcre_r02_workflow_cutover_and_
// caller_inventory.sql, sealed as SCAC v72 by 0603.
//
// Q116 (recovered 2026-09-24): "avoid uncontrolled dual writes, and migrate
// one workflow at a time. The steps are: read legacy state, build the new
// projection, compare outcomes in shadow, establish one write authority, cut
// over, monitor, preserve a bounded recovery path, then retire the old
// workflow and its instructions." `open-workflow-cutover-plan` starts a plan
// at stage=read_legacy; `advance-workflow-cutover-stage` moves it forward
// exactly one step at a time through recovery_ready, requiring fresh
// accepted `ops.workflow_acceptance` evidence (the same store
// `accept-workflow` writes to) at shadow_compare/single_write_authority/
// cutover; `retire-workflow-cutover-plan` is the ONLY door to stage=retired,
// Joe-only, and requires an existing `ops.legacy_schedule_disable_receipt`
// row -- i.e. an already-performed `disable-legacy-schedule` call. It never
// disables a native scheduler itself.
//
// Q153 (recovered): stale-plan supersession -- `open-workflow-cutover-plan`
// automatically supersedes any prior ACTIVE plan for the same workflow
// identity, in the same transaction, and records why. Q153 also added an
// explicit per-slice completion-marking requirement: `mark-slice-completion`
// / `read-slice-completion` are that door, generic across every V5 slice.
//
// Q157 (recovered): a workflow or caller must never read back "done" from
// hidden or absent evidence. `ops.record_workflow_caller` refuses
// status=done without a non-null evidence_ref (enforced in the migration).
// `workflow-cutover-board` goes one step further: it independently re-checks
// the V5-F09/A01 workflow-truth census
// (lib/control_plane_workflow_truth_reader.py) at read time, and while that
// reader answers available:false (its documented state today, pending a
// separate durable signed census store), the board reports
// census.state='unknown' rather than promoting any cached or inferred
// figure to "done".
//
// ACCESS (PR #1245 re-review). Opening (and superseding), advancing,
// cancelling and retiring a plan change what ops.enqueue_job admits for a live
// workflow, so all four verbs are authorityOnly: they run on the partner's
// authority login, and the SQL doors take the actor from
// ops.authority_actor_slug(). Registering a slice's criteria and marking it
// complete are authorityOnly too. A writer login keeps record-workflow-caller
// and mark-slice-progress (in_progress / blocked only).
//
// SLICE DONE-RECORD (migration 0628). The partner doors above stay; beside
// them the AUTOMATED seat (ops.slice_marker_seat -- the local machine actors
// the release pipeline and run.sh call act as) gets its own writer doors:
// register-slice-criteria-from-catalog (criteria read server-side from the
// catalog doctrine, never passed in), bind-slice-criterion-evidence (once per
// criterion), record-release-slice-members, and propose-slice-completion,
// which recomputes every criterion exactly as mark-slice-completion does but
// records only a PROPOSAL. Automation never sets complete: a partner confirms
// many proposals in one act with confirm-slice-completions (authorityOnly),
// which re-evaluates every criterion at that moment. The seat is checked in
// SQL against the server-derived carr.acting_actor_slug. A partner keeps the
// last word: rebind-slice-criterion-evidence and set-slice-mark-hold (both
// authorityOnly) override any automated binding or mark, and a held slice
// refuses every non-authority mark, bind and proposal.
//
// NOTHING HERE RETIRES A REAL PRODUCTION WORKFLOW. `retire-workflow-cutover-
// plan` requires concrete already-existing evidence rows; this slice adds no
// script and no call that manufactures that evidence for any live workflow.

const CUTOVER_STAGES = Object.freeze([
  "read_legacy", "build_projection", "shadow_compare", "single_write_authority",
  "cutover", "monitor", "recovery_ready", "retired",
]);
const CALLER_KINDS = Object.freeze(["script", "verb", "worker_route", "job_definition", "external"]);
const CALLER_STATUSES = Object.freeze(["remaining", "done", "blocked", "superseded", "retired"]);
const SLICE_STATUSES = Object.freeze(["in_progress", "complete", "blocked"]);
// Slice done-record (migration 0628): the evidence kinds a registered
// criterion may carry, and the allowlisted server-resolved sources a
// live_check / accepted_record binding may name (the database CHECK pairs
// each source with its kind). refusal_proof is not offered: no
// server-recorded gate result exists for it to resolve against, so the doors
// refuse it and such a criterion stays unbound for a partner.
const REGISTERED_EVIDENCE_KINDS = Object.freeze([
  "acceptance", "transition", "shipped_release", "live_check", "accepted_record", "unbound",
]);
const LIVE_CHECK_SOURCES = Object.freeze([
  "staging_restore_only_result", "completion_receipt", "job_receipt", "portfolio_acceptance_effect_free",
  "portfolio_revision_acceptance",
]);
const BINDABLE_KINDS = Object.freeze(["shipped_release", "live_check", "accepted_record"]);

function bindSchema(kinds) {
  return {
    type: "object", additionalProperties: false,
    properties: {
      idempotency_key: { type: "string" },
      slice_id: { type: "string", minLength: 1 },
      criterion: { type: "string", minLength: 1 },
      evidence_kind: { type: "string", enum: [...kinds] },
      live_check_source: { type: ["string", "null"], enum: [...LIVE_CHECK_SOURCES, null] },
      live_check_key: { type: ["string", "null"] },
      write_required_reason: { type: ["string", "null"] },
      bound_member_id: { type: ["string", "null"] },
      reason: { type: "string", minLength: 1 },
    },
    required: ["idempotency_key", "slice_id", "criterion", "evidence_kind", "reason"],
  };
}

function completionSchema() {
  return {
    type: "object", additionalProperties: false,
    properties: {
      idempotency_key: { type: "string" },
      slice_id: { type: "string", minLength: 1 },
      criteria_receipt: {
        type: "array", minItems: 1,
        items: {
          type: "object", additionalProperties: false,
          properties: {
            criterion: { type: "string", minLength: 1 },
            evidence_ref: { type: "string", minLength: 1 },
          },
          required: ["criterion", "evidence_ref"],
        },
      },
      reason: { type: ["string", "null"] },
    },
    required: ["idempotency_key", "slice_id", "criteria_receipt"],
  };
}

// The verb handlers run in TWO runtimes: the Node MCP server (a real
// filesystem, node:child_process, a repo checkout) AND the deployed
// Cloudflare Worker (workerd -- no filesystem, no child_process, no
// import.meta.url-relative repo path; those APIs either do not exist or
// throw at MODULE LOAD, not at call time, which is what made the previous
// version of this file crash the Worker's boot on every deploy: top-level
// `fileURLToPath(import.meta.url)` ran before any verb was ever invoked).
//
// The V5-F09/A01 workflow-truth census reader
// (lib/control_plane_workflow_truth_reader.py) is a Python module reachable
// only from the Node process, and only ever answers `available: false`
// today regardless of runtime (a separate, not-yet-landed durable
// signed-census-store PR is what would ever flip it to true). So this
// module never spawns a subprocess at all: a Worker-safe constant read gets
// the exact same fail-closed answer the Node-side subprocess call would
// have returned, without any Node-only API existing anywhere in this file's
// module scope or call graph. When the census store lands, this becomes a
// real read again (over HTTP/fetch, which workerd supports) -- Q157 still
// requires it to fail closed until then, in EITHER runtime.
function readWorkflowTruthCensus() {
  return { available: false, reason: "census_route_not_in_worker" };
}

export function workflowCutoverTools({ withEnvelope, ToolError }) {
  async function bindHandler(c, door, args) {
    let row;
    try {
      row = (await c.query(
        `select * from ${door}($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
        [args.slice_id, args.criterion, args.evidence_kind, args.live_check_source ?? null,
         args.live_check_key ?? null, args.write_required_reason ?? null, args.bound_member_id ?? null,
         args.reason, args.idempotency_key],
      )).rows[0];
    } catch (err) {
      throw new ToolError({ error: "slice_criterion_binding_refused", detail: String(err.message || err) });
    }
    if (!row) throw new ToolError({ error: "slice_criterion_binding_refused" });
    return { ok: true, id: row.id, slice_id: row.slice_id, criterion: row.criterion,
      evidence_kind: row.evidence_kind, live_check_source: row.live_check_source,
      live_check_key: row.live_check_key, write_required_reason: row.write_required_reason,
      bound_member_id: row.bound_member_id, bound_via: row.bound_via, created_at: row.created_at };
  }

  return {
    "open-workflow-cutover-plan": {
      write: true, authorityOnly: true,
      description: "Q116 step 1, partner authority only: open a workflow-migration plan at stage=read_legacy for a registered workflow identity. Q153 stale-plan supersession: any existing ACTIVE plan for the same (workflow_key, workflow_version) is superseded first, in the same transaction, and the reason is recorded -- never left to coexist with the new plan. recovery_plan (Q116's bounded recovery path) is required up front. An open plan does not change what the workflow may enqueue until it reaches single_write_authority. Idempotent on idempotency_key.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          workflow_key: { type: "string", minLength: 1 },
          workflow_version: { type: "integer", minimum: 1 },
          recovery_plan: { type: "string", minLength: 1 },
        },
        required: ["idempotency_key", "workflow_key", "workflow_version", "recovery_plan"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "open-workflow-cutover-plan", args, async () => {
        let row;
        try {
          row = (await c.query(
            "select * from ops.open_workflow_cutover_plan($1,$2,$3,$4)",
            [args.workflow_key, args.workflow_version, args.recovery_plan, args.idempotency_key],
          )).rows[0];
        } catch (err) {
          throw new ToolError({ error: "workflow_cutover_plan_open_refused", detail: String(err.message || err) });
        }
        if (!row) throw new ToolError({ error: "workflow_cutover_plan_open_refused" });
        return { ok: true, plan_id: row.id, workflow_key: row.workflow_key, workflow_version: row.workflow_version,
          stage: row.stage, status: row.status, superseded_by: row.superseded_by };
      }),
    },

    "cancel-workflow-cutover-plan": {
      write: true, authorityOnly: true,
      description: "Partner authority only: cancel an active cutover plan at any stage before retirement. The plan stops governing what its workflow may enqueue at once, and the one-active-plan slot frees for a later open. The reason is recorded. Idempotent on idempotency_key.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          plan_id: { type: "string" },
          reason: { type: "string", minLength: 1 },
        },
        required: ["idempotency_key", "plan_id", "reason"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "cancel-workflow-cutover-plan", args, async () => {
        let row;
        try {
          row = (await c.query(
            "select * from ops.cancel_workflow_cutover_plan($1,$2,$3)",
            [args.plan_id, args.reason, args.idempotency_key],
          )).rows[0];
        } catch (err) {
          throw new ToolError({ error: "workflow_cutover_cancel_refused", detail: String(err.message || err) });
        }
        if (!row) throw new ToolError({ error: "workflow_cutover_plan_not_found" });
        return { ok: true, plan_id: row.id, stage: row.stage, status: row.status };
      }),
    },

    "advance-workflow-cutover-stage": {
      write: true, authorityOnly: true,
      description: "Q116, partner authority only: advance an active cutover plan exactly one step forward through read_legacy -> build_projection -> shadow_compare -> single_write_authority -> cutover -> monitor -> recovery_ready. Refuses to skip a stage, refuses a plan that is not active, and refuses shadow_compare/single_write_authority/cutover without an evidence_ref pointing at an already-accepted ops.workflow_acceptance row (the same store accept-workflow writes) for the exact workflow identity -- 'shadow parity and single-writer checks pass' is verified against that row, not asserted. Cannot reach stage=retired; see retire-workflow-cutover-plan.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          plan_id: { type: "string" },
          to_stage: { type: "string", enum: [...CUTOVER_STAGES] },
          evidence_ref: { type: ["string", "null"], description: "For shadow_compare/single_write_authority/cutover: the ops.workflow_acceptance.id of an accepted shadow (shadow_compare) or canary (single_write_authority, cutover) acceptance for this workflow." },
          reason: { type: "string", minLength: 1 },
        },
        required: ["idempotency_key", "plan_id", "to_stage", "reason"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "advance-workflow-cutover-stage", args, async () => {
        if (args.to_stage === "retired")
          throw new ToolError({ error: "retired_stage_requires_retire_door" });
        let row;
        try {
          row = (await c.query(
            "select * from ops.advance_workflow_cutover_stage($1,$2,$3,$4,$5)",
            [args.plan_id, args.to_stage, args.evidence_ref ?? null, args.reason, args.idempotency_key],
          )).rows[0];
        } catch (err) {
          throw new ToolError({ error: "workflow_cutover_stage_advance_refused", detail: String(err.message || err) });
        }
        if (!row) throw new ToolError({ error: "workflow_cutover_plan_not_found" });
        return { ok: true, plan_id: row.id, stage: row.stage, status: row.status };
      }),
    },

    "retire-workflow-cutover-plan": {
      write: true, authorityOnly: true,
      description: "Q116 last step, partner authority only: the ONLY door to stage=retired. Requires the plan already at stage=recovery_ready and an existing ops.legacy_schedule_disable_receipt row (from a prior, already-performed disable-legacy-schedule call) for the same workflow identity and every registered legacy surface. Never performs a native disable itself. The store derives the workflow-truth census itself (Q157) and refuses retirement while it is unavailable -- which is every call until the census store lands.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          plan_id: { type: "string" },
          disable_receipt_id: { type: "string" },
          reason: { type: "string", minLength: 1 },
        },
        required: ["idempotency_key", "plan_id", "disable_receipt_id", "reason"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "retire-workflow-cutover-plan", args, async () => {
        // The census is derived inside ops.retire_workflow_cutover_plan (PR
        // #1245 re-review P3); nothing this verb passes can satisfy it.
        let row;
        try {
          row = (await c.query(
            "select * from ops.retire_workflow_cutover_plan($1,$2,$3,$4)",
            [args.plan_id, args.disable_receipt_id, args.reason, args.idempotency_key],
          )).rows[0];
        } catch (err) {
          throw new ToolError({ error: "workflow_cutover_retire_refused", detail: String(err.message || err) });
        }
        if (!row) throw new ToolError({ error: "workflow_cutover_plan_not_found" });
        return { ok: true, plan_id: row.id, stage: row.stage, status: row.status };
      }),
    },

    "record-workflow-caller": {
      write: true,
      description: "Caller inventory: upsert one caller's migration status for one workflow identity (done/remaining/blocked/superseded/retired). status=done is refused server-side without a non-null evidence_ref, and status=blocked is refused without blocked_reason -- Q157, a caller cannot read back done from silence.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          workflow_key: { type: "string", minLength: 1 },
          workflow_version: { type: "integer", minimum: 1 },
          caller_locator: { type: "string", minLength: 1 },
          caller_kind: { type: "string", enum: [...CALLER_KINDS] },
          status: { type: "string", enum: [...CALLER_STATUSES] },
          blocked_reason: { type: ["string", "null"] },
          evidence_ref: { type: ["string", "null"] },
        },
        required: ["idempotency_key", "workflow_key", "workflow_version", "caller_locator", "caller_kind", "status"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "record-workflow-caller", args, async () => {
        let row;
        try {
          row = (await c.query(
            "select * from ops.record_workflow_caller($1,$2,$3,$4,$5,$6,$7,$8)",
            [args.workflow_key, args.workflow_version, args.caller_locator, args.caller_kind, args.status,
             args.blocked_reason ?? null, args.evidence_ref ?? null, actor.slug || null],
          )).rows[0];
        } catch (err) {
          throw new ToolError({ error: "workflow_caller_record_refused", detail: String(err.message || err) });
        }
        if (!row) throw new ToolError({ error: "workflow_caller_refused" });
        return { ok: true, id: row.id, workflow_key: row.workflow_key, workflow_version: row.workflow_version,
          caller_locator: row.caller_locator, status: row.status };
      }),
    },

    "workflow-cutover-board": {
      write: false,
      description: "Read the active cutover plan, its stage history, and caller counts (done/remaining/blocked/superseded/retired) for one workflow identity, plus an independently fail-closed read of the V5-F09/A01 workflow-truth census. Per Q157, when that census route answers unavailable (its documented state today), this board reports census.state='unknown' -- it never promotes a cached or inferred figure to 'done'.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          workflow_key: { type: "string", minLength: 1 },
          workflow_version: { type: "integer", minimum: 1 },
        },
        required: ["workflow_key", "workflow_version"],
      },
      handler: async (c, _actor, args) => {
        const row = (await c.query(
          "select ops.workflow_cutover_board($1,$2) as board",
          [args.workflow_key, args.workflow_version],
        )).rows[0];
        const board = row?.board;
        if (!board) throw new ToolError({ error: "workflow_cutover_board_unavailable" });
        const census = readWorkflowTruthCensus();
        return {
          ok: true, ...board,
          census: census.available === true
            ? { state: "read", ...census }
            : { state: "unknown", reason: census.reason },
        };
      },
    },

    "register-slice-checkable-done": {
      write: true, authorityOnly: true,
      description: "Q153, partner authority only: register, once, the checkable_done criteria that define 'done' for a slice_id, each with one evidence binding: 'acceptance' (+workflow_key, workflow_version, acceptance_mode: an accepted ops.workflow_acceptance row for exactly that workflow and mode), 'transition' (+workflow_key, workflow_version, transition_to_stage: a cutover transition into that stage on a plan for that workflow), 'shipped_release' (a merged commit attributed to the slice inside a complete production release), 'live_check' (+live_check_source, live_check_key: a success row in that allowlisted receipt source, or portfolio_acceptance_effect_free: a current portfolio acceptance with zero job/capability/envelope rows in its windows), 'accepted_record' (+live_check_source portfolio_revision_acceptance, live_check_key = portfolio_ref: that portfolio's current, intact accepted revision), or 'unbound' (bound later with rebind-slice-criterion-evidence). Duplicate criteria and a second registration for the same slice_id are refused. Idempotent on idempotency_key. Automation registers from the catalog instead: register-slice-criteria-from-catalog.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          slice_id: { type: "string", minLength: 1 },
          criteria: {
            type: "array", minItems: 1,
            items: {
              type: "object", additionalProperties: false,
              properties: {
                criterion: { type: "string", minLength: 1 },
                evidence_kind: { type: "string", enum: [...REGISTERED_EVIDENCE_KINDS] },
                workflow_key: { type: "string", minLength: 1 },
                workflow_version: { type: "integer", minimum: 1 },
                acceptance_mode: { type: "string", enum: ["shadow", "canary"] },
                transition_to_stage: { type: "string", enum: [...CUTOVER_STAGES] },
                live_check_source: { type: "string", enum: [...LIVE_CHECK_SOURCES] },
                live_check_key: { type: "string", minLength: 1 },
                write_required_reason: { type: "string", minLength: 1 },
              },
              required: ["criterion", "evidence_kind"],
            },
          },
        },
        required: ["idempotency_key", "slice_id", "criteria"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "register-slice-checkable-done", args, async () => {
        let rows;
        try {
          rows = (await c.query(
            "select * from ops.register_slice_checkable_done($1,$2::jsonb,$3)",
            [args.slice_id, JSON.stringify(args.criteria), args.idempotency_key],
          )).rows;
        } catch (err) {
          throw new ToolError({ error: "slice_checkable_done_register_refused", detail: String(err.message || err) });
        }
        return { ok: true, slice_id: args.slice_id, criteria: rows.map(r => ({
          criterion: r.criterion, evidence_kind: r.evidence_kind, workflow_key: r.workflow_key,
          workflow_version: r.workflow_version, acceptance_mode: r.acceptance_mode,
          transition_to_stage: r.transition_to_stage, live_check_source: r.live_check_source,
          live_check_key: r.live_check_key, write_required_reason: r.write_required_reason })) };
      }),
    },

    "register-slice-criteria-from-catalog": {
      write: true,
      description: "Automated slice-marker seat only (the local machine actor): register a DoctorCRE v5 slice's checkable_done criteria exactly as the current slice-catalog doctrine revision states them. Takes no criteria: the server reads them from the catalog, so none can be invented, dropped or reworded. Each is registered evidence_kind='unbound' until bound. Idempotent on idempotency_key.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          slice_id: { type: "string", minLength: 1 },
        },
        required: ["idempotency_key", "slice_id"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "register-slice-criteria-from-catalog", args, async () => {
        let rows;
        try {
          rows = (await c.query(
            "select * from ops.register_slice_criteria_from_catalog($1,$2)",
            [args.slice_id, args.idempotency_key],
          )).rows;
        } catch (err) {
          throw new ToolError({ error: "slice_catalog_register_refused", detail: String(err.message || err) });
        }
        return { ok: true, slice_id: args.slice_id,
          criteria: rows.map(r => ({ criterion: r.criterion, evidence_kind: r.evidence_kind })) };
      }),
    },

    "bind-slice-criterion-evidence": {
      write: true,
      description: "Automated slice-marker seat only: bind ONE criterion that was registered 'unbound', only to a kind the server derives as allowed from the criterion's wording (done_state.criteria[].allowed_kinds), and never while a partner holds the slice. shipped_release names one release member of this slice (bound_member_id) and stays a proposal until a partner confirms it with rebind-slice-criterion-evidence. refusal_proof is not offered: no server-recorded gate result exists, so a negatives criterion stays unbound for a partner. The seat may bind a criterion once; only a partner may rebind (rebind-slice-criterion-evidence), and a partner binding always wins. A reason is required. Idempotent on idempotency_key.",
      inputSchema: bindSchema(BINDABLE_KINDS),
      handler: async (c, actor, args) => withEnvelope(c, actor, "bind-slice-criterion-evidence", args, async () =>
        bindHandler(c, "ops.bind_slice_criterion_evidence", args)),
    },

    "rebind-slice-criterion-evidence": {
      write: true, authorityOnly: true,
      description: "Partner authority only: bind, rebind or explicitly unbind (evidence_kind='unbound') a criterion that was registered 'unbound'. The latest partner binding is the criterion's effective binding whatever automation bound. A reason is required. Idempotent on idempotency_key.",
      inputSchema: bindSchema([...BINDABLE_KINDS, "unbound"]),
      handler: async (c, actor, args) => withEnvelope(c, actor, "rebind-slice-criterion-evidence", args, async () =>
        bindHandler(c, "ops.rebind_slice_criterion_evidence", args)),
    },

    "record-release-slice-members": {
      write: true,
      description: "Automated slice-marker seat only: record merged commits attributed to DoctorCRE v5 catalog slices as members of one complete production release (release_key). These rows are the evidence a shipped_release criterion resolves against. attribution is 'explicit' (the subject names V5-<id>) or 'bare_id'. Refused for a release that is not production/complete and for a slice the catalog does not name. Idempotent per (release, slice, commit).",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          release_key: { type: "string", minLength: 1 },
          members: {
            type: "array", minItems: 1, maxItems: 200,
            items: {
              type: "object", additionalProperties: false,
              properties: {
                slice_id: { type: "string", minLength: 1 },
                commit_sha: { type: "string", pattern: "^[0-9a-f]{40}$" },
                pr_number: { type: ["integer", "null"], minimum: 1 },
                subject: { type: "string", minLength: 1, maxLength: 400 },
                attribution: { type: "string", enum: ["explicit", "bare_id"] },
              },
              required: ["slice_id", "commit_sha", "subject", "attribution"],
            },
          },
        },
        required: ["idempotency_key", "release_key", "members"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "record-release-slice-members", args, async () => {
        let rows;
        try {
          rows = (await c.query(
            "select * from ops.record_release_slice_members($1,$2::jsonb)",
            [args.release_key, JSON.stringify(args.members)],
          )).rows;
        } catch (err) {
          throw new ToolError({ error: "release_slice_members_refused", detail: String(err.message || err) });
        }
        return { ok: true, release_key: args.release_key, members: rows.map(r => ({
          id: r.id, slice_id: r.slice_id, commit_sha: r.commit_sha, pr_number: r.pr_number,
          attribution: r.attribution })) };
      }),
    },

    "list-shipped-releases": {
      write: false,
      description: "Read: complete production releases (release_key, git_sha, completed_at, member_count) completed at or after `since` (ISO timestamp; all when omitted), oldest first. The slice-done marker walks these to attribute shipped merges to DoctorCRE v5 slices.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { since: { type: ["string", "null"] } },
      },
      handler: async (c, _actor, args) => {
        const rows = (await c.query(
          "select * from ops.list_shipped_releases($1::timestamptz)",
          [args.since ?? null],
        )).rows;
        return { ok: true, releases: rows.map(r => ({ release_key: r.release_key, git_sha: r.git_sha,
          completed_at: r.completed_at, member_count: Number(r.member_count) })) };
      },
    },

    "mark-slice-progress": {
      write: true,
      description: "Q153: append an in_progress or blocked mark for a registered slice_id, so a future session reads what's done and what's left. criteria_receipt lists every registered criterion once, each with the evidence_ref proving it (or null while unproven); the server recomputes pass from the criterion's registered workflow and evidence binding. Cannot mark a slice complete -- see mark-slice-completion. blocked needs a reason. Idempotent on idempotency_key.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          slice_id: { type: "string", minLength: 1 },
          status: { type: "string", enum: ["in_progress", "blocked"] },
          criteria_receipt: {
            type: "array", minItems: 1,
            items: {
              type: "object", additionalProperties: false,
              properties: {
                criterion: { type: "string", minLength: 1 },
                evidence_ref: { type: ["string", "null"] },
              },
              required: ["criterion"],
            },
          },
          reason: { type: ["string", "null"] },
        },
        required: ["idempotency_key", "slice_id", "status", "criteria_receipt"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "mark-slice-progress", args, async () => {
        let row;
        try {
          row = (await c.query(
            "select * from ops.mark_slice_progress($1,$2,$3::jsonb,$4,$5,$6)",
            [args.slice_id, args.status, JSON.stringify(args.criteria_receipt), args.reason ?? null,
             args.idempotency_key, actor.slug || null],
          )).rows[0];
        } catch (err) {
          throw new ToolError({ error: "slice_progress_mark_refused", detail: String(err.message || err) });
        }
        if (!row) throw new ToolError({ error: "slice_progress_mark_refused" });
        return { ok: true, id: row.id, slice_id: row.slice_id, status: row.status,
          criteria_receipt: row.criteria_receipt, created_at: row.created_at };
      }),
    },

    "mark-slice-completion": {
      write: true, authorityOnly: true,
      description: "Q153, partner authority only: append status=complete for a registered slice_id. criteria_receipt lists every registered criterion exactly once with the evidence_ref proving it; the server resolves each ref against the criterion's registered workflow and evidence binding and refuses unless every criterion is proven. The caller's own pass claim is never read. Idempotent on idempotency_key.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          slice_id: { type: "string", minLength: 1 },
          criteria_receipt: {
            type: "array", minItems: 1,
            items: {
              type: "object", additionalProperties: false,
              properties: {
                criterion: { type: "string", minLength: 1 },
                evidence_ref: { type: "string", minLength: 1 },
              },
              required: ["criterion", "evidence_ref"],
            },
          },
          reason: { type: ["string", "null"] },
        },
        required: ["idempotency_key", "slice_id", "criteria_receipt"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "mark-slice-completion", args, async () => {
        let row;
        try {
          row = (await c.query(
            "select * from ops.mark_slice_completion($1,$2::jsonb,$3,$4)",
            [args.slice_id, JSON.stringify(args.criteria_receipt), args.reason ?? null, args.idempotency_key],
          )).rows[0];
        } catch (err) {
          throw new ToolError({ error: "slice_completion_mark_refused", detail: String(err.message || err) });
        }
        if (!row) throw new ToolError({ error: "slice_completion_mark_refused" });
        return { ok: true, id: row.id, slice_id: row.slice_id, status: row.status,
          criteria_receipt: row.criteria_receipt, created_at: row.created_at };
      }),
    },

    "propose-slice-completion": {
      write: true,
      description: "Automated slice-marker seat only: PROPOSE that a registered slice_id is complete. criteria_receipt lists every registered criterion once with the evidence_ref proving it; the server resolves each ref against the criterion's effective binding and refuses unless every one is proven. The proposal changes no mark: the slice becomes complete only when a partner confirms it with confirm-slice-completions, which re-evaluates it then. Refused while a partner holds the slice or once it is complete. Idempotent on idempotency_key.",
      inputSchema: completionSchema(),
      handler: async (c, actor, args) => withEnvelope(c, actor, "propose-slice-completion", args, async () => {
        let row;
        try {
          row = (await c.query(
            "select * from ops.propose_slice_completion($1,$2::jsonb,$3,$4)",
            [args.slice_id, JSON.stringify(args.criteria_receipt), args.reason ?? null, args.idempotency_key],
          )).rows[0];
        } catch (err) {
          throw new ToolError({ error: "slice_completion_proposal_refused", detail: String(err.message || err) });
        }
        if (!row) throw new ToolError({ error: "slice_completion_proposal_refused" });
        return { ok: true, proposal_id: row.id, slice_id: row.slice_id, status: "proposed",
          awaiting_partner_confirmation: true, criteria_receipt: row.criteria_receipt, created_at: row.created_at };
      }),
    },

    "confirm-slice-completions": {
      write: true, authorityOnly: true,
      description: "Partner authority only: confirm many slices complete in one act. For each slice_id the server takes the latest automated proposal, re-evaluates every criterion against its effective binding NOW, and writes complete only when all still pass. Optional expected_proposal_ids maps a slice_id to the proposal_id the partner reviewed (from pending-slice-completion-proposals); if that slice's latest proposal is a different one it reports proposal_superseded and is not confirmed. Per-slice outcome: confirmed, held (a partner hold), already_complete, no_proposal, proposal_superseded, stale_proposal (a mark was written after the proposal; re-propose), not_proven (a criterion no longer resolves), unknown_slice. Nothing is confirmed that is not listed. pending-slice-completion-proposals lists what is waiting. A reason is required. Idempotent on idempotency_key.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          slice_ids: { type: "array", minItems: 1, maxItems: 200, items: { type: "string", minLength: 1 } },
          reason: { type: "string", minLength: 1 },
          expected_proposal_ids: {
            type: "object", maxProperties: 200,
            additionalProperties: { type: "string", pattern: "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$" },
          },
        },
        required: ["idempotency_key", "slice_ids", "reason"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "confirm-slice-completions", args, async () => {
        const expected = args.expected_proposal_ids ?? null;
        if (expected !== null) {
          const uuidRe = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
          if (typeof expected !== "object" || Array.isArray(expected)
              || Object.values(expected).some(v => typeof v !== "string" || !uuidRe.test(v))) {
            throw new ToolError({ error: "slice_completion_confirm_refused",
              detail: "expected_proposal_ids maps each slice_id to a proposal_id uuid" });
          }
        }
        let rows;
        try {
          rows = (await c.query(
            "select * from ops.confirm_slice_completions($1::text[],$2,$3,$4::jsonb)",
            [args.slice_ids, args.reason, args.idempotency_key,
              expected === null ? null : JSON.stringify(expected)],
          )).rows;
        } catch (err) {
          throw new ToolError({ error: "slice_completion_confirm_refused", detail: String(err.message || err) });
        }
        const results = rows.map(r => ({ slice_id: r.slice_id, outcome: r.outcome, mark_id: r.mark_id ?? null,
          proposal_id: r.proposal_id ?? null, criteria_receipt: r.criteria_receipt ?? null }));
        return { ok: true, confirmed: results.filter(r => r.outcome === "confirmed").length,
          not_confirmed: results.filter(r => r.outcome !== "confirmed").length, results };
      }),
    },

    "pending-slice-completion-proposals": {
      write: false,
      description: "Read: every slice whose latest automated completion proposal a partner could confirm now (not held, not complete, no mark since the proposal), each re-evaluated on this read (passes_now) with its criteria receipt. Hand the slice_ids to confirm-slice-completions.",
      inputSchema: { type: "object", additionalProperties: false, properties: {} },
      handler: async (c) => {
        const pending = (await c.query(
          "select ops.pending_slice_completion_proposals() as pending",
        )).rows[0]?.pending ?? [];
        return { ok: true, count: pending.length, pending };
      },
    },

    "set-slice-mark-hold": {
      write: true, authorityOnly: true,
      description: "Partner authority only: action='hold' appends a held in_progress or blocked mark (overriding or unmarking any mark, including complete) -- while held, no automated or writer mark, bind or proposal is accepted and confirmation reports the slice held; a proposal made before the hold is stale. action='release' lifts the hold so automation may mark and propose again. A reason is required. Appends; never rewrites a mark. Idempotent on idempotency_key.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          slice_id: { type: "string", minLength: 1 },
          action: { type: "string", enum: ["hold", "release"] },
          status: { type: ["string", "null"], enum: ["in_progress", "blocked", null] },
          reason: { type: "string", minLength: 1 },
        },
        required: ["idempotency_key", "slice_id", "action", "reason"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "set-slice-mark-hold", args, async () => {
        let row;
        try {
          row = (await c.query(
            "select * from ops.set_slice_mark_hold($1,$2,$3,$4,$5)",
            [args.slice_id, args.action, args.status ?? null, args.reason, args.idempotency_key],
          )).rows[0];
        } catch (err) {
          throw new ToolError({ error: "slice_mark_hold_refused", detail: String(err.message || err) });
        }
        if (!row) throw new ToolError({ error: "slice_mark_hold_refused" });
        return { ok: true, id: row.id, slice_id: row.slice_id, status: row.status, marked_via: row.marked_via,
          reason: row.reason, created_at: row.created_at };
      }),
    },

    "read-slice-completion": {
      write: false,
      description: "Q153: read the current (most recent) explicit completion mark for one slice_id (marked:false when never marked), plus done_state: registration, each criterion's effective evidence binding, its newest server-found candidate and candidate_passes (recomputed from the live rows on this read, never from a mark), partner-hold state, the latest completion proposal and whether it awaits partner confirmation, and the slice's shipped release members.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { slice_id: { type: "string", minLength: 1 } },
        required: ["slice_id"],
      },
      handler: async (c, _actor, args) => {
        const row = (await c.query(
          "select * from ops.read_slice_completion($1)",
          [args.slice_id],
        )).rows[0];
        const doneState = (await c.query(
          "select ops.read_slice_done_state($1) as state",
          [args.slice_id],
        )).rows[0]?.state ?? null;
        if (!row || !row.slice_id)
          return { ok: true, slice_id: args.slice_id, marked: false, status: null, criteria_receipt: null,
            done_state: doneState };
        return { ok: true, slice_id: row.slice_id, marked: true, status: row.status,
          marked_via: row.marked_via ?? null, marked_by: row.marked_by_actor_slug ?? null,
          criteria_receipt: row.criteria_receipt, reason: row.reason, created_at: row.created_at,
          done_state: doneState };
      },
    },
  };
}
