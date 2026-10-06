// V5-RW02 browser-only Salesforce READ adapter: read-only guarantee, typed
// safe stops (auth challenge, UI drift, unexpected recipient or account,
// policy conflict), the system-started sign-in on the server clock, Dell's
// consent record, loop episodes, the invoiced scope and the counter.
// Synthetic data only. No real browser, no real Salesforce, no network.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import * as mod from "../src/salesforce-browser-read-rw02.v5.js";
import {
  FakeReaderDriver, FakeCodeDriver, FakeClockClient, SpyRecorder, bindingSource, carr, opp, page, runWith, pressed,
  assertSafeStop, findingCalls,
  checkReadOnlyFacade, checkRecorderAllowlist, checkMissingJoeFlagged, checkCounterResets,
  checkCodeRetryBound, checkAbsenceNeedsCompleteRead, checkCodeStepNeedsSystemStart,
  checkNoFindingsAfterStop, checkHasNextStrict, checkKeysDistinctPerOpportunity, checkCredentialTextRefused,
  checkPageCapExact, checkFacadeForwardsNoArgs, checkCredentialKeyRefusedByName, checkRepeatRunArgsIdentical,
  checkEmptyReadInconclusive, checkGetterSwapHarmless,
  checkAuthChallengesStop, checkSystemSignInCodeExhausted, checkSystemSignInFilledContinues,
  checkServerClockTicket, checkUiDriftStops, checkUnexpectedAccountStops, checkPlanRecipientGuard,
  checkPolicyConflictStops, checkConsentRecord, checkNoPartialWrites, checkLoopEpisodes, checkInvoicedScope,
  checkFailureRecordsUnclean, checkWriteInterruptedTyped, checkUnrecordedOutcomeSurfaces,
  checkSourcesMustBeServerMade,
} from "./salesforce-browser-read-rw02.fixtures.mjs";

const {
  V5_RW02_READER_METHODS, V5_RW02_READ_MODE_RECORD_VERBS, V5_RW02_AUTONOMY_THRESHOLD,
  V5_RW02_CODE_AUTOFILL_MAX_ATTEMPTS, V5_RW02_SAFE_STOP_CLASSES, V5_RW02_SAFE_STOP_REASONS,
  V5_RW02_SAFE_STOP_MESSAGES, V5_RW02_SIGN_IN_OPERATOR_METHODS, V5RW02BrowserReadError,
  classifySignIn, attemptCodeAutofill, reconcileSalesforceDeals, evaluateAutonomyCounter,
  unavailableBindingSource,
} = mod;

// ---------------------------------------------------------------------------
// 1. Read mode performs ZERO Salesforce writes, and no write path is reachable.
// ---------------------------------------------------------------------------

test("the read facade exposes exactly the reader methods and never the driver", async () => {
  await checkReadOnlyFacade(mod);
  assert.deepEqual([...V5_RW02_READER_METHODS], ["nextPage", "observe"]);
});

test("the sign-in facade exposes exactly the Log in press and the four code-step clicks", () => {
  const driver = new FakeCodeDriver();
  driver.typeText = () => {}; driver.readCode = () => "123456";
  const facade = mod.signInOperator(driver);
  assert.deepEqual(Object.keys(facade).sort(), [...V5_RW02_SIGN_IN_OPERATOR_METHODS]);
  assert.equal(facade.typeText, undefined);
  assert.equal(facade.readCode, undefined);
  assert.equal(Object.getPrototypeOf(facade), null);
  assert.ok(Object.isFrozen(facade));
});

test("sign-in actions require page safety before each click", async () => {
  const mismatches = [
    ["origin_mismatch", { observation: { origin: "https://wrong-origin.invalid" } }],
    ["org_mismatch", { observation: { org_id: "00Dwrong" } }],
    ["signed_in_account_mismatch", { observation: { signed_in_account_ref: "wrong-seat" } }],
    ["ui_drift", { ui: { layout_ref: "changed-layout" } }],
    ["unexpected_recipient", { observation: { recipients: ["someone-else"] } }],
    ["policy_conflict", { observation: { policy_conflicts: ["read_forbidden"] } }],
    ["inconsistent_result", { observation: { result_consistency: "inconsistent" } }],
  ];
  for (const [reason_id, mismatch] of mismatches) {
    for (const step of ["password", "code"]) {
      const log = [];
      const recorder = new SpyRecorder();
      const signInOperator = new FakeCodeDriver({ log, fillOn: [1] });
      const password = page({ sign_in: { state: "password_prompt", credentials_autofilled: true },
        ...(step === "password" ? mismatch : {}) });
      const code = page({ sign_in: { state: "code_prompt" },
        ...(step === "code" ? mismatch : {}) });
      const reader = new FakeReaderDriver([[password, code, page()]]);
      const out = await runWith(mod, [], { reader, recorder, signInOperator,
        serverClock: mod.createServerClock(new FakeClockClient()) });
      assertSafeStop(mod, out, recorder, { stop_class: mod.V5_RW02_SAFE_STOP_REASONS[reason_id], reason_id });
      assert.equal(out.run_outcome, "stopped", `${step}: ${reason_id}`);
      assert.equal(recorder.calls.filter(c => c.verb === "record-salesforce-page-stop").length, 1,
        `${step}: ${reason_id} records its unsafe observation`);
      assert.equal(recorder.calls.some(c => c.args.outcome === "clean"), false);
      assert.deepEqual(log, step === "password" ? [] : ["clickLogIn"],
        `${step}: ${reason_id} causes no click on the unsafe page`);
    }
  }
});

test("contradictory page challenges stop before sign-in actions", async () => {
  for (const [challengeForStep, reason_id] of [
    [() => "captcha", "authentication_challenge"],
    [() => "unstated", "challenge_state_unobservable"],
    [() => "session_expired", "authentication_challenge"],
    [step => step === "password" ? "mfa_challenge" : "login_required", "authentication_challenge"],
  ]) {
    for (const step of ["password", "code"]) {
      const challenge = challengeForStep(step);
      const log = [];
      const recorder = new SpyRecorder();
      const password = page({ sign_in: { state: "password_prompt", credentials_autofilled: true },
        observation: step === "password" ? { challenge } : {} });
      const code = page({ sign_in: { state: "code_prompt" },
        observation: step === "code" ? { challenge } : {} });
      const out = await runWith(mod, [], { reader: new FakeReaderDriver([[password, code, page()]]),
        recorder, signInOperator: new FakeCodeDriver({ log, fillOn: [1] }),
        serverClock: mod.createServerClock(new FakeClockClient()) });
      assertSafeStop(mod, out, recorder, { stop_class: "auth_challenge", reason_id });
      assert.equal(out.run_outcome, "stopped");
      assert.equal(out.page_stop.challenge, challenge, `${step}: observed challenge is retained`);
      assert.equal(recorder.calls.filter(c => c.verb === "record-salesforce-page-stop").length, 1);
      assert.equal(recorder.calls.some(c => c.args.outcome === "clean"), false);
      assert.deepEqual(log, step === "password" ? [] : ["clickLogIn"],
        `${step}: ${challenge} causes no click on the unsafe page`);
    }
  }
});

test("the expected login and MFA challenges still complete", async () => {
  const log = [];
  const out = await runWith(mod, [], {
    reader: new FakeReaderDriver([[
      page({ sign_in: { state: "password_prompt", credentials_autofilled: true },
        observation: { challenge: "login_required" } }),
      page({ sign_in: { state: "code_prompt" }, observation: { challenge: "mfa_challenge" } }),
      page(),
    ]]),
    signInOperator: new FakeCodeDriver({ log, fillOn: [1] }),
    serverClock: mod.createServerClock(new FakeClockClient()),
  });
  assert.equal(out.decision, "reconciled");
  assert.ok(log.includes("clickLogIn"));
  assert.ok(log.includes("clickCodeField"));
});

test("a full read run never touches any driver write method", async () => {
  const driver = new FakeReaderDriver([
    page({ opportunities: [opp("A", { team: ["joe-sf"] })], has_next: true }),
    page({ opportunities: [opp("B", { team: [] })], has_next: false }),
  ]);
  const out = await runWith(mod, [], { reader: driver, carrDeals: [carr("A"), carr("B")] });
  assert.equal(out.decision, "reconciled");
  assert.equal(driver.writeCalls, 0, "no write-shaped driver method may be called");
  assert.deepEqual(driver.readCalls.sort(), ["nextPage", "observe", "observe"]);
  assert.equal(out.salesforce_writes, 0);
  assert.equal(out.effects.salesforce_writes, 0);
});

test("the recorder refuses every verb outside the read-mode allowlist", async () => {
  await checkRecorderAllowlist(mod);
  assert.deepEqual([...V5_RW02_READ_MODE_RECORD_VERBS],
    ["add-loop", "record-finding", "record-salesforce-page-stop", "record-salesforce-run-outcome"]);
});

test("a run records only through allowlisted verbs, and ends with its clean outcome", async () => {
  const recorder = new SpyRecorder();
  await runWith(mod, [page({ opportunities: [opp("A"), opp("Z", { team: ["joe-sf"] })] })],
    { recorder, carrDeals: [carr("A"), carr("Q")] });
  assert.ok(recorder.calls.length >= 4);
  for (const { verb } of recorder.calls) assert.ok(V5_RW02_READ_MODE_RECORD_VERBS.includes(verb), verb);
  const last = recorder.calls.at(-1);
  assert.equal(last.verb, "record-salesforce-run-outcome");
  assert.deepEqual(Object.keys(last.args).sort(), ["action_kind", "idempotency_key", "outcome", "run_ref"]);
  assert.equal(last.args.action_kind, mod.V5_RW02_READ_RUN_KIND);
  assert.equal(last.args.outcome, "clean");
});

test("the runner takes no mode, writer, partner, org, consent or capability argument", async () => {
  for (const extra of [{ mode: "write" }, { writer: {} }, { partner: "joe" }, { org_id: "00Dx" },
    { capability: {} }, { tenant: "carr-internal" }, { binding: {} }, { dellConsent: true }, { nowMs: 1 }]) {
    await assert.rejects(runWith(mod, [], extra),
      e => e instanceof V5RW02BrowserReadError && e.code === "unknown_option", JSON.stringify(extra));
  }
});

test("the module source names no Salesforce write method and no deal-mutating verb", () => {
  const src = readFileSync(fileURLToPath(new URL("../src/salesforce-browser-read-rw02.v5.js",
    import.meta.url)), "utf8");
  // A verb can only be called by its name as a string literal.
  for (const verb of ["update-deal", "patch-deal-field", "link-salesforce-reference", "new-deal",
    "record-salesforce-write-readback", "record-salesforce-duplicate-check", "call-verb",
    "revoke-salesforce-read-consent"])
    assert.ok(!src.includes(`"${verb}"`) && !src.includes(`'${verb}'`) && !src.includes(`\`${verb}`), verb);
  for (const method of ["saveOpportunity", "addTeamMember", ".type(", "typeText", ".fill(", "readCode"])
    assert.ok(!src.includes(method), method);
  // clickLogIn exists only as a sign-in facade method and its one call site.
  assert.equal(src.split("clickLogIn").length - 1, 2);
});

// ---------------------------------------------------------------------------
// 2. Identity and consent come from server-side records, never the caller.
// ---------------------------------------------------------------------------

test("no org binding means no run", async () => {
  const recorder = new SpyRecorder();
  const driver = new FakeReaderDriver([page()]);
  const out = await runWith(mod, [], { reader: driver, recorder, bindingSource: unavailableBindingSource });
  assertSafeStop(mod, out, recorder, { stop_class: "binding", reason_id: "org_binding_unavailable" });
  assert.equal(driver.readCalls.length, 0, "the browser is not even observed");
  assert.equal(recorder.calls.length, 0);
});

test("the source must be Dell's org", async () => {
  const driver = new FakeReaderDriver([page()]);
  const out = await runWith(mod, [], { reader: driver, bindingSource: bindingSource({ source_partner: "joe" }) });
  assertSafeStop(mod, out, null, { stop_class: "binding", reason_id: "source_not_dell" });
  assert.equal(driver.readCalls.length, 0);
});

test("Dell's consent is its decision record: allowed with the consent ref, refused when missing, revoked or a flag", async () => {
  await checkConsentRecord(mod);
});

test("evaluateDellConsent needs every property of the record at once", () => {
  const record = { decision_id: mod.V5_RW02_DELL_CONSENT_DECISION_ID, sponsoring_human_slug: "dell",
    human_quote_present: true };
  assert.equal(mod.evaluateDellConsent({ record, revoked: false }).decision, "allowed");
  assert.equal(mod.evaluateDellConsent({ record, revoked: false }).recorded_by_partner, "dell");
  assert.equal(mod.evaluateDellConsent({ record, revoked: false }).consent_basis, "dell_own_record");
  for (const reading of [null, "yes", true, { granted: true }, { record, revoked: undefined },
    { record: { ...record, human_quote_present: "yes" }, revoked: false }]) {
    assert.equal(mod.evaluateDellConsent(reading).decision, "refused", JSON.stringify(reading));
  }
});

test("the server-side consent source reads the pinned decision through the record layer", async () => {
  const seen = [];
  const src = mod.createDellConsentSource({ query: async (sql, params) => {
    seen.push([sql, params]);
    return { rows: [{ consent: JSON.stringify({ record: null, revoked: false }) }] };
  } });
  assert.deepEqual(await src.read(), { record: null, revoked: false });
  assert.match(seen[0][0], /ops\.rw02_consent_record\(\$1::uuid\)/);
  assert.deepEqual(seen[0][1], [mod.V5_RW02_DELL_CONSENT_DECISION_ID]);
});

// ---------------------------------------------------------------------------
// 3. Typed safe stops (checkable_done 1).
// ---------------------------------------------------------------------------

test("every registered stop reason has a registered class and a plain-language message", () => {
  for (const [reason_id, cls] of Object.entries(V5_RW02_SAFE_STOP_REASONS)) {
    assert.ok(V5_RW02_SAFE_STOP_CLASSES.includes(cls), reason_id);
    assert.ok(V5_RW02_SAFE_STOP_MESSAGES[reason_id].length > 30, reason_id);
    assert.ok(!/undefined|\$\{/.test(V5_RW02_SAFE_STOP_MESSAGES[reason_id]), reason_id);
    // What happened, then exactly one outcome fixed by where the stop happens,
    // then a next step: a consent or binding stop never says "nothing was
    // filed" (it never reached the browser), and a page stop never says the
    // read did not start.
    const message = V5_RW02_SAFE_STOP_MESSAGES[reason_id];
    const outcomes = Object.values(mod.V5_RW02_SAFE_STOP_OUTCOMES).filter(o => message.includes(o));
    assert.equal(outcomes.length, 1, reason_id);
    const expected = ["binding", "consent"].includes(cls) ? mod.V5_RW02_SAFE_STOP_OUTCOMES.before_browser
      : ["finding_recipient_unexpected", "finding_outside_scope", "loop_episode_unreadable"].includes(reason_id)
        ? mod.V5_RW02_SAFE_STOP_OUTCOMES.plan : mod.V5_RW02_SAFE_STOP_OUTCOMES.page;
    assert.equal(outcomes[0], expected, reason_id);
    const next = message.slice(message.indexOf(expected) + expected.length).trim();
    assert.ok(next.length > 10 && next.endsWith("."), `${reason_id} has no next step`);
  }
  for (const r of mod.V5_RW02_SIGN_IN_STOP_REASONS) assert.equal(V5_RW02_SAFE_STOP_REASONS[r], "auth_challenge", r);
  for (const cls of ["auth_challenge", "ui_drift", "unexpected_recipient_or_account", "policy_conflict"])
    assert.ok(Object.values(V5_RW02_SAFE_STOP_REASONS).includes(cls), cls);
});

test("CAPTCHA, a new-device prompt and a security check stop typed after a clean page, filing nothing", async () => {
  await checkAuthChallengesStop(mod);
});

test("a system-started sign-in whose code never fills stops after exactly 3 attempts", async () => {
  await checkSystemSignInCodeExhausted(mod);
});

test("a system-started sign-in that fills continues; without a sign-in step it is the partner's", async () => {
  await checkSystemSignInFilledContinues(mod);
});

test("sign-in is only before the first page, and Log in is pressed at most once", async () => {
  const mid = new SpyRecorder();
  const out = await runWith(mod, [page({ opportunities: [opp("A")], has_next: true }),
    page({ sign_in: { state: "password_prompt", credentials_autofilled: true } })],
  { recorder: mid, signInOperator: new FakeCodeDriver(),
    serverClock: mod.createServerClock({ query: async () => ({ rows: [{ now_ms: "1" }] }) }) });
  assertSafeStop(mod, out, mid, { stop_class: "auth_challenge", reason_id: "sign_in_prompt_mid_read" });
  const log = [];
  const twice = new SpyRecorder();
  const again = await runWith(mod, [], { recorder: twice,
    reader: new FakeReaderDriver([[page({ sign_in: { state: "password_prompt", credentials_autofilled: true } })]]),
    signInOperator: new FakeCodeDriver({ log }),
    serverClock: mod.createServerClock({ query: async () => ({ rows: [{ now_ms: "1" }] }) }) });
  assertSafeStop(mod, again, twice, { stop_class: "auth_challenge", reason_id: "sign_in_not_accepted" });
  assert.deepEqual(log, ["clickLogIn"]);
});

test("a sign-in ticket is minted on the server clock at the press; caller time is refused", async () => {
  await checkServerClockTicket(mod);
});

test("UI drift stops: a missing selector, a changed layout, an unparseable row, a self-reported fingerprint", async () => {
  await checkUiDriftStops(mod);
});

test("the UI fingerprint is the adapter's digest of layout and matched selectors", () => {
  const a = mod.rw02ReadUiFingerprint({ layout_ref: "l.v1", selectors_matched: ["b", "a", "a"] });
  assert.equal(a, mod.rw02ReadUiFingerprint({ layout_ref: "l.v1", selectors_matched: ["a", "b"] }));
  assert.notEqual(a, mod.rw02ReadUiFingerprint({ layout_ref: "l.v2", selectors_matched: ["a", "b"] }));
  assert.notEqual(a, mod.rw02ReadUiFingerprint({ layout_ref: "l.v1", selectors_matched: ["a", "b", "c"] }));
});

test("another seat, org or origin, or a page showing recipients, stops as unexpected account or recipient", async () => {
  await checkUnexpectedAccountStops(mod);
});

test("a finding bound for the wrong recipient or outside scope refuses the whole plan", async () => {
  await checkPlanRecipientGuard(mod);
});

test("a page-reported policy conflict, a field value or a credential key stops as a policy conflict", async () => {
  await checkPolicyConflictStops(mod);
});

test("a stop found while planning the third finding leaves zero findings written", async () => {
  await checkNoPartialWrites(mod);
});

test("an unexpected failure after the browser is touched still records the run unclean", async () => {
  await checkFailureRecordsUnclean(mod);
});

test("a record verb refusing part-way is a typed interruption with its true count, and an unclean run", async () => {
  await checkWriteInterruptedTyped(mod);
});

test("an unclean run whose outcome cannot be recorded is surfaced, never swallowed", async () => {
  await checkUnrecordedOutcomeSurfaces(mod);
});

test("consent and loop-episode sources count only when the record layer's factory made them; no caller clock", async () => {
  await checkSourcesMustBeServerMade(mod);
});

test("every run gets a fresh run reference that nothing a caller supplies can repeat", async () => {
  const a = await runWith(mod, [page()]);
  const b = await runWith(mod, [page()]);
  assert.match(a.run_ref, /^[0-9a-f]{24}$/);
  assert.notEqual(a.run_ref, b.run_ref);
});

// ---------------------------------------------------------------------------
// 4. Sign-in classification and the code step.
// ---------------------------------------------------------------------------

test("sign-in stop conditions produce typed stops", () => {
  const cases = [
    [{ state: "password_prompt", credentials_autofilled: false }, "password_prompt_without_autofill"],
    [{ state: "password_prompt" }, "password_prompt_without_autofill"],
    [{ state: "code_prompt" }, "unprompted_code_prompt"],
    [{ state: "captcha" }, "captcha"],
    [{ state: "new_device_prompt" }, "new_device_prompt"],
    [{ state: "security_challenge" }, "security_challenge"],
    [{ state: "unknown" }, "sign_in_state_unobservable"],
    [{ state: "two_factor_push" }, "sign_in_state_unobservable"],
  ];
  for (const [signIn, reason] of cases) {
    const a = classifySignIn(signIn, { systemStartedSignIn: false });
    assert.equal(a.decision, "stop", reason);
    assert.equal(a.reason_id, reason);
    assert.equal(a.stop_class, "auth_challenge");
    assert.equal(a.resolution_owner, "partner_at_the_browser");
    assert.equal(a.credential_entry_performed, false);
  }
  assert.equal(classifySignIn({ state: "signed_in" }, { systemStartedSignIn: false }).decision, "continue");
  assert.equal(classifySignIn({ state: "password_prompt", credentials_autofilled: true }).decision, "log_in_permitted");
  // Even a system-started sign-in never answers a CAPTCHA or a new device.
  for (const state of ["captcha", "new_device_prompt"])
    assert.equal(classifySignIn({ state }, { systemStartedSignIn: true }).decision, "stop");
});

test("a sign-in observation carrying a credential-shaped field is refused outright, by name", async () => {
  await checkCredentialKeyRefusedByName(mod);
  assert.throws(() => classifySignIn({ state: "code_prompt", colour: "x" }, { systemStartedSignIn: false }),
    e => e.code === "unknown_field");
});

test("a code prompt the system did not start stops the run and is recorded as an MFA page stop", async () => {
  const recorder = new SpyRecorder();
  const out = await runWith(mod, [page({ sign_in: { state: "code_prompt" } })], { recorder });
  assertSafeStop(mod, out, recorder, { stop_class: "auth_challenge", reason_id: "unprompted_code_prompt" });
  assert.equal(out.page_stop.challenge, "mfa_challenge");
  assert.equal(recorder.calls[0].verb, "record-salesforce-page-stop");
  assert.equal(recorder.calls[0].args.page.observation.challenge, "mfa_challenge");
});

test("the code autofill step: click field, click the suggestion, check filled", async () => {
  const { ticket, driver } = await pressed(mod, { driver: new FakeCodeDriver({ suggestionOn: [1],
    fillAfterSuggestion: true }) });
  const a = await attemptCodeAutofill({ driver, ticket });
  assert.equal(a.decision, "filled");
  assert.equal(a.attempts, 1);
  assert.deepEqual(driver.log, ["clickLogIn", "clickCodeField", "autofillSuggestionVisible",
    "clickAutofillSuggestion", "codeFieldFilled"]);
});

test("the code autofill step stops for the partner after exactly 3 attempts", async () => {
  await checkCodeRetryBound(mod);
  assert.equal(V5_RW02_CODE_AUTOFILL_MAX_ATTEMPTS, 3);
});

test("the code autofill step never clicks anything for a sign-in the system did not start", async () => {
  await checkCodeStepNeedsSystemStart(mod);
});

test("a sign-in ticket is single-use", async () => {
  const { ticket } = await pressed(mod);
  const first = new FakeCodeDriver({ fillOn: [1] });
  assert.equal((await attemptCodeAutofill({ driver: first, ticket })).decision, "filled");
  const reuse = new FakeCodeDriver({ fillOn: [1] });
  assert.equal((await attemptCodeAutofill({ driver: reuse, ticket })).reason_id, "sign_in_ticket_spent");
  assert.deepEqual(reuse.log, []);
  assert.ok(Object.isFrozen(ticket) && Object.keys(ticket).length === 0, "the ticket carries no readable state");
});

test("a run that stops on a later page records the stop and concludes nothing", async () => {
  await checkNoFindingsAfterStop(mod);
});

test("a non-boolean filled report stops rather than guessing", async () => {
  const { ticket } = await pressed(mod);
  const driver = new FakeCodeDriver({ suggestionOn: [], filledValue: "123456" });
  const a = await attemptCodeAutofill({ driver, ticket });
  assert.equal(a.decision, "stop");
  assert.equal(a.reason_id, "code_fill_state_unobservable");
  assert.ok(!JSON.stringify(a).includes("123456"), "the code value never enters the answer");
});

// ---------------------------------------------------------------------------
// 5. Reconciliation: presence and partner membership only.
// ---------------------------------------------------------------------------

test("a deal in Dell's Salesforce without Joe is flagged; Joe as owner or team member is present", async () => {
  await checkMissingJoeFlagged(mod);
  const r = reconcileSalesforceDeals({ joeUserRef: "joe-sf", complete: true,
    salesforce: [opp("A", { owner: "joe-sf", team: [] }), opp("B", { team: ["joe-sf"] })],
    carr: [carr("A"), carr("B")] });
  assert.deepEqual(r.missing_joe, []);
});

test("unknown-to-CARR and absent-from-Salesforce are both found, matching by opportunity id only", () => {
  const r = reconcileSalesforceDeals({ joeUserRef: "joe-sf", complete: true,
    salesforce: [opp("A", { team: ["joe-sf"] }), opp("N", { team: ["joe-sf"], name: "Deal Q" })],
    carr: [carr("A"), carr("Q"), { deal_id: "00000000-0000-4000-8000-00000000000f", name: "Unlinked",
      salesforce_id: null }] });
  assert.deepEqual(r.unknown_to_carr.map(f => f.opportunity_id), [opp("N").opportunity_id]);
  assert.deepEqual(r.absent_from_salesforce.map(f => [f.name, f.reason_id]).sort(),
    [["Deal Q", "salesforce_id_not_seen"], ["Unlinked", "no_salesforce_link"]]);
});

test("absence is never concluded from an incomplete read", async () => {
  await checkAbsenceNeedsCompleteRead(mod);
});

test("the same opportunity read twice with different facts stops as an inconsistent result", async () => {
  assert.throws(() => reconcileSalesforceDeals({ joeUserRef: "joe-sf", complete: true,
    salesforce: [opp("A"), opp("A", { team: ["joe-sf"] })], carr: [] }),
  e => e.code === "inconsistent_result");
  const recorder = new SpyRecorder();
  const out = await runWith(mod, [page({ opportunities: [opp("A")], has_next: true }),
    page({ opportunities: [opp("A", { team: ["joe-sf"] })] })], { recorder });
  assertSafeStop(mod, out, recorder, { stop_class: "inconsistent_result",
    reason_id: "opportunity_read_twice_differently" });
});

test("reconciliation never compares or proposes field values", () => {
  const r = reconcileSalesforceDeals({ joeUserRef: "joe-sf", complete: true,
    salesforce: [opp("A", { team: ["joe-sf"] })], carr: [carr("A")] });
  assert.equal(r.field_sync, "not_performed_presence_and_membership_only");
  assert.throws(() => reconcileSalesforceDeals({ joeUserRef: "joe-sf", complete: true,
    salesforce: [{ ...opp("A"), stage: "Closed Won" }], carr: [] }), e => e.code === "unknown_field");
});

test("findings are recorded: missing Joe as a Dell loop, unknown as a partner loop, absent as a finding", async () => {
  const recorder = new SpyRecorder();
  const out = await runWith(mod, [page({ opportunities: [opp("A"), opp("N", { team: ["joe-sf"] })] })],
    { recorder, carrDeals: [carr("A"), carr("Q")] });
  assert.equal(out.decision, "reconciled");
  assert.deepEqual(out.counts, { missing_joe: 1, unknown_to_carr: 1, absent_from_salesforce: 1, reopened_loops: 0 });
  const loops = recorder.calls.filter(c => c.verb === "add-loop").map(c => c.args);
  const dell = loops.find(l => l.owner === "Dell");
  assert.equal(dell.kind, "team_loop");
  assert.match(dell.title, /Add Joe/);
  const partner = loops.find(l => l.owner === "Joe");
  assert.equal(partner.kind, "open_loop");
  assert.equal(partner.blocker, "human_only");
  const finding = recorder.calls.find(c => c.verb === "record-finding").args;
  assert.equal(finding.found, false);
  assert.equal(finding.internal, true);
  assert.equal(finding.subject, carr("Q").deal_id);
  assert.equal(finding.kind, "salesforce_presence:salesforce_id_not_seen");
  assert.match(finding.source, /reason salesforce_id_not_seen$/);
  // Nothing the browser showed about sign-in or the seat leaks into a record.
  for (const c of recorder.calls) {
    assert.ok(!JSON.stringify(c.args).includes("dell-seat"), `${c.verb} carries the seat ref`);
    assert.ok(!Object.hasOwn(c.args, "tenant") && !Object.hasOwn(c.args, "actor"),
      `${c.verb} passes a caller identity`);
  }
  assert.equal(findingCalls(recorder).length, 3);
});

test("a closed loop whose finding is true again files a new action; an open one replays", async () => {
  await checkLoopEpisodes(mod);
});

test("a repeat run sends byte-identical finding arguments, so nothing is refused or filed twice", async () => {
  await checkRepeatRunArgsIdentical(mod);
});

test("two opportunities with the same name file two loops with two keys", async () => {
  await checkKeysDistinctPerOpportunity(mod);
});

test("a credential-shaped opportunity name is refused", async () => {
  await checkCredentialTextRefused(mod);
});

test("a non-boolean has_next is a malformed page, not the end of the list", async () => {
  await checkHasNextStrict(mod);
});

test("the page cap is exact", async () => {
  await checkPageCapExact(mod);
});

test("the read facade passes no arguments to the driver", async () => {
  await checkFacadeForwardsNoArgs(mod);
});

test("an empty complete read concludes no absence", async () => {
  await checkEmptyReadInconclusive(mod);
});

test("a getter that answers differently on a second read cannot smuggle a value into a record", async () => {
  await checkGetterSwapHarmless(mod);
});

test("the absence scope is open and closed-won deals not yet invoiced", async () => {
  await checkInvoicedScope(mod);
  const r = reconcileSalesforceDeals({ joeUserRef: "joe-sf", complete: true,
    salesforce: [opp("A", { team: ["joe-sf"] })], carr: [carr("A")] });
  assert.equal(r.absence_scope, "open_or_closed_won_not_invoiced");
});

test("the run caps the number of pages it will read", async () => {
  const pages = Array.from({ length: mod.V5_RW02_READ_PAGE_CAP + 1 }, () => page({ has_next: true }));
  const recorder = new SpyRecorder();
  const out = await runWith(mod, pages, { recorder });
  assertSafeStop(mod, out, recorder, { stop_class: "inconsistent_result", reason_id: "page_cap_reached" });
});

// ---------------------------------------------------------------------------
// 6. The 5-consecutive-clean counter (decision 493de438). Data + pure function;
//    the server-side store is tested in salesforce-read-run-store-rw02.
// ---------------------------------------------------------------------------

const run = (n, outcome = "clean", action_kind = "opportunity_phase_update") =>
  ({ run_ref: `run-${action_kind}-${n}`, action_kind, outcome, execution_mode: "attended" });

test("five consecutive clean attended runs meet the threshold, four do not", () => {
  assert.equal(V5_RW02_AUTONOMY_THRESHOLD, 5);
  const four = evaluateAutonomyCounter({ action_kind: "opportunity_phase_update",
    runs: [1, 2, 3, 4].map(n => run(n)) });
  assert.equal(four.consecutive_clean, 4);
  assert.equal(four.threshold_met, false);
  const five = evaluateAutonomyCounter({ action_kind: "opportunity_phase_update",
    runs: [1, 2, 3, 4, 5].map(n => run(n)) });
  assert.equal(five.consecutive_clean, 5);
  assert.equal(five.threshold_met, true);
});

test("any unclean run resets the count", async () => {
  await checkCounterResets(mod);
  for (const bad of ["refused", "corrected", "failed", "stopped"]) {
    const a = evaluateAutonomyCounter({ action_kind: "opportunity_phase_update",
      runs: [run(1), run(2), run(3), run(4), run(5, bad), run(6)] });
    assert.equal(a.consecutive_clean, 1, bad);
    assert.equal(a.threshold_met, false);
  }
});

test("the read run is a counted kind of its own", () => {
  assert.ok(mod.V5_RW02_AUTONOMY_KINDS.includes(mod.V5_RW02_READ_RUN_KIND));
  const a = evaluateAutonomyCounter({ action_kind: mod.V5_RW02_READ_RUN_KIND,
    runs: [1, 2, 3, 4, 5].map(n => run(n, "clean", mod.V5_RW02_READ_RUN_KIND)) });
  assert.equal(a.threshold_met, true);
});

test("the counter never promotes anything in this slice", () => {
  const a = evaluateAutonomyCounter({ action_kind: "opportunity_phase_update",
    runs: [1, 2, 3, 4, 5, 6, 7].map(n => run(n)) });
  assert.equal(a.threshold_met, true);
  assert.equal(a.autonomy_active, false);
  assert.equal(a.promotion, "not_performed_in_this_slice");
  assert.equal(a.outward_effect_granted, false);
});

test("runs of another kind neither count nor reset (no trust inheritance)", () => {
  const a = evaluateAutonomyCounter({ action_kind: "opportunity_phase_update",
    runs: [run(1), run(2), run(1, "failed", "opportunity_create"), run(3), run(2, "clean", "opportunity_create")] });
  assert.equal(a.consecutive_clean, 3);
});

test("an unattended run, a duplicate run and an unknown outcome are refused", () => {
  assert.throws(() => evaluateAutonomyCounter({ action_kind: "opportunity_phase_update",
    runs: [{ ...run(1), execution_mode: "unattended" }] }), e => e.code === "unattended_run_refused");
  assert.throws(() => evaluateAutonomyCounter({ action_kind: "opportunity_phase_update",
    runs: [run(1), run(1)] }), e => e.code === "run_counted_twice");
  assert.throws(() => evaluateAutonomyCounter({ action_kind: "opportunity_phase_update",
    runs: [run(1, "mostly_clean")] }), e => e.code === "unknown_outcome");
  assert.throws(() => evaluateAutonomyCounter({ action_kind: "etl_protected_send", runs: [] }),
    e => e.code === "unknown_action_kind");
});
