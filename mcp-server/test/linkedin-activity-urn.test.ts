import { required } from "./helpers/legacy-test-registry.ts";

// linkedin-activity-urn.test.ts — coverage for the LinkedIn reference gap,
// found live 2026-08-19 by the weekly social-metrics run.
//
// THE DEFECT. `measure-placement` resolves a placement by a stored key: the
// placement uuid, the Blotato post id, or `placement.url`. For LinkedIn that URL
// is whatever Blotato reports as `postUrl`, which is the share/ugcPost form
// (urn:li:share:… / urn:li:ugcPost:…). LinkedIn NEVER shows a person that id.
// The recent-activity feed, the per-post analytics link, and the entire rendered
// DOM carry only the ACTIVITY urn. So a human — or a session driving a browser —
// reads real impressions off LinkedIn and then cannot record them against
// anything, because the handle they hold matches no stored key.
//
// The two ids are minted for the same post milliseconds apart and are NOT
// derivable from one another, which is what makes this a real gap rather than a
// string-format problem:
//     2026-08-05  stored ugcPost 7490800344260841472  activity 7490800345598779392
//     2026-08-03  stored share   7490064185725280256  activity 7490064188413870080
//     2026-07-31  stored ugcPost 7488980086407389184  activity 7488980089368469505
// Constructing the share urn from the activity id was tried against production
// and correctly refused.
//
// COST BEFORE THE FIX: four readings stranded in a single week, including the
// best-performing post on any platform (2026-08-07, 271 impressions), on the
// channel that out-reaches every other by roughly an order of magnitude. It
// would have stranded four or five more every week for as long as it stood.
//
// THE BRIDGE. Both ids are Snowflake-shaped, so the high bits are the publish
// time (ms = id >> 22). Measured against all four stranded posts on 2026-08-19,
// each activity urn decoded to within 0.1 SECONDS of a real linkedin
// placement's live_at, while the next-nearest linkedin placement sat ~170,000
// seconds away — better than three orders of magnitude of separation. The
// resolver therefore matches inside a ±90s window and REFUSES as ambiguous if
// two placements fall inside it, rather than guessing.
//
// What this file locks down is the decode and its guards. The window match and
// the ambiguity refusal are SQL and are exercised against a real database by the
// migration/db classes; what can silently rot here is the parsing — a regex that
// starts matching the wrong urn type, or a shift that quietly returns 1970.

import assert from "node:assert/strict";
import { test } from "node:test";
import { linkedInActivityPublishedAt } from "./helpers/legacy-test-registry.ts";

const iso = (d: Date) => d.toISOString().replace(/\.\d{3}Z$/, "Z");

// The four posts that were actually stranded, with the publish time each id
// must decode to. These are real ids off Joe's account.
const REAL: [string, string, string][] = [
  ["7491518023099527168", "2026-08-07T15:38:02Z", "Fort Walton Beach comp, 271 impressions"],
  ["7492600148376657920", "2026-08-10T15:18:01Z", "Physicians Advocacy Institute, 162"],
  ["7493338573098622976", "2026-08-12T16:12:15Z", "optometry two businesses, 182"],
  ["7494053482216366083", "2026-08-14T15:33:02Z", "practice ownership data, 131"],
];

test("decodes the publish time of every stranded post", () => {
  for (const [id, want, label] of REAL) {
    const got = linkedInActivityPublishedAt(`urn:li:activity:${id}`);
    assert.ok(got, `${label}: returned null`);
    assert.equal(iso(got), want, label);
  }
});

test("accepts every shape LinkedIn actually hands a reader", () => {
  const id = required(REAL[0])[0];
  const want = required(REAL[0])[1];
  for (const ref of [
    `urn:li:activity:${id}`,
    `https://www.linkedin.com/feed/update/urn:li:activity:${id}/`,
    `https://linkedin.com/feed/update/urn:li:activity:${id}`,
    `/analytics/post-summary/urn:li:activity:${id}/`,
    `  urn:li:activity:${id}  `,
    `URN:LI:ACTIVITY:${id}`,
  ]) {
    const got = linkedInActivityPublishedAt(ref);
    assert.ok(got, `no match for ${ref}`);
    assert.equal(iso(got), want, ref);
  }
});

test("leaves every non-LinkedIn handle alone, so other resolution is untouched", () => {
  for (const ref of [
    "https://x.com/joebookout/status/2085039126311543214",
    "https://facebook.com/102825118368840_1448036917347228",
    "https://www.instagram.com/p/Db9QpVAiQoy/",
    "3029878",
    "921db62b-d8b6-43a8-8f37-829df2d5ceec",
    "",
    "urn:li:share:7490064185725280256", // the STORED form resolves by url, not here
    "urn:li:ugcPost:7490800344260841472",
  ])
    assert.equal(linkedInActivityPublishedAt(ref), null, `should not match: ${ref}`);
});

test("refuses an id that decodes outside plausible range instead of matching 1970", () => {
  // too short to be a snowflake — decodes to 1970
  assert.equal(linkedInActivityPublishedAt("urn:li:activity:123456"), null);
  // absurdly long — beyond the guard
  assert.equal(linkedInActivityPublishedAt("urn:li:activity:999999999999999999999999"), null);
});

test("a real id decodes to a sane, recent instant", () => {
  const got = required(linkedInActivityPublishedAt(`urn:li:activity:${required(REAL[0])[0]}`));
  assert.ok(got.getUTCFullYear() >= 2010 && got.getUTCFullYear() <= 2100);
});

test("distinct posts decode to distinct times, so the window can separate them", () => {
  const times = REAL.map(([id]) => required(linkedInActivityPublishedAt(`urn:li:activity:${id}`)).getTime());
  assert.equal(new Set(times).size, REAL.length);
  const sorted = [...times].sort((a, b) => a - b);
  for (let i = 1; i < sorted.length; i++)
    assert.ok(
      (required(sorted[i]) - required(sorted[i - 1])) / 1000 > 90,
      "two real posts fell inside the ±90s match window — the window is too wide",
    );
});
