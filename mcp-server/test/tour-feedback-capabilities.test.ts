import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import test from "node:test";
import { createReportsWebHandler } from "../src/reports-web.ts";

const module: unknown = await import(new URL("../src/tour-sharing.js", import.meta.url).href);
assert.ok(typeof module === "object" && module !== null && "tourSharingBrowserAccess" in module);
assert.ok(typeof module.tourSharingBrowserAccess === "function");
const tourSharingBrowserAccess = module.tourSharingBrowserAccess;

class ToolError extends Error {
  payload: { error: string };
  constructor(payload: { error: string }) {
    super(payload.error);
    this.payload = payload;
  }
}

const projectionRef = `projection:public:${"a".repeat(32)}`;
const propertyRef = `property:public:${"b".repeat(32)}`;
const fixturePath = new URL("../contracts/fixtures/tour-feedback-capabilities.v1.json", import.meta.url);

async function feedback(scopes: string[] | null) {
  const sharing: unknown = tourSharingBrowserAccess({ ToolError });
  assert.ok(typeof sharing === "object" && sharing !== null && "readFeedback" in sharing);
  assert.ok(typeof sharing.readFeedback === "function");
  const readFeedback = sharing.readFeedback;
  const handler = createReportsWebHandler({
    readFeedbackFn: async ({ sessionDigest }) => {
      const result: unknown = await readFeedback(
        {
          async query(sql: string, params: string[]) {
            assert.match(sql, /ops\.read_tour_share_feedback\(\$1::text\)/);
            assert.deepEqual(params, [sessionDigest]);
            return {
              rows: [
                {
                  feedback:
                    scopes === null
                      ? null
                      : {
                          projection_ref: projectionRef,
                          permission_scopes: scopes,
                          can_download: !scopes.includes("download"),
                          items: [{ property_ref: propertyRef, broker_notes: "private" }],
                        },
                },
              ],
            };
          },
        },
        { session_digest: sessionDigest },
      );
      assert.ok(typeof result === "object" && result !== null && "feedback" in result);
      return { ok: true, data: result.feedback };
    },
  });
  const response = await handler.fetch(
    new Request("https://reports.doctorcre.com/api/share/feedback", {
      headers: { cookie: `__Host-tour_share_session=${"a".repeat(43)}` },
    }),
  );
  assert.equal(response.headers.get("cache-control"), "no-store");
  return { status: response.status, body: (await response.json()) as unknown };
}

test("feedback HTTP contract derives download only from live session scopes and preserves feedback scopes", async () => {
  const withDownload = await feedback(["view_packet", "shortlist", "comment", "download"]);
  const withoutDownload = await feedback(["view_packet", "shortlist", "comment"]);
  const expected = (can_download: boolean) => ({
    status: 200,
    body: {
      data: {
        projection_ref: projectionRef,
        permission_scopes: ["shortlist", "comment"],
        can_download,
        items: [{ property_ref: propertyRef }],
      },
    },
  });
  assert.deepEqual(withDownload, expected(true));
  assert.deepEqual(withoutDownload, expected(false));
  const recorded = { with_download: withDownload, without_download: withoutDownload };
  if (process.env.RECORD_TOUR_FEEDBACK_CONTRACT === "1")
    await writeFile(fixturePath, `${JSON.stringify(recorded, null, 2)}\n`);
  assert.deepEqual(JSON.parse(await readFile(fixturePath, "utf8")), recorded);
});

test("feedback refuses absent live session data without advertising a download capability", async () => {
  assert.deepEqual(await feedback(null), { status: 503, body: { error: "share_unavailable" } });
});
