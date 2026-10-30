// Record an archived email attachment against the deal its mail matched.
import { recordRequirementReply } from './requirement-replies.js';
import { ToolError } from "./tool-error.ts";

export const REQUIREMENT_UPLOAD_MAX_BYTES = 1024 * 1024;
export function isRequirementPdf(bytes) {
  return bytes.length >= 5 && bytes.length <= REQUIREMENT_UPLOAD_MAX_BYTES
    && String.fromCharCode(...bytes.subarray(0, 5)) === '%PDF-';
}

const MAX_BYTES = 25 * 1024 * 1024;
const MIME = new Set(["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/x-ole-storage"]);
const KEY = /^documents\/([A-Za-z0-9._-]+)\/([0-9a-f]{16})\/([A-Za-z0-9._-]+)$/;

export function dealFileTools({ withEnvelope, writeEvent, resolveSubject }) {
  return {
    "attach-deal-file": {
      write: true,
      description: "Record one email attachment, already archived in R2 by the Studio intake under the quota guard, against the deal its mail matched. Takes the deal, the object key (documents/<client>/<first 16 of sha256>/<file name>), file name, type, sha256 and size; it never receives file bytes and cannot read the bucket, so the caller uploads first. Only deal document types (PDF, Word, Excel, PowerPoint, legacy Office) up to 25 MB. Replays of the same file on the same deal return the existing attachment row and write nothing; a key already filed on another record is refused. Writes one attachment row and one event; sends nothing.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        activity_id: { type: "string", format: "uuid", description: "Stored inbound source email for document reply review" },
        deal: { type: "string", description: "deal ref or name the matched mail resolved to" },
        r2_key: { type: "string" }, filename: { type: "string" }, mime: { type: "string" },
        sha256: { type: "string", description: "lowercase hex of the file bytes" },
        bytes: { type: "integer", minimum: 1 },
        source: { type: "string", description: "opaque intake id of the message and attachment; no names or addresses" } },
        required: ["idempotency_key", "deal", "r2_key", "filename", "mime", "sha256", "bytes"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "attach-deal-file", args, async () => {
        const refuse = (error, hint) => { throw new ToolError({ error, ...(hint ? { hint } : {}) }); };
        if (typeof args.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(args.sha256)) refuse("invalid_sha256");
        if (!Number.isSafeInteger(args.bytes) || args.bytes < 1 || args.bytes > MAX_BYTES) refuse("invalid_bytes", "1 byte to 25 MB");
        if (!MIME.has(args.mime)) refuse("unsupported_mime", "deal documents only: PDF, Word, Excel, PowerPoint, legacy Office");
        if (typeof args.filename !== "string" || !args.filename.trim() || args.filename.length > 255 || /[\\/\0]/.test(args.filename))
          refuse("invalid_filename");
        const key = KEY.exec(typeof args.r2_key === "string" ? args.r2_key : "");
        if (!key || key[2] !== args.sha256.slice(0, 16) || key.slice(1).some(part => /^\.+$/.test(part)))
          refuse("invalid_r2_key", "documents/<client>/<first 16 of sha256>/<file name>");
        if (args.source !== undefined && (typeof args.source !== "string" || args.source.length > 200)) refuse("invalid_source");
        const deal = await resolveSubject(c, args.deal);
        if (deal.type !== "deal") refuse("not_a_deal");
        const same = await c.query(
          "select id, subject_type, subject_id, r2_key from attachment where deleted_at is null and (r2_key=$1 or (subject_type='deal' and subject_id=$2 and sha256=$3)) order by created_at limit 2",
          [args.r2_key, deal.id, args.sha256]);
        const other = same.rows.find(row => row.r2_key === args.r2_key && (row.subject_type !== "deal" || row.subject_id !== deal.id));
        if (other) refuse("r2_key_on_other_record");
        if (same.rows.length) {
          const skipped = args.activity_id && await recordRequirementReply(c, actor, args.activity_id, same.rows[0].id, deal.id);
          return { ok: true, attachment_id: same.rows[0].id, existing: true, ...(skipped ? { reply_proposal_skipped: skipped } : {}) };
        }
        const saved = (await c.query(
          "insert into attachment (subject_type, subject_id, r2_key, filename, mime, sha256, bytes, created_by) values ('deal',$1,$2,$3,$4,$5,$6,$7) returning id",
          [deal.id, args.r2_key, args.filename.trim(), args.mime, args.sha256, args.bytes, actor.id])).rows[0];
        const skipped = args.activity_id && await recordRequirementReply(c, actor, args.activity_id, saved.id, deal.id);
        await writeEvent(c, actor, "attach-deal-file", "deal", deal.id, {
          field: "attachment", new: { attachment_id: saved.id, sha256: args.sha256, bytes: args.bytes },
          cause: "ingest_email", ...(args.source ? { agent_rationale: `email attachment ${args.source}` } : {}), idempotency_key: args.idempotency_key });
        return { ok: true, attachment_id: saved.id, existing: false, ...(skipped ? { reply_proposal_skipped: skipped } : {}) };
      }),
    },
  };
}
