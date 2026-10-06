// Planted bugs for the V5-RW02 browser read adapter. Each mutant is loaded from
// a scratch copy; the SAME property check the main suite runs must pass on the
// real module and fail on the mutant.
//
// R1-R8: the builder's set, chosen with Jev (verification_selection,
// 2026-09-26; all eight scored >= 0.72). M1-M11 and F1-F5: the mutants and
// findings from the independent Opus review of PR #1320 at b6d79860, each of
// which survived the first suite.

import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import * as real from "../src/salesforce-browser-read-rw02.v5.js";
import {
  checkReadOnlyFacade, checkRecorderAllowlist, checkMissingJoeFlagged, checkCounterResets,
  checkCodeRetryBound, checkAbsenceNeedsCompleteRead, checkCodeStepNeedsSystemStart,
  checkNoFindingsAfterStop, checkHasNextStrict, checkKeysDistinctPerOpportunity, checkCredentialTextRefused,
  checkPageCapExact, checkFacadeForwardsNoArgs, checkCredentialKeyRefusedByName, checkRepeatRunArgsIdentical,
  checkEmptyReadInconclusive, checkGetterSwapHarmless,
  checkAuthChallengesStop, checkSystemSignInCodeExhausted, checkServerClockTicket, checkUiDriftStops,
  checkUnexpectedAccountStops, checkPlanRecipientGuard, checkPolicyConflictStops, checkConsentRecord,
  checkNoPartialWrites, checkLoopEpisodes, checkInvoicedScope, checkFailureRecordsUnclean,
  checkWriteInterruptedTyped, checkUnrecordedOutcomeSurfaces, checkSourcesMustBeServerMade,
} from "./salesforce-browser-read-rw02.fixtures.mjs";

const SRC = fileURLToPath(new URL("../src/", import.meta.url));
const FILE = "salesforce-browser-read-rw02.v5.js";
const WORK = mkdtempSync(join(tmpdir(), "rw02-browser-read-mutants-"));
test.after(() => rmSync(WORK, { recursive: true, force: true }));
let serial = 0;

function relink(source) {
  return source.replace(/from\s+"\.\/([^"]+)"/g, (_, file) =>
    `from "${pathToFileURL(join(SRC, file)).href}"`);
}
/** `pairs` is [[anchor, replacement], ...]; every anchor must occur exactly once. */
async function mutant(pairs) {
  let source = readFileSync(join(SRC, FILE), "utf8");
  for (const [anchor, replacement] of pairs) {
    assert.equal(source.split(anchor).length - 1, 1, `unique mutant anchor: ${anchor}`);
    source = source.replace(anchor, replacement);
  }
  const path = join(WORK, `${++serial}.mjs`);
  writeFileSync(path, relink(source));
  return import(pathToFileURL(path).href);
}

async function killed(check, ...pairs) {
  await check(real); // the check holds on the real module
  const mod = await mutant(pairs);
  await assert.rejects(Promise.resolve().then(() => check(mod)), "the check must fail on the mutant");
}

// --- Builder's set --------------------------------------------------------

test("MUTANT R1 write reachable: the read facade hands back the driver itself", () => killed(
  checkReadOnlyFacade,
  ["    facade[name] = () => method.call(driver);\n  }\n  return Object.freeze(facade);\n}",
    "    facade[name] = () => method.call(driver);\n  }\n  return driver;\n}"]));

test("MUTANT R2 write reachable: the read-mode recorder forwards any verb", () => killed(
  checkRecorderAllowlist,
  ["if (!V5_RW02_READ_MODE_RECORD_VERBS.includes(verb)) fail(", "if (false) fail("]));

test("MUTANT R3 counter not resetting: an unclean run is skipped instead of resetting", () => killed(
  checkCounterResets,
  ["else { consecutive_clean = 0; last_reset_run_ref = r.run_ref; }", "else { last_reset_run_ref = r.run_ref; }"]));

test("MUTANT R4 missing Joe not flagged: only an empty team counts as missing Joe", () => killed(
  checkMissingJoeFlagged,
  [".filter(o => o.owner_ref !== joeUserRef && !o.team_member_refs.includes(joeUserRef))",
    ".filter(o => o.owner_ref !== joeUserRef && o.team_member_refs.length === 0)"]));

test("MUTANT R5 unbounded code retry: the 3-attempt bound is removed", () => killed(
  checkCodeRetryBound,
  ["while (state.attempts_used < V5_RW02_CODE_AUTOFILL_MAX_ATTEMPTS) {", "while (true) {"]));

test("MUTANT R6 absence concluded from a partial read", () => killed(
  checkAbsenceNeedsCompleteRead,
  ["const absent_from_salesforce = !concluded ? [] : deals", "const absent_from_salesforce = deals"]));

test("MUTANT R7 code step accepts a caller-built {started_by_system: true} as a sign-in", () => killed(
  checkCodeStepNeedsSystemStart,
  ["SIGN_IN_TICKETS.get(ticket) : undefined;",
    "(SIGN_IN_TICKETS.get(ticket) ?? (ticket.started_by_system === true ? { minted_at_ms: 0, readClock: async () => 0, attempts_used: 0, spent: false } : undefined)) : undefined;"]));

test("MUTANT R8 a stopped run still records findings from its partial read", () => killed(
  checkNoFindingsAfterStop,
  ["        return stopHere(verdict.reason_id);\n      }", "        await stopHere(verdict.reason_id); break;\n      }"]));

// --- Reviewer's surviving mutants -----------------------------------------

test("MUTANT M1 a non-boolean has_next is treated as the end of the list", () => killed(
  checkHasNextStrict,
  ["if (hasNext !== true && hasNext !== false) return stopRun(\"observation_shape_drift\", { index });\n      if (hasNext === false) break;",
    "if (hasNext !== true) break;"]));

test("MUTANT M4 the missing-Joe loop is keyed on the opportunity name", () => killed(
  checkKeysDistinctPerOpportunity,
  ["key(\"missing-joe\", f.opportunity_id)", "key(\"missing-joe\", f.name)"]));

test("MUTANT M5 the credential-pattern text check is dropped", () => killed(
  checkCredentialTextRefused,
  ["  for (const pattern of Object.values(V5_RW02_CREDENTIAL_PATTERNS))", "  for (const pattern of [])"]));

test("MUTANT M8a page cap off by one (one page too many)", () => killed(
  checkPageCapExact,
  ["if (index >= V5_RW02_READ_PAGE_CAP) return", "if (index > V5_RW02_READ_PAGE_CAP) return"]));

test("MUTANT M8b page cap off by one (one page too few)", () => killed(
  checkPageCapExact,
  ["if (index >= V5_RW02_READ_PAGE_CAP) return", "if (index >= V5_RW02_READ_PAGE_CAP - 1) return"]));

test("MUTANT M9 the reader facade forwards arguments to the driver", () => killed(
  checkFacadeForwardsNoArgs,
  ["facade[name] = () => method.call(driver);", "facade[name] = (...args) => method.apply(driver, args);"]));

test("MUTANT M10 the credential-key refusal is dropped (falls through to unknown_field)", () => killed(
  checkCredentialKeyRefusedByName,
  ["    if (CREDENTIAL_KEYS.test(key)) fail(\"credential_field_refused\",", "    if (false) fail(\"credential_field_refused\","]));

test("MUTANT M11 the unknown-to-CARR key includes run_ref", () => killed(
  checkRepeatRunArgsIdentical,
  ["key(\"unknown-to-carr\", f.opportunity_id)", "key(\"unknown-to-carr\", f.opportunity_id, run_ref)"]));

// --- Reviewer's findings as mutants ---------------------------------------

test("MUTANT F1 run-specific text in source_note (repeat run hits key_reuse)", () => killed(
  checkRepeatRunArgsIdentical,
  ["const source_note = \"V5-RW02 attended browser read of Dell's Salesforce (decisions c04ac197, c0014a37)\";",
    "const source_note = `V5-RW02 attended browser read, run ${run_ref}`;"]));

test("MUTANT F2 the code bound is per call: the ticket is not spent and the count is call-local", () => killed(
  checkCodeRetryBound,
  ["  state.spent = true;\n", "\n"],
  ["while (state.attempts_used < V5_RW02_CODE_AUTOFILL_MAX_ATTEMPTS) {\n    const attempt = ++state.attempts_used;",
    "for (let attempt = 1; attempt <= V5_RW02_CODE_AUTOFILL_MAX_ATTEMPTS; attempt++) {"]));

test("MUTANT F3 TOCTOU: no snapshot copy and a second read of each field", () => killed(
  checkGetterSwapHarmless,
  ["try { sfCopy = structuredClone(salesforce); carrCopy = structuredClone(carr); }",
    "try { sfCopy = salesforce; carrCopy = carr; }"],
  ["  const team = [...team_member_refs];\n  return {\n    opportunity_id,",
    "  const team = [...raw.team_member_refs];\n  return {\n    opportunity_id: raw.opportunity_id,"]));

test("MUTANT F4 the absent finding is keyed per run", () => killed(
  checkRepeatRunArgsIdentical,
  ["idempotency_key: key(\"absent\", f.deal_id, f.reason_id)", "idempotency_key: key(\"absent\", f.deal_id, run_ref)"]));

test("MUTANT F5 an empty complete read concludes every open deal absent", () => killed(
  checkEmptyReadInconclusive,
  ["const concluded = complete && opportunities.length > 0;", "const concluded = complete;"]));

// --- Safe stops, the follow-ups and the counter's outcome record ----------
// S1-S30: one or more planted bugs per safe-stop refusal and per follow-up,
// against the fixtures Jev ranked proportionate (verification_selection,
// 2026-09-27). Every one must be killed by the check named beside it.

test("MUTANT S1 auth: a CAPTCHA is treated as signed in", () => killed(
  checkAuthChallengesStop,
  ["if (raw.state === \"captcha\") return signInStop(\"captcha\");",
    "if (raw.state === \"captcha\") return freeze({ decision: \"continue\" });"]));

test("MUTANT S2 auth: a new-device prompt is reported as an unobservable state", () => killed(
  checkAuthChallengesStop,
  ["if (raw.state === \"new_device_prompt\") return signInStop(\"new_device_prompt\");",
    "if (raw.state === \"new_device_prompt\") return signInStop(\"sign_in_state_unobservable\");"]));

test("MUTANT S3 auth: a fourth code attempt is allowed", () => killed(
  checkSystemSignInCodeExhausted,
  ["while (state.attempts_used < V5_RW02_CODE_AUTOFILL_MAX_ATTEMPTS) {",
    "while (state.attempts_used <= V5_RW02_CODE_AUTOFILL_MAX_ATTEMPTS) {"]));

test("MUTANT S4 auth: a code prompt after a spent code step gets a second code step", () => killed(
  checkSystemSignInCodeExhausted,
  ["if (code.decision !== \"filled\") return stopHere(code.reason_id);",
    "if (code.decision !== \"filled\") { signIn.codeTried = false; signIn.ticket = null; continue; }"]));

test("MUTANT S5 clock: a caller-supplied time is accepted at the press", () => killed(
  checkServerClockTicket,
  ["  if (\"nowMs\" in args || \"now\" in args || \"clock\" in args) fail(\"caller_clock_refused\",\n    \"the sign-in time is read from the server clock, never passed in\");",
    "  if (false) fail(\"caller_clock_refused\", \"\");"]));

test("MUTANT S6 clock: the server clock is read after the click, not at the press", () => killed(
  checkServerClockTicket,
  ["  const minted_at_ms = await readClock();\n  await operator.clickLogIn();",
    "  await operator.clickLogIn();\n  const minted_at_ms = await readClock();"]));

test("MUTANT S7 clock: staleness is never measured (the mint time stands in for now)", () => killed(
  checkServerClockTicket,
  ["const nowMs = await state.readClock();", "const nowMs = state.minted_at_ms;"]));

test("MUTANT S8 clock: any function is accepted as the server clock", () => killed(
  checkServerClockTicket,
  ["const read = clock !== null && typeof clock === \"object\" ? SERVER_CLOCKS.get(clock) : undefined;",
    "const read = typeof clock === \"function\" ? clock : clock !== null && typeof clock === \"object\" ? SERVER_CLOCKS.get(clock) : undefined;"]));

test("MUTANT S9 drift: a missing selector is not checked (only the fingerprint)", () => killed(
  checkUiDriftStops,
  ["if (missing.length) return stopRun(\"ui_selector_missing\",", "if (false) return stopRun(\"ui_selector_missing\","]));

test("MUTANT S10 drift: the driver's own fingerprint is trusted", () => killed(
  checkUiDriftStops,
  ["const observation = { ...pageObservation, ui_contract_digest };",
    "const observation = { ui_contract_digest, ...pageObservation };"],
  ["if (Object.hasOwn(snapshot.page, \"ui_contract_digest\")) fail(", "if (false) fail("]));

test("MUTANT S11 drift: an unparseable row is filed as a policy conflict", () => killed(
  checkUiDriftStops,
  ["  return \"row_shape_drift\";\n}", "  return \"field_value_observed\";\n}"]));

test("MUTANT S12 drift: the fingerprint ignores the layout", () => killed(
  checkUiDriftStops,
  ["return digest({ kind: \"rw02-read-ui-fingerprint.v1\", layout_ref, selectors });",
    "return digest({ kind: \"rw02-read-ui-fingerprint.v1\", layout_ref: \"synthetic-list-view.v1\", selectors });"]));

test("MUTANT S13 account: the expected seat is taken from the page itself", () => killed(
  checkUnexpectedAccountStops,
  ["expected_account_ref: binding.org.account_ref, expected_ui_contract_digest",
    "expected_account_ref: pageObservation.signed_in_account_ref, expected_ui_contract_digest"]));

test("MUTANT S14 recipient: recipients shown on the page are dropped before the ladder", () => killed(
  checkUnexpectedAccountStops,
  ["const observation = { ...pageObservation, ui_contract_digest };",
    "const observation = { ...pageObservation, ui_contract_digest, recipients: null };"]));

test("MUTANT S15 recipient: a loop's owner is not checked", () => killed(
  checkPlanRecipientGuard,
  ["if (!expected || item.args.kind !== expected.kind || item.args.owner !== expected.owner)",
    "if (!expected || item.args.kind !== expected.kind)"]));

test("MUTANT S16 scope: an absence finding may name any deal", () => killed(
  checkPlanRecipientGuard,
  ["|| !dealIds.has(item.args.subject))", ")"]));

test("MUTANT S17 scope: any verb passes the plan guard", () => killed(
  checkPlanRecipientGuard,
  ["    } else return \"finding_outside_scope\";\n  }\n  return null;", "    }\n  }\n  return null;"]));

test("MUTANT S18 policy: page-reported policy conflicts are dropped", () => killed(
  checkPolicyConflictStops,
  ["const observation = { ...pageObservation, ui_contract_digest };",
    "const observation = { ...pageObservation, ui_contract_digest, policy_conflicts: [] };"]));

test("MUTANT S19 policy: a row carrying a field value is treated as drift, not a policy conflict", () => killed(
  checkPolicyConflictStops,
  ["  if (error?.code === \"unknown_field\") return \"field_value_observed\";\n", ""]));

test("MUTANT S20 consent: a revoked record still allows the run", () => killed(
  checkConsentRecord,
  ["  if (revoked !== false) return refuse(\"dell_consent_revoked\");\n", ""]));

test("MUTANT S21 consent: any sponsor's record counts", () => killed(
  checkConsentRecord,
  ["if (!V5_RW02_CONSENT_SPONSORS.includes(record.sponsoring_human_slug))",
    "if (false)"]));

test("MUTANT S22 consent: a record without the partner's words counts", () => killed(
  checkConsentRecord,
  ["  if (record.human_quote_present !== true) return refuse(\"dell_consent_quote_absent\");\n", ""]));

test("MUTANT S23 consent: any decision id counts, not the pinned one", () => killed(
  checkConsentRecord,
  ["if (!plain(record) || record.decision_id !== V5_RW02_DELL_CONSENT_DECISION_ID)", "if (!plain(record))"]));

test("MUTANT S24 consent: a consent flag on the binding is not refused by name", () => killed(
  checkConsentRecord,
  ["if (plain(raw) && Object.hasOwn(raw, \"dell_consent\"))", "if (false)"]));

test("MUTANT S25 partial write: each finding is written as it is planned", () => killed(
  checkNoPartialWrites,
  ["    const plan = [];\n", "    const plan = []; const push = plan.push.bind(plan);\n    plan.push = item => { recorder.record(item.verb, item.args); return push(item); };\n"]));

test("MUTANT S26 episodes: a closed loop replays instead of filing a new action", () => killed(
  checkLoopEpisodes,
  ["if (status === \"open\") return { key: episodeKeyFor(base, latest)", "return { key: episodeKeyFor(base, latest)"]));

test("MUTANT S27 episodes: episode 1 moves off the base key (old loops would refile)", () => killed(
  checkLoopEpisodes,
  ["return episode === 1 ? base : `${base}:e${episode}`;", "return `${base}:e${episode}`;"]));

test("MUTANT S28 episodes: an unreadable loop status is treated as open", () => killed(
  checkLoopEpisodes,
  ["  if (typeof status !== \"string\") return { stop: \"loop_episode_unreadable\" };\n", ""]));

test("MUTANT S29 invoiced: the absence scope ignores the invoiced marker", () => killed(
  checkInvoicedScope,
  ["where invoiced_on is null and (outcome is null or outcome = 'won') order by id",
    "where outcome is null and closed_on is null order by id"]));

test("MUTANT S30 counter: an unexpected failure is not recorded as an unclean run", () => killed(
  checkFailureRecordsUnclean,
  ["    if (outcomeRecorded || error?.code === \"run_outcome_unrecorded\") throw error;\n", "    throw error;\n"]));

test("MUTANT S31 run ref: the run reference is fixed, so two runs share one outcome key", () => killed(
  checkRepeatRunArgsIdentical,
  ["  const run_ref = randomUUID().replace(/-/g, \"\").slice(0, 24);",
    "  const run_ref = \"0\".repeat(24);"]));

test("MUTANT S32 consent: any object with read() is accepted as the consent source", () => killed(
  checkSourcesMustBeServerMade,
  ["  if (!CONSENT_SOURCES.has(consentSource)) return refused(",
    "  if (!consentSource || typeof consentSource.read !== \"function\") return refused("]));

test("MUTANT S33 episodes: any object with loopEpisodes() is accepted as the episode source", () => killed(
  checkSourcesMustBeServerMade,
  ["  if (!EPISODE_SOURCES.has(findingState)) fail(",
    "  if (!findingState || typeof findingState.loopEpisodes !== \"function\") fail("]));

test("MUTANT S34 interruption: a partial write is thrown untyped instead of answered with its count", () => killed(
  checkWriteInterruptedTyped,
  ["        return interrupted({ run_ref,", "        throw interrupted({ run_ref,"]));

test("MUTANT S35 unrecorded: a failure to record an unclean outcome is swallowed", () => killed(
  checkUnrecordedOutcomeSurfaces,
  ["    try { await recordOutcome(\"failed\"); }\n    catch (recordError) {\n      throw new",
    "    try { await recordOutcome(\"failed\"); }\n    catch (recordError) {\n      if (false) throw new"]));
