// WHY THIS EXISTS. On 2026-08-14 a session told Joe to activate a rule by
// running `./run.sh call activate-rule ...` at his own terminal. It cannot
// work and never could: the local-token path derives an AGENT principal
// (human:false, set server-side, "nothing on the wire can change it" per
// src/index.js), so a human_only verb refuses it no matter who is typing.
//
// The server's hint said to "reconnect through the interactive OAuth connector
// ... or use a receipted local break-glass act". Both the session that wrote
// the instruction and the human who ran it read that and still got it wrong,
// because the hint never says the one thing that matters at a terminal: being
// a human AT THIS PROMPT does not satisfy the gate. The cost was a full round
// trip through Joe to learn nothing new.
//
// This guidance is printed by the local client, so it needs no Worker deploy.

import assert from "node:assert/strict";
import { test } from "node:test";
import { humanOnlyGuidance, isHumanOnlyError } from "../human-only-hint.ts";

test("it says plainly that a human at the local terminal does not satisfy the gate", () => {
  const g = humanOnlyGuidance("activate-rule");
  assert.match(g, /does not satisfy|not satisfied by/i);
  assert.match(g, /agent principal|human:false/i, "it must name WHY, or the reader assumes they typed it wrong");
});

test("it names both real paths, and the break-glass one is ready to run", () => {
  const g = humanOnlyGuidance("activate-rule");
  assert.match(g, /OAuth|connector|Cowork|claude\.ai/i, "the designed path must appear");
  assert.match(g, /CARR_BREAK_GLASS=1/, "the break-glass envelope must appear");
  assert.match(g, /--reason/, "break-glass without a reason is refused, so it must be shown");
  assert.ok(
    g.includes("activate-rule"),
    "the command must carry the caller's own verb, not a placeholder to hand-edit",
  );
});

test("the verb name is interpolated, not hardcoded", () => {
  assert.ok(humanOnlyGuidance("teach").includes("teach"));
  assert.ok(!humanOnlyGuidance("teach").includes("activate-rule"));
});

test("it fires on a human_only payload and stays quiet on every other error", () => {
  assert.equal(isHumanOnlyError({ error: "human_only", hint: "..." }), true);
  assert.equal(isHumanOnlyError({ error: "missing_required", missing: ["x"] }), false);
  assert.equal(isHumanOnlyError({ error: "not_found" }), false);
  assert.equal(isHumanOnlyError(null), false);
  assert.equal(isHumanOnlyError("human_only"), false, "a bare string is not a payload");
});

test("the complete refusal guidance stays byte-identical", () => {
  assert.equal(
    humanOnlyGuidance("activate-rule"),
    "\nWHY THIS CANNOT WORK HERE, whoever is typing:\n  This terminal authenticates with the local token, which is an AGENT\n  principal (human:false, set server-side). Being a human at this prompt\n  does not satisfy a human_only verb — the gate cannot observe you.\n\nTHE TWO PATHS THAT DO WORK:\n  1. An interactive Claude session signed in through the OAuth connector\n     (claude.ai or Cowork). That token carries a verified partner email and\n     is the designed route for a human decision.\n  2. A receipted local break-glass act, when no interactive session exists:\n\n     CARR_BREAK_GLASS=1 ./run.sh call --reason \"why this is the human's own decision\" \\\n       activate-rule '<json args>'\n\n  Break-glass writes a receipt to out/break-glass-receipts.log naming the\n  reason. Use it to CARRY a decision a partner has actually made — never to\n  supply one on their behalf.",
  );
});

test("guidance retains fallback and coercion for caller values", () => {
  for (const value of [undefined, null, false, 0, ""]) {
    assert.equal(humanOnlyGuidance(value), humanOnlyGuidance("<verb>"));
  }
  assert.equal(humanOnlyGuidance(42), humanOnlyGuidance("42"));
});

test("only objects with the exact error value trigger guidance", () => {
  for (const value of [undefined, false, 0, 1, [], {}, { error: null }, { error: "HUMAN_ONLY" }]) {
    assert.equal(isHumanOnlyError(value), false);
  }
  assert.equal(isHumanOnlyError(Object.create({ error: "human_only" })), true);
});
