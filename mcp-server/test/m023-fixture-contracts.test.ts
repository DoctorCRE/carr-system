import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { acceptsConfirmVerb } from "./fixtures/email-followups-app-confirm-verb.ts";
import { adversarialAllowed, adversarialRefused } from "./fixtures/tour-client-text-adversarial.ts";
import { appKeysValid } from "./fixtures/vendor-directory-app-keys.ts";
import {
  CANARY_UNBOUND,
  fetchCandidateBuildRecordRows,
  fetchCheckConclusionRows,
  fetchPredecessorOutcomeRows,
  fetchSchedulerLedgerRows,
  REVISION_FAILED_ANCESTOR,
  SERVICE_KEY,
} from "./gate-zero-producer-stores.v5.fixture.ts";

test("app fixture predicates preserve confirmation and exact key rules", () => {
  assert.equal(acceptsConfirmVerb({ kind: "phase_move", confirm_verb: "confirm-email-deal-progress" }), true);
  assert.equal(acceptsConfirmVerb({ kind: "phase_move", confirm_verb: null }), false);
  assert.equal(acceptsConfirmVerb({ kind: "reply_draft", confirm_verb: null }), true);
  assert.throws(() => Reflect.apply(acceptsConfirmVerb, null, [{ kind: "unknown", confirm_verb: null }]), TypeError);
  assert.equal(appKeysValid({ id: 1, extra: 2 }, ["id"], ["extra"]), true);
  assert.equal(appKeysValid({ id: 1, other: 2 }, ["id"], ["extra"]), false);
  assert.equal(appKeysValid({}, ["id"], []), false);
  assert.equal(appKeysValid(null, [], []), null);
});

test("generated adversarial corpus retains its exact ordered strings", () => {
  for (const [generate, expected] of [
    [adversarialAllowed, "557b75e62c650fb64be822760b972373d7dcbb4efe83fd61a9f7b73166eeda8f"],
    [adversarialRefused, "022b9635f573b0b5fc80e9f2161aaf253f76ac3f3ba371370a0980be81926d3a"],
  ] as const) {
    assert.equal(createHash("sha256").update(JSON.stringify(generate())).digest("hex"), expected);
  }
});

test("producer fixture serves accepted, bound, unbound, missing and failed-check worlds", async () => {
  const accepted = await fetchPredecessorOutcomeRows({ workRequestRef: "WR-000040" });
  assert.equal(accepted.rows.length, 1);
  assert.deepEqual(await fetchPredecessorOutcomeRows({ workRequestRef: "missing" }), {
    store_ref: accepted.store_ref,
    rows: [],
  });
  const bound = await fetchSchedulerLedgerRows({ serviceKey: SERVICE_KEY });
  assert.equal(bound.rows.length, 1);
  assert.ok(bound.rows[0]?.evidence_ref_digest);
  const unbound = await fetchSchedulerLedgerRows({ serviceKey: SERVICE_KEY, canaryRunKey: CANARY_UNBOUND });
  assert.equal(unbound.rows[0]?.evidence_ref_digest, null);
  assert.deepEqual((await fetchSchedulerLedgerRows({ serviceKey: "missing" })).rows, []);
  assert.deepEqual(
    (
      await fetchSchedulerLedgerRows(
        new Proxy(
          {},
          {
            get() {
              throw new Error("trap");
            },
          },
        ),
      )
    ).rows,
    [],
  );
  const check = await fetchCheckConclusionRows({ headSha: REVISION_FAILED_ANCESTOR, checkName: "ops/ci.sh --strict" });
  assert.equal(check.rows[0]?.conclusion_digest, `sha256:${createHash("sha256").update("failure").digest("hex")}`);
  assert.equal((await fetchCandidateBuildRecordRows({ gitSha: REVISION_FAILED_ANCESTOR })).rows.length, 1);
  assert.ok(Object.isFrozen(fetchSchedulerLedgerRows));
});
