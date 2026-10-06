// DoctorCRE v5 V5-RW02 — the browser-only Salesforce READ adapter and the
// day-to-day reconciliation pass, READ-ONLY toward Salesforce.
//
// THE RULINGS THIS FILE ENCODES, read fresh from the record layer:
//
//   c04ac197  Dell's Salesforce is the primary source. A deal there without Joe
//             on it becomes an action to get Joe added, never a silent skip.
//   c0014a37  No continuous field sync. Day to day, reconciliation checks only
//             deal PRESENCE and PARTNER MEMBERSHIP. Field values are a
//             pre-invoice step, and that step is not in this file. A row that
//             arrives carrying a field value is a POLICY CONFLICT and stops.
//   493de438  A kind of update turns autonomous only after 5 CONSECUTIVE clean
//             attended runs of that kind; any unclean run resets the count.
//             Every run's outcome is recorded server-side
//             (record-salesforce-run-outcome); nothing here promotes a kind.
//   9b2e2011  Browser only. No API, connector or MCP route to Salesforce.
//   9ea10e5b  Sign-in: the system never types, reads, copies, logs or stores a
//   4e1efae4  password or a code. It MAY press Log in on credentials the
//             browser autofilled, and for a sign-in IT started it MAY click the
//             code field or the From-Messages suggestion so macOS fills the
//             code, at most 3 attempts. Any other challenge stops the run.
//   bf194d7d  Dell's consent to these reads, relayed by Joe. Dell's accounts
//             need Dell's own OK: a run is allowed only while THAT decision
//             record exists, is a partner's record with the partner's literal
//             words, and has not been revoked (revoke-salesforce-read-consent).
//
// SAFE STOPS ARE TYPED (checkable_done 1). Every stop the run can reach
// returns `answer_kind: "rw02-safe-stop.v1"` with a registered `stop_class`
// (V5_RW02_SAFE_STOP_CLASSES), a registered `reason_id`, a plain-language
// `message`, `partial_writes: 0` and `findings_recorded: 0`. The only records
// a stop leaves are its own receipts: the kernel page stop, when the kernel
// ladder is what stopped, and the run's unclean outcome. A stop never files a
// finding, because a finding from part of a list is a guess.
//
// NO PARTIAL WRITES. Findings are PLANNED in full, every planned write is
// checked (recipient, scope, credential shape), and only then is the first one
// sent. A check that fails on the last planned finding stops the run before
// the first finding is written.
//
// READ MODE IS STRUCTURAL, NOT A FLAG. The runner reaches the browser only
// through readOnlyReader() (observe, nextPage) and, for sign-in only, through
// signInOperator() (the Log in press and the four code-step clicks). Both are
// frozen null-prototype facades that forward no arguments. It writes to the
// record layer only through readModeRecorder(), which admits four named verbs.
//
// IDENTITY AND TIME ARE NOT ARGUMENTS. The org, seat, Joe's Salesforce user ref
// and the expected UI fingerprint come from a server-side binding source; the
// consent comes from the decision record; a sign-in ticket's time comes from
// the database clock, read at the moment Log in is pressed. A caller-supplied
// time, consent flag, partner, org or capability is refused.
//
// REPEAT RUNS ARE REPLAYS; REOPENED LOOPS ARE NEW. Every finding is keyed on
// org + opportunity (or deal + reason) and carries no run-specific text, so a
// repeat run replays. A loop filed for a finding and since CLOSED, whose
// finding is true again, is filed under the next episode key: a new action.

import { randomUUID } from "node:crypto";

import { digest } from "./artifact-trust.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import {
  V5_RW02_ACTION_KIND_KEYS,
  V5_RW02_CREDENTIAL_PATTERNS,
  V5_RW02_ORG_BINDING_SEAM,
  evaluatePageObservation,
} from "./salesforce-reconciliation-rw02.v5.js";

export const V5_RW02_BROWSER_READ_SCHEMA_VERSION = "doctorcre-v5-rw02-browser-read.v2";

/** The ONLY driver methods read mode can reach. */
export const V5_RW02_READER_METHODS = Object.freeze(["nextPage", "observe"]);

/** The ONLY driver methods the sign-in step can reach (rulings 9ea10e5b, 4e1efae4). */
export const V5_RW02_SIGN_IN_OPERATOR_METHODS = Object.freeze([
  "autofillSuggestionVisible", "clickAutofillSuggestion", "clickCodeField", "clickLogIn", "codeFieldFilled",
]);

/** The ONLY record-layer verbs read mode can call. */
export const V5_RW02_READ_MODE_RECORD_VERBS = Object.freeze([
  "add-loop", "record-finding", "record-salesforce-page-stop", "record-salesforce-run-outcome",
]);

/** Decision 493de438. */
export const V5_RW02_AUTONOMY_THRESHOLD = 5;
export const V5_RW02_RUN_OUTCOMES = Object.freeze(["clean", "corrected", "failed", "refused", "stopped"]);

/** The kind a presence-and-membership read run is counted under. */
export const V5_RW02_READ_RUN_KIND = "presence_membership_reconciliation";

/** Every kind the server-side counter keeps: the kernel's write kinds plus the read run. */
export const V5_RW02_AUTONOMY_KINDS = Object.freeze([...V5_RW02_ACTION_KIND_KEYS, V5_RW02_READ_RUN_KIND].sort());

/** Joe's code-step refinement: at most three attempts per sign-in. */
export const V5_RW02_CODE_AUTOFILL_MAX_ATTEMPTS = 3;

/** A read that has not ended by this page is not trusted to be a whole list. */
export const V5_RW02_READ_PAGE_CAP = 50;

/**
 * The decision record that carries Dell's consent (logged by Joe, relaying
 * Dell's OK). Pinned by review; the record itself is re-read on every
 * run, so revoking it stops the next run without a code change.
 */
export const V5_RW02_DELL_CONSENT_DECISION_ID = "bf194d7d-4b33-4683-a320-6b5a8c05766d";

/** Whose decision records may carry a partner's consent. */
export const V5_RW02_CONSENT_SPONSORS = Object.freeze(["dell", "joe"]);

export const V5_RW02_SIGN_IN_STATES = Object.freeze([
  "signed_in", "password_prompt", "code_prompt", "captcha", "new_device_prompt", "security_challenge", "unknown",
]);

export const V5_RW02_SIGN_IN_STOP_REASONS = Object.freeze([
  "captcha",
  "code_attempts_exhausted",
  "code_fill_state_unobservable",
  "log_in_not_offered",
  "new_device_prompt",
  "partner_sign_in_required",
  "password_prompt_without_autofill",
  "security_challenge",
  "sign_in_state_unobservable",
  "sign_in_ticket_spent",
  "sign_in_ticket_stale",
  "unprompted_code_prompt",
]);

/**
 * The layout a read is bound to. Each name is a structural anchor the driver
 * reports as matched on the page; the adapter, not the driver, digests the
 * matched set with the layout ref into the page's UI fingerprint.
 */
export const V5_RW02_READ_UI_SELECTORS = Object.freeze([
  "list_view_table",
  "next_page_control",
  "opportunity_id_cell",
  "opportunity_owner_cell",
  "opportunity_row",
  "opportunity_team_cell",
  "signed_in_user_menu",
]);

/** Sign-in state -> the kernel challenge the page stop is recorded under. */
const KERNEL_CHALLENGE = Object.freeze({
  password_prompt: "login_required",
  code_prompt: "mfa_challenge",
  captcha: "captcha",
  new_device_prompt: "mfa_challenge",
  security_challenge: "captcha",
  unknown: "unstated",
});

// ---------------------------------------------------------------------------
// The typed safe-stop vocabulary.
// ---------------------------------------------------------------------------

export const V5_RW02_SAFE_STOP_CLASSES = Object.freeze([
  "auth_challenge",
  "binding",
  "consent",
  "inconsistent_result",
  "policy_conflict",
  "ui_drift",
  "unexpected_recipient_or_account",
]);

// Every partner-facing message has three parts, in this order: what the system
// saw, what it did about it (the outcome, fixed per stage so no message can
// claim a different one), and what the partner does next.
const RETRY = "then start the read again.";
const SIGN_IN_YOURSELF = `Sign in yourself in the browser, ${RETRY}`;
const RETRY_THEN_REPORT = "Start the read again once; if it stops the same way, report it so the reviewed layout is updated.";
const REVIEWED_CONSENT = "Report it; a consent record has to be reviewed before this read can run.";
const SAFE_STOPS_SOURCE = Object.freeze({
  // Sign-in and challenges the rulings do not cover.
  captcha: ["auth_challenge", "Salesforce showed a CAPTCHA, which the system never solves.", `Clear it yourself in the browser, ${RETRY}`],
  security_challenge: ["auth_challenge", "Salesforce showed a security check the system does not answer.", `Complete it yourself in the browser, ${RETRY}`],
  new_device_prompt: ["auth_challenge", "Salesforce asked to verify this device, which only a partner may approve.", `Approve it yourself in the browser, ${RETRY}`],
  code_attempts_exhausted: ["auth_challenge", "The verification code did not fill after 3 tries, and the system does not try a fourth time.", SIGN_IN_YOURSELF],
  code_fill_state_unobservable: ["auth_challenge", "The system could not tell whether the verification code filled, so it stopped instead of guessing.", SIGN_IN_YOURSELF],
  unprompted_code_prompt: ["auth_challenge", "Salesforce asked for a code the system did not request; it uses the code step only right after it presses Log in itself.", SIGN_IN_YOURSELF],
  password_prompt_without_autofill: ["auth_challenge", "Salesforce asked for a password and the browser did not fill it; the system never types one.", SIGN_IN_YOURSELF],
  partner_sign_in_required: ["auth_challenge", "Salesforce needs a sign-in, and this run was started without the system's sign-in step.", SIGN_IN_YOURSELF],
  log_in_not_offered: ["auth_challenge", "Salesforce asked for sign-in but showed no Log in button the system may press.", SIGN_IN_YOURSELF],
  sign_in_not_accepted: ["auth_challenge", "Salesforce asked for sign-in again after the system pressed Log in once, and it does not press twice.", SIGN_IN_YOURSELF],
  sign_in_prompt_mid_read: ["auth_challenge", "Salesforce asked for sign-in partway through the list; the system signs in only before the first page.", SIGN_IN_YOURSELF],
  sign_in_state_unobservable: ["auth_challenge", "The system could not tell whether Salesforce is signed in, so it stopped instead of guessing.", `Check the browser and sign in if needed, ${RETRY}`],
  sign_in_ticket_spent: ["auth_challenge", "The code step for this sign-in was already used, and the system does not repeat it.", SIGN_IN_YOURSELF],
  sign_in_ticket_stale: ["auth_challenge", "More than two minutes passed since the system pressed Log in, so it did not use the code step.", SIGN_IN_YOURSELF],
  authentication_challenge: ["auth_challenge", "Salesforce showed a sign-in challenge on the page.", `Resolve it yourself in the browser, ${RETRY}`],
  challenge_state_unobservable: ["auth_challenge", "The system could not tell whether the page is asking for sign-in, so it stopped instead of guessing.", `Check the browser and sign in if needed, ${RETRY}`],
  // UI drift.
  ui_drift: ["ui_drift", "The Salesforce page layout no longer matches the reviewed layout, so the system stopped instead of misreading it.", RETRY_THEN_REPORT],
  ui_selector_missing: ["ui_drift", "A part of the Salesforce list the system reads by is missing from the page.", RETRY_THEN_REPORT],
  row_shape_drift: ["ui_drift", "An opportunity row did not read as an opportunity, so the system stopped instead of guessing.", RETRY_THEN_REPORT],
  observation_shape_drift: ["ui_drift", "The page reading came back in a shape the system does not expect.", RETRY_THEN_REPORT],
  // Unexpected recipient or account.
  origin_mismatch: ["unexpected_recipient_or_account", "The browser is on a different web address than the bound Salesforce org.", `Check the address in the browser, ${RETRY}`],
  org_mismatch: ["unexpected_recipient_or_account", "The browser is signed in to a different Salesforce org than the one this read is bound to.", `Switch the browser to the bound org, ${RETRY}`],
  signed_in_account_mismatch: ["unexpected_recipient_or_account", "Salesforce is signed in as a different user than the one this read is bound to.", `Sign in as the bound user, ${RETRY}`],
  record_mismatch: ["unexpected_recipient_or_account", "The page shows a different record than the one expected.", `Return the browser to the opportunity list, ${RETRY}`],
  unexpected_recipient: ["unexpected_recipient_or_account", "The page shows people to send or share with, and a read never has recipients.", `Close that dialog in the browser, ${RETRY}`],
  finding_recipient_unexpected: ["unexpected_recipient_or_account", "A planned follow-up was addressed to someone other than the partner it belongs to.", "Report it; this is a fault in the system, not a Salesforce change."],
  // Policy conflict.
  policy_conflict: ["policy_conflict", "The page reported a conflict with CARR's rules.", "Report it so the conflict is resolved before the next read."],
  field_value_observed: ["policy_conflict", "The page reading carried deal field values, but a day-to-day read checks only presence and team membership.", "Report it; exact values are prepared only at invoice time."],
  credential_observed: ["policy_conflict", "The page reading carried something shaped like a password, code or key, which the system never holds.", "Report it before the next read."],
  finding_outside_scope: ["policy_conflict", "A planned finding fell outside what this read may record.", "Report it; this is a fault in the system, not a Salesforce change."],
  unattended_execution_excluded: ["policy_conflict", "Salesforce reads run only with a partner present, and this run was not attended.", "Start the read yourself while you are at the browser."],
  // Inconsistent results.
  inconsistent_result: ["inconsistent_result", "The page reported inconsistent results.", `Reload the list in the browser, ${RETRY}`],
  result_consistency_unobservable: ["inconsistent_result", "The system could not tell whether the page results are consistent, so it stopped instead of guessing.", `Reload the list in the browser, ${RETRY}`],
  opportunity_read_twice_differently: ["inconsistent_result", "One opportunity appeared twice with different details, so the list may have changed during the read.", "Start the read again."],
  page_cap_reached: ["inconsistent_result", "The list did not end within the page limit, so the system cannot trust that it read the whole list.", "Report it so the page limit or the list view can be reviewed."],
  loop_episode_unreadable: ["inconsistent_result", "The record layer could not say whether an earlier follow-up is open or closed.", "Start the read again later; if it stops the same way, report it."],
  // Before the browser is touched.
  org_binding_unavailable: ["binding", "No reviewed Salesforce org binding is available.", "Report it so the binding can be set up."],
  source_not_dell: ["binding", "The bound source is not Dell's Salesforce, which is the primary source.", "Report it so the binding can be corrected."],
  consent_source_unavailable: ["consent", "The system could not read Dell's consent record.", "Try again later; if it stops the same way, report it."],
  consent_flag_refused: ["consent", "Consent was passed in as a setting, but only Dell's consent decision record counts.", "Report it; this is a fault in the system."],
  dell_consent_record_missing: ["consent", "Dell's reviewed consent decision record was not found.", REVIEWED_CONSENT],
  dell_consent_revoked: ["consent", "Dell's consent has been revoked.", "The read runs again only after Dell gives consent anew and that record is reviewed."],
  dell_consent_not_partner_record: ["consent", "The consent record was not logged by Joe or Dell.", REVIEWED_CONSENT],
  dell_consent_quote_absent: ["consent", "The consent record does not carry a partner's own words.", REVIEWED_CONSENT],
});

/** The outcome sentence each stop states, fixed by where the stop happens. */
export const V5_RW02_SAFE_STOP_OUTCOMES = Object.freeze({
  before_browser: "The read did not start.",
  plan: "None of this run's findings were filed.",
  page: "Nothing was filed.",
});

const PLAN_STOPS = new Set(["finding_recipient_unexpected", "finding_outside_scope", "loop_episode_unreadable"]);

function stopOutcome(reasonId, stopClass) {
  if (stopClass === "binding" || stopClass === "consent") return V5_RW02_SAFE_STOP_OUTCOMES.before_browser;
  if (PLAN_STOPS.has(reasonId)) return V5_RW02_SAFE_STOP_OUTCOMES.plan;
  return V5_RW02_SAFE_STOP_OUTCOMES.page;
}

/** reason_id -> [stop_class, plain-language message for the partner]. */
const SAFE_STOPS = Object.freeze(Object.fromEntries(Object.entries(SAFE_STOPS_SOURCE).map(
  ([id, [cls, what, next]]) => [id, Object.freeze([cls, `${what} ${stopOutcome(id, cls)} ${next}`])])));

export const V5_RW02_SAFE_STOP_REASONS = Object.freeze(
  Object.fromEntries(Object.entries(SAFE_STOPS).map(([id, [cls]]) => [id, cls])));

export const V5_RW02_SAFE_STOP_MESSAGES = Object.freeze(
  Object.fromEntries(Object.entries(SAFE_STOPS).map(([id, [, message]]) => [id, message])));

/** The one recipient each loop finding may go to (c04ac197). */
export const V5_RW02_FINDING_RECIPIENTS = Object.freeze({
  missing_joe: Object.freeze({ kind: "team_loop", owner: "Dell" }),
  unknown_to_carr: Object.freeze({ kind: "open_loop", owner: "Joe" }),
});

const OPPORTUNITY_ID = /^006[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?$/;
const STABLE_ID = /^[A-Za-z0-9][A-Za-z0-9._:+-]{0,255}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const UNSAFE_TEXT = /[\u0000-\u001F\u007F-\u009F​-‏‪-‮⁠-⁤⁦-⁩﻿]/u;
const CREDENTIAL_KEYS = /^(?:password|passwd|pwd|passcode|code|otp|sms_code|mfa_code|token|secret|username|user_name|login|value|credential)s?$/i;

export class V5RW02BrowserReadError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5RW02BrowserReadError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) { throw new V5RW02BrowserReadError(code, message, detail); }

function plain(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}
function object(value, path) {
  if (!plain(value)) fail("invalid_shape", `${path} must be a plain object`, { path });
  return value;
}
function closed(value, allowed, path) {
  const raw = object(value, path);
  for (const key of Object.keys(raw)) {
    if (allowed.includes(key)) continue;
    if (CREDENTIAL_KEYS.test(key)) fail("credential_field_refused",
      `${path}.${key} would carry a credential; this adapter never holds one`, { path: `${path}.${key}` });
    fail("unknown_field", `unknown field ${path}.${key}`, { path, allowed });
  }
  return raw;
}
function freeze(value) {
  if (Array.isArray(value)) { value.forEach(freeze); return Object.freeze(value); }
  if (plain(value)) { Object.values(value).forEach(freeze); return Object.freeze(value); }
  return value;
}
function stableId(value, path) {
  if (typeof value !== "string" || !STABLE_ID.test(value)) fail("invalid_id", `${path} must be a stable id`, { path });
  return value;
}
function credentialShaped(value) {
  return Object.values(V5_RW02_CREDENTIAL_PATTERNS).some(pattern => pattern.test(value));
}
function safeText(value, path, max = 512) {
  if (typeof value !== "string" || !value.trim() || value.length > max || UNSAFE_TEXT.test(value))
    fail("invalid_text", `${path} must be 1-${max} characters of plain text`, { path });
  for (const pattern of Object.values(V5_RW02_CREDENTIAL_PATTERNS))
    if (pattern.test(value)) fail("credential_shaped_value", `${path} carries a credential shape`, { path });
  return value;
}

const EFFECTS = freeze({ ...V5_NO_EFFECTS, salesforce_writes: 0 });

// ---------------------------------------------------------------------------
// 1. The adapter contract: read-only and sign-in-only facades over a driver.
// ---------------------------------------------------------------------------

function facadeOf(driver, methods, code) {
  if (driver === null || typeof driver !== "object") fail(`${code}_unavailable`, "a browser driver is required");
  const facade = Object.create(null);
  for (const name of methods) {
    const method = driver[name];
    if (typeof method !== "function") fail(`${code}_incomplete`, `the driver has no ${name}()`, { method: name });
    // No arguments pass through: the runner cannot hand the driver a command.
    facade[name] = () => method.call(driver);
  }
  return Object.freeze(facade);
}

/**
 * The typed contract a browser driver fulfils for READ:
 *
 *   observe()  -> { sign_in: { state, credentials_autofilled? },
 *                   ui: { layout_ref, selectors_matched[] },
 *                   page: <kernel page observation WITHOUT ui_contract_digest:
 *                          origin, org_id, signed_in_account_ref, challenge,
 *                          result_consistency, recipients?, policy_conflicts?>,
 *                   opportunities: [{ opportunity_id, name, owner_ref,
 *                                     team_member_refs[] }],
 *                   has_next: boolean }
 *   nextPage() -> moves the SAME list view to its next page.
 *
 * Returns a frozen, prototype-less object holding exactly those two functions.
 */
export function readOnlyReader(driver) {
  return facadeOf(driver, V5_RW02_READER_METHODS, "reader");
}

/**
 * The sign-in facade: the Log in press and the four code-step clicks, nothing
 * else. Every method returns only what the driver reports (booleans); there is
 * no method that types, reads or returns a credential or a code.
 */
export function signInOperator(driver) {
  return facadeOf(driver, V5_RW02_SIGN_IN_OPERATOR_METHODS, "sign_in_operator");
}

/** A recorder that can reach exactly the read-mode verbs. */
export function readModeRecorder(recorder) {
  if (!recorder || typeof recorder.record !== "function") fail("recorder_unavailable", "a verb recorder is required");
  const facade = Object.create(null);
  facade.record = async (verb, args) => {
    if (!V5_RW02_READ_MODE_RECORD_VERBS.includes(verb)) fail("verb_not_permitted_in_read_mode",
      `read mode may not call ${String(verb)}`, { verb: String(verb), allowed: [...V5_RW02_READ_MODE_RECORD_VERBS] });
    return recorder.record(verb, args);
  };
  return Object.freeze(facade);
}

/**
 * The production recorder: each call goes through the SAME registered verb a
 * session would use, so the verb derives the actor and tenant itself.
 * `callVerb(name, args)` is e.g. executeRegisteredTool bound to (client, actor).
 */
export function createVerbRecorder({ callVerb } = {}) {
  if (typeof callVerb !== "function") fail("recorder_unavailable", "callVerb is required");
  return readModeRecorder({ record: (verb, args) => callVerb(verb, args) });
}

/** The production binding source until the org/seat binding seam is filled. */
export const unavailableBindingSource = Object.freeze({ read: async () => null });

/**
 * CARR deals in the absence scope, read through the narrow reconciliation view.
 * The scope is every deal not yet invoiced that is open or closed won: a won
 * deal still owes Dell's org its opportunity until the invoice goes out,
 * because corporate credit and payment depend on it (Q097.D1). Lost and paused
 * deals are out of scope; so is every deal marked invoiced.
 */
export const V5_RW02_ABSENCE_SCOPE = "open_or_closed_won_not_invoiced";
export async function readCarrDealsForReconciliation(client) {
  const r = await client.query(
    `select id, name, salesforce_id from v_deal_reconciliation_read
      where invoiced_on is null and (outcome is null or outcome = 'won') order by id`);
  return r.rows.map(row => ({ deal_id: row.id, name: row.name, salesforce_id: row.salesforce_id ?? null }));
}

// ---------------------------------------------------------------------------
// 2. Server-side sources: the clock, the consent record, loop episodes.
// ---------------------------------------------------------------------------

// Module-private registry of server clocks. A clock is an opaque frozen object;
// the function that reads the database lives here, so a caller cannot hand in
// a number, a Date or a function of its own as "the time".
const SERVER_CLOCKS = new WeakMap();

/** The database clock (clock_timestamp(), real time even inside a transaction). */
export function createServerClock(client) {
  if (!client || typeof client.query !== "function") fail("server_clock_unavailable", "a database client is required");
  const clock = Object.freeze(Object.create(null));
  SERVER_CLOCKS.set(clock, async () => {
    const r = await client.query(
      "select (floor(extract(epoch from clock_timestamp()) * 1000))::bigint::text as now_ms");
    const ms = Number(r?.rows?.[0]?.now_ms);
    if (!Number.isSafeInteger(ms) || ms < 0) fail("server_clock_unreadable", "the database clock did not answer");
    return ms;
  });
  return clock;
}

function serverClockReader(clock) {
  const read = clock !== null && typeof clock === "object" ? SERVER_CLOCKS.get(clock) : undefined;
  if (!read) fail("server_clock_required", "sign-in time comes from the server clock only");
  return read;
}

/**
 * The consent record, read from the record layer on every run. Returns
 * { record: {decision_id, sponsoring_human_slug, human_quote_present} | null,
 *   revoked: boolean }. ops.rw02_consent_record does the read.
 */
// Module-private registries: the run accepts a consent source or a loop-episode
// source only if one of the factories below made it, so a caller cannot hand in
// an object whose read() simply answers "consent in force" or "no earlier loop".
const CONSENT_SOURCES = new WeakSet();
const EPISODE_SOURCES = new WeakSet();

export function createDellConsentSource(client) {
  if (!client || typeof client.query !== "function") fail("consent_source_unavailable", "a database client is required");
  const source = Object.freeze({
    read: async () => {
      const r = await client.query("select ops.rw02_consent_record($1::uuid) as consent",
        [V5_RW02_DELL_CONSENT_DECISION_ID]);
      const consent = r?.rows?.[0]?.consent;
      return typeof consent === "string" ? JSON.parse(consent) : consent ?? null;
    },
  });
  CONSENT_SOURCES.add(source);
  return source;
}

/**
 * Earlier add-loop filings for one finding: every tool_call under the base key
 * or one of its episode keys, with the loop's status (null when the loop row
 * cannot be found). ops.rw02_loop_episodes does the read.
 */
export function createLoopEpisodeSource(client) {
  if (!client || typeof client.query !== "function") fail("finding_state_unavailable", "a database client is required");
  const source = Object.freeze({
    loopEpisodes: async baseKey => {
      const r = await client.query(
        "select idempotency_key, status from ops.rw02_loop_episodes($1::text)", [baseKey]);
      return (r?.rows ?? []).map(row => ({ idempotency_key: row.idempotency_key, status: row.status ?? null }));
    },
  });
  EPISODE_SOURCES.add(source);
  return source;
}

// ---------------------------------------------------------------------------
// 3. Sign-in: typed stops, one Log in press, the bounded code step.
// ---------------------------------------------------------------------------

function signInStop(reason_id, detail = {}) {
  return freeze({ answer_kind: "rw02-sign-in.v1", decision: "stop", reason_id, ...detail,
    stop_class: V5_RW02_SAFE_STOP_REASONS[reason_id] ?? "auth_challenge",
    resolution_owner: "partner_at_the_browser", bypass_permitted: false,
    automatic_retry_permitted: false, credential_entry_performed: false, effects: EFFECTS });
}

/**
 * Classify a sign-in observation. `systemStartedSignIn` is the RUNNER's own
 * state (it pressed Log in itself), never a driver or caller claim.
 */
export function classifySignIn(signIn, { systemStartedSignIn = false } = {}) {
  const raw = closed(signIn, ["state", "credentials_autofilled"], "sign_in");
  if (!V5_RW02_SIGN_IN_STATES.includes(raw.state)) return signInStop("sign_in_state_unobservable");
  if (raw.state === "signed_in") return freeze({ answer_kind: "rw02-sign-in.v1", decision: "continue",
    reason_id: "signed_in", credential_entry_performed: false, effects: EFFECTS });
  if (raw.state === "captcha") return signInStop("captcha");
  if (raw.state === "new_device_prompt") return signInStop("new_device_prompt");
  if (raw.state === "security_challenge") return signInStop("security_challenge");
  if (raw.state === "unknown") return signInStop("sign_in_state_unobservable");
  if (raw.state === "code_prompt") {
    if (systemStartedSignIn !== true) return signInStop("unprompted_code_prompt");
    return freeze({ answer_kind: "rw02-sign-in.v1", decision: "code_autofill_permitted",
      reason_id: "system_started_sign_in", max_attempts: V5_RW02_CODE_AUTOFILL_MAX_ATTEMPTS,
      credential_entry_performed: false, effects: EFFECTS });
  }
  // password_prompt
  if (raw.credentials_autofilled !== true) return signInStop("password_prompt_without_autofill");
  return freeze({ answer_kind: "rw02-sign-in.v1", decision: "log_in_permitted",
    reason_id: "credentials_autofilled", permitted_by: "9ea10e5b",
    credential_entry_performed: false, effects: EFFECTS });
}

/** How long after the Log in press the code step may still run. */
export const V5_RW02_SIGN_IN_TICKET_TTL_MS = 120_000;

// Module-private ledger of sign-in tickets. A ticket is an opaque frozen object;
// its state (server-clock mint time, the clock, attempts used, spent) lives
// here, where no caller can reach it, so a hand-built object is not a ticket.
const SIGN_IN_TICKETS = new WeakMap();

function mintTicket(minted_at_ms, readClock) {
  const ticket = Object.freeze(Object.create(null));
  SIGN_IN_TICKETS.set(ticket, { minted_at_ms, readClock, attempts_used: 0, spent: false });
  return ticket;
}

/**
 * Press Log in on credentials the browser autofilled (ruling 9ea10e5b). This
 * is the ONLY way a sign-in ticket comes to exist: pressing Log in IS starting
 * a sign-in. The ticket's time is the SERVER clock, read at the moment of the
 * press (immediately before the click, so the code step's window can only be
 * shorter than the TTL, never longer). No caller time is accepted.
 */
export async function pressLogInOnAutofilledCredentials(args = {}) {
  object(args, "args");
  if ("nowMs" in args || "now" in args || "clock" in args) fail("caller_clock_refused",
    "the sign-in time is read from the server clock, never passed in");
  const unknown = Object.keys(args).filter(k => !["operator", "signIn", "serverClock"].includes(k));
  if (unknown.length) fail("unknown_option", "pressLogIn takes operator, signIn and serverClock only", { unknown });
  const readClock = serverClockReader(args.serverClock);
  const verdict = classifySignIn(args.signIn, { systemStartedSignIn: false });
  if (verdict.decision === "stop") return verdict;
  if (verdict.decision !== "log_in_permitted") return signInStop("log_in_not_offered");
  const operator = signInOperator(args.operator);
  const minted_at_ms = await readClock();
  await operator.clickLogIn();
  return freeze({ answer_kind: "rw02-log-in.v1", decision: "log_in_pressed", reason_id: "credentials_autofilled",
    ticket: mintTicket(minted_at_ms, readClock), minted_at_ms, clock: "server",
    credential_entry_performed: false, effects: EFFECTS });
}

/**
 * The bounded code step (decision 4e1efae4 plus Joe's refinement). The driver
 * reports only booleans; there is no method that returns a code.
 *
 *   clickCodeField(); if autofillSuggestionVisible() then clickAutofillSuggestion();
 *   codeFieldFilled() === true -> filled.
 *
 * At most 3 attempts PER SIGN-IN: the count lives in the ticket's private state
 * and the ticket is spent by its first use, so a second call clicks nothing.
 * Staleness is judged on the SERVER clock the ticket was minted with. A forged
 * ticket is an unprompted code prompt: a stop.
 */
export async function attemptCodeAutofill(args = {}) {
  object(args, "args");
  if ("nowMs" in args || "now" in args || "clock" in args) fail("caller_clock_refused",
    "the code step reads the server clock its ticket was minted with, never a passed-in time");
  const { driver, ticket } = args;
  const state = ticket !== null && typeof ticket === "object" ? SIGN_IN_TICKETS.get(ticket) : undefined;
  if (!state) return signInStop("unprompted_code_prompt");
  if (state.spent) return signInStop("sign_in_ticket_spent", { attempts: state.attempts_used });
  state.spent = true;
  const nowMs = await state.readClock();
  if (!Number.isSafeInteger(nowMs) || nowMs < state.minted_at_ms ||
      nowMs - state.minted_at_ms > V5_RW02_SIGN_IN_TICKET_TTL_MS)
    return signInStop("sign_in_ticket_stale");
  if (!driver) fail("code_driver_unavailable", "a code-step driver is required");
  for (const name of ["clickCodeField", "autofillSuggestionVisible", "clickAutofillSuggestion", "codeFieldFilled"])
    if (typeof driver[name] !== "function") fail("code_driver_incomplete", `the driver has no ${name}()`);
  while (state.attempts_used < V5_RW02_CODE_AUTOFILL_MAX_ATTEMPTS) {
    const attempt = ++state.attempts_used;
    await driver.clickCodeField();
    const suggestion = await driver.autofillSuggestionVisible();
    if (suggestion === true) await driver.clickAutofillSuggestion();
    else if (suggestion !== false) return signInStop("code_fill_state_unobservable", { attempts: attempt });
    const filled = await driver.codeFieldFilled();
    if (filled === true) return freeze({ answer_kind: "rw02-code-autofill.v1", decision: "filled",
      reason_id: "os_autofill_filled", attempts: attempt, credential_entry_performed: false, effects: EFFECTS });
    if (filled !== false) return signInStop("code_fill_state_unobservable", { attempts: attempt });
  }
  return signInStop("code_attempts_exhausted", { attempts: state.attempts_used });
}

// ---------------------------------------------------------------------------
// 4. Consent: Dell's decision record, never a flag.
// ---------------------------------------------------------------------------

/**
 * Pure. Decide Dell-account consent from what the record layer returned for
 * the pinned decision. `reading` is { record, revoked } from the consent
 * source. Consent holds only when ALL of these hold:
 *   - the record exists and is the pinned decision;
 *   - it was logged under a partner's sponsorship (Joe or Dell);
 *   - it carries the partner's literal words (human_quote present);
 *   - no revocation of it exists.
 */
export function evaluateDellConsent(reading) {
  const refuse = reason_id => freeze({ answer_kind: "rw02-consent.v1", decision: "refused", reason_id,
    stop_class: "consent", decision_ref: V5_RW02_DELL_CONSENT_DECISION_ID });
  if (!plain(reading)) return refuse("dell_consent_record_missing");
  const { record, revoked } = reading;
  if (record === null || record === undefined) return refuse("dell_consent_record_missing");
  if (!plain(record) || record.decision_id !== V5_RW02_DELL_CONSENT_DECISION_ID)
    return refuse("dell_consent_record_missing");
  if (revoked !== false) return refuse("dell_consent_revoked");
  if (!V5_RW02_CONSENT_SPONSORS.includes(record.sponsoring_human_slug))
    return refuse("dell_consent_not_partner_record");
  if (record.human_quote_present !== true) return refuse("dell_consent_quote_absent");
  return freeze({ answer_kind: "rw02-consent.v1", decision: "allowed", reason_id: "consent_record_in_force",
    decision_ref: V5_RW02_DELL_CONSENT_DECISION_ID, recorded_by_partner: record.sponsoring_human_slug,
    // Dell's own record, or Joe's record attesting Dell's consent. Both are
    // allowed (Joe's ruling); the basis is carried so no surface can present
    // Joe's attestation as Dell's own words.
    consent_basis: record.sponsoring_human_slug === "dell" ? "dell_own_record" : "partner_attestation" });
}

// ---------------------------------------------------------------------------
// 5. The UI fingerprint.
// ---------------------------------------------------------------------------

/** The digest a read binding's `ui_contract_digest` must equal. */
export function rw02ReadUiFingerprint({ layout_ref, selectors_matched } = {}) {
  stableId(layout_ref, "ui.layout_ref");
  if (!Array.isArray(selectors_matched) || selectors_matched.length > 64)
    fail("invalid_shape", "ui.selectors_matched must be a list");
  const selectors = [...new Set(selectors_matched.map((s, i) => stableId(s, `ui.selectors_matched[${i}]`)))].sort();
  return digest({ kind: "rw02-read-ui-fingerprint.v1", layout_ref, selectors });
}

// ---------------------------------------------------------------------------
// 6. Reconciliation: presence and partner membership only (c04ac197, c0014a37).
// ---------------------------------------------------------------------------

function normalizeOpportunity(value, path) {
  // Callers hand in a structuredClone, so every field below is plain data read
  // exactly once into a local and validated as that local (no getter can swap
  // a value between the check and the use).
  const raw = closed(value, ["opportunity_id", "name", "owner_ref", "team_member_refs"], path);
  const { opportunity_id, name, owner_ref, team_member_refs } = raw;
  if (typeof opportunity_id !== "string" || !OPPORTUNITY_ID.test(opportunity_id))
    fail("invalid_opportunity_id", `${path}.opportunity_id is not an opportunity id`, { path });
  if (!Array.isArray(team_member_refs) || team_member_refs.length > 64)
    fail("invalid_shape", `${path}.team_member_refs must be a list`, { path });
  const team = [...team_member_refs];
  return {
    opportunity_id,
    name: safeText(name, `${path}.name`),
    owner_ref: stableId(owner_ref, `${path}.owner_ref`),
    team_member_refs: team.map((r, i) => stableId(r, `${path}.team_member_refs[${i}]`)).sort(),
  };
}

function normalizeCarrDeal(value, path) {
  const { deal_id, name, salesforce_id } = closed(value, ["deal_id", "name", "salesforce_id"], path);
  if (typeof deal_id !== "string" || !UUID.test(deal_id)) fail("invalid_deal_id", `${path}.deal_id`, { path });
  const sf = salesforce_id ?? null;
  if (sf !== null && (typeof sf !== "string" || !OPPORTUNITY_ID.test(sf)))
    fail("invalid_opportunity_id", `${path}.salesforce_id`, { path });
  return { deal_id, name: safeText(name, `${path}.name`), salesforce_id: sf };
}

/**
 * Pure. Compares opportunities read from Dell's org with CARR's deals in the
 * absence scope, by opportunity id ONLY (a name match is a human question,
 * never a join). `complete` says the read reached the list's end; without it
 * nothing is concluded ABSENT, because an unread page is not an empty one.
 */
export function reconcileSalesforceDeals({ salesforce, carr, joeUserRef, complete } = {}) {
  stableId(joeUserRef, "joeUserRef");
  if (typeof complete !== "boolean") fail("invalid_shape", "complete must be a boolean");
  if (!Array.isArray(salesforce) || !Array.isArray(carr)) fail("invalid_shape", "salesforce and carr are lists");
  // ONE read of every input: structuredClone evaluates each getter once and
  // yields plain data, so what is validated is exactly what is used.
  let sfCopy, carrCopy;
  try { sfCopy = structuredClone(salesforce); carrCopy = structuredClone(carr); }
  catch { fail("invalid_shape", "reconciliation inputs must be plain data"); }
  const seen = new Map();
  sfCopy.forEach((value, i) => {
    const o = normalizeOpportunity(value, `salesforce[${i}]`);
    const prior = seen.get(o.opportunity_id);
    if (prior && digest(prior) !== digest(o)) fail("inconsistent_result",
      "one opportunity was read twice with different facts", { opportunity_id: o.opportunity_id });
    seen.set(o.opportunity_id, o);
  });
  const deals = carrCopy.map((d, i) => normalizeCarrDeal(d, `carr[${i}]`));
  const bySf = new Map(deals.filter(d => d.salesforce_id).map(d => [d.salesforce_id, d]));
  const opportunities = [...seen.values()].sort((a, b) => a.opportunity_id.localeCompare(b.opportunity_id));

  const missing_joe = opportunities
    .filter(o => o.owner_ref !== joeUserRef && !o.team_member_refs.includes(joeUserRef))
    .map(o => ({ opportunity_id: o.opportunity_id, name: o.name,
      carr_deal_id: bySf.get(o.opportunity_id)?.deal_id ?? null, action: "get_joe_added_to_deal" }));
  const unknown_to_carr = opportunities.filter(o => !bySf.has(o.opportunity_id))
    .map(o => ({ opportunity_id: o.opportunity_id, name: o.name }));
  // A read that reached the end but saw NO opportunity is inconclusive (an
  // empty list view, a filter, a render failure): it proves nothing absent.
  const concluded = complete && opportunities.length > 0;
  const absent_from_salesforce = !concluded ? [] : deals
    .filter(d => !d.salesforce_id || !seen.has(d.salesforce_id))
    .map(d => ({ deal_id: d.deal_id, name: d.name, salesforce_id: d.salesforce_id,
      reason_id: d.salesforce_id ? "salesforce_id_not_seen" : "no_salesforce_link" }));

  return freeze({ schema_version: V5_RW02_BROWSER_READ_SCHEMA_VERSION, answer_kind: "rw02-reconciliation.v1",
    opportunities_read: opportunities.length, carr_deals_compared: deals.length,
    missing_joe, unknown_to_carr, absent_from_salesforce, absence_concluded: concluded,
    absence_scope: V5_RW02_ABSENCE_SCOPE,
    field_sync: "not_performed_presence_and_membership_only", effects: EFFECTS });
}

// ---------------------------------------------------------------------------
// 7. The 5-consecutive-clean counter (493de438). Data + a pure function; the
//    runs themselves are stored server-side by record-salesforce-run-outcome.
// ---------------------------------------------------------------------------

/**
 * `runs` is the ordered attended-run history (oldest first). Only runs of
 * `action_kind` count; another kind's runs neither count nor reset (no trust
 * inheritance). Any unclean run of the kind resets the count to zero.
 * Meeting the threshold makes a kind ELIGIBLE for a human promotion review;
 * nothing here promotes it.
 */
export function evaluateAutonomyCounter({ action_kind, runs } = {}) {
  if (!V5_RW02_AUTONOMY_KINDS.includes(action_kind))
    fail("unknown_action_kind", "action_kind is not a registered RW02 kind", { action_kind });
  if (!Array.isArray(runs)) fail("invalid_shape", "runs must be a list");
  const refs = new Set();
  let consecutive_clean = 0;
  let last_reset_run_ref = null;
  runs.forEach((value, i) => {
    const r = closed(value, ["run_ref", "action_kind", "outcome", "execution_mode"], `runs[${i}]`);
    stableId(r.run_ref, `runs[${i}].run_ref`);
    if (refs.has(r.run_ref)) fail("run_counted_twice", "a run may be counted once", { run_ref: r.run_ref });
    refs.add(r.run_ref);
    if (r.execution_mode !== "attended") fail("unattended_run_refused",
      "only attended runs can count toward autonomy", { run_ref: r.run_ref });
    if (!V5_RW02_RUN_OUTCOMES.includes(r.outcome)) fail("unknown_outcome",
      "outcome is not a registered run outcome", { run_ref: r.run_ref });
    if (r.action_kind !== action_kind) return;
    if (r.outcome === "clean") consecutive_clean += 1;
    else { consecutive_clean = 0; last_reset_run_ref = r.run_ref; }
  });
  return freeze({ answer_kind: "rw02-autonomy-counter.v1", action_kind, consecutive_clean,
    threshold: V5_RW02_AUTONOMY_THRESHOLD, threshold_met: consecutive_clean >= V5_RW02_AUTONOMY_THRESHOLD,
    last_reset_run_ref, decision_ref: "493de438", promotion: "not_performed_in_this_slice",
    autonomy_active: false, outward_effect_granted: false, effects: EFFECTS });
}

// ---------------------------------------------------------------------------
// 8. The read run.
// ---------------------------------------------------------------------------

const RUN_OPTIONS = Object.freeze(["reader", "bindingSource", "consentSource", "findingState", "carrDeals",
  "recorder", "signInOperator", "serverClock"]);

function stopAnswer(reason_id, detail = {}) {
  const stop_class = V5_RW02_SAFE_STOP_REASONS[reason_id];
  if (!stop_class) fail("unregistered_stop_reason", "every stop names a registered reason", { reason_id });
  return freeze({ schema_version: V5_RW02_BROWSER_READ_SCHEMA_VERSION, answer_kind: "rw02-safe-stop.v1",
    decision: "stopped", stop_class, reason_id, message: V5_RW02_SAFE_STOP_MESSAGES[reason_id], ...detail,
    resolution_owner: "partner_at_the_browser", bypass_permitted: false, automatic_retry_permitted: false,
    credential_entry_performed: false, partial_writes: 0, salesforce_writes: 0, findings_recorded: 0,
    effects: EFFECTS });
}

function refused(reason_id, detail = {}) {
  return freeze({ ...stopAnswer(reason_id, detail), decision: "refused", stage: "before_browser",
    browser_touched: false });
}

/** A finding write failed after the plan passed: typed, counted, never called a safe stop. */
export const V5_RW02_WRITE_INTERRUPTED_MESSAGE =
  "A finding write failed after the run's checks passed, so some of this run's findings may have been filed. " +
  "The run was recorded unclean. Start the read again; it files the rest without repeating what was filed.";

function interrupted({ run_ref, consent, findings_recorded, findings_planned, cause }) {
  return freeze({ schema_version: V5_RW02_BROWSER_READ_SCHEMA_VERSION, answer_kind: "rw02-write-interrupted.v1",
    decision: "interrupted", reason_id: "finding_write_interrupted", message: V5_RW02_WRITE_INTERRUPTED_MESSAGE,
    run_ref, consent, findings_recorded, findings_planned, partial_writes: findings_recorded, cause,
    run_outcome: "failed", resumable_by_idempotent_replay: true, automatic_retry_permitted: false,
    credential_entry_performed: false, salesforce_writes: 0, effects: EFFECTS });
}

function normalizeBinding(raw) {
  if (raw === null || raw === undefined) return { refusal: refused("org_binding_unavailable",
    { seam: V5_RW02_ORG_BINDING_SEAM }) };
  if (plain(raw) && Object.hasOwn(raw, "dell_consent"))
    return { refusal: refused("consent_flag_refused", { decision_ref: V5_RW02_DELL_CONSENT_DECISION_ID }) };
  const b = closed(raw, ["source_partner", "org", "ui_contract_digest", "joe_user_ref"], "binding");
  if (b.source_partner !== "dell") return { refusal: refused("source_not_dell", { decision_ref: "c04ac197" }) };
  const org = closed(b.org, ["origin", "org_id", "account_ref"], "binding.org");
  return { binding: { org: { origin: org.origin, org_id: stableId(org.org_id, "binding.org.org_id"),
    account_ref: stableId(org.account_ref, "binding.org.account_ref") },
  ui_contract_digest: b.ui_contract_digest, joe_user_ref: stableId(b.joe_user_ref, "binding.joe_user_ref") } };
}

function kernelPage(binding, pageObservation, ui_contract_digest, challengeOverride) {
  const observation = { ...pageObservation, ui_contract_digest };
  if (challengeOverride) observation.challenge = challengeOverride;
  return { execution_mode: "attended",
    binding: { expected_origin: binding.org.origin, expected_org_id: binding.org.org_id,
      expected_account_ref: binding.org.account_ref, expected_ui_contract_digest: binding.ui_contract_digest },
    observation };
}

/** An observation-level contract failure -> the safe-stop reason it is. */
function observationStopReason(error) {
  if (error?.code === "credential_field_refused" || error?.code === "credential_shaped_value")
    return "credential_observed";
  return "observation_shape_drift";
}

/** A row-level contract failure -> the safe-stop reason it is. */
function rowStopReason(error) {
  if (error?.code === "credential_field_refused" || error?.code === "credential_shaped_value")
    return "credential_observed";
  // An extra key on a row is a value the day-to-day read must not take (c0014a37).
  if (error?.code === "unknown_field") return "field_value_observed";
  return "row_shape_drift";
}

function episodeKeyFor(base, episode) { return episode === 1 ? base : `${base}:e${episode}`; }

/**
 * Which key a loop finding is filed under now. Episode 1 is the base key, so
 * loops filed before episodes existed are episode 1. The latest episode's
 * loop still OPEN -> the same key (a replay). CLOSED -> the next episode: the
 * finding is true again, so it is a new action.
 */
async function resolveLoopEpisode(findingState, base) {
  let rows;
  try { rows = structuredClone(await findingState.loopEpisodes(base)); }
  catch { return { stop: "loop_episode_unreadable" }; }
  if (!Array.isArray(rows)) return { stop: "loop_episode_unreadable" };
  let latest = 0; let status;
  for (const row of rows) {
    if (!plain(row) || typeof row.idempotency_key !== "string") return { stop: "loop_episode_unreadable" };
    let episode;
    if (row.idempotency_key === base) episode = 1;
    else {
      const m = row.idempotency_key.startsWith(`${base}:e`) ? /^([2-9]|[1-9][0-9]{1,5})$/
        .exec(row.idempotency_key.slice(base.length + 2)) : null;
      if (!m) return { stop: "loop_episode_unreadable" };
      episode = Number(m[1]);
    }
    if (episode === latest) return { stop: "loop_episode_unreadable" };
    if (episode > latest) { latest = episode; status = row.status; }
  }
  if (latest === 0) return { key: base, episode: 1, reopened: false };
  if (typeof status !== "string") return { stop: "loop_episode_unreadable" };
  if (status === "open") return { key: episodeKeyFor(base, latest), episode: latest, reopened: false };
  return { key: episodeKeyFor(base, latest + 1), episode: latest + 1, reopened: true };
}

/**
 * Pure. Every planned write is checked before the first is sent. Returns the
 * stop reason, or null. `dealIds` is the CARR deal set this run compared.
 * Each item is { finding: "missing_joe"|"unknown_to_carr"|"absent", verb, args }.
 */
export function evaluateFindingPlan(plan, dealIds) {
  const keys = new Set();
  for (const item of plan) {
    if (keys.has(item.args.idempotency_key)) return "finding_outside_scope";
    keys.add(item.args.idempotency_key);
    for (const value of Object.values(item.args))
      if (typeof value === "string" && credentialShaped(value)) return "credential_observed";
    if (item.verb === "add-loop") {
      const expected = V5_RW02_FINDING_RECIPIENTS[item.finding];
      if (!expected || item.args.kind !== expected.kind || item.args.owner !== expected.owner)
        return "finding_recipient_unexpected";
    } else if (item.verb === "record-finding") {
      if (item.finding !== "absent" || item.args.found !== false || !dealIds.has(item.args.subject))
        return "finding_outside_scope";
    } else return "finding_outside_scope";
  }
  return null;
}

/**
 * One attended READ of Dell's opportunity list, page by page, then the
 * reconciliation pass. Every page runs the kernel stop ladder; a stopping page
 * is recorded through record-salesforce-page-stop, the run's unclean outcome
 * through record-salesforce-run-outcome, and the run ends with a typed
 * rw02-safe-stop.v1 and NO findings. A complete read records:
 *   missing Joe      -> add-loop team_loop for Dell ("Add Joe to <deal>")
 *   unknown to CARR  -> add-loop open_loop for Joe (new-deal is humanOnly)
 *   absent from SF   -> record-finding found:false on the CARR deal
 * and then the run's clean outcome.
 */
export async function runSalesforceBrowserReadReconciliation(options = {}) {
  object(options, "options");
  const unknown = Object.keys(options).filter(k => !RUN_OPTIONS.includes(k));
  if (unknown.length) fail("unknown_option",
    "the read run takes no mode, writer, partner, org, tenant, consent flag or capability option", { unknown });
  const { bindingSource, consentSource, findingState, carrDeals } = options;
  const recorder = readModeRecorder(options.recorder);
  if (!bindingSource || typeof bindingSource.read !== "function") fail("binding_source_unavailable",
    "a server-side binding source is required");
  if (typeof carrDeals !== "function") fail("carr_source_unavailable", "a CARR deal source is required");
  if (!EPISODE_SOURCES.has(findingState)) fail("finding_state_unavailable",
    "the loop-episode source must be the record layer's (createLoopEpisodeSource)");

  // Before the browser is touched: the binding, then Dell's consent record.
  const { binding, refusal } = normalizeBinding(await bindingSource.read());
  if (refusal) return refusal;
  if (!CONSENT_SOURCES.has(consentSource)) return refused("consent_source_unavailable",
    { decision_ref: V5_RW02_DELL_CONSENT_DECISION_ID });
  let reading;
  try { reading = structuredClone(await consentSource.read()); }
  catch { return refused("consent_source_unavailable", { decision_ref: V5_RW02_DELL_CONSENT_DECISION_ID }); }
  const consent = evaluateDellConsent(reading);
  if (consent.decision !== "allowed") return refused(consent.reason_id, { decision_ref: consent.decision_ref });
  const consent_ref = { decision_ref: consent.decision_ref, recorded_by_partner: consent.recorded_by_partner,
    consent_basis: consent.consent_basis };

  const reader = readOnlyReader(options.reader);
  const operator = options.signInOperator === undefined ? null : signInOperator(options.signInOperator);
  if (options.serverClock !== undefined) serverClockReader(options.serverClock);
  // A fresh random reference per run, never derived from a time or anything a
  // caller supplies: two runs can never share one outcome key, so a stopped run
  // can never be absorbed into an earlier run recorded clean.
  const run_ref = randomUUID().replace(/-/g, "").slice(0, 24);

  // The run's outcome is recorded server-side EXACTLY once, whatever ends it:
  // clean, a typed stop, or an unexpected failure. An unrecorded unclean run
  // would let the counter skip a reset, so the failure path records too.
  let outcomeRecorded = false;
  const recordOutcome = async (outcome, stop) => {
    await recorder.record("record-salesforce-run-outcome", {
      idempotency_key: `rw02-run-outcome:${run_ref}`, run_ref, action_kind: V5_RW02_READ_RUN_KIND, outcome,
      ...(stop ? { stop_class: V5_RW02_SAFE_STOP_REASONS[stop], reason_id: stop } : {}) });
    outcomeRecorded = true;
  };

  // Every stop from here on: the kernel's own page stop when the kernel ladder
  // stopped, then the unclean run outcome. Never a finding.
  const stopRun = async (reason_id, { kernel = null, index, detail = {} } = {}) => {
    let page_stop = null;
    if (kernel) {
      const verdict = evaluatePageObservation(kernel);
      if (verdict.decision === "stop") {
        await recorder.record("record-salesforce-page-stop",
          { idempotency_key: `rw02-read:${run_ref}:page-stop:${index}`, page: kernel });
        page_stop = { page_index: index, kernel_reason_id: verdict.reason_id,
          challenge: kernel.observation.challenge };
      }
    }
    await recordOutcome("stopped", reason_id);
    return stopAnswer(reason_id, { run_ref, pages_read: index, page_stop, consent: consent_ref,
      run_outcome: "stopped", ...detail });
  };

  const readAndReconcile = async () => {
    const pages = [];
    const opportunities = [];
    const signIn = { pressed: false, ticket: null, codeTried: false };
    for (let index = 0; ; index++) {
      if (index >= V5_RW02_READ_PAGE_CAP) return stopRun("page_cap_reached", { index });
      // ONE read of the page: a plain-data copy, so no getter can answer the
      // validation differently from the use. Sign-in steps re-observe the SAME
      // page index; each step can happen at most once per run.
      let snapshot, ui_contract_digest;
      for (;;) {
        const observed = await reader.observe();
        try { snapshot = structuredClone(observed); }
        catch { return stopRun("observation_shape_drift", { index }); }
        try {
          closed(object(snapshot, "observe()"), ["sign_in", "ui", "page", "opportunities", "has_next"], "observe()");
          closed(snapshot.sign_in, ["state", "credentials_autofilled"], "observe().sign_in");
          object(snapshot.page, "observe().page");
          if (Object.hasOwn(snapshot.page, "ui_contract_digest")) fail("invalid_shape",
            "the UI fingerprint is computed by the adapter, never reported by the driver");
          const ui = closed(snapshot.ui, ["layout_ref", "selectors_matched"], "observe().ui");
          ui_contract_digest = rw02ReadUiFingerprint(ui);
          const missing = V5_RW02_READ_UI_SELECTORS.filter(s => !ui.selectors_matched.includes(s));
          if (missing.length) return stopRun("ui_selector_missing",
            { index, detail: { missing_selector_count: missing.length } });
        } catch (error) {
          if (!(error instanceof V5RW02BrowserReadError)) throw error;
          return stopRun(observationStopReason(error), { index });
        }
        const state = snapshot.sign_in.state;
        const verdict = classifySignIn(snapshot.sign_in, { systemStartedSignIn: signIn.ticket !== null });
        if (verdict.decision === "continue") break;
        const challenge = KERNEL_CHALLENGE[state] ?? "unstated";
        const stopHere = async reason_id => {
          let kernel = null;
          try { kernel = kernelPage(binding, snapshot.page, ui_contract_digest, challenge); evaluatePageObservation(kernel); }
          catch { kernel = null; }
          return stopRun(reason_id, { kernel, index });
        };
        if (verdict.decision === "log_in_permitted" || verdict.decision === "code_autofill_permitted") {
          // A separate page challenge may be absent or match this sign-in step.
          // A contradictory challenge must reach the kernel unchanged and stop
          // before a click, including CAPTCHA and an unobservable challenge.
          if (snapshot.page.challenge !== "none" && snapshot.page.challenge !== challenge) {
            let observedKernel, challengeVerdict;
            try {
              observedKernel = kernelPage(binding, snapshot.page, ui_contract_digest, null);
              challengeVerdict = evaluatePageObservation(observedKernel);
            } catch (error) {
              if (error?.name !== "V5RW02Error") throw error;
              return stopHere(observationStopReason(error));
            }
            return stopRun(challengeVerdict.reason_id, { kernel: observedKernel, index });
          }
          // Suppress only this authorized challenge while running the other
          // page checks before either sign-in click.
          let pageGuard;
          try { pageGuard = evaluatePageObservation(
            kernelPage(binding, snapshot.page, ui_contract_digest, "none")); }
          catch (error) {
            if (error?.name !== "V5RW02Error") throw error;
            return stopHere(observationStopReason(error));
          }
          if (pageGuard.decision === "stop") return stopHere(pageGuard.reason_id);
        }
        // The system signs in only before the first page; mid-read it stops.
        if (index > 0 && verdict.decision !== "stop") return stopHere("sign_in_prompt_mid_read");
        if (verdict.decision === "log_in_permitted") {
          if (signIn.pressed) return stopHere("sign_in_not_accepted");
          if (!operator || options.serverClock === undefined) return stopHere("partner_sign_in_required");
          const pressed = await pressLogInOnAutofilledCredentials({ operator: options.signInOperator,
            signIn: snapshot.sign_in, serverClock: options.serverClock });
          if (pressed.decision !== "log_in_pressed") return stopHere(pressed.reason_id);
          signIn.pressed = true; signIn.ticket = pressed.ticket;
          continue;
        }
        if (verdict.decision === "code_autofill_permitted") {
          if (signIn.codeTried) return stopHere("sign_in_not_accepted");
          signIn.codeTried = true;
          const code = await attemptCodeAutofill({ driver: operator, ticket: signIn.ticket });
          if (code.decision !== "filled") return stopHere(code.reason_id);
          continue;
        }
        return stopHere(verdict.reason_id);
      }

      let kernel, verdict;
      try {
        kernel = kernelPage(binding, snapshot.page, ui_contract_digest, null);
        verdict = evaluatePageObservation(kernel);
      } catch (error) {
        if (error?.name !== "V5RW02Error") throw error;
        return stopRun(observationStopReason(error), { index });
      }
      if (verdict.decision !== "continue") return stopRun(verdict.reason_id, { kernel, index });

      if (!Array.isArray(snapshot.opportunities) || snapshot.opportunities.length > 2000)
        return stopRun("observation_shape_drift", { index });
      for (const [i, row] of snapshot.opportunities.entries()) {
        try { opportunities.push(normalizeOpportunity(row, `page[${index}].opportunities[${i}]`)); }
        catch (error) {
          if (!(error instanceof V5RW02BrowserReadError)) throw error;
          return stopRun(rowStopReason(error), { index });
        }
      }
      pages.push({ page_index: index, evidence_digest: digest(verdict), opportunities: snapshot.opportunities.length });
      const hasNext = snapshot.has_next;
      if (hasNext !== true && hasNext !== false) return stopRun("observation_shape_drift", { index });
      if (hasNext === false) break;
      await reader.nextPage();
    }

    let result;
    const deals = await carrDeals();
    try {
      result = reconcileSalesforceDeals({ salesforce: opportunities, carr: deals,
        joeUserRef: binding.joe_user_ref, complete: true });
    } catch (error) {
      if (error?.code === "inconsistent_result")
        return stopRun("opportunity_read_twice_differently", { index: pages.length });
      throw error;
    }
    // EVERY argument below is identical on a repeat run for the same finding:
    // withEnvelope hashes the arguments and refuses a known key that arrives
    // with different ones (key_reuse). So nothing run-specific (run_ref, time)
    // and nothing volatile (the opportunity NAME, which partners rename) goes
    // into a key, a title, a body or a source. Keys bind the org and the id.
    const org_id = binding.org.org_id;
    const key = (kind, id, extra = null) =>
      `rw02-${kind}:${digest({ kind, org_id, id, extra }).slice(7, 39)}`;
    const source_note = "V5-RW02 attended browser read of Dell's Salesforce (decisions c04ac197, c0014a37)";

    // PLAN every write first. Nothing is sent until the whole plan passes.
    const plan = [];
    const reopened = [];
    for (const f of result.missing_joe) {
      const episode = await resolveLoopEpisode(findingState, key("missing-joe", f.opportunity_id));
      if (episode.stop) return stopRun(episode.stop, { index: pages.length });
      if (episode.reopened) reopened.push(episode.key);
      plan.push({ finding: "missing_joe", verb: "add-loop", args: {
        idempotency_key: episode.key,
        kind: "team_loop", owner: "Dell", domain: "deals",
        title: `Add Joe to Salesforce opportunity ${f.opportunity_id}`,
        body: `Joe is not the owner or a team member on opportunity ${f.opportunity_id} in Dell's Salesforce.`,
        unblocks: "Salesforce reconciliation (decision c04ac197): Joe on every deal",
        source_note } });
    }
    for (const f of result.unknown_to_carr) {
      const episode = await resolveLoopEpisode(findingState, key("unknown-to-carr", f.opportunity_id));
      if (episode.stop) return stopRun(episode.stop, { index: pages.length });
      if (episode.reopened) reopened.push(episode.key);
      plan.push({ finding: "unknown_to_carr", verb: "add-loop", args: {
        idempotency_key: episode.key,
        kind: "open_loop", owner: "Joe", domain: "deals",
        body: `Salesforce opportunity ${f.opportunity_id} in Dell's org has no CARR deal.`,
        blocker: "human_only",
        blocker_detail: `Joe or Dell decides whether ${f.opportunity_id} becomes a CARR deal; creating a deal is humanOnly`,
        source_note } });
    }
    for (const f of result.absent_from_salesforce) {
      // record-finding keeps no value when found is false, so the reason rides
      // in `kind` and `source`, both of which the row keeps. Keyed per deal and
      // reason: a repeat run replays the first row instead of adding one.
      plan.push({ finding: "absent", verb: "record-finding", args: {
        idempotency_key: key("absent", f.deal_id, f.reason_id), subject: f.deal_id,
        kind: `salesforce_presence:${f.reason_id}`, found: false, internal: true, epistemic_status: "observed",
        source: `${source_note}; reason ${f.reason_id}` } });
    }
    const blocked = evaluateFindingPlan(plan, new Set(deals.map(d => d.deal_id)));
    if (blocked) return stopRun(blocked, { index: pages.length, detail: { planned_findings: plan.length } });

    let findings_recorded = 0;
    for (const item of plan) {
      try { await recorder.record(item.verb, item.args); }
      catch (error) {
        // The plan guard above refuses everything the adapter can foresee
        // before the first write. A record verb's own refusal or a lost
        // connection after the plan passed is NOT a safe stop: some findings
        // may have landed. It is reported as exactly that, typed, with the
        // count, and the run is recorded unclean (a failure to record that is
        // an error, never swallowed). Every finding is idempotently keyed, so
        // the next run replays what landed and files the rest.
        try { await recordOutcome("failed"); }
        catch (recordError) {
          // Keep the partial-write count: this is the one case where some
          // findings landed AND the run's unclean outcome is missing.
          throw new V5RW02BrowserReadError("run_outcome_unrecorded",
            "findings were partly written and the run's unclean outcome could not be recorded; do not count this run",
            { run_ref, findings_recorded, findings_planned: plan.length, cause: "finding_write_interrupted",
              record_cause: recordError?.error ?? recordError?.code ?? "unknown" });
        }
        return interrupted({ run_ref, consent: consent_ref, findings_recorded, findings_planned: plan.length,
          cause: typeof error?.error === "string" ? error.error
            : typeof error?.code === "string" ? error.code : "unknown" });
      }
      findings_recorded++;
    }
    await recordOutcome("clean");
    return freeze({ schema_version: V5_RW02_BROWSER_READ_SCHEMA_VERSION, decision: "reconciled", run_ref,
      pages_read: pages.length, pages, consent: consent_ref, signed_in_by_system: signIn.pressed,
      counts: { missing_joe: result.missing_joe.length, unknown_to_carr: result.unknown_to_carr.length,
        absent_from_salesforce: result.absent_from_salesforce.length, reopened_loops: reopened.length },
      reconciliation: result, findings_recorded, run_outcome: "clean",
      salesforce_writes: 0, effects: EFFECTS });
  };

  try { return await readAndReconcile(); }
  catch (error) {
    if (outcomeRecorded || error?.code === "run_outcome_unrecorded") throw error;
    // An unclean run whose outcome cannot be recorded must not pass silently:
    // the counter would then skip a reset. Both failures are surfaced.
    try { await recordOutcome("failed"); }
    catch (recordError) {
      throw new V5RW02BrowserReadError("run_outcome_unrecorded",
        "the run failed and its unclean outcome could not be recorded; do not count this run",
        { run_ref, cause: error?.code ?? error?.message ?? "unknown",
          record_cause: recordError?.error ?? recordError?.code ?? "unknown" });
    }
    throw error;
  }
}
