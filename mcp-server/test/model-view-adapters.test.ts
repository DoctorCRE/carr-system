import assert from "node:assert/strict";
import test from "node:test";
import * as canonical from "../../docs/models/email-contact-review/canonical-view.ts";
import * as competition from "../../docs/models/email-contact-review/competition-view.ts";
import * as review from "../../docs/models/email-contact-review/view.ts";
import * as lifecycle from "../../docs/models/email-lifecycle/view.ts";
import * as reply from "../../docs/models/reply-action-links/ReplyLinks.map.ts";

test("canonical labels preserve precedence for duplicate, binding and phase states", () => {
  const base = { findings: [], badMerge: false, badBinding: false, phase: "scan" };
  assert.equal(canonical.title, "Two signatures sharing a Message-ID");
  assert.deepEqual(canonical.readerActions, []);
  assert.equal(canonical.state(base), "Frozen proposals retained");
  assert.equal(canonical.state({ ...base, phase: "resolve" }), "Checking signature source");
  assert.equal(canonical.state({ ...base, badBinding: true }), "Changed evidence submitted");
  assert.equal(canonical.state({ ...base, badBinding: true, badMerge: true }), "Ambiguous source merged");
  assert.equal(canonical.state({ ...base, findings: [1, 2], badMerge: false }), "Frozen proposals retained");
  assert.equal(canonical.state({ ...base, findings: [1, 2, 3], badMerge: true }), "Duplicate proposals recorded");
  for (const [action, label] of Object.entries({
    Scan: "Read another mail representation",
    Resolve: "Resolve source and hold or record",
    Retry: "Retry after lost receipt",
    Restart: "Restart intake",
  }))
    assert.equal(canonical.step(action), label);
  assert.equal(canonical.step("Unknown"), undefined);
});

test("competition labels retain evidence and precedence", () => {
  const base = { findings: [], excluded: [], badMerge: false, badBinding: false, phase: "scan" };
  assert.equal(competition.title, "Competing signatures remain held across scans");
  assert.deepEqual(competition.readerActions, []);
  assert.equal(competition.state(base), "Competing signatures held");
  assert.equal(competition.state({ ...base, excluded: [1] }), "Distinct sources proven");
  assert.equal(competition.state({ ...base, phase: "resolve", excluded: [1] }), "Checking returning signature");
  assert.equal(competition.state({ ...base, phase: "competing", excluded: [1] }), "Original proposal retained");
  assert.equal(competition.state({ ...base, badMerge: true }), "Competing signature guessed");
  assert.equal(competition.state({ ...base, badMerge: true, badBinding: true }), "Changed evidence submitted");
  assert.equal(competition.state({ ...base, findings: [1], badMerge: false }), "Competing signatures held");
  assert.equal(competition.state({ ...base, findings: [1, 2], badBinding: true }), "Duplicate proposal recorded");
  for (const [action, label] of Object.entries({
    Compete: "Read two indistinguishable keyed signatures",
    Scan: "Read one signature again",
    Resolve: "Check retained evidence",
    Prove: "Read distinguishing evidence for the other source",
    Restart: "Restart intake",
  }))
    assert.equal(competition.step(action), label);
  assert.equal(competition.step("Unknown"), undefined);
});

test("review labels preserve aliases, actions and unmapped actions", () => {
  assert.equal(review.title, "Email signature findings across decoder aliases");
  assert.equal(review.state({ alias: "decoder-a", phase: "held" }), "decoder-a: held");
  for (const [action, label] of Object.entries({
    Resolve: "resolve sender and inspect signature",
    Submit: "submit frozen proposal",
    Retry: "retry or hold after validation",
    AliasChange: "prove decoder alias by unambiguous Message-ID",
    AmbiguousDuplicate: "keep duplicate store keys separate",
    Restart: "reload frozen journal",
    Replay: "repeat scan",
    Done: "keep disposition",
    RecoverHeld: "read full source with exact party",
    RestoreSource: "original source binding restored",
  }))
    assert.equal(review.step(action), label);
  assert.equal(review.step("Unknown"), undefined);
});

test("lifecycle labels preserve terminal states and the hidden refusal action", () => {
  assert.equal(lifecycle.title, "Captured mail lifecycle");
  for (const state of ["live", "deleted", "recalled", "superseded"]) assert.equal(lifecycle.state({ state }), state);
  for (const [action, label] of Object.entries({
    Act: "act on live mail",
    Observe: "observe terminal state and note earlier actions",
    Replay: "replay observation",
    RefuseStaleOrForeign: null,
  }))
    assert.equal(lifecycle.step(action), label);
  assert.equal(lifecycle.step("Unknown"), undefined);
});

test("reply labels cross both session states with every card state and retain unknown actions", () => {
  assert.deepEqual(reply.regions, ["session", "card"]);
  assert.deepEqual(reply.readerActions, []);
  for (const session of ["SignedIn", "SignedOut"]) {
    for (const [card, label] of Object.entries({
      Unissued: "No link",
      Issued: "Link available",
      Review: "Draft ready for review",
      Expired: "Link expired",
      Consumed: "Draft approved",
      Refused: "Request refused",
    }))
      assert.equal(reply.state({ session, card }), `${session === "SignedIn" ? "Signed in" : "Signed out"}: ${label}`);
  }
  for (const [action, label] of Object.entries({
    SignOut: "Sign out",
    SignIn: "Sign in",
    IssueCurrentProposal: "Issue current proposal",
    ResolveOwningSession: "Resolve owning session",
    ResolveForeignOrSignedOut: "Resolve foreign or signed out",
    ResolveAgainWithoutMutation: "Resolve again without mutation",
    EditOrRetireSource: "Edit or retire source",
    ExplicitDomainApproval: "Explicit domain approval",
    ResolveOrActAgain: "Resolve or act again",
    ResolveOrActStaleReference: "Resolve or act stale reference",
    ResolveFreshEligibleReference: "Resolve fresh eligible reference",
  }))
    assert.equal(reply.step(action), label);
  assert.equal(reply.step("Unknown"), "Unknown");
});
