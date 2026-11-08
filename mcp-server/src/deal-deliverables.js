// Own partner deliverable authorization, quota and private/shared lifecycle.
import { organizationTenantForActor } from "./identity.js";
import { evaluateActorAuthority } from "./global-boundaries.v5.js";
import { ToolError } from "./tool-error.ts";

export const DELIVERABLE_POLICY = Object.freeze({
  maxBytes: 25 * 1024 * 1024,
  maxDealBytes: 500 * 1024 * 1024,
  maxDealFiles: 100,
});
export const DELIVERABLE_PREFIX = "/api/v1/deal-deliverables";
const uuid = { type: "string", format: "uuid" };
const fail = (error) => {
  throw new ToolError({ error });
};
const text = (bytes) => new TextDecoder().decode(bytes);
const starts = (b, a) => a.every((n, i) => b[i] === n);

export function deliverableMime(b) {
  if (text(b.subarray(0, 5)) === "%PDF-") return "application/pdf";
  if (starts(b, [137, 80, 78, 71, 13, 10, 26, 10])) return "image/png";
  if (starts(b, [255, 216, 255])) return "image/jpeg";
  if (text(b.subarray(0, 4)) === "RIFF" && text(b.subarray(8, 12)) === "WEBP")
    return "image/webp";
  if (text(b.subarray(4, 8)) === "ftyp" && b.length >= 16) {
    const boxSize = new DataView(
      b.buffer,
      b.byteOffset,
      b.byteLength,
    ).getUint32(0);
    for (
      let offset = 8;
      offset + 4 <= Math.min(boxSize, b.length, 128);
      offset += offset === 8 ? 8 : 4
    ) {
      if (
        ["heic", "heix", "hevc", "hevx"].includes(
          text(b.subarray(offset, offset + 4)),
        )
      )
        return "image/heic";
    }
  }
  if (starts(b, [208, 207, 17, 224, 161, 177, 26, 225]) && b.length >= 512) {
    const directory = new TextDecoder("utf-16le").decode(b);
    if (directory.includes("WordDocument\0")) return "application/msword";
    if (directory.includes("Workbook\0") || directory.includes("Book\0"))
      return "application/vnd.ms-excel";
    if (directory.includes("PowerPoint Document\0"))
      return "application/vnd.ms-powerpoint";
  }
  if (starts(b, [80, 75, 3, 4]) && b.length >= 22) {
    const v = new DataView(b.buffer, b.byteOffset, b.byteLength);
    let end = b.length - 22;
    while (
      end >= Math.max(0, b.length - 65557) &&
      v.getUint32(end, true) !== 0x06054b50
    )
      end--;
    if (
      end >= 0 &&
      v.getUint32(end, true) === 0x06054b50 &&
      end + 22 + v.getUint16(end + 20, true) === b.length &&
      v.getUint16(end + 4, true) === 0 &&
      v.getUint16(end + 6, true) === 0
    ) {
      const names = new Set();
      let pos = v.getUint32(end + 16, true);
      const count = v.getUint16(end + 10, true);
      const centralEnd = pos + v.getUint32(end + 12, true);
      if (count > 0 && count <= 10000 && centralEnd === end) {
        for (let i = 0; i < count; i++) {
          if (pos + 46 > end || v.getUint32(pos, true) !== 0x02014b50)
            fail("unsupported_mime");
          const n = v.getUint16(pos + 28, true),
            extra = v.getUint16(pos + 30, true),
            comment = v.getUint16(pos + 32, true);
          if (
            pos + 46 + n + extra + comment > end ||
            v.getUint16(pos + 8, true) & 1
          )
            fail("unsupported_mime");
          const offset = v.getUint32(pos + 42, true);
          if (
            offset + 30 > v.getUint32(end + 16, true) ||
            v.getUint32(offset, true) !== 0x04034b50
          )
            fail("unsupported_mime");
          names.add(text(b.subarray(pos + 46, pos + 46 + n)));
          pos += 46 + n + extra + comment;
        }
        if (pos === end && names.has("[Content_Types].xml")) {
          if (names.has("word/document.xml"))
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
          if (names.has("xl/workbook.xml"))
            return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
          if (names.has("ppt/presentation.xml"))
            return "application/vnd.openxmlformats-officedocument.presentationml.presentation";
        }
      }
    }
  }
  fail("unsupported_mime");
}
export async function verifyDeliverableBytes(bytes, expected) {
  if (
    !bytes.length ||
    bytes.length > DELIVERABLE_POLICY.maxBytes ||
    bytes.length !== Number(expected.bytes)
  )
    fail("upload_size_refused");
  const mime = deliverableMime(bytes);
  if (mime !== expected.mime) fail("unsupported_mime");
  const sha256 = Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
    (n) => n.toString(16).padStart(2, "0"),
  ).join("");
  if (sha256 !== expected.sha256) fail("hash_mismatch");
  return { sha256, mime, bytes: bytes.length };
}

export async function authorizeDeliverable(c, actor, deal) {
  const tenant = organizationTenantForActor(actor);
  if (
    tenant !== "carr-internal" ||
    actor.human !== true ||
    !["joe", "dell"].includes(actor.slug)
  )
    fail("deliverable_denied");
  const d = (
    await c.query(
      `select d.owner from public.deal d join public.client c on c.id=d.client_id
    join public.party p on p.id=c.party_id where d.id=$1 and c.merged_into is null
    and p.merged_into is null and p.deleted_at is null
    and public.retrieval_visibility_actor_id($2) is not null`,
      [deal, actor.slug],
    )
  ).rows[0];
  const decision =
    d &&
    evaluateActorAuthority({
      actor,
      action: "business.read_deal",
      tenant,
      now: new Date().toISOString(),
      controls: {
        deal_owner_slug: d.owner,
        policy_scope: ["business.read_deal"],
        capabilities: ["deal.read"],
      },
    });
  if (!d?.owner || decision?.decision !== "allow") fail("deliverable_denied");
  return tenant;
}
const documentUpload = (row) => row.object_key.startsWith("document-intakes/");
const publicDeliverable = ({ object_key, organization_tenant_id, created_by, expires_at, ...row }) => ({
  ...row,
  ...(row.state !== "finalized" && Date.parse(expires_at) <= Date.now() ? { state: "expired" } : {}),
});
async function documentReceipt(c, row) {
  if (!documentUpload(row) || row.state !== "finalized") return null;
  const attachment = (await c.query(`select id as attachment_id, deal_id, content_hash as sha256, display_name as filename, document_type as mime
    from public.v_deal_documents where id=$1 and deal_id=$2`,
    [row.id,row.deal_id])).rows[0];
  if (!attachment || attachment.sha256 !== row.sha256 || attachment.filename !== row.filename || attachment.mime !== row.mime) fail("deliverable_denied");
  return { ...attachment, bytes: Number(row.bytes) };
}

const columns =
  "id,deal_id,filename,mime,bytes,sha256,state,visibility,version,created_at,finalized_at";
export async function deliverableRow(
  c,
  actor,
  deal,
  id,
  { lock = false } = {},
) {
  const tenant = await authorizeDeliverable(c, actor, deal);
  const row = (
    await c.query(
      `select * from ops.deal_deliverable where organization_tenant_id=$1 and deal_id=$2 and id=$3${lock ? " for update" : ""}`,
      [tenant, deal, id],
    )
  ).rows[0];
  if (!row) fail("deliverable_denied");
  if (!["deliverables", "document-intakes"].some(prefix => row.object_key === `${prefix}/${tenant}/${row.deal_id}/${row.id}`))
    fail("deliverable_denied");
  return row;
}
export function dealDeliverableTools({ withEnvelope, writeEvent }) {
  const read = { deal_id: uuid };
  const target = { ...read, deliverable_id: uuid };
  const write = (name, properties, required, handler) => ({
    write: true,
    humanOnly: true,
    humanCallerOnly: true,
    description: {
      "start-deal-deliverable":
        "Reserve a partner upload. Defaults to private deliverables. target_collection documents uses deal-document-intake.v1 and files verified bytes into deal-documents.v1 attachments. Returns a server-issued byte upload path.",
      "finalize-deal-deliverable":
        "Finalize verified bytes. Document destinations return a deal-document-intake.v1 attachment receipt visible in list-deal-documents. Repeated finalization returns the same identity.",
      "abandon-deal-deliverable": "Abandon an unfinished upload. Bytes are never filed after abandonment. Replays return the existing disposition.",
      "set-deal-deliverable-visibility":
        "Versioned private/shared designation. Shared means publishable, never published.",
    }[name],
    inputSchema: {
      type: "object",
      additionalProperties: false,
      properties: { idempotency_key: { type: "string" }, ...properties },
      required: ["idempotency_key", ...required],
    },
    handler: async (c, actor, args) => {
      await authorizeDeliverable(c, actor, args.deal_id);
      await c.query("select pg_advisory_xact_lock(hashtextextended($1,0))", [
        args.idempotency_key,
      ]);
      return withEnvelope(c, actor, name, args, async () => {
        const result = await handler(c, actor, args);
        if (!result.existing)
          await writeEvent(c, actor, name, "deal", args.deal_id, {
            field: result.attachment ? "attachment" : "deliverable",
            new: result.attachment || result.deliverable,
            cause: "human_stated",
            idempotency_key: args.idempotency_key,
          });
        return { ok: true, schema_version: args.target_collection === "documents" || result.attachment || result.target_collection === "documents" ? "deal-document-intake.v1" : "deal-deliverables.v1", ...result };
      });
    },
  });
  return {
    "list-deal-deliverables": {
      write: false,
      description:
        "List finalized private/shared partner deliverables for an authorized deal. No bytes or object keys.",
      inputSchema: {
        type: "object",
        additionalProperties: false,
        properties: read,
        required: ["deal_id"],
      },
      handler: async (c, actor, a) => {
        const tenant = await authorizeDeliverable(c, actor, a.deal_id);
        const rows = (
          await c.query(
            `select ${columns} from ops.deal_deliverable where organization_tenant_id=$1 and deal_id=$2 and state='finalized' and object_key like 'deliverables/%' order by created_at,id limit $3`,
            [tenant, a.deal_id, DELIVERABLE_POLICY.maxDealFiles],
          )
        ).rows;
        return {
          ok: true,
          schema_version: "deal-deliverables.v1",
          deliverables: rows,
        };
      },
    },
    "start-deal-deliverable": write(
      "start-deal-deliverable",
      {
        ...read,
        target_collection: { type: "string", enum: ["deliverables", "documents"], description: "Defaults to deliverables. Documents finalize into attachments returned by deal-documents.v1." },
        filename: { type: "string", minLength: 1, maxLength: 255 },
        mime: { type: "string" },
        bytes: {
          type: "integer",
          minimum: 1,
          maximum: DELIVERABLE_POLICY.maxBytes,
        },
        sha256: { type: "string", pattern: "^[a-f0-9]{64}$" },
      },
      ["deal_id", "filename", "mime", "bytes", "sha256"],
      async (c, actor, a) => {
        if (a.target_collection !== undefined && !["deliverables", "documents"].includes(a.target_collection)) fail("invalid_deliverable");
        const prefix = a.target_collection === "documents" ? "document-intakes" : "deliverables";
        const dealId = a.deal_id.toLowerCase();
        const tenant = await authorizeDeliverable(c, actor, dealId);
        if (
          !Number.isSafeInteger(a.bytes) ||
          a.bytes < 1 ||
          a.bytes > DELIVERABLE_POLICY.maxBytes
        )
          fail("upload_size_refused");
        if (
          !/^[a-f0-9]{64}$/.test(a.sha256) ||
          !a.filename.trim() ||
          /[\\/\x00-\x1f\x7f]/.test(a.filename) ||
          a.filename.length > 255
        )
          fail("invalid_deliverable");
        if (
          ![
            "application/pdf",
            "application/msword",
            "application/vnd.ms-excel",
            "application/vnd.ms-powerpoint",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "image/png",
            "image/jpeg",
            "image/heic",
            "image/webp",
          ].includes(a.mime)
        )
          fail("unsupported_mime");
        await c.query("select pg_advisory_xact_lock(hashtextextended($1,0))", [
          `deliverables/${tenant}/${dealId}`,
        ]);
        await c.query(
          `update ops.deal_deliverable set state='expired' where organization_tenant_id=$1 and deal_id=$2 and state='started' and expires_at<=now()`,
          [tenant, dealId],
        );
        const q = (
          await c.query(
            `select count(*)::int n,coalesce(sum(bytes),0)::bigint bytes from ops.deal_deliverable where organization_tenant_id=$1 and deal_id=$2 and state<>'expired'`,
            [tenant, dealId],
          )
        ).rows[0];
        if (
          q.n >= DELIVERABLE_POLICY.maxDealFiles ||
          Number(q.bytes) + a.bytes > DELIVERABLE_POLICY.maxDealBytes
        )
          fail("deliverable_quota_exceeded");
        const id = crypto.randomUUID();
        const row = (
          await c.query(
            `insert into ops.deal_deliverable(id,organization_tenant_id,deal_id,filename,mime,bytes,sha256,object_key,created_by)
        values($1,$2,$3,$4,$5,$6,$7,$8,$9) returning ${columns}`,
            [
              id,
              tenant,
              dealId,
              a.filename.trim(),
              a.mime,
              a.bytes,
              a.sha256,
              `${prefix}/${tenant}/${dealId}/${id}`,
              actor.slug,
            ],
          )
        ).rows[0];
        return {
          deliverable: row,
          upload_path: `${DELIVERABLE_PREFIX}/${dealId}/${id}/bytes`,
        };
      },
    ),
    "finalize-deal-deliverable": write(
      "finalize-deal-deliverable",
      target,
      ["deal_id", "deliverable_id"],
      async (c, actor, a) => {
        const row = await deliverableRow(
          c,
          actor,
          a.deal_id,
          a.deliverable_id,
          { lock: true },
        );
        if (row.state === "finalized") {
          const {
            object_key,
            organization_tenant_id,
            created_by,
            expires_at,
            ...publicRow
          } = row;
          return { deliverable: publicRow, attachment: await documentReceipt(c, row), existing: true };
        }
        if (row.state === "expired" || Date.parse(row.expires_at) <= Date.now()) fail("deliverable_upload_expired");
        if (row.state !== "verified") fail("deliverable_not_verified");
        if (documentUpload(row)) {
          await c.query(`insert into public.attachment(id,subject_type,subject_id,r2_key,filename,mime,sha256,bytes,created_by)
            values($1,'deal',$2,$3,$4,$5,$6,$7,$8)`,
            [row.id,row.deal_id,row.object_key,row.filename,row.mime,row.sha256,row.bytes,actor.id]);
        }
        return {
          deliverable: (
            await c.query(
              `update ops.deal_deliverable set state='finalized',finalized_at=now(),version=1 where id=$1 returning ${columns}`,
              [row.id],
            )
          ).rows[0],
          attachment: await documentReceipt(c, { ...row, state: "finalized" }),
          existing: false,
        };
      },
    ),
    "read-deal-deliverable": {
      write: false,
      description: "Read the current authorized upload state and deal-document-intake.v1 document attachment receipt after a lost response. No object key or bytes.",
      inputSchema: { type: "object", additionalProperties: false, properties: target, required: ["deal_id", "deliverable_id"] },
      handler: async (c, actor, a) => {
        const row = await deliverableRow(c, actor, a.deal_id, a.deliverable_id);
        return { ok: true, schema_version: documentUpload(row) ? "deal-document-intake.v1" : "deal-deliverables.v1",
          deliverable: publicDeliverable(row), attachment: await documentReceipt(c, row) };
      },
    },
    "abandon-deal-deliverable": write(
      "abandon-deal-deliverable", target, ["deal_id", "deliverable_id"],
      async (c, actor, a) => {
        const row = await deliverableRow(c, actor, a.deal_id, a.deliverable_id, { lock: true });
        if (row.state === "finalized") fail("deliverable_upload_conflict");
        const updated = (await c.query(`update ops.deal_deliverable set expires_at=least(expires_at,now()) where id=$1 returning *`, [row.id])).rows[0];
        return { deliverable: publicDeliverable(updated), target_collection: documentUpload(row) ? "documents" : "deliverables", existing: Date.parse(row.expires_at) <= Date.now() };
      },
    ),
    "set-deal-deliverable-visibility": write(
      "set-deal-deliverable-visibility",
      {
        ...target,
        expected_version: { type: "integer", minimum: 1 },
        visibility: { type: "string", enum: ["private", "shared"] },
      },
      ["deal_id", "deliverable_id", "expected_version", "visibility"],
      async (c, actor, a) => {
        const row = await deliverableRow(
          c,
          actor,
          a.deal_id,
          a.deliverable_id,
          { lock: true },
        );
        if (documentUpload(row)) fail("deliverable_denied");
        if (row.state !== "finalized") fail("deliverable_not_finalized");
        if (row.version !== a.expected_version) fail("version_conflict");
        if (!["private", "shared"].includes(a.visibility))
          fail("invalid_visibility");
        return {
          deliverable: (
            await c.query(
              `update ops.deal_deliverable set visibility=$2,version=version+1 where id=$1 returning ${columns}`,
              [row.id, a.visibility],
            )
          ).rows[0],
        };
      },
    ),
  };
}
