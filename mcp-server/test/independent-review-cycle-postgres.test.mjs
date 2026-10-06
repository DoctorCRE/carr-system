// V5-A03 against real disposable PostgreSQL. The unit suite proves the JS
// boundary; this proves the append-only relations and transition locks.
//
// Every refusal below is asserted by its exact reason code, never by "it
// threw": a planted mutant that removes one guard usually trips a LATER guard
// or a constraint, and only the reason code tells the two apart.

import assert from "node:assert/strict";
import { randomBytes, randomUUID } from "node:crypto";
import test from "node:test";

import { V5_REVIEW_DIMENSIONS } from "../src/complete-set-review-a03.vocabulary.v5.js";

const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_V5_A03_DB_REQUIRED === "1";
const LOOPBACK = /@(localhost|127[.]0[.]0[.]1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;
const digest = () => `sha256:${randomBytes(32).toString("hex")}`;
const session = label => `session:${label}-${randomUUID()}`;

async function database(t) {
  if (!DSN) {
    assert.equal(REQUIRED, false, "V5-A03 database proof was required but DATABASE_URL is absent");
    t.skip("the migration class supplies a disposable DATABASE_URL");
    return null;
  }
  assert.ok(LOOPBACK.test(DSN), "REFUSED: V5-A03 database proof only writes to disposable loopback PostgreSQL");
  return (await import("pg")).default ?? (await import("pg"));
}

// Each test runs inside one transaction that is rolled back at the end. An
// expected-failing statement must not leave that transaction aborted for what
// follows, so every expected rejection runs inside its own savepoint.
async function expectReject(client, fn, pattern) {
  await client.query("savepoint expect_reject");
  try {
    await assert.rejects(fn(), pattern);
  } finally {
    await client.query("rollback to savepoint expect_reject");
  }
}

async function inTransaction(t, body) {
  const pg = await database(t); if (!pg) return;
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  await client.query("begin");
  try {
    await body(client);
  } finally {
    await client.query("rollback");
    await client.end();
  }
}

async function actor(client, label, active = true) {
  const slug = `a03-${label}-${randomUUID().slice(0, 8)}`;
  return (await client.query(
    "insert into public.actor(slug,kind,display_name,active) values($1,'automation',$1,$2) returning id,slug",
    [slug, active])).rows[0];
}

async function open(client, maker, { changeRef = `change:a03-${randomUUID()}`, delivered = digest(),
  makerSession = session("maker"), key = randomUUID() } = {}) {
  return (await client.query(
    "select * from ops.v5_a03_open_review_case($1,$2,$3,$4,$5)",
    [changeRef, delivered, makerSession, key, maker.id])).rows[0];
}

async function participant(client, caseId, who, role, sessionRef, dimension = null, context = null) {
  return (await client.query(
    "select * from ops.v5_a03_record_participant($1,$2,$3,$4,$5,$6,$7)",
    [caseId, role, dimension, sessionRef, context, randomUUID(), who.id])).rows[0].participant_id;
}

async function reviewAllDimensions(client, caseId, who, sessionRef) {
  for (const dimension of V5_REVIEW_DIMENSIONS)
    await participant(client, caseId, who, "reviewer", sessionRef, dimension, "fresh");
}

async function findingSet(client, caseId, reviewer, reviewerSession, round, dimension, subject, findings = []) {
  return client.query(
    "select * from ops.v5_a03_record_finding_set($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
    [caseId, round, dimension, reviewerSession, subject, "submitted", findings, true, randomUUID(), reviewer.id]);
}

async function findingSets(client, caseId, reviewer, reviewerSession, round, subject, findingsByDimension = {},
  dimensions = V5_REVIEW_DIMENSIONS) {
  for (const dimension of dimensions)
    await findingSet(client, caseId, reviewer, reviewerSession, round, dimension, subject,
      findingsByDimension[dimension] ?? []);
}

async function seal(client, caseId, sealer, round, repaired, checks, artifact, state) {
  return (await client.query(
    "select * from ops.v5_a03_seal_review_round($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
    [caseId, round, digest(), repaired, "suite:a03", checks, artifact, state, randomUUID(), sealer.id])).rows[0];
}

async function adjudicate(client, caseId, who, sessionRef, outcome, disputed) {
  return (await client.query(
    "select * from ops.v5_a03_record_adjudication($1,$2,$3,$4,$5,$6)",
    [caseId, sessionRef, outcome, disputed, randomUUID(), who.id])).rows[0];
}

async function read(client, caseId) {
  return (await client.query("select ops.v5_a03_read_review_case($1) as review", [caseId])).rows[0].review;
}

/** A case with its maker, controller, releaser and one registered all-dimension reviewer. */
async function staffedCase(client, label) {
  const people = {
    builder: await actor(client, `${label}-builder`),
    program: await actor(client, `${label}-program`),
    releaser: await actor(client, `${label}-releaser`),
    reviewer1: await actor(client, `${label}-reviewer1`),
    reviewer2: await actor(client, `${label}-reviewer2`),
    judge: await actor(client, `${label}-judge`),
  };
  const delivered = digest();
  const makerSession = session(`${label}-maker`);
  const changeRef = `change:${label}-${randomUUID()}`;
  const opened = await open(client, people.builder, { changeRef, delivered, makerSession });
  const sessions = {
    maker: makerSession, program: session(`${label}-program`), releaser: session(`${label}-releaser`),
    reviewer1: session(`${label}-reviewer1`), reviewer2: session(`${label}-reviewer2`), judge: session(`${label}-judge`),
  };
  await participant(client, opened.case_id, people.program, "program_controller", sessions.program);
  await participant(client, opened.case_id, people.releaser, "deployment_controller", sessions.releaser);
  await reviewAllDimensions(client, opened.case_id, people.reviewer1, sessions.reviewer1);
  return { caseId: opened.case_id, changeRef, delivered, people, sessions };
}

/** Round 1: reviewer1 finds F1 on the delivered set; the batch repair produces `repaired`. */
async function roundOneWithFinding(client, box, repaired = digest(), finding = "finding:f1") {
  await findingSets(client, box.caseId, box.people.reviewer1, box.sessions.reviewer1, 1, box.delivered,
    { security: [finding] });
  const sealed = await seal(client, box.caseId, box.people.program, 1, [finding], ["check:a"], repaired, "changes_required");
  assert.equal(sealed.state, "changes_required");
  assert.deepEqual(sealed.detections, []);
  assert.equal(sealed.case_status, "open");
  return repaired;
}

// ---------------------------------------------------------------- happy paths

test("DB: honest path — F1 found, repaired, round 2 by the same reviewer finds nothing — records pass with no drift", async t => {
  await inTransaction(t, async client => {
    const box = await staffedCase(client, "honest");
    const repaired = await roundOneWithFinding(client, box);
    await findingSets(client, box.caseId, box.people.reviewer1, box.sessions.reviewer1, 2, repaired);
    const sealed = await seal(client, box.caseId, box.people.program, 2, [], ["check:a", "check:b"], repaired, "no_changes_required");
    assert.deepEqual(sealed.detections, [], "the same reviewer confirming a repair is not instability");
    assert.equal(sealed.case_status, "concluded");

    const review = await read(client, box.caseId);
    assert.equal(review.status, "concluded");
    assert.deepEqual({ outcome: review.outcome.outcome, decided_by: review.outcome.decided_by },
      { outcome: "pass", decided_by: "clean_round" });
    assert.equal(review.submissions.length, 22);
    assert.deepEqual(review.rounds.map(round => round.detections), [[], []]);
    assert.deepEqual(review.submissions.filter(row => row.round_ordinal === 2).map(row => row.reviewed_set_digest),
      Array(11).fill(repaired), "round 2 reviewed the repaired artifact");

    await expectReject(client, () => participant(client, box.caseId, box.people.judge, "adjudicator", box.sessions.judge),
      /v5_a03_case_concluded/);
    await expectReject(client, () => adjudicate(client, box.caseId, box.people.judge, box.sessions.judge, "fail", ["finding:f1"]),
      /v5_a03_adjudication_without_dispute/);
  });
});

test("DB: a clean first round records pass and closes the case to further rounds and participants", async t => {
  await inTransaction(t, async client => {
    const box = await staffedCase(client, "clean-one");
    await findingSets(client, box.caseId, box.people.reviewer1, box.sessions.reviewer1, 1, box.delivered);
    const sealed = await seal(client, box.caseId, box.people.program, 1, [], ["check:a"], box.delivered, "no_changes_required");
    assert.equal(sealed.case_status, "concluded");
    const review = await read(client, box.caseId);
    assert.deepEqual([review.outcome.outcome, review.outcome.decided_by], ["pass", "clean_round"]);
    await expectReject(client, () => findingSet(client, box.caseId, box.people.reviewer1, box.sessions.reviewer1, 2,
      "security", box.delivered), /v5_a03_case_concluded/);
    await expectReject(client, () => participant(client, box.caseId, box.people.judge, "adjudicator", box.sessions.judge),
      /v5_a03_case_concluded/);
  });
});

test("DB: complete findings seal in two rounds, checks never shrink, and a third loop cannot begin", async t => {
  await inTransaction(t, async client => {
    const box = await staffedCase(client, "two-round");
    await expectReject(client, () => participant(client, box.caseId, box.people.builder, "reviewer", session("self"),
      "architecture", "fresh"), /v5_a03_opposing_role_identity_reused/);
    const repaired = await roundOneWithFinding(client, box);
    await reviewAllDimensions(client, box.caseId, box.people.reviewer2, box.sessions.reviewer2);
    await findingSets(client, box.caseId, box.people.reviewer2, box.sessions.reviewer2, 2, repaired,
      { product: ["finding:f2"] });
    await expectReject(client, () => seal(client, box.caseId, box.people.program, 2, ["finding:f2"], ["check:b"], digest(),
      "changes_required"), /v5_a03_test_weakening/);
    const sealed = await seal(client, box.caseId, box.people.program, 2, ["finding:f2"], ["check:a", "check:b"], digest(),
      "changes_required");
    assert.equal(sealed.case_status, "awaiting_stronger_adjudication");

    await expectReject(client, () => findingSet(client, box.caseId, box.people.reviewer2, box.sessions.reviewer2, 3,
      "architecture", repaired), /v5_a03_review_round_limit_exhausted/);
    await expectReject(client, () => seal(client, box.caseId, box.people.program, 3, [], ["check:a", "check:b"], digest(),
      "no_changes_required"), /v5_a03_review_round_limit_exhausted/);
    await expectReject(client, () => client.query(
      "update ops.v5_a03_review_round set state='no_changes_required' where case_id=$1", [box.caseId]), /append_only/);
    await expectReject(client, () => client.query(
      "delete from ops.v5_a03_review_round where case_id=$1", [box.caseId]), /append_only/);
  });
});

test("DB: two unresolved rounds require a distinct stronger adjudicator and persist one digested disposition", async t => {
  await inTransaction(t, async client => {
    const box = await staffedCase(client, "adjudication");
    const repaired = await roundOneWithFinding(client, box);
    await reviewAllDimensions(client, box.caseId, box.people.reviewer2, box.sessions.reviewer2);
    await findingSets(client, box.caseId, box.people.reviewer2, box.sessions.reviewer2, 2, repaired,
      { product: ["finding:f2"] });
    await seal(client, box.caseId, box.people.program, 2, ["finding:f2"], ["check:a"], digest(), "changes_required");

    await expectReject(client, () => participant(client, box.caseId, box.people.reviewer2, "adjudicator", session("bad-judge")),
      /v5_a03_opposing_role_identity_reused/);
    await participant(client, box.caseId, box.people.judge, "adjudicator", box.sessions.judge);
    const adjudication = await adjudicate(client, box.caseId, box.people.judge, box.sessions.judge, "quarantine", ["finding:f2"]);
    assert.match(adjudication.receipt_digest, /^sha256:[0-9a-f]{64}$/);
    assert.equal(adjudication.outcome, "quarantine");

    const review = await read(client, box.caseId);
    assert.equal(review.status, "concluded");
    assert.equal(review.adjudication.outcome, "quarantine");
    assert.deepEqual([review.outcome.outcome, review.outcome.decided_by], ["quarantine", "stronger_adjudication"]);
    await expectReject(client, () => participant(client, box.caseId, box.people.judge, "architect", session("after-close")),
      /v5_a03_review_round_reopened_after_adjudication/);
    await expectReject(client, () => adjudicate(client, box.caseId, box.people.judge, box.sessions.judge, "pass", ["finding:f2"]),
      /v5_a03_review_round_reopened_after_adjudication/);
    await expectReject(client, () => client.query(
      "delete from ops.v5_a03_case_outcome where case_id=$1", [box.caseId]), /append_only/);
  });
});

// ------------------------------------------------------ round-2 drift routing

test("DB: a repeated finding in round 2 is recorded and routes to adjudication, never stranding the case", async t => {
  await inTransaction(t, async client => {
    const box = await staffedCase(client, "repeated");
    const repaired = await roundOneWithFinding(client, box);
    await reviewAllDimensions(client, box.caseId, box.people.reviewer2, box.sessions.reviewer2);
    await findingSets(client, box.caseId, box.people.reviewer2, box.sessions.reviewer2, 2, repaired,
      { security: ["finding:f1"] });
    const sealed = await seal(client, box.caseId, box.people.program, 2, ["finding:f1"], ["check:a"], digest(), "changes_required");
    assert.deepEqual(sealed.detections, ["repeated_finding"]);
    assert.equal(sealed.case_status, "awaiting_stronger_adjudication");
    await participant(client, box.caseId, box.people.judge, "adjudicator", box.sessions.judge);
    await adjudicate(client, box.caseId, box.people.judge, box.sessions.judge, "fail", ["finding:f1"]);
    const review = await read(client, box.caseId);
    assert.deepEqual(review.rounds[1].detections, ["repeated_finding"]);
    assert.deepEqual([review.status, review.outcome.outcome], ["concluded", "fail"]);
  });
});

test("DB: a round-2 repair back to a rejected tree is recorded as circular reversion", async t => {
  await inTransaction(t, async client => {
    const box = await staffedCase(client, "circular");
    const repaired = await roundOneWithFinding(client, box);
    await reviewAllDimensions(client, box.caseId, box.people.reviewer2, box.sessions.reviewer2);
    await findingSets(client, box.caseId, box.people.reviewer2, box.sessions.reviewer2, 2, repaired,
      { product: ["finding:f2"] });
    const sealed = await seal(client, box.caseId, box.people.program, 2, ["finding:f2"], ["check:a"], box.delivered,
      "changes_required");
    assert.deepEqual(sealed.detections, ["circular_reversion"]);
    assert.equal(sealed.case_status, "awaiting_stronger_adjudication");
  });
});

test("DB: one reviewer giving both answers on the SAME artifact is recorded as instability and escalated, not passed", async t => {
  await inTransaction(t, async client => {
    const box = await staffedCase(client, "unstable");
    // A no-op "repair": round 1's post-repair artifact is the delivered set,
    // so round 2 re-reviews the identical tree and reviewer1 now finds nothing.
    await roundOneWithFinding(client, box, box.delivered);
    await findingSets(client, box.caseId, box.people.reviewer1, box.sessions.reviewer1, 2, box.delivered);
    const sealed = await seal(client, box.caseId, box.people.program, 2, [], ["check:a"], digest(), "no_changes_required");
    assert.deepEqual(sealed.detections, ["reviewer_instability"]);
    assert.equal(sealed.case_status, "awaiting_stronger_adjudication", "a clean-but-unstable round is not a pass");
    assert.equal((await read(client, box.caseId)).outcome, null);
    await participant(client, box.caseId, box.people.judge, "adjudicator", box.sessions.judge);
    await adjudicate(client, box.caseId, box.people.judge, box.sessions.judge, "quarantine", ["finding:f1"]);
    const review = await read(client, box.caseId);
    assert.deepEqual([review.outcome.outcome, review.outcome.decided_by], ["quarantine", "stronger_adjudication"]);
  });
});

// ------------------------------------------------------------- seal guards

test("DB: finding sets follow the round sequence and bind to the artifact each round reviews", async t => {
  await inTransaction(t, async client => {
    const box = await staffedCase(client, "sequence");
    await expectReject(client, () => findingSet(client, box.caseId, box.people.reviewer1, box.sessions.reviewer1, 2,
      "security", box.delivered), /v5_a03_review_round_limit_exhausted/);
    await expectReject(client, () => findingSet(client, box.caseId, box.people.reviewer1, box.sessions.reviewer1, 1,
      "security", digest()), /v5_a03_review_scope_narrower_than_delivered_set/);
    const repaired = await roundOneWithFinding(client, box);
    await expectReject(client, () => findingSet(client, box.caseId, box.people.reviewer1, box.sessions.reviewer1, 1,
      "security", box.delivered), /v5_a03_review_round_limit_exhausted/);
    await expectReject(client, () => findingSet(client, box.caseId, box.people.reviewer1, box.sessions.reviewer1, 2,
      "security", box.delivered), /v5_a03_review_not_bound_to_repaired_artifact/);
    await findingSet(client, box.caseId, box.people.reviewer1, box.sessions.reviewer1, 2, "security", repaired);
  });
});

test("DB: a round seals only with all eleven dimensions, the complete batch, and a truthful state", async t => {
  await inTransaction(t, async client => {
    const box = await staffedCase(client, "seal-guards");
    await findingSets(client, box.caseId, box.people.reviewer1, box.sessions.reviewer1, 1, box.delivered,
      { security: ["finding:f1"] }, V5_REVIEW_DIMENSIONS.slice(0, 10));
    await expectReject(client, () => seal(client, box.caseId, box.people.program, 1, ["finding:f1"], ["check:a"], digest(),
      "changes_required"), /v5_a03_finding_set_dimension_absent/);
    await findingSets(client, box.caseId, box.people.reviewer1, box.sessions.reviewer1, 1, box.delivered, {},
      V5_REVIEW_DIMENSIONS.slice(10));
    await expectReject(client, () => seal(client, box.caseId, box.people.program, 1, [], ["check:a"], digest(),
      "changes_required"), /v5_a03_batch_repair_not_complete/);
    await expectReject(client, () => seal(client, box.caseId, box.people.program, 1, ["finding:f1"], ["check:a"], digest(),
      "no_changes_required"), /v5_a03_round_state_mismatch/);
    const sealed = await seal(client, box.caseId, box.people.program, 1, ["finding:f1"], ["check:a"], digest(), "changes_required");
    assert.equal(sealed.case_status, "open");
  });
});

test("DB: the seal re-checks that every submission reviewed the round's subject artifact", async t => {
  await inTransaction(t, async client => {
    const box = await staffedCase(client, "seal-scope");
    await findingSets(client, box.caseId, box.people.reviewer1, box.sessions.reviewer1, 1, box.delivered,
      { security: ["finding:f1"] }, V5_REVIEW_DIMENSIONS.slice(0, 10));
    // A narrowed submission that bypassed the writer's own scope check.
    const reviewerRow = (await client.query(
      "select id from ops.v5_a03_review_participant where case_id=$1 and role='reviewer' and dimension=$2",
      [box.caseId, V5_REVIEW_DIMENSIONS[10]])).rows[0].id;
    await client.query(
      "insert into ops.v5_a03_finding_set(case_id,round_ordinal,dimension,reviewer_participant_id,reviewed_set_digest,submission_state,finding_refs,enumerated_before_repair,idempotency_key) values($1,1,$2,$3,$4,'submitted','{}',true,$5)",
      [box.caseId, V5_REVIEW_DIMENSIONS[10], reviewerRow, digest(), randomUUID()]);
    await expectReject(client, () => seal(client, box.caseId, box.people.program, 1, ["finding:f1"], ["check:a"], digest(),
      "changes_required"), /v5_a03_review_scope_narrower_than_delivered_set/);
  });
});

test("DB: only the registered program controller's own actor may seal a round", async t => {
  await inTransaction(t, async client => {
    const box = await staffedCase(client, "sealer");
    await findingSets(client, box.caseId, box.people.reviewer1, box.sessions.reviewer1, 1, box.delivered,
      { security: ["finding:f1"] });
    for (const imposter of [box.people.releaser, box.people.builder, box.people.reviewer1])
      await expectReject(client, () => seal(client, box.caseId, imposter, 1, ["finding:f1"], ["check:a"], digest(),
        "changes_required"), /v5_a03_round_sealer_not_program_controller/);
  });
});

// ------------------------------------------------------------- adjudication

test("DB: adjudication waits for round 2 and is refused to a caller who is not the registered adjudicator actor", async t => {
  await inTransaction(t, async client => {
    const box = await staffedCase(client, "adjudicator-lookup");
    const repaired = await roundOneWithFinding(client, box);
    await participant(client, box.caseId, box.people.judge, "adjudicator", box.sessions.judge);
    await expectReject(client, () => adjudicate(client, box.caseId, box.people.judge, box.sessions.judge, "fail", ["finding:f1"]),
      /v5_a03_adjudication_before_round_limit/);
    await reviewAllDimensions(client, box.caseId, box.people.reviewer2, box.sessions.reviewer2);
    await findingSets(client, box.caseId, box.people.reviewer2, box.sessions.reviewer2, 2, repaired,
      { product: ["finding:f2"] });
    await seal(client, box.caseId, box.people.program, 2, ["finding:f2"], ["check:a"], digest(), "changes_required");
    const imposter = await actor(client, "imposter");
    await expectReject(client, () => adjudicate(client, box.caseId, imposter, box.sessions.judge, "pass", ["finding:f2"]),
      /v5_a03_adjudicator_not_registered/);
    await adjudicate(client, box.caseId, box.people.judge, box.sessions.judge, "pass", ["finding:f2"]);
  });
});

test("DB: the program controller who sealed the rounds cannot become the adjudicator", async t => {
  await inTransaction(t, async client => {
    const box = await staffedCase(client, "pc-judge");
    const repaired = await roundOneWithFinding(client, box);
    await reviewAllDimensions(client, box.caseId, box.people.reviewer2, box.sessions.reviewer2);
    await findingSets(client, box.caseId, box.people.reviewer2, box.sessions.reviewer2, 2, repaired,
      { product: ["finding:f2"] });
    await seal(client, box.caseId, box.people.program, 2, ["finding:f2"], ["check:a"], digest(), "changes_required");
    for (const [who, label] of [[box.people.program, "program"], [box.people.releaser, "releaser"], [box.people.builder, "builder"]])
      await expectReject(client, () => participant(client, box.caseId, who, "adjudicator", session(`${label}-judge`)),
        /v5_a03_opposing_role_identity_reused/);
    await expectReject(client, () => participant(client, box.caseId, box.people.judge, "adjudicator", box.sessions.program),
      /v5_a03_opposing_role_identity_reused/);

    // Defense in depth: even a party row that reached the adjudicator seat by
    // some other path is refused at adjudication time.
    const programJudgeSession = session("program-judge");
    await client.query(
      "insert into ops.v5_a03_review_participant(case_id,role,actor_id,session_ref,idempotency_key) values($1,'adjudicator',$2,$3,$4)",
      [box.caseId, box.people.program.id, programJudgeSession, randomUUID()]);
    await expectReject(client, () => adjudicate(client, box.caseId, box.people.program, programJudgeSession, "pass", ["finding:f2"]),
      /v5_a03_adjudicator_is_a_party/);
  });
});

test("DB: an adjudicator who is also a reviewer on the case is a party and is refused", async t => {
  await inTransaction(t, async client => {
    const box = await staffedCase(client, "reviewer-judge");
    const repaired = await roundOneWithFinding(client, box);
    await reviewAllDimensions(client, box.caseId, box.people.reviewer2, box.sessions.reviewer2);
    await findingSets(client, box.caseId, box.people.reviewer2, box.sessions.reviewer2, 2, repaired,
      { product: ["finding:f2"] });
    await seal(client, box.caseId, box.people.program, 2, ["finding:f2"], ["check:a"], digest(), "changes_required");
    await participant(client, box.caseId, box.people.judge, "adjudicator", box.sessions.judge);
    // A reviewer row for the judge that bypassed registration's opposition check.
    await client.query(
      "insert into ops.v5_a03_review_participant(case_id,role,dimension,actor_id,session_ref,context_binding,idempotency_key) values($1,'reviewer','security',$2,$3,'fresh',$4)",
      [box.caseId, box.people.judge.id, session("judge-as-reviewer"), randomUUID()]);
    await expectReject(client, () => adjudicate(client, box.caseId, box.people.judge, box.sessions.judge, "pass", ["finding:f2"]),
      /v5_a03_adjudicator_is_a_party/);
  });
});

// ---------------------------------------------------------- identity guards

test("DB: an inactive maker cannot open a case", async t => {
  await inTransaction(t, async client => {
    const retired = await actor(client, "retired-maker", false);
    await expectReject(client, () => open(client, retired), /v5_a03_maker_actor_current/);
  });
});

test("DB: the releaser and reviewer duties are separated by actor, and no opposing duty may reuse a session", async t => {
  await inTransaction(t, async client => {
    const box = await staffedCase(client, "separation");
    await expectReject(client, () => participant(client, box.caseId, box.people.releaser, "reviewer", session("releaser-review"),
      "architecture", "fresh"), /v5_a03_opposing_role_identity_reused/);
    await expectReject(client, () => participant(client, box.caseId, box.people.reviewer1, "deployment_controller",
      session("reviewer-release")), /v5_a03_opposing_role_identity_reused/);
    const alias = await actor(client, "alias");
    for (const reused of [box.sessions.maker, box.sessions.program, box.sessions.releaser])
      await expectReject(client, () => participant(client, box.caseId, alias, "reviewer", reused, "architecture", "fresh"),
        /v5_a03_opposing_role_identity_reused/);
    await expectReject(client, () => participant(client, box.caseId, alias, "program_controller", box.sessions.reviewer1),
      /v5_a03_opposing_role_identity_reused/);
  });
});

// ---------------------------------------------- the bound is per change, not per case

test("DB: an adjudicated change cannot be reopened under a new digest, a renamed change_ref, or a reused artifact", async t => {
  await inTransaction(t, async client => {
    const box = await staffedCase(client, "cross-case");
    const repaired = await roundOneWithFinding(client, box);
    await reviewAllDimensions(client, box.caseId, box.people.reviewer2, box.sessions.reviewer2);
    await findingSets(client, box.caseId, box.people.reviewer2, box.sessions.reviewer2, 2, repaired,
      { product: ["finding:f2"] });
    const secondRepair = digest();
    await seal(client, box.caseId, box.people.program, 2, ["finding:f2"], ["check:a"], secondRepair, "changes_required");
    await participant(client, box.caseId, box.people.judge, "adjudicator", box.sessions.judge);
    await adjudicate(client, box.caseId, box.people.judge, box.sessions.judge, "fail", ["finding:f2"]);

    // Same change, fresh digest: a new two-round allowance is refused.
    await expectReject(client, () => open(client, box.people.builder, { changeRef: box.changeRef }),
      /v5_a03_change_already_under_review/);
    // Renamed change_ref over the same delivered set, or over either repaired artifact.
    for (const reused of [box.delivered, repaired, secondRepair])
      await expectReject(client, () => open(client, box.people.builder, { delivered: reused }),
        /v5_a03_delivered_set_already_under_review/);

    // Pre-registering an artifact in a second case and then producing it as a
    // repair here would hand that case a clean slate: the seal refuses it.
    const other = await staffedCase(client, "cross-case-other");
    await findingSets(client, other.caseId, other.people.reviewer1, other.sessions.reviewer1, 1, other.delivered,
      { security: ["finding:g1"] });
    for (const bound of [box.delivered, secondRepair])
      await expectReject(client, () => seal(client, other.caseId, other.people.program, 1, ["finding:g1"], ["check:a"], bound,
        "changes_required"), /v5_a03_artifact_bound_to_other_case/);
  });
});

test("DB: opening is idempotent on its key and refuses the key for a different change", async t => {
  await inTransaction(t, async client => {
    const maker = await actor(client, "idem-maker");
    const request = { changeRef: `change:idem-${randomUUID()}`, delivered: digest(), makerSession: session("idem"), key: randomUUID() };
    const first = await open(client, maker, request);
    const replay = await open(client, maker, request);
    assert.deepEqual(replay, first);
    await expectReject(client, () => open(client, maker, { ...request, changeRef: `change:idem-other-${randomUUID()}` }),
      /v5_a03_idempotency_key_reused/);
  });
});
