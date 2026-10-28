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
  ["email_draft_failure_not_resubmittable", "This failed draft cannot be resubmitted. Review the failure before creating a new draft."],
  ["email_source_owner_mismatch", "Use the mailbox owner that matches the email source."],
  ["email_proposal_confidence_invalid", "Provide a confidence between zero and one."],
  ["email_proposal_fields_invalid", "Check the fields for the proposal kind."],
  ["email_proposal_source_missing", "Review the email source before creating a draft."],
  ["email_proposal_quote_invalid", "Use an exact quote from the email."],
  ["email_proposal_evidence_invalid", "Review the email evidence and create a new proposal."],
  ["email_proposal_deal_invalid", "Choose an eligible deal and review the proposal again."],
  ["email_proposal_not_pending", "Re-read the proposal before confirming."],
  ["email_proposal_stale", "Re-read and review the current proposal before confirming."],
  ["email_sender_contact_required", "Create the sender contact before confirming the filing."],
  ["partner_confirmation_required", "A verified partner must confirm this action."],
  ["email_thread_filing_owner_unknown", "The filing owner is unknown, so it cannot be undone."],
  ["email_thread_filing_not_found", "Choose an available automatic filing."],
  ["email_thread_filing_changed", "The email filing has changed. Review it before making changes."],
  ["email_thread_filing_suppressed", "This email was previously unfiled by a partner."],
  ["email_thread_not_continuous", "Review the email and choose a deal."],
  ["email_thread_ambiguous", "Review the email and choose a deal."],
  ["email_thread_recipient_changed", "Review the email and choose a deal."],
  ["email_new_deal_not_found", "Choose an existing deal before confirming."],
  ["email_new_deal_client_mismatch", "Choose a deal belonging to the email client."],
  ["version_conflict", "Re-read the record and ask the human before retrying."],
  ["next_action_conflict", "Re-read get-next-action with ref and owner. Review the current action before retrying set-next-action with its id and version, or null if absent, in expected_action."],
  ["needs_confirm", "Ask the human to confirm before resubmitting with confirm:true."],
  ["not_found", "The requested record was not found."],
  ["unsupported_decision_about", "The about field accepts lead, client, vendor, party, and deal references or names; Work Request references are not supported. Omit about and name the Work Request in the rationale."],
  ["decision_about_work_request_not_found", "The Work Request in the about field was not found. Check its reference, or omit about and name the Work Request in the rationale."],
  ["work_request_not_found", "Re-read the Work Request reference before retrying."],
  ["work_request_terminal_state", "The Work Request is already terminal. Re-read its card before choosing another action."],
  ["work_request_not_completable", "Complete only a captured, triaged, ready, or verification Work Request."],
  ["invalid_completion", "Supply the documented completion inputs and a current base_version."],
  ["missing_criterion_evidence", "Supply exactly one non-empty evidence line for every acceptance criterion id on the Work Request."],
  ["missing_change_ref", "Supply a merged pull request URL and its full merge commit SHA for each change reference."],
  ["missing_user_journey_ref", "User-facing completion requires a reference to an exercised user journey."],
  ["completion_capsule_refused", "The completion evidence was refused. Supply complete evidence and an attestor independent of the executor or owner."],
  ["sponsored_actor_required", "An authenticated partner or sponsored agent must perform this operation."],
  ["memory_attach_required", "Call attach-memory first, then pass the attach_id it returned to this actor when writing or retiring team-memory."],
  ["memory_read_only", "This attach is read_only. Call attach-memory with access read_write, then retry with the new attach_id."],
  ["convention_proposal_not_found", "Choose an active proposal from governance-queue pending_convention_proposals."],
  ["convention_proposal_malformed", "Rewrite the proposal so its first line is 'Convention: <statement>' with an optional 'Card: <json>' line, then adopt it."],
  ["convention_card_already_active", "An active rule already carries this card id; retire it before adopting a replacement."],
  ["convention_pack_unknown", "The proposal names a pack outside the existing pack index; name only packs listed by standing-context."],
  ["rule_adoption_requires_app_approval", "Adopt conventions in the DoctorCRE app's approval queue; chat and agent sessions cannot adopt them."],
  ["rule_adoption_requires_joe","Rule activation runs on Joe's approval chain; Joe adopts this proposal in person."],
  ["rule_id_required", "Supply a rule ID."],
  ["rule_id_malformed", "Supply a full rule UUID or a valid short rule ID."],
  ["rule_not_found", "Choose an existing rule ID."],
  ["ambiguous_rule_id", "Use a longer rule ID to identify one rule."],
  ["decision_id_required", "Provide a decision id: the full uuid or its 8-character short form."],
  ["decision_id_malformed", "Use the full decision uuid or the 8-character short form this system prints."],
  ["decision_not_found", "No decision id begins with that prefix. Check decision-history; log-decision returns the decision_id, not the event_id."],
  ["ambiguous_decision_id", "That prefix matches more than one decision. Pass more characters or the full decision id."],
  ["doctrine_doc_not_found", "The document was not found. Use doctrine-index to choose a current document id or slug for the document argument."],
  ["needs_disambiguation", "Choose an exact record reference from the candidates before retrying."],
  ["decline_claims_completion", "Use complete-work-request to close built work. Decline only work that will not be built."],
  ["loop_not_open", "Re-read the loop before choosing another action."],
  ["action_not_open", "Re-read the current action. It is already closed; review its status before choosing another action."],
  ["critical_date_not_open", "Re-read the current critical date. It is already resolved; review its status before choosing another action."],
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
  ["missing_required", "Supply the named required fields from the tool's documented input."],
  ["invalid_boolean", "Supply a boolean value for the named fields."],
  ["invalid_number", "Supply a numeric value for the named fields."],
  ["tool_call_markup_in_value", "Remove tool-call markup from the named fields."],
  ["value_not_in_declared_vocabulary", "Use a value from the field's documented vocabulary."],
  ["missing_idempotency_key", "Supply a fresh idempotency_key for this operation."],
  ["missing_base_version", "Re-read the record and supply its base_version."],
  ["invalid_base_version", "Supply an integer base_version from a fresh read."],
  ["missing_plan_base", "Review this older call proposal before applying it."],
  ["newer_change_exists", "The record changed after this proposal. Review its current state before deciding."],
  ["invalid_work_queue_fields", "Use only the documented queue fields and positive 32-bit slice, job, and position numbers."],
  ["invalid_work_request", "Supply a numeric Work Request reference such as WR-000014."],
  ["invalid_idempotency_key", "Supply a UUID as idempotency_key and reuse it only for the same action."],
  ["invalid_slice_plan", "Supply one through one hundred non-empty slice titles, each at most 200 characters."],
  ["slice_plan_conflict", "Use the existing ordered slices; replacing their identities is refused."],
  ["slice_not_found", "Choose a slice from the recorded plan."],
  ["invalid_card_title", "Supply a card title of two or three plain words."],
  ["invalid_job_description", "Describe the job in one sentence."],
  ["job_already_started", "This job already has a runner; choose the next unstarted queue job."],
  ["job_repository_mismatch", "Start this job in its recorded repository."],
  ["job_not_found", "Choose an existing job under this Work Request and slice."],
  ["priority_out_of_range", "Choose a position within the visible queue."],
  ["census_inventory_invalid", "Send a JSON object with an items array under 8 MB."],
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
  ["not_a_deal", "Choose a deal record, not a client or lead."],
  ["portal_slug_invalid", "Use lowercase letters, digits and hyphens for the portal name, starting with a letter or digit, at most 63 characters."],
  ["portal_password_invalid", "Supply a portal password of 1 to 1024 characters."],
  ["portal_deal_not_found", "The deal was not found or its client was merged. Re-read the deal before retrying."],
  ["portal_slug_taken", "Choose another portal name. This one is already in use."],
  ["portal_password_differs", "This deal already has that portal name with a different password. Use the original password to retry."],
  ["portal_deal_already_registered", "This deal already has an active client portal. Open its link instead of registering another."],
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

// Row values travel in these: pg's "Failing row contains (...)" and ToolError payloads.
const UNLOGGED = new Set(["detail", "hint", "where", "payload"]);

function diagnostics(error: unknown) {
  const seen = new WeakSet();
  return JSON.parse(JSON.stringify(error, (_key, value) => {
    if (value && typeof value === "object") {
      if (seen.has(value)) return "[circular]";
      seen.add(value);
      if (value instanceof Error) return {
        ...Object.fromEntries(Object.entries(Object.getOwnPropertyDescriptors(value))
          .filter(([key, descriptor]) => "value" in descriptor && !UNLOGGED.has(key))
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
  if (["unsupported_decision_about", "decision_about_work_request_not_found"].includes(code) &&
      typeof payload?.ref === "string" && /^WR-(?:[0-9]{1,12}|AI-[0-9]{3}|SIEP-[0-9A-Z]{2,3})$/i.test(payload.ref))
    result.hint += ` Reference: ${payload.ref}.`;
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
  if (["invalid_idempotency_key", "invalid_report_problem", "invalid_report_problem_fields", "invalid_acceptance_criteria", "current_situation_source_not_found", "invalid_field_value", "validation_error", "value_not_in_declared_vocabulary", "unregistered_operation_fields", "missing_required", "invalid_boolean", "invalid_number", "tool_call_markup_in_value"].includes(code)) {
    const allowed = inputFields(inputSchema);
    const candidates = [payload?.field, payload?.column,
      ...(Array.isArray(payload?.fields) ? payload.fields : []),
      ...(Array.isArray(payload?.missing) ? payload.missing : []),
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
