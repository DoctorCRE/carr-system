// Tests the server-side confidence bar a call-mode change must clear to apply without a card.
import test from "node:test";
import assert from "node:assert/strict";
import { automaticApplyHold } from "../src/call-auto-apply.js";

const step = { kind: "next_step", evidence_quote: "Bayside will send the survey by Friday",
  payload: { deal: "d1", text: "Send the survey", next_date: "2026-10-30" } };
const phase = { kind: "phase_move", evidence_quote: "We moved Gulf Dental to legal",
  payload: { deal: "d2", field: "phase", value: "legal" } };

test("plainly stated next steps and phase moves clear the bar", () => {
  assert.equal(automaticApplyHold(step), null);
  assert.equal(automaticApplyHold(phase), null);
});

test("anything below the bar stays a proposal, with a plain reason", () => {
  const cases = [
    [{ ...step, evidence_quote: null }, /evidence/],
    [{ ...step, evidence_quote: "send survey Friday" }, /evidence/],
    [{ ...step, evidence_quote: "   " }, /evidence/],
    [{ ...step, payload: { ...step.payload, next_date: null } }, /date/],
    [{ ...step, payload: { ...step.payload, text: "Survey" } }, /step/],
    [{ ...phase, payload: { ...phase.payload, value: "" } }, /phase/],
    [{ ...step, payload: { ...step.payload, needs_review: "Dell did not confirm" } }, /Dell did not confirm/],
    [{ kind: "activity", evidence_quote: "We talked about it for a while", payload: {} }, /never applied/],
  ];
  for (const [candidate, reason] of cases) assert.match(automaticApplyHold(candidate) || "", reason);
});

test("punctuation does not count toward the evidence word minimum", () => {
  for (const evidence_quote of ["... !!! ??? ---", "Gulf moved legal ...", "Gulf's ... ??? ---"])
    assert.match(automaticApplyHold({ ...phase, evidence_quote }), /evidence/);
});

test("punctuation does not turn an empty or one-word next step into an automatic change", () => {
  for (const text of ["... !!!", "Survey ...", "Don't ...", "Don’t ..."])
    assert.match(automaticApplyHold({ ...step, payload: { ...step.payload, text } }), /step/);
});

test("ordinary word punctuation and whitespace retain automatic eligibility", () => {
  assert.equal(automaticApplyHold({ ...phase, evidence_quote: "Gulf's\tphase   is LEGAL." }), null);
  assert.equal(automaticApplyHold({ ...step, payload: { ...step.payload, text: "Send survey." } }), null);
});

test("punctuation changes cannot newly qualify a compact quote or next step", () => {
  assert.match(automaticApplyHold({ ...phase, evidence_quote: "one-two-three-four" }), /evidence/);
  assert.match(automaticApplyHold({ ...step, payload: { ...step.payload, text: "send-survey" } }), /step/);
});
