// Record an archived email attachment against the deal its mail matched.
import { recordRequirementReply } from "./requirement-replies.js";
import { installTourDealAccess } from "./tour-deal-access.js";
import { organizationTenantForActor } from "./identity.js";
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
    "register-deal-quote-text": {
      write: true, authorityOnly: true,
      description: "Register a byte-verified lease or LOI text layer as an immutable snapshot. Caller must verify attachment bytes and extraction before this authority-only call. Hash changes create numbered versions. Refuses different text for the same document hash. No extraction or sending occurs here.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string", format: "uuid" },
        document_id: { type: "string", format: "uuid" },
        content_hash: { type: "string", pattern: "^[0-9a-f]{64}$" },
        text: { type: "string", minLength: 1, maxLength: 200000 },
      }, required: ["idempotency_key", "document_id", "content_hash", "text"] },
      handler: async (c, actor, args) => (await c.query(
        "select ops.register_deal_quote_text($1::uuid,$2::uuid,$3::text,$4::text) r",
        [args.document_id,args.idempotency_key,args.content_hash,args.text])).rows[0].r,
    },
    "read-deal-quote-text": {
      writerConnection: true, humanOnly: true,
      description: "Read one immutable lease or LOI text snapshot by attachment and byte hash, after current deal access. Offsets count Unicode code points with exclusive ends. An unavailable text layer refuses; this never fabricates extracted text.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        deal_id: { type: "string", format: "uuid" }, document_id: { type: "string", format: "uuid" },
        content_hash: { type: "string", pattern: "^[0-9a-f]{64}$" },
      }, required: ["deal_id", "document_id", "content_hash"] },
      handler: async (c, actor, args) => {
        await installTourDealAccess(c, actor);
        return (await c.query("select ops.read_deal_quote_text($1::uuid,$2::uuid,$3::text) r",
          [args.deal_id,args.document_id,args.content_hash])).rows[0].r;
      },
    },
    "save-deal-line-quote": {
      write: true, writerConnection: true, humanOnly: true,
      description: "Save an exact text selection and partner comment against an immutable document version. Range is zero-based Unicode code points with exclusive end. Verifies selected text against stored text. Actor/key retries return the original receipt; changed arguments refuse. Does not attach to or send an ask.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string", format: "uuid" }, deal_id: { type: "string", format: "uuid" },
        snapshot_id: { type: "string", format: "uuid" },
        start: { type: "integer", minimum: 0, maximum: 199999 }, end: { type: "integer", minimum: 1, maximum: 200000 },
        quoted_text: { type: "string", minLength: 1, maxLength: 4000 },
        comment: { type: "string", minLength: 1, maxLength: 4000 },
      }, required: ["idempotency_key", "deal_id", "snapshot_id", "start", "end", "quoted_text", "comment"] },
      handler: async (c, actor, args) => {
        await installTourDealAccess(c, actor);
        const {deal_id,idempotency_key,...request} = args;
        return (await c.query("select ops.save_deal_line_quote($1::uuid,$2::uuid,$3::jsonb) r",
          [deal_id,idempotency_key,JSON.stringify(request)])).rows[0].r;
      },
    },
    "list-deal-line-quotes": {
      writerConnection: true, humanOnly: true,
      description: "Read a UUID-cursor page of up to 100 saved or detached quotes for a currently readable deal. Historical selections retain their original text and version. version_changed and from version N expose newer source versions. No send or attachment is implied.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        deal_id: { type: "string", format: "uuid" }, cursor: { type: "string", format: "uuid" },
      }, required: ["deal_id"] },
      handler: async (c, actor, args) => {
        await installTourDealAccess(c, actor);
        return (await c.query("select ops.list_deal_line_quotes($1::uuid,$2::uuid) r",[args.deal_id,args.cursor ?? null])).rows[0].r;
      },
    },
    "detach-deal-line-quote": {
      write: true, writerConnection: true, humanOnly: true,
      description: "Record a partner removing their own saved quote from future ask attachment. Retains text, range and author as evidence. Key retries return the original receipt. This does not cancel an accepted ask.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string", format: "uuid" }, deal_id: { type: "string", format: "uuid" },
        quote_id: { type: "string", format: "uuid" },
      }, required: ["idempotency_key", "deal_id", "quote_id"] },
      handler: async (c, actor, args) => {
        await installTourDealAccess(c, actor);
        return (await c.query("select ops.detach_deal_line_quote($1::uuid,$2::uuid,$3::uuid) r",
          [args.deal_id,args.quote_id,args.idempotency_key])).rows[0].r;
      },
    },
    "list-deal-documents": {
      write: false,
      description: "List stored, undeleted documents for one visible deal UUID. IDs are the attachment UUIDs used by fact citations. Returns display name, MIME document type, null page count, content hash and stored date. Source is omitted because attachments have no stored source field. No bytes, object keys or signed URLs. Pages sort by immutable document UUID; cursors bind tenant and deal. New IDs before the cursor appear on a fresh scan.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        deal_id: { type: "string", pattern: "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$" },
        limit: { type: "integer", minimum: 1, maximum: 100 },
        cursor: { type: "string", minLength: 1, maxLength: 1000 },
      }, required: ["deal_id"] },
      handler: async (c, actor, args) => {
        const tenant = organizationTenantForActor(actor);
        const deal = args.deal_id.toLowerCase();
        let after = null;
        if (args.cursor) {
          try {
            const cursor = JSON.parse(args.cursor);
            if (!cursor || cursor.version !== 1 || cursor.tenant !== tenant || cursor.deal !== deal
              || typeof cursor.after !== "string"
              || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(cursor.after)) throw new Error();
            after = cursor.after;
          } catch { throw new ToolError({ error: "invalid_cursor" }); }
        }
        const limit = args.limit ?? 50;
        const rows = (await c.query(`select id, display_name, document_type, page_count, content_hash, stored_at
          from public.v_deal_documents where organization_tenant_id=$1 and deal_id=$2
            and ($3::uuid is null or id>$3::uuid) order by id limit $4`, [tenant, deal, after, limit + 1])).rows;
        const documents = rows.slice(0, limit);
        const has_more = rows.length > limit;
        return { ok: true, schema_version: "deal-documents.v1", documents, has_more,
          next_cursor: has_more ? JSON.stringify({ version: 1, tenant, deal, after: documents.at(-1).id }) : null };
      },
    },
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
