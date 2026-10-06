// Shared synthetic fixtures and property checks for the V5-RW02 browser read
// adapter. Each check takes a MODULE so the mutant suite runs the very same
// check against a planted-bug copy and requires it to fail there.
// Synthetic data only: no real org, seat, deal or person identifier. The one
// real identifier is the pinned consent decision id, which is a record-layer
// decision ref, not client data.

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { digest } from "../src/artifact-trust.js";

export const ORG = Object.freeze({ origin: "https://synthetic-dell.invalid",
  org_id: "00DsyntheticD01", account_ref: "dell-seat" });

export const CONSENT_DECISION_ID = "bf194d7d-4b33-4683-a320-6b5a8c05766d";

export const SELECTORS = Object.freeze(["list_view_table", "next_page_control", "opportunity_id_cell",
  "opportunity_owner_cell", "opportunity_row", "opportunity_team_cell", "signed_in_user_menu"]);
export const LAYOUT = "synthetic-list-view.v1";

/** The fingerprint computed independently of the module under test. */
export function fingerprint(layout_ref = LAYOUT, selectors = SELECTORS) {
  return digest({ kind: "rw02-read-ui-fingerprint.v1", layout_ref, selectors: [...new Set(selectors)].sort() });
}
const UI = fingerprint();

/** A synthetic 18-char opportunity id derived from a one-letter tag. */
export function oppId(tag) { return `006SYNTH${tag.padEnd(7, "0")}AAA`; }

export function opp(tag, { owner = "dell-sf", team = [], name } = {}) {
  return { opportunity_id: oppId(tag), name: name ?? `Deal ${tag}`, owner_ref: owner,
    team_member_refs: team };
}

export function carr(tag) {
  const hex = tag.charCodeAt(0).toString(16).padStart(12, "0");
  return { deal_id: `00000000-0000-4000-8000-${hex}`, name: `Deal ${tag}`, salesforce_id: oppId(tag) };
}

export function page({ opportunities = [], has_next = false, sign_in = { state: "signed_in" },
  observation = {}, ui = {} } = {}) {
  return { sign_in, has_next, opportunities,
    ui: { layout_ref: LAYOUT, selectors_matched: [...SELECTORS], ...ui },
    page: { origin: ORG.origin, org_id: ORG.org_id, signed_in_account_ref: ORG.account_ref,
      challenge: "none", result_consistency: "consistent", ...observation } };
}

export function bindingSource(patch = {}) {
  return { read: async () => ({ source_partner: "dell", org: { ...ORG }, ui_contract_digest: UI,
    joe_user_ref: "joe-sf", ...patch }) };
}

/**
 * A synthetic database client answering ops.rw02_consent_record. The run only
 * accepts a source made by createDellConsentSource, so tests hand this CLIENT
 * to the real factory (runWith does it) rather than a look-alike source.
 */
export class FakeConsentClient {
  constructor(reading) { this.reading = reading; }
  async query(text, params) {
    assert.match(String(text), /ops\.rw02_consent_record\(\$1::uuid\)/);
    assert.deepEqual(params, [CONSENT_DECISION_ID]);
    if (this.reading instanceof Error) throw this.reading;
    return { rows: [{ consent: this.reading }] };
  }
}

/** What ops.rw02_consent_record answers for the pinned decision. */
export function consentSource({ record = {}, revoked = false, missing = false } = {}) {
  return new FakeConsentClient({ revoked, record: missing ? null : { decision_id: CONSENT_DECISION_ID,
    sponsoring_human_slug: "joe", human_quote_present: true, ...record } });
}

/** A synthetic client answering ops.rw02_loop_episodes: base key -> [{idempotency_key, status}]. */
export class FakeFindingState {
  constructor(byBase = {}) { this.byBase = byBase; this.asked = []; }
  async query(text, params) {
    assert.match(String(text), /ops\.rw02_loop_episodes\(\$1::text\)/);
    const base = params[0];
    this.asked.push(base);
    const rows = typeof this.byBase === "function" ? this.byBase(base) : this.byBase[base];
    if (rows instanceof Error) throw rows;
    return { rows: rows ?? [] };
  }
}

/** A database client whose clock_timestamp() is `ms`; `advance` moves it. */
export class FakeClockClient {
  constructor(ms = 1_700_000_000_000, log = null) { this.ms = ms; this.log = log; this.reads = 0; }
  async query(sql) {
    assert.match(sql, /clock_timestamp\(\)/, "the server clock reads the database clock");
    this.reads++; if (this.log) this.log.push("serverClock");
    return { rows: [{ now_ms: String(this.ms) }] };
  }
  advance(ms) { this.ms += ms; }
}

/**
 * A fake browser driver. It deliberately carries write-shaped methods too, so a
 * test can prove the runner never reaches them through the read facade.
 */
export class FakeReaderDriver {
  constructor(pages) {
    // pages[i] is page i's observation (or a function returning it). An ARRAY
    // at pages[i] is a sequence of observations of that same page: each
    // observe() at index i returns the next one and then sticks at the last,
    // which is how a sign-in screen gives way to the list.
    this.pages = pages; this.index = 0; this.readCalls = []; this.writeCalls = 0; this.argCounts = [];
    this.seen = new Map();
  }
  async observe(...args) {
    this.readCalls.push("observe"); this.argCounts.push(args.length);
    const p = this.pages[this.index];
    const n = this.seen.get(this.index) ?? 0;
    this.seen.set(this.index, n + 1);
    const value = Array.isArray(p) ? p[Math.min(n, p.length - 1)] : p;
    return typeof value === "function" ? value() : structuredClone(value);
  }
  async nextPage(...args) { this.readCalls.push("nextPage"); this.argCounts.push(args.length); this.index++; }
  async saveOpportunity() { this.writeCalls++; }
  async addTeamMember() { this.writeCalls++; }
  async clickLogIn() { this.writeCalls++; }
  async typeText() { this.writeCalls++; }
}

/**
 * The sign-in fake: the Log in press and the code step. It reports only
 * filled / not filled; it has no value to give. `suggestionOn` lists the
 * attempts on which the From-Messages chip shows; `fillOn` the attempts the
 * field fills with no chip; `fillAfterSuggestion` fills once the chip is
 * clicked. A runaway loop is caught by the click ceiling.
 */
export class FakeCodeDriver {
  constructor({ suggestionOn = [], fillOn = [], fillAfterSuggestion = false, filledValue, log = [] } = {}) {
    Object.assign(this, { suggestionOn, fillOn, fillAfterSuggestion, filledValue });
    this.attempt = 0; this.log = log; this.filled = false;
  }
  async clickLogIn() { this.log.push("clickLogIn"); }
  async clickCodeField() {
    this.log.push("clickCodeField"); this.attempt++;
    if (this.attempt > 10) throw new Error("runaway: code field clicked more than 10 times");
    if (this.fillOn.includes(this.attempt)) this.filled = true;
  }
  async autofillSuggestionVisible() {
    this.log.push("autofillSuggestionVisible"); return this.suggestionOn.includes(this.attempt);
  }
  async clickAutofillSuggestion() {
    this.log.push("clickAutofillSuggestion"); if (this.fillAfterSuggestion) this.filled = true;
  }
  async codeFieldFilled() {
    this.log.push("codeFieldFilled"); return this.filledValue !== undefined ? this.filledValue : this.filled;
  }
}

/**
 * Mirrors withEnvelope (tools.js): the request hash covers the verb and every
 * argument except the key; a known key arriving with a different hash is
 * refused as key_reuse, and a matching one replays without a second write.
 */
export class SpyRecorder {
  constructor() { this.calls = []; this.hashes = new Map(); this.writes = 0; }
  async record(verb, args) {
    this.calls.push({ verb, args: structuredClone(args) });
    const hash = createHash("sha256").update(JSON.stringify({ verb,
      args: { ...args, idempotency_key: undefined } })).digest("hex");
    const prior = this.hashes.get(args.idempotency_key);
    if (prior !== undefined) {
      if (prior !== hash) { const e = new Error("key_reuse"); e.error = "key_reuse"; throw e; }
      return { replayed: true };
    }
    this.hashes.set(args.idempotency_key, hash); this.writes++;
    return { ok: true };
  }
}

export const FINDING_VERBS = Object.freeze(["add-loop", "record-finding"]);
export const findingCalls = recorder => recorder.calls.filter(c => FINDING_VERBS.includes(c.verb));

/**
 * Build a run with synthetic server-side sources; any option may be overridden.
 * Synthetic database CLIENTS go through the module's own factories, exactly as
 * production does; anything else is passed through untouched (to be refused).
 */
export function runWith(mod, pages, { recorder = new SpyRecorder(), carrDeals = [], ...rest } = {}) {
  const reader = rest.reader ?? new FakeReaderDriver(pages);
  delete rest.reader;
  const consent = Object.hasOwn(rest, "consentSource") ? rest.consentSource : consentSource();
  const episodes = Object.hasOwn(rest, "findingState") ? rest.findingState : new FakeFindingState();
  delete rest.consentSource; delete rest.findingState;
  return mod.runSalesforceBrowserReadReconciliation({ reader, bindingSource: bindingSource(),
    consentSource: consent instanceof FakeConsentClient ? mod.createDellConsentSource(consent) : consent,
    findingState: episodes instanceof FakeFindingState ? mod.createLoopEpisodeSource(episodes) : episodes,
    carrDeals: async () => carrDeals, recorder, ...rest });
}

/** Every safe stop, whatever its class, has the same shape and wrote no finding. */
export function assertSafeStop(mod, out, recorder, { stop_class, reason_id }) {
  assert.equal(out.answer_kind, "rw02-safe-stop.v1");
  assert.ok(["stopped", "refused"].includes(out.decision), out.decision);
  assert.equal(out.stop_class, stop_class, `stop_class for ${reason_id}`);
  assert.equal(out.reason_id, reason_id);
  assert.equal(mod.V5_RW02_SAFE_STOP_REASONS[reason_id], stop_class);
  assert.equal(typeof out.message, "string");
  assert.ok(out.message.length > 20);
  assert.equal(out.message, mod.V5_RW02_SAFE_STOP_MESSAGES[reason_id]);
  assert.equal(out.bypass_permitted, false);
  assert.equal(out.automatic_retry_permitted, false);
  assert.equal(out.resolution_owner, "partner_at_the_browser");
  assert.equal(out.partial_writes, 0);
  assert.equal(out.findings_recorded, 0);
  assert.equal(out.salesforce_writes, 0);
  assert.equal(out.credential_entry_performed, false);
  if (recorder) assert.deepEqual(findingCalls(recorder), [], "a stop files no finding");
  if (recorder && out.decision === "stopped") {
    const outcome = recorder.calls.filter(c => c.verb === "record-salesforce-run-outcome");
    assert.equal(outcome.length, 1, "a stopped run records exactly one outcome");
    assert.equal(outcome[0].args.outcome, "stopped");
    assert.equal(outcome[0].args.reason_id, reason_id);
    assert.equal(outcome[0].args.stop_class, stop_class);
  }
}

// ---------------------------------------------------------------------------
// Property checks, shared with the mutant suite.
// ---------------------------------------------------------------------------

export async function checkReadOnlyFacade(mod) {
  const driver = new FakeReaderDriver([]);
  const facade = mod.readOnlyReader(driver);
  assert.deepEqual(Object.keys(facade).sort(), ["nextPage", "observe"]);
  assert.notEqual(facade, driver);
  assert.ok(Object.isFrozen(facade));
  for (const name of ["saveOpportunity", "addTeamMember", "clickLogIn", "typeText"])
    assert.equal(facade[name], undefined, name);
  assert.equal(Object.getPrototypeOf(facade), null, "no prototype path back to the driver");
}

export async function checkRecorderAllowlist(mod) {
  const inner = new SpyRecorder();
  const recorder = mod.readModeRecorder(inner);
  for (const verb of ["update-deal", "patch-deal-field", "link-salesforce-reference",
    "record-salesforce-write-readback", "new-deal", "call-verb", "revoke-salesforce-read-consent"]) {
    await assert.rejects(recorder.record(verb, {}),
      e => e instanceof mod.V5RW02BrowserReadError && e.code === "verb_not_permitted_in_read_mode", verb);
  }
  assert.equal(inner.calls.length, 0);
}

export async function checkMissingJoeFlagged(mod) {
  const r = mod.reconcileSalesforceDeals({ joeUserRef: "joe-sf", complete: true,
    salesforce: [opp("A", { owner: "dell-sf", team: ["someone-else"] })], carr: [carr("A")] });
  assert.equal(r.missing_joe.length, 1);
  assert.equal(r.missing_joe[0].opportunity_id, oppId("A"));
  assert.equal(r.missing_joe[0].action, "get_joe_added_to_deal");
}

export async function checkCounterResets(mod) {
  const run = (n, outcome = "clean") => ({ run_ref: `r-${n}`, action_kind: "opportunity_phase_update",
    outcome, execution_mode: "attended" });
  const a = mod.evaluateAutonomyCounter({ action_kind: "opportunity_phase_update",
    runs: [run(1), run(2), run(3), run(4), run(5, "failed"), run(6), run(7), run(8), run(9)] });
  assert.equal(a.consecutive_clean, 4);
  assert.equal(a.threshold_met, false);
}

/** A system-started sign-in: Log in pressed on the server clock, ticket returned. */
export async function pressed(mod, { driver = new FakeCodeDriver(), client = new FakeClockClient() } = {}) {
  const out = await mod.pressLogInOnAutofilledCredentials({ operator: driver,
    signIn: { state: "password_prompt", credentials_autofilled: true },
    serverClock: mod.createServerClock(client) });
  assert.equal(out.decision, "log_in_pressed");
  return { ticket: out.ticket, driver, client, out };
}

export async function checkCodeRetryBound(mod) {
  const { ticket, driver } = await pressed(mod, { driver: new FakeCodeDriver({ suggestionOn: [2] }) });
  const a = await mod.attemptCodeAutofill({ driver, ticket });
  assert.equal(a.decision, "stop");
  assert.equal(a.reason_id, "code_attempts_exhausted");
  assert.equal(a.attempts, 3);
  assert.equal(driver.log.filter(x => x === "clickCodeField").length, 3);
  assert.equal(a.resolution_owner, "partner_at_the_browser");
  // The bound is PER SIGN-IN: calling again with the same ticket clicks nothing.
  const again = await mod.attemptCodeAutofill({ driver, ticket });
  assert.equal(again.decision, "stop");
  assert.equal(driver.log.filter(x => x === "clickCodeField").length, 3, "3 clicks per sign-in, not per call");
  // And a fill on the third attempt is still a fill.
  const late = await pressed(mod, { driver: new FakeCodeDriver({ fillOn: [3] }) });
  assert.equal((await mod.attemptCodeAutofill({ driver: late.driver, ticket: late.ticket })).attempts, 3);
}

export async function checkAbsenceNeedsCompleteRead(mod) {
  const partial = mod.reconcileSalesforceDeals({ joeUserRef: "joe-sf", complete: false,
    salesforce: [opp("A", { team: ["joe-sf"] })], carr: [carr("A"), carr("Q")] });
  assert.deepEqual(partial.absent_from_salesforce, []);
  assert.equal(partial.absence_concluded, false);
  const full = mod.reconcileSalesforceDeals({ joeUserRef: "joe-sf", complete: true,
    salesforce: [opp("A", { team: ["joe-sf"] })], carr: [carr("A"), carr("Q")] });
  assert.equal(full.absent_from_salesforce.length, 1);
}

export async function checkCodeStepNeedsSystemStart(mod) {
  const { ticket: real } = await pressed(mod);
  const forged = [undefined, null, { started_by_system: true }, Object.freeze(Object.create(null)),
    { ...real }, Object.create(real)];
  for (const ticket of forged) {
    const driver = new FakeCodeDriver({ fillOn: [1] });
    const a = await mod.attemptCodeAutofill({ driver, ticket });
    assert.equal(a.decision, "stop");
    assert.equal(a.reason_id, "unprompted_code_prompt");
    assert.deepEqual(driver.log, [], "nothing is clicked for a sign-in the system did not start");
  }
}

export async function checkNoFindingsAfterStop(mod) {
  // Page 1 reads a missing-Joe deal; page 2 hits a code prompt. Nothing may be concluded.
  const recorder = new SpyRecorder();
  const out = await runWith(mod, [page({ opportunities: [opp("A")], has_next: true }),
    page({ sign_in: { state: "code_prompt" } })], { recorder, carrDeals: [carr("A"), carr("Q")] });
  assert.equal(out.decision, "stopped");
  assert.deepEqual(recorder.calls.map(c => c.verb), ["record-salesforce-page-stop", "record-salesforce-run-outcome"]);
  assert.equal(out.findings_recorded, 0);
}

/** Reviewer M1: a non-boolean has_next is a malformed page, never the end of the list. */
export async function checkHasNextStrict(mod) {
  for (const bad of [undefined, "no", 0, null]) {
    const recorder = new SpyRecorder();
    const pages = [{ ...page({ opportunities: [opp("A")] }), has_next: bad }];
    if (bad === undefined) delete pages[0].has_next;
    const out = await runWith(mod, pages, { recorder, carrDeals: [carr("A"), carr("Q")] });
    assertSafeStop(mod, out, recorder, { stop_class: "ui_drift", reason_id: "observation_shape_drift" });
  }
}

/** Reviewer M4: two opportunities with the same name are two findings with two keys. */
export async function checkKeysDistinctPerOpportunity(mod) {
  const recorder = new SpyRecorder();
  await runWith(mod, [page({ opportunities: [opp("A", { name: "Same Name" }), opp("B", { name: "Same Name" })] })],
    { recorder, carrDeals: [carr("A"), carr("B")] });
  const keys = recorder.calls.filter(c => c.verb === "add-loop").map(c => c.args.idempotency_key);
  assert.equal(keys.length, 2);
  assert.equal(new Set(keys).size, 2);
  assert.equal(findingCalls(recorder).length, 2);
}

/** Reviewer M5: a credential-shaped name is refused, not recorded. */
export async function checkCredentialTextRefused(mod) {
  for (const name of ["password=hunter2", "Bearer abcdefghijklmnop", "sk-abcdefghijklmnopqrstu"]) {
    assert.throws(() => mod.reconcileSalesforceDeals({ joeUserRef: "joe-sf", complete: true,
      salesforce: [opp("A", { name })], carr: [] }), e => e.code === "credential_shaped_value", name);
  }
}

/** Reviewer M8: the cap is exact. CAP pages ending the list reconcile; more stops after CAP reads. */
export async function checkPageCapExact(mod) {
  const cap = mod.V5_RW02_READ_PAGE_CAP;
  const exact = Array.from({ length: cap }, (_, i) => page({ has_next: i < cap - 1,
    opportunities: i === 0 ? [opp("A", { team: ["joe-sf"] })] : [] }));
  const ok = await runWith(mod, exact, { carrDeals: [carr("A")] });
  assert.equal(ok.decision, "reconciled");
  assert.equal(ok.pages_read, cap);
  const driver = new FakeReaderDriver(Array.from({ length: cap + 2 }, () => page({ has_next: true })));
  const over = await runWith(mod, [], { reader: driver });
  assert.equal(over.reason_id, "page_cap_reached");
  assert.equal(driver.readCalls.filter(x => x === "observe").length, cap);
}

/** Reviewer M9: the facade hands the driver no arguments. */
export async function checkFacadeForwardsNoArgs(mod) {
  const driver = new FakeReaderDriver([page()]);
  const facade = mod.readOnlyReader(driver);
  await facade.observe({ command: "saveOpportunity" }, "x");
  await facade.nextPage("click:Save");
  assert.deepEqual(driver.argCounts, [0, 0]);
}

/** Reviewer M10: credential-named keys are refused BY NAME, not as a generic unknown field. */
export async function checkCredentialKeyRefusedByName(mod) {
  for (const key of ["password", "code", "otp", "username", "value", "sms_code", "passcode"]) {
    assert.throws(() => mod.classifySignIn({ state: "code_prompt", [key]: "x" }, { systemStartedSignIn: false }),
      e => e.code === "credential_field_refused", key);
  }
}

/**
 * Reviewer M11 and finding 1: a repeat run (another time, renamed opportunities)
 * sends BYTE-IDENTICAL finding arguments, so withEnvelope replays rather than
 * refusing key_reuse, and nothing is filed twice.
 */
export async function checkRepeatRunArgsIdentical(mod) {
  const recorder = new SpyRecorder();
  const carrDeals = [carr("A"), carr("Q"),
    { deal_id: "00000000-0000-4000-8000-00000000000f", name: "Unlinked", salesforce_id: null }];
  const first = await runWith(mod, [page({ opportunities: [opp("A"), opp("N", { name: "Old name" })] })],
    { recorder, carrDeals });
  const firstArgs = findingCalls(recorder).map(c => JSON.stringify(c));
  const before = recorder.calls.length;
  const second = await runWith(mod, [page({ opportunities: [opp("A", { name: "Renamed A" }),
    opp("N", { name: "New name" })] })], { recorder, carrDeals });
  assert.notEqual(first.run_ref, second.run_ref);
  const secondArgs = recorder.calls.slice(before).filter(c => FINDING_VERBS.includes(c.verb))
    .map(c => JSON.stringify(c));
  assert.equal(firstArgs.length, 5);
  assert.deepEqual(secondArgs, firstArgs, "repeat-run finding arguments must be byte-identical");
  // 5 findings + 2 run outcomes (one per run); the repeat filed no finding.
  assert.equal(recorder.writes, 7, "the repeat run filed nothing new");
}

/** Finding 5: an empty complete read concludes no absence. */
export async function checkEmptyReadInconclusive(mod) {
  const recorder = new SpyRecorder();
  const out = await runWith(mod, [page({ opportunities: [], has_next: false })],
    { recorder, carrDeals: [carr("A"), carr("Q")] });
  assert.equal(out.decision, "reconciled");
  assert.equal(out.counts.absent_from_salesforce, 0);
  assert.equal(out.reconciliation.absence_concluded, false);
  assert.deepEqual(findingCalls(recorder), []);
}

/** Finding 3: a getter that answers differently on a second read cannot smuggle a value. */
export async function checkGetterSwapHarmless(mod) {
  const evil = "006‮evil\npassword=hunter2";
  const swapping = () => {
    let reads = 0; let teamReads = 0;
    const o = { name: "Deal S", owner_ref: "dell-sf" };
    Object.defineProperty(o, "opportunity_id", { enumerable: true,
      get: () => (reads++ === 0 ? oppId("S") : evil) });
    Object.defineProperty(o, "team_member_refs", { enumerable: true,
      get: () => (teamReads++ === 0 ? [] : ["bad ref\npassword=hunter2"]) });
    return o;
  };
  const r = mod.reconcileSalesforceDeals({ joeUserRef: "joe-sf", complete: true,
    salesforce: [swapping()], carr: [] });
  assert.deepEqual(r.missing_joe.map(f => f.opportunity_id), [oppId("S")]);
  const recorder = new SpyRecorder();
  await runWith(mod, [() => ({ ...page(), opportunities: [swapping()] })], { recorder });
  const text = JSON.stringify(recorder.calls);
  assert.ok(!text.includes("hunter2") && !text.includes("evil"), "the swapped value never reaches a record");
  assert.ok(text.includes(oppId("S")));
}

// ---------------------------------------------------------------------------
// Safe-stop checks (checkable_done 1). Ranked by Jev (verification_selection,
// 2026-09-27); each has at least one planted mutant it must kill.
// ---------------------------------------------------------------------------

/** F01/F02: challenges the rulings do not cover stop typed, after a clean page, with no finding. */
export async function checkAuthChallengesStop(mod) {
  for (const [state, reason_id, challenge] of [["captcha", "captcha", "captcha"],
    ["new_device_prompt", "new_device_prompt", "mfa_challenge"],
    ["security_challenge", "security_challenge", "captcha"]]) {
    const recorder = new SpyRecorder();
    const out = await runWith(mod, [page({ opportunities: [opp("A")], has_next: true }),
      page({ sign_in: { state } })], { recorder, carrDeals: [carr("A"), carr("Q")] });
    assertSafeStop(mod, out, recorder, { stop_class: "auth_challenge", reason_id });
    assert.equal(out.page_stop.challenge, challenge, state);
    assert.equal(recorder.calls[0].verb, "record-salesforce-page-stop");
  }
}

/** F03: a system-started sign-in whose code never fills stops after exactly 3 attempts. */
export async function checkSystemSignInCodeExhausted(mod) {
  const log = [];
  const client = new FakeClockClient(1_700_000_000_000, log);
  const operator = new FakeCodeDriver({ log });
  const recorder = new SpyRecorder();
  const reader = new FakeReaderDriver([[
    page({ sign_in: { state: "password_prompt", credentials_autofilled: true } }),
    page({ sign_in: { state: "code_prompt" } }),
  ]]);
  const out = await runWith(mod, [], { reader, recorder, signInOperator: operator,
    serverClock: mod.createServerClock(client) });
  assertSafeStop(mod, out, recorder, { stop_class: "auth_challenge", reason_id: "code_attempts_exhausted" });
  assert.equal(log.filter(x => x === "clickLogIn").length, 1, "Log in is pressed once");
  assert.equal(log.filter(x => x === "clickCodeField").length, 3, "exactly 3 code attempts");
  assert.equal(reader.writeCalls, 0, "the reader's own write methods are never reached");
}

/** F03b: a filled code continues to a clean read, and the run says the system signed in. */
export async function checkSystemSignInFilledContinues(mod) {
  const operator = new FakeCodeDriver({ fillOn: [1] });
  const reader = new FakeReaderDriver([[
    page({ sign_in: { state: "password_prompt", credentials_autofilled: true } }),
    page({ sign_in: { state: "code_prompt" } }),
    page({ opportunities: [opp("A", { team: ["joe-sf"] })] }),
  ]]);
  const recorder = new SpyRecorder();
  const out = await runWith(mod, [], { reader, recorder, carrDeals: [carr("A")], signInOperator: operator,
    serverClock: mod.createServerClock(new FakeClockClient()) });
  assert.equal(out.decision, "reconciled");
  assert.equal(out.signed_in_by_system, true);
  // Without a sign-in operator the same prompt is the partner's.
  const plainRecorder = new SpyRecorder();
  const partner = await runWith(mod, [page({ sign_in: { state: "password_prompt", credentials_autofilled: true } })],
    { recorder: plainRecorder });
  assertSafeStop(mod, partner, plainRecorder, { stop_class: "auth_challenge", reason_id: "partner_sign_in_required" });
}

/** F04: the ticket is minted on the SERVER clock at the press; caller time is refused; staleness is server time. */
export async function checkServerClockTicket(mod) {
  const log = [];
  const client = new FakeClockClient(5_000_000, log);
  const driver = new FakeCodeDriver({ fillOn: [1], log });
  for (const extra of [{ nowMs: 1 }, { now: 1 }, { clock: () => 1 }]) {
    await assert.rejects(mod.pressLogInOnAutofilledCredentials({ operator: driver,
      signIn: { state: "password_prompt", credentials_autofilled: true },
      serverClock: mod.createServerClock(client), ...extra }), e => e.code === "caller_clock_refused");
  }
  for (const serverClock of [undefined, 5_000_000, () => 5_000_000, Object.freeze(Object.create(null))]) {
    await assert.rejects(mod.pressLogInOnAutofilledCredentials({ operator: driver,
      signIn: { state: "password_prompt", credentials_autofilled: true }, serverClock }),
    e => e.code === "server_clock_required");
  }
  assert.deepEqual(log, [], "nothing is pressed without the server clock");
  const out = await mod.pressLogInOnAutofilledCredentials({ operator: driver,
    signIn: { state: "password_prompt", credentials_autofilled: true }, serverClock: mod.createServerClock(client) });
  assert.equal(out.minted_at_ms, 5_000_000);
  assert.equal(out.clock, "server");
  assert.deepEqual(log, ["serverClock", "clickLogIn"], "the server clock is read at the press");
  await assert.rejects(mod.attemptCodeAutofill({ driver, ticket: out.ticket, nowMs: 5_000_000 }),
    e => e.code === "caller_clock_refused");
  client.advance(mod.V5_RW02_SIGN_IN_TICKET_TTL_MS + 1);
  const stale = await mod.attemptCodeAutofill({ driver, ticket: out.ticket });
  assert.equal(stale.reason_id, "sign_in_ticket_stale", "staleness is judged on the server clock");
  assert.equal(log.filter(x => x === "clickCodeField").length, 0);
  // A server clock that runs backwards is not trusted either.
  const back = new FakeClockClient(9_000_000);
  const p2 = await mod.pressLogInOnAutofilledCredentials({ operator: new FakeCodeDriver(),
    signIn: { state: "password_prompt", credentials_autofilled: true }, serverClock: mod.createServerClock(back) });
  back.advance(-1);
  assert.equal((await mod.attemptCodeAutofill({ driver: new FakeCodeDriver({ fillOn: [1] }), ticket: p2.ticket }))
    .reason_id, "sign_in_ticket_stale");
  // Log in is never pressed on a password prompt the browser did not fill.
  const noFill = new FakeCodeDriver();
  const refusedPress = await mod.pressLogInOnAutofilledCredentials({ operator: noFill,
    signIn: { state: "password_prompt", credentials_autofilled: false }, serverClock: mod.createServerClock(client) });
  assert.equal(refusedPress.reason_id, "password_prompt_without_autofill");
  assert.deepEqual(noFill.log, []);
}

/** F05/F06/F07: selector missing, layout drift, and a row that does not parse all stop as ui_drift. */
export async function checkUiDriftStops(mod) {
  const cases = [
    [[page({ ui: { selectors_matched: SELECTORS.filter(s => s !== "opportunity_team_cell") } })], "ui_selector_missing"],
    [[page({ ui: { layout_ref: "synthetic-list-view.v2" } })], "ui_drift"],
    [[page({ opportunities: [opp("A")], has_next: true }), page({ opportunities: [opp("B")], has_next: true }),
      page({ opportunities: [{ ...opp("C"), opportunity_id: "not-an-opportunity" }] })], "row_shape_drift"],
    [[{ ...page(), page: { ...page().page, ui_contract_digest: fingerprint() } }], "observation_shape_drift"],
  ];
  for (const [pages, reason_id] of cases) {
    const recorder = new SpyRecorder();
    const out = await runWith(mod, pages, { recorder, carrDeals: [carr("A"), carr("Q")] });
    assertSafeStop(mod, out, recorder, { stop_class: "ui_drift", reason_id });
  }
}

/** F08/F09/F10: another seat, another org or origin, or a page showing recipients stops. */
export async function checkUnexpectedAccountStops(mod) {
  const cases = [
    [{ signed_in_account_ref: "joe-seat" }, "signed_in_account_mismatch"],
    [{ org_id: "00DjoeOrg000001" }, "org_mismatch"],
    [{ origin: "https://synthetic-dell.invalid.evil.example" }, "origin_mismatch"],
    [{ recipients: ["someone-outside"] }, "unexpected_recipient"],
  ];
  for (const [observation, reason_id] of cases) {
    const recorder = new SpyRecorder();
    const out = await runWith(mod, [page({ observation, opportunities: [opp("A")] })],
      { recorder, carrDeals: [carr("A")] });
    assertSafeStop(mod, out, recorder, { stop_class: "unexpected_recipient_or_account", reason_id });
    assert.equal(recorder.calls[0].verb, "record-salesforce-page-stop");
  }
}

/** F11: a plan with a finding bound for the wrong recipient is refused whole, before any write. */
export async function checkPlanRecipientGuard(mod) {
  const deals = new Set([carr("Q").deal_id]);
  const good = [
    { finding: "missing_joe", verb: "add-loop", args: { idempotency_key: "k-1", kind: "team_loop", owner: "Dell" } },
    { finding: "unknown_to_carr", verb: "add-loop", args: { idempotency_key: "k-2", kind: "open_loop", owner: "Joe" } },
    { finding: "absent", verb: "record-finding", args: { idempotency_key: "k-3", subject: carr("Q").deal_id, found: false } },
  ];
  assert.equal(mod.evaluateFindingPlan(good, deals), null);
  const swap = (i, patch) => good.map((item, j) => (j === i ? { ...item, args: { ...item.args, ...patch } } : item));
  assert.equal(mod.evaluateFindingPlan(swap(0, { owner: "Joe" }), deals), "finding_recipient_unexpected");
  assert.equal(mod.evaluateFindingPlan(swap(1, { owner: "Dell" }), deals), "finding_recipient_unexpected");
  assert.equal(mod.evaluateFindingPlan(swap(0, { kind: "action_required" }), deals), "finding_recipient_unexpected");
  assert.equal(mod.evaluateFindingPlan(swap(2, { subject: carr("Z").deal_id }), deals), "finding_outside_scope");
  assert.equal(mod.evaluateFindingPlan(swap(2, { found: true }), deals), "finding_outside_scope");
  assert.equal(mod.evaluateFindingPlan([...good, { finding: "missing_joe", verb: "update-deal", args: {
    idempotency_key: "k-4" } }], deals), "finding_outside_scope");
  assert.equal(mod.evaluateFindingPlan(swap(1, { body: "token=abcdefgh" }), deals), "credential_observed");
}

/** F12/F13: a page-reported policy conflict, or a row carrying a field value, stops as policy_conflict. */
export async function checkPolicyConflictStops(mod) {
  const cases = [
    [[page({ observation: { policy_conflicts: ["pending_unsaved_edit"] }, opportunities: [opp("A")] })], "policy_conflict"],
    [[page({ opportunities: [{ ...opp("A"), amount: "1" }] })], "field_value_observed"],
    [[page({ opportunities: [{ ...opp("A"), close_date: "2026-10-01" }] })], "field_value_observed"],
    [[page({ sign_in: { state: "signed_in", password: "x" } })], "credential_observed"],
  ];
  for (const [pages, reason_id] of cases) {
    const recorder = new SpyRecorder();
    const out = await runWith(mod, pages, { recorder, carrDeals: [carr("A")] });
    assertSafeStop(mod, out, recorder, { stop_class: "policy_conflict", reason_id });
  }
}

/** F14/F15/F16: consent is the decision record, re-read every run; anything else refuses before the browser. */
export async function checkConsentRecord(mod) {
  assert.equal(mod.V5_RW02_DELL_CONSENT_DECISION_ID, CONSENT_DECISION_ID);
  const recorder = new SpyRecorder();
  const ok = await runWith(mod, [page({ opportunities: [opp("A", { team: ["joe-sf"] })] })],
    { recorder, carrDeals: [carr("A")] });
  assert.equal(ok.decision, "reconciled");
  assert.deepEqual(ok.consent, { decision_ref: CONSENT_DECISION_ID, recorded_by_partner: "joe",
    consent_basis: "partner_attestation" });
  const refusals = [
    [{ consentSource: consentSource({ missing: true }) }, "dell_consent_record_missing"],
    [{ consentSource: consentSource({ record: { decision_id: "00000000-0000-4000-8000-000000000001" } }) },
      "dell_consent_record_missing"],
    [{ consentSource: consentSource({ revoked: true }) }, "dell_consent_revoked"],
    [{ consentSource: consentSource({ revoked: "no" }) }, "dell_consent_revoked"],
    [{ consentSource: consentSource({ record: { sponsoring_human_slug: null } }) }, "dell_consent_not_partner_record"],
    [{ consentSource: consentSource({ record: { sponsoring_human_slug: "claude" } }) }, "dell_consent_not_partner_record"],
    [{ consentSource: consentSource({ record: { human_quote_present: false } }) }, "dell_consent_quote_absent"],
    [{ consentSource: new FakeConsentClient(new Error("db down")) }, "consent_source_unavailable"],
    [{ consentSource: undefined }, "consent_source_unavailable"],
    [{ consentSource: new FakeConsentClient({ granted: true }) }, "dell_consent_record_missing"],
    // A look-alike source that simply answers "consent in force" was not made
    // by the record layer's factory, so it is not a consent source at all.
    [{ consentSource: { read: async () => ({ revoked: false, record: { decision_id: CONSENT_DECISION_ID,
      sponsoring_human_slug: "dell", human_quote_present: true } }) } }, "consent_source_unavailable"],
    [{ bindingSource: bindingSource({ dell_consent: { granted: true, decision_ref: CONSENT_DECISION_ID } }) },
      "consent_flag_refused"],
  ];
  for (const [patch, reason_id] of refusals) {
    const r = new SpyRecorder();
    const driver = new FakeReaderDriver([page()]);
    const out = await runWith(mod, [], { reader: driver, recorder: r, ...patch });
    assertSafeStop(mod, out, r, { stop_class: "consent", reason_id });
    assert.equal(out.decision, "refused");
    assert.equal(out.browser_touched, false);
    assert.equal(driver.readCalls.length, 0, `${reason_id}: the browser is not observed`);
    assert.equal(r.calls.length, 0, `${reason_id}: nothing is recorded`);
  }
}

/** F17: a stop found while PLANNING the third finding leaves zero findings written. */
export async function checkNoPartialWrites(mod) {
  const recorder = new SpyRecorder();
  let asked = 0;
  const findingState = new FakeFindingState(() => (++asked === 3 ? [{ idempotency_key: "rw02-x", status: "open" }] : []));
  const out = await runWith(mod, [page({ opportunities: [opp("A"), opp("B"), opp("C")] })],
    { recorder, findingState, carrDeals: [carr("A"), carr("B"), carr("C")] });
  assertSafeStop(mod, out, recorder, { stop_class: "inconsistent_result", reason_id: "loop_episode_unreadable" });
  assert.equal(asked, 3, "the first two findings were planned before the third stopped the run");
  assert.deepEqual(recorder.calls.map(c => c.verb), ["record-salesforce-run-outcome"]);
}

/** F18: a closed loop whose finding is true again files a NEW key; an open one replays. */
export async function checkLoopEpisodes(mod) {
  const base = async () => {
    const r = new SpyRecorder();
    await runWith(mod, [page({ opportunities: [opp("A")] })], { recorder: r, carrDeals: [carr("A")] });
    return r.calls.find(c => c.verb === "add-loop").args.idempotency_key;
  };
  const k1 = await base();
  const cases = [
    [[], k1, 0],
    [[{ idempotency_key: k1, status: "open" }], k1, 0],
    [[{ idempotency_key: k1, status: "done" }], `${k1}:e2`, 1],
    [[{ idempotency_key: k1, status: "done" }, { idempotency_key: `${k1}:e2`, status: "open" }], `${k1}:e2`, 0],
    [[{ idempotency_key: k1, status: "dropped" }, { idempotency_key: `${k1}:e2`, status: "done" }], `${k1}:e3`, 1],
  ];
  for (const [rows, expected, reopened] of cases) {
    const recorder = new SpyRecorder();
    const out = await runWith(mod, [page({ opportunities: [opp("A")] })],
      { recorder, carrDeals: [carr("A")], findingState: new FakeFindingState({ [k1]: rows }) });
    assert.equal(out.decision, "reconciled");
    assert.equal(recorder.calls.find(c => c.verb === "add-loop").args.idempotency_key, expected, JSON.stringify(rows));
    assert.equal(out.counts.reopened_loops, reopened);
  }
  for (const rows of [[{ idempotency_key: k1, status: null }], [{ idempotency_key: `${k1}:e1`, status: "open" }],
    [{ idempotency_key: k1, status: "open" }, { idempotency_key: k1, status: "done" }]]) {
    const recorder = new SpyRecorder();
    const out = await runWith(mod, [page({ opportunities: [opp("A")] })],
      { recorder, carrDeals: [carr("A")], findingState: new FakeFindingState({ [k1]: rows }) });
    assertSafeStop(mod, out, recorder, { stop_class: "inconsistent_result", reason_id: "loop_episode_unreadable" });
  }
}

/** F19: the absence scope reads invoiced_on and admits open and closed-won deals only. */
export async function checkInvoicedScope(mod) {
  let sql = "";
  await mod.readCarrDealsForReconciliation({ query: async text => { sql = text; return { rows: [] }; } });
  assert.match(sql, /invoiced_on is null/);
  assert.match(sql, /outcome is null or outcome = 'won'/);
  assert.equal(mod.V5_RW02_ABSENCE_SCOPE, "open_or_closed_won_not_invoiced");
}

/** A run that fails unexpectedly after touching the browser still records an unclean outcome. */
export async function checkFailureRecordsUnclean(mod) {
  const recorder = new SpyRecorder();
  const reader = new FakeReaderDriver([page()]);
  reader.observe = async () => { throw new Error("browser crashed"); };
  await assert.rejects(runWith(mod, [], { reader, recorder }), /browser crashed/);
  assert.deepEqual(recorder.calls.map(c => [c.verb, c.args.outcome]), [["record-salesforce-run-outcome", "failed"]]);
  const ok = new SpyRecorder();
  await runWith(mod, [page({ opportunities: [opp("A", { team: ["joe-sf"] })] })], { recorder: ok, carrDeals: [carr("A")] });
  assert.deepEqual(ok.calls.map(c => [c.verb, c.args.outcome]), [["record-salesforce-run-outcome", "clean"]]);
}

/**
 * A record verb refusing the 2nd of 3 planned findings AFTER the plan passed:
 * a typed interruption with the true count, the run recorded unclean, and
 * never presented as a zero-write safe stop.
 */
export async function checkWriteInterruptedTyped(mod) {
  const recorder = new SpyRecorder();
  const inner = recorder.record.bind(recorder);
  let n = 0;
  recorder.record = async (verb, args) => {
    if (FINDING_VERBS.includes(verb) && ++n === 2)
      throw Object.assign(new Error("refused"), { error: "blocker_detail_vague" });
    return inner(verb, args);
  };
  const out = await runWith(mod, [page({ opportunities: [opp("A"), opp("B"), opp("C")] })],
    { recorder, carrDeals: [carr("A"), carr("B"), carr("C")] });
  assert.equal(out.answer_kind, "rw02-write-interrupted.v1");
  assert.equal(out.decision, "interrupted");
  assert.equal(out.reason_id, "finding_write_interrupted");
  assert.equal(out.findings_recorded, 1);
  assert.equal(out.partial_writes, 1, "a partial write is reported as one, not as zero");
  assert.equal(out.findings_planned, 3);
  assert.equal(out.cause, "blocker_detail_vague");
  assert.equal(out.run_outcome, "failed");
  assert.equal(out.message, mod.V5_RW02_WRITE_INTERRUPTED_MESSAGE);
  assert.ok(!Object.hasOwn(out, "stop_class"), "an interruption is not a safe stop");
  const outcomes = recorder.calls.filter(c => c.verb === "record-salesforce-run-outcome");
  assert.deepEqual(outcomes.map(c => c.args.outcome), ["failed"]);
}

/** An unclean run whose outcome cannot be recorded is surfaced, never swallowed. */
export async function checkUnrecordedOutcomeSurfaces(mod) {
  const failingOutcome = () => {
    const recorder = new SpyRecorder();
    const inner = recorder.record.bind(recorder);
    recorder.record = async (verb, args) => {
      if (verb === "record-salesforce-run-outcome") throw Object.assign(new Error("db down"), { error: "network" });
      return inner(verb, args);
    };
    return recorder;
  };
  // An unexpected failure mid-read, then the outcome write fails too.
  const reader = new FakeReaderDriver([page()]);
  reader.observe = async () => { throw new Error("browser crashed"); };
  await assert.rejects(runWith(mod, [], { reader, recorder: failingOutcome() }),
    e => e.code === "run_outcome_unrecorded" && e.detail.record_cause === "network");
  // A write interruption whose unclean outcome cannot be recorded.
  const recorder = failingOutcome();
  const inner = recorder.record.bind(recorder);
  recorder.record = async (verb, args) => {
    if (verb === "add-loop") throw Object.assign(new Error("refused"), { error: "no_block" });
    return inner(verb, args);
  };
  let n = 0;
  const inner2 = recorder.record;
  recorder.record = async (verb, args) => {
    if (verb === "add-loop" && ++n === 2) throw Object.assign(new Error("refused"), { error: "no_block" });
    if (verb === "add-loop") return { ok: true };
    return inner2(verb, args);
  };
  await assert.rejects(runWith(mod, [page({ opportunities: [opp("A"), opp("B")] })],
    { recorder, carrDeals: [carr("A"), carr("B")] }),
  e => e.code === "run_outcome_unrecorded" && e.detail.findings_recorded === 1 && e.detail.findings_planned === 2);
}

/** Only record-layer-made sources count: a look-alike loop-episode source is refused. */
export async function checkSourcesMustBeServerMade(mod) {
  await assert.rejects(runWith(mod, [page()], { findingState: { loopEpisodes: async () => [] } }),
    e => e.code === "finding_state_unavailable");
  const lookAlike = { read: async () => ({ revoked: false, record: { decision_id: CONSENT_DECISION_ID,
    sponsoring_human_slug: "dell", human_quote_present: true } }) };
  const out = await runWith(mod, [page()], { consentSource: lookAlike });
  assert.equal(out.reason_id, "consent_source_unavailable");
  await assert.rejects(runWith(mod, [page()], { clock: () => "2026-09-26T12:00:00.000Z" }),
    e => e.code === "unknown_option", "the run takes no caller clock");
}
