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
  if (
    text(b.subarray(4, 8)) === "ftyp" &&
    ["heic", "heix", "hevc", "hevx"].includes(text(b.subarray(8, 12)))
  )
    return "image/heic";
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
    and exists(select 1 from public.actor where slug=$2 and active)`,
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
  if (row.object_key !== `deliverables/${tenant}/${row.deal_id}/${row.id}`)
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
        "Reserve a partner deliverable upload. Private by default. Returns a byte upload path. Files stay with the deal.",
      "finalize-deal-deliverable":
        "Finalize verified deliverable bytes privately. Repeated finalization returns the existing file.",
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
            field: "deliverable",
            new: result.deliverable,
            cause: "human_stated",
            idempotency_key: args.idempotency_key,
          });
        return { ok: true, schema_version: "deal-deliverables.v1", ...result };
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
            `select ${columns} from ops.deal_deliverable where organization_tenant_id=$1 and deal_id=$2 and state='finalized' order by created_at,id limit $3`,
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
              `deliverables/${tenant}/${dealId}/${id}`,
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
          return { deliverable: publicRow, existing: true };
        }
        if (row.state !== "verified") fail("deliverable_not_verified");
        return {
          deliverable: (
            await c.query(
              `update ops.deal_deliverable set state='finalized',finalized_at=now(),version=1 where id=$1 returning ${columns}`,
              [row.id],
            )
          ).rows[0],
          existing: false,
        };
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
