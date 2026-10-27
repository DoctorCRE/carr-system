// Convert internal errors into closed public error responses.
import { ToolError } from "./tool-error.ts";
import { isValidCorrelationId, logLine } from "./correlation.js";

/** The public JSON-RPC/HTTP refusal shape. Every optional field is opt-in per code. */
type PublicError = {
  error: string; correlation_id: string; hint: string;
  candidates?: Record<string, string | null>[]; retired_aliases?: Record<string, string | null>[];
  current_version?: number; fields?: unknown[]; invalid_deal_ids?: string[];
};
type InputSchema = { properties?: Record<string, InputSchema>; items?: InputSchema };

// Public shapes are deliberately closed. Never copy messages, hints, wrapped
// details, event rows, driver codes, or arbitrary properties from a refusal.
const PUBLIC_ERRORS = new Map<unknown, string>([
  ["version_conflict", "Re-read the record and ask the human before retrying."],
  ["next_action_conflict", "Re-read get-next-action with ref and owner. Review the current action before retrying set-next-action with its id and version, or null if absent, in expected_action."],
  ["needs_confirm", "Ask the human to confirm before resubmitting with confirm:true."],
  ["not_found", "The requested record was not found."],
  ["doctrine_doc_not_found", "The document was not found. Use doctrine-index to choose a current document id or slug for the document argument."],
  ["needs_disambiguation", "Choose an exact record reference from the candidates before retrying."],
  ["decline_claims_completion", "Use complete-work-request to close built work. Decline only work that will not be built."],
  ["loop_not_open", "Re-read the loop before choosing another action."],
  ["personal_doc_not_owner", "This personal doctrine belongs to another owner."],
  ["personal_loop_not_owner", "This personal loop belongs to another owner."],
  ["unauthorized", "Authentication is required."],
  ["invalid_call_context_deals", "Choose only open, active deal UUIDs from the current deal board."],
  ["invalid_field_value", "Check the named fields against the tool's documented input."],
  ["invalid_report_problem", "Supply a UUID idempotency_key, non-empty situation (at most 1000 characters), title (200), desired_outcome (2000), and 1 through 12 acceptance criteria."],
  ["invalid_report_problem_fields", "Remove fields outside the documented input for report-problem."],
  ["invalid_acceptance_criteria", "Supply criteria with only id and text; use unique uppercase letters, digits, or hyphens for ids and non-empty text of at most 500 characters."],
  ["current_situation_source_not_found", "Revise situation to match a current shared doctrine source, then retry report-problem."],
  ["validation_error", "Check the named fields against the tool's documented input."],
  ["value_not_in_declared_vocabulary", "Use a value from the field's documented vocabulary."],
  ["missing_idempotency_key", "Supply a fresh idempotency_key for this operation."],
  ["missing_base_version", "Re-read the record and supply its base_version."],
  ["missing_plan_base", "Review this older call proposal before applying it."],
  ["newer_change_exists", "The record changed after this proposal. Review its current state before deciding."],
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
  ["dispatch_ack_not_authorized", "Only the dispatch's delivering desk or its target session may acknowledge it."],
  ["AUTHORIZATION_REFUSED", "This operation requires partner authority."],
  ["vendor_meetings_query_invalid", "Check the vendor meeting filters and restart paging if the query changed."],
  ["vendor_meetings_unavailable", "Vendor meetings are temporarily unavailable. Quote the correlation_id when reporting it."],
  ["tour_share_scope_invalid", "Choose one or more documented tour share scopes, each once."],
  ["database_refused_the_statement", "The operation could not be completed. Quote the correlation_id when reporting it."],
  ["doctrine_version_invalid", "The doctrine document contains a version that cannot be represented safely. Quote the correlation_id when reporting it."],
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

function publicCandidates(candidates: unknown, shapes = CANDIDATE_SHAPES): Record<string, string | null>[] {
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

function inputFields(schema: InputSchema | undefined, names = new Set<string>()) {
  for (const [name, child] of Object.entries(schema?.properties || {})) {
    names.add(name);
    inputFields(child, names);
  }
  if (schema?.items) inputFields(schema.items, names);
  return names;
}

function diagnostics(error: unknown) {
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
export function publicError(error: unknown, { env, inputSchema, fallback = "unhandled_verb_failure" }:
  { env?: { CORRELATION_ID?: string }; inputSchema?: InputSchema; fallback?: string } = {}) {
  const correlationId = isValidCorrelationId(env?.CORRELATION_ID)
    ? env!.CORRELATION_ID!.trim().toLowerCase() : crypto.randomUUID();
  if (env) env.CORRELATION_ID = correlationId;
  logLine("error", "request_error", { correlation_id: correlationId, diagnostic: diagnostics(error) });
  const payload = error instanceof ToolError ? error.payload : null;
  const code = PUBLIC_ERRORS.has(payload?.error) ? payload!.error : fallback;
  const result: PublicError = { error: code, correlation_id: correlationId,
    hint: PUBLIC_ERRORS.get(code) || "The operation failed. Quote the correlation_id when reporting it." };
  if (code === "needs_disambiguation") {
    result.candidates = publicCandidates(payload?.candidates);
    if (Array.isArray(payload?.retired_aliases))
      result.retired_aliases = publicCandidates(payload.retired_aliases, [["ref", "name"]]);
  }
  if (code === "invalid_call_context_deals")
    result.invalid_deal_ids = [...new Set((Array.isArray(payload?.invalid_deal_ids) ? payload.invalid_deal_ids : [])
      .filter(id => typeof id === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/iu.test(id)))].slice(0, 50);
  if (code === "version_conflict" && Number.isSafeInteger(payload?.current_version) && (payload!.current_version as number) >= 0)
    result.current_version = payload!.current_version as number;
  if (["invalid_report_problem", "invalid_report_problem_fields", "invalid_acceptance_criteria", "current_situation_source_not_found", "invalid_field_value", "validation_error", "value_not_in_declared_vocabulary", "unregistered_operation_fields"].includes(code)) {
    const allowed = inputFields(inputSchema);
    const candidates = [payload?.field, payload?.column,
      ...(Array.isArray(payload?.fields) ? payload.fields : []),
      ...(Array.isArray(payload?.columns) ? payload.columns : [])];
    result.fields = [...new Set(candidates.filter(field => allowed.has(field)))];
    if (code === "invalid_field_value" && result.fields.length === 0) {
      result.error = "database_refused_the_statement";
      result.hint = PUBLIC_ERRORS.get(result.error)!;
      delete result.fields;
    }
  }
  return result;
}
