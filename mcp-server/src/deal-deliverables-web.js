// Verify bounded partner upload bytes and gate private downloads on a current session.
import {
  DELIVERABLE_POLICY,
  DELIVERABLE_PREFIX,
  deliverableRow,
  verifyDeliverableBytes,
} from "./deal-deliverables.js";
const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const path = new RegExp(
  `^${DELIVERABLE_PREFIX}/(${UUID})(?:/(${UUID})/(bytes|download|finalize|visibility))?$`,
);
const json = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
    },
  });
const refuse = (error) => {
  throw Object.assign(new Error(error), { payload: { error } });
};
async function boundedBody(request, max) {
  const reader = request.body?.getReader();
  if (!reader) refuse("upload_size_refused");
  const chunks = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > max) {
        await reader.cancel();
        refuse("upload_size_refused");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return bytes;
}
export function createDealDeliverablesHandler({
  transaction,
  bucket,
  callTool,
}) {
  return async (request, actor, sessionActive) => {
    const match = path.exec(new URL(request.url).pathname);
    if (!match) return json({ error: "not_found" }, 404);
    const [, deal, id, action] = match;
    const active = async () => {
      if (!(await sessionActive())) refuse("session_revoked");
    };
    try {
      await active();
      if (action === "bytes") {
        if (request.method !== "PUT")
          return json({ error: "method_not_allowed" }, 405);
        if (
          request.headers.get("content-type") !== "application/octet-stream" ||
          request.headers.has("content-encoding")
        )
          return json({ error: "unsupported_mime" }, 415);
        const length = request.headers.get("content-length");
        if (
          length &&
          (!/^[1-9][0-9]{0,8}$/.test(length) ||
            Number(length) > DELIVERABLE_POLICY.maxBytes)
        )
          refuse("upload_size_refused");
        if (!bucket)
          return json({ error: "deliverable_storage_unavailable" }, 503);
        const result = await transaction(async (c) => {
          const row = await deliverableRow(c, actor, deal, id, { lock: true });
          if (row.state !== "started") refuse("deliverable_upload_conflict");
          if (Date.parse(row.expires_at) <= Date.now())
            refuse("deliverable_upload_expired");
          await c.query(
            "update ops.deal_deliverable set state='uploading' where id=$1",
            [id],
          );
          const bytes = await boundedBody(
            request,
            Math.min(Number(row.bytes), DELIVERABLE_POLICY.maxBytes),
          );
          if (length && Number(length) !== bytes.length)
            refuse("upload_size_refused");
          const verified = await verifyDeliverableBytes(bytes, row);
          await active();
          try {
            const stored = await bucket.put(row.object_key, bytes, {
              httpMetadata: { contentType: "application/octet-stream" },
              customMetadata: { sha256: verified.sha256 },
            });
            if (!stored) refuse("deliverable_storage_unavailable");
            await active();
            await deliverableRow(c, actor, deal, id);
            await c.query(
              "update ops.deal_deliverable set state='verified' where id=$1",
              [id],
            );
            await active();
          } catch (error) {
            await bucket.delete(row.object_key);
            throw error;
          }
          return {
            ok: true,
            state: "verified",
            deliverable_id: id,
            ...verified,
          };
        });
        return json(result, 201);
      }
      if (action === "download") {
        if (request.method !== "GET")
          return json({ error: "method_not_allowed" }, 405);
        if (!bucket)
          return json({ error: "deliverable_storage_unavailable" }, 503);
        const row = await transaction(async (c) => {
          const row = await deliverableRow(c, actor, deal, id);
          if (row.state !== "finalized") refuse("deliverable_not_finalized");
          return row;
        });
        const object = await bucket.get(row.object_key);
        if (
          !object ||
          object.size !== Number(row.bytes) ||
          object.customMetadata?.sha256 !== row.sha256
        )
          refuse("deliverable_storage_unavailable");
        await active();
        await transaction((c) => deliverableRow(c, actor, deal, id));
        await active();
        return new Response(object.body, {
          headers: {
            "content-type": row.mime,
            "content-length": String(row.bytes),
            "content-disposition": `attachment; filename*=UTF-8''${encodeURIComponent(row.filename).replace(/['()*]/g, (c) => "%" + c.charCodeAt(0).toString(16))}`,
            "cache-control": "private, no-store",
            "x-content-type-options": "nosniff",
          },
        });
      }
      const verb = !action
        ? request.method === "GET"
          ? "list-deal-deliverables"
          : request.method === "POST"
            ? "start-deal-deliverable"
            : null
        : request.method === "POST"
          ? {
              finalize: "finalize-deal-deliverable",
              visibility: "set-deal-deliverable-visibility",
            }[action]
          : null;
      if (!verb) return json({ error: "method_not_allowed" }, 405);
      const args =
        request.method === "GET"
          ? {}
          : JSON.parse(
              new TextDecoder().decode(await boundedBody(request, 4096)),
            );
      if (
        !args ||
        typeof args !== "object" ||
        Array.isArray(args) ||
        "deal_id" in args ||
        "deliverable_id" in args
      )
        refuse("invalid_deliverable");
      await active();
      return json(
        await callTool(
          actor,
          verb,
          { ...args, deal_id: deal, ...(id ? { deliverable_id: id } : {}) },
          active,
        ),
      );
    } catch (error) {
      const code = error.payload?.error;
      const status = {
        deliverable_denied: 403,
        session_revoked: 401,
        upload_size_refused: 413,
        unsupported_mime: 415,
        hash_mismatch: 422,
        deliverable_quota_exceeded: 409,
        version_conflict: 409,
        deliverable_upload_conflict: 409,
        deliverable_upload_expired: 410,
        deliverable_not_verified: 409,
        deliverable_not_finalized: 409,
        invalid_deliverable: 400,
        invalid_visibility: 400,
        unregistered_operation_fields: 400,
        deliverable_storage_unavailable: 503,
      }[code];
      if (error instanceof SyntaxError)
        return json({ error: "invalid_json" }, 400);
      return json(
        { error: status ? code : "deliverable_unavailable" },
        status || 503,
      );
    }
  };
}
