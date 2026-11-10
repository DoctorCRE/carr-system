import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  parseSubjectWatchV2,
  subjectWatchPolicySchema,
  subjectWatchV2ResponseSchema,
  validSubjectWatchPolicy,
} from "../contracts/subject-watch.v2.ts";

const fixture: unknown = JSON.parse(
  readFileSync(new URL("../contracts/subject-watch.v2.fixture.json", import.meta.url), "utf8"),
);

test("v2 exported schema bytes retain the published wire contract", () => {
  for (const [schema, hash] of [
    [subjectWatchPolicySchema, "b060c1a345163c6bfb3029d63bf8263132390856d3ee23a701903b5085e1d9db"],
    [subjectWatchV2ResponseSchema, "7dde6fb3660916eb0736b71ad3b39317fc90430dda862cc28f2780f0f412c802"],
  ] as const)
    assert.equal(createHash("sha256").update(JSON.stringify(schema)).digest("hex"), hash);
});

test("v2 parser preserves identity, list shape, null watches and permitted root extensions", () => {
  const parsed = parseSubjectWatchV2(fixture);
  assert.equal(parsed, fixture);
  const extended = { ...parsed, extra: "retained" };
  assert.equal(parseSubjectWatchV2(extended), extended);
  for (const value of [
    { ok: true, schema_version: "subject-watch.v2", watch: null },
    { ok: true, schema_version: "subject-watch.v2", watches: [], has_more: false, next_cursor: null },
    { ...parsed, watches: [] },
  ])
    assert.equal(parseSubjectWatchV2(value), value);
});

test("v2 parser refuses malformed envelopes, nested watches and invalid lease policies", () => {
  const parsed = parseSubjectWatchV2(fixture);
  assert.ok(parsed.watch);
  const watch = parsed.watch;
  for (const value of [
    null,
    [],
    {},
    { ...parsed, ok: false },
    { ...parsed, watches: [], has_more: false, next_cursor: null },
    ...[
      { ...watch, actor_id: "bad" },
      { ...watch, extra: true },
      { ...watch, policy: { change_kinds: ["reply_arrived"] } },
      { ...watch, policy: { change_kinds: ["lease_threshold_crossed"], include_self_changes: true } },
      { ...watch, baseline: { ...watch.baseline, version: 0 } },
    ].map((watch) => ({ ...parsed, watch })),
  ])
    assert.throws(() => parseSubjectWatchV2(value), /^Error: invalid_subject_watch_v2$/);
});

test("policy validation preserves threshold limits, uniqueness and optional self default without mutation", () => {
  const policy = { change_kinds: ["reply_arrived"] };
  assert.equal(validSubjectWatchPolicy(policy), true);
  assert.deepEqual(policy, { change_kinds: ["reply_arrived"] });
  for (const value of [
    null,
    {},
    { change_kinds: [] },
    { change_kinds: ["reply_arrived", "reply_arrived"] },
    { change_kinds: ["lease_threshold_crossed"] },
    { change_kinds: ["reply_arrived"], lease_threshold_days: [3651] },
    { change_kinds: ["reply_arrived"], lease_threshold_months: [121] },
  ])
    assert.equal(validSubjectWatchPolicy(value), false);
  for (const key of ["lease_threshold_days", "lease_threshold_months"])
    assert.equal(validSubjectWatchPolicy({ change_kinds: ["lease_threshold_crossed"], [key]: [0] }), true);
});
