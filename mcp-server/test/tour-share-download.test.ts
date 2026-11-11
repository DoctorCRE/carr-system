import assert from "node:assert/strict";
import test from "node:test";
import { createReportsWebHandler } from "../src/reports-web.ts";

test("download budget refusal is a no-store 429 with a retry delay", async () => {
  const handler = createReportsWebHandler({ downloadFn: async () => ({ ok: false, status: 429 }) });
  const response = await handler.fetch(
    new Request("https://reports.doctorcre.com/api/share/download", {
      headers: { cookie: "__Host-tour_share_session=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" },
    }),
  );
  assert.equal(response.status, 429);
  assert.equal(response.headers.get("retry-after"), "60");
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.deepEqual(await response.json(), { error: "rate_limited" });
});

test("download requires cookie and returns a private attachment", async () => {
  const handler = createReportsWebHandler({
    downloadFn: async () => ({
      ok: true,
      response: new Response("%PDF-fixture", {
        headers: { "content-type": "application/pdf", "content-disposition": 'attachment; filename="Tour.pdf"' },
      }),
    }),
  });
  const url = "https://reports.doctorcre.com/api/share/download";
  assert.equal((await handler.fetch(new Request(url))).status, 401);
  const response = await handler.fetch(
    new Request(url, { headers: { cookie: "__Host-tour_share_session=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" } }),
  );
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(response.headers.get("content-type"), "application/pdf");
  assert.equal(await response.text(), "%PDF-fixture");
});

test("scanner GET and HEAD never exchange a grant", async () => {
  let exchanges = 0;
  const handler = createReportsWebHandler({
    exchangeShareTokenFn: async () => {
      exchanges++;
      throw Error("scanner activated");
    },
  });
  for (const agent of [
    "Slackbot-LinkExpanding 1.0",
    "facebookexternalhit/1.1",
    "Twitterbot/1.0",
    "Microsoft Office Existence Discovery",
    "Googlebot",
  ])
    for (const method of ["GET", "HEAD"])
      for (const path of ["/share", "/api/share/exchange"]) {
        const response = await handler.fetch(
          new Request(`https://reports.doctorcre.com${path}`, { method, headers: { "user-agent": agent } }),
        );
        assert.equal(response.headers.has("set-cookie"), false);
      }
  assert.equal(exchanges, 0);
});
