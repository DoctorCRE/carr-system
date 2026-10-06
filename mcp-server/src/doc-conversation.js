// WR-000112 — the Doc conversation verbs.
//
// LIBRARY ONLY: no shebang and no main-module construct (the SCAC
// inventory scans for those by substring, so they are described, never spelled). This file
// is a registrySource, so every verb below becomes a NEW inventoried ingress and
// the one registerTools line it costs in tools.js re-digests every mcp-tool row
// sourced from that file.
//
// THE CONNECTION FLAGS ARE PART OF THE CONTRACT. mcp.js decides the connection
// from three flags and nothing else, and a grant the connection cannot exercise
// is a run-time permission error that reads like a missing grant:
//   - ops.append_doc_conversation_turn is granted to carr_authority, so
//     add-doc-conversation-turn declares authorityOnly: true;
//   - ops.doc_conversation_facts is granted to carr_writer/carr_authority and
//     needs the acting-actor context, which only the writer path installs, so
//     read-doc-conversation declares writerConnection: true and no write flag.
// Fix the flag, never widen the grant.
//
// WR-000114 adds the three WRITE doors on the same store. All three are
// granted to carr_writer AND carr_authority (0523), and the app has to call
// them as the SIGNED-IN PARTNER, so every one of them declares write: true
// with writerConnection: true and NEVER authorityOnly -- mcp.js:616-618 would
// refuse an authority-only call before any grant is consulted, and the app
// holds no authority binding. Nothing that names an actor appears in any of
// the three schemas: no created_by, granted_by, grantee_actor, actor,
// acting_actor or at. additionalProperties:false turns an attempt to name one
// into a schema error, which is the point -- the creator, the grantor and
// every timestamp are derived inside the definer functions.
//
// WR-000115 adds the LIST door on the same store. It is a READ: it declares
// writerConnection with NO write flag, because mcp.js opens `begin read only`
// for exactly that combination and ops.list_doc_conversations is `stable`. It
// is never authorityOnly, carries no envelope and writes no event -- there is
// nothing to record. Its schema has no `required` key at all and exactly three
// optional fields, none of which names an actor, an offset or a sort.

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/**
 * The read verb's shaper. Pure: it takes the definer function's facts and
 * returns the projection, refusing anything that is not the shape it expects
 * rather than passing an unknown object through to a caller.
 */
export function docConversationProjection(facts, ToolError) {
  if (!facts || typeof facts !== "object" || facts.ok !== true) {
    throw new ToolError({ error: "doc_conversation_not_found" });
  }
  const identity = facts.identity || {};
  return {
    ok: true,
    identity: {
      id: identity.id,
      title: identity.title,
      visibility: identity.visibility,
      pinned_at: identity.pinned_at ?? null,
      archived_at: identity.archived_at ?? null,
      version: identity.version,
      created_by: identity.created_by,
    },
    turns: (Array.isArray(facts.turns) ? facts.turns : []).map(turn => ({
      sequence: turn.sequence,
      role: turn.role,
      body: turn.body,
      msg_id: turn.msg_id,
      origin_channel: turn.origin_channel,
      origin_actor: turn.origin_actor,
      at: turn.at,
    })),
    latest_sequence: facts.latest_sequence,
    more: facts.more === true,
    effective_grants: (Array.isArray(facts.effective_grants) ? facts.effective_grants : [])
      .map(grant => ({
        grantee_actor: grant.grantee_actor,
        granted_at: grant.granted_at,
        granted_by_actor: grant.granted_by_actor,
      })),
    visible_conversation_count: facts.visible_conversation_count ?? null,
  };
}

/**
 * The list verb's shaper. Pure, for the same reason the read's is: it refuses
 * anything that is not ops.list_doc_conversations' own shape rather than
 * passing an unknown object through to a caller.
 */
export function docConversationListProjection(listed, ToolError) {
  if (!listed || typeof listed !== "object" || listed.ok !== true) {
    throw new ToolError({ error: listed?.reason_id || "doc_conversation_not_found" });
  }
  if (!Array.isArray(listed.conversations)) {
    throw new ToolError({ error: "doc_conversation_not_found" });
  }
  return {
    ok: true,
    conversations: listed.conversations.map(row => ({
      id: row.id,
      title: row.title,
      visibility: row.visibility,
      pinned_at: row.pinned_at ?? null,
      archived_at: row.archived_at ?? null,
      version: row.version,
      created_by: row.created_by,
      latest_sequence: row.latest_sequence,
      latest_turn_at: row.latest_turn_at ?? null,
    })),
    more: listed.more === true,
    next_cursor: listed.next_cursor ?? null,
    visible_conversation_count: listed.visible_conversation_count ?? null,
  };
}

export function docConversationTools({ withEnvelope, writeEvent, ToolError }) {
  return {
    "add-doc-conversation-turn": {
      write: true,
      // Its append function is granted to carr_authority ONLY.
      authorityOnly: true,
      description: "Append one turn to a Doc conversation. Sequence, origin channel and origin actor are derived server-side; a caller may not name any of them.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        conversation_id: { type: "string" },
        role: { type: "string", enum: ["human", "assistant", "system"] },
        body: { type: "string" },
        msg_id: { type: "string" },
      }, required: ["idempotency_key", "conversation_id", "role", "body"] },
      handler: async (c, actor, args) =>
        withEnvelope(c, actor, "add-doc-conversation-turn", args, async () => {
          if (!UUID.test(String(args.conversation_id || ""))) {
            throw new ToolError({ error: "doc_conversation_id_invalid",
              hint: "conversation_id is the uuid ops.doc_conversation assigned" });
          }
          if (typeof args.body !== "string" || !args.body.trim() || args.body.length > 20000) {
            throw new ToolError({ error: "doc_conversation_body_invalid",
              hint: "a turn body is non-empty and at most 20000 characters" });
          }
          if (args.msg_id !== undefined && !UUID.test(String(args.msg_id))) {
            throw new ToolError({ error: "doc_conversation_msg_id_invalid" });
          }
          // Minted here when absent, exactly as add-room-turn mints one: the
          // transport identity is the server's to choose.
          const msgId = args.msg_id || crypto.randomUUID();
          const appended = await c.query(
            "select ops.append_doc_conversation_turn($1::uuid,$2::text,$3::text,$4::uuid,$5::uuid) as appended",
            [args.conversation_id, args.role, args.body, msgId, args.idempotency_key]);
          const result = appended.rows[0]?.appended;
          if (!result || result.ok !== true) {
            throw new ToolError({ error: result?.reason_id || "doc_conversation_append_refused",
              conversation_id: args.conversation_id, msg_id: msgId });
          }
          if (result.deduplicated !== true) {
            await writeEvent(c, actor, "add-doc-conversation-turn", "doc_conversation",
              args.conversation_id, {
                field: "turn",
                new: { sequence: result.sequence, role: args.role },
                cause: "automation_job",
                idempotency_key: args.idempotency_key,
              });
          }
          return { ok: true, deduplicated: result.deduplicated === true,
            conversation_id: args.conversation_id, sequence: result.sequence,
            msg_id: result.msg_id, origin_actor: result.origin_actor };
        }),
    },

    "create-doc-conversation": {
      write: true,
      // Granted to carr_writer and carr_authority, and the app calls it as the
      // signed-in partner: a write on the writer connection, never authority-only.
      writerConnection: true,
      description: "Start a Doc conversation. The creator is derived from the signed-in actor; the row id is the idempotency key, so a replayed create is one row.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        title:           { type: "string" },
        visibility:      { type: "string", enum: ["private", "shared"] },
      }, required: ["idempotency_key", "title"] },
      handler: async (c, actor, args) =>
        withEnvelope(c, actor, "create-doc-conversation", args, async () => {
          if (!UUID.test(String(args.idempotency_key || ""))) {
            throw new ToolError({ error: "doc_conversation_idempotency_key_invalid",
              hint: "idempotency_key is a uuid and becomes the conversation id" });
          }
          if (typeof args.title !== "string" || !args.title.trim() || args.title.length > 200) {
            throw new ToolError({ error: "doc_conversation_title_invalid",
              hint: "a title is non-empty and at most 200 characters" });
          }
          const created = await c.query(
            "select ops.create_doc_conversation($1::text,$2::text,$3::uuid) as created",
            [args.title, args.visibility ?? null, args.idempotency_key]);
          const result = created.rows[0]?.created;
          if (!result || result.ok !== true) {
            throw new ToolError({ error: result?.reason_id || "doc_conversation_create_refused",
              idempotency_key: args.idempotency_key });
          }
          if (result.deduplicated !== true) {
            await writeEvent(c, actor, "create-doc-conversation", "doc_conversation",
              result.id, {
                field: "identity",
                new: { title: result.title, visibility: result.visibility },
                cause: "automation_job",
                idempotency_key: args.idempotency_key,
              });
          }
          return { ok: true, deduplicated: result.deduplicated === true,
            conversation_id: result.id, version: result.version,
            title: result.title, visibility: result.visibility,
            created_by: result.created_by };
        }),
    },

    "share-doc-conversation": {
      write: true,
      writerConnection: true,
      description: "Hand another partner access to a Doc conversation, or take that access back. Creator-only; a withdrawal stamps the grant rather than deleting it.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        conversation_id: { type: "string" },
        grantee_slug:    { type: "string" },
        granted:         { type: "boolean" },
      }, required: ["idempotency_key", "conversation_id", "grantee_slug", "granted"] },
      handler: async (c, actor, args) =>
        withEnvelope(c, actor, "share-doc-conversation", args, async () => {
          if (!UUID.test(String(args.conversation_id || ""))) {
            throw new ToolError({ error: "doc_conversation_id_invalid",
              hint: "conversation_id is the uuid ops.doc_conversation assigned" });
          }
          if (typeof args.grantee_slug !== "string" || !args.grantee_slug.trim()) {
            throw new ToolError({ error: "doc_conversation_grantee_slug_invalid" });
          }
          const shared = await c.query(
            "select ops.share_doc_conversation($1::uuid,$2::text,$3::boolean,$4::uuid) as shared",
            [args.conversation_id, args.grantee_slug, args.granted, args.idempotency_key]);
          const result = shared.rows[0]?.shared;
          if (!result || result.ok !== true) {
            throw new ToolError({ error: result?.reason_id || "doc_conversation_share_refused",
              conversation_id: args.conversation_id });
          }
          if (result.already !== true) {
            await writeEvent(c, actor, "share-doc-conversation", "doc_conversation",
              args.conversation_id, {
                field: "grant",
                new: { grantee_slug: args.grantee_slug, granted: args.granted === true },
                cause: "automation_job",
                idempotency_key: args.idempotency_key,
              });
          }
          return { ok: true, already: result.already === true,
            conversation_id: args.conversation_id, grantee_slug: args.grantee_slug,
            granted: args.granted === true };
        }),
    },

    "rename-doc-conversation": {
      write: true,
      writerConnection: true,
      description: "Rename, pin, unpin, archive or unarchive a Doc conversation under a compare-and-swap on its version. Creator-only.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        conversation_id: { type: "string" },
        base_version:    { type: "integer", minimum: 1 },
        title:           { type: "string" },
        pinned:          { type: "boolean" },
        archived:        { type: "boolean" },
      }, required: ["idempotency_key", "conversation_id", "base_version"] },
      handler: async (c, actor, args) =>
        withEnvelope(c, actor, "rename-doc-conversation", args, async () => {
          if (!UUID.test(String(args.conversation_id || ""))) {
            throw new ToolError({ error: "doc_conversation_id_invalid",
              hint: "conversation_id is the uuid ops.doc_conversation assigned" });
          }
          if (args.title !== undefined &&
              (typeof args.title !== "string" || !args.title.trim() || args.title.length > 200)) {
            throw new ToolError({ error: "doc_conversation_title_invalid",
              hint: "a title is non-empty and at most 200 characters" });
          }
          const renamed = await c.query(
            "select ops.rename_doc_conversation($1::uuid,$2::integer,$3::text,$4::boolean,$5::boolean,$6::uuid) as renamed",
            [args.conversation_id, args.base_version, args.title ?? null,
              args.pinned ?? null, args.archived ?? null, args.idempotency_key]);
          const result = renamed.rows[0]?.renamed;
          if (!result || result.ok !== true) {
            throw new ToolError({ error: result?.reason_id || "doc_conversation_rename_refused",
              conversation_id: args.conversation_id,
              current_version: result?.current_version });
          }
          await writeEvent(c, actor, "rename-doc-conversation", "doc_conversation",
            args.conversation_id, {
              field: "title",
              new: { title: result.title, pinned: result.pinned, archived: result.archived },
              cause: "automation_job",
              idempotency_key: args.idempotency_key,
            });
          return { ok: true, conversation_id: args.conversation_id,
            version: result.version, title: result.title,
            pinned: result.pinned === true, archived: result.archived === true };
        }),
    },

    "read-doc-conversation": {
      // A read-only transaction on the identity that carries the acting-actor
      // context. On the reader connection that context is never installed.
      writerConnection: true,
      description: "Read one Doc conversation the acting actor may see: its identity, its turns in sequence order, and the effective access list.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        conversation_id: { type: "string" },
        after_sequence: { type: "integer", minimum: 0 },
        limit: { type: "integer", minimum: 1, maximum: 200 },
      }, required: ["conversation_id"] },
      handler: async (c, a, args) => {
        if (!UUID.test(String(args.conversation_id || ""))) {
          throw new ToolError({ error: "doc_conversation_not_found" });
        }
        const r = await c.query(
          "select ops.doc_conversation_facts($1::uuid,$2::text,$3::integer,$4::integer) as facts",
          [args.conversation_id, a.id, args.after_sequence ?? 0, args.limit ?? 200]);
        if (!r.rows[0]?.facts?.ok) throw new ToolError({ error: "doc_conversation_not_found" });
        return docConversationProjection(r.rows[0].facts, ToolError);
      },
    },

    "list-doc-conversations": {
      // A READ. NO write flag: mcp.js opens `begin read only` for a tool that
      // declares writerConnection without write, which is the transaction
      // ops.list_doc_conversations is written for. The writer connection is
      // still the right one, because it is the only path that installs the
      // acting-actor context the definer derives the actor from.
      writerConnection: true,
      description: "List the Doc conversations the signed-in actor may see: the ones they created and the ones granted to them and not revoked, pinned first then most recently updated, paged by cursor.",
      // NOTHING HERE NAMES AN ACTOR, and there is no offset and no sort field.
      // Every field is optional: the first page of the signed-in partner's own
      // list is {}. additionalProperties:false turns an attempt to name an
      // actor into a schema error, which is the point.
      inputSchema: { type: "object", additionalProperties: false, properties: {
        cursor:           { type: "string" },
        limit:            { type: "integer", minimum: 1, maximum: 100 },
        include_archived: { type: "boolean" },
      } },
      // `a` is deliberately unused: unlike read-doc-conversation above, which
      // passes a.id, this handler passes NO actor at all. That absence is the
      // criterion -- grep this handler for a.id and find nothing.
      handler: async (c, a, args) => {
        const r = await c.query(
          "select ops.list_doc_conversations($1::text,$2::integer,$3::boolean) as listed",
          [args.cursor ?? null, args.limit ?? null, args.include_archived ?? null]);
        const listed = r.rows[0]?.listed;
        if (!listed || listed.ok !== true) {
          throw new ToolError({ error: listed?.reason_id || "doc_conversation_not_found" });
        }
        return docConversationListProjection(listed, ToolError);
      },
    },
  };
}
