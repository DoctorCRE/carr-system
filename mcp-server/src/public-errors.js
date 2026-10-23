import { ToolError } from "./tool-error.js";
import { isValidCorrelationId, logLine } from "./correlation.js";

// Public shapes are deliberately closed. Never copy messages, hints, wrapped
// details, event rows, driver codes, or arbitrary properties from a refusal.
const PUBLIC_ERRORS = new Map([
  ["version_conflict", "Re-read the record and ask the human before retrying."],
  ["needs_confirm", "Ask the human to confirm before resubmitting with confirm:true."],
  ["not_found", "The requested record was not found."],
  ["needs_disambiguation", "Choose an exact record reference from the candidates before retrying."],
  ["loop_not_open", "Re-read the loop before choosing another action."],
  ["unauthorized", "Authentication is required."],
  ["invalid_field_value", "Check the named fields against the tool's documented input."],
  ["validation_error", "Check the named fields against the tool's documented input."],
  ["value_not_in_declared_vocabulary", "Use a value from the field's documented vocabulary."],
  ["missing_idempotency_key", "Supply a fresh idempotency_key for this operation."],
  ["missing_base_version", "Re-read the record and supply its base_version."],
  ["key_reuse", "Use a fresh idempotency_key for changed arguments."],
  ["unknown_tool", "Discover the tool with list-verbs."],
  ["not_in_profile", "This operation is unavailable in the current profile."],
  ["human_approval_required", "This operation requires partner authority."],
  ["human_confirmation_required", "Ask the human to confirm this operation."],
  ["human_only", "This operation requires partner authority."],
  ["human_only_verb_requires_verified_partner", "This operation requires verified partner authority."],
  ["unattended_runner_authority_not_admitted", "This operation is unavailable to unattended sessions."],
  ["actor_not_provisioned", "The authenticated actor is not provisioned."],
  ["authority_connection_unavailable", "Partner authority is unavailable for this operation."],
  ["caller_authority_field_forbidden", "Remove caller-supplied authority fields."],
  ["unregistered_operation_fields", "Remove fields outside the documented input."],
  ["unregistered_operation", "This operation is unavailable."],
  ["mutation_contract_mismatch", "This operation is temporarily unavailable."],
  ["missing_verb", "Supply the verb to call."],
  ["no_recursion", "Nested dispatcher calls are unavailable."],
  ["unparseable_args", "Supply a valid JSON argument object."],
  ["args_not_an_object", "Supply an argument object."],
  ["database_refused_the_statement", "The operation could not be completed. Quote the correlation_id when reporting it."],
]);

const CANDIDATE_SHAPES = [
  ["loop_id", "kind", "section", "renders_into"],
  ["party_id", "party_name", "party_city", "party_state"],
  ["id", "address", "city"],
  ["name", "phase", "client_ref"],
  ["name", "ref", "kind", "city"],
  ["ref", "name"],
  ["name"],
];

function publicCandidates(candidates, shapes = CANDIDATE_SHAPES) {
  if (!Array.isArray(candidates)) return [];
  return candidates.flatMap(candidate => {
    if (!candidate || typeof candidate !== "object") return [];
    const fields = shapes.find(shape => shape.every(field => Object.hasOwn(candidate, field)));
    if (!fields) return [];
    const result = Object.fromEntries(fields
      .filter(field => candidate[field] === null || typeof candidate[field] === "string")
      .map(field => [field, candidate[field]]));
    return Object.keys(result).length ? [result] : [];
  });
}

function inputFields(schema, names = new Set()) {
  for (const [name, child] of Object.entries(schema?.properties || {})) {
    names.add(name);
    inputFields(child, names);
  }
  if (schema?.items) inputFields(schema.items, names);
  return names;
}

function diagnostics(error) {
  const seen = new WeakSet();
  return JSON.parse(JSON.stringify(error, (_key, value) => {
    if (value && typeof value === "object") {
      if (seen.has(value)) return "[circular]";
      seen.add(value);
      if (value instanceof Error) return {
        ...Object.fromEntries(Object.entries(Object.getOwnPropertyDescriptors(value))
          .filter(([, descriptor]) => "value" in descriptor)
          .map(([key, descriptor]) => [key, descriptor.value])),
        name: value.name, message: value.message, stack: value.stack,
      };
    }
    return typeof value === "bigint" ? String(value) : value;
  }) ?? "null");
}

/** The sole projection for caught MCP/HTTP failures. Full diagnostics stay in
 * server logs. Validation fields come from the input schema; disambiguation
 * choices use curated record shapes. */
export function publicError(error, { env, inputSchema, fallback = "unhandled_verb_failure" } = {}) {
  const correlationId = isValidCorrelationId(env?.CORRELATION_ID)
    ? env.CORRELATION_ID.trim().toLowerCase() : crypto.randomUUID();
  if (env) env.CORRELATION_ID = correlationId;
  logLine("error", "request_error", { correlation_id: correlationId, diagnostic: diagnostics(error) });
  const payload = error instanceof ToolError ? error.payload : null;
  const code = PUBLIC_ERRORS.has(payload?.error) ? payload.error : fallback;
  const result = { error: code, correlation_id: correlationId,
    hint: PUBLIC_ERRORS.get(code) || "The operation failed. Quote the correlation_id when reporting it." };
  if (code === "needs_disambiguation") {
    result.candidates = publicCandidates(payload?.candidates);
    if (Array.isArray(payload?.retired_aliases))
      result.retired_aliases = publicCandidates(payload.retired_aliases, [["ref", "name"]]);
  }
  if (code === "version_conflict" && Number.isSafeInteger(payload?.current_version) && payload.current_version >= 0)
    result.current_version = payload.current_version;
  if (["invalid_field_value", "validation_error", "value_not_in_declared_vocabulary", "unregistered_operation_fields"].includes(code)) {
    const allowed = inputFields(inputSchema);
    const candidates = [payload?.field, payload?.column,
      ...(Array.isArray(payload?.fields) ? payload.fields : []),
      ...(Array.isArray(payload?.columns) ? payload.columns : [])];
    result.fields = [...new Set(candidates.filter(field => allowed.has(field)))];
  }
  return result;
}
