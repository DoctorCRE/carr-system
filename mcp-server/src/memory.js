import { personalScopeForActor, organizationTenantForActor } from "./identity.js";

import { integerFields } from "./safe-integer.js";

function sponsorFor(actor, ToolError) {
  const scope = personalScopeForActor(actor);
  if (scope.status === "error") throw new ToolError({ error: scope.error });
  return scope;
}

function recallLimit(value, ToolError) {
  if (value === undefined) return 20;
  if (!Number.isInteger(value) || value < 1 || value > 100)
    throw new ToolError({ error: "memory_limit_invalid", hint: "limit must be an integer from 1 through 100" });
  return value;
}

function validText(value, field, ToolError) {
  if (typeof value !== "string" || !value.trim())
    throw new ToolError({ error: "memory_field_required", field });
  return value.trim();
}

function memoryRecord(row) {
  return integerFields(row, ["version", "work_request_version", "predecessor_version"], "memory_version_invalid",
    { nullable: ["work_request_version", "predecessor_version"] });
}

export function memoryTools({ ToolError, assertNoCallerAuthorityFields }) {
  const guard = args => { if (assertNoCallerAuthorityFields) assertNoCallerAuthorityFields(args); };
  return {

    "recall-memory": {
      // Runs on the writer connection so the sponsor setting is present. Both
      // partners may READ every memory (0579), but recall is where memory is
      // APPLIED, so the query below keeps it to shared rows and the caller's
      // own personal rows.
      writerConnection: true,
      description: "Recall promoted memories relevant to a context, combining shared memories with the authenticated partner's personal scope. Candidates require review and are never returned by autonomous recall; memory is context only and never authority.",
      inputSchema: { type: "object", properties: {
        query: { type: "string" }, context: { type: "string" }, limit: { type: "integer" },
      }, required: ["query"] },
      handler: async (c, actor, args) => {
        guard(args);
        const limit = recallLimit(args.limit, ToolError);
        const query = validText(args.query, "query", ToolError);
        const scope = sponsorFor(actor, ToolError);
        const r = await c.query(
          `select id, kind, statement, context, scope, confidence, status, version, created_at,
                  ts_rank(search_vector, plainto_tsquery('english',$1)) as relevance
             from memory_item
            where organization_tenant_id=$3 and status='promoted'
              and (scope='shared' or (scope='personal' and owner_actor_id=public.retrieval_visibility_actor_id($2)))
              and search_vector @@ plainto_tsquery('english',$1)
            order by (ts_rank(search_vector, plainto_tsquery('english',$1)) +
                      0.5 * ts_rank(to_tsvector('english', coalesce(context,'')), plainto_tsquery('english',coalesce($4,'')))) desc,
                     confidence desc, created_at desc limit $5`,
          [query, scope.sponsor, organizationTenantForActor(actor), args.context || "", limit]);
        return { ok: true, query: args.query, context: args.context || null, memories: r.rows.map(memoryRecord), count: r.rows.length };
      },
    },

  };
}
