import assert from "node:assert/strict";
import test from "node:test";
import { independentApproval, reviewComments } from "../src/review-gate-comments.ts";

const sha = "synthetic-head";
const author = "WR622.S1.J502";
const comment = (reviewer: string, verdict = "APPROVE", head = sha) => ({
  body: `VERDICT: ${verdict}\nReviewed-SHA: ${head}\nReviewer: ${reviewer}`,
  html_url: "https://example.test/review",
});
test("review grammar requires three exact lines, matching head and nonempty reviewer", () => {
  const valid = comment("WR622.S1.J503");
  assert.deepEqual(reviewComments(sha, [valid]), [
    { verdict: "VERDICT: APPROVE", reviewer: "WR622.S1.J503", url: valid.html_url },
  ]);
  for (const body of [
    "",
    "VERDICT: APPROVE",
    valid.body.replace("Reviewer: ", "Reviewer:"),
    valid.body.replace("WR622.S1.J503", "   "),
  ])
    assert.deepEqual(reviewComments(sha, [{ body }]), []);
  assert.deepEqual(
    reviewComments(sha, [comment("WR622.S1.J503", "APPROVE", "old"), comment("WR622.S1.J503", "UNKNOWN"), {}]),
    [],
  );
  for (const separator of ["\r\n", "\r"])
    assert.equal(reviewComments(sha, [{ ...valid, body: valid.body.replaceAll("\n", separator) }]).length, 1);
});
test("approval rejects self review, normalized self cards, ambiguous cards and any current changes", () => {
  for (const reviewer of [author, "WR0622.S01.J0502", "no card", "WR622.S1.J503 WR622.S1.J504"])
    assert.equal(independentApproval(author, sha, [comment(reviewer)]), null);
  assert.equal(independentApproval(author, sha, [comment("WR622.S1.J503"), comment("WR622.S1.J504", "CHANGES")]), null);
  assert.deepEqual(independentApproval(author, sha, [comment("WR622.S1.J503"), comment("WR622.S1.J504")]), {
    author_card: author,
    reviewer_card: "WR622.S1.J504",
    head_sha: sha,
    review_url: "https://example.test/review",
  });
  assert.equal(
    independentApproval(author, sha, [comment("WR622.S1.J503"), comment("WR622.S1.J504", "CHANGES", "old")])
      ?.reviewer_card,
    "WR622.S1.J503",
  );
});
