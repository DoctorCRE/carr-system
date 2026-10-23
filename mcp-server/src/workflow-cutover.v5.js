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

// Slice done-record (migration 0628): the evidence kinds a registered
// criterion may carry, and the allowlisted server-resolved sources a
// live_check / accepted_record binding may name (the database CHECK pairs
// each source with its kind). refusal_proof is not offered: no
// server-recorded gate result exists for it to resolve against, so the doors
// refuse it and such a criterion stays unbound for a partner.

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

export function workflowCutoverTools({ withEnvelope, ToolError }) {

  return {

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

  };
}
