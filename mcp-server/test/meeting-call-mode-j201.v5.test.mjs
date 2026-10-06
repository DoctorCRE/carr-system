// V5-J201 — non-recording Meeting and Call Mode, proved case by case.
//
// Everything here is synthetic and nothing reaches a database, a provider, a
// network, a clock or the filesystem. The module under test is pure, so this
// suite can prove the properties that actually matter about a meeting detector:
//
//   * that CALENDAR TIMING ALONE IS NEVER A MEETING — Joe's own condition on
//     Q088.D1 — and that an `unknown` signal never quietly becomes one,
//   * that a duplicate Joe/Dell pair reconciles to ONE meeting, that two
//     meetings overlapping in time stay TWO, and that a recycled native id
//     refuses instead of merging,
//   * that NO LEDGER A CALLER CAN SUPPLY reaches a prompt or a suppression,
//     because the durable owner that would make "prompt once" true does not
//     exist here and a label in a request is not that owner,
//   * that NO PATH ANYWHERE IN THE MODULE produces a result whose `recording` is
//     anything but "denied" — checked by sweeping every result the suite builds
//     rather than by asserting it once,
//   * that activation needs a verified partner AND an explicit one-tap, with the
//     three silent intents refused by name,
//   * that a model may not speak before activation and may not reach past its
//     seam once it can,
//   * that an observed meeting becomes an artifact F01 ITSELF admits — F01 is
//     the independent oracle for the shape, so the mapping is not graded by the
//     code that produced it,
//   * that the missing upstream adapter refuses HONESTLY rather than being
//     stubbed, which is the case that was live when this slice was written,
//   * and that full v5 is unreachable from every input.
//
// NO FIXTURE NAMES A REAL THING: every account, device, digest and native id
// below is unmistakably test data on an .invalid domain.

import test from "node:test";
import assert from "node:assert/strict";

import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_NO_EFFECTS,
  V5_PROHIBITED_DATA_CLASSES,
} from "../src/global-boundaries.v5.js";
import {
  V5_F01_EVIDENCE_CLASSES,
  V5_F01_HOMES,
  V5_F01_TAINT_CLASSES,
  admitCorporateArtifact,
} from "../src/record-source-authority.v5.js";
import {
  V5J201Error,
  V5_J201_ADAPTER_DEPLOYMENT_STATES,
  V5_J201_ADAPTER_KIND,
  V5_J201_AUDIO_SESSION_STATES,
  V5_J201_AUTHORITATIVE_HOME,
  V5_J201_CALENDAR_SOURCE_SEAM,
  V5_J201_CANDIDATE_SCHEMA_VERSION,
  V5_J201_CONSUMER_GATES,
  V5_J201_CORROBORATING_SIGNAL_KINDS,
  V5_J201_DEVICE_STATES,
  V5_J201_DISMISSAL_REOPEN_REQUIRES,
  V5_J201_EVIDENCE_CLASS,
  V5_J201_EXPLICIT_ACTIVATION_INTENT,
  V5_J201_KERNEL_PRODUCTION_OUTCOME_STEP,
  V5_J201_LEDGER_PROVENANCE_CLASSES,
  V5_J201_MIN_REQUIRED_CORROBORATING_SIGNALS,
  V5_J201_MODEL_SEAMS,
  V5_J201_MODEL_WIDENING_FRAGMENTS,
  V5_J201_MODE_STATES,
  V5_J201_NON_READING_DEPLOYMENT_STATES,
  V5_J201_OBSERVATION_DECISIONS,
  V5_J201_OBSERVATION_SCHEMA_VERSION,
  V5_J201_PLATFORMS,
  V5_J201_PRESENCE_STATES,
  V5_J201_PRODUCTION_OUTCOME_STEPS,
  V5_J201_PROMPT_DECISIONS,
  V5_J201_PROMPT_LEDGER_OWNER_SEAM,
  V5_J201_PROMPT_SCHEMA_VERSION,
  V5_J201_READ_CONTRACT_SCHEMA_VERSION,
  V5_J201_RECONCILIATION_DISPOSITIONS,
  V5_J201_RECORDING_FRAGMENTS,
  V5_J201_RECORDING_POLICY_SEAM,
  V5_J201_RECORDING_STATE,
  V5_J201_REFUSED_ACTIVATION_INTENTS,
  V5_J201_REQUIRED_ITEM_KIND,
  V5_J201_REQUIRED_READ_OPERATIONS,
  V5_J201_SETTLED_DECISIONS,
  V5_J201_SETTLED_DECISION_IDS,
  V5_J201_SESSION_SCHEMA_VERSION,
  V5_J201_SIGNAL_KINDS,
  V5_J201_SLICE_EVIDENCE_INPUTS,
  V5_J201_TAINT_CLASS,
  V5_J201_TRUSTED_LEDGER_PROVENANCE_CLASSES,
  V5_J201_UNWIRED_ONCE_ONLY_DECISIONS,
  activateMeetingMode,
  assertJ201DecisionBinding,
  bindMeetingSourceAdapter,
  evaluateActivationPrompt,
  evaluateMeetingObservation,
  evaluateModelProposal,
  meetingKey,
  meetingModeGaps,
  reconcileMeetingObservations,
  toMeetingRecordLinkCandidate,
  unwiredOnceOnlyPromptDecision,
  v5J201MeetingModeProjection,
  v5J201PolicyDigest,
  v5J201PolicyPreimage,
} from "../src/meeting-call-mode-j201.v5.js";

// ---------------------------------------------------------------------------
// The recording sweep. Every result any test builds is pushed here, and one
// test at the end asserts the invariant over the whole collection. Asserting it
// inside each test would prove it for the paths someone remembered; sweeping
// proves it for the paths they did not.
// ---------------------------------------------------------------------------

const EVERY_RESULT = [];
function keep(result) {
  EVERY_RESULT.push(result);
  return result;
}

const TENANT = ORGANIZATION_TENANT_ID;
const NOW = "2026-09-11T15:10:00Z";

const JOE = Object.freeze({ slug: "joe", human: true });
const DELL = Object.freeze({ slug: "dell", human: true });
const SPONSORED_AGENT = Object.freeze({ slug: "claude", human: false, sponsoring_human_slug: "joe" });
const UNSPONSORED_AGENT = Object.freeze({ slug: "grok", human: false });

const TEAMS_IDENTITY = Object.freeze({
  source_system: "teams.invalid",
  native_id: "19:meeting_TESTONLYaaaa@thread.v2",
  native_id_epoch: "epoch-2026-09-11",
});
const ZOOM_IDENTITY = Object.freeze({
  source_system: "zoom.invalid",
  native_id: "test-only-8140000001",
  native_id_epoch: "epoch-2026-09-11",
});

function adapterDescriptor(overrides = {}) {
  return {
    adapter_kind: "test_only_calendar_presence_adapter",
    deployment_state: "deployed",
    evidence_class: V5_J201_EVIDENCE_CLASS,
    authoritative_home: V5_J201_AUTHORITATIVE_HOME,
    taint_class: V5_J201_TAINT_CLASS,
    observer_account: "joe@carr-test.invalid",
    platforms: ["teams", "zoom"],
    operations: {
      list_calendar_events: { mode: "read", item_kind: V5_J201_REQUIRED_ITEM_KIND },
      read_calendar_event_metadata: { mode: "read", item_kind: V5_J201_REQUIRED_ITEM_KIND },
      create_calendar_event: { mode: "write", item_kind: V5_J201_REQUIRED_ITEM_KIND },
    },
    ...overrides,
  };
}

function boundAdapter(overrides = {}) {
  const result = bindMeetingSourceAdapter({ tenant: TENANT, adapter: adapterDescriptor(overrides) });
  keep(result);
  return result;
}

function calendarSignal(overrides = {}) {
  return {
    platform: "teams",
    native_identity: { ...TEAMS_IDENTITY },
    starts_at: "2026-09-11T15:00:00Z",
    ends_at: "2026-09-11T16:00:00Z",
    organizer_account: "organizer@carr-test.invalid",
    observer_account: "joe@carr-test.invalid",
    declared_data_classes: ["tenant_business_contact"],
    ...overrides,
  };
}

function presenceSignal(overrides = {}) {
  return {
    signal_kind: "presence_session",
    state: "joined",
    observed_at: "2026-09-11T15:05:00Z",
    platform: "teams",
    ...overrides,
  };
}

const POLICY = Object.freeze({
  window_lead_seconds: 300,
  window_trail_seconds: 300,
  required_corroborating_signals: 1,
});

function observe(overrides = {}) {
  const request = {
    tenant: TENANT,
    now: NOW,
    adapter_binding: boundAdapter(),
    calendar_signal: calendarSignal(),
    corroborating_signals: [presenceSignal()],
    detection_policy: { ...POLICY },
    ...overrides,
  };
  return keep(evaluateMeetingObservation(request));
}

// THE STRONGEST CLAIM A CALLER CAN WRITE, pinned as a literal on purpose. This
// fixture is not a durable ledger and cannot become one by being labelled: the
// class below is the very string an earlier version of the module treated as
// authority, and the tests that follow prove it now buys exactly nothing.
const CLAIMED_PROVENANCE = Object.freeze({
  class: "authoritative_durable_store",
  owner: "test-only-prompt-ledger-that-nothing-can-vouch-for",
  read_at: "2026-09-11T15:09:00Z",
});

const EMPTY_LEDGER = Object.freeze({
  prompted_meeting_keys: [],
  dismissed_meeting_keys: [],
  active_meeting_keys: [],
  provenance: { ...CLAIMED_PROVENANCE },
});

function prompt(observation, ledger = EMPTY_LEDGER) {
  return keep(evaluateActivationPrompt({
    tenant: TENANT, now: NOW, observation, prompt_ledger: { ...ledger },
  }));
}

// THE PROMPT ANSWER NO CALLER CAN GET TODAY, built by hand HERE IN THE TEST and
// never by the module. evaluateActivationPrompt refuses every ledger it is
// handed (the matrix test below), so there is no product path to a shown prompt
// until the durable ledger owner exists — and no exported function may hand a
// caller one, which is why this shape is assembled in the suite rather than
// offered by the source. Activation's own rules still have to be provable, so
// these tests state their premise instead of pretending it is reachable.
function shownPrompt(observation = observe()) {
  return Object.freeze({
    schema_version: V5_J201_PROMPT_SCHEMA_VERSION,
    meeting_key: observation.meeting_key,
    records_audio: false,
    recording: V5_J201_RECORDING_STATE,
    recording_permitted: false,
    detection_is_not_consent: true,
    recording_policy_seam: V5_J201_RECORDING_POLICY_SEAM,
    decision: "prompt_once",
    reason_id: "first_prompt_for_this_meeting",
    prompt_shown: true,
    activation_required_from_human: true,
    accepted_activation_intent: V5_J201_EXPLICIT_ACTIVATION_INTENT,
    effects: V5_NO_EFFECTS,
  });
}

function activate(promptResult, overrides = {}) {
  return keep(activateMeetingMode({
    tenant: TENANT, now: NOW, actor: JOE, prompt: promptResult,
    activation_intent: V5_J201_EXPLICIT_ACTIVATION_INTENT,
    ...overrides,
  }));
}

function reconcileObservation(overrides = {}) {
  return {
    observer_partner: "joe",
    platform: "teams",
    native_identity: { ...TEAMS_IDENTITY },
    calendar_uid: "uid-test-only-0001",
    observed_at: "2026-09-11T15:05:00Z",
    ...overrides,
  };
}

function throwsWithCode(fn, code) {
  assert.throws(fn, error => {
    assert.ok(error instanceof V5J201Error, `expected V5J201Error, got ${error?.name}`);
    assert.equal(error.code, code);
    return true;
  });
}

// ---------------------------------------------------------------------------
// The settled decisions this slice claims to encode.
// ---------------------------------------------------------------------------

test("the four settled decision ids are exactly the slice's four", () => {
  assert.deepEqual([...V5_J201_SETTLED_DECISION_IDS], ["Q059.D3", "Q074.D1", "Q088.D1", "Q123.D2"]);
});

test("a matching decision binding is accepted", () => {
  const binding = assertJ201DecisionBinding({
    decisions: Object.fromEntries(V5_J201_SETTLED_DECISION_IDS.map(
      id => [id, { ...V5_J201_SETTLED_DECISIONS[id] }])),
  });
  assert.deepEqual([...binding.decisions_bound], [...V5_J201_SETTLED_DECISION_IDS]);
});

test("a decision binding missing one decision is drift", () => {
  const decisions = Object.fromEntries(V5_J201_SETTLED_DECISION_IDS
    .filter(id => id !== "Q074.D1")
    .map(id => [id, { ...V5_J201_SETTLED_DECISIONS[id] }]));
  throwsWithCode(() => assertJ201DecisionBinding({ decisions }), "decision_subset_drift");
});

test("a decision binding carrying an unregistered decision is drift", () => {
  const decisions = Object.fromEntries(V5_J201_SETTLED_DECISION_IDS.map(
    id => [id, { ...V5_J201_SETTLED_DECISIONS[id] }]));
  decisions["Q999.D9"] = { settled_requirement: "invented", source_evidence_digest: "0".repeat(64) };
  throwsWithCode(() => assertJ201DecisionBinding({ decisions }), "decision_subset_drift");
});

test("a decision binding whose text was edited is drift", () => {
  const decisions = Object.fromEntries(V5_J201_SETTLED_DECISION_IDS.map(
    id => [id, { ...V5_J201_SETTLED_DECISIONS[id] }]));
  decisions["Q088.D1"] = {
    ...decisions["Q088.D1"],
    settled_requirement: decisions["Q088.D1"].settled_requirement.replace("never", "sometimes"),
  };
  throwsWithCode(() => assertJ201DecisionBinding({ decisions }), "decision_text_drift");
});

test("a decision binding whose evidence digest was edited is drift", () => {
  const decisions = Object.fromEntries(V5_J201_SETTLED_DECISION_IDS.map(
    id => [id, { ...V5_J201_SETTLED_DECISIONS[id] }]));
  decisions["Q059.D3"] = { ...decisions["Q059.D3"], source_evidence_digest: "f".repeat(64) };
  throwsWithCode(() => assertJ201DecisionBinding({ decisions }), "decision_evidence_drift");
});

// ---------------------------------------------------------------------------
// The upstream seam: the adapter that may or may not be there.
// ---------------------------------------------------------------------------

test("a deployed adapter offering both required reads binds", () => {
  const binding = boundAdapter();
  assert.equal(binding.decision, "bound");
  assert.equal(binding.bound, true);
  assert.deepEqual([...binding.bound_read_operations], [...V5_J201_REQUIRED_READ_OPERATIONS]);
  // The write half was seen and left alone, not overlooked.
  assert.deepEqual([...binding.unbound_write_operations], ["create_calendar_event"]);
});

test("every non-reading deployment state refuses honestly rather than binding", () => {
  for (const state of V5_J201_NON_READING_DEPLOYMENT_STATES) {
    const binding = boundAdapter({ deployment_state: state });
    assert.equal(binding.bound, false, state);
    assert.equal(binding.decision, "refuse_adapter_unavailable", state);
    assert.equal(binding.reason_id, "adapter_unavailable", state);
    assert.equal(binding.calendar_source_seam, V5_J201_CALENDAR_SOURCE_SEAM, state);
  }
  // "unknown" is a registered state and still does not bind: a deployment
  // nobody has observed is not a deployment.
  assert.ok(V5_J201_ADAPTER_DEPLOYMENT_STATES.includes("unknown"));
  assert.ok(V5_J201_NON_READING_DEPLOYMENT_STATES.includes("unknown"));
});

test("an adapter missing a required read operation refuses and names the gap", () => {
  const operations = adapterDescriptor().operations;
  delete operations.read_calendar_event_metadata;
  const binding = boundAdapter({ operations });
  assert.equal(binding.decision, "refuse_read_contract_unsatisfied");
  assert.deepEqual([...binding.missing_read_operations], ["read_calendar_event_metadata"]);
});

test("an adapter offering a required read in write mode does not satisfy the contract", () => {
  const operations = adapterDescriptor().operations;
  operations.list_calendar_events = { mode: "write", item_kind: V5_J201_REQUIRED_ITEM_KIND };
  const binding = boundAdapter({ operations });
  assert.equal(binding.decision, "refuse_read_contract_unsatisfied");
  assert.deepEqual([...binding.missing_read_operations], ["list_calendar_events"]);
});

test("an adapter offering a required read over the wrong item kind does not satisfy it", () => {
  const operations = adapterDescriptor().operations;
  operations.list_calendar_events = { mode: "read", item_kind: "mail_message" };
  const binding = boundAdapter({ operations });
  assert.equal(binding.decision, "refuse_read_contract_unsatisfied");
  assert.deepEqual([...binding.missing_read_operations], ["list_calendar_events"]);
});

test("an adapter that lowers taint cannot be read at all", () => {
  throwsWithCode(
    () => bindMeetingSourceAdapter({
      tenant: TENANT,
      adapter: adapterDescriptor({ taint_class: "first_party_record_layer" }),
    }),
    "taint_lowering_refused");
});

test("an adapter that moves the home or the evidence class cannot be read at all", () => {
  throwsWithCode(
    () => bindMeetingSourceAdapter({
      tenant: TENANT, adapter: adapterDescriptor({ authoritative_home: "salesforce" }),
    }),
    "authoritative_home_mismatch");
  throwsWithCode(
    () => bindMeetingSourceAdapter({
      tenant: TENANT, adapter: adapterDescriptor({ evidence_class: "corporate_record_export" }),
    }),
    "evidence_class_mismatch");
});

test("an adapter field naming audio is refused before any value is read", () => {
  throwsWithCode(
    () => bindMeetingSourceAdapter({
      tenant: TENANT,
      adapter: { ...adapterDescriptor(), audio_capture_supported: false },
    }),
    "recording_field_refused");
});

test("a binding is refused for a tenant other than the one server-held tenant", () => {
  throwsWithCode(
    () => bindMeetingSourceAdapter({ tenant: "some-other-tenant", adapter: adapterDescriptor() }),
    "tenant_mismatch");
});

test("the three upstream vocabulary values this slice emits are still registered by F01", () => {
  assert.ok(V5_F01_EVIDENCE_CLASSES.includes(V5_J201_EVIDENCE_CLASS));
  assert.ok(V5_F01_HOMES.includes(V5_J201_AUTHORITATIVE_HOME));
  assert.ok(V5_F01_TAINT_CLASSES.includes(V5_J201_TAINT_CLASS));
});

// ---------------------------------------------------------------------------
// Detection. Calendar timing is necessary and never sufficient.
// ---------------------------------------------------------------------------

test("calendar timing plus one affirmative presence signal observes a meeting", () => {
  const result = observe();
  assert.equal(result.decision, "observe_meeting");
  assert.equal(result.reason_id, "calendar_window_and_corroboration_agree");
  assert.deepEqual([...result.corroborating_signal_kinds], ["presence_session"]);
  assert.equal(result.prompt_permitted, true);
  assert.equal(result.activation_required_from_human, true);
});

test("calendar timing ALONE is never a meeting observation", () => {
  const result = observe({ corroborating_signals: [] });
  assert.equal(result.decision, "withhold_insufficient_corroboration");
  assert.equal(result.reason_id, "calendar_timing_alone_is_not_a_meeting");
  assert.equal(result.corroborating_signal_count, 0);
});

test("an unknown state never corroborates, on any of the three signal kinds", () => {
  const cases = [
    { signal_kind: "presence_session", state: "unknown" },
    { signal_kind: "audio_session", state: "unknown" },
    { signal_kind: "device_state", state: "unknown" },
  ];
  for (const override of cases) {
    const result = observe({ corroborating_signals: [presenceSignal(override)] });
    assert.equal(result.decision, "withhold_insufficient_corroboration", override.signal_kind);
    assert.equal(result.corroborating_signal_count, 0, override.signal_kind);
    assert.equal(result.rejected_signals[0].reason_id, "state_not_affirmative", override.signal_kind);
  }
  // Each vocabulary genuinely registers `unknown`, so these are live values a
  // caller may report rather than values the enum check would have caught.
  assert.ok(V5_J201_PRESENCE_STATES.includes("unknown"));
  assert.ok(V5_J201_AUDIO_SESSION_STATES.includes("unknown"));
  assert.ok(V5_J201_DEVICE_STATES.includes("unknown"));
});

test("a negative state never corroborates either", () => {
  const result = observe({
    corroborating_signals: [presenceSignal({ state: "not_joined" })],
  });
  assert.equal(result.decision, "withhold_insufficient_corroboration");
  assert.equal(result.rejected_signals[0].reason_id, "state_not_affirmative");
});

test("each of the three corroborating kinds can carry the observation on its own", () => {
  const affirmative = {
    presence_session: "joined",
    audio_session: "conference_process_holds_input_device",
    device_state: "unlocked_and_attended",
  };
  for (const kind of V5_J201_CORROBORATING_SIGNAL_KINDS) {
    const result = observe({
      corroborating_signals: [presenceSignal({ signal_kind: kind, state: affirmative[kind] })],
    });
    assert.equal(result.decision, "observe_meeting", kind);
    assert.deepEqual([...result.corroborating_signal_kinds], [kind]);
  }
});

test("a policy demanding two signals withholds on one and observes on two", () => {
  const policy = { ...POLICY, required_corroborating_signals: 2 };
  const one = observe({ detection_policy: policy });
  assert.equal(one.decision, "withhold_insufficient_corroboration");
  assert.equal(one.reason_id, "corroborating_signal_count_below_policy");

  const two = observe({
    detection_policy: policy,
    corroborating_signals: [
      presenceSignal(),
      presenceSignal({ signal_kind: "audio_session", state: "conference_process_holds_input_device" }),
    ],
  });
  assert.equal(two.decision, "observe_meeting");
  assert.equal(two.corroborating_signal_count, 2);
});

test("two reports of the SAME signal kind do not count twice", () => {
  const result = observe({
    detection_policy: { ...POLICY, required_corroborating_signals: 2 },
    corroborating_signals: [presenceSignal(), presenceSignal({ observed_at: "2026-09-11T15:06:00Z" })],
  });
  assert.equal(result.decision, "withhold_insufficient_corroboration");
  assert.equal(result.corroborating_signal_count, 1);
});

test("the corroboration floor may be raised but never lowered to zero", () => {
  assert.equal(V5_J201_MIN_REQUIRED_CORROBORATING_SIGNALS, 1);
  throwsWithCode(
    () => evaluateMeetingObservation({
      tenant: TENANT, now: NOW, adapter_binding: boundAdapter(),
      calendar_signal: calendarSignal(), corroborating_signals: [presenceSignal()],
      detection_policy: { ...POLICY, required_corroborating_signals: 0 },
    }),
    "corroboration_floor_breached");
});

test("a detection policy is required and has no default", () => {
  const request = {
    tenant: TENANT, now: NOW, adapter_binding: boundAdapter(),
    calendar_signal: calendarSignal(), corroborating_signals: [presenceSignal()],
  };
  throwsWithCode(() => evaluateMeetingObservation(request), "missing_field");
});

test("before the window opens and after it closes, detection withholds", () => {
  const early = observe({ now: "2026-09-11T14:50:00Z" });
  assert.equal(early.decision, "withhold_outside_calendar_window");
  assert.equal(early.reason_id, "now_outside_detection_window");

  const late = observe({ now: "2026-09-11T16:10:00Z" });
  assert.equal(late.decision, "withhold_outside_calendar_window");

  // The lead and trail are real: one second inside each edge observes.
  const justInside = observe({
    now: "2026-09-11T14:55:01Z",
    corroborating_signals: [presenceSignal({ observed_at: "2026-09-11T14:55:01Z" })],
  });
  assert.equal(justInside.decision, "observe_meeting");
});

test("a signal observed outside the window, or in the future, does not corroborate", () => {
  const stale = observe({
    corroborating_signals: [presenceSignal({ observed_at: "2026-09-11T12:00:00Z" })],
  });
  assert.equal(stale.decision, "withhold_insufficient_corroboration");
  assert.equal(stale.rejected_signals[0].reason_id, "observed_outside_detection_window");

  const future = observe({
    corroborating_signals: [presenceSignal({ observed_at: "2026-09-11T15:30:00Z" })],
  });
  assert.equal(future.decision, "withhold_insufficient_corroboration");
  assert.equal(future.rejected_signals[0].reason_id, "observed_after_now");
});

test("a signal from the other platform does not corroborate this meeting", () => {
  const result = observe({
    corroborating_signals: [presenceSignal({ platform: "zoom" })],
  });
  assert.equal(result.decision, "withhold_insufficient_corroboration");
  assert.equal(result.rejected_signals[0].reason_id, "platform_mismatch");
});

test("an unbound adapter refuses honestly instead of being stubbed", () => {
  const result = observe({ adapter_binding: boundAdapter({ deployment_state: "not_deployed" }) });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "calendar_source_unavailable");
  assert.equal(result.calendar_source_seam, V5_J201_CALENDAR_SOURCE_SEAM);
  assert.equal(result.adapter_reason_id, "adapter_unavailable");
});

test("an observation outside the bound account or platform refuses", () => {
  const otherAccount = observe({
    calendar_signal: calendarSignal({ observer_account: "dell@carr-test.invalid" }),
  });
  assert.equal(otherAccount.decision, "refuse");
  assert.equal(otherAccount.reason_id, "observer_account_outside_binding");

  const otherPlatform = observe({
    adapter_binding: boundAdapter({ platforms: ["teams"] }),
    calendar_signal: calendarSignal({ platform: "zoom", native_identity: { ...ZOOM_IDENTITY } }),
    corroborating_signals: [presenceSignal({ platform: "zoom" })],
  });
  assert.equal(otherPlatform.decision, "refuse");
  assert.equal(otherPlatform.reason_id, "platform_outside_binding");
});

test("the privacy answer is S01's, carried through rather than recopied", () => {
  for (const cls of V5_PROHIBITED_DATA_CLASSES) {
    const result = observe({
      calendar_signal: calendarSignal({ declared_data_classes: [cls] }),
    });
    assert.equal(result.decision, "refuse", cls);
    assert.equal(result.reason_id, "privacy_boundary_refused", cls);
  }
});

test("an impossible instant is refused rather than normalized into a different day", () => {
  throwsWithCode(
    () => evaluateMeetingObservation({
      tenant: TENANT, now: NOW, adapter_binding: boundAdapter(),
      calendar_signal: calendarSignal({ starts_at: "2026-02-31T15:00:00Z" }),
      corroborating_signals: [presenceSignal()], detection_policy: { ...POLICY },
    }),
    "invalid_timestamp");
});

test("an end before its start is refused", () => {
  throwsWithCode(
    () => evaluateMeetingObservation({
      tenant: TENANT, now: NOW, adapter_binding: boundAdapter(),
      calendar_signal: calendarSignal({ ends_at: "2026-09-11T14:00:00Z" }),
      corroborating_signals: [presenceSignal()], detection_policy: { ...POLICY },
    }),
    "invalid_interval");
});

test("an unknown field anywhere in an observation request cannot be read", () => {
  throwsWithCode(
    () => evaluateMeetingObservation({
      tenant: TENANT, now: NOW, adapter_binding: boundAdapter(),
      calendar_signal: calendarSignal(), corroborating_signals: [presenceSignal()],
      detection_policy: { ...POLICY }, trusted: true,
    }),
    "unknown_field");
  throwsWithCode(
    () => evaluateMeetingObservation({
      tenant: TENANT, now: NOW, adapter_binding: boundAdapter(),
      calendar_signal: { ...calendarSignal(), subject: "a title" },
      corroborating_signals: [presenceSignal()], detection_policy: { ...POLICY },
    }),
    "unknown_field");
});

// ---------------------------------------------------------------------------
// checkable_done 1 — duplicate Joe/Dell observations reconcile to one meeting.
// ---------------------------------------------------------------------------

test("Joe and Dell observing the same Teams call reconcile to ONE meeting", () => {
  const result = keep(reconcileMeetingObservations({
    tenant: TENANT, now: NOW,
    observations: [
      reconcileObservation({ observer_partner: "joe" }),
      reconcileObservation({ observer_partner: "dell", observed_at: "2026-09-11T15:07:00Z" }),
    ],
  }));
  assert.equal(result.disposition, "single_meeting");
  assert.equal(result.meeting_count, 1);
  assert.equal(result.observation_count, 2);
  assert.equal(result.merged, true);
  assert.deepEqual([...result.meetings[0].observers], ["dell", "joe"]);
  assert.equal(result.meetings[0].reconciled_from_duplicate_observations, true);
});

test("two DIFFERENT meetings at the same instant stay two meetings", () => {
  // The trap this exists to catch: a reconciler keyed on time would merge these,
  // and the observers are deliberately the same pair as the duplicate case above
  // so the only difference between the two tests is the native identity.
  const result = keep(reconcileMeetingObservations({
    tenant: TENANT, now: NOW,
    observations: [
      reconcileObservation({ observer_partner: "joe" }),
      reconcileObservation({
        observer_partner: "dell",
        platform: "zoom",
        native_identity: { ...ZOOM_IDENTITY },
        calendar_uid: "uid-test-only-0002",
      }),
    ],
  }));
  assert.equal(result.disposition, "distinct_meetings");
  assert.equal(result.meeting_count, 2);
  assert.equal(result.merged, false);
  for (const meeting of result.meetings) {
    assert.equal(meeting.reconciled_from_duplicate_observations, false);
  }
});

test("two meetings on the SAME platform with different native ids stay two", () => {
  const result = keep(reconcileMeetingObservations({
    tenant: TENANT, now: NOW,
    observations: [
      reconcileObservation(),
      reconcileObservation({
        observer_partner: "dell",
        native_identity: { ...TEAMS_IDENTITY, native_id: "19:meeting_TESTONLYbbbb@thread.v2" },
        calendar_uid: "uid-test-only-0003",
      }),
    ],
  }));
  assert.equal(result.meeting_count, 2);
  assert.equal(result.merged, false);
});

test("one native id under two epochs refuses and merges nothing", () => {
  const result = keep(reconcileMeetingObservations({
    tenant: TENANT, now: NOW,
    observations: [
      reconcileObservation(),
      reconcileObservation({
        observer_partner: "dell",
        native_identity: { ...TEAMS_IDENTITY, native_id_epoch: "epoch-2026-09-18" },
      }),
    ],
  }));
  assert.equal(result.disposition, "refuse_ambiguous_identity");
  assert.equal(result.reason_id, "native_id_epoch_conflict");
  assert.equal(result.merged, false);
  assert.deepEqual([...result.meetings], []);
  assert.deepEqual([...result.conflicting_epochs], ["epoch-2026-09-11", "epoch-2026-09-18"]);
});

test("one meeting key claimed under two calendar uids refuses and merges nothing", () => {
  const result = keep(reconcileMeetingObservations({
    tenant: TENANT, now: NOW,
    observations: [
      reconcileObservation(),
      reconcileObservation({ observer_partner: "dell", calendar_uid: "uid-test-only-9999" }),
    ],
  }));
  assert.equal(result.disposition, "refuse_ambiguous_identity");
  assert.equal(result.reason_id, "calendar_uid_conflict_under_one_native_identity");
  assert.equal(result.merged, false);
  assert.deepEqual([...result.meetings], []);
});

test("three partners' worth of observations over two meetings reconcile to two", () => {
  const result = keep(reconcileMeetingObservations({
    tenant: TENANT, now: NOW,
    observations: [
      reconcileObservation({ observer_partner: "joe" }),
      reconcileObservation({ observer_partner: "dell" }),
      reconcileObservation({
        observer_partner: "joe", platform: "zoom",
        native_identity: { ...ZOOM_IDENTITY }, calendar_uid: "uid-test-only-0004",
      }),
    ],
  }));
  assert.equal(result.meeting_count, 2);
  assert.equal(result.observation_count, 3);
  assert.equal(result.merged, true);
});

test("a non-partner observer cannot be reconciled", () => {
  throwsWithCode(
    () => reconcileMeetingObservations({
      tenant: TENANT, now: NOW,
      observations: [reconcileObservation({ observer_partner: "claude" })],
    }),
    "unknown_observer_partner");
});

test("an observation stamped after now is refused", () => {
  throwsWithCode(
    () => reconcileMeetingObservations({
      tenant: TENANT, now: NOW,
      observations: [reconcileObservation({ observed_at: "2026-09-11T18:00:00Z" })],
    }),
    "observed_after_now");
});

test("reconciliation needs at least one observation", () => {
  throwsWithCode(
    () => reconcileMeetingObservations({ tenant: TENANT, now: NOW, observations: [] }),
    "invalid_shape");
});

test("the meeting key cannot be forged by an identifier carrying the separator", () => {
  // The separator is NUL, which assertSafeText refuses outright, so no validated
  // identifier can contain it and no two triples can fold to one key.
  throwsWithCode(
    () => meetingKey({
      platform: "teams",
      native_identity: { ...TEAMS_IDENTITY, native_id: `a\u0000b` },
    }),
    "unsafe_unicode");
});

test("the exported meeting key matches the one detection computes", () => {
  const computed = meetingKey({ platform: "teams", native_identity: { ...TEAMS_IDENTITY } });
  assert.equal(observe().meeting_key, computed);
});

// ---------------------------------------------------------------------------
// checkable_done 2 — detection prompts once and never records.
// ---------------------------------------------------------------------------

test("NO LEDGER A CALLER CAN SUPPLY reaches a prompt or a suppression", () => {
  // THE STANDING RULE AS A MATRIX RATHER THAN A SENTENCE. Every registered
  // provenance class, crossed with owner strings a caller might write —
  // including one that simply repeats the accepted class back — crossed with
  // every ledger state that used to decide something. The privileged outcomes
  // are prompt_once and the three suppressions; none is reachable, because the
  // fact that would justify one (a durable owner that can vouch for this
  // ledger) does not exist in this repository to be consulted.
  const observation = observe();
  const key = observation.meeting_key;
  const owners = [
    "test-only-prompt-ledger-that-nothing-can-vouch-for",
    "authoritative_durable_store",
    "carr-record-layer",
    "joe",
    "x",
  ];
  const states = [
    {},
    { prompted_meeting_keys: [key] },
    { dismissed_meeting_keys: [key] },
    { active_meeting_keys: [key] },
    {
      prompted_meeting_keys: [key], dismissed_meeting_keys: [key],
      active_meeting_keys: [key],
    },
  ];
  let cases = 0;
  for (const cls of V5_J201_LEDGER_PROVENANCE_CLASSES) {
    for (const owner of owners) {
      for (const state of states) {
        const where = `${cls} / ${owner} / ${JSON.stringify(state)}`;
        const result = prompt(observation, {
          ...EMPTY_LEDGER, ...state,
          provenance: { ...CLAIMED_PROVENANCE, class: cls, owner },
        });
        assert.equal(result.decision, "refuse_unproven_prompt_ledger", where);
        assert.equal(result.prompt_shown, false, where);
        assert.ok(!V5_J201_UNWIRED_ONCE_ONLY_DECISIONS.includes(result.decision), where);
        assert.equal(result.reason_id,
          "no_durable_prompt_ledger_owner_exists_to_prove_this_ledger", where);
        // What the caller claimed is RECORDED, so a transcript shows it...
        assert.equal(result.claimed_ledger_provenance_class, cls, where);
        // ...and credited with nothing.
        assert.deepEqual(result.trusted_ledger_provenance_classes, [], where);
        assert.equal(result.prompt_ledger_owner_seam, V5_J201_PROMPT_LEDGER_OWNER_SEAM, where);
        cases += 1;
      }
    }
  }
  assert.equal(cases, V5_J201_LEDGER_PROVENANCE_CLASSES.length * owners.length * states.length);
  assert.ok(cases >= 40, `only ${cases} ledger shapes were tried`);
});

test("the set of provenance classes this module acts on is EMPTY", () => {
  assert.deepEqual([...V5_J201_TRUSTED_LEDGER_PROVENANCE_CLASSES], []);
  // The vocabulary still registers the class an earlier version believed, so
  // the claim can be recorded. Registering it is not trusting it.
  assert.ok(V5_J201_LEDGER_PROVENANCE_CLASSES.includes("authoritative_durable_store"));
  assert.ok(!V5_J201_TRUSTED_LEDGER_PROVENANCE_CLASSES.includes("authoritative_durable_store"));
});

test("the registered answer set of evaluateActivationPrompt holds only refusals", () => {
  assert.deepEqual([...V5_J201_PROMPT_DECISIONS].sort(),
    ["refuse_unproven_prompt_ledger", "suppress_not_observed"]);
  for (const decision of V5_J201_PROMPT_DECISIONS) {
    assert.ok(!V5_J201_UNWIRED_ONCE_ONLY_DECISIONS.includes(decision), decision);
  }
});

test("this kernel raises no prompt at all, and the gaps projection says so", () => {
  // THE HONEST STATEMENT, and the reason the checkable_done item is partial:
  // once-only is a property of whoever owns the ledger durably. That owner does
  // not exist, so this slice refuses rather than prompting on a caller's word.
  const observation = observe();
  assert.equal(prompt(observation).decision, "refuse_unproven_prompt_ledger");
  assert.equal(prompt(observation).prompt_shown, false);

  const gaps = meetingModeGaps();
  assert.equal(gaps.once_only_prompt_enforced_here, false);
  assert.equal(gaps.once_only_prompt_reason_id,
    "the_prompt_ledger_is_owned_by_a_durable_store_this_slice_does_not_contain");
  assert.equal(gaps.prompt_reachable_here, false);
  assert.equal(gaps.prompt_unreachable_reason_id,
    "no_durable_prompt_ledger_owner_exists_to_prove_this_ledger");
  assert.deepEqual(gaps.trusted_prompt_ledger_provenance_classes, []);
  assert.equal(gaps.once_only_rule_written_down_at, "unwiredOnceOnlyPromptDecision");
  assert.equal(gaps.seams.prompt_ledger_owner, V5_J201_PROMPT_LEDGER_OWNER_SEAM);
  assert.ok(gaps.not_built_here.some(line => /durable.*prompt ledger|prompt ledger.*durable/.test(line)),
    JSON.stringify(gaps.not_built_here));
});

// ---------------------------------------------------------------------------
// The once-only rule itself: written down, unit tested against fixture ledgers,
// and NOT WIRED. It is a predicate returning a NAME — it decides nothing in the
// product until a durable ledger owner calls it.
// ---------------------------------------------------------------------------

function ledgerView(meeting_key, overrides = {}) {
  return {
    meeting_key,
    prompted_meeting_keys: [], dismissed_meeting_keys: [], active_meeting_keys: [],
    ...overrides,
  };
}

test("the unwired once-only rule answers every fixture ledger correctly", () => {
  const key = observe().meeting_key;
  const other = meetingKey({ platform: "zoom", native_identity: { ...ZOOM_IDENTITY } });
  const reached = new Set();
  const cases = [
    [ledgerView(key), "prompt_once"],
    [ledgerView(key, { prompted_meeting_keys: [key] }), "suppress_already_prompted"],
    [ledgerView(key, { dismissed_meeting_keys: [key] }), "suppress_dismissed"],
    [ledgerView(key, { active_meeting_keys: [key] }), "suppress_already_active"],
    // Precedence, stated rather than left to the reading order: a running
    // session beats a dismissal, and a dismissal beats an earlier prompt.
    [ledgerView(key, { dismissed_meeting_keys: [key], active_meeting_keys: [key] }),
      "suppress_already_active"],
    [ledgerView(key, { prompted_meeting_keys: [key], dismissed_meeting_keys: [key] }),
      "suppress_dismissed"],
    // A DIFFERENT meeting's entry decides nothing about this one.
    [ledgerView(key, { prompted_meeting_keys: [other], dismissed_meeting_keys: [other] }),
      "prompt_once"],
  ];
  for (const [view, expected] of cases) {
    const answer = unwiredOnceOnlyPromptDecision(view);
    assert.equal(answer, expected, JSON.stringify(view));
    reached.add(answer);
  }
  // Every registered name is reached by a fixture, so none of them is a name
  // the rule cannot actually produce.
  assert.deepEqual([...reached].sort(), [...V5_J201_UNWIRED_ONCE_ONLY_DECISIONS].sort());
  // The one way back from a dismissal is a person, and it is still said.
  assert.equal(V5_J201_DISMISSAL_REOPEN_REQUIRES, "manual_human_reopen");
  assert.equal(meetingModeGaps().dismissal_reopen_requires, V5_J201_DISMISSAL_REOPEN_REQUIRES);
});

test("the unwired rule returns a NAME that cannot be mistaken for a prompt", () => {
  const observation = observe();
  const answer = unwiredOnceOnlyPromptDecision(ledgerView(observation.meeting_key));
  assert.equal(typeof answer, "string");
  assert.equal(answer, "prompt_once");
  // It is not a result: no schema version, so activation cannot be driven from
  // it, and nothing this module exports will turn it into one.
  throwsWithCode(
    () => activateMeetingMode({
      tenant: TENANT, now: NOW, actor: JOE,
      prompt: { decision: answer, prompt_shown: true, meeting_key: observation.meeting_key },
      activation_intent: V5_J201_EXPLICIT_ACTIVATION_INTENT,
    }),
    "invalid_prompt");
});

test("the unwired rule reads a closed ledger view and nothing else", () => {
  const key = observe().meeting_key;
  throwsWithCode(() => unwiredOnceOnlyPromptDecision({ ...ledgerView(key), also: 1 }), "unknown_field");
  throwsWithCode(() => unwiredOnceOnlyPromptDecision({ meeting_key: key }), "missing_field");
  throwsWithCode(
    () => unwiredOnceOnlyPromptDecision({ ...ledgerView(key), transcript_ref: "x" }),
    "recording_field_refused");
});

test("a ledger carrying no provenance cannot reach a prompt decision", () => {
  throwsWithCode(
    () => evaluateActivationPrompt({
      tenant: TENANT, now: NOW, observation: observe(),
      prompt_ledger: {
        prompted_meeting_keys: [], dismissed_meeting_keys: [], active_meeting_keys: [],
      },
    }),
    "missing_field");
});

test("a provenance missing any one of its fields cannot reach a prompt decision", () => {
  // The whole provenance object being absent is one case; a half-filled one is
  // the case that would otherwise slip through as "close enough".
  for (const field of ["class", "owner", "read_at"]) {
    const provenance = { ...CLAIMED_PROVENANCE };
    delete provenance[field];
    throwsWithCode(() => prompt(observe(), { ...EMPTY_LEDGER, provenance }), "missing_field");
  }
});

test("an unregistered ledger provenance class is refused by name", () => {
  throwsWithCode(
    () => prompt(observe(), {
      ...EMPTY_LEDGER, provenance: { ...CLAIMED_PROVENANCE, class: "trust_me" },
    }),
    "unknown_ledger_provenance_class");
});

test("a ledger read after now is refused", () => {
  throwsWithCode(
    () => prompt(observe(), {
      ...EMPTY_LEDGER,
      provenance: { ...CLAIMED_PROVENANCE, read_at: "2026-09-11T15:11:00Z" },
    }),
    "ledger_read_after_now");
});

test("a dismissal, a running session and a prior prompt all refuse alike here", () => {
  // These three states used to be suppressions, and each of them was a decision
  // taken on a caller's word. They are now the unwired rule's business; what
  // this kernel does with any of them is refuse, identically.
  const observation = observe();
  for (const state of [
    { dismissed_meeting_keys: [observation.meeting_key] },
    { active_meeting_keys: [observation.meeting_key] },
    { prompted_meeting_keys: [observation.meeting_key] },
  ]) {
    const result = prompt(observation, { ...EMPTY_LEDGER, ...state });
    const where = JSON.stringify(state);
    assert.equal(result.decision, "refuse_unproven_prompt_ledger", where);
    assert.equal(result.prompt_shown, false, where);
    // The suppression fields those paths used to carry are gone with the paths.
    assert.equal("reopen_requires" in result, false, where);
  }
});

test("no withheld or refused observation can leak a prompt", () => {
  const withheld = [
    observe({ corroborating_signals: [] }),
    observe({ now: "2026-09-11T14:00:00Z" }),
    observe({ adapter_binding: boundAdapter({ deployment_state: "unknown" }) }),
    observe({ calendar_signal: calendarSignal({ declared_data_classes: ["phi"] }) }),
  ];
  for (const observation of withheld) {
    assert.notEqual(observation.decision, "observe_meeting");
    const result = prompt(observation);
    assert.equal(result.decision, "suppress_not_observed", observation.reason_id);
    assert.equal(result.prompt_shown, false, observation.reason_id);
  }
});

test("a prompt cannot be built from something that is not an observation result", () => {
  throwsWithCode(
    () => evaluateActivationPrompt({
      tenant: TENANT, now: NOW,
      observation: { decision: "observe_meeting", meeting_key: "forged" },
      prompt_ledger: { ...EMPTY_LEDGER },
    }),
    "invalid_observation");
});

test("EVERY registered prompt decision reports prompt_shown truthfully", () => {
  // Both registered decisions are refusals and NEITHER shows a prompt. The
  // earlier version of this test had one true in the list; that true is what
  // the durable ledger owner owes and this slice does not have.
  const shown = new Map();
  const observation = observe();
  shown.set("suppress_not_observed", prompt(observe({ corroborating_signals: [] })).prompt_shown);
  shown.set("refuse_unproven_prompt_ledger",
    prompt(observation, {
      ...EMPTY_LEDGER, provenance: { ...CLAIMED_PROVENANCE, class: "caller_supplied_unproven" },
    }).prompt_shown);
  assert.deepEqual([...shown.keys()].sort(), [...V5_J201_PROMPT_DECISIONS].sort());
  assert.deepEqual([...shown.values()], [false, false]);
  // Swept rather than spot-checked: nothing this suite ever built out of
  // evaluateActivationPrompt showed a prompt.
  assert.equal(
    EVERY_RESULT.filter(r => r.schema_version === V5_J201_PROMPT_SCHEMA_VERSION)
      .filter(r => r.prompt_shown !== false).length,
    0);
});

test("no field naming audio can reach any entry point", () => {
  const audioish = {
    audio_bytes: "x", recording_enabled: true, transcript_ref: "x", voiceprint: "x",
    capture_device: "x", mic_level: 1, pcm_frames: 1, waveform: [], stream_url: "x",
    speech_segments: [], diarization: [], listen_now: true,
  };
  for (const [field, value] of Object.entries(audioish)) {
    throwsWithCode(
      () => evaluateMeetingObservation({
        tenant: TENANT, now: NOW, adapter_binding: boundAdapter(),
        calendar_signal: calendarSignal(), corroborating_signals: [presenceSignal()],
        detection_policy: { ...POLICY }, [field]: value,
      }),
      "recording_field_refused");
  }
  // Every registered fragment is exercised by at least one of those field names,
  // so the list is proven rather than asserted.
  for (const fragment of V5_J201_RECORDING_FRAGMENTS) {
    assert.ok(
      Object.keys(audioish).some(name => name.toLowerCase().includes(fragment)),
      `no fixture field exercises the "${fragment}" fragment`);
  }
});

test("the metadata measurement fields do NOT trip the recording check", () => {
  // content_digest and byte_length name a measurement of bytes, never bytes, and
  // a fragment list that refused them would make the slice unable to link a
  // meeting into the records at all.
  const candidate = keep(toMeetingRecordLinkCandidate({
    tenant: TENANT, observation: observe(),
    evidence_ref: "evidence:test-only-0001",
    content_digest: `sha256:${"a".repeat(64)}`,
    byte_length: 512,
    observed_at: "2026-09-11T15:05:00Z",
  }));
  assert.equal(candidate.decision, "candidate");
});

// ---------------------------------------------------------------------------
// Activation. A person's tap, and nothing that resembles one.
// ---------------------------------------------------------------------------

test("an explicit one-tap by a verified partner opens a non-recording session", () => {
  const session = activate(shownPrompt());
  assert.equal(session.decision, "activate");
  assert.equal(session.mode_state, "active_non_recording");
  assert.equal(session.records_audio, false);
  assert.equal(session.audio_retained, false);
  assert.equal(session.resumable, true);
  assert.equal(session.consent_announcement_required, false);
});

test("either partner may activate", () => {
  for (const actor of [JOE, DELL]) {
    const session = activate(shownPrompt(), { actor });
    assert.equal(session.decision, "activate", actor.slug);
    assert.equal(session.actor_slug, actor.slug);
  }
});

test("each silent activation intent refuses BY NAME", () => {
  for (const intent of V5_J201_REFUSED_ACTIVATION_INTENTS) {
    const session = activate(shownPrompt(), { activation_intent: intent });
    assert.equal(session.decision, "refuse", intent);
    assert.equal(session.reason_id, "silent_activation_refused", intent);
    assert.equal(session.attempted_activation_intent, intent);
    assert.equal(session.mode_state, "off", intent);
  }
});

test("an unregistered activation intent refuses too", () => {
  const session = activate(shownPrompt(), { activation_intent: "seemed_like_a_meeting" });
  assert.equal(session.decision, "refuse");
  assert.equal(session.reason_id, "explicit_human_activation_required");
  assert.equal(session.mode_state, "off");
});

test("an agent seat cannot activate Meeting Mode, sponsored or not", () => {
  for (const actor of [SPONSORED_AGENT, UNSPONSORED_AGENT]) {
    const session = activate(shownPrompt(), { actor });
    assert.equal(session.decision, "refuse", actor.slug);
    assert.equal(session.reason_id, "activation_requires_a_verified_partner", actor.slug);
    assert.equal(session.mode_state, "off", actor.slug);
  }
});

test("a partner slug without the human flag is not a verified partner", () => {
  const session = activate(shownPrompt(), { actor: { slug: "joe", human: false } });
  assert.equal(session.decision, "refuse");
  assert.equal(session.reason_id, "activation_requires_a_verified_partner");
});

test("activation without a shown prompt refuses", () => {
  const observation = observe();
  // The ONLY prompt answer this product can actually produce today, fed
  // straight into activation: a refusal, which activates nothing. Until the
  // ledger owner exists there is no live path from a detection to a session.
  const refused = prompt(observation, {
    ...EMPTY_LEDGER, dismissed_meeting_keys: [observation.meeting_key],
  });
  assert.equal(refused.decision, "refuse_unproven_prompt_ledger");
  const session = activate(refused);
  assert.equal(session.decision, "refuse");
  assert.equal(session.reason_id, "activation_without_a_shown_prompt");
  assert.equal(session.mode_state, "off");
});

test("no ledger reaches an activated session through the real prompt path", () => {
  // The end-to-end statement of the same fact, swept over the matrix: whatever
  // a caller says about its ledger, the session it can open is none.
  const observation = observe();
  for (const cls of V5_J201_LEDGER_PROVENANCE_CLASSES) {
    const session = activate(prompt(observation, {
      ...EMPTY_LEDGER, provenance: { ...CLAIMED_PROVENANCE, class: cls },
    }));
    assert.equal(session.decision, "refuse", cls);
    assert.equal(session.mode_state, "off", cls);
  }
});

test("activation cannot be built from a forged prompt object", () => {
  throwsWithCode(
    () => activateMeetingMode({
      tenant: TENANT, now: NOW, actor: JOE,
      prompt: { decision: "prompt_once", prompt_shown: true, meeting_key: "forged" },
      activation_intent: V5_J201_EXPLICIT_ACTIVATION_INTENT,
    }),
    "invalid_prompt");
});

test("an activation intent that is an object rather than a string cannot be read", () => {
  throwsWithCode(
    () => activateMeetingMode({
      tenant: TENANT, now: NOW, actor: JOE, prompt: shownPrompt(),
      activation_intent: { intent: V5_J201_EXPLICIT_ACTIVATION_INTENT, also_record: true },
    }),
    "invalid_shape");
});

test("no mode state this module can reach is a recording state", () => {
  const reached = new Set([
    activate(shownPrompt()).mode_state,
    activate(shownPrompt(), { activation_intent: "silent_activation" }).mode_state,
  ]);
  for (const state of reached) assert.ok(V5_J201_MODE_STATES.includes(state));
  // The only registered state that mentions recording at all is the one that
  // NEGATES it. Anything else touching record/audio/capture would be a state a
  // session could sit in while capturing something.
  assert.deepEqual(
    V5_J201_MODE_STATES.filter(state => /record|audio|capture|listen/.test(state)),
    ["active_non_recording"]);
});

// ---------------------------------------------------------------------------
// The model boundary.
// ---------------------------------------------------------------------------

test("a model proposal inside a declared seam is accepted after activation", () => {
  const session = activate(shownPrompt());
  const result = keep(evaluateModelProposal({
    tenant: TENANT, session,
    proposal: {
      seam: "follow_up_kind", label: "schedule_next_meeting",
      confidence: 0.8, evidence_ref: "note:test-only-0001",
    },
  }));
  assert.equal(result.decision, "accept");
  assert.equal(result.requires_human_confirmation, true);
  assert.equal(result.is_fact, false);
  assert.equal(result.creates_follow_up, false);
  assert.equal(result.changes_mode_state, false);
});

test("a model may not speak before explicit activation", () => {
  const refusedSession = activate(shownPrompt(), { activation_intent: "automatic_on_detection" });
  const result = keep(evaluateModelProposal({
    tenant: TENANT, session: refusedSession,
    proposal: {
      seam: "note_summary_kind", label: "action_items",
      confidence: 0.9, evidence_ref: "note:test-only-0002",
    },
  }));
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "explicit_activation_required_before_model_proposal");
});

test("every registered seam label is accepted, and an unregistered one is not", () => {
  const session = activate(shownPrompt());
  for (const [seam, labels] of Object.entries(V5_J201_MODEL_SEAMS)) {
    for (const label of labels) {
      const result = keep(evaluateModelProposal({
        tenant: TENANT, session,
        proposal: { seam, label, confidence: 0.5, evidence_ref: "note:test-only-0003" },
      }));
      assert.equal(result.decision, "accept", `${seam}/${label}`);
    }
    throwsWithCode(
      () => evaluateModelProposal({
        tenant: TENANT, session,
        proposal: { seam, label: "whatever_it_decided", confidence: 0.5, evidence_ref: "n:1" },
      }),
      "unknown_model_label");
  }
  throwsWithCode(
    () => evaluateModelProposal({
      tenant: TENANT, session,
      proposal: { seam: "permission_kind", label: "action_items", confidence: 0.5, evidence_ref: "n:1" },
    }),
    "unknown_model_seam");
});

test("a proposal reaching for authority, permission or state cannot be read", () => {
  const session = activate(shownPrompt());
  const reaches = {
    authority_class: "system_authority",
    granted_capability: "record.start",
    mode_state: "active_non_recording",
    override_dismissal: true,
    partner_slug: "joe",
    consent_given: true,
    activate_now: true,
    actor_slug: "joe",
    tenant_override: "other",
    scope_widening: true,
    permission_level: 9,
    privilege: "admin",
    authorization_token: "x",
    capability_ref: "x",
  };
  for (const [field, value] of Object.entries(reaches)) {
    throwsWithCode(
      () => evaluateModelProposal({
        tenant: TENANT, session,
        proposal: {
          seam: "note_summary_kind", label: "action_items",
          confidence: 0.5, evidence_ref: "n:1", [field]: value,
        },
      }),
      "model_widening_refused");
  }
  for (const fragment of V5_J201_MODEL_WIDENING_FRAGMENTS) {
    assert.ok(
      Object.keys(reaches).some(name => name.toLowerCase().includes(fragment)),
      `no fixture field exercises the "${fragment}" fragment`);
  }
});

test("a proposal carrying an audio field cannot be read either", () => {
  const session = activate(shownPrompt());
  throwsWithCode(
    () => evaluateModelProposal({
      tenant: TENANT, session,
      proposal: {
        seam: "note_summary_kind", label: "action_items",
        confidence: 0.5, evidence_ref: "n:1", transcript_span: "00:12-00:40",
      },
    }),
    "recording_field_refused");
});

test("a confidence outside zero-to-one is refused", () => {
  const session = activate(shownPrompt());
  for (const confidence of [-0.1, 1.1, Number.NaN, "0.5"]) {
    throwsWithCode(
      () => evaluateModelProposal({
        tenant: TENANT, session,
        proposal: { seam: "note_summary_kind", label: "action_items", confidence, evidence_ref: "n:1" },
      }),
      "invalid_shape");
  }
});

// ---------------------------------------------------------------------------
// Routing the meeting into the same records — graded by F01, not by J201.
// ---------------------------------------------------------------------------

test("an observed meeting becomes an artifact F01 ITSELF admits", () => {
  const candidate = keep(toMeetingRecordLinkCandidate({
    tenant: TENANT, observation: observe(),
    evidence_ref: "evidence:test-only-0002",
    content_digest: `sha256:${"b".repeat(64)}`,
    byte_length: 1024,
    observed_at: "2026-09-11T15:05:00Z",
  }));
  assert.equal(candidate.decision, "candidate");
  assert.equal(candidate.admitted_here, false);

  // F01 is the independent oracle here: J201 does not grade its own mapping.
  const admitted = admitCorporateArtifact({
    tenant: TENANT, artifact: candidate.candidate, now: NOW,
  });
  assert.equal(admitted.decision, "allow");
  assert.equal(admitted.is_fact, false);
  assert.equal(admitted.makes_field_authoritative, false);
});

test("the candidate carries this slice's own provenance and never lowers taint", () => {
  const candidate = keep(toMeetingRecordLinkCandidate({
    tenant: TENANT, observation: observe(),
    evidence_ref: "evidence:test-only-0003",
    content_digest: `sha256:${"c".repeat(64)}`,
    byte_length: 64,
    observed_at: "2026-09-11T15:05:00Z",
  }));
  assert.equal(candidate.candidate.provenance.adapter_kind, V5_J201_ADAPTER_KIND);
  assert.equal(candidate.candidate.taint_class, V5_J201_TAINT_CLASS);
  assert.equal(candidate.candidate.evidence_class, V5_J201_EVIDENCE_CLASS);
});

test("nothing but an observed meeting can be linked into the records", () => {
  const candidate = keep(toMeetingRecordLinkCandidate({
    tenant: TENANT, observation: observe({ corroborating_signals: [] }),
    evidence_ref: "evidence:test-only-0004",
    content_digest: `sha256:${"d".repeat(64)}`,
    byte_length: 64,
    observed_at: "2026-09-11T15:05:00Z",
  }));
  assert.equal(candidate.decision, "refuse");
  assert.equal(candidate.reason_id, "no_observed_meeting_to_link");
  assert.equal(candidate.candidate, null);
});

// ---------------------------------------------------------------------------
// checkable_done 3 — five governed production outcomes required for full v5.
// ---------------------------------------------------------------------------

test("full v5 is unreachable and the five outstanding outcomes are named", () => {
  const gaps = keep(meetingModeGaps());
  assert.equal(gaps.full_v5_ready, false);
  assert.equal(gaps.full_v5_reason_id, "governed_production_outcomes_are_not_producible_from_source");
  assert.equal(gaps.outstanding_production_outcome_steps.length, 5);
  assert.deepEqual(
    [...gaps.outstanding_production_outcome_steps],
    [...V5_J201_PRODUCTION_OUTCOME_STEPS]);
  // The sixth registry entry is named rather than quietly dropped, so the
  // reading that produced "five" can be checked instead of trusted.
  assert.equal(gaps.excluded_from_the_five, V5_J201_KERNEL_PRODUCTION_OUTCOME_STEP);
  assert.ok(!gaps.outstanding_production_outcome_steps.includes(V5_J201_KERNEL_PRODUCTION_OUTCOME_STEP));
});

test("the gap list names the deferred admission of the meeting record-link candidate", () => {
  // toMeetingRecordLinkCandidate projects the shape and stops there. The
  // governed admission and write that would put it in the business records is
  // somebody else's slice, and the gap list has to say so by name rather than
  // leaving `admitted_here: false` as the only trace.
  const gaps = meetingModeGaps();
  const named = gaps.not_built_here.filter(
    line => /record.link candidate/.test(line) && /admission|admit/.test(line) && /write/.test(line));
  assert.equal(named.length, 1, JSON.stringify(gaps.not_built_here));
  assert.ok(/record-source-authority\.v5\.js/.test(named[0]), named[0]);

  // And the thing the gap is about is really deferred, in the same run.
  const candidate = keep(toMeetingRecordLinkCandidate({
    tenant: TENANT, observation: observe(),
    evidence_ref: "evidence:test-only-0009",
    content_digest: `sha256:${"c".repeat(64)}`,
    byte_length: 64,
    observed_at: "2026-09-11T15:05:00Z",
  }));
  assert.equal(candidate.decision, "candidate");
  assert.equal(candidate.admitted_here, false);
  assert.equal(candidate.admitting_module, "record-source-authority.v5.js");
});

test("meetingModeGaps takes no argument through which a caller could argue to yes", () => {
  assert.equal(meetingModeGaps.length, 0);
  // Handing it something anyway changes nothing.
  assert.equal(meetingModeGaps({ full_v5_ready: true }).full_v5_ready, false);
});

test("this slice's own evidence inputs and consumer gates are the catalog's", () => {
  assert.deepEqual([...V5_J201_SLICE_EVIDENCE_INPUTS], [
    "step:j1-core-production-outcome",
    "step:journey-two-contract-binding-receipt",
  ]);
  assert.deepEqual([...V5_J201_CONSUMER_GATES], [
    "global-execution-contract-accepted",
    "global-phi-boundary-accepted",
    "global-prompt-injection-boundary-accepted",
    "global-secrets-boundary-accepted",
    "global-source-authority-accepted",
    "journey-two-preactivation-contract-bound",
  ]);
});

test("what is not built here is named rather than left as an absence", () => {
  const gaps = meetingModeGaps();
  assert.ok(gaps.not_built_here.length >= 6);
  assert.ok(gaps.not_built_here.some(line => /companion/.test(line)));
  assert.ok(gaps.not_built_here.some(line => /recording|transcription/.test(line)));
  assert.equal(gaps.seams.calendar_source, V5_J201_CALENDAR_SOURCE_SEAM);
  assert.equal(gaps.seams.recording_policy, V5_J201_RECORDING_POLICY_SEAM);
});

// ---------------------------------------------------------------------------
// The digest, the projection, and the invariants over everything above.
// ---------------------------------------------------------------------------

test("the policy digest is stable and moves when a vocabulary moves", () => {
  const first = v5J201PolicyDigest();
  assert.equal(first, v5J201PolicyDigest());
  assert.match(first, /^sha256:[0-9a-f]{64}$/);

  const preimage = v5J201PolicyPreimage();
  // Every axis a reader could disagree about is IN the preimage. An axis left
  // out would be one that could move without moving the digest, which is the
  // defect a sibling module in this lane already paid for once.
  for (const key of [
    "settled_decisions", "platforms", "signal_kinds", "presence_states",
    "audio_session_states", "device_states", "affirmative_signal_state",
    "observation_decisions", "prompt_decisions", "reconciliation_dispositions",
    "mode_states", "recording_state", "recording_policy_seam",
    "calendar_source_seam", "explicit_activation_intent",
    "refused_activation_intents", "model_seams", "recording_fragments",
    "model_widening_fragments", "required_read_operations",
    "ledger_provenance_classes", "trusted_ledger_provenance_classes",
    "unwired_once_only_decisions", "dismissal_reopen_requires",
    "prompt_ledger_owner_seam",
    "min_required_corroborating_signals", "production_outcome_steps",
  ]) {
    assert.ok(key in preimage, `${key} is missing from the policy preimage`);
  }
});

test("the projection says what the mode will and will not do", () => {
  const projection = keep(v5J201MeetingModeProjection());
  assert.equal(projection.policy_digest, v5J201PolicyDigest());
  // FALSE, and it must stay false while the gaps block beneath it says nothing
  // enforces once-only. This surface told an operator "prompts once per
  // meeting" while admitting two lines lower that nothing made it true.
  assert.equal(projection.prompts_once_per_meeting, false);
  assert.equal(projection.prompt_reachable_today, false);
  assert.equal(projection.prompts_once_per_meeting_reason_id,
    "the_prompt_ledger_is_owned_by_a_durable_store_this_slice_does_not_contain");
  assert.equal(projection.prompt_ledger_owner_seam, V5_J201_PROMPT_LEDGER_OWNER_SEAM);
  assert.equal(projection.reconciles_on, "platform_and_native_source_identity");
  assert.equal(projection.reconciles_on_time_overlap, false);
  assert.equal(projection.detection_requires.unknown_signal_corroborates, false);
  assert.equal(projection.activation.requires_verified_partner, true);
  assert.equal(projection.audio_retained, false);
  assert.equal(projection.gaps.full_v5_ready, false);
});

test("the projection cannot contradict the gaps block it carries", () => {
  // The exact defect this pass was sent to fix, pinned as an invariant rather
  // than as two literals that a later edit could move apart again.
  const projection = v5J201MeetingModeProjection();
  const gaps = meetingModeGaps();
  assert.equal(projection.prompts_once_per_meeting, gaps.once_only_prompt_enforced_here);
  assert.equal(projection.prompt_reachable_today, gaps.prompt_reachable_here);
  assert.equal(projection.prompts_once_per_meeting_reason_id, gaps.once_only_prompt_reason_id);
  assert.deepEqual(projection.gaps, gaps);
  // And the claim is not true of the product: the reachable answers are both
  // refusals, so no surface may advertise a prompt that fires.
  assert.ok(!V5_J201_PROMPT_DECISIONS.includes("prompt_once"));
});

test("the registered vocabularies are the ones the module actually uses", () => {
  assert.deepEqual([...V5_J201_PLATFORMS], ["teams", "zoom"]);
  assert.deepEqual([...V5_J201_SIGNAL_KINDS].sort(),
    ["audio_session", "calendar_event", "device_state", "presence_session"]);
  assert.deepEqual([...V5_J201_CORROBORATING_SIGNAL_KINDS].sort(),
    ["audio_session", "device_state", "presence_session"]);
  assert.ok(!V5_J201_CORROBORATING_SIGNAL_KINDS.includes("calendar_event"));
  assert.deepEqual([...V5_J201_OBSERVATION_DECISIONS].sort(), [
    "observe_meeting", "refuse", "withhold_insufficient_corroboration",
    "withhold_outside_calendar_window",
  ]);
  assert.deepEqual([...V5_J201_RECONCILIATION_DISPOSITIONS].sort(),
    ["distinct_meetings", "refuse_ambiguous_identity", "single_meeting"]);
});

test("every result is deeply frozen and reports no effects", () => {
  assert.ok(EVERY_RESULT.length > 40, `only ${EVERY_RESULT.length} results were swept`);
  for (const result of EVERY_RESULT) {
    assert.ok(Object.isFrozen(result), `a result was not frozen: ${JSON.stringify(result).slice(0, 120)}`);
    assert.deepEqual(result.effects, V5_NO_EFFECTS);
  }
});

test("the recording answer is the literal string this slice promises", () => {
  // Pinned as a LITERAL on purpose. The sweep below compares results against
  // "denied" rather than against this constant, because a sweep that compared
  // against the constant would stay green if the constant itself were flipped —
  // which is exactly what the mutation pass caught it doing.
  assert.equal(V5_J201_RECORDING_STATE, "denied");
});

test("NOT ONE result this suite produced says anything but recording denied", () => {
  // The invariant, swept rather than spot-checked. If a future path forgets to
  // carry it, this fails without anyone having to remember to test that path.
  assert.ok(EVERY_RESULT.length > 40);
  for (const result of EVERY_RESULT) {
    if ("recording" in result) {
      assert.equal(result.recording, "denied",
        `a result carried recording="${result.recording}"`);
    }
    if ("recording_permitted" in result) {
      assert.equal(result.recording_permitted, false);
    }
    if ("records_audio" in result) {
      assert.equal(result.records_audio, false);
    }
    if ("audio_retained" in result) {
      assert.equal(result.audio_retained, false);
    }
  }
});

test("every observation and prompt result carries the recording answer at all", () => {
  // The sweep above only checks results that HAVE the field. This proves the two
  // decision surfaces always do, so the sweep cannot pass by silence.
  for (const decision of V5_J201_OBSERVATION_DECISIONS) {
    const built = {
      observe_meeting: observe(),
      refuse: observe({ adapter_binding: boundAdapter({ deployment_state: "not_deployed" }) }),
      withhold_insufficient_corroboration: observe({ corroborating_signals: [] }),
      withhold_outside_calendar_window: observe({ now: "2026-09-11T14:00:00Z" }),
    }[decision];
    assert.equal(built.decision, decision);
    assert.equal(built.recording, "denied", decision);
    assert.equal(built.detection_is_not_consent, true, decision);
    assert.equal(built.recording_policy_seam, V5_J201_RECORDING_POLICY_SEAM, decision);
    assert.equal(prompt(built).recording, "denied", decision);
  }
});

test("the read contract schema version is what an adapter must answer to", () => {
  assert.equal(boundAdapter().schema_version, V5_J201_READ_CONTRACT_SCHEMA_VERSION);
  assert.equal(observe().schema_version, V5_J201_OBSERVATION_SCHEMA_VERSION);
  assert.equal(prompt(observe()).schema_version, V5_J201_PROMPT_SCHEMA_VERSION);
  assert.equal(activate(shownPrompt()).schema_version, V5_J201_SESSION_SCHEMA_VERSION);
  assert.equal(
    toMeetingRecordLinkCandidate({
      tenant: TENANT, observation: observe(), evidence_ref: "evidence:test-only-0005",
      content_digest: `sha256:${"e".repeat(64)}`, byte_length: 8,
      observed_at: "2026-09-11T15:05:00Z",
    }).schema_version,
    V5_J201_CANDIDATE_SCHEMA_VERSION);
});
