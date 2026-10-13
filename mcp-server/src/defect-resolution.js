// defect-resolution.js — the defect log's close door (decision cbe91287,
// 2026-10-12: "this system needs to be able to function without me
// micromanaging it").
//
// record-defect's own contract says a defect must ACCUMULATE: the class counts
// are the point, so a close is never a delete and never a state written onto
// public.defect. It is one append-only row in public.defect_resolution (0872)
// naming the fix: a commit, a pull request, or a server receipt. v_defect shows
// the receipt beside the defect; v_defect_class keeps counting it.
//
// THE EVIDENCE IS THE CLOSE. Without a fix reference there is nothing to close
// on, and that refusal happens before the database is touched. One close per
// defect: the same evidence twice is the same act and returns the first
// receipt; different evidence is refused rather than rewriting what the
// defect was closed on.
//
// AND THE SERVER MUST ALREADY HOLD THE EVIDENCE. A close's receipt is bound
// as Work Request and incident evidence, so a well-formed string is not enough:
// ops.defect_resolution_evidence_receipt resolves it to a row (a commit or pull
// request recorded as merged and shipped in ops.release_slice_member, a
// succeeded run, a complete deployment, a resolved incident) or refuses. The
// table's own insert trigger asks the same function, so this check is the
// early, explained refusal and the trigger is the one that cannot be bypassed.

import { ToolError as DefaultToolError } from "./tool-error.js";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const RESOLUTION_EVIDENCE_KINDS = Object.freeze(["commit", "pull_request", "receipt"]);
// The same shapes public.defect_resolution's CHECK constraint holds. This copy
// exists to refuse with a reason before the database is asked; the constraint
// is the one that cannot be bypassed.
const COMMIT = /^[0-9a-f]{7,40}$/;
const PULL_REQUEST = /^[1-9][0-9]{0,6}$/;
const RECEIPT = /^[a-z][a-z0-9_.-]{0,40}:[A-Za-z0-9][A-Za-z0-9:_./-]{0,250}$/;
// ops.work_request_evidence_receipt_normalized's rule: a server receipt's uuid
// is stored lowercase, so an uppercase uuid is the same receipt.
const SERVER_RECEIPT = /^(ops\.run|ops\.deployment|defect-resolution):[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** {evidence_kind, evidence_ref} normalized, or a ToolError naming what is wrong. */
export function resolutionEvidence(args, ToolError = DefaultToolError) {
  const kind = typeof args?.evidence_kind === "string" ? args.evidence_kind.trim() : "";
  let ref = typeof args?.evidence_ref === "string" ? args.evidence_ref.trim() : "";
  if (!kind || !ref)
    throw new ToolError({ error: "resolution_evidence_required",
      hint: "a defect closes on the evidence of its fix: evidence_kind (commit, pull_request or receipt) " +
            "and evidence_ref (the commit sha, the pull request number, or a server receipt such as " +
            "ops.run:<uuid>). Nothing was written." });
  if (kind === "commit" || (kind === "receipt" && SERVER_RECEIPT.test(ref))) ref = ref.toLowerCase();
  if (kind === "pull_request") {
    const url = ref.match(/\/pull\/([0-9]+)\/?$/);
    ref = url ? url[1] : ref.replace(/^(?:#|pr[-:#]?\s*)/i, "");
  }
  const shape = { commit: COMMIT, pull_request: PULL_REQUEST, receipt: RECEIPT }[kind];
  if (!shape || !shape.test(ref))
    throw new ToolError({ error: "invalid_resolution_evidence", evidence_kind: kind,
      hint: "commit: a 7-40 character lowercase hex sha. pull_request: the PR number (#1711, " +
            "PR-1711 and the pull URL are accepted). receipt: <kind>:<id>, e.g. ops.run:<uuid> or " +
            "incident:INC-20260901-03. Nothing was written." });
  return { evidence_kind: kind, evidence_ref: ref };
}

export function defectResolutionTools({ withEnvelope, writeEvent, ToolError }) {
  return {
    "close-defect": {
      write: true,
      description:
        "Close ONE defect on evidence of its fix that the server already holds: a commit or pull request " +
        "recorded as merged and shipped (a release slice member), or a server receipt " +
        "(ops.run:<uuid> that succeeded, ops.deployment:<uuid> that completed, incident:<ref> that is resolved). " +
        "A sha or PR number the server has no record of is refused, because the close's receipt is later bound " +
        "as Work Request and incident evidence. Joe's 2026-10-12 ruling (decision " +
        "cbe91287) removed the human from closing records the system can close on evidence; until then " +
        "record-defect had no close at all. A CLOSE IS A RECEIPT, NOT A DELETE: the defect row never " +
        "changes and still counts toward its class in v_defect_class, because a defect must accumulate " +
        "to be worth anything. The close is one append-only row (public.defect_resolution) shown beside " +
        "the defect in v_defect, and its receipt_ref (defect-resolution:<uuid>) is what an incident or a " +
        "Work Request criterion binds to. Refused without evidence. One close per defect: the same " +
        "evidence again returns the first receipt; different evidence is refused, naming the first.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        defect_id: { type: "string", description: "the defect's uuid, as record-defect returned it (defect_id) or v_defect lists it" },
        evidence_kind: { type: "string", enum: [...RESOLUTION_EVIDENCE_KINDS],
          description: "commit, pull_request, or receipt" },
        evidence_ref: { type: "string", description: "the commit sha, the pull request number, or a server receipt such as ops.run:<uuid>" },
        note: { type: "string", description: "optional: what the fix changed, in a sentence" },
      }, required: ["idempotency_key", "defect_id", "evidence_kind", "evidence_ref"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "close-defect", args, async () => {
        const evidence = resolutionEvidence(args, ToolError);
        const defectId = String(args.defect_id || "").trim().toLowerCase();
        if (!UUID.test(defectId))
          throw new ToolError({ error: "invalid_defect_id", defect_id: args.defect_id,
            hint: "pass the defect's uuid (record-defect returns it as defect_id). Nothing was written." });
        const note = typeof args.note === "string" && args.note.trim() ? args.note.trim().slice(0, 1000) : null;

        const found = await c.query("select id, defect_class from public.defect where id = $1::uuid", [defectId]);
        if (!found.rows.length)
          throw new ToolError({ error: "no_such_defect", defect_id: defectId,
            hint: "no defect has this id; read v_defect for the open ones. Nothing was written." });

        const resolved = (await c.query(
          "select ops.defect_resolution_evidence_receipt($1::text, $2::text) as receipt /* close-defect:resolve-evidence */",
          [evidence.evidence_kind, evidence.evidence_ref])).rows[0]?.receipt || null;
        if (!resolved)
          throw new ToolError({ error: "unresolved_resolution_evidence", ...evidence,
            hint: "the server holds no record of this evidence: a commit or pull request must be recorded as " +
                  "merged and shipped (record-release-slice-members), and a receipt must be a succeeded " +
                  "ops.run:<uuid>, a complete ops.deployment:<uuid> or a resolved incident:<ref>. A bare sha " +
                  "or PR number is not evidence. Nothing was written." });

        // Two racing closes meet at the one-per-defect constraint; the loser
        // reads the winner's receipt below instead of writing a second one.
        const inserted = await c.query(
          `insert into public.defect_resolution
               (defect_id, evidence_kind, evidence_ref, note, idempotency_key, resolved_by)
             values ($1::uuid, $2, $3, $4, $5, $6)
             on conflict (defect_id) do nothing
             returning id, created_at`,
          [defectId, evidence.evidence_kind, evidence.evidence_ref, note, args.idempotency_key, actor.id]);

        if (!inserted.rows.length) {
          const prior = (await c.query(
            `select r.id, r.evidence_kind, r.evidence_ref, r.created_at, a.slug as resolved_by
               from public.defect_resolution r join public.actor a on a.id = r.resolved_by
              where r.defect_id = $1::uuid`, [defectId])).rows[0];
          const existing = prior && { resolution_id: prior.id, receipt_ref: `defect-resolution:${prior.id}`,
            evidence_kind: prior.evidence_kind, evidence_ref: prior.evidence_ref,
            resolved_by: prior.resolved_by, resolved_at: prior.created_at };
          if (!existing || prior.evidence_kind !== evidence.evidence_kind || prior.evidence_ref !== evidence.evidence_ref)
            throw new ToolError({ error: "defect_already_closed", defect_id: defectId, existing,
              hint: "this defect was already closed on different evidence; a close is never rewritten. " +
                    "If the first fix did not hold, record-defect the recurrence." });
          return { ok: true, closed: true, already_closed: true, defect_id: defectId,
            defect_class: found.rows[0].defect_class, ...existing };
        }

        const resolutionId = inserted.rows[0].id;
        await writeEvent(c, actor, "close-defect", "defect", defectId, {
          field: "resolution",
          new: { resolution_id: resolutionId, ...evidence, resolved_to: resolved },
          agent_rationale: note,
          idempotency_key: args.idempotency_key,
        });
        return { ok: true, closed: true, already_closed: false, defect_id: defectId,
          defect_class: found.rows[0].defect_class, resolution_id: resolutionId,
          receipt_ref: `defect-resolution:${resolutionId}`, ...evidence, resolved_to: resolved,
          resolved_by: actor.slug, resolved_at: inserted.rows[0].created_at,
          note: "the defect still counts toward its class; the close is the receipt beside it" };
      }),
    },
  };
}
